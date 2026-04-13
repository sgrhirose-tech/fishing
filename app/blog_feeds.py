"""
関東釣りブログ RSS 取得・キャッシュ・マッチングモジュール

- data/blog_feeds.json に登録されたブログの RSS を定期取得（TTL: 4時間）
- 記事タイトルから魚種キーワードを抽出してタグ付け
- スポットの都道府県 + 対象魚種に基づいて関連記事を返す
"""
import json
import time
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from email.utils import parsedate_to_datetime

_BASE = Path(__file__).parent.parent
_FEEDS_PATH = _BASE / "data" / "blog_feeds.json"

_FEEDS: list = []             # [{name, blog_url, rss_url, pref_slugs, fish_slugs}, ...]
_CACHE: dict = {}             # {rss_url: (timestamp, [articles])}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = 4 * 3600         # 4時間

_FISH_KEYWORDS: dict = {}     # {slug: [keyword, ...]}

# 通称・別名マッピング（魚種スラッグ → キーワードリスト）
_SYNONYMS: dict = {
    "aji":      ["アジ", "鯵", "アジング"],
    "kurodai":  ["クロダイ", "チヌ", "黒鯛", "チヌ釣り", "クロダイ釣り"],
    "suzuki":   ["スズキ", "シーバス", "鱸", "シーバス釣り"],
    "aoriika":  ["アオリイカ", "エギング", "アオリ"],
    "tachiuo":  ["タチウオ", "太刀魚"],
    "shirogisu":["シロギス", "キス", "キスゴ"],
    "iwashi":   ["イワシ", "鰯", "サビキ"],
    "sayori":   ["サヨリ", "細魚"],
    "madako":   ["タコ", "蛸", "マダコ"],
    "buri":     ["ブリ", "ハマチ", "イナダ", "ワラサ", "青物"],
    "kanpachi": ["カンパチ", "ショゴ"],
    "hirame":   ["ヒラメ", "平目", "ソゲ"],
    "karei":    ["カレイ", "鰈"],
    "kasago":   ["カサゴ", "ガシラ", "根魚"],
    "mebaru":   ["メバル", "メバリング"],
    "saba":     ["サバ", "鯖"],
    "surumeika":["スルメイカ", "ヤリイカ"],
    "kamasu":   ["カマス"],
    "madai":    ["マダイ", "真鯛", "タイラバ"],
}


def load_feeds(fish_master: dict | None = None) -> None:
    """起動時に呼ぶ。blog_feeds.json とキーワード辞書をロード。"""
    global _FEEDS, _FISH_KEYWORDS
    try:
        with open(_FEEDS_PATH, encoding="utf-8") as f:
            _FEEDS = json.load(f)
        print(f"[blog_feeds] {len(_FEEDS)} ブログを読み込みました")
    except Exception as e:
        print(f"[blog_feeds] blog_feeds.json 読み込みエラー: {e}")
        _FEEDS = []

    # fish_master（{日本語名: {slug, ...}}）からキーワードを補完
    kw: dict = {slug: list(words) for slug, words in _SYNONYMS.items()}
    if fish_master:
        for jp_name, info in fish_master.items():
            slug = info.get("slug", "")
            if not slug:
                continue
            if slug not in kw:
                kw[slug] = []
            if jp_name and jp_name not in kw[slug]:
                kw[slug].insert(0, jp_name)
    _FISH_KEYWORDS = kw


def _parse_rss(xml_text: str) -> list:
    """RSS 2.0 / Atom XML を解析して記事リストを返す。"""
    articles = []
    try:
        root = ET.fromstring(xml_text)
        # --- RSS 2.0 ---
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            pub   = (item.findtext("pubDate") or "").strip()
            try:
                ts = parsedate_to_datetime(pub).timestamp() if pub else 0.0
            except Exception:
                ts = 0.0
            if title and link:
                articles.append({
                    "title": title,
                    "link":  link,
                    "ts":    ts,
                    "pub":   pub[:16] if pub else "",
                })
        if articles:
            return articles

        # --- Atom ---
        ns = "http://www.w3.org/2005/Atom"
        for entry in root.iter(f"{{{ns}}}entry"):
            title_el = entry.find(f"{{{ns}}}title")
            title = (title_el.text or "").strip() if title_el is not None else ""
            link_el = entry.find(f"{{{ns}}}link")
            link = (link_el.get("href") or "") if link_el is not None else ""
            upd_el = entry.find(f"{{{ns}}}updated")
            pub = (upd_el.text or "")[:19] if upd_el is not None else ""
            ts = 0.0
            if pub:
                try:
                    from datetime import datetime, timezone
                    ts = datetime.fromisoformat(pub).replace(
                        tzinfo=timezone.utc).timestamp()
                except Exception:
                    pass
            if title and link:
                articles.append({
                    "title": title,
                    "link":  link,
                    "ts":    ts,
                    "pub":   pub[:10],
                })
    except Exception:
        pass
    return articles


def _extract_fish_tags(title: str) -> list:
    """記事タイトルに含まれる魚種スラッグリストを返す。"""
    return [
        slug for slug, keywords in _FISH_KEYWORDS.items()
        if any(kw in title for kw in keywords)
    ]


def _fetch_one(feed: dict) -> list:
    """1ブログの RSS を取得・解析して記事リストを返す。失敗時は []。"""
    import requests as _req
    try:
        resp = _req.get(
            feed["rss_url"],
            timeout=8,
            headers={"User-Agent": "Tsuricast/1.0 (+https://tsuricast.jp/)"},
        )
        resp.raise_for_status()
        articles = _parse_rss(resp.text)
        for a in articles:
            a["blog_name"]  = feed["name"]
            a["blog_url"]   = feed["blog_url"]
            a["pref_slugs"] = feed["pref_slugs"]
            a["fish_tags"]  = _extract_fish_tags(a["title"])
        return articles[:20]
    except Exception as e:
        print(f"[blog_feeds] fetch failed ({feed['name']}): {e}")
        return []


def refresh_all() -> None:
    """全フィードを更新してキャッシュに保存する（TTL 未満のものはスキップ）。"""
    now = time.time()
    for feed in _FEEDS:
        url = feed["rss_url"]
        with _CACHE_LOCK:
            cached = _CACHE.get(url)
        if cached and now - cached[0] < _CACHE_TTL:
            continue
        articles = _fetch_one(feed)
        with _CACHE_LOCK:
            _CACHE[url] = (now, articles)


def get_posts_for_spot(spot: dict, limit: int = 5) -> list:
    """スポットに関連する最新ブログ記事を返す。

    フィルタ条件:
      1. ブログの pref_slugs がスポットの都道府県と一致
      2. スコアリング: スポットの対象魚種と記事タグが重なる数を加算
    """
    pref = (spot.get("area") or {}).get("pref_slug", "")
    # target_fish は文字列リスト ["aji", "kurodai", ...]
    spot_fish: list = spot.get("target_fish") or []

    candidates = []
    with _CACHE_LOCK:
        snapshot = list(_CACHE.items())

    # ブログ → 記事 のマッピングを構築
    feed_map = {f["rss_url"]: f for f in _FEEDS}

    for rss_url, (_, articles) in snapshot:
        feed = feed_map.get(rss_url)
        if feed is None:
            continue
        if pref not in feed.get("pref_slugs", []):
            continue
        for a in articles:
            fish_match = sum(1 for s in spot_fish if s in a.get("fish_tags", []))
            # (score 降順, 投稿日時 降順) でソート
            candidates.append((fish_match, a["ts"], a))

    # score 降順 → ts 降順
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [c[2] for c in candidates[:limit]]
