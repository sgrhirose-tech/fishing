#!/usr/bin/env python3
"""
平塚海岸の波浪予報データを収集して CSV に記録するスクリプト。
毎日実行することで1週間分のログを蓄積し、平塚沖観測塔の実測値と比較する。

使い方:
    python scripts/collect_hiratsuka_forecast.py

cron 例（毎朝 6:00 JST に実行）:
    0 21 * * * cd /path/to/fishing && python3 scripts/collect_hiratsuka_forecast.py >> logs/forecast_collect.log 2>&1

記録内容:
    - 日次最大波高（我々のサイトが現在使用している値）
    - 時間帯別波高（朝5-9h / 昼9-15h / 夕15-18h / 夜18-22h の平均）
    - 2座標を並列取得:
        site_proxy: (34.7, 139.3) ← サイトが相模湾スポットに実際に使うプロキシ
        nearshore:  (35.3, 139.4) ← 平塚海岸の0.1度グリッド最近傍
"""

import csv
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone, timedelta


def _make_ssl_context() -> ssl.SSLContext:
    """macOS Python の SSL 証明書エラーに対応したコンテキストを返す。
    certifi があれば使用し、なければシステム証明書、それも失敗なら検証スキップ。"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

_SSL_CTX = _make_ssl_context()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_CSV = os.path.join(BASE_DIR, "data", "hiratsuka_forecast_log.csv")

LOG_HEADER = [
    "fetch_date", "fetch_time_jst", "target_date",
    "coord_label", "lat", "lon",
    # 日次最大値（現在のサイト表示値）
    "daily_wh_max_m", "daily_wp_max_s",
    # 時間帯別波高（hourly API 平均値。対象外座標では空欄）
    "am_wh_m",    # 朝  5〜 9h
    "noon_wh_m",  # 昼  9〜15h
    "pm_wh_m",    # 夕 15〜18h
    "eve_wh_m",   # 夜 18〜22h
]

JST = timezone(timedelta(hours=9))

# 比較対象の2座標
COORDS = [
    ("site_proxy",  34.7, 139.3),  # サイト実使用: 相模湾プロキシ（沖合）
    ("nearshore",   35.3, 139.4),  # 平塚海岸の0.1度グリッド（近傍）
]

# 時間帯定義（開始時刻〜終了時刻-1h）
TIME_PERIODS = [
    ("am",   5,  9),
    ("noon", 9, 15),
    ("pm",  15, 18),
    ("eve", 18, 22),
]


def _fetch_url(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=15, context=_SSL_CTX) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  [エラー] HTTP {e.code}: {body[:300]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  [エラー] {e}", file=sys.stderr)
        return None


def fetch_marine(lat: float, lon: float, today: str, end: str) -> dict | None:
    """Open-Meteo Marine API から今日〜7日分の波浪予報を取得する。

    まず日次+時間別を試み、400 の場合は日次のみで再試行する（座標によっては
    hourly wave_height が提供されないため）。
    """
    base_params = [
        ("latitude",  lat),
        ("longitude", lon),
        ("daily",  "wave_height_max"),
        ("daily",  "wave_period_max"),
        ("timezone",   "Asia/Tokyo"),
        ("start_date", today),
        ("end_date",   end),
    ]
    base_url = "https://marine-api.open-meteo.com/v1/marine?"

    # 1. 日次 + 時間別（時間帯別精度検証のため）
    params_with_hourly = base_params + [("hourly", "wave_height")]
    data = _fetch_url(base_url + urllib.parse.urlencode(params_with_hourly))
    if data is not None:
        return data

    # 2. 日次のみにフォールバック（hourly が対象外座標の場合）
    print(f"    hourly 取得失敗 → 日次のみで再試行", file=sys.stderr)
    return _fetch_url(base_url + urllib.parse.urlencode(base_params))


def period_mean(hourly_vals: list, day_index: int, start_h: int, end_h: int) -> float | None:
    """指定時間帯の時間別波高の平均を返す。"""
    base = day_index * 24
    vals = []
    for h in range(start_h, end_h):
        idx = base + h
        if idx < len(hourly_vals) and hourly_vals[idx] is not None:
            vals.append(hourly_vals[idx])
    return round(sum(vals) / len(vals), 2) if vals else None


def load_existing_keys(csv_path: str) -> set:
    """既存ログから (fetch_date, target_date, coord_label) の集合を返す（重複防止）。"""
    keys = set()
    if not os.path.exists(csv_path):
        return keys
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            keys.add((row["fetch_date"], row["target_date"], row["coord_label"]))
    return keys


def main():
    now_jst    = datetime.now(JST)
    fetch_date = now_jst.strftime("%Y-%m-%d")
    fetch_time = now_jst.strftime("%H:%M")
    today      = date.today().strftime("%Y-%m-%d")
    end        = (date.today() + timedelta(days=6)).strftime("%Y-%m-%d")

    existing_keys = load_existing_keys(LOG_CSV)
    write_header  = not os.path.exists(LOG_CSV)
    rows_added    = 0

    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(LOG_HEADER)

        for coord_label, lat, lon in COORDS:
            print(f"  [{coord_label}] ({lat}, {lon}) 取得中...")
            data = fetch_marine(lat, lon, today, end)
            if not data:
                print("    → データなし（スキップ）")
                continue

            daily  = data.get("daily",  {})
            hourly = data.get("hourly", {})

            dates     = daily.get("time",            [])
            wh_list   = daily.get("wave_height_max", [])
            wp_list   = daily.get("wave_period_max", [])
            hourly_wh = hourly.get("wave_height",    [])

            for day_idx, target_date in enumerate(dates):
                key = (fetch_date, target_date, coord_label)
                if key in existing_keys:
                    continue

                wh = wh_list[day_idx] if day_idx < len(wh_list) else None
                wp = wp_list[day_idx] if day_idx < len(wp_list) else None

                period_vals = {
                    label: period_mean(hourly_wh, day_idx, sh, eh)
                    for label, sh, eh in TIME_PERIODS
                }

                writer.writerow([
                    fetch_date, fetch_time, target_date,
                    coord_label, lat, lon,
                    f"{wh:.2f}" if wh is not None else "",
                    f"{wp:.1f}" if wp is not None else "",
                    period_vals.get("am",   "") if period_vals.get("am")   is not None else "",
                    period_vals.get("noon", "") if period_vals.get("noon") is not None else "",
                    period_vals.get("pm",   "") if period_vals.get("pm")   is not None else "",
                    period_vals.get("eve",  "") if period_vals.get("eve")  is not None else "",
                ])
                existing_keys.add(key)
                rows_added += 1

            print(f"    → {len(dates)} 日分を処理")

    print(f"完了: {rows_added} 行を追加 → {LOG_CSV}")


if __name__ == "__main__":
    main()
