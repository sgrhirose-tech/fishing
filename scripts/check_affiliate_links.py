"""
アフィリエイトリンク月次チェックスクリプト
Render cron で毎月自動実行。結果は stdout（Render ログ）で確認。

チェック内容:
  - /dp/ASIN URL: HTTP ステータス + Amazon在庫なし文言検出
  - /s?k= URL  : HTTP 200 確認のみ
"""

import json
import random
import sys
import time
from pathlib import Path
from datetime import datetime

import requests

# ────────────────────────────────────────────
# 設定
# ────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent
TACKLE_DIR = BASE_DIR / "data" / "tackle"

JSON_FILES = [
    "rod.json",
    "reel.json",
    "accessories.json",
    "extras.json",
    "terminal.json",
    "line.json",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

TIMEOUT = 10

# Amazon の「在庫なし」「商品なし」を示す文言（日本語）
UNAVAILABLE_PATTERNS = [
    "現在お取り扱いできません",
    "この商品は現在お取り扱いできません",
    "見つかりませんでした",
    "申し訳ありませんが、お探しの商品は見つかりませんでした",
    "この商品は、現在お買い求めいただけません",
    "Currently unavailable",
    "This item is not available",
]

# Amazon トップページへのリダイレクト先（商品削除時の挙動）
AMAZON_TOP_PATTERNS = [
    "www.amazon.co.jp/?",
    "www.amazon.co.jp/ref=",
    "/gp/homepage.html",
]


# ────────────────────────────────────────────
# データ収集
# ────────────────────────────────────────────

def collect_links() -> list[dict]:
    """全 JSON から affiliate_slots のリンク情報を収集して返す。"""
    entries = []
    for filename in JSON_FILES:
        path = TACKLE_DIR / filename
        if not path.exists():
            continue
        category = filename.replace(".json", "")
        items = json.loads(path.read_text(encoding="utf-8"))
        for item in items:
            slug = item.get("slug", "")
            slots = item.get("affiliate_slots")
            if not slots or not isinstance(slots, dict):
                continue
            for slot_key, slot_list in slots.items():
                if not isinstance(slot_list, list):
                    continue
                for entry in slot_list:
                    url = entry.get("url", "").strip()
                    name = entry.get("name", "").strip()
                    asin = entry.get("asin", "").strip()
                    if not url:
                        continue
                    entries.append({
                        "category": category,
                        "slug": slug,
                        "slot": slot_key,
                        "name": name,
                        "url": url,
                        "asin": asin,
                    })
    return entries


# ────────────────────────────────────────────
# チェック
# ────────────────────────────────────────────

def check_url(url: str) -> tuple[str, str]:
    """
    URL をチェックして (status, detail) を返す。
    status: "OK" | "DEAD" | "UNAVAILABLE" | "ERROR"
    """
    is_product = "/dp/" in url

    def _fetch():
        return requests.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)

    try:
        resp = _fetch()
    except requests.exceptions.Timeout:
        return "ERROR", "タイムアウト"
    except requests.exceptions.ConnectionError as e:
        return "ERROR", f"接続エラー: {e}"
    except Exception as e:
        return "ERROR", f"例外: {e}"

    # 503 は 1 回だけリトライ
    if resp.status_code == 503:
        time.sleep(10)
        try:
            resp = _fetch()
        except Exception as e:
            return "ERROR", f"503 後リトライ失敗: {e}"

    if resp.status_code == 404:
        return "DEAD", "HTTP 404"

    if resp.status_code != 200:
        return "ERROR", f"HTTP {resp.status_code}"

    # 最終 URL がトップページへリダイレクトされていないか確認
    final_url = resp.url
    for pattern in AMAZON_TOP_PATTERNS:
        if pattern in final_url and "/dp/" not in final_url:
            return "DEAD", f"トップページへリダイレクト: {final_url[:80]}"

    # 商品 URL のみ在庫チェック
    if is_product:
        html = resp.text
        for pattern in UNAVAILABLE_PATTERNS:
            if pattern in html:
                return "UNAVAILABLE", f"文言検出: 「{pattern}」"

    return "OK", ""


# ────────────────────────────────────────────
# メイン
# ────────────────────────────────────────────

def main():
    now = datetime.now().strftime("%Y-%m")
    entries = collect_links()
    total = len(entries)

    print(f"=== アフィリエイトリンクチェック {now} ===")
    print(f"対象: {total}件\n")

    results = {"OK": [], "DEAD": [], "UNAVAILABLE": [], "ERROR": []}

    for i, entry in enumerate(entries, 1):
        label = f"{entry['category']} > {entry['slug']} > slot:{entry['slot']}"
        asin_str = f"  ASIN: {entry['asin']}" if entry['asin'] else ""

        status, detail = check_url(entry["url"])
        results[status].append({**entry, "detail": detail})

        if status != "OK":
            print(f"[{status:<11}] {label}{asin_str}")
            print(f"           {entry['name']}")
            print(f"           {entry['url']}")
            if detail:
                print(f"           → {detail}")
            print()

        # 進捗（10件ごと）
        if i % 10 == 0:
            print(f"  ... {i}/{total} 件チェック済み", flush=True)

        # レート制限
        if i < total:
            time.sleep(random.uniform(3.0, 6.0))

    # サマリー
    print("─" * 50)
    print("--- サマリー ---")
    for status in ("OK", "DEAD", "UNAVAILABLE", "ERROR"):
        count = len(results[status])
        marker = "" if status == "OK" else " ★要確認" if count > 0 else ""
        print(f"  {status:<12}: {count:>4}件{marker}")
    print(f"  合計         : {total:>4}件")

    # 問題があった場合は exit code 1（Render ログで目立つ）
    problem_count = len(results["DEAD"]) + len(results["UNAVAILABLE"])
    if problem_count > 0:
        print(f"\n★ DEAD/UNAVAILABLE が {problem_count}件 あります。上記URLを確認してください。")
        sys.exit(1)
    else:
        print("\n✓ 問題のあるリンクは見つかりませんでした。")


if __name__ == "__main__":
    main()
