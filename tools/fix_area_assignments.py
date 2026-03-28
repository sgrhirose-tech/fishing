#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unadjusted/ と spots/ の JSON ファイルで、
_marine_areas.json の最新センター座標を使ってエリア割り当てを再計算し、
area_name / area_slug / pref_slug を一括修正する。

prefecture / city / city_slug は変更しない。

使い方:
  python tools/fix_area_assignments.py          # ドライラン（変更内容を表示するだけ）
  python tools/fix_area_assignments.py --apply  # 実際に上書き保存
"""

import json
import math
import sys
from pathlib import Path

REPO_ROOT  = Path(__file__).parent.parent
AREAS_FILE = REPO_ROOT / "spots" / "_marine_areas.json"

TARGET_DIRS = [
    REPO_ROOT / "unadjusted",
    REPO_ROOT / "spots",
]

AREA_MAP = {
    "相模湾":   ("sagamibay", "kanagawa"),
    "三浦半島": ("miura",     "kanagawa"),
    "東京湾":   ("tokyobay",  "kanagawa"),
    "内房":     ("uchibo",    "chiba"),
    "外房":     ("sotobo",    "chiba"),
    "九十九里": ("kujukuri",  "chiba"),
}

PREF_SLUG_MAP = {
    "神奈川県": "kanagawa",
    "東京都":   "tokyo",
    "千葉県":   "chiba",
}


def load_areas() -> dict:
    data = json.loads(AREAS_FILE.read_text(encoding="utf-8"))
    return data.get("areas", {})


def assign_area(lat: float, lon: float, areas: dict) -> str:
    candidates = {
        name: info for name, info in areas.items()
        if (info.get("lat_min", -90) <= lat <= info.get("lat_max", 90) and
            info.get("lon_min", -180) <= lon <= info.get("lon_max", 180))
    }
    if not candidates:
        candidates = areas

    best_name = "不明"
    best_dist = float("inf")
    for name, info in candidates.items():
        dlat = lat - info.get("center_lat", 0)
        dlon = lon - info.get("center_lon", 0)
        dist = math.sqrt(dlat * dlat + dlon * dlon)
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def fix_file(path: Path, areas: dict, apply: bool) -> bool:
    try:
        spot = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [スキップ] {path.name}: 読み込みエラー ({e})")
        return False

    loc = spot.get("location", {})
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    if lat is None or lon is None:
        return False

    area_obj = spot.get("area", {})
    current_area_name = area_obj.get("area_name", "")
    current_area_slug = area_obj.get("area_slug", "")
    current_pref_slug = area_obj.get("pref_slug", "")

    # 都道府県名から pref_slug を導出（prefecture フィールドが正しい前提）
    prefecture = area_obj.get("prefecture", "")
    correct_pref_slug = PREF_SLUG_MAP.get(prefecture, current_pref_slug)

    new_area_name = assign_area(lat, lon, areas)
    new_area_slug, area_pref_slug = AREA_MAP.get(new_area_name, (current_area_slug, current_pref_slug))

    # pref_slug: prefecture フィールドが優先、なければエリアのデフォルト
    new_pref_slug = correct_pref_slug if correct_pref_slug else area_pref_slug

    changed = False
    changes = []
    if new_area_name != current_area_name:
        changes.append(f"area_name {current_area_name!r} → {new_area_name!r}")
        changed = True
    if new_area_slug != current_area_slug:
        changes.append(f"area_slug {current_area_slug!r} → {new_area_slug!r}")
        changed = True
    if new_pref_slug != current_pref_slug:
        changes.append(f"pref_slug {current_pref_slug!r} → {new_pref_slug!r}")
        changed = True

    if not changed:
        return False

    print(f"  {path.name}: " + ", ".join(changes))
    if apply:
        spot["area"]["area_name"] = new_area_name
        spot["area"]["area_slug"] = new_area_slug
        spot["area"]["pref_slug"] = new_pref_slug
        path.write_text(json.dumps(spot, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def main():
    apply = "--apply" in sys.argv
    if not apply:
        print("【ドライラン】変更対象を表示します。実際に保存するには --apply を付けてください。\n")

    try:
        areas = load_areas()
    except Exception as e:
        print(f"エラー: _marine_areas.json の読み込み失敗 ({e})")
        sys.exit(1)

    total_changed = 0
    for d in TARGET_DIRS:
        files = sorted(f for f in d.glob("*.json") if not f.name.startswith("_"))
        if not files:
            continue
        print(f"--- {d.name}/ ({len(files)}件) ---")
        changed = sum(1 for f in files if fix_file(f, areas, apply))
        total_changed += changed
        if changed == 0:
            print("  （変更対象なし）")

    print(f"\n{'修正完了' if apply else '変更対象'}: {total_changed} 件")
    if not apply and total_changed > 0:
        print("保存するには: python tools/fix_area_assignments.py --apply")


if __name__ == "__main__":
    main()
