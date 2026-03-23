import json
import math
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

# ========= 設定 =========
INPUT_FILE = Path(__file__).with_name("spots_input.txt")
OUTPUT_DIR = Path(__file__).with_name("spots")

USER_AGENT = "ShirogisuSpotBuilder/1.0 (personal-use; Pythonista)"
REQUEST_INTERVAL_SEC = 1.1

# 海しる試用キー
API_KEYS = [
    "0e83ad5d93214e04abf37c970c32b641",
    "10784fa6ea604de687b2052e55e03879",
    "61b85294618247a6bf652a979c5a5bbc"
]

# 底質レイヤー
BOTTOM_LAYERS = [
    {"name": "砂", "url": "https://api.msil.go.jp/sand/v2/MapServer/1/query"},
    {"name": "石・岩", "url": "https://api.msil.go.jp/stone-rock/v2/MapServer/1/query"},
    {"name": "礫", "url": "https://api.msil.go.jp/gravel/v2/MapServer/1/query"},
    {"name": "貝殻", "url": "https://api.msil.go.jp/shells/v2/MapServer/1/query"},
    {"name": "さんご", "url": "https://api.msil.go.jp/coral/v2/MapServer/1/query"},
    {"name": "溶岩", "url": "https://api.msil.go.jp/lava/v2/MapServer/1/query"}
]

# 等深線レイヤー
DEPTH_LAYERS = [
    {"depth_m": 20, "url": "https://api.msil.go.jp/depth-contour/v2/MapServer/10/query"},
    {"depth_m": 50, "url": "https://api.msil.go.jp/depth-contour/v2/MapServer/11/query"},
    {"depth_m": 100, "url": "https://api.msil.go.jp/depth-contour/v2/MapServer/12/query"},
    {"depth_m": 150, "url": "https://api.msil.go.jp/depth-contour/v2/MapServer/13/query"},
    {"depth_m": 200, "url": "https://api.msil.go.jp/depth-contour/v2/MapServer/14/query"}
]

# 深い等深線ほど探索半径を広げる
DEPTH_SEARCH_RULES = {
    20: [500, 1000, 2000],
    50: [1000, 2000, 5000],
    100: [2000, 5000, 10000],
    150: [2000, 5000, 10000],
    200: [2000, 5000, 10000],
}

# 海底底質の検索に使う沖方向サンプル距離
BOTTOM_OFFSET_DISTANCES_M = [50, 100, 200]
BOTTOM_SEARCH_RADII_M = [100, 250, 500, 1000]


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def normalize_text(text):
    return unicodedata.normalize("NFKC", text).strip()


def slugify_filename(text):
    text = normalize_text(text)
    text = text.replace("　", " ")
    text = re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "spot"


def make_spot_id(name, index):
    safe = re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠ー_]+", "", normalize_text(name))
    if not safe:
        safe = "spot"
    return f"{safe}_{index:02d}"


def parse_input_line(line):
    s = normalize_text(line)
    if not s or s.startswith("#"):
        return None

    parts = [p.strip() for p in s.split("|") if p.strip() != ""]
    if len(parts) != 3:
        raise ValueError("入力形式は '地点名|緯度|経度' にしてください")

    name = parts[0]
    lat = float(parts[1])
    lon = float(parts[2])

    if not (-90 <= lat <= 90):
        raise ValueError(f"緯度が不正です: {lat}")
    if not (-180 <= lon <= 180):
        raise ValueError(f"経度が不正です: {lon}")

    return {
        "name": name,
        "lat": lat,
        "lon": lon,
    }


def load_input_items(path):
    if not path.exists():
        raise FileNotFoundError(f"入力ファイルが見つかりません: {path}")

    lines = path.read_text(encoding="utf-8").splitlines()
    items = []
    for line in lines:
        item = parse_input_line(line)
        if item:
            items.append(item)
    return items


def bearing_deg(lat1, lon1, lat2, lon2):
    """2点間の方位角（度、0=北、時計回り）を返す。"""
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def nearest_point_on_segment(px, py, ax, ay, bx, by):
    """
    点P=(px,py)に最も近い線分AB上の点と、そのセグメント方位角を返す。
    座標は (lat, lon) を想定し、小範囲では平面近似を使用。
    Returns (nearest_lat, nearest_lon, segment_bearing_deg)
    """
    # 平面近似（短い沿岸セグメントには十分な精度）
    dlat = bx - ax
    dlon = by - ay
    seg_len_sq = dlat * dlat + dlon * dlon

    if seg_len_sq == 0:
        return ax, ay, bearing_deg(ax, ay, bx, by) if (ax != bx or ay != by) else 0.0

    t = ((px - ax) * dlat + (py - ay) * dlon) / seg_len_sq
    t = max(0.0, min(1.0, t))

    nearest_lat = ax + t * dlat
    nearest_lon = ay + t * dlon
    seg_bearing = bearing_deg(ax, ay, bx, by)

    return nearest_lat, nearest_lon, seg_bearing


def calculate_sea_bearing(lat, lon, search_radius_m=5000):
    """
    OSM Overpass APIで周辺の海岸線(natural=coastline)を取得し、
    最近傍セグメントの法線（海方向）を返す。

    OSM規約: 進行方向の左が陸、右が海
    → 海方向 = セグメント方位 + 90°（mod 360）

    Returns
    -------
    float | None
        海方向の角度(度)。取得失敗時はNone。
    """
    query = (
        f"[out:json];"
        f"way[\"natural\"=\"coastline\"](around:{search_radius_m},{lat},{lon});"
        f"out geom;"
    )
    try:
        r = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            headers={"User-Agent": USER_AGENT},
            timeout=60
        )
        r.raise_for_status()
        elements = r.json().get("elements", [])
    except Exception as e:
        print(f"    Overpass APIエラー: {e}")
        return None

    if not elements:
        # 半径を広げて再試行
        wider = search_radius_m * 3
        print(f"    半径{search_radius_m}mで海岸線なし。{wider}mで再試行...")
        query2 = (
            f"[out:json];"
            f"way[\"natural\"=\"coastline\"](around:{wider},{lat},{lon});"
            f"out geom;"
        )
        try:
            r = requests.post(
                "https://overpass-api.de/api/interpreter",
                data={"data": query2},
                headers={"User-Agent": USER_AGENT},
                timeout=60
            )
            r.raise_for_status()
            elements = r.json().get("elements", [])
        except Exception as e:
            print(f"    Overpass API再試行エラー: {e}")
            return None

    if not elements:
        print(f"    海岸線データが見つかりませんでした (lat={lat}, lon={lon})")
        return None

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

    seaward = (best_seg_bearing + 90) % 360
    return round(seaward, 1)


def reverse_geocode(lat, lon):
    url = "https://nominatim.openstreetmap.org/reverse"
    params = {
        "lat": lat,
        "lon": lon,
        "format": "jsonv2",
        "addressdetails": 1,
        "accept-language": "ja,en",
        "zoom": 14
    }
    headers = {
        "User-Agent": USER_AGENT
    }

    r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def request_json_with_keys(url, params):
    last_error = None

    for key in API_KEYS:
        p = dict(params)
        p["subscription-key"] = key
        try:
            r = requests.get(url, params=p, timeout=30)
            if r.status_code == 200:
                return r.json()
            last_error = f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as e:
            last_error = str(e)

    raise RuntimeError(last_error or "API request failed")


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)

    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def destination_point(lat, lon, bearing_deg, distance_m):
    r = 6371000.0
    brng = math.radians(bearing_deg)
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

    distances = []
    for x, y in coords:
        distances.append(haversine_m(lat, lon, y, x))

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
                "outFields": "*"
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
                        "attributes_sample": feature.get("attributes", {})
                    }
                    break

            except Exception as e:
                best_hit = {
                    "name": layer["name"],
                    "error": str(e)
                }
                break

        if best_hit:
            results.append(best_hit)

    return results


def query_bottom_types(lat, lon, sea_bearing_deg):
    all_hits = []

    for offset_m in BOTTOM_OFFSET_DISTANCES_M:
        sample_lat, sample_lon = destination_point(lat, lon, sea_bearing_deg, offset_m)
        hits = query_bottom_types_near_point(sample_lat, sample_lon)

        for hit in hits:
            all_hits.append({
                "sample_offset_m": offset_m,
                "sample_lat": round(sample_lat, 8),
                "sample_lon": round(sample_lon, 8),
                **hit
            })

    success_hits = [h for h in all_hits if isinstance(h, dict) and "distance_m" in h]

    if success_hits:
        success_hits.sort(key=lambda x: (x["distance_m"], x["sample_offset_m"]))
        best = success_hits[0]

        unique_names = []
        for h in success_hits:
            if h["name"] not in unique_names:
                unique_names.append(h["name"])

        return {
            "value": "/".join(unique_names),
            "matched_layers": all_hits,
            "best_match": best,
            "status": "取得済み"
        }

    return {
        "value": None,
        "matched_layers": all_hits,
        "best_match": None,
        "status": "該当なし"
    }


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
                "outFields": "*"
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
                        "depth_m": depth_m,
                        "distance_m": round(d, 1),
                        "search_radius_m": radius,
                        "attributes": feature.get("attributes", {})
                    }
                    break

            except Exception as e:
                best_hit = {
                    "depth_m": depth_m,
                    "error": str(e)
                }
                break

        if best_hit is None:
            best_hit = {
                "depth_m": depth_m,
                "distance_m": None,
                "search_radius_m": None,
                "attributes": {},
                "status": "未検出"
            }

        nearest_contours.append(best_hit)

    return {
        "nearest_contours": nearest_contours,
        "status": "取得済み"
    }


def summarize_depth_profile_from_contours(nearest_contours):
    contour_map = {}
    for item in nearest_contours:
        if isinstance(item, dict):
            contour_map[item.get("depth_m")] = item.get("distance_m")

    d20 = contour_map.get(20)
    d50 = contour_map.get(50)
    d100 = contour_map.get(100)
    d150 = contour_map.get(150)
    d200 = contour_map.get(200)

    shallow_confirmed = None
    shallow_reason = "判定材料不足"

    if d20 is not None:
        if d20 >= 1000:
            shallow_confirmed = True
            shallow_reason = f"20m等深線が {d20}m 先のため、近岸は遠浅傾向"
        elif d20 >= 500:
            shallow_confirmed = True
            shallow_reason = f"20m等深線が {d20}m 先で、近岸は比較的なだらか"
        else:
            shallow_confirmed = False
            shallow_reason = f"20m等深線が {d20}m と近く、遠浅とは言い切れない"
    else:
        if d50 is not None and d50 >= 3000:
            shallow_confirmed = True
            shallow_reason = f"50m等深線が {d50}m 先のため、少なくとも近岸は遠浅傾向"
        elif d50 is not None:
            shallow_confirmed = None
            shallow_reason = f"50m等深線は {d50}m 先で確認されたが、20m等深線未取得のため判定保留"

    return {
        "depth_at_50m_from_shore_m": None,
        "depth_at_100m_from_shore_m": None,
        "depth_at_200m_from_shore_m": None,
        "depth_points_source": "海しる等深線APIでは直接取得不可",
        "shallow_profile_confirmed": shallow_confirmed,
        "shallow_profile_reason": shallow_reason,
        "contour_reference": {
            "nearest_20m_contour_distance_m": d20,
            "nearest_50m_contour_distance_m": d50,
            "nearest_100m_contour_distance_m": d100,
            "nearest_150m_contour_distance_m": d150,
            "nearest_200m_contour_distance_m": d200
        }
    }


def derive_features_from_physical(spot):
    pf = spot.get("physical_features") or {}
    bottom = pf.get("bottom_type") or {}
    depth = pf.get("depth_profile") or {}

    matched = bottom.get("matched_layers") or []
    names = []
    for item in matched:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name and name not in names:
            names.append(name)

    best_match = bottom.get("best_match") or {}
    best_name = best_match.get("name")

    bottom_primary = "不明"
    if best_name:
        bottom_primary = best_name
    elif "砂" in names:
        bottom_primary = "砂"
    elif names:
        bottom_primary = names[0]

    bottom_secondary = [n for n in names if n != bottom_primary]

    contour_reference = depth.get("contour_reference") or {}
    d20 = contour_reference.get("nearest_20m_contour_distance_m")
    d50 = contour_reference.get("nearest_50m_contour_distance_m")
    d100 = contour_reference.get("nearest_100m_contour_distance_m")
    d150 = contour_reference.get("nearest_150m_contour_distance_m")
    d200 = contour_reference.get("nearest_200m_contour_distance_m")

    shallow_confirmed = depth.get("shallow_profile_confirmed")

    if shallow_confirmed is True:
        slope_type = "遠浅"
        slope_score = 90
    elif shallow_confirmed is False:
        slope_type = "急深寄り"
        slope_score = 40
    else:
        slope_type = "不明"
        slope_score = 60

    bottom_kisugo_score = 50
    if bottom_primary == "砂":
        bottom_kisugo_score = 85
        if "石・岩" in bottom_secondary:
            bottom_kisugo_score -= 5
    elif bottom_primary in ["貝殻", "礫"]:
        bottom_kisugo_score = 65
    elif bottom_primary == "石・岩":
        bottom_kisugo_score = 35

    terrain_parts = []
    if bottom_primary != "不明":
        terrain_parts.append(f"{bottom_primary}主体")
    if "貝殻" in names:
        terrain_parts.append("貝殻混じり")
    if "石・岩" in names:
        terrain_parts.append("近傍に石要素あり")
    if shallow_confirmed is True:
        terrain_parts.append("遠浅")
    elif shallow_confirmed is False:
        terrain_parts.append("急深寄り")

    terrain_summary = "、".join(terrain_parts) if terrain_parts else "地形情報不足"
    if bottom_primary == "砂" and shallow_confirmed is True:
        terrain_summary += "。シロギス投げ釣り向きの地形"

    return {
        "bottom_primary": bottom_primary,
        "bottom_secondary": bottom_secondary,
        "bottom_is_sandy": bottom_primary == "砂",
        "bottom_has_shell": "貝殻" in names,
        "bottom_has_rock": "石・岩" in names,
        "bottom_kisugo_score": bottom_kisugo_score,
        "slope_type": slope_type,
        "slope_score": slope_score,
        "nearshore_depth_numeric_available": False,
        "nearshore_depth_note": depth.get("shallow_profile_reason"),
        "contour_distances_m": {
            "20m": d20,
            "50m": d50,
            "100m": d100,
            "150m": d150,
            "200m": d200
        },
        "terrain_confidence": "high" if d20 is not None else "medium",
        "terrain_summary": terrain_summary
    }


def build_spot_json(item, reverse_geo, bottom_data, depth_summary, depth_raw, index):
    name = item["name"]
    lat = item["lat"]
    lon = item["lon"]
    sea_bearing_deg = item.get("sea_bearing_deg")
    sea_bearing_source = item.get("sea_bearing_source", "unknown")
    sea_bearing_status = item.get("sea_bearing_status", "unknown")

    reverse_geo = reverse_geo or {}
    address = reverse_geo.get("address") or {}

    prefecture = (
        address.get("state")
        or address.get("province")
        or address.get("region")
    )

    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("municipality")
        or address.get("county")
    )

    display_name = reverse_geo.get("display_name", name)
    updated_at = now_iso()

    spot = {
        "spot_id": make_spot_id(name, index),
        "name": name,
        "official_name": display_name,
        "area": {
            "major": None,
            "minor": None,
            "prefecture": prefecture,
            "city": city,
            "address": display_name
        },
        "location": {
            "latitude": lat,
            "longitude": lon,
            "coordinate_source": "manual input",
            "reverse_geocode_source": "Nominatim Reverse API" if reverse_geo else None
        },
        "physical_features": {
            "shore_type": None,
            "sea_bearing_deg": sea_bearing_deg,
            "sea_bearing_source": sea_bearing_source,
            "sea_bearing_status": sea_bearing_status,
            "bottom_type": {
                **bottom_data,
                "source_system": "海しる",
                "last_updated": updated_at
            },
            "depth_profile": {
                **depth_summary,
                "raw_contours": depth_raw["nearest_contours"],
                "status": "取得済み",
                "source_system": "海しる",
                "last_updated": updated_at
            }
        },
        "derived_features": {},
        "metadata": {
            "created_at": updated_at,
            "updated_at": updated_at,
            "json_created_by": "build_spots_complete.py",
            "notes": [
                "座標付き入力から生成",
                "海に向かう方位はOSM海岸線データから自動算出",
                "住所系は reverse geocoding で取得",
                "底質・等深線は海しるAPIで取得"
            ]
        }
    }

    spot["derived_features"] = derive_features_from_physical(spot)
    return spot


def write_spot_json(spot, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = slugify_filename(spot["name"]) + ".json"
    path = output_dir / filename
    path.write_text(json.dumps(spot, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main():
    items = load_input_items(INPUT_FILE)
    if not items:
        print("入力データがありません")
        return

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    success = []
    failed = []

    print("入力地点数:", len(items))
    print("出力先:", OUTPUT_DIR)

    # --- 海方向を自動算出 ---
    print("\n海方向(sea_bearing_deg)をOSM海岸線データから自動算出中...")
    for idx, item in enumerate(items, start=1):
        name = item["name"]
        lat = item["lat"]
        lon = item["lon"]
        print(f"  [{idx}/{len(items)}] {name} ...", end=" ", flush=True)
        sea_bearing = calculate_sea_bearing(lat, lon)
        if sea_bearing is not None:
            item["sea_bearing_deg"] = sea_bearing
            item["sea_bearing_source"] = "OSM Overpass coastline"
            item["sea_bearing_status"] = "auto"
            print(f"{sea_bearing}°")
        else:
            item["sea_bearing_deg"] = None
            item["sea_bearing_source"] = "OSM Overpass coastline"
            item["sea_bearing_status"] = "failed"
            print("取得失敗")
        # Overpass API レート制限対策
        if idx < len(items):
            time.sleep(1.0)

    print()

    for idx, item in enumerate(items, start=1):
        name = item["name"]
        lat = item["lat"]
        lon = item["lon"]
        sea_bearing_deg = item.get("sea_bearing_deg")

        print("[{}/{}] 処理中: {}".format(idx, len(items), name))
        print("  lat={}, lon={}, sea_bearing_deg={}".format(lat, lon, sea_bearing_deg))

        if sea_bearing_deg is None:
            print("  スキップ: 海方向が取得できませんでした")
            failed.append({
                "name": name,
                "lat": lat,
                "lon": lon,
                "reason": "sea_bearing_deg取得失敗"
            })
            continue

        try:
            reverse_geo = None
            try:
                reverse_geo = reverse_geocode(lat, lon)
            except Exception as e:
                print("  reverse geocode失敗:", e)

            print("  底質取得中...")
            bottom_data = query_bottom_types(lat, lon, sea_bearing_deg)

            print("  等深線取得中...")
            depth_raw = query_depth_contours(lat, lon)
            depth_summary = summarize_depth_profile_from_contours(depth_raw["nearest_contours"])

            spot = build_spot_json(
                item=item,
                reverse_geo=reverse_geo,
                bottom_data=bottom_data,
                depth_summary=depth_summary,
                depth_raw=depth_raw,
                index=idx
            )

            path = write_spot_json(spot, OUTPUT_DIR)
            print("  作成完了:", path.name)

            success.append({
                "name": name,
                "file": str(path),
                "lat": lat,
                "lon": lon,
                "sea_bearing_deg": sea_bearing_deg
            })

        except Exception as e:
            print("  失敗:", e)
            failed.append({
                "name": name,
                "lat": lat,
                "lon": lon,
                "reason": str(e)
            })

        if idx < len(items):
            time.sleep(REQUEST_INTERVAL_SEC)

    report = {
        "created_at": now_iso(),
        "input_count": len(items),
        "success_count": len(success),
        "failed_count": len(failed),
        "success": success,
        "failed": failed
    }

    report_path = OUTPUT_DIR / "_build_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 完了 ===")
    print("成功:", len(success))
    print("失敗:", len(failed))
    print("レポート:", report_path.name)


if __name__ == "__main__":
    main()
