#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spots/ の info.access を監査し、疑わしいエントリをレポートする。

チェック内容:
  1. 書式チェック: "○○駅から徒歩○分" / "○○駅からバス○分" に合致するか
  2. 駅存在チェック:
     - 徒歩: Google Places Nearby Search でスポット付近に実在するか
     - バス: Google Places Text Search で駅名を直接検索（出発駅はスポットから遠い）
  3. 所要時間の妥当性: 直線距離から推定時間と申告時間を比較
     - 徒歩: 80m/分 換算で乖離が大きければフラグ
     - バス: 直線距離2km未満でバス表記はフラグ

使い方:
  python tools/audit_access.py                 # 全件監査 → access_audit.tsv
  python tools/audit_access.py --slug abosaki  # 1件のみ
  python tools/audit_access.py --no-api        # Google API なし（書式チェックのみ）
  python tools/audit_access.py --empty         # access が空のスポットも出力
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

REPO_ROOT   = Path(__file__).parent.parent
SPOTS_DIR   = REPO_ROOT / "spots"
CONFIG_FILE = REPO_ROOT / "config.json"
OUTPUT_FILE = REPO_ROOT / "access_audit.tsv"

NEARBY_SEARCH_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
TEXT_SEARCH_URL   = "https://maps.googleapis.com/maps/api/place/textsearch/json"

# 徒歩速度（m/分）
WALK_SPEED_MPM = 80
# 直線距離に対する道路距離の補正係数（一般的に道路距離は直線の1.3〜1.5倍）
ROAD_FACTOR    = 1.4
# 乖離を許容する倍率（この倍率以内なら OK 扱い）
# 海沿いは道が曲がりくねるため 2.5 程度が現実的
TOLERANCE_RATIO = 2.5
# バス表記でこの距離未満なら「近すぎる」フラグ
BUS_MIN_KM = 1.0
# 駅名照合で使う部分一致の最短文字数
MIN_STATION_MATCH_LEN = 2


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


def search_nearby_stations(lat: float, lon: float, api_key: str,
                            radius_m: int = 5000) -> list[dict]:
    """Google Places Nearby Search でスポット付近の transit_station を返す。"""
    params = {
        "location": f"{lat},{lon}",
        "radius":   radius_m,
        "type":     "transit_station",
        "key":      api_key,
        "language": "ja",
    }
    wait = 1.0
    for attempt in range(4):
        try:
            resp = requests.get(NEARBY_SEARCH_URL, params=params, timeout=10)
            if resp.status_code == 429:
                if attempt < 3:
                    time.sleep(wait)
                    wait *= 2
                    continue
                return []
            resp.raise_for_status()
            results = resp.json().get("results", [])
            stations = []
            for r in results:
                loc = r.get("geometry", {}).get("location", {})
                if "lat" not in loc:
                    continue
                name = r.get("name", "")
                # バス停（名前に「駅」を含まない）は除外
                if "駅" not in name:
                    continue
                stations.append({
                    "name": name,
                    "lat":  loc["lat"],
                    "lon":  loc["lng"],
                })
            return stations
        except requests.RequestException as e:
            if attempt < 3:
                time.sleep(wait)
                wait *= 2
            else:
                print(f"  [警告] Places Nearby 失敗: {e}", file=sys.stderr)
                return []
    return []


def search_station_by_name(station_name: str, area_hint: str,
                            api_key: str) -> dict | None:
    """
    Google Places Text Search で駅名を直接検索し、最初の結果を返す。
    バス出発駅の存在確認に使用（スポットから遠い場合がある）。
    """
    query = f"{station_name} {area_hint}".strip()
    params = {
        "query":    query,
        "type":     "transit_station",
        "language": "ja",
        "key":      api_key,
    }
    wait = 1.0
    for attempt in range(4):
        try:
            resp = requests.get(TEXT_SEARCH_URL, params=params, timeout=10)
            if resp.status_code == 429:
                if attempt < 3:
                    time.sleep(wait)
                    wait *= 2
                    continue
                return None
            resp.raise_for_status()
            results = resp.json().get("results", [])
            for r in results:
                name = r.get("name", "")
                # 駅名の部分一致チェック
                q_bare = re.sub(r"駅$", "", station_name)
                r_bare = re.sub(r"駅$", "", name)
                if q_bare in r_bare or r_bare in q_bare:
                    loc = r.get("geometry", {}).get("location", {})
                    if "lat" in loc:
                        return {"name": name, "lat": loc["lat"], "lon": loc["lng"]}
            return None
        except requests.RequestException as e:
            if attempt < 3:
                time.sleep(wait)
                wait *= 2
            else:
                print(f"  [警告] Text Search 失敗: {e}", file=sys.stderr)
                return None
    return None


# "○○駅から徒歩○分" / "○○駅からバス○分" / "○○方面から車利用"
_ACCESS_RE = re.compile(
    r"^(?P<station>.+?駅)から(?P<mode>徒歩|バス)(?P<min>\d+)分$"
)
_CAR_RE = re.compile(r"から車利用")


def parse_access(access: str) -> dict | None:
    """アクセス文字列をパースして dict を返す。マッチしなければ None。"""
    m = _ACCESS_RE.match(access.strip())
    if not m:
        return None
    return {
        "station": m.group("station"),
        "mode":    m.group("mode"),
        "minutes": int(m.group("min")),
    }


def station_name_match(query: str, stations: list[dict]) -> dict | None:
    """
    query（"○○駅"）を stations リストと照合。
    駅名から「駅」を除いた部分の部分一致で判定する。
    最初にマッチした駅を返す。
    """
    q_bare = re.sub(r"駅$", "", query)
    for st in stations:
        st_bare = re.sub(r"駅$", "", st["name"])
        if q_bare in st_bare or st_bare in q_bare:
            return st
    return None


def audit_spot(spot_data: dict, api_key: str | None,
               request_delay: float = 0.3) -> dict:
    """
    1件のスポットを監査する。

    Returns dict with keys:
      slug, name, current_access, parsed_station, parsed_mode, parsed_min,
      nearest_station, nearest_dist_km, flag, reason
    """
    slug    = spot_data.get("slug", "")
    name    = spot_data.get("name", "")
    lat     = spot_data["location"]["latitude"]
    lon     = spot_data["location"]["longitude"]
    access  = spot_data.get("info", {}).get("access", "")

    row = {
        "slug":            slug,
        "name":            name,
        "current_access":  access,
        "parsed_station":  "",
        "parsed_mode":     "",
        "parsed_min":      "",
        "nearest_station": "",
        "nearest_dist_km": "",
        "station_found":   "",
        "found_dist_km":   "",
        "flag":            "",
        "reason":          "",
    }

    # 空欄
    if not access:
        row["flag"]   = "EMPTY"
        row["reason"] = "access が空"
        return row

    # 車利用は検証対象外
    if _CAR_RE.search(access):
        row["flag"]   = "OK_CAR"
        row["reason"] = "車利用（検証対象外）"
        return row

    # 書式チェック
    parsed = parse_access(access)
    if not parsed:
        row["flag"]   = "FORMAT_ERROR"
        row["reason"] = "書式不一致（○○駅から徒歩/バス○分 でない）"
        return row

    row["parsed_station"] = parsed["station"]
    row["parsed_mode"]    = parsed["mode"]
    row["parsed_min"]     = str(parsed["minutes"])

    # API なしモードはここで終了
    if api_key is None:
        row["flag"]   = "OK_FORMAT"
        row["reason"] = "書式 OK（API チェックなし）"
        return row

    # ── 徒歩モード: Nearby Search でスポット付近の駅を確認 ──────────────
    if parsed["mode"] == "徒歩":
        stations = search_nearby_stations(lat, lon, api_key)
        time.sleep(request_delay)

        if stations:
            nearest = min(stations, key=lambda s: haversine_km(lat, lon, s["lat"], s["lon"]))
            nearest_dist = haversine_km(lat, lon, nearest["lat"], nearest["lon"])
            row["nearest_station"] = nearest["name"]
            row["nearest_dist_km"] = f"{nearest_dist:.2f}"
        else:
            nearest = None

        found_st = station_name_match(parsed["station"], stations) if stations else None
        if found_st:
            found_dist_km = haversine_km(lat, lon, found_st["lat"], found_st["lon"])
            row["station_found"] = found_st["name"]
            row["found_dist_km"] = f"{found_dist_km:.2f}"
        else:
            found_dist_km = None

        if not stations:
            row["flag"]   = "API_NO_RESULT"
            row["reason"] = "5km 圏内に駅なし（要確認）"
            return row

        if not found_st:
            nearest_name = nearest["name"] if nearest else "不明"
            row["flag"]   = "STATION_MISMATCH"
            row["reason"] = f"申告駅「{parsed['station']}」が周辺に見当たらない（最寄: {nearest_name}）"
            return row

        # 所要時間の妥当性チェック（徒歩）
        dist_m       = found_dist_km * 1000 * ROAD_FACTOR
        expected_min = dist_m / WALK_SPEED_MPM
        ratio        = parsed["minutes"] / max(expected_min, 1)
        if ratio > TOLERANCE_RATIO or ratio < (1 / TOLERANCE_RATIO):
            row["flag"]   = "TIME_IMPLAUSIBLE"
            row["reason"] = (
                f"直線{found_dist_km:.1f}km → 推定徒歩{expected_min:.0f}分 "
                f"vs 申告{parsed['minutes']}分（{ratio:.1f}x）"
            )
            return row

    # ── バスモード: Text Search で出発駅を直接検索 ────────────────────
    elif parsed["mode"] == "バス":
        area_hint = (spot_data.get("area", {}).get("prefecture", "")
                     or spot_data.get("area", {}).get("area_name", ""))
        found_st = search_station_by_name(parsed["station"], area_hint, api_key)
        time.sleep(request_delay)

        if not found_st:
            row["flag"]   = "STATION_NOT_FOUND"
            row["reason"] = f"申告駅「{parsed['station']}」が Text Search で見つからない"
            return row

        found_dist_km = haversine_km(lat, lon, found_st["lat"], found_st["lon"])
        row["station_found"] = found_st["name"]
        row["found_dist_km"] = f"{found_dist_km:.2f}"

        if found_dist_km < BUS_MIN_KM:
            row["flag"]   = "BUS_TOO_CLOSE"
            row["reason"] = (
                f"直線{found_dist_km:.2f}km — 徒歩圏なのにバス表記"
            )
            return row

    row["flag"]   = "OK"
    row["reason"] = f"問題なし（距離{found_dist_km:.1f}km）"
    return row


def main():
    parser = argparse.ArgumentParser(
        description="spots/ の info.access を監査して access_audit.tsv を出力する"
    )
    parser.add_argument("--slug",   metavar="SLUG", help="1件のみ処理するスラッグ")
    parser.add_argument("--no-api", action="store_true",
                        help="Google API を使わず書式チェックのみ")
    parser.add_argument("--empty",  action="store_true",
                        help="access が空のスポットも TSV に含める")
    args = parser.parse_args()

    cfg      = load_config()
    api_key  = None if args.no_api else cfg["api_key"]
    delay    = cfg.get("request_delay_sec", 0.3)

    if args.slug:
        files = [SPOTS_DIR / f"{args.slug}.json"]
        files = [f for f in files if f.exists()]
        if not files:
            print(f"[エラー] {args.slug}.json が見つかりません")
            return
    else:
        files = sorted(f for f in SPOTS_DIR.glob("*.json")
                       if not f.name.startswith("_"))

    if not files:
        print(f"{SPOTS_DIR} に JSON ファイルが見つかりません。")
        return

    mode_label = "書式チェックのみ" if args.no_api else "Google API あり"
    print(f"対象: {len(files)}件  モード: {mode_label}\n")

    results = []
    counters = {"OK": 0, "OK_FORMAT": 0, "OK_CAR": 0, "EMPTY": 0, "flag": 0}

    for i, path in enumerate(files, 1):
        spot = json.loads(path.read_text(encoding="utf-8"))
        name = spot.get("name", path.stem)
        access = spot.get("info", {}).get("access", "")
        print(f"[{i}/{len(files)}] {name} ({path.stem})  access='{access}'")

        row = audit_spot(spot, api_key, request_delay=delay)

        flag = row["flag"]
        if flag in ("OK", "OK_FORMAT", "OK_CAR"):
            counters[flag] += 1
            print(f"  ✓ {flag}: {row['reason']}")
        elif flag == "EMPTY":
            counters["EMPTY"] += 1
            print(f"  - EMPTY")
        else:
            counters["flag"] += 1
            print(f"  ✗ {flag}: {row['reason']}")

        # EMPTY はオプションで除外
        if flag == "EMPTY" and not args.empty:
            continue

        results.append(row)

    # TSV 出力
    fieldnames = [
        "slug", "name", "current_access",
        "parsed_station", "parsed_mode", "parsed_min",
        "nearest_station", "nearest_dist_km",
        "station_found", "found_dist_km",
        "flag", "reason",
    ]
    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(results)

    print(f"\n── 完了 ──")
    print(f"  OK: {counters['OK']}  OK_FORMAT: {counters['OK_FORMAT']}  "
          f"OK_CAR: {counters['OK_CAR']}  EMPTY: {counters['EMPTY']}  "
          f"要確認: {counters['flag']}")
    print(f"  → {OUTPUT_FILE.relative_to(REPO_ROOT)}")
    if counters["flag"] > 0:
        print(f"\n  要確認 {counters['flag']}件 を refetch_access.py で修正できます:")
        print(f"    python tools/refetch_access.py --flagged-only --apply")


if __name__ == "__main__":
    main()
