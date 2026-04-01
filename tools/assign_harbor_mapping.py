#!/usr/bin/env python3
"""
全スポットに最寄り港コードを自動割り当てして data/harbor_mapping.json を生成する。

事前準備:
  python tools/fetch_harbor_list.py  # harbor_list.json を生成

処理:
  1. data/harbor_list.json を読み込む（港コード + 緯度経度）
  2. spots/*.json を全件読み込む（緯度経度）
  3. Haversine 距離で各スポットの最寄り港を決定
  4. data/harbor_mapping.json を生成

使い方:
    python tools/assign_harbor_mapping.py              # 全スポットを処理
    python tools/assign_harbor_mapping.py --dry-run    # 表示のみ（ファイル保存しない）
    python tools/assign_harbor_mapping.py --max-km 50  # 50km 超の割り当てを警告
    python tools/assign_harbor_mapping.py --slug abosaki  # 1スポットのみ確認
"""

import argparse
import json
import math
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
SPOTS_DIR = _REPO_ROOT / "spots"
HARBOR_LIST_PATH = _REPO_ROOT / "data" / "harbor_list.json"
OUTPUT_PATH = _REPO_ROOT / "data" / "harbor_mapping.json"


# ─────────────────────────────────────────────────────────
# Haversine 距離計算
# ─────────────────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """2点間の球面距離（km）を返す。"""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


# ─────────────────────────────────────────────────────────
# データ読み込み
# ─────────────────────────────────────────────────────────

def load_harbor_list() -> list[dict]:
    """data/harbor_list.json を読み込んで座標あり港のみ返す。"""
    if not HARBOR_LIST_PATH.exists():
        print(f"[エラー] {HARBOR_LIST_PATH} が見つかりません。")
        print("  先に以下を実行してください:")
        print("    python tools/fetch_harbor_list.py")
        sys.exit(1)

    with open(HARBOR_LIST_PATH, encoding="utf-8") as f:
        data = json.load(f)

    all_harbors = data.get("harbors", [])
    valid = [h for h in all_harbors if h.get("lat") is not None and h.get("lon") is not None]
    invalid = len(all_harbors) - len(valid)

    if invalid > 0:
        print(f"[情報] 座標なし港をスキップ: {invalid} 件（座標あり: {len(valid)} 件）")
    if not valid:
        print("[エラー] 座標付き港が0件です。fetch_harbor_list.py を再実行してください。")
        sys.exit(1)

    return valid


def load_spots() -> list[dict]:
    """spots/*.json を全件読み込んで slug・lat・lon を返す。"""
    spots = []
    for p in sorted(SPOTS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            slug = data.get("slug") or p.stem
            loc = data.get("location", {})
            lat = loc.get("latitude")
            lon = loc.get("longitude")
            if lat is None or lon is None:
                continue
            spots.append({
                "slug": slug,
                "lat": float(lat),
                "lon": float(lon),
                "name": data.get("name", slug),
            })
        except Exception as e:
            print(f"  [警告] {p.name} スキップ: {e}")
    return spots


# ─────────────────────────────────────────────────────────
# 最寄り港の決定
# ─────────────────────────────────────────────────────────

def find_nearest_harbor(spot_lat: float, spot_lon: float, harbors: list[dict]) -> tuple[dict, float]:
    """
    スポット座標に最も近い港と距離(km)を返す。
    """
    best_harbor = None
    best_dist = float("inf")
    for h in harbors:
        dist = haversine_km(spot_lat, spot_lon, h["lat"], h["lon"])
        if dist < best_dist:
            best_dist = dist
            best_harbor = h
    return best_harbor, best_dist


# ─────────────────────────────────────────────────────────
# メイン処理
# ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="全スポットに最寄り港を自動割り当て")
    parser.add_argument("--dry-run", action="store_true",
                        help="結果を表示するのみ（ファイル保存しない）")
    parser.add_argument("--max-km", type=float, default=80.0,
                        help="この距離(km)を超える割り当てを警告する（デフォルト: 80km）")
    parser.add_argument("--slug", metavar="SLUG",
                        help="1スポットのみ処理（確認・デバッグ用）")
    args = parser.parse_args()

    print("=== 最寄り港 自動割り当て ===")

    harbors = load_harbor_list()
    spots = load_spots()
    print(f"[読み込み] 港: {len(harbors)} 件、スポット: {len(spots)} 件")

    if args.slug:
        spots = [s for s in spots if s["slug"] == args.slug]
        if not spots:
            print(f"[エラー] slug '{args.slug}' が見つかりません")
            sys.exit(1)

    # 既存の harbor_mapping.json があれば読み込んでマージ（手動エントリを保持）
    existing_mapping: dict[str, dict] = {}
    if OUTPUT_PATH.exists():
        with open(OUTPUT_PATH, encoding="utf-8") as f:
            old = json.load(f)
        existing_mapping = old.get("spots", {})

    result_spots: dict[str, dict] = {}
    warn_count = 0

    for spot in spots:
        slug = spot["slug"]
        nearest, dist_km = find_nearest_harbor(spot["lat"], spot["lon"], harbors)

        entry = {
            "harbor_code": nearest["harbor_code"],
            "harbor_name": nearest["harbor_name"],
            "note": f"自動割り当て（最近傍 {dist_km:.1f}km）",
        }

        if dist_km > args.max_km:
            print(f"  [警告] {slug} ({spot['name']}) → {nearest['harbor_name']} ({dist_km:.0f}km) ※距離大")
            warn_count += 1

        result_spots[slug] = entry

    # 既存の手動エントリ（note が "自動割り当て" でないもの）を優先
    manual_overrides = 0
    for slug, entry in existing_mapping.items():
        if entry.get("note") and "自動割り当て" not in entry["note"]:
            result_spots[slug] = entry  # 手動エントリで上書き
            manual_overrides += 1

    output = {
        "_meta": {
            "description": "spot slug → tide736.net 港コード マッピング",
            "harbor_code_format": "pc-hc（例: 神奈川=14, 千葉=12, 東京=13）",
            "source": "https://tide736.net/",
            "note": "自動生成（assign_harbor_mapping.py）。手動エントリは note から '自動割り当て' を外せば上書きされない。",
        },
        "spots": result_spots,
    }

    # サマリー表示
    # 港ごとのスポット数を集計
    from collections import Counter
    harbor_counts = Counter(v["harbor_code"] for v in result_spots.values())
    print(f"\n[サマリー]")
    print(f"  割り当て完了: {len(result_spots)} スポット")
    print(f"  手動エントリ（保持）: {manual_overrides} 件")
    print(f"  距離警告 ({args.max_km}km超): {warn_count} 件")
    print(f"\n[使用港]（上位10）")
    for code, count in harbor_counts.most_common(10):
        # 港名を取得
        name = next((h["harbor_name"] for h in harbors if h["harbor_code"] == code), code)
        print(f"  {code}  {name}  {count} スポット")

    if args.dry_run:
        print("\n[dry-run] ファイル保存をスキップしました")
        return

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n[保存] {OUTPUT_PATH}")
    print("\n次のステップ:")
    print("  python scripts/fetch_tides.py --dry-run  # データ取得テスト")


if __name__ == "__main__":
    main()
