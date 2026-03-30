"""
X（Twitter）への海況自動投稿モジュール。
毎朝4:00から6エリア分を5分間隔で投稿する。
"""

import json
import os
import re
import unicodedata
import urllib.request
from pathlib import Path

from .spots import _get_marine_cache
from .weather import fetch_weather, fetch_marine_with_fallback, fetch_sst_noaa
from .scoring import direction_label, weather_code_label, WEATHER_EMOJI
from .lunar import tide_label

# ============================================================
# エリア定義（投稿順）
# ============================================================

AREA_SCHEDULE = [
    ("sagamibay", "相模湾",   "https://tsuricast.jp/spots?area=sagamibay"),
    ("tokyobay",  "東京湾",   "https://tsuricast.jp/spots?area=tokyobay"),
    ("uchibo",    "内房",     "https://tsuricast.jp/spots?area=uchibo"),
    ("sotobo",    "外房",     "https://tsuricast.jp/spots?area=sotobo"),
    ("kujukuri",  "九十九里", "https://tsuricast.jp/spots?area=kujukuri"),
    ("miura",     "三浦半島", "https://tsuricast.jp/spots?area=miura"),
]

# エリア slug → 日本語名 マッピング（_marine_areas.json のキーと対応）
_SLUG_TO_JP = {
    "sagamibay": "相模湾",
    "tokyobay":  "東京湾",
    "uchibo":    "内房",
    "sotobo":    "外房",
    "kujukuri":  "九十九里",
    "miura":     "三浦半島",
}

_URL_PATTERN = re.compile(r'https?://\S+')


# ============================================================
# 文字数カウント（X の重み付きルール）
# ============================================================

def count_weighted(text: str) -> int:
    """X の重み付き文字数を返す。URL は t.co 換算で 23 文字固定。"""
    # URL を 23 文字のプレースホルダーに置換してカウント
    placeholder = "\x00" * 23  # ASCII null × 23 = 23 文字
    replaced = _URL_PATTERN.sub(placeholder, text)

    total = 0
    for ch in replaced:
        cat = unicodedata.category(ch)
        name = unicodedata.name(ch, "")
        # CJK・ひらがな・カタカナ・全角記号 → 2
        if (
            "\u3000" <= ch <= "\u9FFF"   # CJK / ひら / カタ / 全角記号
            or "\uF900" <= ch <= "\uFAFF"  # CJK互換
            or "\uFF00" <= ch <= "\uFFEF"  # 全角ASCII・半角カナ
            or cat.startswith("S")         # Symbol (絵文字など)
        ):
            total += 2
        else:
            total += 1
    return total


# ============================================================
# エリア気象データ取得
# ============================================================

def get_area_weather(area_name_jp: str, date_str: str, include_tide: bool = True) -> dict:
    """_marine_areas.json を使ってエリア代表地点の気象データを取得する。"""
    proxy_dict, _, area_centers = _get_marine_cache()

    if area_name_jp not in area_centers:
        return {}

    center_lat, center_lon, fetch_km = area_centers[area_name_jp]
    proxy_lat, proxy_lon = proxy_dict.get(area_name_jp, (center_lat, center_lon))

    weather = fetch_weather(center_lat, center_lon, date_str)
    marine = fetch_marine_with_fallback(proxy_lat, proxy_lon, date_str)
    sst = fetch_sst_noaa(center_lat, center_lon, date_str)

    daily = weather.get("daily", {})

    # 風速・風向
    wind_speed = None
    wind_dir_deg = None
    spd_list = daily.get("wind_speed_10m_max", [])
    dir_list = daily.get("wind_direction_10m_dominant", [])
    if spd_list and spd_list[0] is not None:
        wind_speed = round(spd_list[0], 1)
    if dir_list and dir_list[0] is not None:
        wind_dir_deg = dir_list[0]
    wind_dir_label = direction_label(wind_dir_deg) if wind_dir_deg is not None else "--"

    # 天気コード
    weather_code = None
    wc_list = daily.get("weather_code", [])
    if wc_list and wc_list[0] is not None:
        weather_code = int(wc_list[0])
    weather_label = weather_code_label(weather_code) if weather_code is not None else "--"
    weather_emoji = WEATHER_EMOJI.get(weather_code, "🌡") if weather_code is not None else ""

    # 波高（marine API → 風推定フォールバック）
    wave_height = None
    if marine and "daily" in marine:
        wh_list = marine["daily"].get("wave_height_max", [])
        if wh_list and wh_list[0] is not None:
            wave_height = round(wh_list[0], 1)
    if wave_height is None and marine.get("wave_height_max") is not None:
        wave_height = round(marine["wave_height_max"], 1)
    if wave_height is None and wind_speed is not None:
        from .weather import estimate_wave_from_wind
        wave_height = round(estimate_wave_from_wind(wind_speed, fetch_km), 1)

    # 潮回り
    import datetime as _dt
    tide = tide_label(_dt.date.fromisoformat(date_str)) if include_tide else "--"

    return {
        "wind_speed":    wind_speed,
        "wind_dir_deg":  wind_dir_deg,
        "wind_dir_label": wind_dir_label,
        "wave_height":   wave_height,
        "weather_code":  weather_code,
        "weather_label": weather_label,
        "weather_emoji": weather_emoji,
        "sst":           round(sst, 1) if sst is not None else None,
        "tide":          tide,
    }


# ============================================================
# Claude API による一言コメント生成
# ============================================================

def _get_prompt_file(mode: str) -> Path:
    """モードに対応するプロンプトファイルを返す。モード別ファイルを優先し、なければ共通ファイルにフォールバック。"""
    base = Path(__file__).parent.parent
    candidates = [
        base / f"x_post_prompt_{mode}.md",
        base / "x_post_prompt.md",
    ]
    for p in candidates:
        if p.exists() and p.stat().st_size > 0:
            return p
    return candidates[0]


def generate_area_comment(area_name_jp: str, area_data: dict, mode: str = "morning") -> str:
    """Claude API でエリア海況の一言コメントを生成する（最大40文字）。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""

    prompt_file = _get_prompt_file(mode)
    if not prompt_file.exists():
        print(f"  [警告] プロンプトファイルが見つかりません: {prompt_file}")
        return ""
    content = prompt_file.read_text(encoding="utf-8")
    # ## SYSTEM / ## USER の2セクションに分割
    if "## USER" in content:
        sys_part, user_part = content.split("## USER", 1)
        system_prompt = sys_part.replace("## SYSTEM", "").strip()
        user_template = user_part.strip()
    else:
        system_prompt = ""
        user_template = content.strip()

    def _v(key, unit="", default="--"):
        val = area_data.get(key)
        return f"{val}{unit}" if val is not None else default

    時間帯 = "今朝" if mode == "morning" else "明日"
    user_prompt = user_template.format(
        エリア名=area_name_jp,
        天気=area_data.get("weather_label", "--"),
        波高=_v("wave_height"),
        風速=_v("wind_speed"),
        風向=area_data.get("wind_dir_label", "--"),
        水温=_v("sst"),
        潮回り=area_data.get("tide", "--"),
        投稿時間帯=f"{時間帯}の{'実況' if mode == 'morning' else '情報'}",
    )

    try:
        payload = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if system_prompt:
            payload["system"] = system_prompt
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        text = result["content"][0]["text"].strip()
        # 改行を取り除き 40 文字に切り捨て
        text = text.replace("\n", " ")
        return text[:40]
    except Exception as e:
        print(f"  [警告] AIコメント生成失敗 ({area_name_jp}): {e}")
        return ""


# ============================================================
# ツイート本文フォーマット
# ============================================================

def format_tweet(area_name_jp: str, area_data: dict, comment: str, url: str,
                 mode: str = "morning") -> str:
    """投稿用ツイートテキストを組み立てる。280文字超過時はコメントを短縮。"""

    def _v(key, unit="", default="--"):
        val = area_data.get(key)
        return f"{val}{unit}" if val is not None else default

    weather_str = (
        f"{area_data['weather_emoji']} {area_data['weather_label']}"
        if area_data.get("weather_emoji")
        else area_data.get("weather_label", "--")
    )

    if mode == "morning":
        title = f"🌊 今朝の{area_name_jp}海況"
        hashtags = f"#釣り #{area_name_jp} #海釣り"
    else:
        title = f"🌅 明日の{area_name_jp}海況情報"
        hashtags = f"#釣り #{area_name_jp} #海釣り #釣り情報"

    def _build(cmt: str) -> str:
        parts = [
            title,
            "",
            f"波　: {_v('wave_height')}m",
            f"風　: {_v('wind_speed')}m/s {_v('wind_dir_label')}",
            f"天気: {weather_str}",
            f"水温: {_v('sst')}°C",
        ]
        if cmt:
            parts += ["", cmt]
        parts += [
            "",
            f"{area_name_jp}の釣り場詳細👇",
            url,
            "",
            hashtags,
        ]
        return "\n".join(parts)

    tweet = _build(comment)

    # 280文字を超えた場合、コメントを段階的に短縮
    if count_weighted(tweet) > 280 and comment:
        for length in (30, 20, 10, 0):
            shorter = comment[:length] if length > 0 else ""
            tweet = _build(shorter)
            if count_weighted(tweet) <= 280:
                break

    return tweet


# ============================================================
# X API 投稿
# ============================================================

def _get_twitter_client():
    """tweepy.Client を環境変数から生成して返す。X_POST_ENV=test でテストアカウントを使用。"""
    import tweepy  # noqa: PLC0415
    suffix = "_TEST" if os.environ.get("X_POST_ENV") == "test" else ""
    return tweepy.Client(
        consumer_key=os.environ[f"X_API_KEY{suffix}"],
        consumer_secret=os.environ[f"X_API_SECRET{suffix}"],
        access_token=os.environ[f"X_ACCESS_TOKEN{suffix}"],
        access_token_secret=os.environ[f"X_ACCESS_TOKEN_SECRET{suffix}"],
    )


def post_tweet(text: str) -> None:
    """X API v2 でツイートを投稿する。"""
    client = _get_twitter_client()
    client.create_tweet(text=text)


# ============================================================
# エリア1件分の投稿処理
# ============================================================

def post_area(area_slug: str, area_name_jp: str, url: str, date_str: str,
              mode: str = "morning") -> bool:
    """1エリア分のデータ取得・コメント生成・投稿を行う。失敗時はFalseを返す。"""
    try:
        area_data = get_area_weather(area_name_jp, date_str)
        comment = generate_area_comment(area_name_jp, area_data, mode=mode)
        tweet = format_tweet(area_name_jp, area_data, comment, url, mode=mode)
        x_env = os.environ.get("X_POST_ENV", "production")
        print(f"--- {area_name_jp} ({count_weighted(tweet)}文字) [{x_env}] ---")
        print(tweet)
        print()
        post_tweet(tweet)
        return True
    except Exception as e:
        print(f"[ERROR] {area_name_jp}: {e}")
        return False
