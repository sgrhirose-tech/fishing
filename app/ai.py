"""
Claude API を使ったスポット別AIコメント生成。
プロンプトは プロジェクトルートの ai_prompt.md から読み込む。
{spot_data} プレースホルダーに翌日の朝/昼/夕/夜データ（JSON）を差し込む。
"""
import json
import os
import urllib.request
from pathlib import Path

_AI_COMMENT_CACHE: dict = {}  # (slug, date) → text


def generate_spot_comment(spot: dict, periods: list, date_str: str) -> str:
    """スポット1件分の気象データからAIコメントを生成。(slug, date) でキャッシュ。"""
    slug = spot.get("slug", "")
    cache_key = (slug, date_str)
    if cache_key in _AI_COMMENT_CACHE:
        return _AI_COMMENT_CACHE[cache_key]

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return ""

    prompt_file = Path(__file__).parent.parent / "ai_prompt.md"
    if not prompt_file.exists():
        return ""
    template = prompt_file.read_text(encoding="utf-8")

    spot_json = {
        "name": spot.get("name", ""),
        "area": spot.get("area", {}).get("area_name", ""),
        "date": date_str,
        "periods": periods,
    }
    prompt = template.replace("{spot_data}", json.dumps(spot_json, ensure_ascii=False, indent=2))

    try:
        body = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 512,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        text = result["content"][0]["text"]
        _AI_COMMENT_CACHE[cache_key] = text
        return text
    except Exception as e:
        print(f"  [警告] AIコメント生成失敗: {e}")
        return ""
