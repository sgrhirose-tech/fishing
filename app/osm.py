"""
OpenStreetMap (Overpass API) 周辺施設取得モジュール。
スポット詳細ページのマップに駐車場・トイレ・釣具屋・コンビニを表示する。
"""

import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

# 検索半径（メートル）
AMENITY_SEARCH_RADIUS_M = 1000

# 取得対象の施設タイプ
FACILITY_TYPES = [
    {"key": "amenity", "value": "parking",     "label": "駐車場",   "color": "#1565C0", "symbol": "P"},
    {"key": "amenity", "value": "toilets",     "label": "トイレ",   "color": "#2E7D32", "symbol": "WC"},
    {"key": "shop",    "value": "fishing",     "label": "釣具屋",   "color": "#E65100", "symbol": "釣"},
    {"key": "shop",    "value": "convenience", "label": "コンビニ", "color": "#6A1B9A", "symbol": "C"},
]

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

_osm_cache: dict = {}

# ── facilities.json キャッシュ ──────────────────────────────────
_FACILITIES_DATA: dict = {}  # slug -> list[dict]


def load_facilities_json(path: str | None = None) -> None:
    """起動時に data/facilities.json をメモリに読み込む。"""
    global _FACILITIES_DATA
    if path is None:
        path = str(pathlib.Path(__file__).parent.parent / "data" / "facilities.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        _FACILITIES_DATA = {k: v for k, v in data.items() if k != "_meta"}
        print(f"[facilities] {len(_FACILITIES_DATA)} スポット分の施設データを読み込みました")
    except FileNotFoundError:
        print(f"[facilities] {path} が見つかりません。Overpass API フォールバックを使用します")
    except Exception as e:
        print(f"[facilities] 読み込みエラー: {e}")


def get_cached_facilities(slug: str) -> list[dict] | None:
    """slug のキャッシュ済み施設リストを返す。未収録なら None。"""
    return _FACILITIES_DATA.get(slug)


def fetch_nearby_facilities(lat: float, lon: float,
                             radius_m: int = AMENITY_SEARCH_RADIUS_M) -> list[dict]:
    """
    Overpass API でスポット周辺の施設を取得する。
    戻り値: [{"type": "駐車場", "name": "...", "lat": ..., "lon": ..., "color": ...}, ...]
    """
    cache_key = (round(lat, 3), round(lon, 3), radius_m)
    if cache_key in _osm_cache:
        return _osm_cache[cache_key]

    # Overpass QL クエリ組み立て
    conditions = []
    for ft in FACILITY_TYPES:
        conditions.append(
            f'node["{ft["key"]}"="{ft["value"]}"](around:{radius_m},{lat},{lon});'
            f'way["{ft["key"]}"="{ft["value"]}"](around:{radius_m},{lat},{lon});'
        )
    query = (
        "[out:json][timeout:10];\n"
        "(\n"
        + "\n".join(conditions) +
        "\n);\n"
        "out center;"
    )

    facilities = []
    try:
        # 全エンドポイントを順に試す。429/504 なら次へ切り替え
        last_exc = None
        result = None
        for ep in OVERPASS_ENDPOINTS:
            try:
                data = urllib.parse.urlencode({"data": query}).encode("utf-8")
                req = urllib.request.Request(ep, data=data, method="POST")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                break  # 成功
            except urllib.error.HTTPError as e:
                if e.code in (429, 504):
                    last_exc = e
                    time.sleep(2)
                    continue  # 次のエンドポイントへ
                raise
            except Exception as e:
                last_exc = e
                break  # ネットワークエラー等はリトライしない
        if result is None:
            raise last_exc or Exception("全エンドポイントで取得失敗")

        # タグ → 施設種別・色のマッピング
        tag_map = {(ft["key"], ft["value"]): ft for ft in FACILITY_TYPES}

        for el in result.get("elements", []):
            tags = el.get("tags", {})
            # node: lat/lon 直接。way: center を使用
            if el["type"] == "node":
                el_lat, el_lon = el.get("lat"), el.get("lon")
            else:
                center = el.get("center", {})
                el_lat, el_lon = center.get("lat"), center.get("lon")
            if el_lat is None or el_lon is None:
                continue

            # 施設種別を特定
            ft_info = None
            for ft in FACILITY_TYPES:
                if tags.get(ft["key"]) == ft["value"]:
                    ft_info = ft
                    break
            if ft_info is None:
                continue

            name = tags.get("name") or tags.get("name:ja") or ft_info["label"]
            facilities.append({
                "type": ft_info["label"],
                "name": name,
                "lat": el_lat,
                "lon": el_lon,
                "color": ft_info["color"],
                "symbol": ft_info["symbol"],
            })

    except Exception as e:
        print(f"  [情報] OSM施設取得失敗 ({lat},{lon}): {e}")

    _osm_cache[cache_key] = facilities
    return facilities
