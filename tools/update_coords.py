"""
[廃止予定] このスクリプトは tools/build_spots.py に統合されました。

既存 spots/ の座標だけを再補正したい場合にのみ使用してください。
新規スポット登録は tools/build_spots.py を使ってください。

釣り場座標精緻化スクリプト

Google Places Text Search API で釣り場名を検索し、
既存座標と 1km 以内であれば座標を更新する。
"""

import argparse
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

PLACES_TEXT_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/textsearch/json"


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """2点間の距離を km で返す（Haversine 式）"""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return R * 2 * math.asin(math.sqrt(a))


def load_config(config_path: str) -> dict:
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    required = ["api_key", "threshold_km", "search_language", "search_region",
                "request_delay_sec", "max_candidates"]
    for key in required:
        if key not in cfg:
            raise ValueError(f"config.json に '{key}' が見つかりません")
    if cfg["api_key"] == "YOUR_GOOGLE_PLACES_API_KEY":
        raise ValueError("config.json の api_key を設定してください")
    return cfg


# ---------------------------------------------------------------------------
# Google Places API
# ---------------------------------------------------------------------------

def search_place(name: str, cfg: dict) -> list[dict]:
    """
    Places Text Search で name を検索し、候補リストを返す。
    各要素: {"name": str, "lat": float, "lon": float}
    レート制限 (429) 時は指数バックオフで最大 3 回リトライ。
    """
    params = {
        "query": name,
        "language": cfg["search_language"],
        "region": cfg["search_region"],
        "key": cfg["api_key"],
    }
    wait = 1.0
    for attempt in range(4):
        try:
            resp = requests.get(PLACES_TEXT_SEARCH_URL, params=params, timeout=10)
            if resp.status_code == 429:
                if attempt < 3:
                    logger.warning("429 Too Many Requests — %s 秒待機してリトライ", wait)
                    time.sleep(wait)
                    wait *= 2
                    continue
                raise RuntimeError("429 Too Many Requests: リトライ上限に達しました")
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            candidates = []
            for r in results[: cfg["max_candidates"]]:
                loc = r.get("geometry", {}).get("location", {})
                if "lat" in loc and "lng" in loc:
                    candidates.append({
                        "name": r.get("name", ""),
                        "lat": loc["lat"],
                        "lon": loc["lng"],
                    })
            return candidates
        except requests.RequestException as e:
            if attempt < 3:
                logger.warning("リクエストエラー: %s — リトライ (%d/3)", e, attempt + 1)
                time.sleep(wait)
                wait *= 2
            else:
                raise
    return []


# ---------------------------------------------------------------------------
# スポット処理
# ---------------------------------------------------------------------------

def process_spot(filepath: Path, cfg: dict, dry_run: bool) -> dict:
    """
    1 つの釣り場 JSON ファイルを処理し、ログエントリを返す。
    dry_run=True の場合はファイルに書き込まない。
    """
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    slug = data.get("slug", filepath.stem)
    name = data.get("name", "")
    location = data.get("location", {})
    old_lat = location.get("latitude")
    old_lon = location.get("longitude")

    entry = {
        "slug": slug,
        "name": name,
        "old_coords": [old_lat, old_lon],
    }

    if old_lat is None or old_lon is None:
        logger.warning("[%s] location フィールドが不完全です。スキップ", slug)
        entry["status"] = "ERROR"
        entry["error"] = "location フィールドが不完全"
        return entry

    # 検索クエリを組み立てる（name だけ → name + city のフォールバック）
    city = data.get("area", {}).get("city", "")
    queries = [name]
    if city:
        queries.append(f"{name} {city}")

    candidates = []
    try:
        for query in queries:
            candidates = search_place(query, cfg)
            if candidates:
                break
    except Exception as e:
        logger.error("[%s] API エラー: %s", slug, e)
        entry["status"] = "ERROR"
        entry["error"] = str(e)
        return entry

    if not candidates:
        logger.info("[%s] '%s' → NOT_FOUND", slug, name)
        entry["status"] = "NOT_FOUND"
        return entry

    # 最短距離の候補を選択
    best = min(candidates, key=lambda c: haversine_km(old_lat, old_lon, c["lat"], c["lon"]))
    dist = haversine_km(old_lat, old_lon, best["lat"], best["lon"])

    if dist <= cfg["threshold_km"]:
        entry["status"] = "UPDATED"
        entry["found_name"] = best["name"]
        entry["new_coords"] = [best["lat"], best["lon"]]
        entry["distance_km"] = round(dist, 4)
        logger.info(
            "[%s] '%s' → UPDATED (%.0fm) '%s'",
            slug, name, dist * 1000, best["name"],
        )
        if not dry_run:
            data["location"]["latitude"] = best["lat"]
            data["location"]["longitude"] = best["lon"]
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.write("\n")
    else:
        entry["status"] = "TOO_FAR"
        entry["found_name"] = best["name"]
        entry["nearest_coords"] = [best["lat"], best["lon"]]
        entry["distance_km"] = round(dist, 4)
        logger.info(
            "[%s] '%s' → TOO_FAR (%.1fkm) '%s'",
            slug, name, dist, best["name"],
        )

    return entry


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="釣り場座標を Google Places API で精緻化する"
    )
    parser.add_argument(
        "--spots-dir", default="./spots_wip",
        help="spots/ ディレクトリのパス (default: ./spots_wip)",
    )
    parser.add_argument(
        "--config", default="./config.json",
        help="設定ファイルのパス (default: ./config.json)",
    )
    parser.add_argument(
        "--output", default="./results/update_log.json",
        help="ログ出力先 (default: ./results/update_log.json)",
    )
    parser.add_argument(
        "--threshold", type=float, default=None,
        help="距離閾値 km (config.json の値を上書き)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="ファイルを書き換えずに結果をプレビュー",
    )
    parser.add_argument(
        "--slug", default=None,
        help="特定の1件だけ処理（デバッグ用）",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.threshold is not None:
        cfg["threshold_km"] = args.threshold

    spots_dir = Path(args.spots_dir)
    if not spots_dir.is_dir():
        raise SystemExit(f"spots-dir が見つかりません: {spots_dir}")

    # 対象ファイル列挙（_ で始まるファイルを除外）
    files = sorted(
        f for f in spots_dir.glob("*.json")
        if not f.name.startswith("_")
    )
    if args.slug:
        files = [f for f in files if f.stem == args.slug]
        if not files:
            raise SystemExit(f"slug '{args.slug}' に対応するファイルが見つかりません")

    if args.dry_run:
        logger.info("=== DRY RUN モード（ファイルは変更されません）===")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    details = []
    summary = {"updated": 0, "too_far": 0, "not_found": 0, "error": 0}

    for i, filepath in enumerate(files, 1):
        logger.info("(%d/%d) %s", i, len(files), filepath.name)
        entry = process_spot(filepath, cfg, dry_run=args.dry_run)
        details.append(entry)
        status_key = entry["status"].lower()
        if status_key in summary:
            summary[status_key] += 1
        if i < len(files):
            time.sleep(cfg["request_delay_sec"])

    log = {
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dry_run": args.dry_run,
        "threshold_km": cfg["threshold_km"],
        "summary": summary,
        "details": details,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("\n===== 処理結果 =====")
    print(f"  UPDATED  : {summary['updated']}")
    print(f"  TOO_FAR  : {summary['too_far']}")
    print(f"  NOT_FOUND: {summary['not_found']}")
    print(f"  ERROR    : {summary['error']}")
    print(f"\nログ出力先: {output_path}")
    if args.dry_run:
        print("（DRY RUN: ファイルは変更されていません）")


if __name__ == "__main__":
    main()
