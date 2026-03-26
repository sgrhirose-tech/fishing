"""
気象・海洋データ取得モジュール。
CLI（fishing_advisor_pythonista.py）と FastAPI ウェブアプリで共用。
"""

import json
import math
import os
import urllib.parse
import urllib.request

from .spots import get_marine_proxy_dict, get_marine_fallbacks

# ============================================================
# キャッシュ（インメモリ・プロセス内）
# ============================================================
_WEATHER_CACHE: dict = {}      # (grid_lat, grid_lon, date_str) → result
_SST_CACHE: dict = {}          # (grid_lat, grid_lon, date_str) → result
_WEATHERAPI_CACHE: dict = {}   # (grid_lat, grid_lon, date_str) → result
_MARINE_COORD_CACHE: dict = {} # (lat2, lon2) → (lat, lon, is_fallback)


def _get_weatherapi_key() -> str:
    return os.environ.get("WEATHERAPI_KEY", "")


# ============================================================
# 気象データ取得
# ============================================================

def fetch_weather(lat: float, lon: float, date_str: str) -> dict:
    """Open-Meteo Weather API から気象データを取得。"""
    grid_lat = round(round(lat * 10) / 10, 1)
    grid_lon = round(round(lon * 10) / 10, 1)
    cache_key = (grid_lat, grid_lon, date_str)
    if cache_key in _WEATHER_CACHE:
        return _WEATHER_CACHE[cache_key]

    base_url = "https://api.open-meteo.com/v1/forecast"
    params = [
        ("latitude", lat),
        ("longitude", lon),
        ("daily", "wind_speed_10m_max"),
        ("daily", "wind_direction_10m_dominant"),
        ("daily", "precipitation_sum"),
        ("daily", "weather_code"),
        ("hourly", "temperature_2m"),
        ("daily", "temperature_2m_max"),
        ("wind_speed_unit", "ms"),
        ("timezone", "Asia/Tokyo"),
        ("start_date", date_str),
        ("end_date", date_str),
    ]
    try:
        full_url = base_url + "?" + urllib.parse.urlencode(params)
        with urllib.request.urlopen(full_url, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        _WEATHER_CACHE[cache_key] = result
        return result
    except Exception as e:
        print(f"  [警告] 気象データ取得失敗 ({lat},{lon}): {e}")
        return {}


def fetch_marine(lat: float, lon: float, date_str: str) -> dict:
    """Open-Meteo Marine API から波高データを取得（沖合代理座標で呼ぶこと）。"""
    base_url = "https://marine-api.open-meteo.com/v1/marine"
    params = [
        ("latitude", lat),
        ("longitude", lon),
        ("daily", "wave_height_max"),
        ("daily", "dominant_wave_direction"),
        ("daily", "wave_period_max"),
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
            return {}   # 湾内・沿岸は対象外のため正常
        print(f"  [警告] 波浪データ取得失敗 ({lat},{lon}): {e}")
        return {}
    except Exception as e:
        print(f"  [警告] 波浪データ取得失敗 ({lat},{lon}): {e}")
        return {}


def fetch_marine_weatherapi(lat: float, lon: float, date_str: str) -> dict:
    """WeatherAPI.com Marine API から波高を取得。WEATHERAPI_KEY 未設定時は即 {} を返す。"""
    api_key = _get_weatherapi_key()
    if not api_key:
        return {}

    grid_lat = round(round(lat * 4) / 4, 2)
    grid_lon = round(round(lon * 4) / 4, 2)
    cache_key = (grid_lat, grid_lon, date_str)
    if cache_key in _WEATHERAPI_CACHE:
        return _WEATHERAPI_CACHE[cache_key]

    url = "https://api.weatherapi.com/v1/marine.json"
    params = urllib.parse.urlencode([
        ("key", api_key),
        ("q", f"{lat},{lon}"),
        ("dt", date_str),
        ("tides", "no"),
    ])
    result = {}
    try:
        with urllib.request.urlopen(url + "?" + params, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for day in data.get("forecast", {}).get("forecastday", []):
            if day.get("date") == date_str:
                hours = day.get("hour", [])
                day_hours = hours[6:16]
                heights = [h["sig_ht_mt"] for h in day_hours if h.get("sig_ht_mt") is not None]
                periods = [h["swell_period_secs"] for h in day_hours if h.get("swell_period_secs") is not None]
                if heights:
                    result = {"wave_height_max": max(heights)}
                    if periods:
                        result["swell_period_max"] = max(periods)
                    break
    except Exception as e:
        print(f"  [情報] WeatherAPI 波浪取得失敗: {e}")

    if result:
        _WEATHERAPI_CACHE[cache_key] = result
    return result


def estimate_wave_from_wind(wind_speed_ms: float, fetch_km: float) -> float:
    """SMB 簡易式で風速と吹送距離から有義波高（m）を推定。"""
    if not wind_speed_ms or wind_speed_ms <= 0:
        return 0.0
    Hs = 0.0248 * wind_speed_ms * math.sqrt(fetch_km * 1000 / 9.8)
    return round(min(Hs, 5.0), 2)


def fetch_marine_with_fallback(lat: float, lon: float, date_str: str) -> dict:
    """プライマリ → フォールバック座標の順で波高データを取得。
    フォールバック使用時は '_is_fallback': True を付加。"""
    cache_key = (round(lat, 2), round(lon, 2))

    if cache_key in _MARINE_COORD_CACHE:
        c = _MARINE_COORD_CACHE[cache_key]
        result = fetch_marine(c[0], c[1], date_str)
        if result:
            result["_is_fallback"] = c[2]
            return result

    result = fetch_marine(lat, lon, date_str)
    if result:
        _MARINE_COORD_CACHE[cache_key] = (lat, lon, False)
        return result

    fallbacks = sorted(
        get_marine_fallbacks(),
        key=lambda p: (p[0] - lat) ** 2 + (p[1] - lon) ** 2,
    )
    for fb_lat, fb_lon in fallbacks:
        result = fetch_marine(fb_lat, fb_lon, date_str)
        if result:
            result["_is_fallback"] = True
            _MARINE_COORD_CACHE[cache_key] = (fb_lat, fb_lon, True)
            return result

    return {}


def fetch_sst_noaa(lat: float, lon: float, date_str: str) -> float | None:
    """海面水温を取得（NOAA ERDDAP → Open-Meteo Marine の順で試行）。"""
    grid_lat = round(round(lat * 10) / 10, 1)
    grid_lon = round(round(lon * 10) / 10, 1)
    cache_key = (grid_lat, grid_lon, date_str)
    if cache_key in _SST_CACHE:
        return _SST_CACHE[cache_key]

    lat_str = f"{lat:.4f}"
    lon_str = f"{lon:.4f}"

    # 1. NOAA ERDDAP
    url = (
        "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.json"
        f"?analysed_sst%5B(last)%5D%5B({lat_str})%5D%5B({lon_str})%5D"
    )
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rows = data.get("table", {}).get("rows", [])
        if rows and rows[0] and rows[0][3] is not None:
            sst = float(rows[0][3])
            _SST_CACHE[cache_key] = sst
            return sst
    except Exception as e:
        print(f"  [情報] NOAA水温取得失敗 ({lat},{lon}): {e}")

    # 2. フォールバック: Open-Meteo Marine
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
        valid = [v for v in sst_list[6:16] if v is not None]
        if valid:
            sst = max(valid)
            _SST_CACHE[cache_key] = sst
            return sst
    except Exception:
        pass

    return None
