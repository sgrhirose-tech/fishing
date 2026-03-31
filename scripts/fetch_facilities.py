"""
周辺施設情報バッチスクリプト。

spots/*.json の全スポットに対して Overpass API で施設情報を取得し、
data/facilities.json に保存する。

--dry-run オプションを付けた場合は GitHub へのプッシュを行わない。

実行例:
  python scripts/fetch_facilities.py --dry-run
  GITHUB_TOKEN=your_token python scripts/fetch_facilities.py
"""

import argparse
import base64
import json
import os
import pathlib
import sys
import time
import urllib.request
from datetime import datetime, timezone, timedelta

# プロジェクトルートを sys.path に追加（app パッケージの import に必要）
REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.osm import fetch_nearby_facilities, AMENITY_SEARCH_RADIUS_M

SPOTS_DIR = REPO_ROOT / "spots"
OUTPUT_PATH = REPO_ROOT / "data" / "facilities.json"
REQUEST_INTERVAL_SEC = 1.2
GITHUB_API_URL = "https://api.github.com"
GITHUB_REPO = "sgrhirose-tech/fishing"
OUTPUT_PATH_IN_REPO = "data/facilities.json"
JST = timezone(timedelta(hours=9))


def load_spots() -> list[dict]:
    spots = []
    for path in sorted(SPOTS_DIR.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                spots.append(json.load(f))
        except Exception as e:
            print(f"  [WARN] {path.name} 読み込みスキップ: {e}")
    return spots


def build_facilities_json(spots: list[dict]) -> dict:
    JST_NOW = datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    result: dict = {
        "_meta": {
            "generated_at": JST_NOW,
            "spot_count": len(spots),
            "radius_m": AMENITY_SEARCH_RADIUS_M,
        }
    }

    for i, spot in enumerate(spots, 1):
        slug = spot.get("slug", "")
        loc = spot.get("location", {})
        lat = loc.get("latitude")
        lon = loc.get("longitude")
        name = spot.get("name", slug)

        if not slug or lat is None or lon is None:
            print(f"  [SKIP] slug/lat/lon 不足: {slug or '(no slug)'}")
            continue

        print(f"  [{i}/{len(spots)}] {name} ({slug})", end="", flush=True)
        facilities = fetch_nearby_facilities(lat, lon)
        result[slug] = facilities
        print(f" → {len(facilities)} 件")

        if i < len(spots):
            time.sleep(REQUEST_INTERVAL_SEC)

    return result


def push_to_github(token: str, content: str) -> None:
    """GitHub REST API で data/facilities.json を master にコミットする。"""
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    url = f"{GITHUB_API_URL}/repos/{GITHUB_REPO}/contents/{OUTPUT_PATH_IN_REPO}"

    # 既存ファイルの SHA を取得（PUT に必要）
    existing_sha = None
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            existing_sha = json.loads(resp.read())["sha"]
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise

    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    JST_NOW = datetime.now(JST).strftime("%Y-%m-%d")
    payload: dict = {
        "message": f"chore: 施設情報バッチ更新 {JST_NOW}",
        "content": encoded,
        "branch": "master",
    }
    if existing_sha:
        payload["sha"] = existing_sha

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="PUT")
    with urllib.request.urlopen(req) as resp:
        resp_data = json.loads(resp.read())
    commit_sha = resp_data.get("commit", {}).get("sha", "")
    print(f"[GitHub] コミット完了: {commit_sha[:7]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="周辺施設情報バッチスクリプト")
    parser.add_argument("--dry-run", action="store_true",
                        help="GitHub へのプッシュを行わない（ローカル保存のみ）")
    args = parser.parse_args()

    print(f"[開始] spots ディレクトリ: {SPOTS_DIR}")
    spots = load_spots()
    print(f"[情報] {len(spots)} スポットを処理します")

    result = build_facilities_json(spots)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    output_str = json.dumps(result, ensure_ascii=False, indent=2)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(output_str)
    print(f"[保存] {OUTPUT_PATH} ({len(result) - 1} スポット)")

    if args.dry_run:
        print("[dry-run] GitHub へのプッシュをスキップしました")
        return

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("[ERROR] 環境変数 GITHUB_TOKEN が設定されていません", file=sys.stderr)
        sys.exit(1)

    print("[GitHub] プッシュ中...")
    push_to_github(token, output_str)


if __name__ == "__main__":
    main()
