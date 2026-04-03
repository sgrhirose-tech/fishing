#!/usr/bin/env python3
"""
1スポットのアクセス情報・底質等深線・ハーバーコードを一括再取得する。

新規スポット作成後のセットアップ用。
既存スポットの一括補正には backfill_harbor_code.py を使うこと。

使い方:
  python tools/refetch_all.py --slug kamogawa-ko
  python tools/refetch_all.py --slug kamogawa-ko --skip-access
  python tools/refetch_all.py --slug kamogawa-ko --skip-physical
  python tools/refetch_all.py --slug kamogawa-ko --skip-harbor
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TOOLS_DIR  = REPO_ROOT / "tools"


def run_step(label: str, cmd: list) -> bool:
    print(f"\n=== {label} ===")
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if result.returncode != 0:
        print(f"[失敗] {label} (returncode={result.returncode})")
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="1スポットのアクセス情報・底質等深線・ハーバーコードを一括再取得"
    )
    parser.add_argument("--slug",           required=True, metavar="SLUG",
                        help="対象スポットのスラッグ")
    parser.add_argument("--skip-access",    action="store_true",
                        help="アクセス情報をスキップ")
    parser.add_argument("--skip-physical",  action="store_true",
                        help="底質・等深線をスキップ")
    parser.add_argument("--skip-harbor",    action="store_true",
                        help="ハーバーコードをスキップ")
    args = parser.parse_args()

    py   = sys.executable
    slug = args.slug
    results: dict[str, bool] = {}

    if not args.skip_access:
        results["アクセス情報"] = run_step(
            "アクセス情報",
            [py, str(TOOLS_DIR / "refetch_access.py"), "--slug", slug, "--apply"],
        )

    if not args.skip_physical:
        results["底質・等深線"] = run_step(
            "底質・等深線",
            [py, str(TOOLS_DIR / "refetch_physical_data.py"), "--slug", slug, "--apply"],
        )

    if not args.skip_harbor:
        results["ハーバーコード"] = run_step(
            "ハーバーコード",
            [py, str(TOOLS_DIR / "backfill_harbor_code.py"), "--slug", slug],
        )

    print("\n" + "=" * 40)
    all_ok = all(results.values())
    for label, ok in results.items():
        print(f"  [{'OK' if ok else 'NG'}] {label}")
    print(f"\n[{'完了' if all_ok else '一部失敗'}] {slug}")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
