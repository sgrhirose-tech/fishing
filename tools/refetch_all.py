#!/usr/bin/env python3
"""
スポットのアクセス情報・底質等深線・ハーバーコードを一括再取得する。

新規スポット作成後のセットアップ用（1件）、または spots_wip 全量処理に使う。
既存 spots/ の一括 harbor_code 補正には backfill_harbor_code.py を使うこと。

使い方:
  # 1件
  python tools/refetch_all.py --slug kamogawa-ko
  python tools/refetch_all.py --slug kamogawa-ko --dir spots_wip

  # spots_wip 全量
  python tools/refetch_all.py --dir spots_wip

  # ステップ個別スキップ
  python tools/refetch_all.py --dir spots_wip --skip-access
  python tools/refetch_all.py --dir spots_wip --skip-physical
  python tools/refetch_all.py --dir spots_wip --skip-harbor
"""

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
TOOLS_DIR  = REPO_ROOT / "tools"


def run_step(label: str, cmd: list) -> bool:
    print(f"  [{label}]", " ".join(str(c) for c in cmd[2:]))
    result = subprocess.run(cmd, cwd=str(REPO_ROOT))
    if result.returncode != 0:
        print(f"  → 失敗 (returncode={result.returncode})")
        return False
    return True


def process_slug(slug: str, spots_dir: Path,
                 skip_access: bool, skip_physical: bool, skip_harbor: bool) -> dict:
    py = sys.executable
    results: dict[str, bool] = {}

    if not skip_access:
        results["アクセス"] = run_step(
            "アクセス",
            [py, str(TOOLS_DIR / "refetch_access.py"),
             "--slug", slug, "--apply", "--spots-dir", str(spots_dir)],
        )

    if not skip_physical:
        results["底質・等深線"] = run_step(
            "底質・等深線",
            [py, str(TOOLS_DIR / "refetch_physical_data.py"),
             "--slug", slug, "--apply"],
        )

    if not skip_harbor:
        results["ハーバーコード"] = run_step(
            "ハーバーコード",
            [py, str(TOOLS_DIR / "backfill_harbor_code.py"),
             "--slug", slug, "--spots-dir", str(spots_dir)],
        )

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="スポットのアクセス情報・底質等深線・ハーバーコードを一括再取得"
    )
    parser.add_argument("--slug",           metavar="SLUG",
                        help="対象スポットのスラッグ（省略時は --dir 内を全件処理）")
    parser.add_argument("--dir",            metavar="DIR", default="spots",
                        help="対象ディレクトリ（デフォルト: spots）")
    parser.add_argument("--skip-access",    action="store_true",
                        help="アクセス情報をスキップ")
    parser.add_argument("--skip-physical",  action="store_true",
                        help="底質・等深線をスキップ")
    parser.add_argument("--skip-harbor",    action="store_true",
                        help="ハーバーコードをスキップ")
    args = parser.parse_args()

    spots_dir = Path(args.dir) if Path(args.dir).is_absolute() else REPO_ROOT / args.dir
    if not spots_dir.exists():
        print(f"[エラー] ディレクトリが見つかりません: {spots_dir}")
        sys.exit(1)

    # 対象スラッグ一覧
    if args.slug:
        slugs = [args.slug]
    else:
        slugs = sorted(p.stem for p in spots_dir.glob("*.json")
                       if not p.name.startswith("_"))
        if not slugs:
            print(f"[エラー] {spots_dir} に JSON ファイルがありません")
            sys.exit(1)
        print(f"[対象] {spots_dir.name}/ の {len(slugs)} 件を処理します\n")

    summary: dict[str, dict[str, bool]] = {}

    for i, slug in enumerate(slugs, 1):
        print(f"=== ({i}/{len(slugs)}) {slug} ===")
        summary[slug] = process_slug(
            slug, spots_dir,
            args.skip_access, args.skip_physical, args.skip_harbor,
        )
        print()

    # サマリー
    print("=" * 50)
    failed = [s for s, r in summary.items() if not all(r.values())]
    print(f"[完了] {len(slugs) - len(failed)}/{len(slugs)} 件成功")
    if failed:
        print("[失敗]")
        for slug in failed:
            ng = [k for k, v in summary[slug].items() if not v]
            print(f"  {slug}: {', '.join(ng)}")
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
