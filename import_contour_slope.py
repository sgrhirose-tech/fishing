#!/usr/bin/env python3
"""
spots_personal/ にある参照JSON（海しるAPIで取得した等深線データ入り）から
nearest_20m_contour_distance_m を抽出して spots/{slug}.json に書き込む。

使い方:
    python import_contour_slope.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent
PERSONAL_DIR = ROOT / "spots_personal"
SPOTS_DIR = ROOT / "spots"

NAME_TO_SLUG = {
    "一色海岸": "isshiki",
    "三浦海岸": "miura",
    "久里浜海岸": "kurihama",
    "二宮海岸": "ninomiya",
    "国府津海岸": "kozu",
    "大浜海岸": "ohama",
    "大磯海水浴場": "oiso",
    "守屋海水浴場": "moriya",
    "富津海水浴場": "futtsu",
    "平塚海水浴場": "hiratsuka",
    "御宿海岸": "onjuku",
    "御幸ノ浜": "miyuki",
    "森戸海岸": "morito",
    "津久井浜": "tsukui",
    "長者ヶ崎海岸": "chojakasaki",
    "片瀬東浜海水浴場": "katase_east",
    "片瀬西浜・鵠沼海水浴場": "katase",
    "秋谷海岸": "akiya",
    "辻堂海岸": "tsujido",
    "逗子海岸": "zushi",
    "酒匂海岸": "sakawa",
    "銚子港": "choshi_port",
    "銚子外港": "choshi_outer",
    "黒生港": "kuroo_port",
    "名洗港": "narasho_port",
    "犬吠埼": "inubosaki",
}


def main():
    if not PERSONAL_DIR.exists():
        print(f"[エラー] {PERSONAL_DIR} が見つかりません")
        return

    files = [p for p in sorted(PERSONAL_DIR.glob("*.json")) if not p.stem.startswith("_")]
    if not files:
        print(f"[情報] {PERSONAL_DIR} にJSONファイルがありません")
        return

    updated, skipped, unmatched = 0, 0, 0

    for p in files:
        try:
            with open(p, encoding="utf-8") as f:
                ref = json.load(f)
        except Exception as e:
            print(f"  [警告] {p.name} 読み込み失敗: {e}")
            skipped += 1
            continue

        name = ref.get("name", "")
        slug = NAME_TO_SLUG.get(name)
        if not slug:
            print(f"  [未対応] {name} ({p.name}) → スラッグ不明、スキップ")
            unmatched += 1
            continue

        spot_path = SPOTS_DIR / f"{slug}.json"
        if not spot_path.exists():
            print(f"  [警告] {spot_path} が見つかりません、スキップ")
            skipped += 1
            continue

        # 等深線距離を取得
        try:
            dist = (ref["physical_features"]["depth_profile"]
                       ["contour_reference"]["nearest_20m_contour_distance_m"])
        except KeyError:
            print(f"  [警告] {name}: contour_referenceフィールドなし、スキップ")
            skipped += 1
            continue

        # スポットJSONを読み込んで更新
        with open(spot_path, encoding="utf-8") as f:
            spot = json.load(f)

        spot.setdefault("physical_features", {})["nearest_20m_contour_distance_m"] = dist

        with open(spot_path, "w", encoding="utf-8") as f:
            json.dump(spot, f, ensure_ascii=False, indent=2)

        dist_str = f"{dist}m" if dist is not None else "null（スキップ）"
        print(f"  ✓ {slug} ({name}): nearest_20m_contour_distance_m = {dist_str}")
        updated += 1

    print(f"\n完了: {updated}件更新 / {unmatched}件未対応 / {skipped}件スキップ")


if __name__ == "__main__":
    main()
