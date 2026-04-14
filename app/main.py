"""
FastAPI アプリケーション。
uvicorn app.main:app --reload で起動。
"""

import json
import os
import re as _re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.utils import formatdate

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request

from .constants import REGIONS, VALID_REGION_SLUGS, PREF_TO_REGION, REGION_NAMES
from .spots import load_spots, load_spot, spot_lat, spot_lon, spot_name, spot_slug
from .spots import spot_area, spot_area_name, spot_bearing, spot_kisugo, spot_terrain, spot_slope_type, spot_type_label, assign_area, get_area_centers, get_photos
from .weather import (fetch_weather, fetch_weather_range,
                       fetch_marine_weatherapi, fetch_marine, fetch_marine_range,
                       fetch_sst_noaa, get_weather_fetched_at)
from .scoring import score_spot, direction_label, score_7days
from .osm import fetch_nearby_facilities, load_facilities_json, get_cached_facilities

JST = timezone(timedelta(hours=9))

# ── 魚種マスタ ────────────────────────────────────────────────
_FISH_MASTER: dict = {}
_FISH_SLUG_TO_NAME: dict = {}  # {slug: 魚名}

def _load_fish_master() -> None:
    global _FISH_MASTER, _FISH_SLUG_TO_NAME
    path = _BASE / "data" / "fish_master.json"
    try:
        with open(path, encoding="utf-8") as f:
            _FISH_MASTER = json.load(f)
        _FISH_SLUG_TO_NAME = {v["slug"]: k for k, v in _FISH_MASTER.items() if "slug" in v}
        print(f"[fish_master] {len(_FISH_MASTER)} 魚種を読み込みました")
    except Exception as e:
        print(f"[fish_master] 読み込みエラー: {e}")


_METHOD_MASTER: dict = {}
_METHOD_SLUG_TO_NAME: dict = {}  # {slug: 釣法名}

def _load_method_master() -> None:
    global _METHOD_MASTER, _METHOD_SLUG_TO_NAME
    path = _BASE / "data" / "method_master.json"
    try:
        with open(path, encoding="utf-8") as f:
            _METHOD_MASTER = json.load(f)
        _METHOD_SLUG_TO_NAME = {v["slug"]: k for k, v in _METHOD_MASTER.items()}
        print(f"[method_master] {len(_METHOD_MASTER)} 釣法を読み込みました")
    except Exception as e:
        print(f"[method_master] 読み込みエラー: {e}")


# ── 釣法 → タックル マッピング ────────────────────────────────
# (category_slug, item_slug, 表示名) のタプルリスト
_METHOD_TO_TACKLE: dict[str, list[tuple[str, str, str]]] = {
    "サビキ釣り":   [("rod", "iso-rod", "磯竿"),          ("reel", "spinning", "スピニングリール"), ("line", "nylon", "ナイロンライン")],
    "投げ釣り":     [("rod", "casting-rod", "投げ竿"),    ("reel", "spinning", "スピニングリール"), ("line", "pe", "PEライン"), ("terminal", "casting-rig", "投げ釣り仕掛け")],
    "ウキ釣り":     [("rod", "iso-rod", "磯竿"),          ("reel", "spinning", "スピニングリール"), ("terminal", "float-rig", "ウキ釣り仕掛け")],
    "フカセ釣り":   [("rod", "iso-rod", "磯竿"),          ("reel", "spinning", "スピニングリール"), ("line", "fluorocarbon", "フロロカーボンライン")],
    "カゴ釣り":     [("rod", "iso-rod", "磯竿"),          ("reel", "spinning", "スピニングリール"), ("line", "nylon", "ナイロンライン")],
    "エギング":     [("rod", "eging-rod", "エギングロッド"), ("reel", "spinning", "スピニングリール"), ("line", "pe", "PEライン"), ("terminal", "egi", "エギ（餌木）")],
    "アジング":     [("rod", "lure-rod", "ルアーロッド"), ("reel", "spinning", "スピニングリール"), ("line", "fluorocarbon", "フロロカーボンライン")],
    "メバリング":   [("rod", "lure-rod", "ルアーロッド"), ("reel", "spinning", "スピニングリール"), ("line", "fluorocarbon", "フロロカーボンライン")],
    "ルアー釣り":   [("rod", "lure-rod", "ルアーロッド"), ("reel", "spinning", "スピニングリール"), ("line", "pe", "PEライン"), ("terminal", "lure", "ルアー・ワーム")],
    "ジギング":     [("rod", "lure-rod", "ルアーロッド"), ("reel", "spinning", "スピニングリール"), ("line", "pe", "PEライン"), ("terminal", "lure", "ルアー・ワーム")],
    "タイラバ":     [("rod", "boat-rod", "船竿"),         ("reel", "conventional", "両軸リール（船用）"), ("line", "pe", "PEライン")],
    "船釣り":       [("rod", "boat-rod", "船竿"),         ("reel", "conventional", "両軸リール（船用）"), ("line", "pe", "PEライン")],
    "バス釣り":     [("rod", "bass-rod", "バスロッド"),   ("reel", "spinning", "スピニングリール"), ("terminal", "lure", "ルアー・ワーム")],
}


def _get_tackle_for_methods(methods: list) -> list:
    """釣法名リストから重複なしのタックルリストを返す。"""
    seen: set = set()
    result = []
    for method in methods:
        for cat, slug, name in _METHOD_TO_TACKLE.get(method, []):
            key = (cat, slug)
            if key not in seen:
                seen.add(key)
                result.append({"category": cat, "slug": slug, "name": name})
    return result


def _tomorrow() -> str:
    return (datetime.now(JST) + timedelta(days=1)).strftime("%Y-%m-%d")

def _today() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")

_WEEKDAYS = "月火水木金土日"

def _format_date_jp(date_str: str) -> str:
    """'YYYY-MM-DD' → 'M月D日（曜日）' 形式に変換。"""
    from datetime import datetime as _dt
    d = _dt.strptime(date_str, "%Y-%m-%d")
    return f"{d.month}月{d.day}日（{_WEEKDAYS[d.weekday()]}）"


# ============================================================
# FastAPI アプリ
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    load_spots()
    load_facilities_json()
    _load_fish_master()
    _load_method_master()
    _slug_map = {k: v["slug"] for k, v in _FISH_MASTER.items() if "slug" in v}
    templates.env.globals["fish_slug_map"] = _slug_map
    templates.env.globals["fish_name_map"] = {v: k for k, v in _slug_map.items()}
    templates.env.globals["method_slug_map"] = {k: v["slug"] for k, v in _METHOD_MASTER.items()}
    yield


app = FastAPI(title="Tsuricast", lifespan=lifespan)

# 静的ファイルとテンプレート
import pathlib
_BASE = pathlib.Path(__file__).parent.parent

app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(_BASE / "templates"))

_ROBOTS_TXT = """\
User-agent: *
Allow: /
Disallow: /api/

# --- AI training crawlers: block ---
User-agent: GPTBot
Disallow: /

User-agent: ChatGPT-User
Disallow: /

User-agent: CCBot
Disallow: /

User-agent: anthropic-ai
Disallow: /


User-agent: Bytespider
Disallow: /

User-agent: Amazonbot
Disallow: /

User-agent: Applebot-Extended
Disallow: /

User-agent: cohere-ai
Disallow: /

User-agent: PerplexityBot
Disallow: /

User-agent: Diffbot
Disallow: /

User-agent: omgili
Disallow: /

User-agent: omgilibot
Disallow: /

Sitemap: https://tsuricast.jp/sitemap.xml
"""

@app.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
def robots_txt():
    return _ROBOTS_TXT

@app.get("/ads.txt", response_class=PlainTextResponse, include_in_schema=False)
def ads_txt():
    return "google.com, pub-1877528534583136, DIRECT, f08c47fec0942fa0\n"

@app.get("/feed.xml", include_in_schema=False)
def feed_xml():
    spots_dir = str(_BASE / "spots")
    spots = load_spots()

    def _mtime(s: dict) -> float:
        path = os.path.join(spots_dir, f"{s.get('slug', '')}.json")
        return os.path.getmtime(path) if os.path.exists(path) else 0.0

    recent = sorted(spots, key=_mtime, reverse=True)[:50]

    items = []
    for s in recent:
        a = s.get("area", {})
        p  = a.get("pref_slug", "")
        ar = a.get("area_slug", "")
        c  = a.get("city_slug", "")
        sl = s.get("slug", "")
        if not all([p, ar, c, sl]):
            continue
        url      = f"{_BASE_URL}/{p}/{ar}/{c}/{sl}"
        name     = s.get("name", sl)
        area_name = a.get("area_name", "")
        city      = a.get("city", "")
        seabed    = s.get("derived_features", {}).get("seabed_summary", "")
        desc = f"{area_name}・{city}の釣り場。{seabed}"
        pub  = formatdate(_mtime(s) or None, localtime=True)
        items.append(
            f"  <item>\n"
            f"    <title>{name}</title>\n"
            f"    <link>{url}</link>\n"
            f"    <description>{desc}</description>\n"
            f"    <guid isPermaLink=\"true\">{url}</guid>\n"
            f"    <pubDate>{pub}</pubDate>\n"
            f"  </item>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        '  <channel>\n'
        '    <title>Tsuricast 新着釣り場</title>\n'
        f'    <link>{_BASE_URL}/</link>\n'
        '    <description>千葉・東京・神奈川の釣り場天気・波高情報。天気・風・波高・水温を毎日更新。</description>\n'
        '    <language>ja</language>\n'
        f'    <atom:link href="{_BASE_URL}/feed.xml" rel="self" type="application/rss+xml"/>\n'
        + "\n".join(items) + "\n"
        '  </channel>\n'
        '</rss>'
    )
    return Response(xml, media_type="application/rss+xml")

_BASE_URL = "https://tsuricast.jp"

@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml():
    spots = load_spots()
    urls: list[tuple[str, str, str]] = []  # (loc, changefreq, priority)

    # 固定ページ
    urls.append((f"{_BASE_URL}/",       "daily",   "1.0"))
    urls.append((f"{_BASE_URL}/spots",  "daily",   "0.8"))
    urls.append((f"{_BASE_URL}/toilet/", "weekly",  "0.7"))
    urls.append((f"{_BASE_URL}/fish/",  "weekly",  "0.7"))
    for s in ("safety", "privacy", "about", "contact"):
        urls.append((f"{_BASE_URL}/{s}", "monthly", "0.4"))
    # 魚種別ページ
    for _fn, _fd in _FISH_MASTER.items():
        _fslug = _fd.get("slug")
        if _fslug:
            urls.append((f"{_BASE_URL}/fish/{_fslug}", "weekly", "0.6"))
    # 釣法ページ
    urls.append((f"{_BASE_URL}/method/", "weekly", "0.7"))
    for _mslug in _METHOD_SLUG_TO_NAME:
        urls.append((f"{_BASE_URL}/method/{_mslug}", "weekly", "0.6"))

    # スポットデータから動的ページを収集
    seen_prefs: set = set()
    seen_areas: set = set()
    seen_cities: set = set()
    for spot in spots:
        a = spot.get("area", {})
        p  = a.get("pref_slug", "")
        ar = a.get("area_slug", "")
        c  = a.get("city_slug", "")
        sl = spot.get("slug", "")
        if not all([p, ar, c, sl]):
            continue
        if p not in seen_prefs:
            seen_prefs.add(p)
            urls.append((f"{_BASE_URL}/{p}/", "weekly", "0.7"))
        if (p, ar) not in seen_areas:
            seen_areas.add((p, ar))
            urls.append((f"{_BASE_URL}/{p}/{ar}/", "weekly", "0.7"))
        if (p, ar, c) not in seen_cities:
            seen_cities.add((p, ar, c))
            urls.append((f"{_BASE_URL}/{p}/{ar}/{c}/", "weekly", "0.6"))
        urls.append((f"{_BASE_URL}/{p}/{ar}/{c}/{sl}", "weekly", "0.5"))

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for loc, freq, pri in urls:
        lines += [
            "  <url>",
            f"    <loc>{loc}</loc>",
            f"    <changefreq>{freq}</changefreq>",
            f"    <priority>{pri}</priority>",
            "  </url>",
        ]
    lines.append("</urlset>")
    return Response("\n".join(lines), media_type="application/xml")

# ============================================================
# API エンドポイント
# ============================================================

@app.get("/api/spots")
def api_spots():
    """全スポットの基本情報を JSON で返す（Leaflet マップ用）。"""
    spots = load_spots()
    return [
        {
            "slug": spot_slug(s),
            "name": spot_name(s),
            "lat": spot_lat(s),
            "lon": spot_lon(s),
            "area_name": spot_area_name(s),
            "city": spot_area(s),
            "pref_slug": s.get("area", {}).get("pref_slug", ""),
            "area_slug": s.get("area", {}).get("area_slug", ""),
            "city_slug": s.get("area", {}).get("city_slug", ""),
        }
        for s in spots
    ]



def _compute_forecast(spot) -> dict:
    """スポットの7日分予報を計算して返す。page_spot_detail と api_forecast で共用。"""
    from datetime import date, timedelta
    today = date.today()
    start = today.strftime("%Y-%m-%d")
    end   = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    lat = spot_lat(spot)
    lon = spot_lon(spot)
    area = assign_area(spot)
    area_centers = get_area_centers()
    fetch_km = area_centers[area][2] if area in area_centers else 50

    weather = fetch_weather_range(lat, lon, start, end)
    marine  = fetch_marine_range(lat, lon, start, end)
    if not marine:
        from .weather import fetch_marine_with_fallback
        marine = fetch_marine_with_fallback(lat, lon, start)
    sst = fetch_sst_noaa(lat, lon, start)

    all_days = score_7days(spot, weather, marine, sst=sst, fetch_km=fetch_km)

    # 潮名を tide736.net キャッシュデータで上書き（フォールバックなし・データなしは "ー"）
    from .tides import get_tide_data
    slug = spot_slug(spot)
    for day in all_days:
        tide_info = get_tide_data(slug, day["date"])
        if tide_info and tide_info.get("tide_name"):
            moon_str = f"（月齢{tide_info['moon_age']:.1f}）" if tide_info.get("moon_age") is not None else ""
            tide_str = f"{tide_info['tide_name']}{moon_str}"
        else:
            tide_str = "ー"
        for period in day["periods"]:
            period["tide"] = tide_str

    today_data = all_days[0] if all_days else None   # days[0] = 今日
    forecast   = all_days[1:]                        # days[1:] = 明日+6日

    # 気象データ取得時刻（JST 表示用）
    fetched_ts = get_weather_fetched_at(lat, lon, start, end)
    import datetime as _dt2
    if fetched_ts:
        fetched_at = _dt2.datetime.fromtimestamp(fetched_ts, tz=_dt2.timezone(
            _dt2.timedelta(hours=9))).strftime("%m/%d %H:%M")
    else:
        fetched_at = None

    return {"slug": slug, "days": forecast, "today": today_data, "fetched_at": fetched_at}


@app.get("/api/forecast/{slug}")
def api_forecast(slug: str):
    """スポットの7日分・4区分予報を返す。"""
    spot = load_spot(slug)
    if not spot:
        raise HTTPException(status_code=404, detail="スポットが見つかりません")
    return _compute_forecast(spot)


@app.get("/api/ai-comment/{slug}")
def api_ai_comment(slug: str):
    """翌日のAIコメントを生成して返す。ai_prompt.md が必要。"""
    from .ai import generate_spot_comment
    spot = load_spot(slug)
    if not spot:
        raise HTTPException(status_code=404, detail="スポットが見つかりません")
    tomorrow = _tomorrow()
    lat, lon = spot_lat(spot), spot_lon(spot)
    area = assign_area(spot)
    area_centers = get_area_centers()
    fetch_km = area_centers[area][2] if area in area_centers else 50
    weather = fetch_weather_range(lat, lon, tomorrow, tomorrow)
    marine = fetch_marine_range(lat, lon, tomorrow, tomorrow)
    if not marine:
        from .weather import fetch_marine_with_fallback
        marine = fetch_marine_with_fallback(lat, lon, tomorrow)
    sst = fetch_sst_noaa(lat, lon, tomorrow)
    days = score_7days(spot, weather, marine, sst=sst, fetch_km=fetch_km)
    periods = days[0]["periods"] if days else []
    text = generate_spot_comment(spot, periods, tomorrow)
    return {"comment": text, "date": tomorrow}


@app.get("/api/osm/{slug}")
def api_osm(slug: str):
    """スポット周辺の OSM 施設（駐車場・トイレ等）を返す。
    facilities.json にデータがあればそれを使用し、未収録の場合は Overpass API にフォールバックする。
    """
    spot = load_spot(slug)
    if not spot:
        raise HTTPException(status_code=404, detail="スポットが見つかりません")
    cached = get_cached_facilities(slug)
    if cached is not None:
        return {"slug": slug, "facilities": cached}
    facilities = fetch_nearby_facilities(spot_lat(spot), spot_lon(spot))
    return {"slug": slug, "facilities": facilities}


@app.get("/api/weather/{slug}")
def api_weather(slug: str, date: str | None = None):
    """単一スポットの気象データを返す（6h キャッシュ済み）。"""
    spot = load_spot(slug)
    if not spot:
        raise HTTPException(status_code=404, detail="スポットが見つかりません")
    date_str = date or _tomorrow()
    lat = spot_lat(spot)
    lon = spot_lon(spot)
    weather = fetch_weather(lat, lon, date_str)
    marine = fetch_marine_weatherapi(lat, lon, date_str)
    if not marine:
        marine = fetch_marine(lat, lon, date_str)
    sst = fetch_sst_noaa(lat, lon, date_str)
    area = assign_area(spot)
    area_centers = get_area_centers()
    fetch_km = area_centers[area][2] if area in area_centers else 50
    result = score_spot(spot, weather, marine, sst_noaa=sst, fetch_km=fetch_km)
    return {
        "slug": slug,
        "date": date_str,
        "total": result["total"],
        "scores": result["scores"],
        "details": {k: v for k, v in result["details"].items() if not k.startswith("_")},
    }


@app.get("/api/spots/{slug}/tide")
def api_tide(slug: str, date: str | None = None):
    """スポットの潮汐データを返す。data/tides/ のキャッシュから読み込む。
    キャッシュが存在しない場合は tide: null を返す（エラーにしない）。
    潮汐データは scripts/fetch_tides.py の月次バッチで生成される。
    """
    from .tides import get_tide_data
    date_str = date or datetime.now(JST).strftime("%Y-%m-%d")
    data = get_tide_data(slug, date_str)
    if data is None:
        return {"tide": None, "tide_unavailable_reason": "data_not_fetched"}
    return data


# ============================================================
# ページルート
# ============================================================

@app.get("/", response_class=HTMLResponse)
def page_top(request: Request):
    spots = load_spots()
    prefs: dict = {}
    for s in spots:
        p_slug = s.get("area", {}).get("pref_slug", "")
        p_name = s.get("area", {}).get("prefecture", "")
        if p_slug:
            prefs.setdefault(p_slug, {"name": p_name, "count": 0})
            prefs[p_slug]["count"] += 1
    region_groups = []
    for r in REGIONS:
        region_prefs = {slug: prefs[slug] for slug in r["prefs"] if slug in prefs}
        if region_prefs:
            region_groups.append({
                "slug": r["slug"],
                "name": r["name"],
                "prefs": region_prefs,
            })
    area_counts: dict = {}
    for s in spots:
        a_slug = s.get("area", {}).get("area_slug", "")
        if a_slug:
            area_counts[a_slug] = area_counts.get(a_slug, 0) + 1
    recent_articles = _load_articles()[:6]
    return templates.TemplateResponse(request, "top.html", {
        "spots": spots,
        "tomorrow": _tomorrow(),
        "region_groups": region_groups,
        "area_counts": area_counts,
        "fish_list": [
            {"name": name, "slug": data["slug"]}
            for name, data in _FISH_MASTER.items()
            if data.get("slug")
            and any(data["slug"] in s.get("target_fish", []) for s in spots)
        ],
        "recent_articles": recent_articles,
    })


@app.get("/toilet/", response_class=HTMLResponse)
def page_toilet(request: Request):
    """トイレあり釣り場一覧ページ。"""
    spots = load_spots()
    toilet_spots = []
    for s in spots:
        slug = spot_slug(s)
        facilities = get_cached_facilities(slug) or []
        if any(f["type"] == "トイレ" for f in facilities):
            toilet_spots.append(s)

    # REGIONS 順に都道府県グループを構築
    pref_name_map: dict[str, str] = {}
    for s in spots:
        a = s.get("area", {})
        p_slug = a.get("pref_slug", "")
        p_name = a.get("prefecture", "")
        if p_slug and p_name:
            pref_name_map[p_slug] = p_name

    pref_groups: list[dict] = []
    seen: set = set()
    for r in REGIONS:
        for p_slug in r["prefs"]:
            if p_slug in seen:
                continue
            p_spots = [s for s in toilet_spots if s.get("area", {}).get("pref_slug") == p_slug]
            if p_spots:
                seen.add(p_slug)
                pref_groups.append({
                    "pref_slug": p_slug,
                    "pref_name": pref_name_map.get(p_slug, p_slug),
                    "spots": p_spots,
                })

    return templates.TemplateResponse(request, "toilet.html", {
        "pref_groups": pref_groups,
        "total": len(toilet_spots),
    })


@app.get("/method/", response_class=HTMLResponse)
def page_method_index(request: Request):
    methods = []
    for name, data in _METHOD_MASTER.items():
        # この釣法を使う魚種を逆引き
        target_fish = [fn for fn, fd in _FISH_MASTER.items() if name in fd.get("method", [])]
        methods.append({
            "name": name,
            "slug": data["slug"],
            "difficulty": data.get("difficulty", 1),
            "short_desc": data.get("short_desc", ""),
            "target_fish": target_fish,
        })
    return templates.TemplateResponse(request, "method_index.html", {
        "methods": methods,
        "total": len(methods),
    })


@app.get("/method/{method_slug}", response_class=HTMLResponse)
def page_method(request: Request, method_slug: str):
    method_name = _METHOD_SLUG_TO_NAME.get(method_slug)
    if not method_name:
        raise HTTPException(status_code=404, detail="釣法が見つかりません")
    data = _METHOD_MASTER[method_name]
    # この釣法を使う魚種を逆引き
    target_fish = [fn for fn, fd in _FISH_MASTER.items() if method_name in fd.get("method", [])]
    # この釣法で釣れるスポット数を計算
    method_fish_slugs = {
        v["slug"] for k, v in _FISH_MASTER.items()
        if method_name in v.get("method", []) and "slug" in v
    }
    all_spots = load_spots()
    method_spots_count = sum(
        1 for s in all_spots
        if any(f in method_fish_slugs for f in s.get("target_fish", []))
    )
    return templates.TemplateResponse(request, "method.html", {
        "method_name": method_name,
        "method_slug": method_slug,
        "data": data,
        "target_fish": target_fish,
        "method_spots_count": method_spots_count,
    })


@app.get("/fish/", response_class=HTMLResponse)
def page_fish_index(request: Request):
    all_spots = load_spots()
    fish_list = []
    for fish_name, fish_data in _FISH_MASTER.items():
        slug = fish_data.get("slug", "")
        count = sum(1 for s in all_spots if slug in s.get("target_fish", []))
        fish_list.append({
            "name": fish_name,
            "slug": fish_data.get("slug", ""),
            "count": count,
            "method": fish_data.get("method", []),
        })
    fish_list = [f for f in fish_list if f["count"] > 0]
    fish_list.sort(key=lambda x: x["count"], reverse=True)
    return templates.TemplateResponse(request, "fish_index.html", {
        "fish_list": fish_list,
        "total_fish": len(fish_list),
    })


@app.get("/fish/{fish_slug}", response_class=HTMLResponse)
def page_fish(request: Request, fish_slug: str):
    fish_name = _FISH_SLUG_TO_NAME.get(fish_slug)
    if not fish_name:
        raise HTTPException(status_code=404, detail="魚種が見つかりません")
    fish_data = _FISH_MASTER[fish_name]
    all_spots = load_spots()
    matched = [s for s in all_spots if fish_slug in s.get("target_fish", [])]
    # エリア別にグループ化
    areas: dict = {}
    for s in matched:
        a = s.get("area", {})
        key = a.get("area_slug", "")
        if key not in areas:
            areas[key] = {"name": a.get("area_name", ""), "spots": []}
        areas[key]["spots"].append(s)
    tackle_links = _get_tackle_for_methods(fish_data.get("method", []))
    return templates.TemplateResponse(request, "fish.html", {
        "fish_name": fish_name,
        "fish_slug": fish_slug,
        "fish_data": fish_data,
        "areas": areas,
        "total": len(matched),
        "tackle_links": tackle_links,
    })


SPOT_TYPE_LABELS = {
    "breakwater":       "堤防・防波堤",
    "rocky_shore":      "磯・岩場",
    "sand_beach":       "砂浜",
    "fishing_facility": "釣り公園・施設",
}

@app.get("/spots", response_class=HTMLResponse)
def page_spots(
    request: Request,
    area: str = Query(None),
    fish: str = Query(None),
    spot_type: str = Query(None, alias="type"),
    method: str = Query(None),
):
    all_spots = load_spots()
    fish_slug_map = {k: v["slug"] for k, v in _FISH_MASTER.items() if "slug" in v}
    fish_name_map = {v: k for k, v in fish_slug_map.items()}

    area_name = None
    if area:
        filtered = [s for s in all_spots if s.get("area", {}).get("area_slug") == area]
        if filtered:
            all_spots = filtered
            area_name = filtered[0]["area"]["area_name"]

    if fish and fish in fish_name_map:
        all_spots = [s for s in all_spots if fish in s.get("target_fish", [])]

    if spot_type and spot_type in SPOT_TYPE_LABELS:
        all_spots = [s for s in all_spots if s.get("classification", {}).get("primary_type") == spot_type]

    active_method_name = ""
    if method and method in _METHOD_SLUG_TO_NAME:
        active_method_name = _METHOD_SLUG_TO_NAME[method]
        method_fish_slugs = {
            v["slug"] for k, v in _FISH_MASTER.items()
            if active_method_name in v.get("method", []) and "slug" in v
        }
        all_spots = [s for s in all_spots
                     if any(f in method_fish_slugs for f in s.get("target_fish", []))]

    # 現在の絞り込み結果から魚種の出現頻度を集計（上位10件）
    from collections import Counter
    fish_counts = Counter(f for s in all_spots for f in s.get("target_fish", []))
    available_fish = [
        {"slug": slug, "name": fish_name_map.get(slug, slug), "count": cnt}
        for slug, cnt in fish_counts.most_common(10)
        if slug in fish_name_map
    ]

    return templates.TemplateResponse(request, "spots.html", {
        "spots": all_spots,
        "area_name": area_name,
        "active_area": area or "",
        "active_fish": fish or "",
        "active_type": spot_type or "",
        "active_method": method or "",
        "active_method_name": active_method_name,
        "available_fish": available_fish,
        "spot_type_labels": SPOT_TYPE_LABELS,
        "active_fish_name": fish_name_map.get(fish, "") if fish else "",
        "active_type_label": SPOT_TYPE_LABELS.get(spot_type, "") if spot_type else "",
        "fish_slug_map": fish_slug_map,
        "fish_name_map": fish_name_map,
    })


@app.get("/privacy", response_class=HTMLResponse)
def page_privacy(request: Request):
    return templates.TemplateResponse(request, "static_pages/privacy.html", {})

@app.get("/about", response_class=HTMLResponse)
def page_about(request: Request):
    return templates.TemplateResponse(request, "static_pages/about.html", {})

@app.get("/contact", response_class=HTMLResponse)
def page_contact(request: Request):
    return templates.TemplateResponse(request, "static_pages/contact.html", {})

@app.get("/safety", response_class=HTMLResponse)
def page_safety(request: Request):
    return templates.TemplateResponse(request, "static_pages/safety.html", {})


# ---- タックルガイド（アフィリエイト） ----------------------------------------

_TACKLE_DIR = _BASE / "data" / "tackle"
_ARTICLES_DIR = _BASE / "articles"

try:
    import mistune as _mistune
    _MARKDOWN = _mistune.create_markdown(plugins=["table"])
except ImportError:
    _MARKDOWN = None

_AFFILIATE_MARKER = _re.compile(r'<!--\s*affiliate:\s*(\d+)\s*-->')
_LINK_CARD_RE = _re.compile(r'<!--\s*link-card:\s*([^|>\n]+)\|([^|>\n]+)(?:\|([^>\n]*))?\s*-->')


def _build_link_card_html(url: str, title: str, desc: str = "") -> str:
    url = url.strip()
    title = title.strip()
    desc = desc.strip() if desc else ""
    desc_html = f'<p class="page-link-card-desc">{desc}</p>' if desc else ""
    return (
        f'<a class="page-link-card" href="{url}">'
        f'<p class="page-link-card-title">{title}</p>'
        f'{desc_html}'
        f'</a>'
    )


def _amazon_image_url(asin: str) -> str:
    """ASINからamazon.co.jpの商品画像URLを生成する（国コード09）。"""
    return f"https://images-na.ssl-images-amazon.com/images/P/{asin}.09.LZZZZZZZ.jpg"


def _build_affiliate_html(links: list) -> str:
    """商品カードHTMLを生成する。"""
    items_html = ""
    for link in links:
        name = link.get("name", "")
        url = link.get("url", "#")
        price = link.get("price", "")
        note = link.get("note", "")
        asin = (link.get("asin", "") or "").strip()
        price_html = f"<p class='tackle-affiliate-price'>{price}</p>" if price else ""
        note_html = f"<p class='tackle-affiliate-note'>{note}</p>" if note else ""
        image_html = (
            f'<img src="{_amazon_image_url(asin)}" alt="" loading="lazy" referrerpolicy="no-referrer">'
            if asin else ""
        )
        items_html += (
            f'<a href="{url}" target="_blank" rel="noopener sponsored" class="tackle-affiliate-item">'
            f'{image_html}'
            f'<div class="tackle-affiliate-info">'
            f'<p class="tackle-affiliate-name">{name}</p>'
            f'{price_html}{note_html}'
            f'</div></a>'
        )
    return f'<section class="tackle-affiliate"><div class="tackle-affiliate-list">{items_html}</div></section>'


def _render_tackle_body(category_slug: str, item: dict) -> tuple:
    """Markdownファイルがあればそれを基にHTMLを組み立てる。返り値: (body_html, from_md)"""
    md_path = _TACKLE_DIR / category_slug / f"{item['slug']}.md"
    if not md_path.exists() or _MARKDOWN is None:
        return item.get("body", "").replace("\n", "<br>"), False

    md_text = md_path.read_text(encoding="utf-8")
    slots = item.get("affiliate_slots", {}) or {}

    parts = _AFFILIATE_MARKER.split(md_text)
    html_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            html_parts.append(_MARKDOWN(part))
        else:
            if isinstance(slots, dict):
                links = slots.get(part, [])
            elif isinstance(slots, list):
                idx = int(part) - 1
                links = slots[idx] if 0 <= idx < len(slots) else []
            else:
                links = []
            if links:
                html_parts.append(_build_affiliate_html(links))
    return "".join(html_parts), True


def _load_tackle_categories() -> list:
    path = _TACKLE_DIR / "categories.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_tackle_items(category_slug: str) -> list:
    path = _TACKLE_DIR / f"{category_slug}.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 記事（Articles）ヘルパー ──────────────────────────────────

_H1_RE = _re.compile(r'^#\s+(.+)', _re.MULTILINE)

_CATEGORY_CARD: dict[str, str] = {
    "column": "fishing_master_card.png",
    "report": "reporter_card.png",
    "info":   "shop_girl_card.png",
}

_ARTICLE_CARD_DIR = _BASE / "static" / "img" / "articles"


def _article_card_image(category: str, slug: str) -> str:
    """記事専用サムネイルのパスを返す。
    static/img/articles/{category}/{slug}.jpg があればそれを使い、
    なければカテゴリ共通のフォールバック画像を返す。
    戻り値は templates で https://tsuricast.jp/static/img/{戻り値} と結合される。
    """
    custom = _ARTICLE_CARD_DIR / category / f"{slug}.jpg"
    if custom.exists():
        return f"articles/{category}/{slug}.jpg"
    return _CATEGORY_CARD.get(category, "fishing_master_card.png")


def _extract_article_meta(content: str, slug: str) -> tuple[dict, str]:
    """Markdownテキストからメタ情報と本文（フロントマター除去済み）を返す。"""
    meta: dict = {}
    body = content
    if content.startswith("---"):
        end = content.find("---", 3)
        if end > 0:
            current_key = None
            for line in content[3:end].splitlines():
                stripped = line.strip()
                if stripped.startswith("- "):
                    # YAML リスト要素
                    item = stripped[2:].strip()
                    if current_key is not None:
                        if isinstance(meta.get(current_key), list):
                            meta[current_key].append(item)
                        else:
                            meta[current_key] = [item]
                elif ":" in line and not line.startswith(" "):
                    k, v = line.split(":", 1)
                    current_key = k.strip()
                    val = v.strip()
                    meta[current_key] = val if val else None
            body = content[end + 3:].strip()
    if "title" not in meta:
        m = _H1_RE.search(body)
        if m:
            title = _re.sub(r'^[①-⑩\d]+[\s\.。、]+', '', m.group(1)).strip()
            meta["title"] = title
    meta.setdefault("slug", slug)
    return meta, body


def _load_articles() -> list:
    """articles/{category}/ 以下の全フォルダのメタ情報一覧を返す。"""
    result = []
    if not _ARTICLES_DIR.exists():
        return result
    for cat_dir in sorted(_ARTICLES_DIR.iterdir()):
        if not cat_dir.is_dir() or cat_dir.name.startswith("."):
            continue
        seen_slugs: set = set()
        for entry in sorted(cat_dir.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                # サブディレクトリ形式: {slug}/index.md or {slug}/*.md
                md_path = entry / "index.md"
                if not md_path.exists():
                    mds = sorted(entry.glob("*.md"))
                    if not mds:
                        continue
                    md_path = mds[0]
                slug = entry.name
            elif entry.is_file() and entry.suffix == ".md" and entry.name != "index.md":
                # フラットファイル形式: {slug}.md
                # サブディレクトリ版が存在する場合はスキップ
                if (cat_dir / entry.stem).is_dir():
                    continue
                md_path = entry
                slug = entry.stem
            else:
                continue
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            content = md_path.read_text(encoding="utf-8")
            meta, _ = _extract_article_meta(content, slug)
            meta["category"] = cat_dir.name
            meta["card_image"] = _article_card_image(cat_dir.name, slug)
            result.append(meta)
    return result


def _build_spot_article_index() -> dict[str, list]:
    """spot_slug → [article_meta, ...] の逆引き辞書を返す。"""
    index: dict[str, list] = {}
    for art in _load_articles():
        for s in art.get("related_spots") or []:
            index.setdefault(s, []).append(art)
    return index


_SPOT_ARTICLE_INDEX: dict[str, list] = _build_spot_article_index()


def _load_article_slots(category: str, slug: str) -> list:
    """articles/{category}/{slug}/affiliate.json を読み込んで返す。"""
    path = _ARTICLES_DIR / category / slug / "affiliate.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


_MD_LINK_RE = _re.compile(r'href="\./([\w-]+)\.md"')


def _render_md_with_affiliates(content: str, slots: list, article_path: str = "") -> str:
    """MarkdownをアフィリエイトスロットHTMLに展開してHTMLに変換する。"""
    if _MARKDOWN is None:
        return content

    # link-card マーカーをプレースホルダーに置換（Mistune処理前）
    link_card_map: dict[str, str] = {}

    def _replace_link_card(m: _re.Match) -> str:
        key = f"@@LINKCARD{len(link_card_map):04d}@@"
        link_card_map[key] = _build_link_card_html(m.group(1), m.group(2), m.group(3) or "")
        return key

    content = _LINK_CARD_RE.sub(_replace_link_card, content)

    parts = _AFFILIATE_MARKER.split(content)
    html_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            html_parts.append(_MARKDOWN(part))
        else:
            idx = int(part) - 1
            links = slots[idx] if isinstance(slots, list) and 0 <= idx < len(slots) else []
            if links:
                html_parts.append(_build_affiliate_html(links))
    html = "".join(html_parts)

    # プレースホルダーをリンクカードHTMLに戻す（<p>タグで包まれた場合も対応）
    for key, card_html in link_card_map.items():
        html = html.replace(f"<p>{key}</p>", card_html)
        html = html.replace(key, card_html)

    if article_path:
        html = _MD_LINK_RE.sub(lambda m: f'href="/articles/{article_path}/{m.group(1)}/"', html)
    return html


_ARTICLE_CATEGORY_LABELS: dict[str, str] = {
    "column": "店長コラム",
    "info":   "店員インフォメーション",
}
_ARTICLE_CATEGORY_ORDER = ["column", "info"]


@app.get("/articles/", response_class=HTMLResponse)
def page_articles_top(request: Request):
    all_articles = _load_articles()
    grouped: dict[str, list] = {cat: [] for cat in _ARTICLE_CATEGORY_ORDER}
    for a in all_articles:
        cat = a.get("category", "")
        if cat in grouped:
            grouped[cat].append(a)
    categories = [
        {"key": cat, "label": _ARTICLE_CATEGORY_LABELS[cat], "articles": grouped[cat]}
        for cat in _ARTICLE_CATEGORY_ORDER
    ]
    return templates.TemplateResponse(request, "articles/top.html", {
        "categories": categories,
    })


@app.get("/articles/{category}/{slug}/", response_class=HTMLResponse)
def page_article_detail(request: Request, category: str, slug: str):
    slug_dir = _ARTICLES_DIR / category / slug
    flat_md = _ARTICLES_DIR / category / f"{slug}.md"

    if slug_dir.is_dir():
        # サブディレクトリ形式
        md_path = slug_dir / "index.md"
        if not md_path.exists():
            mds = sorted(slug_dir.glob("*.md"))
            if not mds:
                raise HTTPException(status_code=404)
            md_path = mds[0]
        parts_paths = sorted(p for p in slug_dir.glob("*.md") if p.name != "index.md" and p != md_path)
    elif flat_md.exists():
        # フラットファイル形式
        md_path = flat_md
        parts_paths = []
    else:
        raise HTTPException(status_code=404, detail="記事が見つかりません")

    content = md_path.read_text(encoding="utf-8")
    meta, body = _extract_article_meta(content, slug)
    slots = _load_article_slots(category, slug)
    body_html = _render_md_with_affiliates(body, slots, article_path=f"{category}/{slug}")
    part_metas = []
    for p in parts_paths:
        pm, _ = _extract_article_meta(p.read_text(encoding="utf-8"), p.stem)
        pm["part_slug"] = p.stem
        part_metas.append(pm)
    card_image = _article_card_image(category, slug)
    return templates.TemplateResponse(request, "articles/detail.html", {
        "meta": meta,
        "body_html": body_html,
        "slug": slug,
        "category": category,
        "card_image": card_image,
        "parts": part_metas,
    })


@app.get("/articles/{category}/{slug}/{part_slug}/", response_class=HTMLResponse)
def page_article_part(request: Request, category: str, slug: str, part_slug: str):
    slug_dir = _ARTICLES_DIR / category / slug
    md_path = slug_dir / f"{part_slug}.md"
    if not md_path.exists():
        raise HTTPException(status_code=404)
    content = md_path.read_text(encoding="utf-8")
    meta, body = _extract_article_meta(content, slug)
    slots = _load_article_slots(category, slug)
    body_html = _render_md_with_affiliates(body, slots, article_path=f"{category}/{slug}")
    all_parts = sorted(p.stem for p in slug_dir.glob("*.md") if p.name != "index.md")
    idx = all_parts.index(part_slug) if part_slug in all_parts else -1
    prev_part = all_parts[idx - 1] if idx > 0 else None
    next_part = all_parts[idx + 1] if 0 <= idx < len(all_parts) - 1 else None
    card_image = _article_card_image(category, slug)
    return templates.TemplateResponse(request, "articles/part.html", {
        "meta": meta,
        "body_html": body_html,
        "slug": slug,
        "category": category,
        "card_image": card_image,
        "part_slug": part_slug,
        "prev_part": prev_part,
        "next_part": next_part,
    })


@app.get("/tackle/", response_class=HTMLResponse)
def page_tackle_top(request: Request):
    categories = _load_tackle_categories()
    return templates.TemplateResponse(request, "tackle/top.html", {
        "categories": categories,
    })


@app.get("/tackle/{category_slug}/", response_class=HTMLResponse)
def page_tackle_category(request: Request, category_slug: str):
    categories = _load_tackle_categories()
    category = next((c for c in categories if c["slug"] == category_slug), None)
    if not category:
        raise HTTPException(status_code=404, detail="カテゴリが見つかりません")
    items = _load_tackle_items(category_slug)
    return templates.TemplateResponse(request, "tackle/category.html", {
        "category": category,
        "categories": categories,
        "items": items,
    })


@app.get("/tackle/{category_slug}/{item_slug}/", response_class=HTMLResponse)
def page_tackle_item(request: Request, category_slug: str, item_slug: str):
    categories = _load_tackle_categories()
    category = next((c for c in categories if c["slug"] == category_slug), None)
    if not category:
        raise HTTPException(status_code=404, detail="カテゴリが見つかりません")
    items = _load_tackle_items(category_slug)
    item = next((i for i in items if i["slug"] == item_slug), None)
    if not item:
        raise HTTPException(status_code=404, detail="アイテムが見つかりません")
    body_html, body_from_md = _render_tackle_body(category_slug, item)
    return templates.TemplateResponse(request, "tackle/item.html", {
        "category": category,
        "categories": categories,
        "item": item,
        "items": items,
        "body_html": body_html,
        "body_from_md": body_from_md,
    })


@app.get("/area/", response_class=HTMLResponse)
def page_area_index(request: Request):
    spots = load_spots()
    prefs: dict = {}
    for s in spots:
        p_slug = s.get("area", {}).get("pref_slug", "")
        p_name = s.get("area", {}).get("prefecture", "")
        if p_slug:
            prefs.setdefault(p_slug, {"name": p_name, "count": 0})
            prefs[p_slug]["count"] += 1
    region_groups = []
    for r in REGIONS:
        region_prefs = {slug: prefs[slug] for slug in r["prefs"] if slug in prefs}
        if region_prefs:
            region_groups.append({
                "slug": r["slug"],
                "name": r["name"],
                "prefs": region_prefs,
            })
    return templates.TemplateResponse(request, "area_index.html", {
        "region_groups": region_groups,
    })


@app.get("/{slug}/", response_class=HTMLResponse)
def page_pref_or_region(request: Request, slug: str):
    # 地方ページ
    if slug in VALID_REGION_SLUGS:
        region = next(r for r in REGIONS if r["slug"] == slug)
        all_spots = load_spots()
        prefs: dict = {}
        for s in all_spots:
            p_slug = s.get("area", {}).get("pref_slug", "")
            p_name = s.get("area", {}).get("prefecture", "")
            if p_slug in region["prefs"]:
                prefs.setdefault(p_slug, {"name": p_name, "count": 0})
                prefs[p_slug]["count"] += 1
        if not prefs:
            raise HTTPException(status_code=404)
        return templates.TemplateResponse(request, "region.html", {
            "region_slug": slug,
            "region_name": region["name"],
            "prefs": prefs,
        })

    # 都道府県ページ
    all_spots = load_spots()
    spots = [s for s in all_spots if s.get("area", {}).get("pref_slug") == slug]
    if not spots:
        raise HTTPException(status_code=404)
    pref_slug = slug
    pref_name = spots[0]["area"]["prefecture"]
    region_slug = PREF_TO_REGION.get(pref_slug, "")
    region_name = REGION_NAMES.get(region_slug, "")
    areas: dict = {}
    for s in spots:
        a_slug = s["area"]["area_slug"]
        a_name = s["area"]["area_name"]
        areas.setdefault(a_slug, {"name": a_name, "count": 0})
        areas[a_slug]["count"] += 1
    cities: dict = {}
    for s in spots:
        c_slug = s["area"].get("city_slug", "")
        c_name = s["area"].get("city", "")
        a_slug = s["area"].get("area_slug", "")
        if c_slug:
            cities.setdefault(c_slug, {"name": c_name, "count": 0, "area_slug": a_slug})
            cities[c_slug]["count"] += 1
    return templates.TemplateResponse(request, "pref.html", {
        "pref_slug": pref_slug,
        "pref_name": pref_name,
        "region_slug": region_slug,
        "region_name": region_name,
        "areas": areas,
        "cities": cities,
        "spots": spots,
    })


@app.get("/{pref_slug}/{area_slug}/", response_class=HTMLResponse)
def page_area(request: Request, pref_slug: str, area_slug: str):
    all_spots = load_spots()
    spots = [s for s in all_spots
             if s.get("area", {}).get("pref_slug") == pref_slug
             and s.get("area", {}).get("area_slug") == area_slug]
    if not spots:
        raise HTTPException(status_code=404)
    pref_name = spots[0]["area"]["prefecture"]
    area_name = spots[0]["area"]["area_name"]
    region_slug = PREF_TO_REGION.get(pref_slug, "")
    region_name = REGION_NAMES.get(region_slug, "")
    cities: dict = {}
    for s in spots:
        c_slug = s["area"]["city_slug"]
        c_name = s["area"]["city"]
        if not c_slug:
            continue
        cities.setdefault(c_slug, {"name": c_name, "count": 0})
        cities[c_slug]["count"] += 1
    return templates.TemplateResponse(request, "area.html", {
        "pref_slug": pref_slug,
        "area_slug": area_slug,
        "pref_name": pref_name,
        "area_name": area_name,
        "region_slug": region_slug,
        "region_name": region_name,
        "cities": cities,
        "spots": spots,
    })


@app.get("/{pref_slug}/{area_slug}/{city_slug}/", response_class=HTMLResponse)
def page_city(request: Request, pref_slug: str, area_slug: str, city_slug: str):
    all_spots = load_spots()
    spots = [s for s in all_spots
             if s.get("area", {}).get("pref_slug") == pref_slug
             and s.get("area", {}).get("area_slug") == area_slug
             and s.get("area", {}).get("city_slug") == city_slug]
    if not spots:
        raise HTTPException(status_code=404)
    pref_name = spots[0]["area"]["prefecture"]
    area_name = spots[0]["area"]["area_name"]
    city_name = spots[0]["area"]["city"]
    region_slug = PREF_TO_REGION.get(pref_slug, "")
    region_name = REGION_NAMES.get(region_slug, "")
    fish_slug_map = {k: v["slug"] for k, v in _FISH_MASTER.items() if "slug" in v}
    fish_name_map = {v: k for k, v in fish_slug_map.items()}
    spot_descriptions = {
        s["slug"]: (
            (s.get("info") or {}).get("description")
            or _build_spot_description(s, fish_name_map)
        )
        for s in spots
    }
    return templates.TemplateResponse(request, "city.html", {
        "pref_slug": pref_slug,
        "area_slug": area_slug,
        "city_slug": city_slug,
        "pref_name": pref_name,
        "area_name": area_name,
        "city_name": city_name,
        "region_slug": region_slug,
        "region_name": region_name,
        "spots": spots,
        "fish_slug_map": fish_slug_map,
        "fish_name_map": fish_name_map,
        "spot_descriptions": spot_descriptions,
    })


def _truncate_meta(text: str, limit: int = 130) -> str:
    """文章の区切りを探して limit 字以内に収め、超える場合は … を付ける。"""
    if len(text) <= limit:
        return text
    # limit 字以内の最後の句点・感嘆符・疑問符を探す（直近 40 字の範囲）
    sub = text[:limit]
    for i in range(len(sub) - 1, max(len(sub) - 40, 0) - 1, -1):
        if sub[i] in "。！？":
            return sub[:i + 1] + "…"
    # 見つからなければ limit 字で切って … を付ける
    return sub + "…"


def _build_spot_description(spot: dict, fish_name_map: dict) -> str:
    """スポットの既存データから100〜200字の説明文を動的生成する。"""
    area  = spot.get("area", {})
    pref  = area.get("prefecture", "")
    city  = area.get("city", "")
    name  = spot.get("name", "")
    stype = spot_type_label(spot)
    label = getattr(stype, "label", None) if stype else None

    intro = (
        f"{pref}{city}の{name}は、{label}の釣り場です。"
        if label else f"{pref}{city}に位置する{name}の釣り場です。"
    )

    fish = [fish_name_map.get(f, f) for f in spot.get("target_fish", [])[:5]]
    fish_str = f"{'・'.join(fish)}が狙えます。" if fish else ""

    seabed = (spot.get("derived_features") or {}).get("seabed_summary", "")
    slope  = spot_slope_type(spot)
    terrain_str = ""
    if seabed:
        terrain_str = f"底質は{seabed}"
        if slope and slope != "不明":
            terrain_str += f"、地形は{slope}タイプ"
        terrain_str += "。"

    notes = (spot.get("info") or {}).get("notes", "")
    notes_str = notes if notes and len(notes) > 10 else ""

    return intro + fish_str + terrain_str + notes_str


@app.get("/{pref_slug}/{area_slug}/{city_slug}/{slug}", response_class=HTMLResponse)
def page_spot_detail(
    request: Request,
    pref_slug: str,
    area_slug: str,
    city_slug: str,
    slug: str,
):
    spot = load_spot(slug)
    if not spot:
        raise HTTPException(status_code=404, detail="スポットが見つかりません")
    today_str    = _today()
    tomorrow_str = _tomorrow()
    region_slug = PREF_TO_REGION.get(pref_slug, "")
    region_name = REGION_NAMES.get(region_slug, "")
    fish_slug_map = {k: v["slug"] for k, v in _FISH_MASTER.items() if "slug" in v}
    fish_name_map = {v: k for k, v in fish_slug_map.items()}
    fish_names_jp = [fish_name_map.get(s, s) for s in spot.get("target_fish", [])[:3]]
    # スポットの対象魚種から釣法を収集してタックルリストを生成
    spot_methods: list = []
    for fish_s in spot.get("target_fish", []):
        fish_n = fish_name_map.get(fish_s)
        if fish_n and fish_n in _FISH_MASTER:
            for m in _FISH_MASTER[fish_n].get("method", []):
                if m not in spot_methods:
                    spot_methods.append(m)
    tackle_links = _get_tackle_for_methods(spot_methods)
    cached_facilities = get_cached_facilities(slug) or []
    facility_types = {f["type"] for f in cached_facilities}
    facility_flags = {
        "parking":     "駐車場" in facility_types,
        "toilet":      "トイレ" in facility_types,
        "convenience": "コンビニ" in facility_types,
    }
    try:
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
            preloaded_forecast = _ex.submit(_compute_forecast, spot).result(timeout=8)
    except Exception as e:
        print(f"[警告] _compute_forecast スキップ ({slug}): {e}")
        preloaded_forecast = None
    return templates.TemplateResponse(request, "spot.html", {
        "spot":               spot,
        "today_jp":           _format_date_jp(today_str),
        "tomorrow_jp":        _format_date_jp(tomorrow_str),
        "slope_type":         spot_slope_type(spot),
        "spot_type":          spot_type_label(spot),
        "photos":             get_photos(slug),
        "preloaded_forecast": preloaded_forecast,
        "region_slug":        region_slug,
        "region_name":        region_name,
        "fish_slug_map":      fish_slug_map,
        "fish_name_map":      fish_name_map,
        "fish_names_jp":      fish_names_jp,
        "facility_flags":     facility_flags,
        "tackle_links":       tackle_links,
        "spot_description":   (spot.get("info") or {}).get("description") or (spot.get("info") or {}).get("lead_text") or _build_spot_description(spot, fish_name_map),
        "meta_description":   _truncate_meta((spot.get("info") or {}).get("description") or (spot.get("info") or {}).get("lead_text") or _build_spot_description(spot, fish_name_map)),
        "related_articles":   _SPOT_ARTICLE_INDEX.get(slug, []),
    })
