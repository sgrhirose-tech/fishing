#!/usr/bin/env python3
"""
釣り場リード文自動生成バッチスクリプト。

Claude web_search ツールを使って釣り場のニッチ情報を収集し、
spots/{slug}.json の info.lead_text に200〜260字のリード文を書き込む。

メタデータは data/lead_meta.json で管理する。
Render cron では実行後に GitHub へプッシュして永続化する。

使い方:
    python scripts/gen_lead_texts.py                    # 優先度順に20件処理
    python scripts/gen_lead_texts.py --limit 50
    python scripts/gen_lead_texts.py --slug enoshima    # 1件のみ
    python scripts/gen_lead_texts.py --force            # 既存でも強制再生成
    python scripts/gen_lead_texts.py --dry-run          # 生成のみ、ファイル書き込み・GitHub プッシュなし

Render cron スケジュール: 0 16 * * 0 (毎週日曜 01:00 JST)
"""
import argparse
import base64
import datetime
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

_REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from app.lead_gen import generate_lead_text, update_spot_json  # noqa: E402

_SPOTS_DIR      = _REPO_ROOT / "spots"
_META_PATH      = _REPO_ROOT / "data" / "lead_meta.json"

GITHUB_API       = "https://api.github.com"
GITHUB_OWNER     = "sgrhirose-tech"
GITHUB_REPO      = "fishing"
GITHUB_BRANCH    = "master"

# エリア優先度（低い数字 = 高優先）
_AREA_PRIORITY: dict[str, int] = {
    "tokyobay":  0,
    "sagamibay": 1,
    "miura":     2,
    "isewan":    3,
    "osakawan":  4,
}
_AREA_PRIORITY_DEFAULT = 99

_REFRESH_DAYS   = 180
_SLEEP_BETWEEN  = 3   # 秒（レート制限対策）


# ---------------------------------------------------------------------------
# メタデータ I/O
# ---------------------------------------------------------------------------

def load_lead_meta() -> dict:
    if _META_PATH.exists():
        try:
            return json.loads(_META_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_lead_meta(meta: dict) -> None:
    _META_PATH.parent.mkdir(parents=True, exist_ok=True)
    _META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# スポット読み込み
# ---------------------------------------------------------------------------

def load_all_spots() -> list[dict]:
    spots = []
    for p in sorted(_SPOTS_DIR.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            spots.append(data)
        except Exception as e:
            print(f"  [警告] {p.name} 読み込みスキップ: {e}")
    return spots


# ---------------------------------------------------------------------------
# 優先度ソート
# ---------------------------------------------------------------------------

def _spot_sort_key(spot: dict, meta: dict) -> tuple:
    """優先度タプルを返す（小さいほど先に処理）。"""
    slug = spot.get("slug", "")
    m    = meta.get(slug, {})
    info = spot.get("info") or {}

    has_lead   = bool(info.get("lead_text"))
    gen_at_str = m.get("generated_at", "")
    needs_rev  = m.get("needs_review", False)

    # グループ判定
    if not has_lead:
        group = 0   # 未生成
    elif gen_at_str:
        try:
            gen_at = datetime.datetime.fromisoformat(gen_at_str)
            age    = (datetime.datetime.now(datetime.timezone.utc) - gen_at).days
        except Exception:
            age = 0
        group = 1 if age >= _REFRESH_DAYS else (2 if needs_rev else 3)
    else:
        group = 2 if needs_rev else 3

    area_slug  = (spot.get("area") or {}).get("area_slug", "")
    area_pri   = _AREA_PRIORITY.get(area_slug, _AREA_PRIORITY_DEFAULT)

    return (group, area_pri, slug)


def get_spots_to_process(
    spots: list[dict],
    meta: dict,
    limit: int,
    force: bool = False,
    slug_filter: str | None = None,
) -> list[dict]:
    if slug_filter:
        return [s for s in spots if s.get("slug") == slug_filter]

    if force:
        candidates = spots
    else:
        # グループ 3（新鮮 + 不要）は除外
        candidates = [s for s in spots if _spot_sort_key(s, meta)[0] < 3]

    candidates.sort(key=lambda s: _spot_sort_key(s, meta))
    return candidates[:limit]


# ---------------------------------------------------------------------------
# GitHub プッシュ（複数ファイルをまとめて 1 コミット）
# ---------------------------------------------------------------------------

def _get_sha(url: str, headers: dict) -> str | None:
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8")).get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def _push_file(token: str, local_path: pathlib.Path, github_path: str, message: str) -> None:
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    url = f"{GITHUB_API}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{github_path}"
    sha = _get_sha(url, headers)

    content_b64 = base64.b64encode(local_path.read_bytes()).decode("ascii")
    payload: dict = {"message": message, "content": content_b64, "branch": GITHUB_BRANCH}
    if sha:
        payload["sha"] = sha

    body = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(url, data=body, headers=headers, method="PUT")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    commit_sha = data.get("commit", {}).get("sha", "?")
    print(f"  [GitHub] {github_path} → commit {commit_sha[:8]}")


def push_results_to_github(token: str, updated_slugs: list[str]) -> None:
    """更新したスポット JSON と lead_meta.json を GitHub に push する。"""
    print(f"\n[GitHub] {len(updated_slugs)} スポット + メタデータをプッシュ中...")
    today = datetime.date.today().isoformat()
    msg   = f"feat: リード文バッチ更新（{today}）"

    # メタデータ
    _push_file(token, _META_PATH, "data/lead_meta.json", msg)

    # 各スポット JSON
    for slug in updated_slugs:
        local = _SPOTS_DIR / f"{slug}.json"
        if local.exists():
            try:
                _push_file(token, local, f"spots/{slug}.json", msg)
                time.sleep(0.5)
            except Exception as e:
                print(f"  [警告] {slug} のプッシュ失敗: {e}")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="釣り場リード文自動生成バッチ")
    parser.add_argument("--limit",   type=int, default=20, help="処理件数（デフォルト20）")
    parser.add_argument("--slug",    type=str, default=None, help="1件だけ処理するスポットスラッグ")
    parser.add_argument("--force",   action="store_true",   help="既存のリード文も強制再生成")
    parser.add_argument("--dry-run", action="store_true",   help="生成のみ（ファイル書き込み・GitHub プッシュなし）")
    args = parser.parse_args()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[エラー] ANTHROPIC_API_KEY が設定されていません")
        sys.exit(1)

    github_token = os.environ.get("GITHUB_TOKEN", "")

    print(f"=== リード文自動生成バッチ 開始 {datetime.datetime.now().isoformat()} ===")
    if args.dry_run:
        print("[DRY-RUN モード] ファイル書き込み・GitHub プッシュはしません")

    # データ読み込み
    all_spots = load_all_spots()
    meta      = load_lead_meta()
    print(f"[読み込み] {len(all_spots)} スポット / メタデータ {len(meta)} 件")

    # 処理対象を決定
    targets = get_spots_to_process(
        all_spots, meta,
        limit=args.limit,
        force=args.force,
        slug_filter=args.slug,
    )
    print(f"[処理対象] {len(targets)} 件\n")

    # 結果集計
    results: dict[str, str] = {}   # slug → "ok" | "fallback" | "error"
    updated_slugs: list[str] = []
    jst = datetime.timezone(datetime.timedelta(hours=9))

    for i, spot in enumerate(targets):
        slug = spot.get("slug", "")
        name = spot.get("name", "")
        print(f"[{i+1}/{len(targets)}] {slug} ({name})")

        try:
            text, quality, needs_review = generate_lead_text(spot, api_key)
        except Exception as e:
            print(f"  [エラー] 生成例外: {e}")
            meta[slug] = {**meta.get(slug, {}), "needs_review": True}
            results[slug] = "error"
            continue

        if text:
            print(f"  → {len(text)} 字 / quality={quality}")
            print(f"  {text[:60]}…" if len(text) > 60 else f"  {text}")
            if not args.dry_run:
                if update_spot_json(slug, text):
                    updated_slugs.append(slug)
        else:
            print(f"  → ニッチ情報なし。lead_text は書き込みません。")

        # メタデータ更新
        meta[slug] = {
            "generated_at": datetime.datetime.now(jst).isoformat(),
            "quality":      quality,
            "needs_review": needs_review,
        }
        results[slug] = quality

        if i < len(targets) - 1:
            time.sleep(_SLEEP_BETWEEN)

    # メタデータ保存
    if not args.dry_run:
        save_lead_meta(meta)

    # GitHub プッシュ
    if not args.dry_run and updated_slugs and github_token:
        try:
            push_results_to_github(github_token, updated_slugs)
        except Exception as e:
            print(f"\n[警告] GitHub プッシュ失敗: {e}")
            print("  ローカル変更は保存済みです。手動で git push してください。")
    elif not github_token and not args.dry_run:
        print("\n[情報] GITHUB_TOKEN 未設定のため GitHub プッシュをスキップします")
        print("  ローカル変更を手動で git commit & push してください。")

    # サマリー
    ok_count       = sum(1 for v in results.values() if v == "ok")
    fallback_count = sum(1 for v in results.values() if v == "fallback")
    error_count    = sum(1 for v in results.values() if v == "error")
    print(f"\n=== 完了 ===")
    print(f"  OK（リード文生成）  : {ok_count} 件")
    print(f"  FALLBACK（情報不足）: {fallback_count} 件")
    print(f"  ERROR               : {error_count} 件")

    # needs_review の一覧
    review_slugs = [slug for slug, m in meta.items() if m.get("needs_review")]
    if review_slugs:
        print(f"\n[要レビュー] {len(review_slugs)} 件:")
        for s in sorted(review_slugs)[:10]:
            print(f"  - {s}")
        if len(review_slugs) > 10:
            print(f"  ... 他 {len(review_slugs) - 10} 件")


if __name__ == "__main__":
    main()
