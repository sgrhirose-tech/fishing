#!/usr/bin/env python3
"""
銚子エリア5スポットのJSONを生成するスクリプト。
build_spots_complete.py の関数を使って海底底質・等深線距離を自動取得し、
現行アプリのスキーマで spots/{slug}.json に出力する。

使い方:
    python create_spots_from_csv.py
"""

import json
import sys
import time
from pathlib import Path

# build_spots_complete.py を同じディレクトリから import
sys.path.insert(0, str(Path(__file__).parent))
from build_spots_complete import (
    calculate_sea_bearing,
    query_bottom_types,
    query_depth_contours,
    summarize_depth_profile_from_contours,
)

SPOTS_DIR = Path(__file__).parent / "spots"

# 底質名→seabed_type
BOTTOM_TYPE_MAP = {
    "砂":    "sand",
    "礫":    "gravel",
    "貝殻":  "shell",
    "石・岩": "rock",
    "さんご": "coral",
    "溶岩":  "lava",
}

# スポット定義（CSVから）
SPOTS = [
    {
        "slug":   "choshi_port",
        "name":   "銚子港",
        "lat":    35.7349,
        "lon":    140.8263,
        "notes":  "利根川河口に位置する大型漁港。クロダイ・スズキ・ハゼの実績が高く、潮の流れが速い",
        "access": "銚子駅から徒歩15分",
    },
    {
        "slug":   "choshi_outer",
        "name":   "銚子外港",
        "lat":    35.7279,
        "lon":    140.8444,
        "notes":  "銚子漁港の外港。投げ釣りでカレイ・ヒラメが狙える。太平洋に面し波に注意",
        "access": "外川駅（銚子電鉄）から徒歩10分",
    },
    {
        "slug":   "kuroo_port",
        "name":   "黒生港",
        "lat":    35.7363,
        "lon":    140.8628,
        "notes":  "犬吠埼北側の小漁港。根魚やメジナが狙える磯礁帯に近接",
        "access": "笠上黒生駅（銚子電鉄）から徒歩10分",
    },
    {
        "slug":   "narasho_port",
        "name":   "名洗港",
        "lat":    35.7246,
        "lon":    140.8573,
        "notes":  "犬吠埼近くの漁港。イシモチ・カレイの投げ釣りで人気",
        "access": "海鹿島駅（銚子電鉄）から徒歩5分",
    },
    {
        "slug":   "inubosaki",
        "name":   "犬吠埼",
        "lat":    35.7077,
        "lon":    140.8684,
        "notes":  "関東最東端の岬。荒磯からの磯釣りが楽しめる。灯台周辺はメジナ・クロダイのポイント",
        "access": "犬吠駅（銚子電鉄）から徒歩5分",
    },
]


def derive_seabed_type(bottom_value: str | None) -> str:
    """底質文字列（例: '砂', '砂/石・岩'）から最初の要素を seabed_type に変換。"""
    if not bottom_value:
        return "unknown"
    primary = bottom_value.split("/")[0].strip()
    return BOTTOM_TYPE_MAP.get(primary, "unknown")


def derive_kisugo_score(bottom_value: str | None) -> int:
    """底質から bottom_kisugo_score を導出。"""
    if not bottom_value:
        return 50
    parts = [p.strip() for p in bottom_value.split("/")]
    primary = parts[0] if parts else ""
    secondary = parts[1:] if len(parts) > 1 else []

    if primary == "砂":
        score = 85
        if "石・岩" in secondary:
            score -= 5
        return score
    elif primary in ("貝殻", "礫"):
        return 65
    elif primary == "石・岩":
        return 35
    return 50


def build_terrain_summary(bottom_value: str | None, dist_20m: float | None) -> str:
    """底質と20m等深線距離から terrain_summary を生成。"""
    parts = []

    if bottom_value:
        primary = bottom_value.split("/")[0].strip()
        parts.append(f"{primary}主体")
        names = [p.strip() for p in bottom_value.split("/")]
        if "貝殻" in names:
            parts.append("貝殻混じり")
        if "石・岩" in names:
            parts.append("近傍に石要素あり")

    if dist_20m is not None:
        if dist_20m >= 2000:
            parts.append("遠浅")
        elif dist_20m >= 1000:
            parts.append("やや急深")
        else:
            parts.append("急深")
    else:
        parts.append("傾斜不明")

    return "、".join(parts) if parts else "地形情報不足"


def process_spot(spot: dict) -> dict:
    slug = spot["slug"]
    name = spot["name"]
    lat  = spot["lat"]
    lon  = spot["lon"]

    print(f"\n[{name}] 処理中...")

    # 1. 海方向
    print(f"  海方向を計算中...")
    sea_bearing = calculate_sea_bearing(lat, lon)
    print(f"  sea_bearing_deg = {sea_bearing}")
    time.sleep(1.1)

    # 2. 底質
    bearing_for_query = sea_bearing if sea_bearing is not None else 90.0
    print(f"  底質を取得中...")
    bottom_result = query_bottom_types(lat, lon, bearing_for_query)
    bottom_value = bottom_result.get("value")
    print(f"  bottom = {bottom_value} ({bottom_result.get('status')})")
    time.sleep(1.1)

    # 3. 等深線
    print(f"  等深線距離を取得中...")
    depth_result = query_depth_contours(lat, lon)
    nearest_contours = depth_result.get("nearest_contours", [])
    depth_summary = summarize_depth_profile_from_contours(nearest_contours)
    dist_20m = depth_summary.get("contour_reference", {}).get("nearest_20m_contour_distance_m")
    print(f"  nearest_20m_contour_distance_m = {dist_20m}")
    time.sleep(1.1)

    # 4. 導出
    seabed_type      = derive_seabed_type(bottom_value)
    kisugo_score     = derive_kisugo_score(bottom_value)
    terrain_summary  = build_terrain_summary(bottom_value, dist_20m)

    return {
        "slug": slug,
        "name": name,
        "location": {
            "latitude":  lat,
            "longitude": lon,
        },
        "area": {
            "prefecture": "千葉県",
            "pref_slug":  "chiba",
            "area_name":  "九十九里",
            "area_slug":  "kujukuri",
            "city":       "銚子市",
            "city_slug":  "choshi",
        },
        "physical_features": {
            "sea_bearing_deg":               sea_bearing,
            "seabed_type":                   seabed_type,
            "depth_near_m":                  None,
            "depth_far_m":                   None,
            "surfer_spot":                   False,
            "nearest_20m_contour_distance_m": dist_20m,
        },
        "derived_features": {
            "bottom_kisugo_score": kisugo_score,
            "terrain_summary":     terrain_summary,
        },
        "info": {
            "notes":     spot["notes"],
            "access":    spot["access"],
            "photo_url": f"https://raw.githubusercontent.com/sgrhirose-tech/fishing/resources/photos/{slug}.jpg",
        },
    }


def main():
    SPOTS_DIR.mkdir(exist_ok=True)

    for spot in SPOTS:
        result = process_spot(spot)
        out_path = SPOTS_DIR / f"{spot['slug']}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"  -> 書き込み完了: {out_path.name}")

    print("\n全スポット完了。")


if __name__ == "__main__":
    main()
