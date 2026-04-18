#!/usr/bin/env python3
"""
spot JSON と記事 index.md に git log の最終コミット日を書き込む初期化スクリプト。
一度だけ実行する。以降は手動 or バッチが各ファイルを更新する。

使い方:
    python scripts/set_file_dates.py              # spots + articles 両方
    python scripts/set_file_dates.py --spots-only
    python scripts/set_file_dates.py --articles-only
    python scripts/set_file_dates.py --dry-run    # 書き込まず確認だけ
"""
import argparse
import json
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


# ---------------------------------------------------------------------------
# Spot JSON
# ---------------------------------------------------------------------------

def process_spots(dry_run: bool) -> int:
    spots_dir = _ROOT / "spots"
    count = 0
    for p in sorted(spots_dir.glob("*.json")):
        if p.name.startswith("_"):
            continue
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("updated_at"):
            continue  # すでに設定済み
        date = git_date(p)
        if not date:
            print(f"  [スキップ] {p.name} — git log なし")
            continue
        data["updated_at"] = date
        if not dry_run:
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  {p.name}: {date}")
        count += 1
    return count


# ---------------------------------------------------------------------------
# Article index.md
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r'^---\n(.*?)\n---\n', re.DOTALL)


def add_updated_to_frontmatter(content: str, date: str) -> str | None:
    """frontmatter に updated: date を追記して返す。すでにあれば None。"""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        # frontmatter なし → 先頭に追加
        return f"---\nupdated: {date}\n---\n{content}"
    fm = m.group(1)
    if re.search(r'^updated\s*:', fm, re.MULTILINE):
        return None  # すでにある
    new_fm = fm + f"\nupdated: {date}"
    return content[:3] + "\n" + new_fm + "\n---\n" + content[m.end():]


def process_articles(dry_run: bool) -> int:
    articles_dir = _ROOT / "articles"
    count = 0
    for md in sorted(articles_dir.rglob("index.md")):
        content = md.read_text(encoding="utf-8")
        date = git_date(md)
        if not date:
            print(f"  [スキップ] {md.relative_to(_ROOT)} — git log なし")
            continue
        new_content = add_updated_to_frontmatter(content, date)
        if new_content is None:
            continue  # すでに設定済み
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
    parser.add_argument("--dry-run",       action="store_true")
    args = parser.parse_args()

    do_spots    = not args.articles_only
    do_articles = not args.spots_only

    if args.dry_run:
        print("[DRY-RUN] ファイル書き込みなし\n")

    total = 0
    if do_spots:
        print("=== Spot JSON ===")
        total += process_spots(args.dry_run)

    if do_articles:
        print("\n=== Article index.md ===")
        total += process_articles(args.dry_run)

    print(f"\n完了: {total} ファイル更新")


if __name__ == "__main__":
    main()
