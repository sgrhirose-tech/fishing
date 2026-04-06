"""
AIコメントAPI呼び出しのJSONLログ書き込みユーティリティ。
logs/ai_comment.jsonl にAPIコール成功時の情報を追記する。
"""
import json
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta

_LOG_PATH = Path(__file__).parent.parent / "logs" / "ai_comment.jsonl"
_JST = timezone(timedelta(hours=9))


def log_ai_call(
    source: str,
    key: str,
    usage: dict,
    latency_ms: int,
    comment: str,
) -> None:
    """
    APIコール結果をJSONL形式でログに追記する。

    Args:
        source: 呼び出し元 ("xpost" / "spot" / "advisor")
        key: 識別キー（エリアslug+モード+日付 or スポットslug+日付）
        usage: Anthropicレスポンスの usage フィールド
        latency_ms: レイテンシ（ミリ秒）
        comment: 生成されたコメントテキスト
    """
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(_JST).isoformat(timespec="seconds"),
            "source": source,
            "key": key,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
            "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
            "latency_ms": latency_ms,
            "comment": comment,
        }
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  [ログ書き込み失敗] {e}")
