"""
X（Twitter）への海況自動投稿モジュール。
毎朝4:00から4投稿（関東・静岡・愛知三重・近畿）を5分間隔で投稿する。
"""

import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
from pathlib import Path

from .spots import _get_marine_cache
from .weather import fetch_weather, fetch_marine_with_fallback, fetch_sst_noaa
from .scoring import direction_label, weather_code_label, WEATHER_EMOJI
from .lunar import tide_label

# ============================================================
# エリアグループ定義（投稿順）
# ============================================================

AREA_SCHEDULE = [
    {
        "post_label": "関東",
        "areas": ["内房", "外房", "東京湾", "相模湾"],
    },
    {
        "post_label": "静岡",
        "areas": ["遠州灘", "駿河湾", "伊豆"],
    },
    {
        "post_label": "愛知・三重",
        "areas": ["伊勢湾", "三河湾", "志摩南伊勢", "熊野灘"],
    },
    {
        "post_label": "近畿",
        "areas": ["大阪湾", "紀伊水道"],
    },
]

_URL_PATTERN = re.compile(r'https?://\S+')


# ============================================================
# 文字数カウント（X の重み付きルール）
# ============================================================

def count_weighted(text: str) -> int:
    """X の重み付き文字数を返す。URL は t.co 換算で 23 文字固定。"""
    placeholder = "\x00" * 23
    replaced = _URL_PATTERN.sub(placeholder, text)
    total = 0
    for ch in replaced:
        cat = unicodedata.category(ch)
        if (
            "\u3000" <= ch <= "\u9FFF"
            or "\uF900" <= ch <= "\uFAFF"
            or "\uFF00" <= ch <= "\uFFEF"
            or cat.startswith("S")
        ):
            total += 2
        else:
            total += 1
    return total


# ============================================================
# エリア気象データ取得
# ============================================================

def get_area_weather(area_name_jp: str, date_str: str) -> dict:
    """_marine_areas.json を使ってエリア代表地点の気象データを取得する。"""
    proxy_dict, _, area_centers = _get_marine_cache()

    if area_name_jp not in area_centers:
        return {}

    center_lat, center_lon, fetch_km = area_centers[area_name_jp]
    proxy_lat, proxy_lon = proxy_dict.get(area_name_jp, (center_lat, center_lon))

    weather = fetch_weather(center_lat, center_lon, date_str)
    marine = fetch_marine_with_fallback(proxy_lat, proxy_lon, date_str)

    daily = weather.get("daily", {})
    hourly = weather.get("hourly", {})

    # 最高気温
    temp_max = None
    temp_list = daily.get("temperature_2m_max", [])
    if temp_list and temp_list[0] is not None:
        temp_max = round(temp_list[0])

    # 風速・風向（朝8時の値）
    wind_speed = None
    wind_dir_deg = None
    _spd = hourly.get("wind_speed_10m", [])
    _dir = hourly.get("wind_direction_10m", [])
    if len(_spd) > 8 and _spd[8] is not None:
        wind_speed = round(_spd[8])
    if len(_dir) > 8 and _dir[8] is not None:
        wind_dir_deg = _dir[8]
    wind_dir_label = direction_label(wind_dir_deg) if wind_dir_deg is not None else "--"

    # 天気コード
    weather_code = None
    wc_list = daily.get("weather_code", [])
    if wc_list and wc_list[0] is not None:
        weather_code = int(wc_list[0])
    weather_emoji = WEATHER_EMOJI.get(weather_code, "🌡") if weather_code is not None else ""

    # 波高
    wave_height = None
    if marine and "daily" in marine:
        wh_list = marine["daily"].get("wave_height_max", [])
        if wh_list and wh_list[0] is not None:
            wave_height = round(wh_list[0], 1)
    if wave_height is None and marine.get("wave_height_max") is not None:
        wave_height = round(marine["wave_height_max"], 1)

    return {
        "temp_max":       temp_max,
        "wind_speed":     wind_speed,
        "wind_dir_label": wind_dir_label,
        "wave_height":    wave_height,
        "weather_emoji":  weather_emoji,
    }


# ============================================================
# ツイート本文フォーマット
# ============================================================

def _pad(name: str, max_len: int) -> str:
    """全角スペースで name を max_len 全角分に揃える。"""
    return name + "　" * (max_len - len(name))


def format_group_tweet(post_label: str, areas_data: list,
                       timestamp: str, mode: str = "morning") -> str:
    """複数エリアをまとめた投稿テキストを組み立てる。
    areas_data: [(area_name, area_data_dict), ...]
    """
    header_word = "今朝" if mode == "morning" else "明日"
    header = f"🌊 {header_word}の海況（{timestamp}）"

    max_len = max(len(name) for name, _ in areas_data)

    lines = []
    for area_name, data in areas_data:
        if not data:
            continue
        padded = _pad(area_name, max_len)
        emoji    = data.get("weather_emoji", "")
        temp     = data.get("temp_max")
        wave     = data.get("wave_height")
        wind_dir = data.get("wind_dir_label", "--")
        wind_spd = data.get("wind_speed")

        temp_str  = f"{temp}℃"   if temp     is not None else "--℃"
        wave_str  = f"波{wave}m"  if wave     is not None else "波--m"
        wind_str  = f"{wind_dir}{wind_spd}m/s" if wind_spd is not None else f"{wind_dir}--m/s"

        lines.append(f"{padded} {emoji} {temp_str} {wave_str} {wind_str}")

    parts = (
        [header, "", f"【{post_label}】"]
        + lines
        + ["", "詳細はこちら👇", "tsuricast.jp", "", "#釣り #海釣り"]
    )
    return "\n".join(parts)


# ============================================================
# X API 投稿
# ============================================================

def _get_twitter_client():
    import tweepy  # noqa: PLC0415
    suffix = "_TEST" if os.environ.get("X_POST_ENV") == "test" else ""
    return tweepy.Client(
        consumer_key=os.environ[f"X_API_KEY{suffix}"],
        consumer_secret=os.environ[f"X_API_SECRET{suffix}"],
        access_token=os.environ[f"X_ACCESS_TOKEN{suffix}"],
        access_token_secret=os.environ[f"X_ACCESS_TOKEN_SECRET{suffix}"],
    )


def post_tweet(text: str) -> None:
    client = _get_twitter_client()
    client.create_tweet(text=text)


# ============================================================
# グループ1件分の投稿処理
# ============================================================

def post_group(group: dict, date_str: str, mode: str = "morning",
               timestamp: str = "") -> bool:
    """1グループ分のデータ取得・フォーマット・投稿を行う。失敗時はFalseを返す。"""
    post_label = group["post_label"]
    area_names = group["areas"]
    try:
        areas_data = []
        for name in area_names:
            data = get_area_weather(name, date_str)
            areas_data.append((name, data))

        if not any(d.get("wave_height") is not None for _, d in areas_data):
            print(f"[SKIP] {post_label}: 全エリアで波高データ未取得のため投稿スキップ")
            return False

        tweet = format_group_tweet(post_label, areas_data, timestamp, mode=mode)
        x_env = os.environ.get("X_POST_ENV", "production")
        print(f"--- {post_label} ({count_weighted(tweet)}文字) [{x_env}] ---")
        print(tweet)
        print()
        post_tweet(tweet)
        return True
    except Exception as e:
        print(f"[ERROR] {post_label}: {e}")
        return False
