#!/usr/bin/env python3
"""
気象庁局コード 自動マッピング生成スクリプト。

spots/*.json に記載の harbor_name（tide736.net 港名）を
harbor_list.json の緯度経度で引き当てて、最近傍の気象庁局コードを
Haversine 距離で決定し、data/jma_harbor_map.json に出力する。

出力した jma_harbor_map.json は人手で確認・修正したうえで
apply_jma_harbor_map.py で spots/*.json に書き込む。

Usage:
    python scripts/create_jma_harbor_map.py
    python scripts/create_jma_harbor_map.py --max-dist 80   # 距離しきい値 km（デフォルト 60）
    python scripts/create_jma_harbor_map.py --show-all      # しきい値超えも含む全件表示
"""

import argparse
import json
import math
import pathlib
import sys

_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

# fetch_jma_tides.py から局リストを再利用
from scripts.fetch_jma_tides import JMA_ALL_STATIONS, JMA_STATIONS, TARGET_STATIONS

OUTPUT_PATH = _ROOT / "data" / "jma_harbor_map.json"
HARBOR_LIST_PATH = _ROOT / "data" / "harbor_list.json"
SPOTS_DIR = _ROOT / "spots"

DEFAULT_MAX_DIST_KM = 60.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearest_jma(lat: float, lon: float) -> tuple[str, str, float]:
    """TARGET_STATIONS の中で最近傍の局を返す (code, name, km)。座標は十進法度。"""
    best_code = best_name = ""
    best_dist = float("inf")
    for code, (name, _kana, s_lat, s_lon) in JMA_STATIONS.items():
        if code not in TARGET_STATIONS:
            continue
        d = _haversine_km(lat, lon, s_lat, s_lon)
        if d < best_dist:
            best_dist = d
            best_code = code
            best_name = name
    return best_code, best_name, best_dist


def load_harbor_coords() -> dict[str, tuple[float, float]]:
    """harbor_code → (lat, lon) の辞書を返す。"""
    data = json.loads(HARBOR_LIST_PATH.read_text(encoding="utf-8"))
    result: dict[str, tuple[float, float]] = {}
    for h in data.get("harbors", []):
        code = h.get("harbor_code")
        lat = h.get("lat")
        lon = h.get("lon")
        if code and lat and lon:
            result[code] = (float(lat), float(lon))
    return result


def load_spots_harbor_info() -> dict[str, dict]:
    """spots/*.json から harbor_code をキーに {harbor_name, slugs} を収集する。"""
    result: dict[str, dict] = {}
    for f in sorted(SPOTS_DIR.glob("*.json")):
        if f.stem.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        hc = data.get("harbor_code")
        hn = data.get("harbor_name", "")
        slug = data.get("slug", f.stem)
        if not hc:
            continue
        if hc not in result:
            result[hc] = {"harbor_name": hn, "slugs": []}
        result[hc]["slugs"].append(slug)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="気象庁局コード 港マッピング生成")
    parser.add_argument("--max-dist", type=float, default=DEFAULT_MAX_DIST_KM,
                        help=f"距離しきい値 km（この距離以上はNG扱い、デフォルト {DEFAULT_MAX_DIST_KM}）")
    parser.add_argument("--show-all", action="store_true",
                        help="しきい値超えも含む全件表示")
    args = parser.parse_args()

    harbor_coords = load_harbor_coords()
    spots_info = load_spots_harbor_info()

    mapping: dict[str, dict] = {}
    no_coords: list[str] = []
    too_far: list[dict] = []

    for harbor_code, info in sorted(spots_info.items(), key=lambda x: x[1]["harbor_name"]):
        harbor_name = info["harbor_name"]
        slugs = info["slugs"]

        coords = harbor_coords.get(harbor_code)
        if not coords:
            no_coords.append(harbor_code)
            continue

        lat, lon = coords
        jma_code, jma_name, dist_km = nearest_jma(lat, lon)

        entry = {
            "harbor_name": harbor_name,
            "harbor_code": harbor_code,
            "lat": lat,
            "lon": lon,
            "jma_harbor_code": jma_code,
            "jma_station_name": jma_name,
            "dist_km": round(dist_km, 1),
            "slugs": slugs,
        }

        if dist_km > args.max_dist:
            too_far.append(entry)
        else:
            mapping[harbor_code] = entry

    # 出力
    output = {
        "_meta": {
            "description": "tide736.net 港コード → 気象庁局コード マッピング",
            "note": "apply_jma_harbor_map.py で spots/*.json の jma_harbor_code に書き込む",
            "max_dist_km": args.max_dist,
            "generated_by": "scripts/create_jma_harbor_map.py",
        },
        "harbors": mapping,
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"保存: {OUTPUT_PATH.relative_to(_ROOT)}  ({len(mapping)} 港)")

    # コンソールサマリー
    print(f"\n{'港名':14s} {'港CD':8s} {'JMA局':8s} {'局名':10s} {'距離':>8s}  スポット数")
    print("-" * 65)
    for entry in sorted(mapping.values(), key=lambda e: e["harbor_name"]):
        print(f"{entry['harbor_name']:14s} {entry['harbor_code']:8s} "
              f"{entry['jma_harbor_code']:8s} {entry['jma_station_name']:10s} "
              f"{entry['dist_km']:6.1f} km  {len(entry['slugs'])}スポット")

    if too_far:
        print(f"\n⚠️  距離しきい値 ({args.max_dist} km) 超え — mapping 未登録 ({len(too_far)} 港):")
        for e in sorted(too_far, key=lambda x: x["harbor_name"]):
            print(f"  {e['harbor_name']:14s} {e['harbor_code']:8s} → {e['jma_station_name']:10s} {e['dist_km']:.1f} km")
        print("  --show-all または --max-dist を増やすと mapping に含められます。")

    if no_coords:
        print(f"\n⚠️  緯度経度なし — harbor_list.json に未登録 ({len(no_coords)} 港):")
        for hc in no_coords:
            print(f"  {hc}")


if __name__ == "__main__":
    main()
