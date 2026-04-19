#!/usr/bin/env python3
"""
spot JSON と記事 index.md に git log の最終コミット日を書き込む初期化スクリプト。
一度だけ実行する。以降は手動 or バッチが各ファイルを更新する。

使い方:
    python scripts/set_file_dates.py                         # spots + articles 両方（git log）
    python scripts/set_file_dates.py --spots-only
    python scripts/set_file_dates.py --articles-only
    python scripts/set_file_dates.py --use-mtime             # ファイルの変更日時（mtime）を使う
    python scripts/set_file_dates.py --use-birthtime         # ファイルの作成日（macOS のみ）を使う
    python scripts/set_file_dates.py --use-birthtime --force # 既存値も上書き
    python scripts/set_file_dates.py --dry-run               # 書き込まず確認だけ
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

_ROOT = pathlib.Path(__file__).parent.parent


def git_date(path: pathlib.Path) -> str:
    """ファイルの最終コミット日を YYYY-MM-DD で返す。未コミットなら空文字。"""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%cs", "--", str(path)],
        capture_output=True, text=True, cwd=_ROOT,
    )
    return result.stdout.strip()


def mtime_date(path: pathlib.Path) -> str:
    """ファイルの mtime を YYYY-MM-DD で返す。"""
    ts = os.path.getmtime(path)
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def birthtime_date(path: pathlib.Path) -> str:
    """ファイルの作成日を YYYY-MM-DD で返す（macOS の st_birthtime）。非対応環境は mtime にフォールバック。"""
    st = os.stat(path)
    ts = getattr(st, "st_birthtime", None) or st.st_mtime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _get_date(path: pathlib.Path, use_mtime: bool, use_birthtime: bool) -> str:
    if use_birthtime:
        return birthtime_date(path)
    if use_mtime:
        return mtime_date(path)
    return git_date(path)


# ---------------------------------------------------------------------------
# Spot JSON
# ---------------------------------------------------------------------------

def process_spots(dry_run: bool, use_mtime: bool, use_birthtime: bool, force: bool) -> int:
    spots_dir = _ROOT / "spots"
    count = 0
    for p in sorted(spots_dir.glob("*.json")):
        if p.name.startswith("_"):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("updated_at") and not force:
            continue
        date = _get_date(p, use_mtime, use_birthtime)
        if not date:
            print(f"  [スキップ] {p.name} — 日付取得失敗")
            continue
        if data.get("updated_at") == date:
            continue
        data["updated_at"] = date
        if not dry_run:
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  {p.name}: {date}")
        count += 1
    return count


# ---------------------------------------------------------------------------
# Article index.md
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r'^---\n(.*?)\n---\n', re.DOTALL)


def add_updated_to_frontmatter(content: str, date: str, force: bool) -> str | None:
    """frontmatter に updated: date を追記して返す。すでにあれば None（force 時は上書き）。"""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return f"---\nupdated: {date}\n---\n{content}"
    fm = m.group(1)
    existing = re.search(r'^updated\s*:\s*(\S+)', fm, re.MULTILINE)
    if existing:
        if not force:
            return None
        if existing.group(1) == date:
            return None
        new_fm = re.sub(r'^(updated\s*:).*$', f'\\g<1> {date}', fm, flags=re.MULTILINE)
    else:
        new_fm = fm + f"\nupdated: {date}"
    return content[:3] + "\n" + new_fm + "\n---\n" + content[m.end():]


def process_articles(dry_run: bool, use_mtime: bool, use_birthtime: bool, force: bool) -> int:
    articles_dir = _ROOT / "articles"
    count = 0
    for md in sorted(articles_dir.rglob("index.md")):
        content = md.read_text(encoding="utf-8")
        date = _get_date(md, use_mtime, use_birthtime)
        if not date:
            print(f"  [スキップ] {md.relative_to(_ROOT)} — 日付取得失敗")
            continue
        new_content = add_updated_to_frontmatter(content, date, force)
        if new_content is None:
            continue
        if not dry_run:
            md.write_text(new_content, encoding="utf-8")
        print(f"  {md.relative_to(_ROOT)}: {date}")
        count += 1
    return count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spots-only",     action="store_true")
    parser.add_argument("--articles-only",  action="store_true")
    parser.add_argument("--use-mtime",      action="store_true",
                        help="git log の代わりにファイルの mtime（変更日時）を使う")
    parser.add_argument("--use-birthtime",  action="store_true",
                        help="ファイルの作成日を使う（macOS 専用。他環境は mtime にフォールバック）")
    parser.add_argument("--force",          action="store_true",
                        help="既存の updated / updated_at も上書きする")
    parser.add_argument("--dry-run",        action="store_true")
    args = parser.parse_args()

    do_spots    = not args.articles_only
    do_articles = not args.spots_only

    if args.use_birthtime:
        mode = "birthtime（作成日）"
    elif args.use_mtime:
        mode = "mtime（変更日時）"
    else:
        mode = "git log"
    print(f"[モード] {mode}{'  [FORCE]' if args.force else ''}{'  [DRY-RUN]' if args.dry_run else ''}\n")

    total = 0
    if do_spots:
        print("=== Spot JSON ===")
        total += process_spots(args.dry_run, args.use_mtime, args.use_birthtime, args.force)

    if do_articles:
        print("\n=== Article index.md ===")
        total += process_articles(args.dry_run, args.use_mtime, args.use_birthtime, args.force)

    print(f"\n完了: {total} ファイル更新")


if __name__ == "__main__":
    main()

import argparse
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

_ROOT = pathlib.Path(__file__).parent.parent


def git_date(path: pathlib.Path) -> str:
    """ファイルの最終コミット日を YYYY-MM-DD で返す。未コミットなら空文字。"""
    result = subprocess.run(
        ["git", "log", "-1", "--format=%cs", "--", str(path)],
        capture_output=True, text=True, cwd=_ROOT,
    )
    return result.stdout.strip()


def mtime_date(path: pathlib.Path) -> str:
    """ファイルの mtime を YYYY-MM-DD で返す。"""
    ts = os.path.getmtime(path)
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Spot JSON
# ---------------------------------------------------------------------------

def process_spots(dry_run: bool, use_mtime: bool, force: bool) -> int:
    spots_dir = _ROOT / "spots"
    count = 0
    for p in sorted(spots_dir.glob("*.json")):
        if p.name.startswith("_"):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("updated_at") and not force:
            continue  # すでに設定済み（--force なし）
        date = mtime_date(p) if use_mtime else git_date(p)
        if not date:
            print(f"  [スキップ] {p.name} — 日付取得失敗")
            continue
        if data.get("updated_at") == date:
            continue  # 値が同じなら不要
        data["updated_at"] = date
        if not dry_run:
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  {p.name}: {date}")
        count += 1
    return count


# ---------------------------------------------------------------------------
# Article index.md
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r'^---\n(.*?)\n---\n', re.DOTALL)


def add_updated_to_frontmatter(content: str, date: str, force: bool) -> str | None:
    """frontmatter に updated: date を追記して返す。すでにあれば None（force 時は上書き）。"""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return f"---\nupdated: {date}\n---\n{content}"
    fm = m.group(1)
    existing = re.search(r'^updated\s*:\s*(\S+)', fm, re.MULTILINE)
    if existing:
        if not force:
            return None
        if existing.group(1) == date:
            return None
        new_fm = re.sub(r'^(updated\s*:).*$', f'\\g<1> {date}', fm, flags=re.MULTILINE)
    else:
        new_fm = fm + f"\nupdated: {date}"
    return content[:3] + "\n" + new_fm + "\n---\n" + content[m.end():]


def process_articles(dry_run: bool, use_mtime: bool, force: bool) -> int:
    articles_dir = _ROOT / "articles"
    count = 0
    for md in sorted(articles_dir.rglob("index.md")):
        content = md.read_text(encoding="utf-8")
        date = mtime_date(md) if use_mtime else git_date(md)
        if not date:
            print(f"  [スキップ] {md.relative_to(_ROOT)} — 日付取得失敗")
            continue
        new_content = add_updated_to_frontmatter(content, date, force)
        if new_content is None:
            continue
        if not dry_run:
            md.write_text(new_content, encoding="utf-8")
        print(f"  {md.relative_to(_ROOT)}: {date}")
        count += 1
    return count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--spots-only",    action="store_true")
    parser.add_argument("--articles-only", action="store_true")
    parser.add_argument("--use-mtime",     action="store_true",
                        help="git log の代わりにファイルの mtime を使う")
    parser.add_argument("--force",         action="store_true",
                        help="既存の updated_at も上書きする")
    parser.add_argument("--dry-run",       action="store_true")
    args = parser.parse_args()

    do_spots    = not args.articles_only
    do_articles = not args.spots_only

    mode = "mtime" if args.use_mtime else "git log"
    print(f"[モード] {mode}{'  [FORCE]' if args.force else ''}{'  [DRY-RUN]' if args.dry_run else ''}\n")

    total = 0
    if do_spots:
        print("=== Spot JSON ===")
        total += process_spots(args.dry_run, args.use_mtime, args.force)

    if do_articles:
        print("\n=== Article index.md ===")
        total += process_articles(args.dry_run, args.use_mtime, args.force)

    print(f"\n完了: {total} ファイル更新")


if __name__ == "__main__":
    main()
