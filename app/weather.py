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

# ============================================================
# ディスクキャッシュ（再起動・デプロイ後もデータを保持）
# ============================================================
_DISK_CACHE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "weather_cache.json"
)
# ディスク書き込みの過剰呼び出しを抑制するタイムスタンプ
_DISK_CACHE_LAST_SAVED: float = 0
_DISK_CACHE_SAVE_INTERVAL = 300  # 最短5分に1回だけ書き込む


def _key_to_str(key: tuple) -> str:
    return "|".join(str(x) for x in key)


def _str_to_key(s: str) -> tuple:
    parts = s.split("|")
    result = []
    for p in parts:
        try:
            result.append(float(p) if "." in p else int(p))
        except ValueError:
            result.append(p)
    return tuple(result)


def _load_disk_cache() -> None:
    """起動時にディスクキャッシュをメモリへ読み込む。
    TTLの2倍まで古いデータを受け入れ（429時のスタールキャッシュとして使用）。"""
    global _WEATHER_CACHE, _MARINE_CACHE
    try:
        if not os.path.exists(_DISK_CACHE_PATH):
            return
        with open(_DISK_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        now = _time.time()
        loaded_w = loaded_m = 0
        for k_str, (ts, v) in data.get("weather", {}).items():
            if now - ts < _WEATHER_TTL * 2:
                _WEATHER_CACHE[_str_to_key(k_str)] = (ts, v)
                loaded_w += 1
        for k_str, (ts, v) in data.get("marine", {}).items():
            if now - ts < _MARINE_TTL * 2:
                _MARINE_CACHE[_str_to_key(k_str)] = (ts, v)
                loaded_m += 1
        if loaded_w + loaded_m > 0:
            print(f"[起動] ディスクキャッシュ読み込み: 気象{loaded_w}件 / 波浪{loaded_m}件")
    except Exception as e:
        print(f"[起動] ディスクキャッシュ読み込み失敗（無視）: {e}")


def _save_disk_cache() -> None:
    """メモリキャッシュをディスクへ書き込む（バックグラウンドスレッドで実行）。"""
    global _DISK_CACHE_LAST_SAVED
    now = _time.time()
    if now - _DISK_CACHE_LAST_SAVED < _DISK_CACHE_SAVE_INTERVAL:
        return
    _DISK_CACHE_LAST_SAVED = now

    def _write():
        try:
            payload = {
                "weather": {_key_to_str(k): list(v) for k, v in _WEATHER_CACHE.items()},
                "marine":  {_key_to_str(k): list(v) for k, v in _MARINE_CACHE.items()},
                "saved_at": now,
            }
            tmp = _DISK_CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, _DISK_CACHE_PATH)
        except Exception as e:
            print(f"[警告] ディスクキャッシュ書き込み失敗（無視）: {e}")

    threading.Thread(target=_write, daemon=True).start()


# モジュール読み込み時に即座にディスクキャッシュを復元
_load_disk_cache()


def _get_weatherapi_key() -> str:
    return os.environ.get("WEATHERAPI_KEY", "")


def _get_openmeteo_api_key() -> str:
    return os.environ.get("OPEN_METEO_API_KEY", "")


def _openmeteo_url(base_path: str, params: list) -> str:
    """Open-Meteo のリクエスト URL を生成する。
    OPEN_METEO_API_KEY が設定されていればカスタマー URL + apikey を使用。"""
    api_key = _get_openmeteo_api_key()
    if api_key:
        # marine-api を先に判定（api.open-meteo.com の置換と衝突するため）
        if "marine-api.open-meteo.com" in base_path:
            host = base_path.replace("marine-api.open-meteo.com", "customer-marine-api.open-meteo.com")
        else:
            host = base_path.replace("api.open-meteo.com", "customer-api.open-meteo.com")
        return host + "?" + urllib.parse.urlencode(params) + "&apikey=" + api_key
    return base_path + "?" + urllib.parse.urlencode(params)


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
            full_url = _openmeteo_url(base_url, params)
            with urllib.request.urlopen(full_url, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            _WEATHER_CACHE[cache_key] = (_time.time(), result)
            _save_disk_cache()
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
            full_url = _openmeteo_url(base_url, params)
            with urllib.request.urlopen(full_url, timeout=15) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            _MARINE_CACHE[cache_key] = (_time.time(), result)
            _save_disk_cache()
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


def _weather_is_complete(result: dict) -> bool:
    """気象レスポンスに日次気温と朝8時の風速・風向が揃っているか判定する。"""
    if not result:
        return False
    daily = result.get("daily") or {}
    hourly = result.get("hourly") or {}
    temp_list = daily.get("temperature_2m_max") or []
    spd_list = hourly.get("wind_speed_10m") or []
    dir_list = hourly.get("wind_direction_10m") or []
    if not temp_list or temp_list[0] is None:
        return False
    if len(spd_list) <= 8 or spd_list[8] is None:
        return False
    if len(dir_list) <= 8 or dir_list[8] is None:
        return False
    return True


def fetch_weather_with_fallback(
    lat: float,
    lon: float,
    fallback_coords: list,
    date_str: str,
) -> dict:
    """プライマリ → フォールバック座標の順で気象データを取得する。
    `_weather_is_complete` を満たすレスポンスが得られたらそれを返す。
    満たすものが無ければ最後に取得できたベストエフォートの結果を返す。"""
    primary = fetch_weather(lat, lon, date_str)
    if _weather_is_complete(primary):
        return primary

    # プライマリからの距離が近い順に試す（marine の fallback ソートと同じ方式）
    ordered = sorted(
        fallback_coords or [],
        key=lambda p: (p[0] - lat) ** 2 + (p[1] - lon) ** 2,
    )
    best = primary
    for fb_lat, fb_lon in ordered:
        # 429 クールダウン中は追加コールしない
        if _time.time() < _WEATHER_RATE_LIMIT_UNTIL:
            break
        # プライマリと同じグリッドに丸められる座標はスキップ
        if round(round(fb_lat * 10) / 10, 1) == round(round(lat * 10) / 10, 1) \
           and round(round(fb_lon * 10) / 10, 1) == round(round(lon * 10) / 10, 1):
            continue
        result = fetch_weather(fb_lat, fb_lon, date_str)
        if _weather_is_complete(result):
            result["_is_fallback"] = True
            return result
        if result and not best:
            best = result
    return best or {}


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
        full_url = _openmeteo_url(base_url, params)
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
