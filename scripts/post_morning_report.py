#!/usr/bin/env python3
"""
Render cron から実行される海況投稿スクリプト。
スケジュール: 55 10,18 * * * (UTC)
  - 18:55 UTC = 3:55 AM JST → morning モード（当日の海況）
  - 10:55 UTC = 7:55 PM JST → evening モード（翌日の海況予報）
"""

import datetime
import sys
import time
from pathlib import Path

# プロジェクトルートを Python パスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.x_poster import AREA_SCHEDULE, post_group  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))


def detect_mode() -> str:
    """起動時刻の JST 時間帯から朝/夜モードを判定する。"""
    return "morning" if datetime.datetime.now(JST).hour < 12 else "evening"


def sleep_until_target(mode: str) -> None:
    """目標時刻まで待機する（最大10分）。
    morning → 4:00 AM JST
    evening → 20:00 PM JST
    """
    now = datetime.datetime.now(JST)
    target_hour = 4 if mode == "morning" else 20
    target = now.replace(hour=target_hour, minute=0, second=0, microsecond=0)
    wait = (target - now).total_seconds()
    if 0 < wait <= 600:
        print(f"[INFO] {target_hour:02d}:00 JST まで {int(wait)} 秒待機します...")
        time.sleep(wait)


def main() -> None:
    mode = detect_mode()
    print(f"[START] {datetime.datetime.now(JST).isoformat()}")
    print(f"[INFO] モード: {mode}")

    sleep_until_target(mode)

    now = datetime.datetime.now(JST)
    if mode == "morning":
        date_str = now.strftime("%Y-%m-%d")
    else:
        date_str = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"[INFO] 投稿日付: {date_str}")

    now = datetime.datetime.now(JST)
    timestamp = now.strftime("%-m/%-d %H:%M")

    results = []
    for i, group in enumerate(AREA_SCHEDULE):
        if i > 0:
            print(f"[INFO] 5分待機中...")
            time.sleep(5 * 60)

        success = post_group(group, date_str, mode=mode, timestamp=timestamp)
        status = "OK" if success else "FAILED"
        label = group["post_label"]
        print(f"[{status}] {label}")
        results.append((label, status))

    print(f"\n[DONE] {datetime.datetime.now(JST).isoformat()}")
    for name, status in results:
        print(f"  {status}: {name}")


if __name__ == "__main__":
    main()
