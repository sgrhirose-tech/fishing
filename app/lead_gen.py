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

【魚種スラッグ対応表】
以下のスラッグが USER メッセージに記載された場合、対応する日本語名を使ってください。
kurodai=クロダイ / mejina=メジナ / aji=アジ / karei=カレイ / kisu=キス / suzuki=スズキ
ma-aji=マアジ / shiro-aji=シロアジ / sardinella=イワシ / katakuchi-iwashi=カタクチイワシ
saba=サバ / sanma=サンマ / hirame=ヒラメ / tachiuo=タチウオ / tako=タコ / ika=イカ
surume-ika=スルメイカ / koika=コウイカ / buri=ブリ / kampachi=カンパチ / hamachi=ハマチ
inada=イナダ / warasa=ワラサ / maguro=マグロ / katsuo=カツオ / sawara=サワラ
aburame=アイナメ / kasago=カサゴ / mebaru=メバル / isaki=イサキ / chinu=チヌ
shiro-gikesu=シロギス / makogarei=マコガレイ / ishigarei=イシガレイ / ainame=アイナメ
haze=ハゼ / unagi=ウナギ / fugu=フグ / korodai=コロダイ / tairyo=タイ

【検索の進め方】
以下のようなクエリを組み合わせて検索し、個人ブログや地元釣具店のレポートを優先して読んでください。
- "{spot_name} 釣り 釣行記"
- "{spot_name} {primary_fish} ポイント"
- "{spot_name} 釣り 穴場"
- "{spot_name} 釣り コツ"

【釣り禁止・立入禁止の場合】
検索で釣り禁止または立入禁止が判明した場合は、リード文の代わりに以下の形式だけを出力してください（他は一切書かない）：
「この釣り場は〇〇年に釣り禁止となりました。」
「この釣り場は〇〇年に立入禁止となりました。」
年が不明な場合は「現在」を使ってください。
例：この釣り場は2023年に釣り禁止となりました。

【出力ルール】
- 文字数: 210〜260文字（厳守）
- 語り口: 砕けた丁寧語（「〜です」「〜ます」調）
- 以下を必ず1つ以上含める：地名・地形・水深・底質・常連が通う時期・時間帯など「その場所ならではの情報」
- 汎用的な魚の習性説明（「底を引く」「遠投する」など）は書かない
- 「釣れます」「おすすめです」などの営業的表現は使わない
- 観光情報・アクセス情報は含めない
- ニッチ情報がまったく見つからなかった場合は、空文字列を返してください

【厳禁】
- 「以下がリード文です」「収集した情報をもとに」など前置きは一切書かない
- 「---」などの区切り線は書かない
- 「文字数確認：○字」などの確認コメントは書かない
- マークダウン記法（**太字**、## 見出しなど）は使わない
- リード文本文のみを出力すること

【良いリード文の例】

例1（桟橋式有料施設）:
横浜本牧ふ頭D突堤から東京湾に向かって伸びる桟橋式の釣り施設です。底質は砂地で平坦な地形が広がり、沖桟橋の水深は約15メートルあります。カレイは12月から3月にかけて砂地の底を這う個体が多く、常連はエサをコマセカゴと組み合わせた仕掛けで足元に止めて待つスタイルが主流です。夏から秋はアジとサバが回遊し、カゴ釣りでの数釣りが楽しめます。

例2（護岸・公園）:
羽田空港対岸に位置する「みなと広場」の北東向き約300mの護岸が釣り場で、東京湾の航路に面しているぶん潮通しが際立っています。水深は岸壁際で約5メートル、潮が動く時間帯には小型のクロダイが浮いてくるためヘチ釣りの実績があります。春から初夏にかけてはシーバスのランカーも出ており、地元では夜間の橋脚際が有名なポイントです。\
"""


def _call_claude(messages: list[dict], api_key: str, retry: int = 3) -> dict:
    """Claude API を呼び出す（リトライ付き）。レスポンス JSON を返す。"""
    payload = {
        "model": _MODEL,
        "max_tokens": _MAX_TOKENS,
        "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 1}],
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
                data = json.loads(resp.read().decode("utf-8"))
            usage = data.get("usage", {})
            cache_write = usage.get("cache_creation_input_tokens", 0)
            cache_read  = usage.get("cache_read_input_tokens", 0)
            if cache_write or cache_read:
                print(f"  [cache] write={cache_write} read={cache_read} input={usage.get('input_tokens',0)}")
            return data
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            print(f"  [警告] Claude API HTTP {e.code}: {err_body[:200]}")
            if attempt < retry - 1:
                wait = 65 if e.code == 429 else 2 ** attempt * 3
                print(f"  [{wait}秒待機中...]")
                time.sleep(wait)
        except Exception as e:
            print(f"  [警告] Claude API エラー: {e}")
            if attempt < retry - 1:
                time.sleep(2 ** attempt * 3)
    return {}


def _extract_text(response: dict) -> str:
    """レスポンスから最後のテキストブロックを取り出す。"""
    for block in reversed(response.get("content", [])):
        if block.get("type") == "text":
            return _clean_text(block["text"])
    return ""


import re as _re

_HR_RE         = _re.compile(r'\n\s*[-─―]{3,}\s*\n')
_LEAD_SEP_RE   = _re.compile(r'^[-─―]{3,}\s*\n?')   # 先頭の --- ストリップ用
_TRAIL_SEP_RE  = _re.compile(r'\n?\s*[-─―]{3,}\s*$') # 末尾の --- ストリップ用
_PREAMBLE_RE   = _re.compile(
    r'^(?:以下[、，はが]|収集|情報を|リード文|十分な情報|確認したところ'
    r'|上記[のをがにリ]|字数確認|【文字数|以上の情報|以下が'
    r'|[-─―]{3,}$'    # 単独の区切り線
    r')'
)
_POSTSCRIPT_RE = _re.compile(r'^(?:文字数|[-─―]{3}|\*\*文字|ルールに従い)')
_BOLD_RE       = _re.compile(r'\*\*(.+?)\*\*')
_H_RE          = _re.compile(r'#{1,6}\s*')
# モデルが「情報なし」や「エラー」を文章で説明しているパターン → 空文字を返す
_NO_INFO_RE    = _re.compile(
    r'空文字列を返|情報が得られません|ニッチ情報.*見つかりません|ルールに従い.*空'
    r'|ニッチ情報が不足|ニッチ情報が十分に取得できません'
    r'|ニッチな釣り場固有情報|収集できたニッチ情報.*が限られ'
    r'|検索回数上限に達'
)


def _clean_text(text: str) -> str:
    """前置き・区切り線・文字数コメント・マークダウンを除去する。
    モデルが「情報なし」や「エラー」を文章で説明している場合は空文字を返す。
    """
    text = text.strip()
    if not text:
        return ""

    # モデルが「情報なし/エラー」を説明しているだけのケース
    if _NO_INFO_RE.search(text):
        return ""

    # 先頭・末尾の --- を除去してから段落分割
    text = _LEAD_SEP_RE.sub("", text).strip()
    text = _TRAIL_SEP_RE.sub("", text).strip()

    # 「最後の \n\n 以降を取る」戦略：
    # 前置きは最初の段落に、本文は最後の段落に来ることが多い。
    # ただしモデルが本文を2回出力し末尾が短く切れている場合は長い版を優先する。
    if "\n\n" in text:
        paragraphs = text.split("\n\n")
        candidates = []
        for para in paragraphs:
            para = _LEAD_SEP_RE.sub("", para).strip()
            if not para:
                continue
            if (_PREAMBLE_RE.match(para) or _POSTSCRIPT_RE.match(para)
                    or _NO_INFO_RE.search(para)):
                continue
            candidates.append(para)

        if not candidates:
            return ""

        best = candidates[-1]  # 基本は末尾を採用
        # 末尾が短い（< 150字）かつ前方に長い版（≥200字）があれば長い版を優先
        if len(best) < 150:
            longer = [c for c in candidates[:-1] if len(c) >= 200]
            if longer:
                best = max(longer, key=len)
        text = best

    # 先頭の前置き行を除去（段落が1つのケース向け）
    lines = text.splitlines()
    while lines and (_PREAMBLE_RE.match(lines[0].strip())
                     or _POSTSCRIPT_RE.match(lines[0].strip())):
        lines.pop(0)

    # 末尾の後書き行を除去
    while lines and _POSTSCRIPT_RE.match(lines[-1].strip()):
        lines.pop()

    text = "\n".join(lines)

    # マークダウン除去
    text = _BOLD_RE.sub(r"\1", text)
    text = _H_RE.sub("", text)

    return text.strip()


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
    fish_slugs = ", ".join(fish[:3]) if fish else "不明"  # 最大3種まで

    user_prompt = (
        f"【釣り場名】{name}（{pref} {area_name}）\n"
        f"【対象魚スラッグ】{fish_slugs}\n"
        f"【メモ】{spot.get('info', {}).get('notes', '')}"
    )

    messages = [{"role": "user", "content": user_prompt}]
    response = _call_claude(messages, api_key)
    if not response:
        return "", "fallback", True

    text = _extract_text(response)

    # ニッチ情報なし判定（空文字 or 短すぎる応答）
    if not text or len(text) < 10:
        return "", "fallback", True

    # 釣り禁止・立入禁止の通知は短くても ok
    if _re.search(r'釣り禁止|立入禁止|立ち入り禁止', text):
        return text, "ok", False

    if len(text) < 50:
        return "", "fallback", True

    quality = "ok" if 150 <= len(text) <= 260 else "fallback"
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
