"""
Claude API を使ったスポット別AIコメント生成。
プロンプトは プロジェクトルートの ai_prompt.md から読み込む。
{spot_data} プレースホルダーに翌日の朝/昼/夕/夜データ（JSON）を差し込む。
"""
import json
import os
import time
import urllib.request
from pathlib import Path

_CACHE_DIR = Path(__file__).parent.parent / "data" / "cache"


def _load_spot_cache(date_str: str) -> dict:
    path = _CACHE_DIR / f"ai_comment_{date_str}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_spot_cache(date_str: str, cache: dict) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = _CACHE_DIR / f"ai_comment_{date_str}.json"
        path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"  [キャッシュ書き込み失敗] {e}")


def generate_spot_comment(spot: dict, periods: list, date_str: str) -> str:
    """スポット1件分の気象データからAIコメントを生成。日付ごとのファイルキャッシュを使用。"""
    from .ai_logger import log_ai_call

    slug = spot.get("slug", "")

    # ファイルキャッシュを確認
    cache = _load_spot_cache(date_str)
    if slug in cache:
        return cache[slug]

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""

    prompt_file = Path(__file__).parent.parent / "ai_prompt.md"
    if not prompt_file.exists():
        return ""
    content = prompt_file.read_text(encoding="utf-8")

    # ## SYSTEM / ## USER の2セクションに分割
    if "## USER" in content:
        sys_part, user_part = content.split("## USER", 1)
        system_prompt = sys_part.replace("## SYSTEM", "").strip()
        user_template = user_part.strip()
    else:
        system_prompt = ""
        user_template = content.strip()

    spot_json = {
        "name": spot.get("name", ""),
        "area": spot.get("area", {}).get("area_name", ""),
        "date": date_str,
        "periods": periods,
    }
    user_prompt = user_template.replace("{spot_data}", json.dumps(spot_json, ensure_ascii=False, indent=2))

    try:
        payload = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 256,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        if system_prompt:
            payload["system"] = [
                {
                    "type": "text",
                    "text": system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        body_bytes = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body_bytes,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2024-06-01",
                "anthropic-beta": "prompt-caching-2024-07-31",
                "content-type": "application/json",
            },
        )
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        latency_ms = int((time.monotonic() - t0) * 1000)

        text = result["content"][0]["text"]

        # ログ記録
        log_ai_call("spot", f"{slug}_{date_str}", result.get("usage", {}), latency_ms, text)

        # キャッシュ保存
        cache[slug] = text
        _save_spot_cache(date_str, cache)

        return text
    except Exception as e:
        print(f"  [警告] AIコメント生成失敗: {e}")
        return ""
