#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
更新済み TSV を読み込み、spots/*.json の info.notes を一括更新する。

使い方:
    python tools/import_notes_tsv.py --input tsv/notes_updated/*.tsv [--dry-run]

注意:
    更新対象は spots/ のみ（spots_wip/ は変更しない）
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT  = Path(__file__).parent.parent
SPOTS_DIR  = REPO_ROOT / "spots"


def load_tsv(path: Path) -> dict[str, str]:
    """TSV を読み込み {slug: notes} を返す。# 行・空行・ヘッダー行はスキップ。"""
    result = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        print(f"[ERROR] {path}: {e}", file=sys.stderr)
        return result

    for i, line in enumerate(lines, 1):
        if not line.strip() or line.startswith("#"):
            continue
        cols = line.split("\t")
        if cols[0] == "slug":  # ヘッダー行
            continue
        if len(cols) < 4:
            print(f"[WARNING] {path.name} 行{i}: 列数不足（{len(cols)}列）", file=sys.stderr)
            continue
        slug  = cols[0].strip()
        notes = cols[3].strip()
        if slug in result:
            print(f"[WARNING] {path.name}: slug 重複 '{slug}'（上書き）", file=sys.stderr)
        result[slug] = notes
    return result


def build_slug_index(spots_dir: Path) -> dict[str, Path]:
    index = {}
    for p in spots_dir.glob("*.json"):
        if not p.name.startswith("_"):
            index[p.stem] = p
    return index


def apply_update(json_path: Path, new_notes: str, dry_run: bool) -> tuple[str, str]:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    old_notes = data.get("info", {}).get("notes", "")
    if not dry_run:
        data.setdefault("info", {})["notes"] = new_notes
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return old_notes, new_notes


def main():
    parser = argparse.ArgumentParser(description="TSV から spots/*.json の notes を一括更新する")
    parser.add_argument(
        "--input", nargs="+", required=True,
        help="入力 TSV ファイル（複数指定可）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="JSON を書き換えず差分のみ表示",
    )
    args = parser.parse_args()

    # 全 TSV を統合
    merged: dict[str, str] = {}
    for fpath in args.input:
        p = Path(fpath)
        if not p.exists():
            print(f"[ERROR] ファイルが見つかりません: {p}", file=sys.stderr)
            sys.exit(1)
        entries = load_tsv(p)
        for slug, notes in entries.items():
            if slug in merged:
                print(f"[WARNING] slug 重複 '{slug}'（{p.name} で上書き）", file=sys.stderr)
            merged[slug] = notes

    if not merged:
        print("更新対象がありませんでした。")
        return

    slug_index = build_slug_index(SPOTS_DIR)

    updated = skipped = warned = 0
    for slug, new_notes in sorted(merged.items()):
        if slug not in slug_index:
            print(f"[WARNING] slug not found: {slug}", file=sys.stderr)
            warned += 1
            continue

        try:
            old_notes, _ = apply_update(slug_index[slug], new_notes, args.dry_run)
        except Exception as e:
            print(f"[ERROR] {slug}: {e}", file=sys.stderr)
            warned += 1
            continue

        if old_notes == new_notes:
            skipped += 1
        elif args.dry_run:
            print(f"[DRY-RUN] {slug}:\n  変更前: {old_notes}\n  変更後: {new_notes}")
            updated += 1
        else:
            print(f"[UPDATED] {slug}")
            updated += 1

    mode = "（DRY-RUN）" if args.dry_run else ""
    print(f"\n完了{mode}: updated {updated} / skipped(unchanged) {skipped} / warned {warned}")


if __name__ == "__main__":
    main()
