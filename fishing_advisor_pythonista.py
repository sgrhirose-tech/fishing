#!/usr/bin/env python3
"""
シロギス釣り場アドバイザー【Pythonista 3 版】
iPhone の「Pythonista 3」アプリで動かすためのバージョンです。

釣り場の固定情報（底質・海方向・地形）は spots/ フォルダ内の JSON から読み込みます。
JSON ファイルは build_spots_complete.py で事前に生成してください。

使い方:
1. build_spots_complete.py を実行して spots/ フォルダを生成
2. このファイルと spots/ フォルダを Pythonista の同じディレクトリに配置
3. 再生ボタンで実行、または Apple Shortcuts から呼び出す
"""

import os
import json
import math
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

# エリアごとの波高取得用沖合代理座標は spots/_marine_areas.json から読み込む
_MARINE_COORD_CACHE: dict = {}


def _load_marine_areas(json_path="spots/_marine_areas.json"):
    """spots/_marine_areas.json からエリア定義・フォールバック座標を読み込む。
    ファイルがない場合は空で返す（_MARINE_FALLBACKS のチェーンで補完）。"""
    p = Path(__file__).parent / json_path
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        proxy = {
            name: (v["lat"], v["lon"])
            for name, v in data.get("areas", {}).items()
        }
        fallbacks = [(v["lat"], v["lon"]) for v in data.get("fallbacks", [])]
        return proxy, fallbacks
    except Exception as e:
        print(f"[警告] marine_areas 読み込み失敗: {e}")
        return {}, []


MARINE_PROXY, _MARINE_FALLBACKS = _load_marine_areas()


def _load_area_centers(json_path="spots/_marine_areas.json"):
    """エリア名 → 地理的中心座標 dict を返す（エリア分類用）"""
    p = Path(__file__).parent / json_path
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return {
            name: (v["center_lat"], v["center_lon"])
            for name, v in data.get("areas", {}).items()
            if "center_lat" in v and "center_lon" in v
        }
    except Exception:
        return {}


def _assign_area(spot: dict, area_centers: dict) -> str:
    """スポット座標に最近傍のエリア名を返す"""
    loc = spot.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    if lat is None or lon is None:
        return ""
    return min(area_centers, key=lambda n: (area_centers[n][0]-lat)**2 + (area_centers[n][1]-lon)**2)


def _select_areas(area_names: list):
    """エリアを複数選択して list を返す。全選択/キャンセルなら None を返す"""
    try:
        import dialogs  # Pythonista only
        chosen = dialogs.list_dialog("エリアを選択（複数可）", area_names, multiple=True)
        return chosen if chosen else None
    except (ImportError, TypeError):
        # dialogs 未対応、または multiple パラメータ非対応の旧バージョン
        pass
    # フォールバック: コンソール入力（カンマ区切り）
    print("エリアを選択してください（複数可, カンマ区切り。Enter でスキップ）:")
    for i, name in enumerate(area_names, 1):
        print(f"  {i}. {name}")
    try:
        ans = input("番号> ").strip()
        if not ans:
            return None
        selected = []
        for token in ans.split(","):
            token = token.strip()
            if token.isdigit():
                idx = int(token) - 1
                if 0 <= idx < len(area_names):
                    selected.append(area_names[idx])
        return selected or None
    except EOFError:
        return None


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
# スポットデータ読み込み（spots/*.json から）
# ============================================================

def load_spots(spots_dir="spots"):
    """spots/ フォルダ内の JSON ファイルから全スポットデータを読み込む"""
    spots_path = Path(spots_dir)
    if not spots_path.exists():
        print(f"[エラー] {spots_dir} フォルダが見つかりません")
        return []
    spots = []
    for p in sorted(spots_path.glob("*.json")):
        if p.stem.startswith("_"):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                spots.append(json.load(f))
        except Exception as e:
            print(f"[警告] {p.name} の読み込みに失敗: {e}")
    return spots


# JSON フィールドのアクセサ
def spot_lat(s):
    return s["location"]["latitude"]

def spot_lon(s):
    return s["location"]["longitude"]

def spot_name(s):
    return s["name"]

def spot_area(s):
    a = s.get("area", {})
    return a.get("city") or a.get("prefecture") or "不明"

def spot_bearing(s):
    """海方向（度）。OSM 海岸線から算出。取得失敗時は None。"""
    return s.get("physical_features", {}).get("sea_bearing_deg")

def spot_kisugo(s):
    """シロギス適性スコア 0〜100（derived_features.bottom_kisugo_score）"""
    return s.get("derived_features", {}).get("bottom_kisugo_score", 50)

def spot_terrain(s):
    """地形サマリー文字列（derived_features.terrain_summary）"""
    return s.get("derived_features", {}).get("terrain_summary", "")


def get_marine_proxy(lat, lon):
    """最近傍の沖合代理座標（lat, lon）を返す"""
    return min(
        MARINE_PROXY.values(),
        key=lambda p: (p[0] - lat) ** 2 + (p[1] - lon) ** 2,
    )


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
    """波高データを Open-Meteo Marine API から取得（沖合代理座標で呼ぶこと）"""
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
            # 湾内・沿岸は海洋波浪モデルの対象外のため正常
            return {}
        print(f"  [警告] 波浪データ取得失敗 ({lat},{lon}): {e}")
        return {}
    except Exception as e:
        print(f"  [警告] 波浪データ取得失敗 ({lat},{lon}): {e}")
        return {}


def fetch_marine_with_fallback(lat, lon, date_str):
    """プライマリ座標 → _MARINE_FALLBACKS の順で波高データを取得する。
    フォールバック座標を使用した場合は '_is_fallback': True を付加して返す。"""
    cache_key = (round(lat, 2), round(lon, 2))

    # キャッシュ済みの成功座標があれば直接使用
    if cache_key in _MARINE_COORD_CACHE:
        c = _MARINE_COORD_CACHE[cache_key]
        result = fetch_marine(c[0], c[1], date_str)
        if result:
            result["_is_fallback"] = c[2]
            return result

    # 1次: プライマリ座標
    result = fetch_marine(lat, lon, date_str)
    if result:
        _MARINE_COORD_CACHE[cache_key] = (lat, lon, False)
        return result

    # 2次: フォールバック座標を距離順に試行
    fallbacks = sorted(
        _MARINE_FALLBACKS,
        key=lambda p: (p[0] - lat) ** 2 + (p[1] - lon) ** 2,
    )
    for fb_lat, fb_lon in fallbacks:
        result = fetch_marine(fb_lat, fb_lon, date_str)
        if result:
            result["_is_fallback"] = True
            _MARINE_COORD_CACHE[cache_key] = (fb_lat, fb_lon, True)
            return result

    return {}


def fetch_sst_noaa(lat, lon, date_str):
    """
    海面水温を取得（NOAA ERDDAP → Open-Meteo Marine の順で試行）

    1次: NOAA CoastWatch ERDDAP (jplMURSST41) MUR SST
    2次: Open-Meteo Marine API（沿岸では 400 になる場合あり）
    """
    lat_str = f"{lat:.4f}"
    lon_str = f"{lon:.4f}"

    # --- 1. NOAA ERDDAP ---
    url = (
        "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.json"
        f"?analysed_sst%5B(last)%5D%5B({lat_str})%5D%5B({lon_str})%5D"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = data.get("table", {}).get("rows", [])
        if rows and rows[0] and rows[0][3] is not None:
            return float(rows[0][3])
    except Exception as e:
        print(f"  [情報] NOAA水温取得失敗 ({lat},{lon}): {e}")

    # --- 2. フォールバック: Open-Meteo Marine API ---
    base_url = "https://marine-api.open-meteo.com/v1/marine"
    params = [
        ("latitude", lat),
        ("longitude", lon),
        ("hourly", "sea_surface_temperature"),
        ("timezone", "Asia/Tokyo"),
        ("start_date", date_str),
        ("end_date", date_str),
    ]
    try:
        full_url = base_url + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(full_url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        sst_list = data.get("hourly", {}).get("sea_surface_temperature", [])
        valid = [v for v in sst_list if v is not None]
        if valid:
            return sum(valid) / len(valid)
    except Exception:
        pass

    return None


# ============================================================
# スコアリング関数
# ============================================================

def angle_diff(a, b):
    diff = abs(a - b) % 360
    return min(diff, 360 - diff)


def calc_wind_score(wind_speed, wind_dir, sea_bearing_deg):
    """
    sea_bearing_deg: 海方向（度）。None の場合は方位スコアなし（中立値）。
    """
    if sea_bearing_deg is not None:
        # 陸方向 = 海方向の逆
        inland_dir = (sea_bearing_deg + 180) % 360
        diff = angle_diff(wind_dir, inland_dir)
        if diff <= 45:
            dir_label = "追い風（オフショア）"
            dir_pts = 15
            is_surfer_friendly = False
        elif diff <= 90:
            dir_label = "やや追い風"
            dir_pts = 10
            is_surfer_friendly = False
        elif diff <= 135:
            dir_label = "横風〜やや向かい風"
            dir_pts = 3
            is_surfer_friendly = True
        else:
            dir_label = "向かい風（オンショア）"
            dir_pts = 6
            is_surfer_friendly = True
    else:
        dir_pts = 7
        dir_label = "方位データなし"
        is_surfer_friendly = None

    if wind_speed < 3.0:
        spd_label = f"{wind_speed:.1f}m/s（微風）"
        spd_pts = 25
    elif wind_speed < 5.0:
        spd_label = f"{wind_speed:.1f}m/s（弱風）"
        spd_pts = 20
    elif wind_speed < 7.0:
        spd_label = f"{wind_speed:.1f}m/s（やや強い）"
        spd_pts = 10
    else:
        spd_label = f"{wind_speed:.1f}m/s（釣行困難）"
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
        return {"pts": 15, "label": "データなし"}
    if wave_height < 0.3:
        return {"pts": 30, "label": f"{wave_height:.1f}m（ベタ凪）"}
    elif wave_height < 0.5:
        return {"pts": 24, "label": f"{wave_height:.1f}m（穏やか）"}
    elif wave_height < 0.8:
        return {"pts": 15, "label": f"{wave_height:.1f}m（やや波あり）"}
    elif wave_height < 1.2:
        return {"pts": 5, "label": f"{wave_height:.1f}m（波あり・釣りにくい）"}
    else:
        return {"pts": 0, "label": f"{wave_height:.1f}m（荒れ・釣り不可）"}


def calc_temp_score(sst):
    if sst is None:
        return {"pts": 8, "label": "データなし"}
    if 20.0 <= sst <= 24.0:
        return {"pts": 15, "label": f"{sst:.1f}°C（最適）"}
    elif 18.0 <= sst < 20.0 or 24.0 < sst <= 26.0:
        return {"pts": 11, "label": f"{sst:.1f}°C（良好）"}
    elif 15.0 <= sst < 18.0 or 26.0 < sst <= 28.0:
        return {"pts": 5, "label": f"{sst:.1f}°C（やや不向き）"}
    else:
        return {"pts": 1, "label": f"{sst:.1f}°C（厳しい）"}


def calc_seabed_score(kisugo_score):
    """
    kisugo_score: 0〜100（derived_features.bottom_kisugo_score）
    → シロギス適性に基づき 0〜15点に換算
    """
    pts = round(kisugo_score / 100 * 15)
    if kisugo_score >= 80:
        label = "砂地主体（シロギス最適）"
    elif kisugo_score >= 60:
        label = "砂混じり（良好）"
    elif kisugo_score >= 40:
        label = "混合底（可）"
    else:
        label = "砂以外主体（不向き）"
    return {"pts": pts, "label": label}


# ============================================================
# 釣り場スコアの総合計算
# ============================================================

def score_spot(spot, weather_data, marine_data, sst_noaa=None):
    details = {}

    # 底質スコア（JSON の bottom_kisugo_score を使用）
    kisugo_score = spot_kisugo(spot)
    sb = calc_seabed_score(kisugo_score)
    seabed_pts = sb["pts"]
    details["seabed"] = sb["label"]
    details["terrain"] = spot_terrain(spot)

    # 風スコア
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

    sea_bearing_deg = spot_bearing(spot)
    if wind_speed is not None and wind_dir is not None:
        ws = calc_wind_score(wind_speed, wind_dir, sea_bearing_deg)
        wind_pts = ws["total_pts"]
        details["wind_speed"] = ws["spd_label"]
        details["wind_dir"] = f"{direction_label(wind_dir)}（{ws['dir_label']}）"
        details["surfer_friendly"] = ws["surfer_friendly"]
    else:
        wind_pts = 20
        details["wind_speed"] = "データなし"
        details["wind_dir"] = "データなし"
        details["surfer_friendly"] = None

    # 波高スコア
    wave_height = None
    wave_is_fallback = marine_data.get("_is_fallback", False)
    if marine_data and "daily" in marine_data:
        d = marine_data["daily"]
        wh_list = d.get("wave_height_max", [])
        if wh_list and wh_list[0] is not None:
            wave_height = wh_list[0]
    wv = calc_wave_score(wave_height)
    if wave_is_fallback and wave_height is not None:
        wv["label"] += "（沖合参考）"
    wave_pts = wv["pts"]
    details["wave"] = wv["label"]

    # 水温スコア（NOAA ERDDAP を優先）
    sst = sst_noaa
    tp = calc_temp_score(sst)
    temp_pts = tp["pts"]
    details["sst"] = tp["label"]

    # 降水ペナルティ
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
        area = spot_area(spot)

        lines.append(f"  {mark}: {spot_name(spot)}（{area}）  [{r['total']}点]")
        lines.append(f"         底質   : {d['seabed']}")
        if d.get("terrain"):
            lines.append(f"         地形   : {d['terrain']}")
        lines.append(f"         海水温 : {d['sst']}")
        lines.append(f"         波高   : {d['wave']}")
        lines.append(f"         風速   : {d['wind_speed']}")
        lines.append(f"         風向   : {d['wind_dir']}")
        lines.append(f"         降水量 : {d['precip']}")

        if d.get("rain_warning"):
            lines.append(f"         !! {d['rain_warning']}")

        sf = d.get("surfer_friendly")
        if sf is False:
            lines.append("         >> オフショア: 釣り場が空きやすい")
        elif sf is True:
            lines.append("         >> オンショア: 向かい風に注意")

        lines.append("")

    lines.append("【エリア別ベスト】")
    areas = {}
    for r in ranked:
        area = spot_area(r["spot"])
        if area not in areas:
            areas[area] = r
    for area, r in areas.items():
        lines.append(f"  {area}: {spot_name(r['spot'])} ({r['total']}点)")

    lines.append("")
    lines.append("【スコアの見方】")
    lines.append("  100点満点（底質15点 + 風40点 + 波30点 + 水温15点）")
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
_key_file = Path(__file__).parent / "api_key.txt"
if _key_file.exists():
    ANTHROPIC_API_KEY = _key_file.read_text(encoding="utf-8").strip()
else:
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def claude_ai_comment(scored_spots):
    api_key = ANTHROPIC_API_KEY
    if not api_key:
        print("[情報] ANTHROPIC_API_KEY が未設定のためAIアドバイスをスキップします")
        return ""

    ranked = sorted(scored_spots, key=lambda x: x["total"], reverse=True)
    top5 = []
    for i, r in enumerate(ranked[:5]):
        d = r["details"]
        top5.append({
            "rank": i + 1,
            "name": spot_name(r["spot"]),
            "area": spot_area(r["spot"]),
            "score": r["total"],
            "seabed": d["seabed"],
            "terrain": d.get("terrain", ""),
            "sst": d["sst"],
            "wave": d["wave"],
            "wind_speed": d["wind_speed"],
            "wind_dir": d["wind_dir"],
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
- 波高0.5m以上は釣りにくい。1m以上は危険
- 大雨の後は海が濁り釣果が落ちやすい

## 出力形式
1. **1位のおすすめポイント**: 具体的なアドバイス（2〜3文）
2. **2位・3位**: 簡単なコメント（各1〜2文）
3. **総合コメント**: 今日の全体的な釣況（1〜2文）

親しみやすい言葉で、釣り師が聞いて役立つ情報を簡潔に伝えてください。"""

    body = json.dumps({
        "model": "claude-opus-4-6",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["content"][0]["text"]
    except Exception as e:
        print(f"[警告] Claude API エラー: {e}")
        return ""


# ============================================================
# メイン処理（Pythonista版）
# ============================================================

def main():
    # 0〜2時台は当日、3時以降は翌日の予報を取得
    now = datetime.now(JST)
    days_ahead = 1 if now.hour >= 3 else 0
    target_date = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # Pythonista のコンソールをクリア
    if _console_module:
        _console_module.clear()

    # spots/ フォルダから釣り場データを読み込む
    spots = load_spots("spots")
    if not spots:
        print("[エラー] spotsフォルダにデータがありません。")
        print("build_spots_complete.py を先に実行してください。")
        return

    # エリア絞り込み
    area_centers = _load_area_centers()
    selected_areas = _select_areas(list(area_centers.keys())) if area_centers else None
    if selected_areas:
        spots = [s for s in spots if _assign_area(s, area_centers) in selected_areas]
        if not spots:
            print(f"[エラー] 選択エリアのスポットが見つかりません: {', '.join(selected_areas)}")
            return

    area_label = f"【{'・'.join(selected_areas)}】" if selected_areas else ""
    print("シロギス釣り場アドバイザー")
    print(f"対象日: {target_date} {area_label}")
    print(f"釣り場数: {len(spots)}か所")
    print("気象・海洋データを取得しています...\n")

    scored_spots = []
    for spot in spots:
        lat = spot_lat(spot)
        lon = spot_lon(spot)
        name = spot_name(spot)
        print(f"  {name}...", end="", flush=True)
        weather = fetch_weather(lat, lon, target_date)
        proxy_lat, proxy_lon = get_marine_proxy(lat, lon)
        marine = fetch_marine_with_fallback(proxy_lat, proxy_lon, target_date)
        sst = fetch_sst_noaa(lat, lon, target_date)
        result = score_spot(spot, weather, marine, sst_noaa=sst)
        scored_spots.append(result)
        d = result["details"]
        missing = []
        if d.get("wind_speed") == "データなし":
            missing.append("×風")
        if d.get("wave") == "データなし":
            missing.append("×波")
        if d.get("sst") == "データなし":
            missing.append("×水温")
        suffix = f"（{'、'.join(missing)}）" if missing else ""
        print(f" {result['total']}点{suffix}")

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

    # ファイルに保存（python フォルダと同階層の results フォルダ）
    results_dir = Path(__file__).parent.parent / "results"
    results_dir.mkdir(exist_ok=True)
    now_str = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    output_file = results_dir / f"fishing_report_{now_str}.txt"
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"レポートを保存しました: {output_file}")
    except Exception as e:
        print(f"[情報] ファイル保存をスキップ: {e}")


if __name__ == "__main__":
    main()
