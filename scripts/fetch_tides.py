#!/usr/bin/env python3
"""
潮汐データ 月次バッチスクリプト。

harbor_list.json に登録された全港の潮汐データを tide736.net API から取得し、
data/tides/{harbor_code}_{YYYY-MM}.json に保存して GitHub REST API 経由でプッシュする。

tide736.net は個人運営の無料サービスのため、アクセス頻度を最小化する設計とする。
rg=month を使って1リクエストで1ヶ月分を取得し、月1回のみ実行する。

Render Cron Job スケジュール:
  tsuricast-tides-monthly:  "0 18 1 * *"   # 毎月2日 AM3:00 JST（当月分）
  tsuricast-tides-prefetch: "0 18 24 * *"  # 毎月25日 AM3:00 JST（翌月分を先行取得）

使い方（ローカル）:
    python scripts/fetch_tides.py                    # 当月＋翌月を取得してGitHubへプッシュ
    python scripts/fetch_tides.py --month next       # 翌月のみ取得
    python scripts/fetch_tides.py --harbor 14-5      # 特定港のみ取得
    python scripts/fetch_tides.py --force            # 既存キャッシュも上書き
    python scripts/fetch_tides.py --dry-run          # 取得のみ、GitHubへプッシュしない
"""

import argparse
import base64
import json
import os
import pathlib
import ssl
import sys
import time
import urllib.request
import urllib.error

# SSL 証明書検証を無効化（macOS の証明書ストア問題 / 自己署名証明書対策）
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE
from datetime import datetime, timezone, timedelta

_REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

HARBOR_LIST_PATH = _REPO_ROOT / "data" / "harbor_list.json"
TIDES_DIR = _REPO_ROOT / "data" / "tides"

TIDE_API_BASE = "https://api.tide736.net/get_tide.php"
USER_AGENT = "TsuricastBot/1.0 (personal-use)"
REQUEST_TIMEOUT = 10
REQUEST_INTERVAL = 1.0  # 港ごとの待機秒数（連続アクセス防止）
RETRY_COUNT = 3
RETRY_WAIT = 10  # 通常リトライ待機秒数
RETRY_WAIT_429 = 30  # rate limit 時の待機秒数

GITHUB_API = "https://api.github.com"
GITHUB_OWNER = "sgrhirose-tech"
GITHUB_REPO = "fishing"
GITHUB_BRANCH = "master"

JST = timezone(timedelta(hours=9))


# ─────────────────────────────────────────────────────────
# 港リストの読み込み
# ─────────────────────────────────────────────────────────

def load_all_harbors() -> list[dict]:
    """harbor_list.json から全港リストを返す（全国対応）。"""
    with open(HARBOR_LIST_PATH, encoding="utf-8") as f:
        data = json.load(f)
    harbors = []
    for h in data.get("harbors", []):
        code = h.get("harbor_code") or f"{h['pc']}-{h['hc']}"
        pc, hc = code.split("-", 1)
        harbors.append({
            "harbor_code": code,
            "harbor_name": h.get("harbor_name", code),
            "pc": pc,
            "hc": hc,
        })
    return harbors


# ─────────────────────────────────────────────────────────
# tide736.net API 呼び出し
# ─────────────────────────────────────────────────────────

def fetch_month_raw(pc: str, hc: str, year: int, month: int) -> dict | None:
    """
    tide736.net から指定港・指定月の潮汐データを取得して生のレスポンスdictを返す。
    失敗時は None を返す（リトライ3回）。
    """
    url = (
        f"{TIDE_API_BASE}"
        f"?pc={pc}&hc={hc}&yr={year}&mn={month}&dy=1&rg=month"
    )
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", USER_AGENT)

    for attempt in range(1, RETRY_COUNT + 1):
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_SSL_CTX) as resp:
                body = resp.read().decode("utf-8")
            data = json.loads(body)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"    [rate limit] {RETRY_WAIT_429}秒待機して再試行 (試行 {attempt}/{RETRY_COUNT})")
                time.sleep(RETRY_WAIT_429)
            elif e.code >= 500:
                print(f"    [HTTP {e.code}] {RETRY_WAIT}秒待機して再試行 (試行 {attempt}/{RETRY_COUNT})")
                time.sleep(RETRY_WAIT)
            else:
                print(f"    [HTTP {e.code}] スキップ")
                return None
        except Exception as e:
            print(f"    [エラー] {e} (試行 {attempt}/{RETRY_COUNT})")
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_WAIT)

    return None


# ─────────────────────────────────────────────────────────
# レスポンス正規化
# ─────────────────────────────────────────────────────────

def normalize(raw: dict, harbor_code: str, harbor_name: str, month_str: str) -> dict:
    """
    tide736.net のレスポンスを Tsuricast 内部フォーマットに正規化する。

    入力: raw["tide"]["chart"] = {"YYYY-MM-DD": {...}, ...}
    出力: {"_meta": {...}, "days": {"YYYY-MM-DD": {...}, ...}}
    """
    chart = raw.get("tide", {}).get("chart", {})
    days: dict[str, dict] = {}

    for date_str, day_raw in chart.items():
        sun = day_raw.get("sun", {})
        moon = day_raw.get("moon", {})

        # moon_age は文字列で来ることがある
        try:
            moon_age = float(moon.get("age", 0))
        except (TypeError, ValueError):
            moon_age = None

        hourly = [
            {"time": item["time"], "cm": item["cm"]}
            for item in day_raw.get("tide", [])
        ]
        ebb_raw = [
            {"time": item["time"], "cm": item["cm"]}
            for item in day_raw.get("ebb", [])
        ]
        # API が ebb を返さない場合は hourly の局所最小値から導出する
        if not ebb_raw and len(hourly) >= 3:
            cms = [h["cm"] for h in hourly]
            ebb_raw = [
                {"time": hourly[i]["time"], "cm": round(cms[i], 1)}
                for i in range(1, len(cms) - 1)
                if cms[i] < cms[i - 1] and cms[i] < cms[i + 1]
            ]

        days[date_str] = {
            "tide_name": moon.get("title", ""),
            "sunrise": sun.get("rise", ""),
            "sunset": sun.get("set", ""),
            "moon_age": moon_age,
            "flood": [
                {"time": item["time"], "cm": item["cm"]}
                for item in day_raw.get("flood", [])
            ],
            "ebb": ebb_raw,
            "hourly": hourly,
        }

    return {
        "_meta": {
            "harbor_code": harbor_code,
            "harbor_name": harbor_name,
            "month": month_str,
            "fetched_at": datetime.now(JST).isoformat(),
        },
        "days": days,
    }


# ─────────────────────────────────────────────────────────
# ローカル保存
# ─────────────────────────────────────────────────────────

def cache_file_path(harbor_code: str, month_str: str) -> pathlib.Path:
    """data/tides/{harbor_code}_{YYYY-MM}.json のパスを返す。"""
    filename = f"{harbor_code}_{month_str}.json"
    return TIDES_DIR / filename


def save_local(data: dict, harbor_code: str, month_str: str) -> pathlib.Path:
    """data/tides/ に JSON ファイルとして保存する。"""
    TIDES_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_file_path(harbor_code, month_str)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ─────────────────────────────────────────────────────────
# GitHub API プッシュ（facilities.py と同じ方式）
# ─────────────────────────────────────────────────────────

def delete_file_from_github(token: str, github_path: str, sha: str, commit_message: str) -> None:
    """GitHub REST API でファイルを削除する。"""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{github_path}"
    payload = {
        "message": commit_message,
        "sha": sha,
        "branch": GITHUB_BRANCH,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="DELETE")
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        resp_data = json.loads(resp.read().decode("utf-8"))
        commit_sha = resp_data.get("commit", {}).get("sha", "?")
        print(f"    [GitHub] 削除完了: commit {commit_sha[:8]}")


def push_file_to_github(token: str, local_path: pathlib.Path, github_path: str, commit_message: str) -> None:
    """GitHub REST API でファイルをコミット・プッシュする。"""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{github_path}"

    sha = None
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            current = json.loads(resp.read().decode("utf-8"))
            sha = current.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            pass  # 新規ファイル
        else:
            raise

    with open(local_path, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("ascii")

    payload: dict = {
        "message": commit_message,
        "content": content_b64,
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="PUT")
    with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as resp:
        resp_data = json.loads(resp.read().decode("utf-8"))
        commit_sha = resp_data.get("commit", {}).get("sha", "?")
        print(f"    [GitHub] プッシュ完了: commit {commit_sha[:8]}")


# ─────────────────────────────────────────────────────────
# 古いキャッシュの削除
# ─────────────────────────────────────────────────────────

def cleanup_old_tides(token: str | None, dry_run: bool) -> None:
    """
    当月より古い data/tides/*.json をローカルと GitHub から削除する。

    dry_run=True の場合は削除対象を表示するだけで削除しない。
    token=None の場合はローカル削除のみ（GitHub 削除はスキップ）。
    """
    now = datetime.now(JST)
    # 当月の YYYY-MM（これより古いファイルを削除）
    current_month_str = f"{now.year}-{now.month:02d}"

    if not TIDES_DIR.exists():
        return

    targets: list[pathlib.Path] = []
    for p in sorted(TIDES_DIR.glob("*.json")):
        # ファイル名: {harbor_code}_{YYYY-MM}.json
        # harbor_code は "-" を含む（例: 12-6）ので末尾から月を取る
        stem = p.stem  # e.g. "12-6_2026-02"
        parts = stem.rsplit("_", 1)
        if len(parts) != 2:
            continue
        month_str = parts[1]  # "YYYY-MM"
        if month_str < current_month_str:
            targets.append(p)

    if not targets:
        print("[クリーンアップ] 削除対象なし")
        return

    print(f"[クリーンアップ] {len(targets)} ファイルを削除します（{current_month_str} より古い月）")

    deleted_local = 0
    deleted_github = 0
    error_count = 0

    for p in targets:
        github_path = f"data/tides/{p.name}"
        month_label = p.stem.rsplit("_", 1)[-1]
        print(f"  削除: {p.name}", end=" ... ", flush=True)

        if dry_run:
            print("(dry-run)")
            continue

        # ローカル削除
        try:
            p.unlink()
            deleted_local += 1
        except OSError as e:
            print(f"[ローカル削除エラー] {e}")
            error_count += 1
            continue

        # GitHub 削除
        if token is None:
            print("ローカル削除済み（GitHub スキップ）")
            continue

        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{github_path}"
        sha = None
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
                sha = json.loads(resp.read().decode("utf-8")).get("sha")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print("ローカル削除済み（GitHub に存在しない）")
                continue
            else:
                print(f"[GitHub 取得エラー {e.code}]")
                error_count += 1
                continue
        except Exception as e:
            print(f"[GitHub 取得エラー] {e}")
            error_count += 1
            continue

        try:
            delete_file_from_github(
                token, github_path, sha,
                f"chore: 潮汐キャッシュ削除 {p.stem.rsplit('_', 1)[0]} {month_label}（月次クリーンアップ）",
            )
            deleted_github += 1
        except Exception as e:
            print(f"[GitHub 削除エラー] {e}")
            error_count += 1

    if not dry_run:
        print(f"[クリーンアップ完了] ローカル: {deleted_local} 件, GitHub: {deleted_github} 件, エラー: {error_count} 件")


# ─────────────────────────────────────────────────────────
# 対象月リストの決定
# ─────────────────────────────────────────────────────────

def target_months(mode: str) -> list[tuple[int, int, str]]:
    """
    取得対象の月リストを返す。各要素は (year, month, 'YYYY-MM')。

    mode:
      "both"  → 当月 + 翌月（デフォルト）
      "current" → 当月のみ
      "next"  → 翌月のみ
    """
    now = datetime.now(JST)
    current = (now.year, now.month)
    if now.month == 12:
        nxt = (now.year + 1, 1)
    else:
        nxt = (now.year, now.month + 1)

    month_list = []
    for y, m in ([current] if mode in ("both", "current") else []) + ([nxt] if mode in ("both", "next") else []):
        month_list.append((y, m, f"{y}-{m:02d}"))
    return month_list


# ─────────────────────────────────────────────────────────
# メイン処理
# ─────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="潮汐データ月次バッチスクリプト")
    parser.add_argument("--force", action="store_true",
                        help="既存キャッシュファイルを上書きする")
    parser.add_argument("--month", choices=["both", "current", "next"], default="both",
                        help="取得対象月: both=当月+翌月（デフォルト）, current=当月のみ, next=翌月のみ")
    parser.add_argument("--harbor", metavar="PC-HC",
                        help="特定港のみ取得（デバッグ用）。例: --harbor 14-5")
    parser.add_argument("--dry-run", action="store_true",
                        help="取得・保存のみ実行。GitHub へのプッシュはしない")
    parser.add_argument("--skip-cleanup", action="store_true",
                        help="古いキャッシュファイルの削除をスキップする")
    args = parser.parse_args()

    print("=== 潮汐データバッチ開始 ===")

    harbors = load_all_harbors()

    if not harbors:
        print("[情報] 港リストが空です。終了します。")
        print("  → python tools/fetch_harbor_list.py を実行して harbor_list.json を生成してください。")
        return

    if args.harbor:
        harbors = [h for h in harbors if h["harbor_code"] == args.harbor]
        if not harbors:
            print(f"[エラー] 港コード '{args.harbor}' が見つかりません")
            sys.exit(1)

    months = target_months(args.month)
    print(f"[対象] {len(harbors)} 港 × {len(months)} ヶ月 = {len(harbors) * len(months)} リクエスト")
    for _, _, ms in months:
        print(f"  - {ms}")

    token = None
    if not args.dry_run:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            print("[エラー] GITHUB_TOKEN が設定されていません。--dry-run で実行するか環境変数を設定してください")
            sys.exit(1)

    success_count = 0
    skip_count = 0
    error_count = 0
    pushed_paths: list[tuple[pathlib.Path, str]] = []  # (local_path, github_path)

    total = len(harbors) * len(months)
    idx = 0
    for harbor in harbors:
        for year, month, month_str in months:
            idx += 1
            code = harbor["harbor_code"]
            name = harbor["harbor_name"]
            print(f"  [{idx:3d}/{total}] {code} {name} {month_str}", end=" ... ", flush=True)

            cache_path = cache_file_path(code, month_str)
            if cache_path.exists() and not args.force:
                print("スキップ（既存）")
                skip_count += 1
                continue

            raw = fetch_month_raw(harbor["pc"], harbor["hc"], year, month)
            if raw is None:
                print("取得失敗")
                error_count += 1
                if idx < total:
                    time.sleep(REQUEST_INTERVAL)
                continue

            normalized = normalize(raw, code, name, month_str)
            save_local(normalized, code, month_str)
            day_count = len(normalized.get("days", {}))
            print(f"取得完了（{day_count}日分）")
            success_count += 1

            github_path = f"data/tides/{code}_{month_str}.json"
            pushed_paths.append((cache_path, github_path))

            if idx < total:
                time.sleep(REQUEST_INTERVAL)

    print(f"\n[結果] 成功: {success_count}, スキップ: {skip_count}, 失敗: {error_count}")

    if args.dry_run:
        print("[dry-run] GitHub プッシュをスキップしました")
        if not args.skip_cleanup:
            print()
            cleanup_old_tides(None, dry_run=True)
        print("=== 潮汐データバッチ完了 ===")
        return

    if not pushed_paths:
        print("[情報] プッシュ対象ファイルなし")
        print("=== 潮汐データバッチ完了 ===")
        return

    print(f"\n[GitHub] {len(pushed_paths)} ファイルをプッシュします...")
    push_errors = 0
    for local_path, github_path in pushed_paths:
        harbor_code = local_path.stem.rsplit("_", 1)[0] if "_" in local_path.stem else local_path.stem
        month_label = local_path.stem.rsplit("_", 1)[-1] if "_" in local_path.stem else ""
        try:
            push_file_to_github(
                token,
                local_path,
                github_path,
                f"chore: 潮汐データ更新 {harbor_code} {month_label}（月次バッチ）",
            )
        except Exception as e:
            print(f"    [GitHub エラー] {github_path}: {e}")
            push_errors += 1

    if push_errors:
        print(f"[警告] {push_errors} ファイルのプッシュに失敗しました")

    if not args.skip_cleanup:
        print()
        cleanup_old_tides(token, dry_run=False)

    print("=== 潮汐データバッチ完了 ===")


if __name__ == "__main__":
    main()
