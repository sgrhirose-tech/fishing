#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mac用 TSV一括JSON作成ツール

tsv/ フォルダにある TSV ファイルを一括処理し、
unadjusted/ フォルダに釣りスポット JSON を書き出す。

TSV フォーマット（ヘッダなし・タブ区切り・6〜7列）:
  name  lat  lon  slug  notes  access  [area]

  第7列 area は任意。日本語エリア名（例: 外房）を指定すると自動判定を上書きする。

エリア・都道府県・市区町村は緯度経度から自動導出する。
審査・手修正後に spots/ へ移動して本番反映すること。

使い方:
  python tools/mac_batch_from_tsv.py
"""

import json
import math
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ── tools/ ディレクトリにある pythonista_spot_tools から API 関数を再利用 ──
sys.path.insert(0, str(Path(__file__).parent))
from pythonista_spot_tools import fetch_physical_data, build_spot_json

# ──────────────────────────────────────────
# 定数
# ──────────────────────────────────────────

REPO_ROOT  = Path(__file__).parent.parent
TSV_DIR    = REPO_ROOT / "tsv"
OUTPUT_DIR = REPO_ROOT / "unadjusted"
AREAS_FILE = REPO_ROOT / "spots" / "_marine_areas.json"

USER_AGENT = "ShirogisuSpotBuilder/1.0 (personal-use; Mac)"

# macOS の python.org 版 Python は証明書バンドルを自動参照しないため
# 個人用ツールとして SSL 検証を無効化して確実に接続できるようにする
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# area_name → (area_slug, pref_slug_fallback, prefecture_fallback)
# pref_slug_fallback は Nominatim が失敗した場合のみ使用する
AREA_MAP = {
    "相模湾":   ("sagamibay", "kanagawa", "神奈川県"),
    "三浦半島": ("miura",     "kanagawa", "神奈川県"),
    "東京湾":   ("tokyobay",  "kanagawa", "神奈川県"),
    "内房":     ("uchibo",    "chiba",    "千葉県"),
    "外房":     ("sotobo",    "chiba",    "千葉県"),
    "九十九里": ("kujukuri",  "chiba",    "千葉県"),
}

# 都道府県名 → pref_slug（Nominatim の実際の都道府県から導出）
PREF_SLUG_MAP = {
    "神奈川県": "kanagawa",
    "東京都":   "tokyo",
    "千葉県":   "chiba",
}


# ──────────────────────────────────────────
# TSV パーサー
# ──────────────────────────────────────────

def parse_tsv_file(path: Path) -> list:
    """
    TSV を読み込んで list[dict] を返す。
    列順: name, lat, lon, slug, notes, access（ヘッダなし）
    BOM 付き UTF-8 / 空行 / # コメント行はスキップ。
    """
    text = path.read_text(encoding="utf-8-sig")
    records = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        cols = stripped.split("\t")
        if len(cols) < 4:
            print(f"  [スキップ] 行{lineno}: 列数不足 ({len(cols)}列) — {stripped[:60]!r}")
            continue
        try:
            lat = float(cols[1])
            lon = float(cols[2])
        except ValueError:
            print(f"  [スキップ] 行{lineno}: lat/lon が数値でない — {cols[1]!r}, {cols[2]!r}")
            continue
        records.append({
            "name":   cols[0].strip(),
            "lat":    lat,
            "lon":    lon,
            "slug":   cols[3].strip() if len(cols) > 3 else f"spot_{lineno:03d}",
            "notes":  cols[4].strip() if len(cols) > 4 else "",
            "access": cols[5].strip() if len(cols) > 5 else "",
            "area":   cols[6].strip() if len(cols) > 6 else "",  # 任意: エリア名上書き
        })
    return records


# ──────────────────────────────────────────
# エリア自動判定
# ──────────────────────────────────────────

def assign_area(lat: float, lon: float) -> str:
    """
    _marine_areas.json のバウンディングボックスで候補エリアに絞り込んだ上で、
    center_lat/center_lon への距離が最小のエリア名を返す。
    BBox 定義がない or 全エリア外の場合は全エリアでフォールバック。
    """
    try:
        data = json.loads(AREAS_FILE.read_text(encoding="utf-8"))
        areas = data.get("areas", {})
    except Exception as e:
        print(f"  [警告] _marine_areas.json 読み込み失敗: {e}")
        return "不明"

    # Step 1: BBox内に入るエリアに絞る
    candidates = {
        name: info for name, info in areas.items()
        if (info.get("lat_min", -90) <= lat <= info.get("lat_max", 90) and
            info.get("lon_min", -180) <= lon <= info.get("lon_max", 180))
    }
    if not candidates:
        candidates = areas  # 全外れの場合は全エリアでフォールバック

    # Step 2: 候補内で center_lat/lon への距離が最小を選ぶ
    best_name = "不明"
    best_dist = float("inf")
    for name, info in candidates.items():
        dlat = lat - info.get("center_lat", 0)
        dlon = lon - info.get("center_lon", 0)
        dist = math.sqrt(dlat * dlat + dlon * dlon)
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


# ──────────────────────────────────────────
# Nominatim 住所取得
# ──────────────────────────────────────────

def reverse_geocode(lat: float, lon: float) -> dict:
    """
    Nominatim reverse geocode で prefecture / city を返す。
    失敗時は空文字で返す。
    """
    params = {
        "lat": lat,
        "lon": lon,
        "format": "jsonv2",
        "addressdetails": 1,
        "accept-language": "ja,en",
        "zoom": 14,
    }
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
            addr = json.loads(resp.read().decode("utf-8")).get("address", {})
        prefecture = (
            addr.get("state") or addr.get("province") or addr.get("region") or ""
        )
        city = (
            addr.get("city") or addr.get("town") or
            addr.get("village") or addr.get("municipality") or
            addr.get("county") or ""
        )
        return {"prefecture": prefecture, "city": city}
    except Exception as e:
        print(f"  [警告] Nominatim 取得失敗: {e}")
        return {"prefecture": "", "city": ""}


# ──────────────────────────────────────────
# 1 レコードを処理してスポット dict を返す
# ──────────────────────────────────────────

def process_record(rec: dict, idx: int, total: int) -> dict:
    name   = rec["name"]
    lat    = rec["lat"]
    lon    = rec["lon"]
    slug   = rec["slug"]
    notes  = rec["notes"]
    access = rec["access"]

    print(f"\n  [{idx}/{total}] {name} ({slug})  lat={lat} lon={lon}")

    # エリア自動判定（TSV第7列で上書き可能）
    area_name = rec.get("area") or assign_area(lat, lon)
    area_slug, pref_slug, prefecture = AREA_MAP.get(
        area_name, ("unknown", "unknown", "")
    )
    print(f"    エリア自動判定: {area_name} ({area_slug})")

    # 住所取得（Nominatim）
    print("    住所取得 (Nominatim)...", end=" ", flush=True)
    geo = reverse_geocode(lat, lon)
    if not geo["prefecture"] and prefecture:
        geo["prefecture"] = prefecture
    print(f"→ {geo['prefecture']} {geo['city']}")
    time.sleep(1.1)  # Nominatim レート制限対策

    # Nominatim の実際の都道府県から pref_slug を導出（AREA_MAP のフォールバックより優先）
    actual_pref = geo["prefecture"] or prefecture
    actual_pref_slug = PREF_SLUG_MAP.get(actual_pref, pref_slug)

    # 物理データ取得（OSM + 海しる）
    phys = fetch_physical_data(lat, lon)
    if phys is None:
        print("    [警告] 物理データ API 取得失敗 — 空値で作成")
        phys = {
            "sea_bearing_deg": None,
            "seabed_type": "unknown",
            "nearest_20m_contour_distance_m": None,
            "bottom_kisugo_score": 50,
            "terrain_summary": "地形情報不足",
        }

    return build_spot_json(
        slug, name, lat, lon,
        area_name, area_slug,
        actual_pref,
        actual_pref_slug,
        geo["city"], "",          # city_slug は空文字
        phys, notes, access,
    )


# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────

def main():
    TSV_DIR.mkdir(exist_ok=True)
    OUTPUT_DIR.mkdir(exist_ok=True)

    tsv_files = sorted(TSV_DIR.glob("*.tsv"))
    if not tsv_files:
        print(f"TSV ファイルが見つかりません: {TSV_DIR}")
        print("tsv/ フォルダに .tsv ファイルを置いて再実行してください。")
        return

    total_success, total_failed = [], []

    for tsv_path in tsv_files:
        records = parse_tsv_file(tsv_path)
        print(f"\n処理中: {tsv_path.name}  ({len(records)}件)")

        for idx, rec in enumerate(records, 1):
            try:
                spot = process_record(rec, idx, len(records))
                out_path = OUTPUT_DIR / f"{rec['slug']}.json"
                out_path.write_text(
                    json.dumps(spot, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(f"    → {out_path.name}")
                total_success.append(rec["slug"])
            except Exception as e:
                print(f"    [エラー] {rec.get('slug', '?')}: {e}")
                total_failed.append((rec.get("slug", "?"), str(e)))

    print(f"\n── 完了 ──")
    print(f"成功: {len(total_success)}件 / 失敗: {len(total_failed)}件")
    if total_failed:
        print("失敗詳細:")
        for slug, reason in total_failed:
            print(f"  {slug}: {reason}")
    if total_success:
        print(f"\n出力先: {OUTPUT_DIR}")
        print("確認・手修正後に spots/ へコピーして本番反映してください。")


if __name__ == "__main__":
    main()
