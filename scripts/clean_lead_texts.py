#!/usr/bin/env python3
"""
既存の info.lead_text に含まれる前置き・区切り線・後書きを一括除去する。
API は呼ばない。ローカルの spots/*.json を直接書き換える。

使い方:
    python scripts/clean_lead_texts.py           # 変更件数を確認して実行
    python scripts/clean_lead_texts.py --dry-run # 変更内容だけ表示して書き込まない
"""
import argparse
import json
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from app.lead_gen import _clean_text  # noqa: E402

_SPOTS_DIR = _REPO_ROOT / "spots_wip"


def main() -> None:
    parser = argparse.ArgumentParser(description="lead_text 一括クリーニング")
    parser.add_argument("--dry-run", action="store_true", help="書き込まずに変更内容だけ表示")
    args = parser.parse_args()

    changed = 0
    cleared = 0
    skipped = 0

    for p in sorted(_SPOTS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[警告] {p.name} 読み込みスキップ: {e}")
            continue

        info = data.get("info") or {}
        original = info.get("lead_text", "")
        if not original:
            skipped += 1
            continue

        cleaned = _clean_text(original)

        if cleaned == original:
            skipped += 1
            continue

        slug = data.get("slug", p.stem)
        if not cleaned:
            # クリーニング後に空になった → lead_text を削除
            print(f"[削除] {slug}")
            print(f"  before: {original[:80]}…" if len(original) > 80 else f"  before: {original}")
            cleared += 1
        else:
            print(f"[修正] {slug}  {len(original)}字 → {len(cleaned)}字")
            print(f"  before: {original[:60]}…" if len(original) > 60 else f"  before: {original}")
            print(f"  after : {cleaned[:60]}…" if len(cleaned) > 60 else f"  after : {cleaned}")
            changed += 1

        if not args.dry_run:
            if cleaned:
                data.setdefault("info", {})["lead_text"] = cleaned
            else:
                data.get("info", {}).pop("lead_text", None)
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}完了: 修正={changed} 削除={cleared} 変更なし={skipped}")


if __name__ == "__main__":
    main()
