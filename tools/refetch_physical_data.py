#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
確定座標から底質・等深線・施設種別を一括取得するツール。

spot_editor.py で座標と海方向を確認・修正した後に実行する。
底質・等深線（海しる）と施設種別（OSM Overpass）をまとめて取得し、
unadjusted/ → spots/ へ書き込む。書き込み成功後は unadjusted/ の元ファイルを削除する。

使い方:
  python tools/refetch_physical_data.py               # ドライラン（全件）
  python tools/refetch_physical_data.py --apply        # spots/ に書き込み・元ファイル削除
  python tools/refetch_physical_data.py --slug kamogawa-ko  # 1件のみ処理
  python tools/refetch_physical_data.py --skip-classified   # 分類済みをスキップ
"""

import argparse
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pythonista_spot_tools import fetch_physical_data

REPO_ROOT    = Path(__file__).parent.parent
OVERPASS_URL = "http://overpass-api.de/api/interpreter"

# ──────────────────────────────────────────
# OSM 施設種別分類ルール
# ──────────────────────────────────────────

# (OSMキー, OSM値, primary_type, 基本信頼度)
TERRAIN_TAGS = [
    ("natural",  "beach",      "sand_beach",       0.90),
    ("natural",  "sand",       "sand_beach",       0.80),
    ("natural",  "shingle",    "rocky_shore",      0.75),
    ("natural",  "cliff",      "rocky_shore",      0.90),
    ("natural",  "rock",       "rocky_shore",      0.85),
    ("natural",  "bare_rock",  "rocky_shore",      0.85),
    ("man_made", "breakwater", "breakwater",       0.95),
    ("man_made", "seawall",    "breakwater",       0.85),
    ("man_made", "quay",       "breakwater",       0.80),
    ("man_made", "pier",       "fishing_facility", 0.85),
    ("leisure",  "fishing",    "fishing_facility", 0.95),
    ("leisure",  "marina",     "fishing_facility", 0.85),
    ("leisure",  "slipway",    "fishing_facility", 0.80),
]

# (OSMキー, OSM値, フラグ名)
SECONDARY_TAGS = [
    ("landuse",  "harbour",  "harbour"),
    ("man_made", "pier",     "pier"),
    ("leisure",  "slipway",  "slipway"),
    ("leisure",  "marina",   "marina"),
    ("natural",  "cliff",    "cliff"),
    ("amenity",  "parking",  "parking_nearby"),
]

# 距離係数テーブル
_DIST_FACTORS = [(15, 1.0), (50, 0.85), (150, 0.65)]


# ──────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _dist_factor(dist_m: float) -> float:
    for threshold, factor in _DIST_FACTORS:
        if dist_m <= threshold:
            return factor
    return 0.0  # 150m 超は対象外


# ──────────────────────────────────────────
# OSM 施設種別推定
# ──────────────────────────────────────────

def classify_spot(lat: float, lon: float) -> dict | None:
    """
    Overpass API でスポット周辺の地物タグを取得し、
    主分類・補助フラグ・信頼度を返す。
    API 失敗時は None を返す。
    """
    query = (
        "[out:json][timeout:15];\n(\n"
        f'  node["natural"~"^(beach|sand|shingle|cliff|rock|bare_rock)$"](around:150,{lat},{lon});\n'
        f'  way["natural"~"^(beach|sand|shingle|cliff|rock|bare_rock)$"](around:150,{lat},{lon});\n'
        f'  node["man_made"~"^(breakwater|seawall|quay|pier)$"](around:150,{lat},{lon});\n'
        f'  way["man_made"~"^(breakwater|seawall|quay|pier)$"](around:150,{lat},{lon});\n'
        f'  node["leisure"~"^(fishing|marina|slipway)$"](around:150,{lat},{lon});\n'
        f'  way["leisure"~"^(fishing|marina|slipway)$"](around:150,{lat},{lon});\n'
        f'  node["landuse"="harbour"](around:150,{lat},{lon});\n'
        f'  way["landuse"="harbour"](around:150,{lat},{lon});\n'
        f'  node["amenity"="parking"](around:200,{lat},{lon});\n'
        f'  way["amenity"="parking"](around:200,{lat},{lon});\n'
        ");\nout center;"
    )
    try:
        data = urllib.parse.urlencode({"data": query}).encode("utf-8")
        req = urllib.request.Request(OVERPASS_URL, data=data, method="POST")
        req.add_header("User-Agent", "TsuricastSpotClassifier/1.0 (personal-use)")
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"    [警告] Overpass 取得失敗: {e}")
        return None

    scores: dict[str, float] = {}
    secondary: set[str] = set()
    evidence: list[str] = []

    for el in result.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "node":
            el_lat, el_lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center", {})
            el_lat, el_lon = center.get("lat"), center.get("lon")
        if el_lat is None or el_lon is None:
            continue

        dist = _haversine_m(lat, lon, el_lat, el_lon)
        factor = _dist_factor(dist)
        if factor == 0.0:
            continue

        for key, value, primary_type, base_conf in TERRAIN_TAGS:
            if tags.get(key) == value:
                score = base_conf * factor
                if score > scores.get(primary_type, 0):
                    scores[primary_type] = score
                evidence.append(f"{key}={value}@{int(dist)}m")
                break

        for key, value, flag in SECONDARY_TAGS:
            if tags.get(key) == value:
                secondary.add(flag)

    if not scores:
        primary_type, confidence = "unknown", 0.0
    else:
        primary_type = max(scores, key=scores.get)
        confidence   = round(scores[primary_type], 2)

    return {
        "primary_type":    primary_type,
        "confidence":      confidence,
        "secondary_flags": sorted(secondary),
        "source":          "osm_rule",
        "osm_evidence":    sorted(set(evidence))[:5],
    }


# ──────────────────────────────────────────
# 1ファイル処理
# ──────────────────────────────────────────

def process_file(
    src_path: Path,
    dst_path: Path | None = None,
    dry_run: bool = True,
    skip_classified: bool = False,
) -> bool:
    spot        = json.loads(src_path.read_text(encoding="utf-8"))
    lat         = spot["location"]["latitude"]
    lon         = spot["location"]["longitude"]
    sea_bearing = spot.get("physical_features", {}).get("sea_bearing_deg")

    # 分類済みスキップ
    if skip_classified:
        src = spot.get("classification", {}).get("source", "")
        if src in ("osm_rule", "manual", "mixed"):
            print(f"    [スキップ] 分類済み ({spot['classification']['primary_type']})")
            return True

    print(f"    座標: ({lat:.6f}, {lon:.6f})  海方向: {sea_bearing}°")

    # 底質・等深線（海しる）
    print("    底質・等深線取得 (海しる)...", end=" ", flush=True)
    phys = fetch_physical_data(lat, lon, sea_bearing=sea_bearing)
    if phys is None:
        print("失敗")
        return False
    print("完了")

    # 施設種別推定（Overpass）
    print("    施設種別推定 (Overpass)...", end=" ", flush=True)
    cls = classify_spot(lat, lon)
    if cls:
        print(f"→ {cls['primary_type']} (confidence={cls['confidence']})")
    else:
        print("失敗（分類スキップ）")
    time.sleep(1.0)  # Overpass レート制限

    if dry_run:
        print(f"    [ドライラン] seabed={phys.get('seabed_type')}  "
              f"contour={phys.get('nearest_20m_contour_distance_m')}m  "
              f"cls={cls['primary_type'] if cls else 'N/A'}")
        return True

    # 書き込み
    spot.setdefault("physical_features", {})
    spot["physical_features"]["seabed_type"]                    = phys.get("seabed_type", "unknown")
    spot["physical_features"]["nearest_20m_contour_distance_m"] = phys.get("nearest_20m_contour_distance_m")

    spot.setdefault("derived_features", {})
    spot["derived_features"]["bottom_kisugo_score"] = phys.get("bottom_kisugo_score", 50)
    spot["derived_features"]["seabed_summary"]       = phys.get("seabed_summary", "")

    if cls:
        spot["classification"] = cls

    out = dst_path or src_path
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(spot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    → {out.relative_to(REPO_ROOT)}")

    # 書き込み先が元ファイルと異なる場合（unadjusted/ → spots/）は元ファイルを削除
    if out != src_path:
        src_path.unlink()
        print(f"    削除: {src_path.relative_to(REPO_ROOT)}")

    return True


# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="確定座標から底質・等深線・施設種別を一括取得する"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="spots/ に書き込み、unadjusted/ の元ファイルを削除する（デフォルト: ドライラン）",
    )
    parser.add_argument(
        "--slug", metavar="SLUG",
        help="1件のみ処理するスラッグ",
    )
    parser.add_argument(
        "--skip-classified", action="store_true",
        help="既に classification が設定済みのスポットをスキップ",
    )
    args = parser.parse_args()

    src_dir = REPO_ROOT / "unadjusted"
    dst_dir = REPO_ROOT / "spots"
    dry_run = not args.apply

    if args.slug:
        files = [src_dir / f"{args.slug}.json"]
        files = [f for f in files if f.exists()]
        if not files:
            print(f"{args.slug}.json が見つかりません: {src_dir}")
            return
    else:
        files = sorted(f for f in src_dir.glob("*.json") if not f.name.startswith("_"))

    if not files:
        print(f"{src_dir} に JSON ファイルが見つかりません。")
        return

    mode = "ドライラン" if dry_run else f"書き込みモード（→ spots/ ・元ファイル削除）"
    print(f"対象: {len(files)}件  モード: {mode}\n")

    ok = 0
    for i, path in enumerate(files, 1):
        try:
            data      = json.loads(path.read_text(encoding="utf-8"))
            spot_name = data.get("name", path.stem)
        except Exception:
            spot_name = path.stem
        print(f"[{i}/{len(files)}] {spot_name} ({path.stem})")

        dst = None if dry_run else dst_dir / path.name

        if process_file(
            path,
            dst_path=dst,
            dry_run=dry_run,
            skip_classified=args.skip_classified,
        ):
            ok += 1

    print(f"\n── 完了 ── 成功: {ok}件 / 失敗: {len(files) - ok}件")
    if dry_run:
        print("\n実際に書き込むには --apply を指定してください。")


if __name__ == "__main__":
    main()
