"""
気象・海洋データ取得モジュール。
CLI（fishing_advisor_pythonista.py）と FastAPI ウェブアプリで共用。
"""

import json
import math
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

from .spots import get_marine_proxy_dict, get_marine_fallbacks

# ============================================================
# キャッシュ（インメモリ・プロセス内）
# ============================================================
import time as _time

_WEATHER_TTL    = 4 * 3600   # 天気データ: 4時間（レート制限対策で延長）
_SST_TTL        = 24 * 3600  # 水温データ: 24時間（日次更新）
_MARINE_TTL     = 4 * 3600   # 波浪データ: 4時間（レート制限対策で延長）

_WEATHER_CACHE: dict = {}      # (grid_lat, grid_lon, start_date, end_date) → (ts, result)
_SST_CACHE: dict = {}          # (grid_lat, grid_lon, date_str) → (ts, result)
_WEATHERAPI_CACHE: dict = {}   # (grid_lat, grid_lon, date_str) → result（失敗はキャッシュしない）
_MARINE_CACHE: dict = {}       # (grid_lat, grid_lon, start_date, end_date) → (ts, result)
_MARINE_COORD_CACHE: dict = {} # (lat2, lon2) → (lat, lon, is_fallback)

# Open-Meteo レート制限対策
_API_SEMAPHORE = threading.Semaphore(2)   # 同時API呼び出し上限
_WEATHER_RATE_LIMIT_UNTIL: float = 0     # 429受信後クールダウン終了時刻
_MARINE_RATE_LIMIT_UNTIL: float  = 0


def _get_weatherapi_key() -> str:
    return os.environ.get("WEATHERAPI_KEY", "")


# ============================================================
# 気象データ取得
# ============================================================

def fetch_weather(lat: float, lon: float, date_str: str) -> dict:
    """Open-Meteo Weather API から気象データを取得（1日分）。"""
    return fetch_weather_range(lat, lon, date_str, date_str)


def fetch_weather_range(lat: float, lon: float,
                        start_date: str, end_date: str) -> dict:
    """Open-Meteo Weather API から指定範囲の気象データを取得（最大7日）。"""
    global _WEATHER_RATE_LIMIT_UNTIL
    grid_lat = round(round(lat * 10) / 10, 1)
    grid_lon = round(round(lon * 10) / 10, 1)
    cache_key = (grid_lat, grid_lon, start_date, end_date)
    if cache_key in _WEATHER_CACHE:
        ts, cached = _WEATHER_CACHE[cache_key]
        if _time.time() - ts < _WEATHER_TTL:
            return cached

    # 429クールダウン中はスタールキャッシュを返す
    if _time.time() < _WEATHER_RATE_LIMIT_UNTIL:
        if cache_key in _WEATHER_CACHE:
            return _WEATHER_CACHE[cache_key][1]
        return {}

    base_url = "https://api.open-meteo.com/v1/forecast"
    params = [
        ("latitude", lat),
        ("longitude", lon),
        # 日次データ
        ("daily", "wind_speed_10m_max"),
        ("daily", "wind_direction_10m_dominant"),
        ("daily", "precipitation_sum"),
        ("daily", "weather_code"),
        ("daily", "temperature_2m_max"),
        # 時間別データ（4区分スコア用）
        ("hourly", "wind_speed_10m"),
        ("hourly", "wind_direction_10m"),
        ("hourly", "precipitation"),
        ("hourly", "temperature_2m"),
        ("hourly", "weather_code"),
        ("hourly", "apparent_temperature"),
        ("wind_speed_unit", "ms"),
        ("timezone", "Asia/Tokyo"),
        ("start_date", start_date),
        ("end_date", end_date),
    ]
    with _API_SEMAPHORE:
        # セマフォ取得後に再確認（待機中に他スレッドがキャッシュした可能性）
        if cache_key in _WEATHER_CACHE:
            ts, cached = _WEATHER_CACHE[cache_key]
            if _time.time() - ts < _WEATHER_TTL:
                return cached
        if _time.time() < _WEATHER_RATE_LIMIT_UNTIL:
            if cache_key in _WEATHER_CACHE:
                return _WEATHER_CACHE[cache_key][1]
            return {}
        try:
            full_url = base_url + "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(full_url, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            _WEATHER_CACHE[cache_key] = (_time.time(), result)
            return result
        except urllib.error.HTTPError as e:
            if e.code == 429:
                _WEATHER_RATE_LIMIT_UNTIL = _time.time() + 120  # 2分間クールダウン
                print(f"  [警告] 気象データ取得失敗 ({lat},{lon}): {e} — 2分クールダウン開始")
                if cache_key in _WEATHER_CACHE:
                    return _WEATHER_CACHE[cache_key][1]  # スタールキャッシュで代替
            else:
                print(f"  [警告] 気象データ取得失敗 ({lat},{lon}): {e}")
            return {}
        except Exception as e:
            print(f"  [警告] 気象データ取得失敗 ({lat},{lon}): {e}")
            return {}


def fetch_marine(lat: float, lon: float, date_str: str) -> dict:
    """Open-Meteo Marine API から波高データを取得（1日分）。"""
    return fetch_marine_range(lat, lon, date_str, date_str)


def fetch_marine_range(lat: float, lon: float,
                       start_date: str, end_date: str) -> dict:
    """Open-Meteo Marine API から指定範囲の波高データを取得（最大7日）。"""
    global _MARINE_RATE_LIMIT_UNTIL
    grid_lat = round(round(lat * 10) / 10, 1)
    grid_lon = round(round(lon * 10) / 10, 1)
    cache_key = (grid_lat, grid_lon, start_date, end_date)
    if cache_key in _MARINE_CACHE:
        ts, cached = _MARINE_CACHE[cache_key]
        if _time.time() - ts < _MARINE_TTL:
            return cached

    # 429クールダウン中はスタールキャッシュを返す
    if _time.time() < _MARINE_RATE_LIMIT_UNTIL:
        if cache_key in _MARINE_CACHE:
            return _MARINE_CACHE[cache_key][1]
        return {}

    base_url = "https://marine-api.open-meteo.com/v1/marine"
    params = [
        ("latitude", lat),
        ("longitude", lon),
        ("daily", "wave_height_max"),
        ("daily", "dominant_wave_direction"),
        ("daily", "wave_period_max"),
        ("timezone", "Asia/Tokyo"),
        ("start_date", start_date),
        ("end_date", end_date),
    ]
    with _API_SEMAPHORE:
        # セマフォ取得後に再確認
        if cache_key in _MARINE_CACHE:
            ts, cached = _MARINE_CACHE[cache_key]
            if _time.time() - ts < _MARINE_TTL:
                return cached
        if _time.time() < _MARINE_RATE_LIMIT_UNTIL:
            if cache_key in _MARINE_CACHE:
                return _MARINE_CACHE[cache_key][1]
            return {}
        try:
            full_url = base_url + "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(full_url, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            _MARINE_CACHE[cache_key] = (_time.time(), result)
            return result
        except urllib.error.HTTPError as e:
            if e.code == 400:
                _MARINE_CACHE[cache_key] = (_time.time(), {})  # 沿岸は対象外 → キャッシュして再試行を避ける
                return {}
            if e.code == 429:
                _MARINE_RATE_LIMIT_UNTIL = _time.time() + 120  # 2分間クールダウン
                print(f"  [警告] 波浪データ取得失敗 ({lat},{lon}): {e} — 2分クールダウン開始")
                if cache_key in _MARINE_CACHE:
                    return _MARINE_CACHE[cache_key][1]  # スタールキャッシュで代替
            else:
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
    Hs = 0.0248 * wind_speed_ms * math.sqrt(fetch_km)
    return round(min(Hs, 4.0), 2)


def fetch_marine_with_fallback(lat: float, lon: float, date_str: str) -> dict:
    """プライマリ → フォールバック座標の順で波高データを取得。
    フォールバック使用時は '_is_fallback': True を付加。"""
    coord_key = (round(lat, 2), round(lon, 2))

    # 429クールダウン中: 既知の成功座標のスタールキャッシュを返すか空dictで即返却
    if _time.time() < _MARINE_RATE_LIMIT_UNTIL:
        if coord_key in _MARINE_COORD_CACHE:
            c = _MARINE_COORD_CACHE[coord_key]
            stale = fetch_marine(c[0], c[1], date_str)  # キャッシュから返る（API呼び出しなし）
            if stale:
                stale["_is_fallback"] = c[2]
                return stale
        return {}

    if coord_key in _MARINE_COORD_CACHE:
        c = _MARINE_COORD_CACHE[coord_key]
        result = fetch_marine(c[0], c[1], date_str)
        if result:
            result["_is_fallback"] = c[2]
            return result

    result = fetch_marine(lat, lon, date_str)
    if result:
        _MARINE_COORD_CACHE[coord_key] = (lat, lon, False)
        return result

    fallbacks = sorted(
        get_marine_fallbacks(),
        key=lambda p: (p[0] - lat) ** 2 + (p[1] - lon) ** 2,
    )
    for fb_lat, fb_lon in fallbacks:
        # フォールバック試行中に 429 クールダウンに入った場合は打ち切り
        if _time.time() < _MARINE_RATE_LIMIT_UNTIL:
            break
        result = fetch_marine(fb_lat, fb_lon, date_str)
        if result:
            result["_is_fallback"] = True
            _MARINE_COORD_CACHE[coord_key] = (fb_lat, fb_lon, True)
            return result

    return {}


def fetch_sst_noaa(lat: float, lon: float, date_str: str) -> float | None:
    """海面水温を取得（NOAA ERDDAP → Open-Meteo Marine の順で試行）。"""
    grid_lat = round(round(lat * 10) / 10, 1)
    grid_lon = round(round(lon * 10) / 10, 1)
    cache_key = (grid_lat, grid_lon, date_str)
    if cache_key in _SST_CACHE:
        ts, cached = _SST_CACHE[cache_key]
        if _time.time() - ts < _SST_TTL:
            return cached

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
            _SST_CACHE[cache_key] = (_time.time(), sst)
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
            _SST_CACHE[cache_key] = (_time.time(), sst)
            return sst
    except Exception:
        pass

    return None


def get_weather_fetched_at(lat: float, lon: float, start_date: str, end_date: str) -> float | None:
    """指定座標・期間の気象キャッシュ取得時刻（unixtime）を返す。キャッシュ未存在時は None。"""
    grid_lat = round(round(lat * 10) / 10, 1)
    grid_lon = round(round(lon * 10) / 10, 1)
    entry = _WEATHER_CACHE.get((grid_lat, grid_lon, start_date, end_date))
    return entry[0] if entry else None
