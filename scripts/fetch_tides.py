#!/usr/bin/env python3
"""
潮汐データ 月次バッチスクリプト。

harbor_mapping.json に登録された全港の潮汐データを tide736.net API から取得し、
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
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

_REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

HARBOR_MAPPING_PATH = _REPO_ROOT / "data" / "harbor_mapping.json"
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
# harbor_mapping.json の読み込み
# ─────────────────────────────────────────────────────────

def load_harbor_mapping() -> dict:
    """harbor_mapping.json を読み込んで返す。"""
    with open(HARBOR_MAPPING_PATH, encoding="utf-8") as f:
        return json.load(f)


def unique_harbors(mapping: dict) -> list[dict]:
    """spot → harbor マッピングから重複を除いた港リストを返す。"""
    seen: set[str] = set()
    harbors = []
    for spot_data in mapping.get("spots", {}).values():
        code = spot_data["harbor_code"]
        if code not in seen:
            seen.add(code)
            pc, hc = code.split("-", 1)
            harbors.append({
                "harbor_code": code,
                "harbor_name": spot_data["harbor_name"],
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
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
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

        days[date_str] = {
            "tide_name": moon.get("title", ""),
            "sunrise": sun.get("rise", ""),
            "sunset": sun.get("set", ""),
            "moon_age": moon_age,
            "flood": [
                {"time": item["time"], "cm": item["cm"]}
                for item in day_raw.get("flood", [])
            ],
            "ebb": [
                {"time": item["time"], "cm": item["cm"]}
                for item in day_raw.get("ebb", [])
            ],
            "hourly": [
                {"time": item["time"], "cm": item["cm"]}
                for item in day_raw.get("tide", [])
            ],
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
        with urllib.request.urlopen(req, timeout=15) as resp:
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
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp_data = json.loads(resp.read().decode("utf-8"))
        commit_sha = resp_data.get("commit", {}).get("sha", "?")
        print(f"    [GitHub] プッシュ完了: commit {commit_sha[:8]}")


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
    args = parser.parse_args()

    print("=== 潮汐データバッチ開始 ===")

    mapping = load_harbor_mapping()
    harbors = unique_harbors(mapping)

    if not harbors:
        print("[情報] harbor_mapping.json に港が登録されていません。終了します。")
        print("  → data/harbor_mapping.json の spots に港コードを追加してください。")
        return

    if args.harbor:
        harbors = [h for h in harbors if h["harbor_code"] == args.harbor]
        if not harbors:
            print(f"[エラー] 港コード '{args.harbor}' が harbor_mapping.json に存在しません")
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
    print("=== 潮汐データバッチ完了 ===")


if __name__ == "__main__":
    main()
