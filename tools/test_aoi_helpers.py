#!/usr/bin/env python3
"""
generate_aoi_comments.py のヘルパー関数単体テスト

Usage:
    python tools/test_aoi_helpers.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools.generate_aoi_comments import (
    deg_to_8dir,
    calc_wind_relative,
    calc_tide_activity,
    build_user_message,
    COMPASS16_TO_DEG,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
_results: list[bool] = []


def check(label: str, got, expected) -> None:
    ok = got == expected
    _results.append(ok)
    mark = PASS if ok else FAIL
    print(f"  [{mark}] {label}")
    if not ok:
        print(f"         got={got!r}  expected={expected!r}")


# ──────────────────────────────────────────────
# deg_to_8dir
# ──────────────────────────────────────────────
print("=== deg_to_8dir ===")
check("0°  → N",   deg_to_8dir(0),   "N")
check("45° → NE",  deg_to_8dir(45),  "NE")
check("90° → E",   deg_to_8dir(90),  "E")
check("135°→ SE",  deg_to_8dir(135), "SE")
check("170°→ S",   deg_to_8dir(170), "S")   # 茅ヶ崎海岸
check("180°→ S",   deg_to_8dir(180), "S")
check("270°→ W",   deg_to_8dir(270), "W")
check("315°→ NW",  deg_to_8dir(315), "NW")
check("360°→ N",   deg_to_8dir(360), "N")
check("22°→ N",    deg_to_8dir(22),  "N")   # 境界直前
check("23°→ NE",   deg_to_8dir(23),  "NE")  # 境界直後

# ──────────────────────────────────────────────
# calc_wind_relative
# ──────────────────────────────────────────────
print("\n=== calc_wind_relative ===")

# 基本 5 区分（spot_facing=S=180°）
# 北風：北から吹いてくる → 南向き岸の釣り人の背後から → 追い風
check("北風 / 南向き岸 → 追い風",
      calc_wind_relative("北", 5.0, 180), "追い風")
# 南風：南から吹いてくる → 海から顔に当たる → 向かい風
check("南風 / 南向き岸 → 向かい風",
      calc_wind_relative("南", 5.0, 180), "向かい風")
check("東風 / 南向き岸 → 横風",
      calc_wind_relative("東", 5.0, 180), "横風")
check("西風 / 南向き岸 → 横風",
      calc_wind_relative("西", 5.0, 180), "横風")
check("北東風 / 南向き岸 → 斜め追い風",
      calc_wind_relative("北東", 5.0, 180), "斜め追い風")
check("南東風 / 南向き岸 → 斜め向かい風",
      calc_wind_relative("南東", 5.0, 180), "斜め向かい風")

# ほぼ無風
check("風速 0.5 → ほぼ無風",
      calc_wind_relative("北", 0.5, 180), "ほぼ無風")
check("風速 0.9 → ほぼ無風",
      calc_wind_relative("北", 0.9, 180), "ほぼ無風")
check("風速 1.0 → 無風でない(追い風)",
      calc_wind_relative("北", 1.0, 180), "追い風")

# spot_facing_deg が None
check("spot_facing=None → None",
      calc_wind_relative("北", 5.0, None), None)

# コンパス未知
check("コンパス未知 → None",
      calc_wind_relative("ー", 5.0, 180), None)

# 実際のスポット例: 茅ヶ崎（sea_bearing_deg=170≈S）× 北北東の風
# 北北東から吹く → 南向き岸の背後やや右 → 斜め追い風
check("北北東風 / 茅ヶ崎(170°) → 斜め追い風",
      calc_wind_relative("北北東", 5.0, 170), "斜め追い風")

# 実際のスポット例: 東扇島西公園（sea_bearing_deg=285≈W）× 北北東の風
# 北北東から吹く → 西向き岸の右横 → 横風
check("北北東風 / 東扇島(285°) → 横風",
      calc_wind_relative("北北東", 5.0, 285), "横風")

# ──────────────────────────────────────────────
# calc_tide_activity
# ──────────────────────────────────────────────
print("\n=== calc_tide_activity ===")
check("大潮 → 活発",   calc_tide_activity("大潮（月齢14.2）"), "活発")
check("中潮 → 活発",   calc_tide_activity("中潮（月齢11.0）"), "活発")
check("小潮 → 穏やか", calc_tide_activity("小潮（月齢7.3）"),  "穏やか")
check("長潮 → 穏やか", calc_tide_activity("長潮（月齢8.1）"),  "穏やか")
check("若潮 → 穏やか", calc_tide_activity("若潮（月齢9.6）"),  "穏やか")
check("空文字 → None", calc_tide_activity(""),                 None)
check("ー → None",     calc_tide_activity("ー"),               None)

# ──────────────────────────────────────────────
# build_user_message — null 行省略の確認
# ──────────────────────────────────────────────
print("\n=== build_user_message (null 省略) ===")

TMPL = (
    "{spot_name}\n"
    "風：{wind_dir} {wind_speed}m/s{wind_relative_clause}\n"
    "潮汐：{tide_info}{tide_activity_clause}\n"
    "施設区分：{spot_type}{facing_line}"
)

spot_with_facing = {
    "name": "テスト海岸",
    "classification": {"primary_type": "sand_beach"},
    "physical_features": {"sea_bearing_deg": 180},  # 南向き
}
spot_no_facing = {
    "name": "テスト海岸",
    "classification": {"primary_type": "sand_beach"},
    "physical_features": {},  # sea_bearing_deg なし
}

period_base = {
    "sky": "晴れ",
    "temp_raw": 20.0,
    "wave_height_raw": 0.8,
    "wind_dir_compass": "北",
    "wind_speed_raw": 5.0,
    "wave_period_raw": 6.0,
    "sst_raw": 18.0,
    "tide": "若潮（月齢9.6）",
    "precip": "0.0mm",
}

# A: wind_relative が有効なとき括弧付き
# 北風×南向き岸=追い風 なので「（追い風）」が含まれる
msg_a = build_user_message(spot_with_facing, period_base, TMPL, month=4)
check("A: wind_relative 有効 → 括弧付き",
      "（追い風）" in msg_a, True)

# A: spot_facing なし → wind_relative_clause が空
msg_b = build_user_message(spot_no_facing, period_base, TMPL, month=4)
check("A: spot_facing なし → 括弧なし",
      "（" not in msg_b.split("\n")[1], True)

# B: tide_activity → 若潮は穏やか
check("B: 若潮 → （潮の動き：穏やか）",
      "（潮の動き：穏やか）" in msg_a, True)

# B: tide_info が ー → tide_activity_clause が空
period_no_tide = {**period_base, "tide": "ー"}
msg_c = build_user_message(spot_with_facing, period_no_tide, TMPL, month=4)
check("B: tide ー → 潮の動き行なし",
      "潮の動き" not in msg_c, True)

# C: spot_facing あり → facing_line が含まれる
check("C: spot_facing あり → facing_line 含む",
      "釣り場の正面：S" in msg_a, True)

# C: spot_facing なし → facing_line が空
check("C: spot_facing なし → facing_line なし",
      "釣り場の正面" not in msg_b, True)

# ──────────────────────────────────────────────
# build_user_message — temp_min / temp_max
# ──────────────────────────────────────────────
print("\n=== build_user_message (temp_min / temp_max) ===")

TMPL_TEMP = (
    "{spot_name}\n"
    "最低気温：{temp_min}℃\n"
    "最高気温：{temp_max}℃"
)

period_temp = {**period_base, "temp_min_raw": 11.5, "temp_max_raw": 23.4}
msg_temp = build_user_message(spot_with_facing, period_temp, TMPL_TEMP, month=4)
check("temp_min 値あり → 11.5",  "最低気温：11.5℃" in msg_temp, True)
check("temp_max 値あり → 23.4",  "最高気温：23.4℃" in msg_temp, True)

period_temp_none = {**period_base, "temp_min_raw": None, "temp_max_raw": None}
msg_none = build_user_message(spot_with_facing, period_temp_none, TMPL_TEMP, month=4)
check("temp_min None → ー",      "最低気温：ー℃" in msg_none, True)
check("temp_max None → ー",      "最高気温：ー℃" in msg_none, True)

# ──────────────────────────────────────────────
# 集計
# ──────────────────────────────────────────────
total = len(_results)
passed = sum(_results)
failed = total - passed
print(f"\n{'='*40}")
print(f"結果: {passed}/{total} PASS  ({failed} FAIL)")
sys.exit(0 if failed == 0 else 1)
