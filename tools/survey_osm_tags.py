#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OSMタグ調査用軽量スクリプト。

海しるAPIを呼ばず Overpass だけを使い、スポット周辺のOSMタグを収集・集計する。
TERRAIN_TAGS の修正方針を決めるための調査に使う。

使い方:
  python tools/survey_osm_tags.py                        # 全件集計
  python tools/survey_osm_tags.py --slug akiya-gyoko     # 1件詳細
  python tools/survey_osm_tags.py --radius 500           # 半径変更（デフォルト300m）
"""

import argparse
import json
import math
import ssl
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

OVERPASS_ENDPOINTS = [
    "http://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

_sleep_sec = 2.0
# HTTPS エンドポイント用: SSL 検証なし（ツールスクリプト専用）
_SSL_CTX = ssl._create_unverified_context()


def _overpass_post(query: str) -> dict:
    """複数エンドポイントへフォールバックしながら Overpass にPOSTする。"""
    global _sleep_sec
    encoded = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_err: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        req = urllib.request.Request(endpoint, data=encoded, method="POST")
        req.add_header("User-Agent", "TsuricastTagSurvey/1.0 (personal-use)")
        ctx = None if endpoint.startswith("http://") else _SSL_CTX
        try:
            with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            _sleep_sec = max(2.0, _sleep_sec * 0.8)
            return result
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 502, 503, 504):
                last_err = e
                continue
            raise
        except Exception as e:
            last_err = e
            continue
    _sleep_sec = min(30.0, _sleep_sec * 2)
    raise last_err

# 調査対象キー（TERRAIN_TAGS より広め）
SURVEY_KEYS = (
    "natural", "man_made", "leisure", "landuse", "harbour",
    "waterway", "amenity", "seamark:type", "water", "sport", "tourism",
)


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _query(lat: float, lon: float, radius: int) -> list[dict]:
    r = radius
    q = (
        f"[out:json][timeout:20];\n(\n"
        f'  node["natural"~"^(beach|sand|shingle|cliff|rock|bare_rock|coastline|water|wetland)$"](around:{r},{lat},{lon});\n'
        f'  way["natural"~"^(beach|sand|shingle|cliff|rock|bare_rock|coastline|water|wetland)$"](around:{r},{lat},{lon});\n'
        f'  node["man_made"~"^(breakwater|seawall|quay|pier|jetty|groyne|dyke)$"](around:{r},{lat},{lon});\n'
        f'  way["man_made"~"^(breakwater|seawall|quay|pier|jetty|groyne|dyke)$"](around:{r},{lat},{lon});\n'
        f'  node["leisure"~"^(fishing|marina|slipway|swimming_area)$"](around:{r},{lat},{lon});\n'
        f'  way["leisure"~"^(fishing|marina|slipway|swimming_area)$"](around:{r},{lat},{lon});\n'
        f'  node["landuse"~"^(harbour|industrial|port)$"](around:{r},{lat},{lon});\n'
        f'  way["landuse"~"^(harbour|industrial|port)$"](around:{r},{lat},{lon});\n'
        f'  node["harbour"](around:{r},{lat},{lon});\n'
        f'  way["harbour"](around:{r},{lat},{lon});\n'
        f'  node["waterway"~"^(dock|riverbank|canal)$"](around:{r},{lat},{lon});\n'
        f'  way["waterway"~"^(dock|riverbank|canal)$"](around:{r},{lat},{lon});\n'
        f'  node["amenity"="parking"](around:{r},{lat},{lon});\n'
        f'  way["amenity"="parking"](around:{r},{lat},{lon});\n'
        f");\nout center;"
    )
    return _overpass_post(q).get("elements", [])


def _elem_coords(el: dict) -> tuple[float, float] | None:
    if el["type"] == "node":
        return el.get("lat"), el.get("lon")
    c = el.get("center", {})
    return c.get("lat"), c.get("lon")


def survey_one(slug: str, name: str, lat: float, lon: float, radius: int) -> list[dict]:
    """1スポットを調査して要素リストを返す。"""
    try:
        elements = _query(lat, lon, radius)
    except Exception as e:
        print(f"  [エラー] {e}")
        return []

    rows = []
    for el in elements:
        coords = _elem_coords(el)
        if coords[0] is None:
            continue
        dist  = _haversine_m(lat, lon, coords[0], coords[1])
        tags  = el.get("tags", {})
        interesting = {k: v for k, v in tags.items() if k in SURVEY_KEYS}
        if interesting:
            rows.append({
                "type": el["type"],
                "dist": dist,
                "tags": interesting,
            })
    return sorted(rows, key=lambda r: r["dist"])


def print_detail(slug: str, name: str, lat: float, lon: float, radius: int) -> None:
    print(f"\n=== {slug} ({name}) ===")
    print(f"座標: ({lat}, {lon})  半径: {radius}m")
    rows = survey_one(slug, name, lat, lon, radius)
    if not rows:
        print("  取得要素なし（またはエラー）")
        return
    print(f"取得要素: {len(rows)}件\n")
    for r in rows:
        tag_str  = "  ".join(f"{k}={v}" for k, v in sorted(r["tags"].items()))
        over_flag = " [>150m]" if r["dist"] > 150 else ""
        print(f"  [{r['type']:4s}] dist={int(r['dist']):4d}m{over_flag}  {tag_str}")


def run_all(files: list[Path], radius: int) -> None:
    # key=value → スポット数
    tag_spots: dict[str, set[str]] = defaultdict(set)
    # key=value → 距離帯ごとのスポット数
    dist_bands = [15, 50, 150, 999999]
    dist_counts: dict[str, list[int]] = defaultdict(lambda: [0] * len(dist_bands))

    total = len(files)
    errors = 0

    for i, path in enumerate(files, 1):
        spot = json.loads(path.read_text(encoding="utf-8"))
        slug = spot.get("slug", path.stem)
        name = spot.get("name", slug)
        lat  = spot["location"]["latitude"]
        lon  = spot["location"]["longitude"]

        print(f"[{i}/{total}] {name} ({slug})...", end=" ", flush=True)
        try:
            rows = survey_one(slug, name, lat, lon, radius)
            print(f"{len(rows)}件")
        except Exception as e:
            print(f"エラー: {e}")
            errors += 1
            time.sleep(1)
            continue

        for r in rows:
            for k, v in r["tags"].items():
                key = f"{k}={v}"
                tag_spots[key].add(slug)
                for bi, band in enumerate(dist_bands):
                    if r["dist"] <= band:
                        dist_counts[key][bi] += 1
                        break

        if i < total:
            time.sleep(_sleep_sec)

    # ── 集計結果表示 ──
    print(f"\n{'─'*60}")
    print(f"調査完了: {total}件  エラー: {errors}件  半径: {radius}m")
    print(f"\nタグ出現ランキング（スポット数順）:\n")
    print(f"  {'タグ':<35}  スポット数  ≤15m  ≤50m  ≤150m  >150m")
    print(f"  {'─'*35}  ──────────  ────  ────  ─────  ─────")

    sorted_tags = sorted(tag_spots.items(), key=lambda x: len(x[1]), reverse=True)
    for tag, spots in sorted_tags:
        dc = dist_counts[tag]
        print(f"  {tag:<35}  {len(spots):>10}  {dc[0]:>4}  {dc[1]:>4}  {dc[2]:>5}  {dc[3]:>5}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="OSMタグ調査用軽量スクリプト（海しるなし）"
    )
    parser.add_argument("--slug",   metavar="SLUG", help="1件のみ詳細表示")
    parser.add_argument("--radius", type=int, default=300, metavar="M",
                        help="Overpass 検索半径メートル（デフォルト: 300）")
    args = parser.parse_args()

    spots_dir = REPO_ROOT / "spots"
    files = sorted(f for f in spots_dir.glob("*.json") if not f.name.startswith("_"))

    if not files:
        print(f"{spots_dir} に JSON ファイルが見つかりません。")
        return

    if args.slug:
        path = spots_dir / f"{args.slug}.json"
        if not path.exists():
            print(f"{args.slug}.json が見つかりません: {spots_dir}")
            return
        spot = json.loads(path.read_text(encoding="utf-8"))
        print_detail(
            args.slug,
            spot.get("name", args.slug),
            spot["location"]["latitude"],
            spot["location"]["longitude"],
            args.radius,
        )
    else:
        print(f"調査: {len(files)}件  半径: {args.radius}m\n")
        run_all(files, args.radius)


if __name__ == "__main__":
    main()
