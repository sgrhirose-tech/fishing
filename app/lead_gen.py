"""
釣り場リード文自動生成モジュール。

Claude の web_search ツール（サーバーサイド）を使って釣り場に関する
ニッチ情報を収集し、200〜260 字のリード文を生成する。

生成結果は spots/{slug}.json の info.lead_text に書き込む。
ニッチ情報が取れなかった場合は空文字を返す（_build_spot_description() にフォールバック）。
"""
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import certifi as _certifi
    _SSL_CONTEXT = ssl.create_default_context(cafile=_certifi.where())
except ImportError:
    _SSL_CONTEXT = ssl.create_default_context()

_REPO_ROOT = Path(__file__).parent.parent
_SPOTS_DIR = _REPO_ROOT / "spots"

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 800

# エリア名（日本語）→ area_slug のマップ（プロンプト用）
_AREA_NAMES: dict[str, str] = {
    "tokyobay":  "東京湾",
    "sagamibay": "相模湾",
    "miura":     "三浦半島",
    "isewan":    "伊勢湾",
    "osakawan":  "大阪湾",
    "uchibo":    "内房",
    "sotobo":    "外房",
}

_SYSTEM_PROMPT = """\
あなたは釣り場情報サービス「Tsuricast」のコンテンツ担当です。
指定された釣り場について web_search ツールで情報を収集し、リード文を生成してください。

【検索の進め方】
以下のようなクエリを組み合わせて検索し、個人ブログや地元釣具店のレポートを優先して読んでください。
- "{spot_name} 釣り 釣行記"
- "{spot_name} {primary_fish} ポイント"
- "{spot_name} 釣り 穴場"

【出力ルール】
- 文字数: 200〜260文字（厳守）
- 語り口: 砕けた丁寧語（「〜です」「〜ます」調）
- 地名・施設名・具体的な数字など「地元感のある情報」を1つ以上含める
- 汎用的な釣り方説明（「底を引く」「遠投する」など）は書かない
- 「釣れます」「おすすめです」などの営業的表現は使わない
- 観光情報・アクセス情報は含めない
- ニッチ情報がまったく見つからなかった場合は、空文字列を返してください

テキストのみ出力してください。JSON・マークダウン記法は不要です。\
"""


def _call_claude(messages: list[dict], api_key: str, retry: int = 3) -> dict:
    """Claude API を呼び出す（リトライ付き）。レスポンス JSON を返す。"""
    payload = {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
        "system": [{"type": "text", "text": _SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
            "content-type": "application/json",
        },
    )
    for attempt in range(retry):
        try:
            with urllib.request.urlopen(req, timeout=60, context=_SSL_CONTEXT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"  [警告] Claude API HTTP {e.code}: {err_body[:200]}")
            if attempt < retry - 1:
                time.sleep(2 ** attempt * 3)
        except Exception as e:
            print(f"  [警告] Claude API エラー: {e}")
            if attempt < retry - 1:
                time.sleep(2 ** attempt * 3)
    return {}


def _extract_text(response: dict) -> str:
    """レスポンスから最後のテキストブロックを取り出す。"""
    for block in reversed(response.get("content", [])):
        if block.get("type") == "text":
            return block["text"].strip()
    return ""


def generate_lead_text(spot: dict, api_key: str) -> tuple[str, str, bool]:
    """
    釣り場のリード文を生成する。

    Returns:
        (text, quality, needs_review)
        text         : 生成されたリード文（空文字 = ニッチ情報なし）
        quality      : "ok" | "fallback"
        needs_review : レビューが必要かどうか
    """
    name = spot.get("name", "")
    area = spot.get("area", {})
    pref = area.get("prefecture", "")
    area_name = _AREA_NAMES.get(area.get("area_slug", ""), area.get("area_name", ""))
    fish = spot.get("target_fish", [])
    primary_fish_slug = fish[0] if fish else ""

    # 魚種スラッグ → 日本語名の簡易マップ（プロンプト用。完全性は不要）
    _FISH_JP: dict[str, str] = {
        "kurodai": "クロダイ", "mejina": "メジナ", "aji": "アジ",
        "karei": "カレイ", "kisu": "キス", "suzuki": "スズキ",
        "sardinella": "イワシ", "saba": "サバ", "sanma": "サンマ",
        "hirame": "ヒラメ", "tachiuo": "タチウオ", "tako": "タコ",
        "ika": "イカ", "buri": "ブリ", "kampachi": "カンパチ",
    }
    primary_fish_jp = _FISH_JP.get(primary_fish_slug, primary_fish_slug)

    user_prompt = (
        f"以下の釣り場のリード文を生成してください。\n\n"
        f"【釣り場名】{name}（{pref} {area_name}）\n"
        f"【主な対象魚】{primary_fish_jp or '不明'}\n"
        f"【メモ】{spot.get('info', {}).get('notes', '')}"
    )

    messages = [{"role": "user", "content": user_prompt}]
    response = _call_claude(messages, api_key)
    if not response:
        return "", "fallback", True

    text = _extract_text(response)

    # 文字数チェック
    char_count = len(text)
    if text and not (200 <= char_count <= 260):
        # 1回リプロンプト
        messages.append({"role": "assistant", "content": response.get("content", [])})
        messages.append({
            "role": "user",
            "content": (
                f"文字数が {char_count} 文字です。200〜260文字に収めてください。"
                "ニッチ情報が見つからない場合は空文字列のみ返してください。"
            ),
        })
        response2 = _call_claude(messages, api_key)
        text = _extract_text(response2) if response2 else text

    # ニッチ情報なし判定（空文字 or 「整理中」など明示的な空応答）
    if not text or len(text) < 50:
        return "", "fallback", True

    quality = "ok" if 200 <= len(text) <= 260 else "fallback"
    needs_review = quality == "fallback"
    return text, quality, needs_review


def update_spot_json(slug: str, lead_text: str) -> bool:
    """spots/{slug}.json の info.lead_text を更新する。成功時 True。"""
    path = _SPOTS_DIR / f"{slug}.json"
    if not path.exists():
        print(f"  [エラー] ファイルが見つかりません: {path}")
        return False
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("info", {})["lead_text"] = lead_text
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return True
    except Exception as e:
        print(f"  [エラー] JSON 更新失敗 ({slug}): {e}")
        return False
