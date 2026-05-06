"""
潮汐データ読み込みモジュール。

データソース:
  1. data/jma_tides/{jma_code}_{YYYY}.json  — 気象庁推算潮位表
       満潮・干潮・時別潮位（整時24点）
  2. 天文計算
       日の出・日の入り（astral）、月齢・潮名（数式）
  3. data/tides/{harbor_code}_{YYYY-MM}.json — tide736.net（フォールバック）
       jma_harbor_code 未設定 or JMA hourly データが存在しない場合のみ使用
"""

import json
import pathlib
from datetime import date

_DATA_DIR  = pathlib.Path(__file__).parent.parent / "data"
_TIDES_DIR = _DATA_DIR / "tides"
_JMA_DIR   = _DATA_DIR / "jma_tides"

# ── 月齢・潮名 ────────────────────────────────────────────────

_SYNODIC_MONTH = 29.530588853
_JD_EPOCH      = 2451544.5       # JD at 2000-01-01 00:00 UT
_JD_NEW_MOON   = 2451550.09766   # JD of 2000-01-06 18:14 UT (known new moon)
_JST_OFFSET    = 9 / 24          # JST = UTC+9: midnight JST = 15:00 UTC prev day

def _moon_age(date_str: str) -> float:
    """月齢を返す（JST 深夜0時基準）。"""
    d = date.fromisoformat(date_str)
    days = (d - date(2000, 1, 1)).days
    jd = _JD_EPOCH + days - _JST_OFFSET
    return (jd - _JD_NEW_MOON) % _SYNODIC_MONTH

_TIDE_NAME_TABLE: list[tuple[float, float, str]] = [
    ( 0.0,  2.0,  "大潮"),
    ( 2.0,  6.0,  "中潮"),
    ( 6.0,  9.0,  "小潮"),
    ( 9.0, 10.0,  "長潮"),
    (10.0, 11.0,  "若潮"),
    (11.0, 12.5,  "中潮"),
    (12.5, 16.0,  "大潮"),
    (16.0, 20.0,  "中潮"),
    (20.0, 23.0,  "小潮"),
    (23.0, 24.0,  "長潮"),
    (24.0, 25.0,  "若潮"),
    (25.0, 27.0,  "中潮"),
    (27.0, 29.53, "大潮"),
]

def _tide_name(moon_age: float) -> str:
    age = moon_age % _SYNODIC_MONTH
    for lo, hi, name in _TIDE_NAME_TABLE:
        if lo <= age < hi:
            return name
    return "大潮"

# ── 日の出・日の入り ──────────────────────────────────────────

def _sun_times(lat: float, lon: float, date_str: str) -> tuple[str, str]:
    """日の出・日の入り時刻 (HH:MM JST) を返す。計算失敗時は空文字。"""
    try:
        from astral import LocationInfo
        from astral.sun import sun
        from zoneinfo import ZoneInfo
        loc = LocationInfo(latitude=lat, longitude=lon, timezone="Asia/Tokyo")
        s = sun(loc.observer, date=date.fromisoformat(date_str),
                tzinfo=ZoneInfo("Asia/Tokyo"))
        return s["sunrise"].strftime("%H:%M"), s["sunset"].strftime("%H:%M")
    except Exception:
        return "", ""

# ── JMA データ読み込み ────────────────────────────────────────

def _load_jma_day(jma_code: str, date_str: str) -> dict | None:
    year = date_str[:4]
    path = _JMA_DIR / f"{jma_code}_{year}.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("days", {}).get(date_str)

# ── 極値導出 ──────────────────────────────────────────────────

def _hm_to_min(s: str) -> int:
    h, m = map(int, s.split(":"))
    return h * 60 + m

def _derive_extrema(hourly: list[dict], find_max: bool) -> list[dict]:
    """hourly データから極大(find_max=True)または極小(find_max=False)を2次補間で導出。
    時刻間隔は自動検出。端点は除外。"""
    if len(hourly) < 3:
        return []
    interval_min = _hm_to_min(hourly[1]["time"]) - _hm_to_min(hourly[0]["time"])
    if interval_min <= 0:
        interval_min = 60
    cms   = [h["cm"] for h in hourly]
    times = [h["time"] for h in hourly]
    result = []
    for i in range(1, len(cms) - 1):
        y0, y1, y2 = cms[i - 1], cms[i], cms[i + 1]
        is_extreme = (y1 > y0 and y1 > y2) if find_max else (y1 < y0 and y1 < y2)
        if not is_extreme:
            continue
        a = (y0 - 2 * y1 + y2) / 2
        b = (y2 - y0) / 2
        dx = -b / (2 * a) if a != 0 else 0.0
        dx = max(-1.0, min(1.0, dx))
        offset_min = int(dx * interval_min)
        hh, mm = map(int, times[i].split(":"))
        total = max(0, min(23 * 60 + 59, hh * 60 + mm + offset_min))
        t = f"{total // 60:02d}:{total % 60:02d}"
        cm = round(y1 + b * dx + a * dx * dx, 1)
        result.append({"time": t, "cm": cm})
    return result

def _derive_flood(hourly: list[dict]) -> list[dict]:
    return _derive_extrema(hourly, find_max=True)

def _derive_ebb(hourly: list[dict]) -> list[dict]:
    return _derive_extrema(hourly, find_max=False)

# ── tide736.net フォールバック ────────────────────────────────

def _load_tide736_day(harbor_code: str, date_str: str) -> dict | None:
    month_str  = date_str[:7]
    cache_path = _TIDES_DIR / f"{harbor_code}_{month_str}.json"
    if not cache_path.exists():
        return None
    try:
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return cached.get("days", {}).get(date_str)

# ── メイン API ────────────────────────────────────────────────

def get_tide_data(slug: str, date_str: str) -> dict | None:
    """
    スポット slug と日付文字列（YYYY-MM-DD）から潮汐データを返す。

    Returns:
        潮汐データ dict:
          - date: str
          - harbor_name: str
          - tide_name: str          潮名（大潮/中潮/小潮/長潮/若潮）
          - sunrise: str            日の出時刻 (HH:MM)
          - sunset: str             日の入り時刻 (HH:MM)
          - moon_age: float         月齢
          - flood: list[dict]       満潮リスト
          - ebb: list[dict]         干潮リスト
          - hourly: list[dict]      時刻別潮位
          - data_source: str        "jma" | "tide736"

        harbor_code 未設定・データなしの場合は None。
    """
    from .spots import load_spot
    spot = load_spot(slug)
    if not spot:
        return None

    harbor_code = spot.get("harbor_code")
    harbor_name = spot.get("harbor_name", "")
    if not harbor_code:
        return None

    jma_code = spot.get("jma_harbor_code")
    loc      = spot.get("location") or {}
    lat      = loc.get("latitude")
    lon      = loc.get("longitude")

    # ── 気象庁パス ────────────────────────────────────────────
    if jma_code:
        jma_day = _load_jma_day(jma_code, date_str)
        if jma_day and jma_day.get("hourly"):
            hourly = jma_day["hourly"]
            flood  = jma_day.get("flood") or _derive_flood(hourly)
            ebb    = jma_day.get("ebb")   or _derive_ebb(hourly)
            ma             = round(_moon_age(date_str), 1)
            sunrise, sunset = _sun_times(lat, lon, date_str) if lat and lon else ("", "")
            return {
                "date":        date_str,
                "harbor_name": harbor_name,
                "tide_name":   _tide_name(ma),
                "sunrise":     sunrise,
                "sunset":      sunset,
                "moon_age":    ma,
                "flood":       flood,
                "ebb":         ebb,
                "hourly":      hourly,
                "data_source": "jma",
            }

    # ── tide736.net フォールバック ─────────────────────────────
    day_data = _load_tide736_day(harbor_code, date_str)
    if not day_data:
        return None

    result = {"date": date_str, "harbor_name": harbor_name, **day_data, "data_source": "tide736"}
    hourly = result.get("hourly", [])
    if not result.get("flood") and hourly:
        result["flood"] = _derive_flood(hourly)
    if not result.get("ebb") and hourly:
        result["ebb"] = _derive_ebb(hourly)
    return result
