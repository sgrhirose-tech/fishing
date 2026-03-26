"""
FastAPI アプリケーション。
uvicorn app.main:app --reload で起動。
"""

import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request

from .spots import load_spots, load_spot, spot_lat, spot_lon, spot_name, spot_slug
from .spots import spot_area, spot_area_name, spot_bearing, spot_kisugo, spot_terrain, assign_area, get_area_centers
from .weather import fetch_weather, fetch_marine_weatherapi, fetch_marine, fetch_sst_noaa
from .scoring import score_spot, direction_label
from .osm import fetch_nearby_facilities

JST = timezone(timedelta(hours=9))

# ============================================================
# インメモリ TTL キャッシュ
# ============================================================

_ranking_cache: dict = {}   # date_str → (timestamp, scored_spots)
_RANKING_TTL = 6 * 3600     # 6時間


def _compute_ranking(date_str: str) -> list[dict]:
    """全スポットのスコアを計算して降順に返す。"""
    spots = load_spots()
    area_centers = get_area_centers()
    results = []
    for spot in spots:
        lat = spot_lat(spot)
        lon = spot_lon(spot)
        weather = fetch_weather(lat, lon, date_str)
        marine = fetch_marine_weatherapi(lat, lon, date_str)
        if not marine:
            marine = fetch_marine(lat, lon, date_str)
        area = assign_area(spot)
        fetch_km = area_centers[area][2] if area in area_centers else 50
        sst = fetch_sst_noaa(lat, lon, date_str)
        result = score_spot(spot, weather, marine, sst_noaa=sst, fetch_km=fetch_km)
        results.append(result)
    return sorted(results, key=lambda x: x["total"], reverse=True)


def get_ranking(date_str: str) -> list[dict]:
    """TTL キャッシュ付きランキング取得。"""
    now = time.time()
    if date_str in _ranking_cache:
        ts, data = _ranking_cache[date_str]
        if now - ts < _RANKING_TTL:
            return data
    data = _compute_ranking(date_str)
    _ranking_cache[date_str] = (now, data)
    return data


def _tomorrow() -> str:
    return (datetime.now(JST) + timedelta(days=1)).strftime("%Y-%m-%d")


# ============================================================
# FastAPI アプリ
# ============================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 起動時に spots をプリロード
    load_spots()
    yield


app = FastAPI(title="シロギス釣り場ガイド", lifespan=lifespan)

# 静的ファイルとテンプレート
import pathlib
_BASE = pathlib.Path(__file__).parent.parent

app.mount("/static", StaticFiles(directory=str(_BASE / "static")), name="static")
templates = Jinja2Templates(directory=str(_BASE / "templates"))

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
    return {
        "date": date_str,
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
    return templates.TemplateResponse(request, "top.html", {
        "spots": spots,
        "tomorrow": _tomorrow(),
    })


@app.get("/spots", response_class=HTMLResponse)
def page_spots(request: Request):
    spots = load_spots()
    return templates.TemplateResponse(request, "spots.html", {
        "spots": spots,
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
    return templates.TemplateResponse(request, "pref.html", {
        "pref_slug": pref_slug,
        "pref_name": pref_name,
        "areas": areas,
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
    return templates.TemplateResponse(request, "spot.html", {
        "spot": spot,
        "tomorrow": _tomorrow(),
    })
