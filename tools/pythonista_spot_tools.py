#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pythonista (iPhone) 用 釣りスポット JSON 作成・修正ツール

機能:
  1. 新規スポット作成 — name/lat/lon/slug/notes/access を入力し、
                         OSM・海しる API でデータを取得して JSON を出力
  2. 既存スポット修正 — slug 指定で JSON を読み込み、各フィールドを編集。
                         座標または sea_bearing_deg を変更した場合、
                         保存時に底質・等深線を自動再取得チャレンジ。

使い方:
  1. このファイルをiPhoneの Pythonista にコピー
  2. 実行すると対話メニューが表示される
  3. JSON の出力先は DEFAULT_SPOTS_DIR（デフォルト: スクリプトと同じ階層の spots/）

依存ライブラリ:
  - stdlib のみ（json, math, time, pathlib, urllib など）
  - 外部ライブラリ不要
"""

import json
import math
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ──────────────────────────────────────────
# 設定
# ──────────────────────────────────────────

USER_AGENT = "ShirogisuSpotBuilder/1.0 (personal-use; Pythonista)"

# macOS の python.org 版 Python は証明書バンドルを自動参照しないため
# 個人用ツールとして SSL 検証を無効化して確実に接続できるようにする
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
REQUEST_INTERVAL_SEC = 1.1

# 海しる試用キー（期限切れの場合は更新が必要）
API_KEYS = [
    "0e83ad5d93214e04abf37c970c32b641",
    "10784fa6ea604de687b2052e55e03879",
    "61b85294618247a6bf652a979c5a5bbc",
]

# 底質レイヤー
BOTTOM_LAYERS = [
    {"name": "砂",     "url": "https://api.msil.go.jp/sand/v2/MapServer/1/query"},
    {"name": "石・岩", "url": "https://api.msil.go.jp/stone-rock/v2/MapServer/1/query"},
    {"name": "礫",     "url": "https://api.msil.go.jp/gravel/v2/MapServer/1/query"},
    {"name": "貝殻",   "url": "https://api.msil.go.jp/shells/v2/MapServer/1/query"},
    {"name": "さんご", "url": "https://api.msil.go.jp/coral/v2/MapServer/1/query"},
    {"name": "溶岩",   "url": "https://api.msil.go.jp/lava/v2/MapServer/1/query"},
]

# 等深線レイヤー
DEPTH_LAYERS = [
    {"depth_m": 20,  "url": "https://api.msil.go.jp/depth-contour/v2/MapServer/10/query"},
    {"depth_m": 50,  "url": "https://api.msil.go.jp/depth-contour/v2/MapServer/11/query"},
    {"depth_m": 100, "url": "https://api.msil.go.jp/depth-contour/v2/MapServer/12/query"},
    {"depth_m": 150, "url": "https://api.msil.go.jp/depth-contour/v2/MapServer/13/query"},
    {"depth_m": 200, "url": "https://api.msil.go.jp/depth-contour/v2/MapServer/14/query"},
]

DEPTH_SEARCH_RULES = {
    20:  [500, 1000, 2000],
    50:  [1000, 2000, 5000],
    100: [2000, 5000, 10000],
    150: [2000, 5000, 10000],
    200: [2000, 5000, 10000],
}

BOTTOM_OFFSET_DISTANCES_M = [50, 100, 200]
BOTTOM_SEARCH_RADII_M = [100, 250, 500, 1000]

BOTTOM_TYPE_MAP = {
    "砂":    "sand",
    "礫":    "gravel",
    "貝殻":  "shell",
    "石・岩": "rock",
    "さんご": "coral",
    "溶岩":  "lava",
}

# スポット JSON の出力先（Pythonista 上の実際のパスに合わせて変更可）
DEFAULT_SPOTS_DIR = Path(__file__).parent.parent / "spots"

# ──────────────────────────────────────────
# 海岸線ローカルキャッシュ
# ──────────────────────────────────────────

_COASTLINE_CACHE_PATH = Path(__file__).parent / "data" / "coastline_elements.json"
_coastline_cache = None  # None=未試行, []=失敗/空, list=ロード済み


def _load_coastline_cache():
    global _coastline_cache
    if _coastline_cache is not None:
        return _coastline_cache
    if not _COASTLINE_CACHE_PATH.exists():
        _coastline_cache = []
        return _coastline_cache
    try:
        with _COASTLINE_CACHE_PATH.open(encoding="utf-8") as f:
            _coastline_cache = json.load(f)
        print(f"  [cache] 海岸線キャッシュ: {len(_coastline_cache)}ウェイ読み込み")
    except Exception as e:
        print(f"  [cache] 読み込み失敗: {e}")
        _coastline_cache = []
    return _coastline_cache


def _filter_coastline_local(lat, lon, distance_m):
    """キャッシュから distance_m 以内にノードを持つウェイを返す（粗フィルタ）。"""
    result = []
    for way in _load_coastline_cache():
        for node in way.get("geometry", []):
            if haversine_m(lat, lon, node["lat"], node["lon"]) <= distance_m:
                result.append(way)
                break
    return result


# ──────────────────────────────────────────
# 数学ユーティリティ
# ──────────────────────────────────────────

def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def destination_point(lat, lon, bearing_deg_val, distance_m):
    r = 6371000.0
    brng = math.radians(bearing_deg_val)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    d_div_r = distance_m / r
    lat2 = math.asin(
        math.sin(lat1) * math.cos(d_div_r) +
        math.cos(lat1) * math.sin(d_div_r) * math.cos(brng)
    )
    lon2 = lon1 + math.atan2(
        math.sin(brng) * math.sin(d_div_r) * math.cos(lat1),
        math.cos(d_div_r) - math.sin(lat1) * math.sin(lat2)
    )
    return math.degrees(lat2), math.degrees(lon2)


def bearing_deg(lat1, lon1, lat2, lon2):
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def nearest_point_on_segment(px, py, ax, ay, bx, by):
    dlat = bx - ax
    dlon = by - ay
    seg_len_sq = dlat * dlat + dlon * dlon
    if seg_len_sq == 0:
        return ax, ay, bearing_deg(ax, ay, bx, by) if (ax != bx or ay != by) else 0.0
    t = ((px - ax) * dlat + (py - ay) * dlon) / seg_len_sq
    t = max(0.0, min(1.0, t))
    nearest_lat = ax + t * dlat
    nearest_lon = ay + t * dlon
    seg_b = bearing_deg(ax, ay, bx, by)
    return nearest_lat, nearest_lon, seg_b


# ──────────────────────────────────────────
# Overpass API
# ──────────────────────────────────────────

_OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]


def _overpass_get(query, _retries=2):
    for endpoint in _OVERPASS_ENDPOINTS:
        url = endpoint + "?" + urllib.parse.urlencode({"data": query})
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        for attempt in range(_retries):
            try:
                with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                    return json.loads(resp.read().decode("utf-8")).get("elements", [])
            except urllib.error.HTTPError as e:
                if e.code in (429, 503, 504) and attempt < _retries - 1:
                    wait = 5 * (attempt + 1)   # 5s, 10s（旧: 30s, 60s）
                    print(f"    HTTP {e.code} – {wait}秒後にリトライ ({endpoint})...")
                    time.sleep(wait)
                else:
                    break  # このエンドポイントは諦めて次へ
            except Exception:
                break
    raise RuntimeError("すべての Overpass エンドポイントが失敗しました")


def calculate_sea_bearing(lat, lon, search_radius_m=10000):
    # ── キャッシュ優先（download_coastline.py でキャッシュ構築済みなら Overpass 不要）──
    cache = _load_coastline_cache()
    if cache:
        elements = _filter_coastline_local(lat, lon, search_radius_m * 1.5)
        if not elements:
            elements = _filter_coastline_local(lat, lon, 100_000 * 1.5)
        source = "cache"
    else:
        # ── Overpass フォールバック（キャッシュ未構築時のみ）──────────────────────
        query = (
            f"[out:json][timeout:25];"
            f"way[\"natural\"=\"coastline\"](around:{search_radius_m},{lat},{lon});"
            f"out geom qt 20;"
        )
        try:
            elements = _overpass_get(query)
        except Exception as e:
            print(f"    Overpass APIエラー: {e}")
            return None

        if not elements:
            wider = 100_000
            print(f"    半径{search_radius_m}mで海岸線なし。{wider}mで再試行...")
            query2 = (
                f"[out:json][timeout:25];"
                f"way[\"natural\"=\"coastline\"](around:{wider},{lat},{lon});"
                f"out geom qt 10;"
            )
            try:
                elements = _overpass_get(query2)
            except Exception as e:
                print(f"    Overpass API再試行エラー: {e}")
                return None
        source = "overpass"

    if not elements:
        print(f"    [{source}] 海岸線データが見つかりませんでした")
        return None

    print(f"    [{source}] {len(elements)}ウェイ発見 セグメント解析中...")
    best_dist = float("inf")
    best_seg_bearing = None

    for way in elements:
        geom = way.get("geometry", [])
        for i in range(len(geom) - 1):
            p1 = geom[i]
            p2 = geom[i + 1]
            seg_lat, seg_lon, seg_b = nearest_point_on_segment(
                lat, lon,
                p1["lat"], p1["lon"],
                p2["lat"], p2["lon"]
            )
            d = haversine_m(lat, lon, seg_lat, seg_lon)
            if d < best_dist:
                best_dist = d
                best_seg_bearing = seg_b

    if best_seg_bearing is None:
        return None

    return round((best_seg_bearing + 90) % 360, 1)


# ──────────────────────────────────────────
# 海しる API
# ──────────────────────────────────────────

def request_json_with_keys(url, params):
    last_error = None
    for key in API_KEYS:
        p = dict(params)
        p["subscription-key"] = key
        full_url = url + "?" + urllib.parse.urlencode(p)
        req = urllib.request.Request(full_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}"
        except Exception as e:
            last_error = str(e)
    raise RuntimeError(last_error or "API request failed")


def flatten_coords(geometry):
    coords = []
    if not geometry:
        return coords
    if "x" in geometry and "y" in geometry:
        return [[geometry["x"], geometry["y"]]]

    def walk(obj):
        if isinstance(obj, list):
            if len(obj) >= 2 and isinstance(obj[0], (int, float)) and isinstance(obj[1], (int, float)):
                coords.append([obj[0], obj[1]])
            else:
                for item in obj:
                    walk(item)

    for key in ("paths", "rings", "curvePaths", "curveRings", "points"):
        if key in geometry:
            walk(geometry[key])
    return coords


def min_distance_to_feature(lat, lon, feature):
    geometry = feature.get("geometry")
    coords = flatten_coords(geometry)
    if not coords:
        return None
    distances = [haversine_m(lat, lon, y, x) for x, y in coords]
    return min(distances) if distances else None


def query_bottom_types_near_point(lat, lon):
    results = []
    for layer in BOTTOM_LAYERS:
        best_hit = None
        for radius in BOTTOM_SEARCH_RADII_M:
            params = {
                "f": "json",
                "geometry": f"{lon},{lat}",
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "distance": radius,
                "units": "esriSRUnit_Meter",
                "returnGeometry": "true",
                "outFields": "*",
            }
            try:
                data = request_json_with_keys(layer["url"], params)
                features = data.get("features", []) if isinstance(data, dict) else []
                if not features:
                    continue
                candidates = []
                for feature in features:
                    d = min_distance_to_feature(lat, lon, feature)
                    if d is not None:
                        candidates.append((d, feature))
                if candidates:
                    candidates.sort(key=lambda x: x[0])
                    d, feature = candidates[0]
                    best_hit = {
                        "name": layer["name"],
                        "distance_m": round(d, 1),
                        "search_radius_m": radius,
                    }
                    break
            except Exception as e:
                best_hit = {"name": layer["name"], "error": str(e)}
                break
        if best_hit:
            results.append(best_hit)
    return results


def query_bottom_types(lat, lon, sea_bearing_deg_val):
    all_hits = []
    for offset_m in BOTTOM_OFFSET_DISTANCES_M:
        sample_lat, sample_lon = destination_point(lat, lon, sea_bearing_deg_val, offset_m)
        hits = query_bottom_types_near_point(sample_lat, sample_lon)
        for hit in hits:
            all_hits.append({"sample_offset_m": offset_m, **hit})

    success_hits = [h for h in all_hits if isinstance(h, dict) and "distance_m" in h]
    if success_hits:
        success_hits.sort(key=lambda x: (x["distance_m"], x["sample_offset_m"]))
        unique_names = []
        for h in success_hits:
            if h["name"] not in unique_names:
                unique_names.append(h["name"])
        return {
            "value": "/".join(unique_names),
            "best_match": success_hits[0],
            "status": "取得済み",
        }
    return {"value": None, "best_match": None, "status": "該当なし"}


def query_depth_contours(lat, lon):
    nearest_contours = []
    for layer in DEPTH_LAYERS:
        depth_m = layer["depth_m"]
        radii = DEPTH_SEARCH_RULES.get(depth_m, [1000, 2000, 5000])
        best_hit = None
        for radius in radii:
            params = {
                "f": "json",
                "geometry": f"{lon},{lat}",
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "distance": radius,
                "units": "esriSRUnit_Meter",
                "returnGeometry": "true",
                "outFields": "*",
            }
            try:
                data = request_json_with_keys(layer["url"], params)
                features = data.get("features", []) if isinstance(data, dict) else []
                if not features:
                    continue
                candidates = []
                for feature in features:
                    d = min_distance_to_feature(lat, lon, feature)
                    if d is not None:
                        candidates.append((d, feature))
                if candidates:
                    candidates.sort(key=lambda x: x[0])
                    d, _ = candidates[0]
                    best_hit = {"depth_m": depth_m, "distance_m": round(d, 1)}
                    break
            except Exception as e:
                best_hit = {"depth_m": depth_m, "error": str(e)}
                break
        if best_hit is None:
            best_hit = {"depth_m": depth_m, "distance_m": None, "status": "未検出"}
        nearest_contours.append(best_hit)
    return {"nearest_contours": nearest_contours}


def summarize_depth_profile_from_contours(nearest_contours):
    contour_map = {}
    for item in nearest_contours:
        if isinstance(item, dict):
            contour_map[item.get("depth_m")] = item.get("distance_m")
    return {
        "contour_reference": {
            "nearest_20m_contour_distance_m":  contour_map.get(20),
            "nearest_50m_contour_distance_m":  contour_map.get(50),
            "nearest_100m_contour_distance_m": contour_map.get(100),
            "nearest_150m_contour_distance_m": contour_map.get(150),
            "nearest_200m_contour_distance_m": contour_map.get(200),
        }
    }


# ──────────────────────────────────────────
# 導出ロジック
# ──────────────────────────────────────────

def derive_seabed_type(bottom_value):
    if not bottom_value:
        return "unknown"
    primary = bottom_value.split("/")[0].strip()
    return BOTTOM_TYPE_MAP.get(primary, "unknown")


def derive_kisugo_score(bottom_value):
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


def build_terrain_summary(bottom_value, dist_20m):
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


# ──────────────────────────────────────────
# API ラッパー（まとめて取得）
# ──────────────────────────────────────────

def fetch_physical_data(lat, lon, sea_bearing=None):
    """
    lat/lon から底質・等深線を取得し、導出済みのフィールドを返す。
    sea_bearing が None の場合は OSM から自動取得を試みる。

    Returns dict with keys:
      sea_bearing_deg, seabed_type, nearest_20m_contour_distance_m,
      bottom_kisugo_score, terrain_summary
    Or None if all API calls fail.
    """
    result = {
        "sea_bearing_deg": sea_bearing,
        "seabed_type": None,
        "nearest_20m_contour_distance_m": None,
        "bottom_kisugo_score": 50,
        "terrain_summary": "地形情報不足",
    }

    # 海方向
    if sea_bearing is None:
        print("  海方向を取得中 (OSM)...")
        sea_bearing = calculate_sea_bearing(lat, lon)
        if sea_bearing is not None:
            result["sea_bearing_deg"] = sea_bearing
            print(f"  sea_bearing_deg = {sea_bearing}°")
        else:
            print("  海方向取得失敗 — 底質クエリは 90° で代替")
            sea_bearing = 90.0
        time.sleep(REQUEST_INTERVAL_SEC)

    # 底質
    print("  底質を取得中 (海しる)...")
    try:
        bottom_result = query_bottom_types(lat, lon, sea_bearing)
        bottom_value = bottom_result.get("value")
        print(f"  bottom = {bottom_value} ({bottom_result.get('status')})")
    except Exception as e:
        print(f"  底質取得エラー: {e}")
        bottom_value = None
    time.sleep(REQUEST_INTERVAL_SEC)

    # 等深線
    print("  等深線を取得中 (海しる)...")
    try:
        depth_raw = query_depth_contours(lat, lon)
        depth_summary = summarize_depth_profile_from_contours(depth_raw["nearest_contours"])
        dist_20m = depth_summary["contour_reference"]["nearest_20m_contour_distance_m"]
        print(f"  nearest_20m_contour_distance_m = {dist_20m}")
    except Exception as e:
        print(f"  等深線取得エラー: {e}")
        dist_20m = None
    time.sleep(REQUEST_INTERVAL_SEC)

    # 何も取れなかった場合は None を返して呼び出し元に知らせる
    if bottom_value is None and dist_20m is None and result["sea_bearing_deg"] is None:
        return None

    result["seabed_type"] = derive_seabed_type(bottom_value)
    result["nearest_20m_contour_distance_m"] = dist_20m
    result["bottom_kisugo_score"] = derive_kisugo_score(bottom_value)
    result["terrain_summary"] = build_terrain_summary(bottom_value, dist_20m)
    return result


# ──────────────────────────────────────────
# JSON ビルダー
# ──────────────────────────────────────────

def build_spot_json(
    slug, name, lat, lon,
    area_name, area_slug, pref, pref_slug, city, city_slug,
    phys, notes, access
):
    return {
        "slug": slug,
        "name": name,
        "location": {
            "latitude":  lat,
            "longitude": lon,
        },
        "area": {
            "prefecture": pref,
            "pref_slug":  pref_slug,
            "area_name":  area_name,
            "area_slug":  area_slug,
            "city":       city,
            "city_slug":  city_slug,
        },
        "physical_features": {
            "sea_bearing_deg":               phys.get("sea_bearing_deg"),
            "seabed_type":                   phys.get("seabed_type") or "unknown",
            "depth_near_m":                  None,
            "depth_far_m":                   None,
            "surfer_spot":                   False,
            "nearest_20m_contour_distance_m": phys.get("nearest_20m_contour_distance_m"),
        },
        "derived_features": {
            "bottom_kisugo_score": phys.get("bottom_kisugo_score", 50),
            "terrain_summary":     phys.get("terrain_summary", "地形情報不足"),
        },
        "info": {
            "notes":     notes,
            "access":    access,
            "photo_url": f"https://raw.githubusercontent.com/sgrhirose-tech/fishing/resources/photos/{slug}.jpg",
        },
    }


# ──────────────────────────────────────────
# 入力ヘルパー
# ──────────────────────────────────────────

def prompt(label, default=None, cast=None):
    """プロンプトを表示して入力を受け取る。Enterでデフォルト値を使用。"""
    hint = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"  {label}{hint}: ").strip()
        if raw == "" and default is not None:
            return default
        if raw == "" and default is None:
            print("    入力が必要です")
            continue
        if cast:
            try:
                return cast(raw)
            except ValueError:
                print(f"    形式が不正です（{cast.__name__} を期待）")
                continue
        return raw


def prompt_optional(label, default=None):
    """空入力でNoneまたはdefaultを返す任意入力。"""
    hint = f" [{default}]" if default is not None else " (空でスキップ)"
    raw = input(f"  {label}{hint}: ").strip()
    if raw == "":
        return default
    return raw


# ──────────────────────────────────────────
# モード 1: 新規スポット作成
# ──────────────────────────────────────────

def mode_create(spots_dir: Path):
    spots_dir = Path(spots_dir)
    print("\n── 新規スポット作成 ──")
    slug      = prompt("slug (例: choshi_port)")
    name      = prompt("name (例: 銚子港)")
    lat       = prompt("latitude",  cast=float)
    lon       = prompt("longitude", cast=float)
    area_name = prompt("area_name",  default="九十九里")
    area_slug = prompt("area_slug",  default="kujukuri")
    pref      = prompt("prefecture", default="千葉県")
    pref_slug = prompt("pref_slug",  default="chiba")
    city      = prompt("city",       default="銚子市")
    city_slug = prompt("city_slug",  default="choshi")
    notes     = prompt("notes")
    access    = prompt("access")

    print("\nAPIからデータを取得中...")
    phys = fetch_physical_data(lat, lon)
    if phys is None:
        print("  警告: API取得に失敗しました。空の物理データで作成します。")
        phys = {
            "sea_bearing_deg": None,
            "seabed_type": "unknown",
            "nearest_20m_contour_distance_m": None,
            "bottom_kisugo_score": 50,
            "terrain_summary": "地形情報不足",
        }

    spot = build_spot_json(
        slug, name, lat, lon,
        area_name, area_slug, pref, pref_slug, city, city_slug,
        phys, notes, access
    )

    out_path = spots_dir / f"{slug}.json"
    spots_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(spot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n保存完了: {out_path}")


# ──────────────────────────────────────────
# モード 2: 既存スポット修正
# ──────────────────────────────────────────

def mode_edit(spots_dir: Path):
    spots_dir = Path(spots_dir)
    print("\n── 既存スポット修正 ──")

    # slug 選択
    jsons = sorted(spots_dir.glob("*.json"))
    jsons = [p for p in jsons if not p.name.startswith("_")]
    if not jsons:
        print(f"  {spots_dir} に JSON ファイルが見つかりません")
        return

    print("  利用可能なスポット:")
    for i, p in enumerate(jsons, 1):
        print(f"    {i}. {p.stem}")
    raw = input("  番号またはslugを入力: ").strip()
    if raw.isdigit() and 1 <= int(raw) <= len(jsons):
        json_path = jsons[int(raw) - 1]
    else:
        json_path = spots_dir / f"{raw}.json"
        if not json_path.exists():
            print(f"  ファイルが見つかりません: {json_path}")
            return

    spot = json.loads(json_path.read_text(encoding="utf-8"))
    pf   = spot.setdefault("physical_features", {})
    df   = spot.setdefault("derived_features", {})
    info = spot.setdefault("info", {})
    loc  = spot.setdefault("location", {})

    # 変更追跡（再取得トリガー用）
    orig_lat     = loc.get("latitude")
    orig_lon     = loc.get("longitude")
    orig_bearing = pf.get("sea_bearing_deg")

    # 編集ループ
    FIELDS = [
        ("latitude",                       lambda: loc.get("latitude"),
                                           lambda v: loc.update({"latitude": v}),       float),
        ("longitude",                      lambda: loc.get("longitude"),
                                           lambda v: loc.update({"longitude": v}),      float),
        ("sea_bearing_deg",                lambda: pf.get("sea_bearing_deg"),
                                           lambda v: pf.update({"sea_bearing_deg": v}), lambda s: float(s) if s.lower() != "null" else None),
        ("seabed_type",                    lambda: pf.get("seabed_type"),
                                           lambda v: pf.update({"seabed_type": v}),     str),
        ("nearest_20m_contour_distance_m", lambda: pf.get("nearest_20m_contour_distance_m"),
                                           lambda v: pf.update({"nearest_20m_contour_distance_m": v}),
                                           lambda s: float(s) if s.lower() != "null" else None),
        ("bottom_kisugo_score",            lambda: df.get("bottom_kisugo_score"),
                                           lambda v: df.update({"bottom_kisugo_score": v}), int),
        ("terrain_summary",                lambda: df.get("terrain_summary"),
                                           lambda v: df.update({"terrain_summary": v}),     str),
        ("notes",                          lambda: info.get("notes"),
                                           lambda v: info.update({"notes": v}),         str),
        ("access",                         lambda: info.get("access"),
                                           lambda v: info.update({"access": v}),        str),
    ]

    while True:
        print(f"\n  ── {spot.get('name', json_path.stem)} ──")
        for i, (label, getter, _, _) in enumerate(FIELDS, 1):
            print(f"  [{i}] {label}: {getter()}")
        print("  [0] 保存して終了")

        choice = input("  番号を選択: ").strip()
        if choice == "0":
            break
        if not choice.isdigit() or not (1 <= int(choice) <= len(FIELDS)):
            print("  無効な選択です")
            continue

        idx = int(choice) - 1
        label, getter, setter, cast = FIELDS[idx]
        current = getter()
        print(f"  現在値: {current}")
        raw = input(f"  新しい値 (Enterでキャンセル): ").strip()
        if raw == "":
            print("  変更なし")
            continue
        try:
            new_val = cast(raw)
            setter(new_val)
            print(f"  → {new_val} に変更")
        except (ValueError, TypeError) as e:
            print(f"  エラー: {e}")

    # ── 保存前処理 ──
    new_lat     = loc.get("latitude")
    new_lon     = loc.get("longitude")
    new_bearing = pf.get("sea_bearing_deg")

    geo_changed = (new_lat != orig_lat) or (new_lon != orig_lon) or (new_bearing != orig_bearing)

    if geo_changed:
        print("\n座標または海方向が変更されました。底質・等深線を再取得チャレンジ中...")
        phys = fetch_physical_data(new_lat, new_lon, sea_bearing=new_bearing)
        if phys is not None:
            # 成功: 物理データを更新
            pf["sea_bearing_deg"]               = phys["sea_bearing_deg"]
            pf["seabed_type"]                   = phys["seabed_type"] or "unknown"
            pf["nearest_20m_contour_distance_m"] = phys["nearest_20m_contour_distance_m"]
            df["bottom_kisugo_score"]            = phys["bottom_kisugo_score"]
            df["terrain_summary"]                = phys["terrain_summary"]
            print("  再取得成功 — 物理データを更新しました")
        else:
            print("  再取得失敗 — 手動入力値を保持します")

    # JSON 保存
    json_path.write_text(json.dumps(spot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n保存完了: {json_path}")


# ──────────────────────────────────────────
# モード 3: バッチCSV作成
# ──────────────────────────────────────────

def parse_csv_line(line: str, index: int) -> dict:
    """
    'name,lat,lon[,slug[,notes[,access]]]' をパースして dict を返す。
    パース失敗時は ValueError を raise。
    """
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 3:
        raise ValueError(f"フィールドが不足（name,lat,lon が必要）: {line!r}")

    name = parts[0]
    if not name:
        raise ValueError(f"name が空です: {line!r}")

    try:
        lat = float(parts[1])
        lon = float(parts[2])
    except ValueError:
        raise ValueError(f"lat/lon が数値ではありません: {parts[1]!r}, {parts[2]!r}")

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError(f"座標が範囲外: lat={lat}, lon={lon}")

    slug   = parts[3] if len(parts) > 3 and parts[3] else f"spot_{index:03d}"
    notes  = parts[4] if len(parts) > 4 else ""
    access = parts[5] if len(parts) > 5 else ""

    return {"slug": slug, "name": name, "lat": lat, "lon": lon,
            "notes": notes, "access": access}


def mode_batch_create(spots_dir: Path):
    spots_dir = Path(spots_dir)
    print("\n── バッチCSV作成 ──")

    # エリアデフォルト
    area_name = prompt("area_name",  default="九十九里")
    area_slug = prompt("area_slug",  default="kujukuri")
    pref      = prompt("prefecture", default="千葉県")
    pref_slug = prompt("pref_slug",  default="chiba")
    city      = prompt("city",       default="銚子市")
    city_slug = prompt("city_slug",  default="choshi")

    # CSV 入力（空行で終了）
    print("\nCSVを貼り付け（空行で終了）:")
    print("  書式: name,lat,lon[,slug[,notes[,access]]]")
    lines = []
    while True:
        try:
            row = input()
        except EOFError:
            break
        if row.strip() == "":
            break
        lines.append(row)

    # パース
    items = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            item = parse_csv_line(stripped, i)
            items.append(item)
        except ValueError as e:
            print(f"  [スキップ] 行{i}: {e}")

    if not items:
        print("有効な行がありません。終了します。")
        return

    print(f"\n{len(items)}件 読み込み:")
    for it in items:
        print(f"  {it['slug']} ({it['name']})  lat={it['lat']} lon={it['lon']}")

    ok = input("続行しますか？ [y/N]: ").strip().lower()
    if ok != "y":
        print("キャンセルしました。")
        return

    # バッチ処理
    spots_dir.mkdir(parents=True, exist_ok=True)
    success, failed = [], []

    for idx, it in enumerate(items, 1):
        slug  = it["slug"]
        name  = it["name"]
        lat   = it["lat"]
        lon   = it["lon"]
        notes = it["notes"]
        access = it["access"]

        print(f"\n[{idx}/{len(items)}] {name} ({slug}) ...")

        try:
            phys = fetch_physical_data(lat, lon)
            if phys is None:
                print("  警告: API取得失敗 — 空の物理データで作成")
                phys = {
                    "sea_bearing_deg": None,
                    "seabed_type": "unknown",
                    "nearest_20m_contour_distance_m": None,
                    "bottom_kisugo_score": 50,
                    "terrain_summary": "地形情報不足",
                }

            spot = build_spot_json(
                slug, name, lat, lon,
                area_name, area_slug, pref, pref_slug, city, city_slug,
                phys, notes, access
            )
            out_path = spots_dir / f"{slug}.json"
            out_path.write_text(json.dumps(spot, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"  → 保存完了: {out_path.name}")
            success.append(slug)

        except Exception as e:
            print(f"  → エラー: {e}")
            failed.append((slug, str(e)))

    # サマリー
    print(f"\n── 完了 ──")
    print(f"成功: {len(success)}件 / 失敗: {len(failed)}件")
    if failed:
        print("失敗詳細:")
        for slug, reason in failed:
            print(f"  {slug}: {reason}")


# ──────────────────────────────────────────
# エントリーポイント
# ──────────────────────────────────────────

def main():
    spots_dir = DEFAULT_SPOTS_DIR
    print(f"spots ディレクトリ: {spots_dir}")

    print("\n── メニュー ──")
    print("  [1] 新規スポット作成（対話形式）")
    print("  [2] 既存スポット修正")
    print("  [3] バッチCSV作成")
    print("  [0] 終了")
    choice = input("選択: ").strip()

    if choice == "1":
        mode_create(spots_dir)
    elif choice == "2":
        mode_edit(spots_dir)
    elif choice == "3":
        mode_batch_create(spots_dir)
    elif choice == "0":
        print("終了")
    else:
        print("無効な選択です")


if __name__ == "__main__":
    main()
