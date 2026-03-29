"""
FastAPI アプリケーション。
uvicorn app.main:app --reload で起動。
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from email.utils import formatdate

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request

from .spots import load_spots, load_spot, spot_lat, spot_lon, spot_name, spot_slug
from .spots import spot_area, spot_area_name, spot_bearing, spot_kisugo, spot_terrain, spot_slope_type, assign_area, get_area_centers, get_photos
from .weather import (fetch_weather, fetch_weather_range,
                       fetch_marine_weatherapi, fetch_marine, fetch_marine_range,
                       fetch_sst_noaa)
from .scoring import score_spot, direction_label, score_7days
from .osm import fetch_nearby_facilities

JST = timezone(timedelta(hours=9))

# ============================================================
# インメモリ TTL キャッシュ
# ============================================================

_ranking_cache: dict = {}       # date_str → (timestamp, scored_spots)
_RANKING_TTL = 6 * 3600         # 6時間
_refresh_in_progress: set = set()  # 二重更新防止


def _score_one_spot(spot: dict, date_str: str, area_centers: dict) -> dict:
    """スポット1件分の気象取得・スコア計算を行う（スレッド内で実行）。"""
    lat = spot_lat(spot)
    lon = spot_lon(spot)
    weather = fetch_weather(lat, lon, date_str)
    marine = fetch_marine_weatherapi(lat, lon, date_str)
    if not marine:
        marine = fetch_marine(lat, lon, date_str)
    area = assign_area(spot)
    fetch_km = area_centers[area][2] if area in area_centers else 50
    sst = fetch_sst_noaa(lat, lon, date_str)
    return score_spot(spot, weather, marine, sst_noaa=sst, fetch_km=fetch_km)


def _compute_ranking(date_str: str) -> list[dict]:
    """全スポットのスコアを並列計算して降順に返す。"""
    spots = load_spots()
    area_centers = get_area_centers()
    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {
            executor.submit(_score_one_spot, spot, date_str, area_centers): spot
            for spot in spots
        }
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as e:
                print(f"[警告] スコア計算失敗 ({futures[future].get('slug', '?')}): {e}")
    return sorted(results, key=lambda x: x["total"], reverse=True)


def _trigger_background_refresh(date_str: str) -> None:
    """ランキングをバックグラウンドスレッドで再計算する。二重起動を防ぐ。"""
    if date_str in _refresh_in_progress:
        return
    _refresh_in_progress.add(date_str)

    def _do() -> None:
        try:
            data = _compute_ranking(date_str)
            _ranking_cache[date_str] = (time.time(), data)
            print(f"[更新] ランキングキャッシュ更新完了 ({date_str})")
        finally:
            _refresh_in_progress.discard(date_str)

    threading.Thread(target=_do, daemon=True).start()


def get_ranking(date_str: str) -> list[dict]:
    """Stale-While-Revalidate キャッシュ付きランキング取得。
    キャッシュが新鮮なら即返す。期限切れなら古いデータを即返しつつバックグラウンドで更新。
    キャッシュなし（コールドスタート）のみ同期計算する。
    """
    now = time.time()
    if date_str in _ranking_cache:
        ts, data = _ranking_cache[date_str]
        if now - ts < _RANKING_TTL:
            return data                        # 新鮮: そのまま即返す
        # 期限切れ: 古いデータを即返しバックグラウンドで更新
        _trigger_background_refresh(date_str)
        return data
    # キャッシュなし（コールドスタート時のみ）: 同期計算
    data = _compute_ranking(date_str)
    _ranking_cache[date_str] = (now, data)
    return data


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
    # 起動時に spots をプリロード
    load_spots()
    # ランキングキャッシュをバックグラウンドで事前計算
    import asyncio
    asyncio.create_task(_warm_ranking_cache())
    yield


async def _warm_ranking_cache():
    """サーバー起動後にランキングを事前計算してキャッシュを温める。
    同期HTTPコールをスレッドプールで実行し、イベントループをブロックしない。
    """
    import asyncio
    await asyncio.sleep(3)  # サーバーが完全に起動するまで待機
    try:
        date_str = _tomorrow()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, get_ranking, date_str)
        print(f"[起動] ランキングキャッシュを事前計算しました ({date_str})")
    except Exception as e:
        print(f"[起動] ランキングキャッシュの事前計算に失敗: {e}")


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

User-agent: Claude-Web
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
    for s in ("safety", "privacy", "about", "contact"):
        urls.append((f"{_BASE_URL}/{s}", "monthly", "0.4"))

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


@app.get("/api/ranking")
def api_ranking(date: str | None = None):
    """全スポットのランキング JSON を返す。date 未指定は翌日。"""
    date_str = date or _tomorrow()
    ranked = get_ranking(date_str)
    cache_ts = _ranking_cache.get(date_str, (0, None))[0]
    payload = {
        "date": date_str,
        "updated_at": datetime.fromtimestamp(cache_ts, tz=JST).isoformat() if cache_ts else None,
        "spots": [
            {
                "rank": i + 1,
                "slug": spot_slug(r["spot"]),
                "name": spot_name(r["spot"]),
                "total": r["total"],
                "scores": r["scores"],
                "details": {
                    k: v for k, v in r["details"].items()
                    if not k.startswith("_")
                },
            }
            for i, r in enumerate(ranked)
        ],
    }
    response = JSONResponse(content=payload)
    response.headers["Cache-Control"] = "public, max-age=1800, stale-while-revalidate=3600"
    return response


@app.get("/api/forecast/{slug}")
def api_forecast(slug: str):
    """スポットの7日分・4区分予報を返す。"""
    spot = load_spot(slug)
    if not spot:
        raise HTTPException(status_code=404, detail="スポットが見つかりません")

    from datetime import date, timedelta
    today = date.today()
    start = today.strftime("%Y-%m-%d")            # 今日から取得（days[0]=今日）
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
    today_data = all_days[0] if all_days else None   # days[0] = 今日
    forecast   = all_days[1:]                        # days[1:] = 明日+6日
    return {"slug": slug, "days": forecast, "today": today_data}


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
    """スポット周辺の OSM 施設（駐車場・トイレ等）を返す。"""
    spot = load_spot(slug)
    if not spot:
        raise HTTPException(status_code=404, detail="スポットが見つかりません")
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
    _PREF_ORDER = ["chiba", "tokyo", "kanagawa"]
    prefs = {k: prefs[k] for k in _PREF_ORDER if k in prefs}
    return templates.TemplateResponse(request, "top.html", {
        "spots": spots,
        "tomorrow": _tomorrow(),
        "prefs": prefs,
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
    return templates.TemplateResponse(request, "spots.html", {
        "spots": all_spots,
        "area_name": area_name,
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


@app.get("/{pref_slug}/", response_class=HTMLResponse)
def page_pref(request: Request, pref_slug: str):
    all_spots = load_spots()
    spots = [s for s in all_spots if s.get("area", {}).get("pref_slug") == pref_slug]
    if not spots:
        raise HTTPException(status_code=404)
    pref_name = spots[0]["area"]["prefecture"]
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
    return templates.TemplateResponse(request, "city.html", {
        "pref_slug": pref_slug,
        "area_slug": area_slug,
        "city_slug": city_slug,
        "pref_name": pref_name,
        "area_name": area_name,
        "city_name": city_name,
        "spots": spots,
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
    return templates.TemplateResponse(request, "spot.html", {
        "spot":         spot,
        "today_jp":     _format_date_jp(today_str),
        "tomorrow_jp":  _format_date_jp(tomorrow_str),
        "slope_type":   spot_slope_type(spot),
        "photos":       get_photos(slug),
    })
