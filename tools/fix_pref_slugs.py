#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
unadjusted/ と spots/ の JSON ファイルで、
prefecture と pref_slug が食い違っているものを一括修正する。

API 呼び出しなし。prefecture フィールドの値をもとに pref_slug を上書きする。

使い方:
  python tools/fix_pref_slugs.py          # ドライラン（変更内容を表示するだけ）
  python tools/fix_pref_slugs.py --apply  # 実際に上書き保存
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

PREF_SLUG_MAP = {
    "神奈川県": "kanagawa",
    "東京都":   "tokyo",
    "千葉県":   "chiba",
}

TARGET_DIRS = [
    REPO_ROOT / "unadjusted",
    REPO_ROOT / "spots",
]


def fix_file(path: Path, apply: bool) -> bool:
    try:
        spot = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  [スキップ] {path.name}: 読み込みエラー ({e})")
        return False

    area = spot.get("area", {})
    pref = area.get("prefecture", "")
    current_slug = area.get("pref_slug", "")
    correct_slug = PREF_SLUG_MAP.get(pref)

    if not correct_slug or correct_slug == current_slug:
        return False  # 変更不要

    print(f"  {path.name}: pref_slug {current_slug!r} → {correct_slug!r}  ({pref})")
    if apply:
        spot["area"]["pref_slug"] = correct_slug
        path.write_text(json.dumps(spot, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def main():
    apply = "--apply" in sys.argv
    if not apply:
        print("【ドライラン】変更対象を表示します。実際に保存するには --apply を付けてください。\n")

    total_changed = 0
    for d in TARGET_DIRS:
        files = sorted(f for f in d.glob("*.json") if not f.name.startswith("_"))
        if not files:
            continue
        print(f"--- {d.name}/ ({len(files)}件) ---")
        changed = sum(1 for f in files if fix_file(f, apply))
        total_changed += changed
        if changed == 0:
            print("  （変更対象なし）")

    print(f"\n{'修正完了' if apply else '変更対象'}: {total_changed} 件")
    if not apply and total_changed > 0:
        print("保存するには: python tools/fix_pref_slugs.py --apply")


if __name__ == "__main__":
    main()
