"""
spots/*.json（または spots_wip/*.json）の info.notes から魚種を抽出し、
各スポット JSON に target_fish フィールドを追加するバッチスクリプト。

使い方:
  python tools/extract_target_fish.py                     # spots/ を対象、実際に書き込み
  python tools/extract_target_fish.py --dir spots_wip     # spots_wip/ を対象
  python tools/extract_target_fish.py --dry-run           # 書き込みなし（確認用）
"""

import argparse
import json
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).parent.parent

# -------------------------------------------------------------------------
# 魚種パターン定義
# -------------------------------------------------------------------------

# 検索対象の表記（正規名 or エイリアス）→ 正規名 のマッピング。
# リスト順に検索するため、長い名前を先に記載すること（部分マッチ防止）。
FISH_NORMALIZE: dict[str, str] = {
    # 長い名前 / エイリアスを先に
    "アオリイカ":   "アオリイカ",
    "ソウダガツオ": "ソウダガツオ",
    "ウミタナゴ":   "ウミタナゴ",
    "イシガキダイ": "イシガキダイ",
    "コウイカ":     "コウイカ",
    "イシダイ":     "イシダイ",
    "シマアジ":     "シマアジ",
    "シロギス":     "シロギス",
    "タチウオ":     "タチウオ",
    "マゴチ":       "マゴチ",
    "マダイ":       "マダイ",
    "メジナ":       "メジナ",
    "クロダイ":     "クロダイ",
    "カサゴ":       "カサゴ",
    "カレイ":       "カレイ",
    "カマス":       "カマス",
    "メバル":       "メバル",
    "ヒラメ":       "ヒラメ",
    "サヨリ":       "サヨリ",
    "スズキ":       "スズキ",
    "イワシ":       "イワシ",
    "サバ":         "サバ",
    "ハゼ":         "ハゼ",
    "タコ":         "タコ",
    "アジ":         "アジ",
    "ブリ":         "ブリ",
    # エイリアス
    "チヌ":         "クロダイ",
    "シーバス":     "スズキ",
    "キス":         "シロギス",
    "イナダ":       "ブリ",
    "ワラサ":       "ブリ",
    "ワカシ":       "ブリ",
    "ショゴ":       "カンパチ",
    "キビレ":       "クロダイ",
}

# fish_master.json に含まれない追加エントリ（エイリアス解決後に登場しうるもの）
_EXTRA_MASTER_FISH = {"カンパチ", "イシガキダイ"}


def extract_fish_from_notes(notes: str) -> list[str]:
    """notes テキストから魚種名を抽出し、正規名のリストを返す（重複なし・出現順）。"""
    found: list[str] = []
    seen: set[str] = set()
    text = notes or ""
    for pattern, canonical in FISH_NORMALIZE.items():
        if pattern in text and canonical not in seen:
            found.append(canonical)
            seen.add(canonical)
    return found


def process_spots(spots_dir: pathlib.Path, dry_run: bool) -> dict[str, list[str]]:
    """
    指定ディレクトリの全スポット JSON を処理し、target_fish を書き込む。
    戻り値: {slug: [魚種名, ...]}
    """
    result: dict[str, list[str]] = {}

    json_files = sorted(f for f in spots_dir.glob("*.json") if not f.name.startswith("_"))
    if not json_files:
        print(f"[WARN] {spots_dir} に JSON ファイルが見つかりません")
        return result

    updated = 0
    skipped = 0

    for path in json_files:
        with open(path, encoding="utf-8") as f:
            spot = json.load(f)

        slug = spot.get("slug", path.stem)
        notes = spot.get("info", {}).get("notes", "")
        fish_list = extract_fish_from_notes(notes)

        result[slug] = fish_list

        existing = spot.get("target_fish")
        if existing == fish_list:
            skipped += 1
            continue

        if dry_run:
            print(f"  [DRY] {slug}: {fish_list}")
        else:
            spot["target_fish"] = fish_list
            with open(path, "w", encoding="utf-8") as f:
                json.dump(spot, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print(f"  [OK]  {slug}: {fish_list}")
        updated += 1

    label = "更新予定" if dry_run else "更新"
    print(f"\n{label}: {updated} 件 / スキップ（変更なし）: {skipped} 件")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="notes から魚種を抽出して target_fish に書き込む")
    parser.add_argument("--dir", choices=["spots", "spots_wip"], default="spots",
                        help="対象ディレクトリ（デフォルト: spots）")
    parser.add_argument("--dry-run", action="store_true",
                        help="JSON を書き込まず確認のみ")
    args = parser.parse_args()

    spots_dir = REPO_ROOT / args.dir
    print(f"[開始] 対象: {spots_dir}  dry-run: {args.dry_run}")

    process_spots(spots_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
