"""
潮汐データ読み込みモジュール。

data/tides/{harbor_code}_{YYYY-MM}.json から潮汐データを読み込み、
指定スポット・指定日のデータを返す。

データは scripts/fetch_tides.py の月次バッチで生成される。
harbor_mapping.json に未登録のスポット、またはキャッシュファイルが
存在しない日付は None を返す。
"""

import json
import pathlib

_DATA_DIR = pathlib.Path(__file__).parent.parent / "data"
_HARBOR_MAPPING_PATH = _DATA_DIR / "harbor_mapping.json"
_TIDES_DIR = _DATA_DIR / "tides"

_harbor_mapping: dict | None = None


def _get_harbor_mapping() -> dict:
    """harbor_mapping.json をロードしてキャッシュする（遅延初期化）。"""
    global _harbor_mapping
    if _harbor_mapping is None:
        try:
            with open(_HARBOR_MAPPING_PATH, encoding="utf-8") as f:
                _harbor_mapping = json.load(f)
        except FileNotFoundError:
            _harbor_mapping = {"spots": {}}
    return _harbor_mapping


def get_tide_data(slug: str, date_str: str) -> dict | None:
    """
    スポット slug と日付文字列（YYYY-MM-DD）から潮汐データを返す。

    Returns:
        潮汐データ dict。以下のキーを含む:
          - date: str               日付 (YYYY-MM-DD)
          - harbor_name: str        港名
          - tide_name: str          潮名（大潮/中潮/小潮/長潮/若潮）
          - sunrise: str            日の出時刻 (HH:MM)
          - sunset: str             日の入り時刻 (HH:MM)
          - moon_age: float | None  月齢
          - flood: list[dict]       満潮リスト [{"time": "HH:MM", "cm": float}, ...]
          - ebb: list[dict]         干潮リスト [{"time": "HH:MM", "cm": float}, ...]
          - hourly: list[dict]      時刻別潮位（24件）

        キャッシュなし・mapping 未登録の場合は None。
    """
    mapping = _get_harbor_mapping().get("spots", {}).get(slug)
    if not mapping:
        return None

    harbor_code = mapping["harbor_code"]
    month_str = date_str[:7]  # YYYY-MM
    cache_path = _TIDES_DIR / f"{harbor_code}_{month_str}.json"

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, encoding="utf-8") as f:
            cached = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    day_data = cached.get("days", {}).get(date_str)
    if not day_data:
        return None

    return {
        "date": date_str,
        "harbor_name": mapping["harbor_name"],
        **day_data,
    }


def reload_mapping() -> None:
    """harbor_mapping.json のキャッシュをクリアして再読み込みを促す（テスト用）。"""
    global _harbor_mapping
    _harbor_mapping = None
