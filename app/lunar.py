"""
月齢・潮区分ユーティリティ。

外部APIなし・純粋な数学計算。
lru_cache により同一日付の重複計算を防ぐ（7日予報で最大7通り）。
"""
from __future__ import annotations

import functools
from datetime import date

# 2000年1月6日 = 既知の朔日（新月）
_KNOWN_NEW_MOON = date(2000, 1, 6)
# 朔望月の平均日数
_LUNAR_CYCLE = 29.53058867


@functools.lru_cache(maxsize=400)  # 約1年分
def moon_age(d: date) -> float:
    """月齢（0.0〜29.5）を返す。"""
    delta = (d - _KNOWN_NEW_MOON).days
    return delta % _LUNAR_CYCLE


def tide_type(age: float) -> str:
    """月齢から潮区分（大潮/中潮/小潮/長潮/若潮）を返す。"""
    a = age % _LUNAR_CYCLE
    # 新月前後（月齢 0〜2.5 / 27〜29.5）
    if a < 2.5 or a > 27.0:
        return "大潮"
    # 満月前後（月齢 13.5〜16.5）
    if 13.5 < a < 16.5:
        return "大潮"
    # 長潮・若潮
    if 7.0 <= a <= 8.5 or 22.0 <= a <= 23.5:
        return "長潮"
    if 8.5 < a <= 10.0 or 23.5 < a <= 25.0:
        return "若潮"
    # 小潮
    if 5.0 <= a < 7.0 or 19.5 <= a < 22.0:
        return "小潮"
    # その他（中潮）
    return "中潮"


def tide_label(d: date) -> str:
    """表示用文字列を返す。例: '大潮（月齢14.2）'"""
    age = moon_age(d)
    t = tide_type(age)
    return f"{t}（月齢{age:.1f}）"
