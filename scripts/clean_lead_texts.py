#!/usr/bin/env python3
"""
既存の info.lead_text に含まれる前置き・区切り線・後書きを一括除去する。
API は呼ばない。ローカルの spots/*.json（または --dir で指定したディレクトリ）を直接書き換える。

使い方:
    python scripts/clean_lead_texts.py                   # spots/ を処理
    python scripts/clean_lead_texts.py --dir spots_wip   # spots_wip/ を処理
    python scripts/clean_lead_texts.py --dry-run         # 変更内容だけ表示して書き込まない
"""
import argparse
import json
import pathlib
import sys

_REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from app.lead_gen import _clean_text  # noqa: E402

# 80字未満かつ釣り禁止通知でもないテキストは不完全と見なして削除
_MIN_CHARS = 80
_BAN_RE    = __import__("re").compile(r'釣り禁止|立入禁止|釣禁')


def _should_delete(text: str) -> bool:
    """クリーニング後でも使い物にならないテキストか判定する。"""
    if not text:
        return True
    # 短すぎる（釣り禁止通知は除く）
    if len(text) < _MIN_CHARS and not _BAN_RE.search(text):
        return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="lead_text 一括クリーニング")
    parser.add_argument("--dry-run", action="store_true", help="書き込まずに変更内容だけ表示")
    parser.add_argument("--dir", default="spots", help="処理対象ディレクトリ名（デフォルト: spots）")
    args = parser.parse_args()

    spots_dir = _REPO_ROOT / args.dir
    if not spots_dir.is_dir():
        print(f"[エラー] ディレクトリが見つかりません: {spots_dir}", file=sys.stderr)
        sys.exit(1)

    changed = 0
    cleared = 0
    skipped = 0

    for p in sorted(spots_dir.glob("*.json")):
        if p.name.startswith("_") or p.name.startswith("."):
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

        # クリーニング後も不完全なら削除扱い
        if _should_delete(cleaned):
            cleaned = ""

        if cleaned == original:
            skipped += 1
            continue

        slug = data.get("slug", p.stem)
        if not cleaned:
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
