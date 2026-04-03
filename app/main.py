"""
FastAPI アプリケーション。
uvicorn app.main:app --reload で起動。
"""

import json
import os
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
                       fetch_sst_noaa)
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
    return {"slug": slug, "days": forecast, "today": today_data}


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
    return templates.TemplateResponse(request, "method.html", {
        "method_name": method_name,
        "method_slug": method_slug,
        "data": data,
        "target_fish": target_fish,
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
    return templates.TemplateResponse(request, "fish.html", {
        "fish_name": fish_name,
        "fish_slug": fish_slug,
        "fish_data": fish_data,
        "areas": areas,
        "total": len(matched),
    })


@app.get("/spots", response_class=HTMLResponse)
def page_spots(request: Request, area: str = Query(None)):
    all_spots = load_spots()
    area_name = None
    if area:
        filtered = [s for s in all_spots if s.get("area", {}).get("area_slug") == area]
        if filtered:
            all_spots = filtered
            area_name = filtered[0]["area"]["area_name"]
    fish_slug_map = {k: v["slug"] for k, v in _FISH_MASTER.items() if "slug" in v}
    fish_name_map = {v: k for k, v in fish_slug_map.items()}
    return templates.TemplateResponse(request, "spots.html", {
        "spots": all_spots,
        "area_name": area_name,
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
    })


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
    cached_facilities = get_cached_facilities(slug) or []
    facility_types = {f["type"] for f in cached_facilities}
    facility_flags = {
        "parking":     "駐車場" in facility_types,
        "toilet":      "トイレ" in facility_types,
        "convenience": "コンビニ" in facility_types,
    }
    return templates.TemplateResponse(request, "spot.html", {
        "spot":               spot,
        "today_jp":           _format_date_jp(today_str),
        "tomorrow_jp":        _format_date_jp(tomorrow_str),
        "slope_type":         spot_slope_type(spot),
        "spot_type":          spot_type_label(spot),
        "photos":             get_photos(slug),
        "preloaded_forecast": _compute_forecast(spot),
        "region_slug":        region_slug,
        "region_name":        region_name,
        "fish_slug_map":      fish_slug_map,
        "fish_name_map":      fish_name_map,
        "fish_names_jp":      fish_names_jp,
        "facility_flags":     facility_flags,
    })
