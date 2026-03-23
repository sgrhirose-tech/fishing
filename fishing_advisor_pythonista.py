#!/usr/bin/env python3
"""
シロギス釣り場アドバイザー【Pythonista 3 版】
iPhone の「Pythonista 3」アプリで動かすためのバージョンです。

PC版との違い:
- コマンドライン引数なし（常に翌日の予報を取得）
- レポートをクリップボードに自動コピー
- ファイル保存先を Pythonista の Documents フォルダに変更

使い方:
1. Pythonista 3 アプリ（有料）をインストール
2. このファイルを Pythonista に貼り付けて保存
3. 再生ボタンで実行、または Apple Shortcuts から呼び出す
"""

import os
import json
import math
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9))

# Pythonista 固有モジュール（PC環境では None になる）
try:
    import clipboard as _clipboard_module
except ImportError:
    _clipboard_module = None

try:
    import console as _console_module
except ImportError:
    _console_module = None


# ============================================================
# 釣り場データ（固定情報）
# shore_direction: 海岸線が「海に向かって」いる方位（度）
#   例）南向きの砂浜 → 180
#   「追い風（オフショア）」＝陸から海への風＝この方向の逆から吹く風
# seabed: sand=砂地, sand_gravel=砂礫, sand_mud=砂泥, sand_rock=砂と岩礁混在
# ============================================================
FISHING_SPOTS = [
    # ---- 相模湾 ----
    {
        "id": "tsujido",
        "name": "辻堂海岸",
        "area": "相模湾",
        "lat": 35.3285,
        "lon": 139.4567,
        "shore_direction": 180,
        "seabed": "sand",
        "depth_near": 5,
        "depth_far": 15,
        "surfer_spot": True,
        "notes": "湘南の代表的な砂浜。サーファー多め。オフショア時は空きやすい",
        "access": "辻堂駅から徒歩15分",
    },
    {
        "id": "tsujido_park",
        "name": "辻堂海浜公園下",
        "area": "相模湾",
        "lat": 35.3358,
        "lon": 139.4384,
        "shore_direction": 180,
        "seabed": "sand",
        "depth_near": 5,
        "depth_far": 15,
        "surfer_spot": True,
        "notes": "辻堂海浜公園の目の前。辻堂海岸と隣接するが空いていることも多い",
        "access": "辻堂駅から徒歩20分（海浜公園経由）",
    },
    {
        "id": "katase",
        "name": "片瀬西浜（江ノ島）",
        "area": "相模湾",
        "lat": 35.3037,
        "lon": 139.4797,
        "shore_direction": 185,
        "seabed": "sand",
        "depth_near": 4,
        "depth_far": 12,
        "surfer_spot": True,
        "notes": "広い砂浜。サーファー多い。江ノ島の西側で多少風が遮られる",
        "access": "片瀬江ノ島駅から徒歩5分",
    },
    {
        "id": "katase_east",
        "name": "片瀬東浜（江ノ島）",
        "area": "相模湾",
        "lat": 35.3065,
        "lon": 139.4869,
        "shore_direction": 175,
        "seabed": "sand",
        "depth_near": 4,
        "depth_far": 12,
        "surfer_spot": True,
        "notes": "江ノ島の東側の砂浜。西浜よりやや静かな日もある",
        "access": "片瀬江ノ島駅から徒歩5分",
    },
    {
        "id": "oiso",
        "name": "大磯海岸",
        "area": "相模湾",
        "lat": 35.3049,
        "lon": 139.3093,
        "shore_direction": 175,
        "seabed": "sand_gravel",
        "depth_near": 5,
        "depth_far": 20,
        "surfer_spot": False,
        "notes": "砂と砂利が混じる。サーファー少なめ。良型シロギスが出ることも",
        "access": "大磯駅から徒歩10分",
    },
    {
        "id": "hiratsuka",
        "name": "平塚海岸",
        "area": "相模湾",
        "lat": 35.3197,
        "lon": 139.3479,
        "shore_direction": 180,
        "seabed": "sand",
        "depth_near": 4,
        "depth_far": 15,
        "surfer_spot": True,
        "notes": "遠浅の砂浜。平塚新港も近く便利",
        "access": "平塚駅からバス20分",
    },
    {
        "id": "sakawa",
        "name": "酒匂海岸（小田原）",
        "area": "相模湾",
        "lat": 35.2587,
        "lon": 139.1593,
        "shore_direction": 170,
        "seabed": "sand",
        "depth_near": 5,
        "depth_far": 20,
        "surfer_spot": False,
        "notes": "比較的空いている穴場。砂地が広がる",
        "access": "鴨宮駅から車10分",
    },
    {
        "id": "miyuki",
        "name": "御幸の浜（小田原）",
        "area": "相模湾",
        "lat": 35.2503,
        "lon": 139.1469,
        "shore_direction": 185,
        "seabed": "sand",
        "depth_near": 4,
        "depth_far": 18,
        "surfer_spot": False,
        "notes": "小田原漁港に隣接。比較的空いている砂浜。シロギスの良型が出る",
        "access": "小田原駅から徒歩20分",
    },
    {
        "id": "kozu",
        "name": "国府津海岸",
        "area": "相模湾",
        "lat": 35.2724,
        "lon": 139.1889,
        "shore_direction": 180,
        "seabed": "sand",
        "depth_near": 5,
        "depth_far": 20,
        "surfer_spot": False,
        "notes": "砂地が広がる浜。駅至近で利便性が高い穴場",
        "access": "国府津駅から徒歩3分",
    },
    {
        "id": "ninomiya",
        "name": "二宮海岸",
        "area": "相模湾",
        "lat": 35.3021,
        "lon": 139.2437,
        "shore_direction": 180,
        "seabed": "sand",
        "depth_near": 4,
        "depth_far": 18,
        "surfer_spot": False,
        "notes": "二宮町の遠浅砂浜。湘南西端の穴場。比較的空いている",
        "access": "二宮駅から徒歩15分",
    },
    # ---- 三浦半島 ----
    {
        "id": "zushi",
        "name": "逗子海岸",
        "area": "三浦半島",
        "lat": 35.2999,
        "lon": 139.5764,
        "shore_direction": 170,
        "seabed": "sand",
        "depth_near": 4,
        "depth_far": 15,
        "surfer_spot": True,
        "notes": "相模湾東端の砂浜。逗子マリーナ近く。サーファーも来る",
        "access": "逗子・葉山駅から徒歩15分",
    },
    {
        "id": "morito",
        "name": "森戸海岸（葉山）",
        "area": "三浦半島",
        "lat": 35.2670,
        "lon": 139.5836,
        "shore_direction": 265,
        "seabed": "sand_gravel",
        "depth_near": 4,
        "depth_far": 15,
        "surfer_spot": False,
        "notes": "葉山の砂礫浜。ヨット多いがサーファー少なめ。静かな環境",
        "access": "逗子駅からバス15分",
    },
    {
        "id": "isshiki",
        "name": "一色海岸（葉山）",
        "area": "三浦半島",
        "lat": 35.2530,
        "lon": 139.5860,
        "shore_direction": 250,
        "seabed": "sand",
        "depth_near": 4,
        "depth_far": 15,
        "surfer_spot": False,
        "notes": "葉山の砂浜。シロギスの有名ポイント。西向きで相模湾に面する",
        "access": "逗子駅からバス20分",
    },
    {
        "id": "chojakasaki",
        "name": "長者ヶ崎海岸",
        "area": "三浦半島",
        "lat": 35.2454,
        "lon": 139.6219,
        "shore_direction": 220,
        "seabed": "sand_gravel",
        "depth_near": 5,
        "depth_far": 18,
        "surfer_spot": False,
        "notes": "葉山・横須賀境の岬周辺。砂礫混じり。眺望よく穴場的存在",
        "access": "逗子駅からバス30分",
    },
    {
        "id": "akiya",
        "name": "秋谷海岸",
        "area": "三浦半島",
        "lat": 35.2316,
        "lon": 139.6142,
        "shore_direction": 195,
        "seabed": "sand",
        "depth_near": 5,
        "depth_far": 18,
        "surfer_spot": False,
        "notes": "横須賀市の砂浜。立石公園近く。比較的空いている穴場",
        "access": "京急長沢駅から徒歩15分",
    },
    {
        "id": "miura",
        "name": "三浦海岸",
        "area": "三浦半島",
        "lat": 35.1389,
        "lon": 139.6234,
        "shore_direction": 130,
        "seabed": "sand",
        "depth_near": 4,
        "depth_far": 15,
        "surfer_spot": True,
        "notes": "神奈川屈指のシロギスポイント。白い砂浜が1km以上続く",
        "access": "三浦海岸駅から徒歩5分",
    },
    {
        "id": "tsukui",
        "name": "津久井浜",
        "area": "三浦半島",
        "lat": 35.1683,
        "lon": 139.6512,
        "shore_direction": 135,
        "seabed": "sand",
        "depth_near": 4,
        "depth_far": 15,
        "surfer_spot": True,
        "notes": "ウィンドサーフィンで有名な砂浜。シロギスも出る好ポイント",
        "access": "津久井浜駅から徒歩5分",
    },
    {
        "id": "ohama",
        "name": "大浜海岸（三浦）",
        "area": "三浦半島",
        "lat": 35.1343,
        "lon": 139.6167,
        "shore_direction": 185,
        "seabed": "sand",
        "depth_near": 4,
        "depth_far": 15,
        "surfer_spot": False,
        "notes": "三浦半島南端の砂浜。三崎漁港近く。比較的空いている",
        "access": "三崎口駅からバスまたは車15分",
    },
    {
        "id": "kurihama",
        "name": "久里浜海岸",
        "area": "東京湾",
        "lat": 35.2175,
        "lon": 139.7174,
        "shore_direction": 215,
        "seabed": "sand_mud",
        "depth_near": 3,
        "depth_far": 12,
        "surfer_spot": False,
        "notes": "東京湾口の砂泥浜。波は穏やか。ペリー来航の地",
        "access": "京急久里浜駅から徒歩15分",
    },
    # ---- 東京湾 ----
    {
        "id": "nojima",
        "name": "野島海岸（金沢八景）",
        "area": "東京湾",
        "lat": 35.3374,
        "lon": 139.6405,
        "shore_direction": 100,
        "seabed": "sand_mud",
        "depth_near": 3,
        "depth_far": 10,
        "surfer_spot": False,
        "notes": "東京湾内で穏やか。砂泥底。波が静かでファミリー可",
        "access": "金沢八景駅から徒歩20分",
    },
    {
        "id": "futtsu",
        "name": "富津海岸",
        "area": "東京湾",
        "lat": 35.3085,
        "lon": 139.8134,
        "shore_direction": 270,
        "seabed": "sand",
        "depth_near": 2,
        "depth_far": 8,
        "surfer_spot": False,
        "notes": "東京湾内の遠浅砂地。波が静かでシロギスに最適",
        "access": "佐貫町駅から車15分",
    },
    # ---- 内房 ----
    {
        "id": "hota",
        "name": "保田海岸",
        "area": "内房",
        "lat": 35.1677,
        "lon": 139.8289,
        "shore_direction": 290,
        "seabed": "sand",
        "depth_near": 3,
        "depth_far": 15,
        "surfer_spot": False,
        "notes": "内房の砂浜ポイント。シロギスの好場",
        "access": "保田駅から徒歩5分",
    },
    {
        "id": "takeoka",
        "name": "竹岡・関豊海岸",
        "area": "内房",
        "lat": 35.2762,
        "lon": 139.8012,
        "shore_direction": 280,
        "seabed": "sand_rock",
        "depth_near": 5,
        "depth_far": 20,
        "surfer_spot": False,
        "notes": "砂と岩礁が混在。変化に富む地形",
        "access": "竹岡駅から徒歩10分",
    },
    # ---- 外房 ----
    {
        "id": "ohara",
        "name": "大原海岸",
        "area": "外房",
        "lat": 35.2536,
        "lon": 140.3734,
        "shore_direction": 95,
        "seabed": "sand",
        "depth_near": 4,
        "depth_far": 15,
        "surfer_spot": True,
        "notes": "外房の砂地。外洋に面しているためうねりが入りやすい",
        "access": "大原駅から徒歩10分",
    },
    {
        "id": "onjuku",
        "name": "御宿海岸",
        "area": "外房",
        "lat": 35.1822,
        "lon": 140.3864,
        "shore_direction": 110,
        "seabed": "sand",
        "depth_near": 4,
        "depth_far": 18,
        "surfer_spot": True,
        "notes": "白砂のシロギス名所。外洋うねり注意",
        "access": "御宿駅から徒歩15分",
    },
    {
        "id": "moriya",
        "name": "守谷海岸（勝浦）",
        "area": "外房",
        "lat": 35.1486,
        "lon": 140.3174,
        "shore_direction": 130,
        "seabed": "sand",
        "depth_near": 5,
        "depth_far": 20,
        "surfer_spot": False,
        "notes": "透明度高い砂浜。遠浅でシロギスの好ポイント",
        "access": "勝浦駅から徒歩20分",
    },
]


# ============================================================
# 気象・海洋データ取得
# Open-Meteo API（無料・API key不要・ECMWFモデル使用）
# ============================================================

def fetch_weather(lat, lon, date_str):
    base_url = "https://api.open-meteo.com/v1/forecast"
    params = [
        ("latitude", lat),
        ("longitude", lon),
        ("daily", "wind_speed_10m_max"),
        ("daily", "wind_direction_10m_dominant"),
        ("daily", "precipitation_sum"),
        ("daily", "weather_code"),
        ("wind_speed_unit", "ms"),
        ("timezone", "Asia/Tokyo"),
        ("start_date", date_str),
        ("end_date", date_str),
    ]
    try:
        full_url = base_url + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(full_url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"  [警告] 気象データ取得失敗 ({lat},{lon}): {e}")
        return {}


def fetch_marine(lat, lon, date_str):
    """波高データを Open-Meteo Marine API から取得（外洋スポット向け）"""
    base_url = "https://marine-api.open-meteo.com/v1/marine"
    params = [
        ("latitude", lat),
        ("longitude", lon),
        ("daily", "wave_height_max"),
        ("daily", "dominant_wave_direction"),
        ("timezone", "Asia/Tokyo"),
        ("start_date", date_str),
        ("end_date", date_str),
    ]
    try:
        full_url = base_url + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(full_url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 400:
            # 沿岸・湾内は海洋波浪モデルの対象外のため正常な挙動
            return {}
        print(f"  [警告] 波浪データ取得失敗 ({lat},{lon}): {e}")
        return {}
    except Exception as e:
        print(f"  [警告] 波浪データ取得失敗 ({lat},{lon}): {e}")
        return {}


def fetch_sst_noaa(lat, lon, date_str):
    """
    NOAA CoastWatch ERDDAP (jplMURSST41) から海面水温を取得
    MUR SST: 複数衛星データの複合解析、解像度 0.01°（約1km）、APIキー不要
    https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.html
    ※ MUR SST は約1日の遅延があるため、対象日の前日データを使用
    """
    target = datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)
    noaa_date = target.strftime("%Y-%m-%dT09:00:00Z")
    lat_str = f"{lat:.4f}"
    lon_str = f"{lon:.4f}"
    url = (
        f"https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.json"
        f"?analysed_sst[({noaa_date}):1:({noaa_date})]"
        f"[({lat_str}):1:({lat_str})]"
        f"[({lon_str}):1:({lon_str})]"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = data.get("table", {}).get("rows", [])
        if rows and rows[0] and rows[0][3] is not None:
            return float(rows[0][3])
        return None
    except Exception as e:
        print(f"  [警告] 水温データ取得失敗 ({lat},{lon}): {e}")
        return None


# ============================================================
# スコアリング関数
# ============================================================

def angle_diff(a, b):
    diff = abs(a - b) % 360
    return min(diff, 360 - diff)


def calc_wind_score(wind_speed, wind_dir, shore_direction):
    inland_dir = (shore_direction + 180) % 360
    diff = angle_diff(wind_dir, inland_dir)

    if diff <= 45:
        dir_label = "追い風（オフショア）"
        dir_pts = 10
        is_surfer_friendly = False
    elif diff <= 90:
        dir_label = "やや追い風"
        dir_pts = 7
        is_surfer_friendly = False
    elif diff <= 135:
        dir_label = "横風〜やや向かい風"
        dir_pts = 4
        is_surfer_friendly = True
    else:
        dir_label = "向かい風（オンショア）"
        dir_pts = 1
        is_surfer_friendly = True

    if wind_speed < 3.0:
        spd_label = f"{wind_speed:.1f}m/s（微風）"
        spd_pts = 15
    elif wind_speed < 5.0:
        spd_label = f"{wind_speed:.1f}m/s（弱風）"
        spd_pts = 12
    elif wind_speed < 7.0:
        spd_label = f"{wind_speed:.1f}m/s（やや強い）"
        spd_pts = 7
    elif wind_speed < 10.0:
        spd_label = f"{wind_speed:.1f}m/s（強風）"
        spd_pts = 3
    else:
        spd_label = f"{wind_speed:.1f}m/s（非常に強い）"
        spd_pts = 0

    return {
        "dir_pts": dir_pts,
        "spd_pts": spd_pts,
        "dir_label": dir_label,
        "spd_label": spd_label,
        "surfer_friendly": is_surfer_friendly,
        "total_pts": dir_pts + spd_pts,
    }


def calc_wave_score(wave_height):
    if wave_height is None:
        return {"pts": 10, "label": "データなし"}
    if wave_height < 0.3:
        return {"pts": 20, "label": f"{wave_height:.1f}m（ベタ凪）"}
    elif wave_height < 0.5:
        return {"pts": 16, "label": f"{wave_height:.1f}m（穏やか）"}
    elif wave_height < 0.8:
        return {"pts": 10, "label": f"{wave_height:.1f}m（やや波あり）"}
    elif wave_height < 1.2:
        return {"pts": 4, "label": f"{wave_height:.1f}m（波あり・釣りにくい）"}
    else:
        return {"pts": 0, "label": f"{wave_height:.1f}m（荒れ・釣り不可）"}


def calc_temp_score(sst):
    if sst is None:
        return {"pts": 12, "label": "データなし"}
    if 20.0 <= sst <= 24.0:
        return {"pts": 20, "label": f"{sst:.1f}°C（最適）"}
    elif 18.0 <= sst < 20.0 or 24.0 < sst <= 26.0:
        return {"pts": 15, "label": f"{sst:.1f}°C（良好）"}
    elif 15.0 <= sst < 18.0 or 26.0 < sst <= 28.0:
        return {"pts": 7, "label": f"{sst:.1f}°C（やや不向き）"}
    else:
        return {"pts": 2, "label": f"{sst:.1f}°C（厳しい）"}


def calc_seabed_score(seabed):
    table = {
        "sand":        (35, "砂地（最適）"),
        "sand_gravel": (25, "砂礫（良好）"),
        "sand_mud":    (20, "砂泥（可）"),
        "sand_rock":   (15, "砂と岩礁混在（やや不向き）"),
        "rock":        (0,  "岩礁（不向き）"),
        "mud":         (3,  "泥底（不向き）"),
    }
    pts, label = table.get(seabed, (10, seabed))
    return {"pts": pts, "label": label}


# ============================================================
# 釣り場スコアの総合計算
# ============================================================

def score_spot(spot, weather_data, marine_data, sst_noaa=None):
    details = {}

    sb = calc_seabed_score(spot["seabed"])
    seabed_pts = sb["pts"]
    details["seabed"] = sb["label"]

    wind_speed = None
    wind_dir = None
    if weather_data and "daily" in weather_data:
        d = weather_data["daily"]
        spd_list = d.get("wind_speed_10m_max", [])
        dir_list = d.get("wind_direction_10m_dominant", [])
        if spd_list and spd_list[0] is not None:
            wind_speed = spd_list[0]
        if dir_list and dir_list[0] is not None:
            wind_dir = dir_list[0]

    if wind_speed is not None and wind_dir is not None:
        ws = calc_wind_score(wind_speed, wind_dir, spot["shore_direction"])
        wind_pts = ws["total_pts"]
        details["wind_speed"] = ws["spd_label"]
        details["wind_dir"] = f"{direction_label(wind_dir)}（{ws['dir_label']}）"
        details["surfer_friendly"] = ws["surfer_friendly"]
    else:
        wind_pts = 12
        details["wind_speed"] = "データなし"
        details["wind_dir"] = "データなし"
        details["surfer_friendly"] = None

    wave_height = None
    if marine_data and "daily" in marine_data:
        d = marine_data["daily"]
        wh_list = d.get("wave_height_max", [])
        if wh_list and wh_list[0] is not None:
            wave_height = wh_list[0]
    wv = calc_wave_score(wave_height)
    wave_pts = wv["pts"]
    details["wave"] = wv["label"]

    # NOAA ERDDAP データを優先、なければフォールバック
    sst = sst_noaa
    tp = calc_temp_score(sst)
    temp_pts = tp["pts"]
    details["sst"] = tp["label"]

    precip = None
    if weather_data and "daily" in weather_data:
        pr_list = weather_data["daily"].get("precipitation_sum", [])
        if pr_list and pr_list[0] is not None:
            precip = pr_list[0]

    rain_penalty = 0
    if precip is not None:
        details["precip"] = f"{precip:.1f}mm"
        if precip > 10:
            rain_penalty = -30
            details["rain_warning"] = "大雨（釣行非推奨）"
        elif precip > 5:
            rain_penalty = -15
            details["rain_warning"] = "雨（注意）"
        elif precip > 1:
            rain_penalty = -5
            details["rain_warning"] = "小雨"
    else:
        details["precip"] = "データなし"

    total = seabed_pts + wind_pts + wave_pts + temp_pts + rain_penalty

    return {
        "spot": spot,
        "total": total,
        "scores": {
            "seabed": seabed_pts,
            "wind": wind_pts,
            "wave": wave_pts,
            "temp": temp_pts,
            "rain_penalty": rain_penalty,
        },
        "details": details,
    }


# ============================================================
# ユーティリティ
# ============================================================

def direction_label(deg):
    dirs = [
        "北", "北北東", "北東", "東北東",
        "東", "東南東", "南東", "南南東",
        "南", "南南西", "南西", "西南西",
        "西", "西北西", "北西", "北北西",
    ]
    idx = int((deg + 11.25) / 22.5) % 16
    return dirs[idx]


# ============================================================
# テキストレポート生成
# ============================================================

RANK_MARKS = ["1位", "2位", "3位", "4位", "5位"]


def generate_report(scored_spots, target_date):
    ranked = sorted(scored_spots, key=lambda x: x["total"], reverse=True)
    now_str = datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")

    lines = []
    lines.append("=" * 62)
    lines.append(f"   シロギス釣り場おすすめレポート  {target_date}")
    lines.append("=" * 62)
    lines.append(f"   作成: {now_str} JST")
    lines.append("")

    lines.append("【おすすめ釣り場 トップ5】")
    lines.append("")
    for i, r in enumerate(ranked[:5]):
        spot = r["spot"]
        d = r["details"]
        mark = RANK_MARKS[i]

        lines.append(f"  {mark}: {spot['name']}（{spot['area']}）  [{r['total']}点]")
        lines.append(f"         底質   : {d['seabed']}")
        lines.append(f"         海水温 : {d['sst']}")
        lines.append(f"         波高   : {d['wave']}")
        lines.append(f"         風速   : {d['wind_speed']}")
        lines.append(f"         風向   : {d['wind_dir']}")
        lines.append(f"         降水量 : {d['precip']}")

        if d.get("rain_warning"):
            lines.append(f"         !! {d['rain_warning']}")

        sf = d.get("surfer_friendly")
        if sf is False:
            lines.append("         >> オフショア: サーファー少なく釣り場が空きやすい")
        elif sf is True and spot.get("surfer_spot"):
            lines.append("         >> オンショア: サーファーが来やすい（混雑注意）")

        lines.append(f"         アクセス: {spot['access']}")
        lines.append(f"         memo   : {spot['notes']}")
        lines.append("")

    lines.append("【エリア別ベスト】")
    areas = {}
    for r in ranked:
        area = r["spot"]["area"]
        if area not in areas:
            areas[area] = r
    for area, r in areas.items():
        lines.append(f"  {area:6s}: {r['spot']['name']} ({r['total']}点)")

    lines.append("")
    lines.append("【スコアの見方】")
    lines.append("  100点満点（底質35点 + 風25点 + 波20点 + 水温20点）")
    lines.append("  雨が多い場合はペナルティあり（最大-30点）")
    lines.append("")
    lines.append("【注意事項】")
    lines.append("  ・予報は数値モデルによる推定値です。出発前に最新情報をご確認ください")
    lines.append("  ・天候の急変には十分注意してください")
    lines.append("  ・気象データ: Open-Meteo API（ECMWFモデル、約9kmメッシュ）")
    lines.append("=" * 62)

    return "\n".join(lines)


# ============================================================
# Claude API によるAIコメント（オプション）
# Pythonista の設定画面または以下の変数に直接APIキーを入力してください
# ============================================================

# ★ Anthropic API キーをここに直接書いてもOKです（Pythonista用）
# 例: ANTHROPIC_API_KEY = "sk-ant-xxxxxxxxxxxx"
# セキュリティ注意: 他人とファイルを共有する場合は消してください
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def claude_ai_comment(scored_spots):
    api_key = ANTHROPIC_API_KEY
    if not api_key:
        return ""

    try:
        import anthropic
    except ImportError:
        print("[情報] anthropic ライブラリが未インストールです（Pythonistaでは使用不可）")
        return ""

    ranked = sorted(scored_spots, key=lambda x: x["total"], reverse=True)
    top5 = []
    for i, r in enumerate(ranked[:5]):
        d = r["details"]
        top5.append({
            "rank": i + 1,
            "name": r["spot"]["name"],
            "area": r["spot"]["area"],
            "score": r["total"],
            "seabed": d["seabed"],
            "sst": d["sst"],
            "wave": d["wave"],
            "wind_speed": d["wind_speed"],
            "wind_dir": d["wind_dir"],
            "surfer_friendly": d.get("surfer_friendly"),
            "precip": d["precip"],
            "rain_warning": d.get("rain_warning", "なし"),
        })

    prompt = f"""あなたは投げ釣りでシロギス（白ギス）を専門とする釣りガイドです。
以下の釣り場スコアデータをもとに、明日の釣行計画に役立つ具体的なアドバイスを
日本語で書いてください。

## 上位5釣り場のデータ
{json.dumps(top5, ensure_ascii=False, indent=2)}

## シロギス釣りの基礎知識（参考にしてください）
- シロギスは砂地を好む魚。岩礁や泥地には少ない
- 適水温は18〜26°C、最も活性が高いのは20〜24°C
- 投げ釣りは追い風（オフショア）だと仕掛けが遠くまで飛ぶ
- オフショアの日はサーファーが来にくく釣り場が空く
- 波高0.5m以上は釣りにくい。1m以上は危険
- 大雨の後は海が濁り釣果が落ちやすい

## 出力形式
1. **1位のおすすめポイント**: 具体的なアドバイス（2〜3文）
2. **2位・3位**: 簡単なコメント（各1〜2文）
3. **総合コメント**: 今日の全体的な釣況（1〜2文）

親しみやすい言葉で、釣り師が聞いて役立つ情報を簡潔に伝えてください。"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text
    except Exception as e:
        print(f"[警告] Claude API エラー: {e}")
        return ""


# ============================================================
# メイン処理（Pythonista版）
# ============================================================

def main():
    # 常に「翌日」の予報を取得（コマンドライン引数なし）
    target_date = (datetime.now(JST) + timedelta(days=1)).strftime("%Y-%m-%d")

    # Pythonista のコンソールに色付き表示
    if _console_module:
        _console_module.clear()

    print("シロギス釣り場アドバイザー")
    print(f"対象日: {target_date}")
    print(f"釣り場数: {len(FISHING_SPOTS)}か所")
    print("気象・海洋データを取得しています...\n")

    scored_spots = []
    for spot in FISHING_SPOTS:
        print(f"  {spot['name']}...", end="", flush=True)
        weather = fetch_weather(spot["lat"], spot["lon"], target_date)
        marine = fetch_marine(spot["lat"], spot["lon"], target_date)
        sst = fetch_sst_noaa(spot["lat"], spot["lon"], target_date)
        result = score_spot(spot, weather, marine, sst_noaa=sst)
        scored_spots.append(result)
        print(f" {result['total']}点")

    print()
    report = generate_report(scored_spots, target_date)

    # Claude API コメント追加（ANTHROPIC_API_KEY 設定時のみ）
    ai_text = claude_ai_comment(scored_spots)
    if ai_text:
        report += "\n\n" + "=" * 62 + "\n"
        report += "【AIアドバイス（Claude）】\n"
        report += "=" * 62 + "\n"
        report += ai_text + "\n"
        report += "=" * 62

    print(report)

    # クリップボードにコピー（Pythonista環境のみ）
    if _clipboard_module:
        _clipboard_module.set(report)
        print("\nレポートをクリップボードにコピーしました")
        print("メモ帳やメッセージアプリに貼り付けて使えます")

    # ファイルに保存（Pythonista の Documents フォルダ）
    docs_dir = os.path.expanduser("~/Documents")
    output_file = os.path.join(docs_dir, f"fishing_report_{target_date}.txt")
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"レポートを保存しました: {output_file}")
    except Exception as e:
        print(f"[情報] ファイル保存をスキップ: {e}")


if __name__ == "__main__":
    main()
