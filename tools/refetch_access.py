#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Google Places Nearby Search + Directions API を使い、
各スポットの交通アクセス情報（info.access）を再取得・更新する。

処理フロー（1スポットあたり）:
  1. Places Nearby Search で半径3km内の transit_station を最大5件取得
  2. Directions API (mode=transit) で各駅 → スポットのルートを取得
  3. 最短時間のルートを採用し、書式を自動生成:
       ○○駅から徒歩○分   （全行程が徒歩の場合）
       ○○駅からバス○分   （バス利用を含む場合）
     ※ ルートが見つからない・徒歩30分超の場合:
       ○○方面から車利用
  4. --apply 付きのときのみ spots/ の JSON を上書き

使い方:
  python tools/refetch_access.py                       # ドライラン（全件）
  python tools/refetch_access.py --apply               # 全件書き込み
  python tools/refetch_access.py --slug abosaki        # 1件のみ
  python tools/refetch_access.py --flagged-only        # audit_access.tsv の NG のみ
  python tools/refetch_access.py --flagged-only --apply
  python tools/refetch_access.py --skip-ok             # 既存 OK 書式をスキップ
"""

import argparse
import csv
import json
import math
import re
import sys
import time
from pathlib import Path

import requests

REPO_ROOT    = Path(__file__).parent.parent
SPOTS_DIR    = REPO_ROOT / "spots"
CONFIG_FILE  = REPO_ROOT / "config.json"
AUDIT_FILE   = REPO_ROOT / "access_audit.tsv"

NEARBY_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
DIRECTIONS_URL    = "https://maps.googleapis.com/maps/api/directions/json"

# 徒歩のみで許容する最大時間（分）。超えたら「車利用」
MAX_WALK_MIN  = 30
# 駅の候補を最大何件試すか
MAX_STATIONS  = 5
# Nearby Search の半径（m）
NEARBY_RADIUS = 3000

_ACCESS_RE = re.compile(r"^(.+?駅)から(徒歩|バス)(\d+)分$")


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        print(f"[エラー] {CONFIG_FILE} が見つかりません", file=sys.stderr)
        sys.exit(1)
    with open(CONFIG_FILE, encoding="utf-8") as f:
        cfg = json.load(f)
    if "api_key" not in cfg:
        print("[エラー] config.json に api_key がありません", file=sys.stderr)
        sys.exit(1)
    return cfg


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def _get(url: str, params: dict, retries: int = 4) -> dict | None:
    """GET リクエスト。429 は指数バックオフでリトライ。失敗時は None。"""
    wait = 1.0
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code == 429:
                if attempt < retries - 1:
                    print(f"  [429] {wait:.0f}s 待機してリトライ...")
                    time.sleep(wait)
                    wait *= 2
                    continue
                return None
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            if attempt < retries - 1:
                time.sleep(wait)
                wait *= 2
            else:
                print(f"  [警告] リクエスト失敗: {e}", file=sys.stderr)
                return None
    return None


def search_nearby_stations(lat: float, lon: float, api_key: str) -> list[dict]:
    """スポット付近の transit_station を距離順で返す（最大 MAX_STATIONS 件）。"""
    data = _get(NEARBY_SEARCH_URL, {
        "location": f"{lat},{lon}",
        "radius":   NEARBY_RADIUS,
        "type":     "transit_station",
        "key":      api_key,
        "language": "ja",
    })
    if not data:
        return []
    results = data.get("results", [])
    stations = []
    for r in results:
        loc = r.get("geometry", {}).get("location", {})
        if "lat" not in loc:
            continue
        stations.append({
            "name": r.get("name", ""),
            "lat":  loc["lat"],
            "lon":  loc["lng"],
        })
    # 駅（名前に「駅」を含む）を優先し、同順位は距離順にソート
    stations.sort(key=lambda s: (
        0 if "駅" in s["name"] else 1,
        haversine_km(lat, lon, s["lat"], s["lon"]),
    ))
    return stations[:MAX_STATIONS]


def get_directions(origin_lat: float, origin_lon: float,
                   dest_lat: float, dest_lon: float,
                   api_key: str) -> dict | None:
    """
    Directions API (mode=transit) でルートを取得する。
    transit で結果なしの場合は walking モードにフォールバック。
    """
    def _fetch(mode: str) -> dict | None:
        return _get(DIRECTIONS_URL, {
            "origin":      f"{origin_lat},{origin_lon}",
            "destination": f"{dest_lat},{dest_lon}",
            "mode":        mode,
            "language":    "ja",
            "key":         api_key,
        })

    for mode in ("transit", "walking"):
        data = _fetch(mode)
        if data and data.get("status") == "OK":
            routes = data.get("routes", [])
            if routes:
                return {"mode": mode, "route": routes[0]}
    return None


def classify_route(route: dict, transit_mode: str) -> tuple[str, int]:
    """
    ルートを解析し (移動種別, 所要分) を返す。
    移動種別: "徒歩" / "バス" / "車利用"
    """
    leg = route["legs"][0]
    duration_sec = leg["duration"]["value"]
    duration_min = round(duration_sec / 60)

    if transit_mode == "walking":
        # walking モードにフォールバックしたケース
        if duration_min > MAX_WALK_MIN:
            return "車利用", duration_min
        return "徒歩", duration_min

    # transit モードの場合、ステップを解析
    steps = leg.get("steps", [])
    uses_bus = False
    for step in steps:
        travel_mode = step.get("travel_mode", "")
        if travel_mode == "TRANSIT":
            vtype = (step.get("transit_details", {})
                        .get("line", {})
                        .get("vehicle", {})
                        .get("type", ""))
            # BUS, SHARE_TAXI 等
            if vtype in ("BUS", "SHARE_TAXI", "INTERCITY_BUS", "TROLLEYBUS"):
                uses_bus = True
                break
            # 電車・地下鉄の乗り継ぎは通常起きないが、乗り継ぎがあればバス扱い
            # （駅出発なので HEAVY_RAIL/SUBWAY は通常なし）

    if uses_bus:
        return "バス", duration_min
    return "徒歩", duration_min


def round_up_5(minutes: int) -> int:
    """所要時間を5分単位に切り上げる。"""
    return math.ceil(minutes / 5) * 5


def is_bus_stop(name: str) -> bool:
    """Google Places の名前からバス停かどうかを判定する。"""
    return "（バス）" in name or "(バス)" in name or "駅" not in name


def format_access(station_name: str, mode: str, minutes: int,
                  area_hint: str = "") -> str:
    """アクセス文字列を書式化する。所要時間は5分単位に切り上げ。"""
    if mode == "車利用":
        hint = area_hint or station_name
        return f"{hint}方面から車利用"
    mins = round_up_5(minutes)
    if is_bus_stop(station_name):
        # 「（バス）」などを除去してバス停名を整形
        stop_name = re.sub(r"[（(]バス[）)]", "", station_name).strip()
        return f"{stop_name}バス停から{mode}{mins}分"
    station = station_name if station_name.endswith("駅") else station_name + "駅"
    return f"{station}から{mode}{mins}分"


def fetch_access_for_spot(spot: dict, api_key: str,
                           delay: float = 0.5) -> dict:
    """
    1スポットの交通アクセスを取得する。

    Returns:
      {
        "access": "○○駅から徒歩○分" など,
        "station": "○○駅",
        "mode": "徒歩" / "バス" / "車利用",
        "minutes": int,
        "source": "directions_transit" / "directions_walking" / "no_station" / "error",
      }
    """
    lat = spot["location"]["latitude"]
    lon = spot["location"]["longitude"]

    # Step1: 近隣の駅を取得
    stations = search_nearby_stations(lat, lon, api_key)
    time.sleep(delay)

    if not stations:
        area = (spot.get("area", {}).get("area_name", "")
                or spot.get("area", {}).get("prefecture", ""))
        return {
            "access":  f"{area}方面から車利用" if area else "車利用",
            "station": "",
            "mode":    "車利用",
            "minutes": 0,
            "source":  "no_station",
        }

    # Step2: 各駅のルートを取得し、最短を選ぶ
    best = None
    best_sec = float("inf")
    best_station = None
    best_transit_mode = "walking"

    for st in stations:
        result = get_directions(st["lat"], st["lon"], lat, lon, api_key)
        time.sleep(delay)
        if not result:
            continue
        leg = result["route"]["legs"][0]
        sec = leg["duration"]["value"]
        if sec < best_sec:
            best_sec = sec
            best = result["route"]
            best_station = st
            best_transit_mode = result["mode"]

    if best is None:
        # Directions 全失敗 → 最近傍駅から直線距離で推定
        st = stations[0]
        km = haversine_km(lat, lon, st["lat"], st["lon"])
        est_min = round(km * 1000 * 1.4 / 80)
        if est_min > MAX_WALK_MIN:
            area = (spot.get("area", {}).get("area_name", "")
                    or spot.get("area", {}).get("prefecture", ""))
            return {
                "access":  f"{area}方面から車利用",
                "station": st["name"],
                "mode":    "車利用",
                "minutes": est_min,
                "source":  "estimated_walk",
            }
        station_name = st["name"]
        station_label = station_name if station_name.endswith("駅") else station_name + "駅"
        return {
            "access":  f"{station_label}から徒歩{est_min}分（推定）",
            "station": station_name,
            "mode":    "徒歩",
            "minutes": est_min,
            "source":  "estimated_walk",
        }

    travel_mode, minutes = classify_route(best, best_transit_mode)
    station_name = best_station["name"]
    area = (spot.get("area", {}).get("area_name", "")
            or spot.get("area", {}).get("prefecture", ""))

    if travel_mode == "車利用":
        access_str = format_access(station_name, "車利用", minutes, area_hint=area)
    else:
        access_str = format_access(station_name, travel_mode, minutes)

    src = f"directions_{best_transit_mode}"
    return {
        "access":  access_str,
        "station": station_name,
        "mode":    travel_mode,
        "minutes": minutes,
        "source":  src,
    }


def load_flagged_slugs() -> set[str]:
    """audit_access.tsv から flag が NG のスラッグ一覧を返す。"""
    if not AUDIT_FILE.exists():
        print(f"[エラー] {AUDIT_FILE} が見つかりません。先に audit_access.py を実行してください。",
              file=sys.stderr)
        sys.exit(1)
    ok_flags = {"OK", "OK_FORMAT", "OK_CAR", "EMPTY"}
    slugs = set()
    with open(AUDIT_FILE, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        for row in reader:
            if row.get("flag", "") not in ok_flags:
                slugs.add(row["slug"])
    return slugs


def main():
    parser = argparse.ArgumentParser(
        description="Google Directions API でスポットの交通アクセスを再取得・更新する"
    )
    parser.add_argument("--apply",        action="store_true",
                        help="実際に spots/ の JSON を上書きする（デフォルト: ドライラン）")
    parser.add_argument("--slug",         metavar="SLUG",
                        help="1件のみ処理するスラッグ")
    parser.add_argument("--flagged-only", action="store_true",
                        help="access_audit.tsv で NG フラグのスポットのみ処理")
    parser.add_argument("--skip-ok",      action="store_true",
                        help="既存 access が正しい書式のスポットをスキップ")
    args = parser.parse_args()

    cfg   = load_config()
    key   = cfg["api_key"]
    delay = cfg.get("request_delay_sec", 0.5)

    # 対象ファイルの決定
    if args.slug:
        files = [SPOTS_DIR / f"{args.slug}.json"]
        files = [f for f in files if f.exists()]
        if not files:
            print(f"[エラー] {args.slug}.json が見つかりません")
            return
    else:
        all_files = sorted(f for f in SPOTS_DIR.glob("*.json")
                           if not f.name.startswith("_"))
        if args.flagged_only:
            flagged = load_flagged_slugs()
            files = [f for f in all_files if f.stem in flagged]
            print(f"flagged-only: {len(flagged)}件 → 該当ファイル {len(files)}件")
        else:
            files = all_files

    if not files:
        print("処理対象が見つかりません。")
        return

    mode_label = "書き込みモード" if args.apply else "ドライラン"
    print(f"対象: {len(files)}件  モード: {mode_label}\n")
    print("[注意] Google Directions API の利用には GCP で Directions API の有効化が必要です。\n")

    ok = 0
    skipped = 0
    errors = 0

    for i, path in enumerate(files, 1):
        spot = json.loads(path.read_text(encoding="utf-8"))
        name = spot.get("name", path.stem)
        current = spot.get("info", {}).get("access", "")
        print(f"[{i}/{len(files)}] {name} ({path.stem})")
        print(f"  現在: '{current}'")

        # --skip-ok: 正しい書式なら飛ばす
        if args.skip_ok and _ACCESS_RE.match(current.strip()):
            print("  → [スキップ] 既存の書式が正常")
            skipped += 1
            continue

        result = fetch_access_for_spot(spot, key, delay=delay)
        new_access = result["access"]
        source     = result["source"]

        print(f"  提案: '{new_access}'  （source={source}, "
              f"station={result['station']}, mode={result['mode']}, "
              f"min={result['minutes']}）")

        if args.apply:
            spot.setdefault("info", {})["access"] = new_access
            path.write_text(json.dumps(spot, ensure_ascii=False, indent=2),
                            encoding="utf-8")
            print(f"  → 書き込み完了")
            ok += 1
        else:
            ok += 1

    print(f"\n── 完了 ── 処理: {ok}件  スキップ: {skipped}件  エラー: {errors}件")
    if not args.apply:
        print("\n実際に書き込むには --apply を指定してください。")


if __name__ == "__main__":
    main()
