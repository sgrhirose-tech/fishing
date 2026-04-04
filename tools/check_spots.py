#!/usr/bin/env python3
"""
納品前チェックスクリプト。

spots_wip/ (デフォルト) または指定ディレクトリのスポット JSON を全件確認し、
スポット名・スラッグ・都道府県・市町村・港コードをログ表示しながら NG 項目を検出する。

使い方:
  python tools/check_spots.py                  # spots_wip/ を検査
  python tools/check_spots.py --dir spots      # spots/ を検査
  python tools/check_spots.py --ng-only        # NG 件のみ表示
  python tools/check_spots.py --slug kamogawa-ko
"""

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DEFAULT_DIR = "spots_wip"


def check_spot(spot: dict) -> list[str]:
    """NG 理由のリストを返す。空リストなら OK。"""
    reasons = []

    loc = spot.get("location", {})
    lat = loc.get("latitude")
    lon = loc.get("longitude")

    if lat is None or lon is None or (lat == 0 and lon == 0):
        reasons.append("座標が 0,0 または未設定")
    elif not (24 <= lat <= 46 and 122 <= lon <= 154):
        reasons.append(f"座標が日本国外 ({lat:.4f}, {lon:.4f})")

    sea_bearing = spot.get("physical_features", {}).get("sea_bearing_deg")
    if sea_bearing is None:
        reasons.append("sea_bearing_deg 未設定")
    elif not (0 <= sea_bearing <= 360):
        reasons.append(f"sea_bearing_deg 範囲外 ({sea_bearing})")

    ptype = spot.get("classification", {}).get("primary_type", "")
    if not ptype or ptype == "unknown":
        reasons.append("primary_type が unknown / 未設定")

    if not spot.get("harbor_code"):
        reasons.append("harbor_code 未設定")

    if not spot.get("info", {}).get("access"):
        reasons.append("access 未設定")

    if not spot.get("target_fish"):
        reasons.append("target_fish 空")

    return reasons


def fmt_name(name: str, width: int = 15) -> str:
    """全角考慮で幅を揃える（簡易）。"""
    count = 0
    chars = []
    for c in name:
        w = 2 if ord(c) > 0x7F else 1
        if count + w > width:
            chars.append("…")
            count += 1
            break
        chars.append(c)
        count += w
    return "".join(chars).ljust(width + (width - count))


def main() -> None:
    parser = argparse.ArgumentParser(description="納品前スポット一括チェック")
    parser.add_argument("--dir",     metavar="DIR", default=DEFAULT_DIR,
                        help=f"検査ディレクトリ（デフォルト: {DEFAULT_DIR}）")
    parser.add_argument("--ng-only", action="store_true",
                        help="NG 件のみ表示（OK 行を省略）")
    parser.add_argument("--slug",    metavar="SLUG",
                        help="1件のみ処理するスラッグ")
    args = parser.parse_args()

    spots_dir = Path(args.dir) if Path(args.dir).is_absolute() else REPO_ROOT / args.dir
    if not spots_dir.exists():
        print(f"[エラー] ディレクトリが見つかりません: {spots_dir}")
        sys.exit(1)

    paths = sorted(p for p in spots_dir.glob("*.json") if not p.name.startswith("_"))
    if args.slug:
        paths = [p for p in paths if p.stem == args.slug]
        if not paths:
            print(f"[エラー] slug '{args.slug}' が見つかりません")
            sys.exit(1)

    if not paths:
        print(f"JSON ファイルが見つかりません: {spots_dir}")
        sys.exit(1)

    print(f"=== {spots_dir.name}/ チェック結果 ({len(paths)}件) ===\n")

    # slug 重複チェック用
    slug_count: dict[str, int] = {}
    for p in paths:
        slug_count[p.stem] = slug_count.get(p.stem, 0) + 1

    ok_list: list[str] = []
    ng_list: list[tuple[str, str, list[str]]] = []  # (name, slug, reasons)

    for path in paths:
        try:
            spot = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[ERROR] {path.name}: {e}")
            continue

        name      = spot.get("name", "(無名)")
        slug      = spot.get("slug", path.stem)
        area      = spot.get("area", {})
        pref      = area.get("prefecture", "")
        pref_slug = area.get("pref_slug", "")
        city      = area.get("city", "")
        city_slug = area.get("city_slug", "")
        hcode     = spot.get("harbor_code", "")
        hname     = spot.get("harbor_name", "")

        reasons = check_spot(spot)
        if slug_count.get(slug, 0) > 1:
            reasons.append("slug 重複")

        harbor_str = f"{hname}({hcode})" if hcode else "(未設定)"
        pref_str   = f"{pref}({pref_slug})" if pref_slug else pref or "(未設定)"
        city_str   = f"{city}({city_slug})" if city_slug else city or "(未設定)"

        status = "[OK]" if not reasons else "[NG]"
        line = (
            f"{status} {fmt_name(name)}"
            f"  slug={slug:<22}"
            f"  pref={pref_str:<18}"
            f"  city={city_str:<18}"
            f"  harbor={harbor_str}"
        )

        if reasons:
            ng_list.append((name, slug, reasons))
            if not args.ng_only:
                print(line)
                print(f"     → NG: {' / '.join(reasons)}")
        else:
            ok_list.append(slug)
            if not args.ng_only:
                print(line)

    # NG only モードではここで NG 件だけ表示
    if args.ng_only:
        for name, slug, reasons in ng_list:
            area      = {}
            for p in paths:
                if p.stem == slug:
                    try:
                        area = json.loads(p.read_text(encoding="utf-8")).get("area", {})
                    except Exception:
                        pass
                    break
            pref      = area.get("prefecture", "")
            pref_slug = area.get("pref_slug", "")
            city      = area.get("city", "")
            city_slug = area.get("city_slug", "")
            spot_raw  = {}
            for p in paths:
                if p.stem == slug:
                    try:
                        spot_raw = json.loads(p.read_text(encoding="utf-8"))
                    except Exception:
                        pass
                    break
            hcode = spot_raw.get("harbor_code", "")
            hname = spot_raw.get("harbor_name", "")
            harbor_str = f"{hname}({hcode})" if hcode else "(未設定)"
            pref_str   = f"{pref}({pref_slug})" if pref_slug else pref or "(未設定)"
            city_str   = f"{city}({city_slug})" if city_slug else city or "(未設定)"
            print(
                f"[NG] {fmt_name(name)}"
                f"  slug={slug:<22}"
                f"  pref={pref_str:<18}"
                f"  city={city_str:<18}"
                f"  harbor={harbor_str}"
            )
            print(f"     → NG: {' / '.join(reasons)}")

    # サマリー
    print(f"\n── 完了 ── OK: {len(ok_list)}件 / NG: {len(ng_list)}件")

    if ng_list:
        print("\nNG スポット一覧:")
        for name, slug, reasons in ng_list:
            print(f"  {fmt_name(name)} ({slug})  {' / '.join(reasons)}")

    print("""
【目視確認チェックリスト】
□ 座標を Google Maps（📍 地図）で確認し、実在の釣り場と一致している
□ 海の向きが地形と合っている
□ 施設種別（primary_type）が実態に合っている
□ notes が釣り場の特徴を正確に表している（架空情報でない）
□ access の駅名・時間が実際と合っている
□ 釣り禁止・立入禁止スポットが含まれていない""")

    sys.exit(0 if not ng_list else 1)


if __name__ == "__main__":
    main()
