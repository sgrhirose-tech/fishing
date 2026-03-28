#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unadjusted/ の JSON から緯度経度・海方向を読み取り、
底質・等深線20m を再取得して unadjusted-2/ に保存するバッチツール。

手作業で座標と海方向を調整した後に使用する。

使い方:
  python tools/refetch_physical_data.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pythonista_spot_tools import fetch_physical_data

REPO_ROOT  = Path(__file__).parent.parent
INPUT_DIR  = REPO_ROOT / "unadjusted"
OUTPUT_DIR = REPO_ROOT / "unadjusted-2"


def process_file(src_path: Path) -> bool:
    spot = json.loads(src_path.read_text(encoding="utf-8"))
    lat         = spot["location"]["latitude"]
    lon         = spot["location"]["longitude"]
    sea_bearing = spot.get("physical_features", {}).get("sea_bearing_deg")

    print(f"    座標: ({lat:.6f}, {lon:.6f})  海方向: {sea_bearing}°")
    phys = fetch_physical_data(lat, lon, sea_bearing=sea_bearing)
    if phys is None:
        print("    [失敗] API 取得エラー")
        return False

    spot.setdefault("physical_features", {})
    spot["physical_features"]["seabed_type"]                    = phys.get("seabed_type", "unknown")
    spot["physical_features"]["nearest_20m_contour_distance_m"] = phys.get("nearest_20m_contour_distance_m")

    spot.setdefault("derived_features", {})
    spot["derived_features"]["bottom_kisugo_score"] = phys.get("bottom_kisugo_score", 50)
    spot["derived_features"]["seabed_summary"]       = phys.get("seabed_summary", "")

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = OUTPUT_DIR / src_path.name
    out_path.write_text(json.dumps(spot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    → {out_path.relative_to(REPO_ROOT)}")
    return True


def main():
    files = sorted(f for f in INPUT_DIR.glob("*.json") if not f.name.startswith("_"))
    if not files:
        print("unadjusted/ に JSON ファイルが見つかりません。")
        return

    print(f"対象: {len(files)}件\n")
    ok = 0
    for i, path in enumerate(files, 1):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            spot_name = data.get("name", path.stem)
        except Exception:
            spot_name = path.stem
        print(f"[{i}/{len(files)}] {spot_name} ({path.stem})")
        if process_file(path):
            ok += 1

    print(f"\n── 完了 ── 成功: {ok}件 / 失敗: {len(files) - ok}件")


if __name__ == "__main__":
    main()
