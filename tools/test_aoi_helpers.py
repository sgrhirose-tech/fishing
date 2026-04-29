#!/usr/bin/env python3
"""
app/aoi.py のヘルパー関数単体テスト

Usage:
    python tools/test_aoi_helpers.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from app.aoi import (
    deg_to_8dir,
    calc_wind_relative,
    calc_tide_activity,
    build_user_message,
    _scrub_placeholders,
    _fmt_precip_mmh,
    COMPASS16_TO_DEG,
    detect_mode,
    calc_weather_hash,
    AoiRateLimiter,
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
# build_user_message — date_label
# ──────────────────────────────────────────────
print("\n=== build_user_message (date_label) ===")

TMPL_LABEL = "{date_label}の{spot_name}\n波高：{wave}m"

msg_today = build_user_message(spot_with_facing, period_base, TMPL_LABEL,
                                month=4, date_label="今日")
check("date_label=今日 → '今日のテスト海岸'",
      msg_today.startswith("今日のテスト海岸"), True)

msg_tomorrow = build_user_message(spot_with_facing, period_base, TMPL_LABEL,
                                   month=4, date_label="明日")
check("date_label=明日 → '明日のテスト海岸'",
      msg_tomorrow.startswith("明日のテスト海岸"), True)

msg_default = build_user_message(spot_with_facing, period_base, TMPL_LABEL, month=4)
check("date_label デフォルト → 明日",
      msg_default.startswith("明日のテスト海岸"), True)

# ──────────────────────────────────────────────
# _scrub_placeholders — LLM がリテラルを出した場合の保険
# ──────────────────────────────────────────────
print("\n=== _scrub_placeholders ===")

leaked = "{date_label}の葉山港、追い風で投げやすい。"
check("{date_label} → 今日 に置換",
      _scrub_placeholders(leaked, "今日", "葉山港"),
      "今日の葉山港、追い風で投げやすい。")

leaked2 = "{date_label}の{spot_name}、完全に勝ちです。"
check("{date_label} と {spot_name} の両方を置換",
      _scrub_placeholders(leaked2, "明日", "茅ヶ崎海岸"),
      "明日の茅ヶ崎海岸、完全に勝ちです。")

clean = "茅ヶ崎、明日完全に勝ちです。"
check("プレースホルダ無しは無変更",
      _scrub_placeholders(clean, "明日", "茅ヶ崎海岸"),
      clean)

# ──────────────────────────────────────────────
# _fmt_precip_mmh — 時間帯別1時間降水量の整数化
# ──────────────────────────────────────────────
print("\n=== _fmt_precip_mmh ===")

check("0.0 → '0'",     _fmt_precip_mmh(0.0), "0")
check("0.4 → '0' (四捨五入)", _fmt_precip_mmh(0.4), "0")
check("0.5 → '0' (banker丸め)", _fmt_precip_mmh(0.5), "0")
check("0.6 → '1'",     _fmt_precip_mmh(0.6), "1")
check("6.2 → '6'",     _fmt_precip_mmh(6.2), "6")
check("None → '-'",    _fmt_precip_mmh(None), "-")

# ──────────────────────────────────────────────
# build_user_message — precip_morning/noon/evening/night
# ──────────────────────────────────────────────
print("\n=== build_user_message (時間帯別降水量) ===")

TMPL_PRECIP = "{spot_name}\n降水量(mm/h)：朝{precip_morning}, 昼{precip_noon}, 夕{precip_evening}, 夜{precip_night}"

period_with_precip = {
    **period_base,
    "precip_max_morning_raw": 0.0,
    "precip_max_noon_raw":    6.2,
    "precip_max_evening_raw": 8.1,
    "precip_max_night_raw":   1.7,
}
msg_precip = build_user_message(spot_with_facing, period_with_precip, TMPL_PRECIP, month=4)
check("precip 各値あり → 整数化",
      "降水量(mm/h)：朝0, 昼6, 夕8, 夜2" in msg_precip, True)

period_precip_none = {
    **period_base,
    "precip_max_morning_raw": None,
    "precip_max_noon_raw":    None,
    "precip_max_evening_raw": None,
    "precip_max_night_raw":   None,
}
msg_precip_none = build_user_message(spot_with_facing, period_precip_none, TMPL_PRECIP, month=4)
check("precip 全て None → '-'",
      "降水量(mm/h)：朝-, 昼-, 夕-, 夜-" in msg_precip_none, True)

# ──────────────────────────────────────────────
# detect_mode
# ──────────────────────────────────────────────
print("\n=== detect_mode ===")

check("danger: 安全第一",      detect_mode("安全第一でやめときます。"),  "danger")
check("danger: 無理は禁物",    detect_mode("無理は禁物、家で待機です。"), "danger")
check("ng: 家の日",            detect_mode("家の日にします。"),           "ng")
check("ng: 装備しても厳しい",  detect_mode("装備しても厳しい状況です。"), "ng")
check("good: これは行く日",    detect_mode("これは行く日です。"),         "good")
check("good: 完全に勝ち",      detect_mode("完全に勝ちのコンディション。"), "good")
check("good: 迷う必要ありません", detect_mode("迷う必要ありません、晴れです。"), "good")
check("good: コンディション出来上がってます", detect_mode("コンディション出来上がってます。"), "good")
check("good: ベタ凪ぎ",        detect_mode("ベタ凪ぎで最高です。"),       "good")
check("unsure: その他",        detect_mode("ちょっと迷います。"),         "unsure")
check("unsure: 空文字",        detect_mode(""),                           "unsure")
# 優先順: danger > ng > good (本文に複数フレーズが混在するケース)
check("danger が ng より優先",  detect_mode("家の日だけど安全第一で。"),   "danger")
check("ng が good より優先",    detect_mode("これは行く日だが家の日にします。"), "ng")

# ──────────────────────────────────────────────
# calc_weather_hash
# ──────────────────────────────────────────────
print("\n=== calc_weather_hash ===")

h1 = calc_weather_hash(0.6, 3.7, 0, 0, 0, 0, "快晴")
h2 = calc_weather_hash(0.6, 3.7, 0, 0, 0, 0, "快晴")
h3 = calc_weather_hash(1.2, 3.7, 0, 0, 0, 0, "快晴")  # 波高が違う
h4 = calc_weather_hash(0.6, 3.7, 0, 0, 0, 0, "雨")    # 天気が違う

check("同一データ → 同一ハッシュ",   h1 == h2, True)
check("波高違い → 異なるハッシュ",   h1 == h3, False)
check("天気違い → 異なるハッシュ",   h1 == h4, False)
check("ハッシュ長は16文字",           len(h1) == 16, True)

# ──────────────────────────────────────────────
# AoiRateLimiter
# ──────────────────────────────────────────────
print("\n=== AoiRateLimiter ===")

rl = AoiRateLimiter()
rl.RATE_LIMIT_DAY   = 3  # テスト用に小さい値
rl.RATE_LIMIT_NIGHT = 2
rl.RATE_LIMIT_DAILY = 5

results_rl = [rl.check_and_consume() for _ in range(6)]
# 昼間(6:00-22:00)想定なら最初の3回True、4回目以降False (日次5に達するまで)
# 夜間なら最初の2回True
# どちらかに応じて判定する（テスト時刻依存なので柔軟に）

from datetime import datetime, timezone, timedelta
jst_now_hour = datetime.now(timezone(timedelta(hours=9))).hour
is_night_test = jst_now_hour < 6 or jst_now_hour >= 22
h_limit = rl.RATE_LIMIT_NIGHT if is_night_test else rl.RATE_LIMIT_DAY
daily_limit = rl.RATE_LIMIT_DAILY

expected = [True] * min(h_limit, daily_limit) + [False] * (6 - min(h_limit, daily_limit))
check(f"時間制限 ({h_limit}/h) + 日次制限 ({daily_limit}/day) が正しく機能する",
      results_rl, expected)

# ──────────────────────────────────────────────
# 集計
# ──────────────────────────────────────────────
total = len(_results)
passed = sum(_results)
failed = total - passed
print(f"\n{'='*40}")
print(f"結果: {passed}/{total} PASS  ({failed} FAIL)")
sys.exit(0 if failed == 0 else 1)
