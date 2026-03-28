#!/usr/bin/env python3
"""
Render cron から実行される朝の海況投稿スクリプト。
毎朝3:55 JST に起動し、4:00 JST から5分間隔で6エリアを投稿する。
"""

import datetime
import sys
import time
from pathlib import Path

# プロジェクトルートを Python パスに追加
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.x_poster import AREA_SCHEDULE, post_area  # noqa: E402

JST = datetime.timezone(datetime.timedelta(hours=9))


def sleep_until_4am_jst() -> None:
    """4:00 AM JST まで待機する（最大10分）。"""
    now = datetime.datetime.now(JST)
    target = now.replace(hour=4, minute=0, second=0, microsecond=0)
    if now >= target:
        return
    wait = (target - now).total_seconds()
    if 0 < wait <= 600:  # 最大10分だけ待つ
        print(f"[INFO] 4:00 AM JST まで {int(wait)} 秒待機します...")
        time.sleep(wait)


def main() -> None:
    print(f"[START] {datetime.datetime.now(JST).isoformat()}")
    sleep_until_4am_jst()

    date_str = datetime.datetime.now(JST).strftime("%Y-%m-%d")
    print(f"[INFO] 投稿日付: {date_str}")

    results = []
    for i, (slug, name, url) in enumerate(AREA_SCHEDULE):
        if i > 0:
            print(f"[INFO] 5分待機中...")
            time.sleep(5 * 60)

        success = post_area(slug, name, url, date_str)
        status = "OK" if success else "FAILED"
        print(f"[{status}] {name}")
        results.append((name, status))

    print(f"\n[DONE] {datetime.datetime.now(JST).isoformat()}")
    for name, status in results:
        print(f"  {status}: {name}")


if __name__ == "__main__":
    main()
