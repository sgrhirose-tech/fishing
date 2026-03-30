#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
スポット一括登録パイプライン

TSV ファイルを入力とし、以下の順でデータを充填して spots/<slug>.json を出力する。

  ② TSVパース + エリア自動判定 + Nominatim 逆ジオコーディング
  ③ Google Places 座標補正（1km 以内なら更新）
  ④ 海方向計算（確定座標から）
  ⑤ 底質・等深線取得（海しる API）
  ⑥ OSM 施設分類 → unknown 残りはキーワード補完

出力: spots/<slug>.json（SPOT_SCHEMA.md 準拠）

使い方:
  python tools/build_spots.py                 # 全件（tsv/ フォルダ内全 .tsv）
  python tools/build_spots.py --dry-run        # ドライラン（書き出しなし）
  python tools/build_spots.py --slug kamogawa  # 1件のみ
  python tools/build_spots.py --skip-google    # Google Places をスキップ
"""

import argparse
import json
import math
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import requests  # Google Places のみ（requirements.txt: requests>=2.31.0）

# pythonista_spot_tools から海方向計算と海しる API を import
sys.path.insert(0, str(Path(__file__).parent))
from pythonista_spot_tools import calculate_sea_bearing, fetch_physical_data

# ──────────────────────────────────────────
# パス定数
# ──────────────────────────────────────────

REPO_ROOT  = Path(__file__).parent.parent
AREAS_FILE = REPO_ROOT / "spots" / "_marine_areas.json"
OUTPUT_DIR = REPO_ROOT / "spots"
TSV_DIR    = REPO_ROOT / "tsv"
CONFIG_FILE = REPO_ROOT / "config.json"

# ──────────────────────────────────────────
# 定数
# ──────────────────────────────────────────

USER_AGENT = "ShirogisuSpotBuilder/1.0 (personal-use; Mac)"

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

AREA_MAP = {
    "相模湾":     ("sagamibay",      "kanagawa", "神奈川県"),
    "三浦半島":   ("miura",          "kanagawa", "神奈川県"),
    "東京湾":     ("tokyobay",       "kanagawa", "神奈川県"),
    "内房":       ("uchibo",         "chiba",    "千葉県"),
    "外房":       ("sotobo",         "chiba",    "千葉県"),
    "九十九里":   ("kujukuri",       "chiba",    "千葉県"),
    "東伊豆":     ("higashi-izu",    "shizuoka", "静岡県"),
    "南伊豆":     ("minami-izu",     "shizuoka", "静岡県"),
    "西伊豆":     ("nishi-izu",      "shizuoka", "静岡県"),
    "駿河":       ("suruga",         "shizuoka", "静岡県"),
    "遠州":       ("enshu",          "shizuoka", "静岡県"),
    "東三河":     ("higashi-mikawa", "aichi",    "愛知県"),
    "西三河":     ("nishi-mikawa",   "aichi",    "愛知県"),
    "尾張":       ("owari",          "aichi",    "愛知県"),
    "三重北中部": ("mie-north",      "mie",      "三重県"),
    "三重南部":   ("mie-south",      "mie",      "三重県"),
}

PREF_SLUG_MAP = {
    "神奈川県": "kanagawa",
    "東京都":   "tokyo",
    "千葉県":   "chiba",
    "静岡県":   "shizuoka",
    "愛知県":   "aichi",
    "三重県":   "mie",
}

# Google Places
PLACES_TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"

# Overpass
OVERPASS_ENDPOINTS = [
    "http://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]

# OSM 施設種別分類ルール
TERRAIN_TAGS = [
    ("natural",  "beach",      "sand_beach",       0.90),
    ("natural",  "sand",       "sand_beach",       0.80),
    ("natural",  "shingle",    "rocky_shore",      0.75),
    ("natural",  "cliff",      "rocky_shore",      0.90),
    ("natural",  "rock",       "rocky_shore",      0.85),
    ("natural",  "bare_rock",  "rocky_shore",      0.85),
    ("man_made", "breakwater", "breakwater",       0.95),
    ("man_made", "seawall",    "breakwater",       0.85),
    ("man_made", "quay",       "breakwater",       0.80),
    ("man_made", "pier",       "fishing_facility", 0.85),
    ("leisure",  "fishing",    "fishing_facility", 0.95),
    ("leisure",  "marina",     "fishing_facility", 0.85),
    ("leisure",  "slipway",    "fishing_facility", 0.80),
    ("waterway", "dock",       "fishing_facility", 0.90),
    ("harbour",  "yes",        "fishing_facility", 0.85),
]

SECONDARY_TAGS = [
    ("landuse",  "harbour",  "harbour"),
    ("man_made", "pier",     "pier"),
    ("leisure",  "slipway",  "slipway"),
    ("leisure",  "marina",   "marina"),
    ("natural",  "cliff",    "cliff"),
    ("amenity",  "parking",  "parking_nearby"),
]

_DIST_FACTORS = [(15, 1.0), (50, 0.85), (150, 0.65), (300, 0.45)]

# キーワード補完ルール
NAME_KEYWORDS = [
    ("砂浜",     "sand_beach",       0.80),
    ("ビーチ",   "sand_beach",       0.75),
    ("海岸",     "sand_beach",       0.70),
    ("浜",       "sand_beach",       0.60),
    ("磯",       "rocky_shore",      0.75),
    ("岩場",     "rocky_shore",      0.80),
    ("崎",       "rocky_shore",      0.55),
    ("鼻",       "rocky_shore",      0.55),
    ("防波堤",   "breakwater",       0.90),
    ("堤防",     "breakwater",       0.85),
    ("波止",     "breakwater",       0.85),
    ("テトラ",   "breakwater",       0.85),
    ("漁港",     "fishing_facility", 0.90),
    ("岸壁",     "fishing_facility", 0.85),
    ("ふ頭",     "fishing_facility", 0.85),
    ("ふ頭公園", "fishing_facility", 0.85),
    ("埠頭",     "fishing_facility", 0.85),
    ("埠頭公園", "fishing_facility", 0.85),
    ("桟橋",     "fishing_facility", 0.80),
    ("港",       "fishing_facility", 0.75),
]

# Overpass 動的スリープ（エラー後に延長、連続成功で短縮）
_overpass_sleep = 1.0


# ──────────────────────────────────────────
# ② TSVパース
# ──────────────────────────────────────────

def parse_tsv_file(path: Path) -> list:
    """
    TSV（ヘッダなし・タブ区切り・6〜7列）を読み込み list[dict] を返す。
    列順: name / lat / lon / slug / notes / access / [area上書き]
    """
    text = path.read_text(encoding="utf-8-sig")
    records = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cols = stripped.split("\t")
        if len(cols) < 4:
            print(f"  [スキップ] 行{lineno}: 列数不足 ({len(cols)}列)")
            continue
        try:
            lat = float(cols[1])
            lon = float(cols[2])
        except ValueError:
            print(f"  [スキップ] 行{lineno}: lat/lon が数値でない")
            continue
        records.append({
            "name":   cols[0].strip(),
            "lat":    lat,
            "lon":    lon,
            "slug":   cols[3].strip(),
            "notes":  cols[4].strip() if len(cols) > 4 else "",
            "access": cols[5].strip() if len(cols) > 5 else "",
            "area":   cols[6].strip() if len(cols) > 6 else "",
        })
    return records


# ──────────────────────────────────────────
# ② エリア自動判定
# ──────────────────────────────────────────

def assign_area(lat: float, lon: float) -> str:
    """_marine_areas.json の BBox + 距離でエリア名を返す。"""
    try:
        data = json.loads(AREAS_FILE.read_text(encoding="utf-8"))
        areas = data.get("areas", {})
    except Exception as e:
        print(f"  [警告] _marine_areas.json 読み込み失敗: {e}")
        return "不明"

    candidates = {
        name: info for name, info in areas.items()
        if (info.get("lat_min", -90) <= lat <= info.get("lat_max", 90) and
            info.get("lon_min", -180) <= lon <= info.get("lon_max", 180))
    }
    if not candidates:
        candidates = areas

    best_name, best_dist = "不明", float("inf")
    for name, info in candidates.items():
        d = math.sqrt((lat - info["center_lat"]) ** 2 + (lon - info["center_lon"]) ** 2)
        if d < best_dist:
            best_dist = d
            best_name = name
    return best_name


# ──────────────────────────────────────────
# ② Nominatim 逆ジオコーディング
# ──────────────────────────────────────────

def reverse_geocode(lat: float, lon: float, lang: str = "ja,en") -> dict:
    """Nominatim から prefecture / city を取得する。失敗時は空文字。"""
    params = {
        "lat": lat, "lon": lon, "format": "jsonv2",
        "addressdetails": 1, "accept-language": lang, "zoom": 14,
    }
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            addr = json.loads(resp.read().decode("utf-8")).get("address", {})
        prefecture = (addr.get("state") or addr.get("province") or addr.get("region") or "")
        city = (
            addr.get("city") or addr.get("town") or
            addr.get("village") or addr.get("municipality") or
            addr.get("county") or ""
        )
        return {"prefecture": prefecture, "city": city}
    except Exception as e:
        print(f"  [警告] Nominatim 取得失敗: {e}")
        return {"prefecture": "", "city": ""}


def _city_to_slug(name_en: str) -> str:
    s = name_en.lower().strip()
    s = re.sub(r'[\s\-]+', '-', s)
    s = re.sub(r'[^a-z0-9\-]', '', s)
    return s.strip('-')


# ──────────────────────────────────────────
# ③ Google Places 座標補正
# ──────────────────────────────────────────

def load_config(config_path: Path) -> dict:
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    required = ["api_key", "threshold_km", "search_language", "search_region",
                "request_delay_sec", "max_candidates"]
    for key in required:
        if key not in cfg:
            raise ValueError(f"config.json に '{key}' が見つかりません")
    return cfg


def search_place(name: str, cfg: dict) -> list:
    """Places Text Search で name を検索し候補リストを返す。429 時は指数バックオフ。"""
    params = {
        "query": name,
        "language": cfg["search_language"],
        "region":   cfg["search_region"],
        "key":      cfg["api_key"],
    }
    wait = 1.0
    for attempt in range(4):
        try:
            resp = requests.get(PLACES_TEXT_SEARCH_URL, params=params, timeout=10)
            if resp.status_code == 429:
                if attempt < 3:
                    print(f"  [Google] 429 — {wait:.0f}s 待機してリトライ")
                    time.sleep(wait)
                    wait *= 2
                    continue
                return []
            resp.raise_for_status()
            results = resp.json().get("results", [])
            candidates = []
            for r in results[: cfg["max_candidates"]]:
                loc = r.get("geometry", {}).get("location", {})
                if "lat" in loc and "lng" in loc:
                    candidates.append({"name": r["name"], "lat": loc["lat"], "lon": loc["lng"],
                                       "address": r.get("formatted_address", "")})
            return candidates
        except requests.RequestException as e:
            if attempt < 3:
                time.sleep(wait)
                wait *= 2
            else:
                print(f"  [Google] リクエストエラー: {e}")
                return []
    return []


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def refine_coords(name: str, city: str, lat: float, lon: float, cfg: dict,
                  pref_hint: str = "") -> tuple:
    """
    Google Places で座標を補正する。
    Returns: (new_lat, new_lon, source_label)
      source_label: "google" / "google_exact" (補正成功) / "tsv" (補正なし)
    """
    queries = [name]
    if city:
        queries.append(f"{name} {city}")

    candidates = []
    for query in queries:
        candidates = search_place(query, cfg)
        if candidates:
            break

    if not candidates:
        print(f"  [Google] NOT_FOUND — TSV 座標を使用")
        return lat, lon, "tsv"

    # lat=0, lon=0 は「座標未入力」として無条件採用（都道府県一致候補を優先）
    if lat == 0.0 and lon == 0.0:
        pref_cands = [c for c in candidates
                      if pref_hint and pref_hint in c.get("address", "")]
        chosen = pref_cands[0] if pref_cands else candidates[0]
        print(f"  [Google] 直接取得: → '{chosen['name']}' "
              f"({chosen['lat']:.5f}, {chosen['lon']:.5f})")
        return chosen["lat"], chosen["lon"], "google_direct"

    best = min(candidates, key=lambda c: haversine_km(lat, lon, c["lat"], c["lon"]))
    dist = haversine_km(lat, lon, best["lat"], best["lon"])

    if dist <= cfg["threshold_km"]:
        print(f"  [Google] 補正: {dist * 1000:.0f}m → '{best['name']}'")
        return best["lat"], best["lon"], "google"
    else:
        # 名称完全一致 かつ 都道府県一致なら距離閾値を無視して補正
        name_match = best["name"] == name
        pref_match = pref_hint and pref_hint in best.get("address", "")
        if name_match and pref_match:
            print(f"  [Google] 補正(完全一致・{pref_hint}): {dist:.2f}km → '{best['name']}'")
            return best["lat"], best["lon"], "google_exact"
        print(f"  [Google] TOO_FAR ({dist:.2f}km) '{best['name']}' — TSV 座標を使用")
        return lat, lon, "tsv"


# ──────────────────────────────────────────
# ⑥ OSM 施設分類
# ──────────────────────────────────────────

def _haversine_m(lat1, lon1, lat2, lon2):
    R = 6_371_000
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _dist_factor(dist_m: float) -> float:
    for threshold, factor in _DIST_FACTORS:
        if dist_m <= threshold:
            return factor
    return 0.0


def _overpass_post(query: str) -> dict:
    global _overpass_sleep
    encoded = urllib.parse.urlencode({"data": query}).encode("utf-8")
    last_err = None
    for i, endpoint in enumerate(OVERPASS_ENDPOINTS):
        if i > 0:
            time.sleep(2)
        req = urllib.request.Request(endpoint, data=encoded, method="POST")
        req.add_header("User-Agent", USER_AGENT)
        ctx = None if endpoint.startswith("http://") else _SSL_CTX
        try:
            with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            _overpass_sleep = max(1.0, _overpass_sleep * 0.8)
            return result
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 502, 503, 504):
                last_err = e
                continue
            raise
        except Exception as e:
            last_err = e
            continue
    _overpass_sleep = min(30.0, _overpass_sleep * 2)
    raise last_err


def classify_spot(lat: float, lon: float) -> dict | None:
    """Overpass でスポット周辺地物を取得し施設種別を返す。失敗時は None。"""
    query = (
        "[out:json][timeout:20];\n(\n"
        f'  node["natural"~"^(beach|sand|shingle|cliff|rock|bare_rock)$"](around:300,{lat},{lon});\n'
        f'  way["natural"~"^(beach|sand|shingle|cliff|rock|bare_rock)$"](around:300,{lat},{lon});\n'
        f'  node["man_made"~"^(breakwater|seawall|quay|pier)$"](around:300,{lat},{lon});\n'
        f'  way["man_made"~"^(breakwater|seawall|quay|pier)$"](around:300,{lat},{lon});\n'
        f'  node["leisure"~"^(fishing|marina|slipway)$"](around:300,{lat},{lon});\n'
        f'  way["leisure"~"^(fishing|marina|slipway)$"](around:300,{lat},{lon});\n'
        f'  node["landuse"="harbour"](around:300,{lat},{lon});\n'
        f'  way["landuse"="harbour"](around:300,{lat},{lon});\n'
        f'  node["waterway"="dock"](around:300,{lat},{lon});\n'
        f'  way["waterway"="dock"](around:300,{lat},{lon});\n'
        f'  node["harbour"="yes"](around:300,{lat},{lon});\n'
        f'  way["harbour"="yes"](around:300,{lat},{lon});\n'
        ");\nout center;"
    )
    try:
        result = _overpass_post(query)
    except Exception as e:
        print(f"  [Overpass] 取得失敗: {e}")
        return None

    scores: dict[str, float] = {}
    secondary: set[str] = set()
    evidence: list[str] = []

    for el in result.get("elements", []):
        tags = el.get("tags", {})
        if el["type"] == "node":
            el_lat, el_lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center", {})
            el_lat, el_lon = center.get("lat"), center.get("lon")
        if el_lat is None or el_lon is None:
            continue

        dist = _haversine_m(lat, lon, el_lat, el_lon)
        factor = _dist_factor(dist)
        if factor == 0.0:
            continue

        for key, value, primary_type, base_conf in TERRAIN_TAGS:
            if tags.get(key) == value:
                score = base_conf * factor
                if score > scores.get(primary_type, 0):
                    scores[primary_type] = score
                evidence.append(f"{key}={value}@{int(dist)}m")
                break

        for key, value, flag in SECONDARY_TAGS:
            if tags.get(key) == value:
                secondary.add(flag)

    if not scores:
        primary_type, confidence = "unknown", 0.0
    else:
        primary_type = max(scores, key=scores.get)
        confidence   = round(scores[primary_type], 2)

    return {
        "primary_type":    primary_type,
        "confidence":      confidence,
        "secondary_flags": sorted(secondary),
        "source":          "osm_rule",
        "osm_evidence":    sorted(set(evidence))[:5],
    }


# ──────────────────────────────────────────
# ⑥ キーワード補完（unknown 残り用）
# ──────────────────────────────────────────

def classify_by_keyword(name: str) -> dict | None:
    """スポット名末尾キーワードで分類。マッチなし / バッティング時は None。"""
    hits: dict[str, tuple] = {}
    for kw, cls, conf in NAME_KEYWORDS:
        if name.endswith(kw):
            if cls not in hits or conf > hits[cls][0]:
                hits[cls] = (conf, kw)

    if not hits:
        return None

    best_conf = max(c for c, _ in hits.values())
    best_classes = [cls for cls, (c, _) in hits.items() if c == best_conf]

    if len(best_classes) > 1:
        return None  # バッティング → 要目視

    cls = best_classes[0]
    conf, kw = hits[cls]
    return {
        "primary_type":    cls,
        "confidence":      conf,
        "secondary_flags": [],
        "source":          "name_keyword",
        "osm_evidence":    [f"keyword:{kw}"],
    }


# ──────────────────────────────────────────
# 1 レコードを処理
# ──────────────────────────────────────────

def process_record(rec: dict, idx: int, total: int, cfg: dict,
                   skip_google: bool, dry_run: bool) -> bool:
    name   = rec["name"]
    lat    = rec["lat"]
    lon    = rec["lon"]
    slug   = rec["slug"]
    notes  = rec["notes"]
    access = rec["access"]

    print(f"\n[{idx}/{total}] {name} ({slug})")

    # ── ② エリア自動判定 ────────────────────────────────────
    area_name = rec.get("area") or assign_area(lat, lon)
    area_slug, pref_slug_fallback, pref_fallback = AREA_MAP.get(
        area_name, ("unknown", "unknown", "")
    )
    print(f"  エリア: {area_name} ({area_slug})")

    # ── ③ Google Places 座標補正（Nominatim より先に確定座標を得る）──
    if skip_google:
        print("  [Google] スキップ（--skip-google）")
        coord_source = "tsv"
    else:
        print("  座標補正 (Google Places)...", end=" ", flush=True)
        lat, lon, coord_source = refine_coords(name, "", lat, lon, cfg,
                                                pref_hint=pref_fallback)
        time.sleep(cfg["request_delay_sec"])

    # ── ② Nominatim 逆ジオコーディング（確定済み座標で取得）────
    print("  住所取得 (Nominatim)...", end=" ", flush=True)
    geo_ja = reverse_geocode(lat, lon, lang="ja,en")
    if not geo_ja["prefecture"]:
        geo_ja["prefecture"] = pref_fallback
    print(f"→ {geo_ja['prefecture']} {geo_ja['city']}")
    time.sleep(1.1)

    geo_en = reverse_geocode(lat, lon, lang="en,ja")
    city_slug = _city_to_slug(geo_en.get("city", ""))
    time.sleep(1.1)

    actual_pref      = geo_ja["prefecture"] or pref_fallback
    actual_pref_slug = PREF_SLUG_MAP.get(actual_pref, pref_slug_fallback)
    city             = geo_ja["city"]

    # ── ④ 海方向計算（確定座標から）──────────────────────────
    if skip_google:
        print("  海方向計算... スキップ（--skip-google）")
        sea_bearing = None
    else:
        print("  海方向計算 (OSM)...", end=" ", flush=True)
        try:
            sea_bearing = calculate_sea_bearing(lat, lon)
            print(f"→ {sea_bearing}°")
        except Exception as e:
            print(f"→ 失敗 ({e})")
            sea_bearing = None

    # ── ⑤ 底質・等深線取得（海しる）────────────────────────
    if skip_google:
        print("  底質・等深線取得... スキップ（--skip-google）")
        phys = None
    else:
        print("  底質・等深線取得 (海しる)...", end=" ", flush=True)
        try:
            phys = fetch_physical_data(lat, lon, sea_bearing=sea_bearing)
            if phys:
                print(f"→ {phys.get('seabed_type')} / 20m等深線 {phys.get('nearest_20m_contour_distance_m')}m")
            else:
                print("→ 取得失敗")
        except Exception as e:
            print(f"→ エラー ({e})")
            phys = None

    # ── ⑥ OSM 施設分類 ──────────────────────────────────────
    print("  施設分類 (Overpass)...", end=" ", flush=True)
    try:
        classification = classify_spot(lat, lon)
        if classification:
            print(f"→ {classification['primary_type']} (conf={classification['confidence']})")
        else:
            print("→ 失敗")
    except Exception as e:
        print(f"→ エラー ({e})")
        classification = None
    time.sleep(_overpass_sleep)

    # unknown 残りにキーワード補完を試みる
    if classification is None or classification["primary_type"] == "unknown":
        kw_cls = classify_by_keyword(name)
        if kw_cls:
            classification = kw_cls
            print(f"  キーワード補完: {kw_cls['primary_type']} (keyword: {kw_cls['osm_evidence']})")
        else:
            if classification is None:
                classification = {
                    "primary_type":    "unknown",
                    "confidence":      0.0,
                    "secondary_flags": [],
                    "source":          "osm_rule",
                    "osm_evidence":    [],
                }

    # ── ⑥-b サーフスポット判定 (OSM sport=surfing) ─────────────
    # 砂浜スポットに限定（漁港・磯など非砂浜での誤判定を防ぐ）
    _cls_type = classification.get("primary_type", "unknown") if classification else "unknown"
    if skip_google or _cls_type != "sand_beach":
        surfer_spot = False
        if not skip_google and _cls_type != "sand_beach":
            print(f"  サーフスポット判定... スキップ（{_cls_type}）")
    else:
        print("  サーフスポット判定 (OSM)...", end=" ", flush=True)
        try:
            from update_surfer_spots import is_surf_spot
            surfer_spot = is_surf_spot(lat, lon)
            print(f"→ {'true' if surfer_spot else 'false'}")
        except Exception as e:
            print(f"→ 失敗 ({e})")
            surfer_spot = False
        time.sleep(1.5)

    # ── JSON 組み立て ───────────────────────────────────────
    spot = {
        "slug": slug,
        "name": name,
        "location": {
            "latitude":  lat,
            "longitude": lon,
        },
        "area": {
            "prefecture": actual_pref,
            "pref_slug":  actual_pref_slug,
            "area_name":  area_name,
            "area_slug":  area_slug,
            "city":       city,
            "city_slug":  city_slug,
        },
        "physical_features": {
            "sea_bearing_deg":               sea_bearing if phys is None else phys.get("sea_bearing_deg", sea_bearing),
            "seabed_type":                   phys.get("seabed_type") if phys else None,
            "surfer_spot":                   surfer_spot,
            "nearest_20m_contour_distance_m": phys.get("nearest_20m_contour_distance_m") if phys else None,
        },
        "derived_features": {
            "bottom_kisugo_score": phys.get("bottom_kisugo_score", 50) if phys else 50,
            "seabed_summary":      phys.get("seabed_summary", "") if phys else "",
        },
        "info": {
            "notes":  notes,
            "access": access,
        },
        "classification": classification,
    }

    if dry_run:
        print(f"  [DRY RUN] 書き出しなし ({slug}.json)")
        return True

    out_path = OUTPUT_DIR / f"{slug}.json"
    out_path.write_text(json.dumps(spot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  → {out_path.relative_to(REPO_ROOT)}")
    return True


# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="TSV からスポット JSON を一括生成するパイプライン"
    )
    parser.add_argument(
        "--tsv-dir", default=str(TSV_DIR),
        help=f"TSV ファイルのディレクトリ (default: {TSV_DIR})",
    )
    parser.add_argument(
        "--config", default=str(CONFIG_FILE),
        help=f"設定ファイル (default: {CONFIG_FILE})",
    )
    parser.add_argument(
        "--slug", default=None,
        help="1件のみ処理するスラッグ（デバッグ用）",
    )
    parser.add_argument(
        "--skip-google", action="store_true",
        help="Google Places 座標補正をスキップ（API コール節約）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="spots/ に書き出さずプレビューのみ",
    )
    args = parser.parse_args()

    # Google Places 設定読み込み（--skip-google でも config は読む）
    cfg = load_config(Path(args.config))
    if cfg.get("api_key") == "YOUR_GOOGLE_PLACES_API_KEY":
        if not args.skip_google:
            print("[警告] config.json の api_key が未設定です。--skip-google で続行します。")
            args.skip_google = True

    tsv_dir = Path(args.tsv_dir)
    tsv_dir.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    tsv_files = sorted(tsv_dir.glob("*.tsv"))
    if not tsv_files:
        print(f"TSV ファイルが見つかりません: {tsv_dir}")
        print("tsv/ フォルダに .tsv ファイルを置いて再実行してください。")
        return

    # TSV をすべてパース
    all_records = []
    for tsv_path in tsv_files:
        recs = parse_tsv_file(tsv_path)
        print(f"読み込み: {tsv_path.name}  ({len(recs)}件)")
        all_records.extend(recs)

    # --slug フィルタ
    if args.slug:
        all_records = [r for r in all_records if r["slug"] == args.slug]
        if not all_records:
            print(f"slug '{args.slug}' が TSV に見つかりません")
            return

    if args.dry_run:
        print("\n=== DRY RUN モード ===")

    total = len(all_records)
    success, failed = [], []

    for idx, rec in enumerate(all_records, 1):
        try:
            ok = process_record(rec, idx, total, cfg,
                                skip_google=args.skip_google,
                                dry_run=args.dry_run)
            if ok:
                success.append(rec["slug"])
        except Exception as e:
            print(f"\n[エラー] {rec.get('slug', '?')}: {e}")
            failed.append((rec.get("slug", "?"), str(e)))

    print(f"\n── 完了 ──  成功: {len(success)}件 / 失敗: {len(failed)}件")
    if failed:
        print("失敗詳細:")
        for slug, reason in failed:
            print(f"  {slug}: {reason}")
    if not args.dry_run and success:
        print(f"\n出力先: {OUTPUT_DIR}")
        print("spot_editor.py で座標・各フィールドを確認・修正してください。")


if __name__ == "__main__":
    main()
