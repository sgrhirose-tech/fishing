"""
潮汐データ読み込みモジュール。

data/tides/{harbor_code}_{YYYY-MM}.json から潮汐データを読み込み、
指定スポット・指定日のデータを返す。

データは scripts/fetch_tides.py の月次バッチで生成される。
スポット JSON に harbor_code が未設定、またはキャッシュファイルが
存在しない日付は None を返す。
"""

import json
import pathlib

_DATA_DIR = pathlib.Path(__file__).parent.parent / "data"
_TIDES_DIR = _DATA_DIR / "tides"


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

        harbor_code 未設定・キャッシュなしの場合は None。
    """
    from .spots import load_spot
    spot = load_spot(slug)
    if not spot:
        return None

    harbor_code = spot.get("harbor_code")
    harbor_name = spot.get("harbor_name", "")
    if not harbor_code:
        return None

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
        "harbor_name": harbor_name,
        **day_data,
    }
