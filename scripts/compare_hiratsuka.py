#!/usr/bin/env python3
"""
蓄積した平塚海岸の波浪予報データと平塚沖観測塔の実測値を比較するスクリプト。

使い方:
    1. collect_hiratsuka_forecast.py を数日間実行してログを蓄積する
    2. data/hiratsuka_actual.csv に平塚タワーの実測値を転記する
       https://www.hiratsuka-tower.jp/list
    3. python scripts/compare_hiratsuka.py

出力:
    - 日別・時間帯別の予報 vs 実測値比較テーブル
    - 座標別の誤差統計（プロキシ vs 近傍）
    - 「日次最大値の一律適用」と「時間帯別hourly平均」の精度比較
"""

import csv
import os
from collections import defaultdict

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FORECAST_CSV = os.path.join(BASE_DIR, "data", "hiratsuka_forecast_log.csv")
ACTUAL_CSV   = os.path.join(BASE_DIR, "data", "hiratsuka_actual.csv")

PERIODS     = ["朝", "昼", "夕", "夜"]
PERIOD_KEYS = ["am", "noon", "pm", "eve"]   # forecast CSV の列プレフィックス

COORD_PROXY = "site_proxy"
COORD_NEAR  = "nearshore"


# ── データ読み込み ──────────────────────────────────────────────

def load_forecast(csv_path: str) -> dict:
    """
    予報ログを読み込み、当日予報（fetch_date == target_date）を優先して返す。
    戻り値: {target_date: {coord_label: row_dict}}
    """
    if not os.path.exists(csv_path):
        return {}

    # {(target_date, coord_label): [row_dict, ...]}
    raw = defaultdict(list)
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            raw[(row["target_date"], row["coord_label"])].append(row)

    result = defaultdict(dict)
    for (td, cl), rows in raw.items():
        # 当日予報 (fetch_date == target_date) が最も信頼性が高い
        same_day = [r for r in rows if r["fetch_date"] == td]
        best = same_day[0] if same_day else sorted(rows, key=lambda r: r["fetch_date"])[-1]
        result[td][cl] = best

    return result


def load_actual(csv_path: str) -> dict:
    """
    実測値 CSV を読み込んで {date: {period_key: float}} を返す。
    period_key は "am" / "noon" / "pm" / "eve"。
    """
    if not os.path.exists(csv_path):
        return {}

    result = defaultdict(dict)
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            d = row.get("date", "").strip()
            if not d:
                continue
            for pk in PERIOD_KEYS:
                val = row.get(f"{pk}_wave_m", "").strip()
                if val:
                    try:
                        result[d][pk] = float(val)
                    except ValueError:
                        pass
    return result


# ── テンプレート生成 ──────────────────────────────────────────────

def create_actual_template(forecast: dict) -> None:
    """実測値入力用 CSV テンプレートを生成する。"""
    dates = sorted(forecast.keys())
    with open(ACTUAL_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "date",
            "am_wave_m",   "noon_wave_m", "pm_wave_m", "eve_wave_m",
            "notes",
        ])
        for d in dates:
            writer.writerow([d, "", "", "", "",
                              "平塚タワー https://www.hiratsuka-tower.jp/list から転記"])
    print(f"テンプレート生成: {ACTUAL_CSV}")
    print()
    print("【転記方法】")
    print("  平塚タワー ( https://www.hiratsuka-tower.jp/list ) で各日付を選択し、")
    print("  以下の時間帯の有義波高（H1/3）を転記してください。")
    print("  朝(am)   : 5〜 9時の代表値（平均または最大）")
    print("  昼(noon) : 9〜15時の代表値")
    print("  夕(pm)   :15〜18時の代表値")
    print("  夜(eve)  :18〜22時の代表値")


# ── 比較出力 ──────────────────────────────────────────────

def fmt(v, unit="m") -> str:
    return f"{v:.2f}{unit}" if v is not None else "  — "


def diff_str(forecast_val, actual_val) -> str:
    if forecast_val is None or actual_val is None:
        return "  — "
    d = forecast_val - actual_val
    return f"{d:+.2f}"


def print_comparison(forecast: dict, actual: dict) -> None:
    all_dates = sorted(set(list(forecast.keys())))
    has_actual_dates = [d for d in all_dates if d in actual and actual[d]]

    print("\n" + "=" * 90)
    print("  平塚海岸  波浪予報 vs 実測値 比較レポート")
    print("=" * 90)

    # ── 誤差集計用 ──
    errs = {
        "proxy_daily": [],
        "near_daily":  [],
        "proxy_hourly": {pk: [] for pk in PERIOD_KEYS},
        "near_hourly":  {pk: [] for pk in PERIOD_KEYS},
    }

    for target_date in all_dates:
        fc = forecast.get(target_date, {})
        ac = actual.get(target_date, {})

        proxy = fc.get(COORD_PROXY)
        near  = fc.get(COORD_NEAR)

        daily_proxy = float(proxy["daily_wh_max_m"]) if proxy and proxy.get("daily_wh_max_m") else None
        daily_near  = float(near["daily_wh_max_m"])  if near  and near.get("daily_wh_max_m")  else None

        def hourly_val(row, pk):
            col = f"{pk}_wh_m"
            v = row.get(col, "").strip() if row else ""
            return float(v) if v else None

        has_ac = bool(ac)

        print(f"\n── {target_date} ──")
        print(f"  {'時間帯':<5} {'予報(proxy日次)':<16} {'予報(near日次)':<16} "
              f"{'予報(proxy時別)':<16} {'予報(near時別)':<16} "
              f"{'実測値':<10} {'誤差(proxy)':<12} {'誤差(near)'}")

        for pk, label in zip(PERIOD_KEYS, PERIODS):
            hv_proxy = hourly_val(proxy, pk)
            hv_near  = hourly_val(near,  pk)
            actual_v = ac.get(pk)

            # 誤差集計
            if actual_v is not None:
                if daily_proxy is not None:
                    errs["proxy_daily"].append(daily_proxy - actual_v)
                if daily_near is not None:
                    errs["near_daily"].append(daily_near - actual_v)
                if hv_proxy is not None:
                    errs["proxy_hourly"][pk].append(hv_proxy - actual_v)
                if hv_near is not None:
                    errs["near_hourly"][pk].append(hv_near - actual_v)

            print(f"  {label:<5} "
                  f"{fmt(daily_proxy):<16} {fmt(daily_near):<16} "
                  f"{fmt(hv_proxy):<16} {fmt(hv_near):<16} "
                  f"{fmt(actual_v):<10} "
                  f"{diff_str(daily_proxy, actual_v):<12} "
                  f"{diff_str(daily_near,  actual_v)}")

    # ── 統計サマリー ──
    if not has_actual_dates:
        print("\n実測値がまだ入力されていません。")
        print(f"  → {ACTUAL_CSV} を編集して実測値を入力してください。")
        return

    def stats(vals: list) -> str:
        if not vals:
            return "データなし"
        avg = sum(vals) / len(vals)
        mae = sum(abs(e) for e in vals) / len(vals)
        return f"平均誤差 {avg:+.2f}m, MAE {mae:.2f}m (n={len(vals)})"

    print("\n" + "=" * 90)
    print("  統計サマリー（予報 − 実測）")
    print("=" * 90)
    print(f"  日次最大値（現サイト方式）:")
    print(f"    プロキシ(34.7,139.3): {stats(errs['proxy_daily'])}")
    print(f"    近傍(35.3,139.4):     {stats(errs['near_daily'])}")
    print(f"  時間帯別hourly平均:")
    for pk, label in zip(PERIOD_KEYS, PERIODS):
        pv = stats(errs["proxy_hourly"][pk])
        nv = stats(errs["near_hourly"][pk])
        print(f"    {label}  プロキシ: {pv}")
        print(f"       近傍:     {nv}")

    # ── 総合判定 ──
    all_proxy = errs["proxy_daily"]
    all_near  = errs["near_daily"]
    if all_proxy and all_near:
        avg_p = sum(all_proxy) / len(all_proxy)
        avg_n = sum(all_near)  / len(all_near)
        print()
        if avg_p > 0.1:
            print(f"  → プロキシ座標の予報は実測より平均 {avg_p:+.2f}m 大きい（過大傾向）")
        elif avg_p < -0.1:
            print(f"  → プロキシ座標の予報は実測より平均 {avg_p:+.2f}m 小さい（過小傾向）")
        else:
            print(f"  → プロキシ座標の予報は実測と概ね一致（平均誤差 {avg_p:+.2f}m）")

        if abs(avg_n) < abs(avg_p):
            print(f"  → 近傍座標({avg_n:+.2f}m)の方がプロキシより実測に近い → 座標変更を検討")
        else:
            print(f"  → プロキシ座標の方が精度が高いか同等 → 現状維持が妥当")
    print()


# ── メイン ──────────────────────────────────────────────

def main():
    forecast = load_forecast(FORECAST_CSV)
    if not forecast:
        print(f"[エラー] 予報ログが見つかりません: {FORECAST_CSV}")
        print("先に  python scripts/collect_hiratsuka_forecast.py  を実行してください。")
        return

    actual = load_actual(ACTUAL_CSV)
    if not actual:
        create_actual_template(forecast)
        print("\n実測値を入力後、再度このスクリプトを実行してください。")
        return

    print_comparison(forecast, actual)


if __name__ == "__main__":
    main()
