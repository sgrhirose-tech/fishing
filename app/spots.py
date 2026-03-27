"""
スポットデータ読み込みユーティリティ。
spots/ フォルダ内の JSON ファイルを読み込む。
"""

import json
from pathlib import Path

# spots/ フォルダのデフォルトパス（プロジェクトルート直下）
_ROOT = Path(__file__).parent.parent
_SPOTS_DIR = _ROOT / "spots"

# キャッシュ
_spots_cache: list | None = None
_marine_cache: tuple | None = None  # (MARINE_PROXY, _MARINE_FALLBACKS, area_centers)


def _load_marine_areas(spots_dir: Path | None = None) -> tuple[dict, list, dict]:
    """spots/_marine_areas.json からエリア定義を読み込む。
    戻り値: (MARINE_PROXY, fallbacks, area_centers)
    """
    p = (spots_dir or _SPOTS_DIR) / "_marine_areas.json"
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        proxy = {
            name: (v["lat"], v["lon"])
            for name, v in data.get("areas", {}).items()
        }
        fallbacks = [(v["lat"], v["lon"]) for v in data.get("fallbacks", [])]
        area_centers = {
            name: (v["center_lat"], v["center_lon"], v.get("fetch_km", 50))
            for name, v in data.get("areas", {}).items()
            if "center_lat" in v and "center_lon" in v
        }
        return proxy, fallbacks, area_centers
    except Exception as e:
        print(f"[警告] marine_areas 読み込み失敗: {e}")
        return {}, [], {}


def _get_marine_cache() -> tuple[dict, list, dict]:
    global _marine_cache
    if _marine_cache is None:
        _marine_cache = _load_marine_areas()
    return _marine_cache


def load_spots(spots_dir: str | None = None) -> list[dict]:
    """spots/ フォルダ内の JSON ファイルから全スポットデータを読み込む。"""
    global _spots_cache
    if _spots_cache is not None:
        return _spots_cache

    d = Path(spots_dir) if spots_dir else _SPOTS_DIR
    if not d.exists():
        print(f"[エラー] {d} フォルダが見つかりません")
        return []

    spots = []
    for p in sorted(d.glob("*.json")):
        if p.stem.startswith("_"):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                spots.append(json.load(f))
        except Exception as e:
            print(f"[警告] {p.name} の読み込みに失敗: {e}")

    _spots_cache = spots
    return spots


def load_spot(slug: str) -> dict | None:
    """スラッグで特定のスポットを返す。見つからなければ None。"""
    path = _SPOTS_DIR / f"{slug}.json"
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# スポットフィールドアクセサ
def spot_lat(s: dict) -> float:
    return s["location"]["latitude"]

def spot_lon(s: dict) -> float:
    return s["location"]["longitude"]

def spot_name(s: dict) -> str:
    return s["name"]

def spot_slug(s: dict) -> str:
    return s.get("slug", "")

def spot_area(s: dict) -> str:
    """市区町村名を返す（なければ都道府県名）。"""
    a = s.get("area", {})
    return a.get("city") or a.get("prefecture") or "不明"

def spot_area_name(s: dict) -> str:
    """エリア名（相模湾・三浦半島など）を返す。"""
    return s.get("area", {}).get("area_name", "")

def spot_bearing(s: dict) -> float | None:
    """海方向（度）。取得失敗時は None。"""
    return s.get("physical_features", {}).get("sea_bearing_deg")

def spot_kisugo(s: dict) -> float:
    """底質スコア 0〜100。"""
    return s.get("derived_features", {}).get("bottom_kisugo_score", 50)

def spot_terrain(s: dict) -> str:
    """地形サマリー文字列。"""
    return s.get("derived_features", {}).get("terrain_summary", "")

def classify_slope(dist_m) -> str:
    """20m等深線距離からslope_typeを返す。"""
    if dist_m is None:
        return "不明"
    if dist_m < 1000:
        return "急深"
    if dist_m < 2000:
        return "やや急深"
    return "遠浅"

def spot_slope_type(s: dict) -> str:
    """スポットの海底傾斜分類を返す。"""
    dist = s.get("physical_features", {}).get("nearest_20m_contour_distance_m")
    return classify_slope(dist)


# エリアユーティリティ
def get_marine_proxy(lat: float, lon: float) -> tuple[float, float]:
    """最近傍の沖合代理座標を返す。"""
    proxy, _, _ = _get_marine_cache()
    if not proxy:
        return lat, lon
    return min(proxy.values(), key=lambda p: (p[0] - lat) ** 2 + (p[1] - lon) ** 2)

def get_marine_proxy_dict() -> dict:
    proxy, _, _ = _get_marine_cache()
    return proxy

def get_marine_fallbacks() -> list:
    _, fallbacks, _ = _get_marine_cache()
    return fallbacks

def get_area_centers() -> dict:
    _, _, centers = _get_marine_cache()
    return centers

def assign_area(spot: dict) -> str:
    """スポット座標に最近傍のエリア名を返す。"""
    area_centers = get_area_centers()
    if not area_centers:
        return spot_area_name(spot)
    lat = spot_lat(spot)
    lon = spot_lon(spot)
    return min(area_centers, key=lambda n: (area_centers[n][0] - lat) ** 2 + (area_centers[n][1] - lon) ** 2)
