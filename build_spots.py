#!/usr/bin/env python3
"""
spots/ フォルダと _marine_areas.json を生成するスクリプト。

fishing_advisor.py の FISHING_SPOTS・MARINE_PROXY データを
fishing_advisor_pythonista.py が期待する新スキーマに変換します。

使い方:
    python build_spots.py
"""

import json
from pathlib import Path

# ============================================================
# 旧スキーマ → 新スキーマ変換マッピング
# ============================================================

# area → {prefecture, area_slug, area_name, city} のマッピング
# URL構造: /{pref_slug}/{area_slug}/{city_slug}/{spot_slug}
SPOT_META = {
    "tsujido":      {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "相模湾", "area_slug": "sagamibay", "city": "藤沢市",        "city_slug": "fujisawa"},
    "tsujido_park": {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "相模湾", "area_slug": "sagamibay", "city": "藤沢市",        "city_slug": "fujisawa"},
    "katase":       {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "相模湾", "area_slug": "sagamibay", "city": "藤沢市",        "city_slug": "fujisawa"},
    "katase_east":  {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "相模湾", "area_slug": "sagamibay", "city": "藤沢市",        "city_slug": "fujisawa"},
    "oiso":         {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "相模湾", "area_slug": "sagamibay", "city": "大磯町",        "city_slug": "oiso"},
    "hiratsuka":    {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "相模湾", "area_slug": "sagamibay", "city": "平塚市",        "city_slug": "hiratsuka"},
    "sakawa":       {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "相模湾", "area_slug": "sagamibay", "city": "小田原市",      "city_slug": "odawara"},
    "miyuki":       {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "相模湾", "area_slug": "sagamibay", "city": "小田原市",      "city_slug": "odawara"},
    "kozu":         {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "相模湾", "area_slug": "sagamibay", "city": "小田原市",      "city_slug": "odawara"},
    "ninomiya":     {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "相模湾", "area_slug": "sagamibay", "city": "二宮町",        "city_slug": "ninomiya"},
    "zushi":        {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "三浦半島", "area_slug": "miura",   "city": "逗子市",        "city_slug": "zushi"},
    "morito":       {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "三浦半島", "area_slug": "miura",   "city": "葉山町",        "city_slug": "hayama"},
    "isshiki":      {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "三浦半島", "area_slug": "miura",   "city": "葉山町",        "city_slug": "hayama"},
    "chojakasaki":  {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "三浦半島", "area_slug": "miura",   "city": "葉山町",        "city_slug": "hayama"},
    "akiya":        {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "三浦半島", "area_slug": "miura",   "city": "横須賀市",      "city_slug": "yokosuka"},
    "miura":        {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "三浦半島", "area_slug": "miura",   "city": "三浦市",        "city_slug": "miura"},
    "tsukui":       {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "三浦半島", "area_slug": "miura",   "city": "横須賀市",      "city_slug": "yokosuka"},
    "ohama":        {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "三浦半島", "area_slug": "miura",   "city": "三浦市",        "city_slug": "miura"},
    "kurihama":     {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "三浦半島", "area_slug": "miura",   "city": "横須賀市",      "city_slug": "yokosuka"},
    "nojima":       {"prefecture": "神奈川県", "pref_slug": "kanagawa", "area": "東京湾",   "area_slug": "tokyobay","city": "横浜市金沢区",  "city_slug": "kanazawa"},
    "futtsu":       {"prefecture": "千葉県",   "pref_slug": "chiba",    "area": "東京湾",   "area_slug": "tokyobay","city": "富津市",        "city_slug": "futtsu"},
    "hota":         {"prefecture": "千葉県",   "pref_slug": "chiba",    "area": "内房",     "area_slug": "uchibo",  "city": "鋸南町",        "city_slug": "kyonan"},
    "takeoka":      {"prefecture": "千葉県",   "pref_slug": "chiba",    "area": "内房",     "area_slug": "uchibo",  "city": "富津市",        "city_slug": "futtsu"},
    "ohara":        {"prefecture": "千葉県",   "pref_slug": "chiba",    "area": "外房",     "area_slug": "sotobo",  "city": "いすみ市",      "city_slug": "isumi"},
    "onjuku":       {"prefecture": "千葉県",   "pref_slug": "chiba",    "area": "外房",     "area_slug": "sotobo",  "city": "御宿町",        "city_slug": "onjuku"},
    "moriya":       {"prefecture": "千葉県",   "pref_slug": "chiba",    "area": "外房",     "area_slug": "sotobo",  "city": "勝浦市",        "city_slug": "katsuura"},
}

# 底質 → シロギス適性スコア (0-100) と地形サマリー
SEABED_MAP = {
    "sand":       {"kisugo_score": 90, "terrain_summary": "遠浅の砂浜"},
    "sand_gravel":{"kisugo_score": 70, "terrain_summary": "砂礫混じりの浜"},
    "sand_mud":   {"kisugo_score": 55, "terrain_summary": "砂泥の穏やかな浜"},
    "sand_rock":  {"kisugo_score": 40, "terrain_summary": "砂と岩礁が混在する浜"},
    "rock":       {"kisugo_score": 10, "terrain_summary": "岩礁主体の浜"},
    "mud":        {"kisugo_score": 15, "terrain_summary": "泥底の穏やかな浜"},
}

# 旧 FISHING_SPOTS データ
FISHING_SPOTS = [
    {"id": "tsujido",     "name": "辻堂海岸",           "area": "相模湾",   "lat": 35.3285, "lon": 139.4567, "shore_direction": 180, "seabed": "sand",       "depth_near": 5, "depth_far": 15, "surfer_spot": True,  "notes": "湘南の代表的な砂浜。サーファー多め。オフショア時は空きやすい",                   "access": "辻堂駅から徒歩15分"},
    {"id": "tsujido_park","name": "辻堂海浜公園下",     "area": "相模湾",   "lat": 35.3358, "lon": 139.4384, "shore_direction": 180, "seabed": "sand",       "depth_near": 5, "depth_far": 15, "surfer_spot": True,  "notes": "辻堂海浜公園の目の前。辻堂海岸と隣接するが空いていることも多い",               "access": "辻堂駅から徒歩20分（海浜公園経由）"},
    {"id": "katase",      "name": "片瀬西浜（江ノ島）", "area": "相模湾",   "lat": 35.3037, "lon": 139.4797, "shore_direction": 185, "seabed": "sand",       "depth_near": 4, "depth_far": 12, "surfer_spot": True,  "notes": "広い砂浜。サーファー多い。江ノ島の西側で多少風が遮られる",                     "access": "片瀬江ノ島駅から徒歩5分"},
    {"id": "katase_east", "name": "片瀬東浜（江ノ島）", "area": "相模湾",   "lat": 35.3065, "lon": 139.4869, "shore_direction": 175, "seabed": "sand",       "depth_near": 4, "depth_far": 12, "surfer_spot": True,  "notes": "江ノ島の東側の砂浜。西浜よりやや静かな日もある",                               "access": "片瀬江ノ島駅から徒歩5分"},
    {"id": "oiso",        "name": "大磯海岸",           "area": "相模湾",   "lat": 35.3049, "lon": 139.3093, "shore_direction": 175, "seabed": "sand_gravel","depth_near": 5, "depth_far": 20, "surfer_spot": False, "notes": "砂と砂利が混じる。サーファー少なめ。良型シロギスが出ることも",               "access": "大磯駅から徒歩10分"},
    {"id": "hiratsuka",   "name": "平塚海岸",           "area": "相模湾",   "lat": 35.3197, "lon": 139.3479, "shore_direction": 180, "seabed": "sand",       "depth_near": 4, "depth_far": 15, "surfer_spot": True,  "notes": "遠浅の砂浜。平塚新港も近く便利",                                               "access": "平塚駅からバス20分"},
    {"id": "sakawa",      "name": "酒匂海岸（小田原）", "area": "相模湾",   "lat": 35.2587, "lon": 139.1593, "shore_direction": 170, "seabed": "sand",       "depth_near": 5, "depth_far": 20, "surfer_spot": False, "notes": "比較的空いている穴場。砂地が広がる",                                           "access": "鴨宮駅から車10分"},
    {"id": "miyuki",      "name": "御幸の浜（小田原）", "area": "相模湾",   "lat": 35.2503, "lon": 139.1469, "shore_direction": 185, "seabed": "sand",       "depth_near": 4, "depth_far": 18, "surfer_spot": False, "notes": "小田原漁港に隣接。比較的空いている砂浜。シロギスの良型が出る",               "access": "小田原駅から徒歩20分"},
    {"id": "kozu",        "name": "国府津海岸",         "area": "相模湾",   "lat": 35.2724, "lon": 139.1889, "shore_direction": 180, "seabed": "sand",       "depth_near": 5, "depth_far": 20, "surfer_spot": False, "notes": "砂地が広がる浜。駅至近で利便性が高い穴場",                                     "access": "国府津駅から徒歩3分"},
    {"id": "ninomiya",    "name": "二宮海岸",           "area": "相模湾",   "lat": 35.3021, "lon": 139.2437, "shore_direction": 180, "seabed": "sand",       "depth_near": 4, "depth_far": 18, "surfer_spot": False, "notes": "二宮町の遠浅砂浜。湘南西端の穴場。比較的空いている",                         "access": "二宮駅から徒歩15分"},
    {"id": "zushi",       "name": "逗子海岸",           "area": "三浦半島", "lat": 35.2999, "lon": 139.5764, "shore_direction": 170, "seabed": "sand",       "depth_near": 4, "depth_far": 15, "surfer_spot": True,  "notes": "相模湾東端の砂浜。逗子マリーナ近く。サーファーも来る",                       "access": "逗子・葉山駅から徒歩15分"},
    {"id": "morito",      "name": "森戸海岸（葉山）",   "area": "三浦半島", "lat": 35.2670, "lon": 139.5836, "shore_direction": 265, "seabed": "sand_gravel","depth_near": 4, "depth_far": 15, "surfer_spot": False, "notes": "葉山の砂礫浜。ヨット多いがサーファー少なめ。静かな環境",                     "access": "逗子駅からバス15分"},
    {"id": "isshiki",     "name": "一色海岸（葉山）",   "area": "三浦半島", "lat": 35.2530, "lon": 139.5860, "shore_direction": 250, "seabed": "sand",       "depth_near": 4, "depth_far": 15, "surfer_spot": False, "notes": "葉山の砂浜。シロギスの有名ポイント。西向きで相模湾に面する",                 "access": "逗子駅からバス20分"},
    {"id": "chojakasaki", "name": "長者ヶ崎海岸",       "area": "三浦半島", "lat": 35.2454, "lon": 139.6219, "shore_direction": 220, "seabed": "sand_gravel","depth_near": 5, "depth_far": 18, "surfer_spot": False, "notes": "葉山・横須賀境の岬周辺。砂礫混じり。眺望よく穴場的存在",                     "access": "逗子駅からバス30分"},
    {"id": "akiya",       "name": "秋谷海岸",           "area": "三浦半島", "lat": 35.2316, "lon": 139.6142, "shore_direction": 195, "seabed": "sand",       "depth_near": 5, "depth_far": 18, "surfer_spot": False, "notes": "横須賀市の砂浜。立石公園近く。比較的空いている穴場",                         "access": "京急長沢駅から徒歩15分"},
    {"id": "miura",       "name": "三浦海岸",           "area": "三浦半島", "lat": 35.1389, "lon": 139.6234, "shore_direction": 130, "seabed": "sand",       "depth_near": 4, "depth_far": 15, "surfer_spot": True,  "notes": "神奈川屈指のシロギスポイント。白い砂浜が1km以上続く",                         "access": "三浦海岸駅から徒歩5分"},
    {"id": "tsukui",      "name": "津久井浜",           "area": "三浦半島", "lat": 35.1683, "lon": 139.6512, "shore_direction": 135, "seabed": "sand",       "depth_near": 4, "depth_far": 15, "surfer_spot": True,  "notes": "ウィンドサーフィンで有名な砂浜。シロギスも出る好ポイント",                   "access": "津久井浜駅から徒歩5分"},
    {"id": "ohama",       "name": "大浜海岸（三浦）",   "area": "三浦半島", "lat": 35.1343, "lon": 139.6167, "shore_direction": 185, "seabed": "sand",       "depth_near": 4, "depth_far": 15, "surfer_spot": False, "notes": "三浦半島南端の砂浜。三崎漁港近く。比較的空いている",                         "access": "三崎口駅からバスまたは車15分"},
    {"id": "kurihama",    "name": "久里浜海岸",         "area": "東京湾",   "lat": 35.2175, "lon": 139.7174, "shore_direction": 215, "seabed": "sand_mud",   "depth_near": 3, "depth_far": 12, "surfer_spot": False, "notes": "東京湾口の砂泥浜。波は穏やか。ペリー来航の地",                               "access": "京急久里浜駅から徒歩15分"},
    {"id": "nojima",      "name": "野島海岸（金沢八景）","area": "東京湾",  "lat": 35.3374, "lon": 139.6405, "shore_direction": 100, "seabed": "sand_mud",   "depth_near": 3, "depth_far": 10, "surfer_spot": False, "notes": "東京湾内で穏やか。砂泥底。波が静かでファミリー可",                           "access": "金沢八景駅から徒歩20分"},
    {"id": "futtsu",      "name": "富津海岸",           "area": "東京湾",   "lat": 35.3085, "lon": 139.8134, "shore_direction": 270, "seabed": "sand",       "depth_near": 2, "depth_far": 8,  "surfer_spot": False, "notes": "東京湾内の遠浅砂地。波が静かでシロギスに最適",                               "access": "佐貫町駅から車15分"},
    {"id": "hota",        "name": "保田海岸",           "area": "内房",     "lat": 35.1677, "lon": 139.8289, "shore_direction": 290, "seabed": "sand",       "depth_near": 3, "depth_far": 15, "surfer_spot": False, "notes": "内房の砂浜ポイント。シロギスの好場",                                           "access": "保田駅から徒歩5分"},
    {"id": "takeoka",     "name": "竹岡・関豊海岸",     "area": "内房",     "lat": 35.2762, "lon": 139.8012, "shore_direction": 280, "seabed": "sand_rock",  "depth_near": 5, "depth_far": 20, "surfer_spot": False, "notes": "砂と岩礁が混在。変化に富む地形",                                               "access": "竹岡駅から徒歩10分"},
    {"id": "ohara",       "name": "大原海岸",           "area": "外房",     "lat": 35.2536, "lon": 140.3734, "shore_direction": 95,  "seabed": "sand",       "depth_near": 4, "depth_far": 15, "surfer_spot": True,  "notes": "外房の砂地。外洋に面しているためうねりが入りやすい",                         "access": "大原駅から徒歩10分"},
    {"id": "onjuku",      "name": "御宿海岸",           "area": "外房",     "lat": 35.1822, "lon": 140.3864, "shore_direction": 110, "seabed": "sand",       "depth_near": 4, "depth_far": 18, "surfer_spot": True,  "notes": "白砂のシロギス名所。外洋うねり注意",                                           "access": "御宿駅から徒歩15分"},
    {"id": "moriya",      "name": "守谷海岸（勝浦）",   "area": "外房",     "lat": 35.1486, "lon": 140.3174, "shore_direction": 130, "seabed": "sand",       "depth_near": 5, "depth_far": 20, "surfer_spot": False, "notes": "透明度高い砂浜。遠浅でシロギスの好ポイント",                                 "access": "勝浦駅から徒歩20分"},
]

# ============================================================
# _marine_areas.json の内容
# ============================================================

MARINE_AREAS = {
    "areas": {
        "相模湾": {
            "lat": 34.70,
            "lon": 139.30,
            "center_lat": 35.29,
            "center_lon": 139.35,
            "fetch_km": 80
        },
        "三浦半島": {
            "lat": 34.70,
            "lon": 139.70,
            "center_lat": 35.22,
            "center_lon": 139.60,
            "fetch_km": 60
        },
        "東京湾": {
            "lat": 35.00,
            "lon": 140.00,
            "center_lat": 35.28,
            "center_lon": 139.73,
            "fetch_km": 30
        },
        "内房": {
            "lat": 35.00,
            "lon": 140.00,
            "center_lat": 35.22,
            "center_lon": 139.82,
            "fetch_km": 40
        },
        "外房": {
            "lat": 35.10,
            "lon": 141.00,
            "center_lat": 35.20,
            "center_lon": 140.37,
            "fetch_km": 100
        }
    },
    "fallbacks": [
        {"lat": 34.70, "lon": 139.30},
        {"lat": 34.70, "lon": 139.70},
        {"lat": 35.10, "lon": 141.00},
        {"lat": 35.00, "lon": 140.00}
    ]
}


def convert_spot(old: dict) -> dict:
    """旧スキーマのスポットを新スキーマに変換する。"""
    spot_id = old["id"]
    meta = SPOT_META.get(spot_id, {})
    seabed_info = SEABED_MAP.get(old.get("seabed", "sand"), SEABED_MAP["sand"])

    return {
        "slug": spot_id,
        "name": old["name"],
        "location": {
            "latitude": old["lat"],
            "longitude": old["lon"]
        },
        "area": {
            "prefecture": meta.get("prefecture", ""),
            "pref_slug": meta.get("pref_slug", ""),
            "area_name": meta.get("area", old.get("area", "")),
            "area_slug": meta.get("area_slug", ""),
            "city": meta.get("city", ""),
            "city_slug": meta.get("city_slug", "")
        },
        "physical_features": {
            "sea_bearing_deg": old.get("shore_direction"),
            "seabed_type": old.get("seabed", "sand"),
            "depth_near_m": old.get("depth_near"),
            "depth_far_m": old.get("depth_far"),
            "surfer_spot": old.get("surfer_spot", False)
        },
        "derived_features": {
            "bottom_kisugo_score": seabed_info["kisugo_score"],
            "terrain_summary": seabed_info["terrain_summary"]
        },
        "info": {
            "notes": old.get("notes", ""),
            "access": old.get("access", "")
        }
    }


def main():
    spots_dir = Path(__file__).parent / "spots"
    spots_dir.mkdir(exist_ok=True)

    # 各スポットを個別JSONファイルに書き出す
    for old_spot in FISHING_SPOTS:
        new_spot = convert_spot(old_spot)
        slug = old_spot["id"]
        out_path = spots_dir / f"{slug}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(new_spot, f, ensure_ascii=False, indent=2)
        print(f"  作成: spots/{slug}.json")

    # _marine_areas.json を書き出す
    marine_path = spots_dir / "_marine_areas.json"
    with open(marine_path, "w", encoding="utf-8") as f:
        json.dump(MARINE_AREAS, f, ensure_ascii=False, indent=2)
    print(f"  作成: spots/_marine_areas.json")

    print(f"\n完了: {len(FISHING_SPOTS)} スポット + _marine_areas.json を生成しました")
    print(f"出力先: {spots_dir}")


if __name__ == "__main__":
    main()
