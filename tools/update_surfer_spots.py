"""
update_surfer_spots.py

OSM の sport=surfing タグを使って spots/ の surfer_spot フィールドを更新する。

使い方:
  python tools/update_surfer_spots.py            # ドライラン（デフォルト）
  python tools/update_surfer_spots.py --apply    # spots/ に上書き保存
  python tools/update_surfer_spots.py --slug katase  # 1件のみ
  python tools/update_surfer_spots.py --force    # 既に true のスポットも再確認
"""

import sys, json, time, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from pythonista_spot_tools import _overpass_get  # 既存 Overpass ヘルパーを再利用

REPO_ROOT = Path(__file__).parent.parent
SEARCH_RADIUS_M = 500


def is_surf_spot(lat, lon):
    """座標から半径 SEARCH_RADIUS_M 以内に OSM sport=surfing があれば True。"""
    query = (
        f"[out:json][timeout:25];"
        f"(node[\"sport\"=\"surfing\"](around:{SEARCH_RADIUS_M},{lat},{lon});"
        f"way[\"sport\"=\"surfing\"](around:{SEARCH_RADIUS_M},{lat},{lon});"
        f"relation[\"sport\"=\"surfing\"](around:{SEARCH_RADIUS_M},{lat},{lon}););"
        f"out count;"
    )
    try:
        result = _overpass_get(query)
        return (
            len(result) > 0
            and result[0].get("tags", {}).get("total", "0") != "0"
        )
    except Exception as e:
        print(f"    Overpass エラー: {e}")
        return None  # None = 判定不能（上書きしない）


def main():
    parser = argparse.ArgumentParser(description="OSM sport=surfing で surfer_spot を更新")
    parser.add_argument("--apply",  action="store_true", help="spots/ に上書き保存（デフォルト: ドライラン）")
    parser.add_argument("--slug",   help="1件のみ処理")
    parser.add_argument("--force",  action="store_true", help="既に true のスポットも再確認")
    args = parser.parse_args()

    spots_dir = REPO_ROOT / "spots"

    if args.slug:
        files = [spots_dir / f"{args.slug}.json"]
        files = [f for f in files if f.exists()]
        if not files:
            print(f"{args.slug}.json が見つかりません: {spots_dir}")
            return
    else:
        files = sorted(f for f in spots_dir.glob("*.json") if not f.name.startswith("_"))

    changed = 0
    for path in files:
        spot = json.loads(path.read_text(encoding="utf-8"))
        lat = spot["location"]["latitude"]
        lon = spot["location"]["longitude"]
        current = spot.get("physical_features", {}).get("surfer_spot", False)

        # 砂浜以外はスキップ（漁港・磯など非砂浜スポットの誤判定を防ぐ）
        primary_type = spot.get("classification", {}).get("primary_type", "unknown")
        if primary_type != "sand_beach":
            print(f"  スキップ（{primary_type}）: {spot.get('name', path.stem)}")
            continue

        # 既に true で --force なしならスキップ
        if current and not args.force:
            print(f"  スキップ（既に true）: {spot.get('name', path.stem)}")
            continue

        surf = is_surf_spot(lat, lon)
        if surf is None:
            print(f"  エラー（スキップ）: {spot.get('name', path.stem)}")
            continue
        marker = " ★変更" if surf != current else ""
        label = "true ✓" if surf else "false"
        print(f"  {spot.get('name', path.stem)} ({path.stem}): {label}{marker}")

        if surf != current:
            changed += 1
            if args.apply:
                spot.setdefault("physical_features", {})["surfer_spot"] = surf
                path.write_text(
                    json.dumps(spot, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8"
                )

        time.sleep(3.0)  # Overpass レート制限対策（429/504 軽減）

    mode = "保存済み" if args.apply else "ドライラン"
    print(f"\n[{mode}] {changed}件変更対象")


if __name__ == "__main__":
    main()
