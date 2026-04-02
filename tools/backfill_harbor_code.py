#!/usr/bin/env python3
"""
既存の spots/*.json に harbor_code / harbor_name を一括追記する一時ツール。

harbor_mapping.json → spot JSON への移行用。実行後は削除可。

使い方:
    python tools/backfill_harbor_code.py           # 全件処理
    python tools/backfill_harbor_code.py --dry-run # 表示のみ
    python tools/backfill_harbor_code.py --slug abosaki  # 1件確認
"""

import argparse
import json
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SPOTS_DIR = REPO_ROOT / "spots"
HARBOR_LIST_PATH = REPO_ROOT / "data" / "harbor_list.json"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def find_nearest_harbor(lat: float, lon: float, harbors: list) -> tuple[dict, float]:
    best, best_dist = None, float("inf")
    for h in harbors:
        d = haversine_km(lat, lon, h["lat"], h["lon"])
        if d < best_dist:
            best_dist = d
            best = h
    return best, best_dist


def load_harbor_list() -> list:
    if not HARBOR_LIST_PATH.exists():
        print(f"[エラー] {HARBOR_LIST_PATH} が見つかりません。")
        sys.exit(1)
    with open(HARBOR_LIST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    harbors = [h for h in data.get("harbors", []) if h.get("lat") is not None and h.get("lon") is not None]
    print(f"[読み込み] 港: {len(harbors)} 件")
    return harbors


def main() -> None:
    parser = argparse.ArgumentParser(description="spots/*.json に harbor_code を一括追記")
    parser.add_argument("--dry-run", action="store_true", help="表示のみ（ファイル保存しない）")
    parser.add_argument("--slug", metavar="SLUG", help="1スポットのみ処理")
    args = parser.parse_args()

    harbors = load_harbor_list()

    paths = sorted(SPOTS_DIR.glob("*.json"))
    if args.slug:
        paths = [p for p in paths if p.stem == args.slug]
        if not paths:
            print(f"[エラー] slug '{args.slug}' が見つかりません")
            sys.exit(1)

    updated = skipped = errors = 0

    for path in paths:
        if path.name.startswith("_"):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                spot = json.load(f)

            if spot.get("harbor_code"):
                skipped += 1
                continue

            loc = spot.get("location", {})
            lat = loc.get("latitude")
            lon = loc.get("longitude")
            if lat is None or lon is None:
                print(f"  [スキップ] {path.name}: 座標なし")
                skipped += 1
                continue

            nearest, dist_km = find_nearest_harbor(float(lat), float(lon), harbors)
            if not nearest:
                print(f"  [スキップ] {path.name}: 最近傍港なし")
                skipped += 1
                continue

            print(f"  {path.stem}: {nearest['harbor_name']} ({nearest['harbor_code']}, {dist_km:.1f}km)")

            if not args.dry_run:
                spot["harbor_code"] = nearest["harbor_code"]
                spot["harbor_name"] = nearest["harbor_name"]
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(spot, f, ensure_ascii=False, indent=2)
                    f.write("\n")

            updated += 1

        except Exception as e:
            print(f"  [エラー] {path.name}: {e}")
            errors += 1

    print(f"\n[完了] 更新: {updated} 件 / スキップ: {skipped} 件 / エラー: {errors} 件")
    if args.dry_run:
        print("[dry-run] ファイルは保存されていません")


if __name__ == "__main__":
    main()
