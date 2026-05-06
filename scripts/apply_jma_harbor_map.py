#!/usr/bin/env python3
"""
気象庁局コード spots/*.json 書き込みスクリプト。

data/jma_harbor_map.json の内容を読み込み、
各 spots/*.json の harbor_code に対応する jma_harbor_code を書き込む。

事前に create_jma_harbor_map.py でマッピングを生成・確認してから実行すること。

Usage:
    python scripts/apply_jma_harbor_map.py             # 全スポット更新
    python scripts/apply_jma_harbor_map.py --dry-run   # 更新内容を表示のみ（ファイル変更なし）
    python scripts/apply_jma_harbor_map.py --clear      # jma_harbor_code を削除（ロールバック）
"""

import argparse
import json
import pathlib
import sys

_ROOT = pathlib.Path(__file__).parent.parent
MAP_PATH  = _ROOT / "data" / "jma_harbor_map.json"
SPOTS_DIR = _ROOT / "spots"


def load_map() -> dict[str, str]:
    """harbor_code → jma_harbor_code の辞書を返す。"""
    data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    return {hc: e["jma_harbor_code"] for hc, e in data["harbors"].items()}


def main() -> None:
    parser = argparse.ArgumentParser(description="気象庁局コード spots 書き込み")
    parser.add_argument("--dry-run", action="store_true",
                        help="ファイルを変更せず差分を表示")
    parser.add_argument("--clear", action="store_true",
                        help="jma_harbor_code フィールドを削除（ロールバック）")
    args = parser.parse_args()

    if args.clear and args.dry_run:
        sys.exit("--clear と --dry-run は同時に指定できません")

    harbor_map = load_map() if not args.clear else {}

    updated = skipped = cleared = 0

    for f in sorted(SPOTS_DIR.glob("*.json")):
        if f.stem.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [ERROR] {f.name}: {e}")
            continue

        harbor_code = data.get("harbor_code")
        if not harbor_code:
            continue

        if args.clear:
            if "jma_harbor_code" not in data:
                continue
            new_data = {k: v for k, v in data.items() if k != "jma_harbor_code"}
            action = f"削除: {data['jma_harbor_code']!r}"
            cleared += 1
        else:
            jma_code = harbor_map.get(harbor_code)
            if not jma_code:
                skipped += 1
                continue
            current = data.get("jma_harbor_code")
            if current == jma_code:
                skipped += 1
                continue
            new_data = dict(data)
            new_data["jma_harbor_code"] = jma_code
            action = f"{current!r} → {jma_code!r}" if current else f"追加: {jma_code!r}"
            updated += 1

        slug = data.get("slug", f.stem)
        harbor_name = data.get("harbor_name", "")
        print(f"  [{slug}] {harbor_name} ({harbor_code}): {action}")

        if not args.dry_run:
            f.write_text(json.dumps(new_data, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")

    if args.clear:
        print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}削除: {cleared}スポット")
    else:
        print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}更新: {updated}スポット / スキップ: {skipped}スポット")


if __name__ == "__main__":
    main()
