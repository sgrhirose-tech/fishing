#!/usr/bin/env python3
"""
施設情報 週次バッチスクリプト。

全スポットの OSM 施設データ（駐車場・トイレ・釣具屋・コンビニ）を
Overpass API から取得して data/facilities.json に保存し、
GitHub REST API 経由でリポジトリにプッシュする。

Render Cron Job スケジュール: 0 18 * * 0  (UTC日曜18:00 = JST月曜03:00)

使い方（ローカル）:
    python scripts/fetch_facilities.py           # 全スポット取得してGitHubへプッシュ
    python scripts/fetch_facilities.py --dry-run # 取得のみ、GitHubへプッシュしない
"""

import argparse
import base64
import json
import os
import pathlib
import sys
import time
import urllib.request

# リポジトリルートを sys.path に追加（app.osm をインポートするため）
_REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from app.osm import fetch_nearby_facilities, AMENITY_SEARCH_RADIUS_M

SPOTS_DIR = _REPO_ROOT / "spots"
OUTPUT_PATH = _REPO_ROOT / "data" / "facilities.json"

GITHUB_API = "https://api.github.com"
GITHUB_OWNER = "sgrhirose-tech"
GITHUB_REPO = "fishing"
GITHUB_FILE_PATH = "data/facilities.json"
GITHUB_BRANCH = "master"

# スポット間の待機時間（秒）。Overpass API の負荷対策
REQUEST_INTERVAL_SEC = 2.5


def load_all_spots() -> list[dict]:
    """spots/*.json を全件読み込んで slug・lat・lon を返す。"""
    spots = []
    for p in sorted(SPOTS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            slug = data.get("slug") or p.stem
            loc = data.get("location", {})
            lat = loc.get("latitude")
            lon = loc.get("longitude")
            if lat is None or lon is None:
                continue
            spots.append({"slug": slug, "lat": float(lat), "lon": float(lon)})
        except Exception as e:
            print(f"  [警告] {p.name} の読み込みスキップ: {e}")
    return spots


def fetch_all(spots: list[dict]) -> dict:
    """全スポットの施設データを取得してdict形式で返す。"""
    from datetime import datetime, timezone, timedelta
    JST = timezone(timedelta(hours=9))
    result = {}
    total = len(spots)
    for i, spot in enumerate(spots, 1):
        slug = spot["slug"]
        lat, lon = spot["lat"], spot["lon"]
        print(f"  [{i:3d}/{total}] {slug} ({lat:.4f}, {lon:.4f})", end=" ... ", flush=True)
        try:
            facilities = fetch_nearby_facilities(lat, lon)
            result[slug] = facilities
            print(f"{len(facilities)} 件")
        except Exception as e:
            print(f"失敗: {e}")
            result[slug] = []
        if i < total:
            time.sleep(REQUEST_INTERVAL_SEC)

    result["_meta"] = {
        "generated_at": datetime.now(JST).isoformat(),
        "spot_count": len(spots),
        "radius_m": AMENITY_SEARCH_RADIUS_M,
    }
    return result


def save_local(data: dict) -> None:
    """data/facilities.json にローカル保存する。"""
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[保存] {OUTPUT_PATH}")


def push_to_github(token: str) -> None:
    """GitHub REST API で data/facilities.json をコミット・プッシュする。"""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"

    # 現在のファイル SHA を取得（更新に必要）
    sha = None
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            current = json.loads(resp.read().decode("utf-8"))
            sha = current.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print("[GitHub] 新規ファイルとして作成します")
        else:
            raise

    # ファイル内容を base64 エンコード
    with open(OUTPUT_PATH, "rb") as f:
        content_b64 = base64.b64encode(f.read()).decode("ascii")

    payload: dict = {
        "message": "chore: 施設情報バッチ更新（週次）",
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
        print(f"[GitHub] プッシュ完了: commit {commit_sha[:8]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="施設情報バッチ取得スクリプト")
    parser.add_argument("--dry-run", action="store_true",
                        help="取得のみ実行。GitHub へのプッシュはしない")
    args = parser.parse_args()

    print("=== 施設情報バッチ開始 ===")

    spots = load_all_spots()
    print(f"[読み込み] {len(spots)} スポット")

    data = fetch_all(spots)
    save_local(data)

    if args.dry_run:
        print("[dry-run] GitHub プッシュをスキップしました")
        return

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[エラー] GITHUB_TOKEN が設定されていません。--dry-run で実行するか環境変数を設定してください")
        sys.exit(1)

    push_to_github(token)
    print("=== 施設情報バッチ完了 ===")


if __name__ == "__main__":
    main()
