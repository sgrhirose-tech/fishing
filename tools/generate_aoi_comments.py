#!/usr/bin/env python3
"""
葵コメント監視バッチ

朝晩2回実行してログに書き出す。表示機能は未実装（モニタリング専用）。

Usage:
    python tools/generate_aoi_comments.py [--slot 朝|夜] [--slugs slug1 slug2 ...]
    python tools/generate_aoi_comments.py --slug zushi-kaigan

Output:
    logs/aoi_comments.jsonl  (JSONL追記)
    stdout に人間が読めるサマリー
    メール送信（MAIL_FROM / MAIL_TO / MAIL_PASSWORD 設定時）
"""

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from app.aoi import (
    COMPASS16_TO_DEG, _TIDE_ACTIVITY,
    MODELS, AOI_PROMPT_PATH,
    deg_to_8dir, calc_wind_relative, calc_tide_activity,
    build_user_message, call_claude_with_retry,
    load_prompt, _fmt, _fmt_precip_mmh, _scrub_placeholders,
    get_spot_targets, pick_period,
    send_mail,
)
import app.aoi as _aoi_mod

JST = timezone(timedelta(hours=9))
LOG_PATH = ROOT / "logs" / "aoi_comments.jsonl"

# モニタリング対象スポット（各施設種別・地域をカバー）
DEFAULT_SLUGS = [
    # sand_beach — 湘南・三浦
    "chigasaki-kaigan",
    "shichirigahama",
    "zushi-kaigan",
    "katase_east",
    "katase",             # 片瀬西浜（江ノ島）
    "oiso",
    # sand_beach — 外房・内房
    "hebara-kaigan",
    "iwai-kaigan",
    # rocky_shore
    "inamuragasaki",
    "katsuura-todai-shita",  # 勝浦灯台下
    # fishing_facility / breakwater
    "hayama-ko",
    "akiya-gyoko",
    "misaki-ko",
    "otsu-shinteibo",
    "kurihama",
    "higashi-ogishima-nishi-koen",  # 東扇島西公園
]


def get_spot_data(spot: dict, tomorrow: str) -> dict | None:
    """[後方互換] 単一日の day を返す。新規利用は get_spot_targets を推奨。"""
    targets = get_spot_targets(spot, [("明日", tomorrow)])
    return targets[0]["day"] if targets else None


def main() -> None:
    parser = argparse.ArgumentParser(description="葵コメント監視バッチ")
    parser.add_argument("--slot", choices=["朝", "夜"], default=None,
                        help="時間帯（省略時は時刻で自動判定）")
    parser.add_argument("--slugs", nargs="+", default=None,
                        help="対象スポットslug（省略時はデフォルトリスト）")
    parser.add_argument("--no-mail", action="store_true",
                        help="メール送信を抑制（ローカルテスト用）")
    parser.add_argument("--model", choices=["haiku", "sonnet"], default=None,
                        help="使用モデル（デフォルト: haiku、環境変数 AOI_MODEL でも指定可）")
    args = parser.parse_args()

    model_key = args.model or os.environ.get("AOI_MODEL", "haiku")
    _aoi_mod.MODEL = MODELS.get(model_key, MODELS["haiku"])

    now = datetime.now(JST)
    slot = args.slot or ("朝" if now.hour < 12 else "夜")
    today    = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    slugs = args.slugs or DEFAULT_SLUGS
    targets_spec = [("今日", today), ("明日", tomorrow)]

    buf = io.StringIO()

    def _p(msg: str = "") -> None:
        print(msg)
        buf.write(msg + "\n")

    _p(f"=== 葵コメント生成 {now.strftime('%Y-%m-%d %H:%M')} JST　スロット:{slot} ===")
    _p(f"対象日: {today} (今日) / {tomorrow} (明日)")
    _p(f"モデル: {_aoi_mod.MODEL}  max_tokens: {_aoi_mod.MAX_TOKENS}"
       f"  対象: {len(slugs)}スポット × {len(targets_spec)}日 = {len(slugs)*len(targets_spec)}コメント")
    _p()

    system_tmpl, user_tmpl = load_prompt()

    from app.spots import load_spot

    LOG_PATH.parent.mkdir(exist_ok=True)
    ok = err = skip = 0

    for slug in slugs:
        spot = load_spot(slug)
        if not spot:
            _p(f"  [SKIP] {slug}: スポット不明 (×{len(targets_spec)})")
            skip += len(targets_spec)
            continue

        spot_name = spot.get("name", slug)

        try:
            targets = get_spot_targets(spot, targets_spec)
        except Exception as e:
            _p(f"  [ERROR] {slug} ({spot_name}): 気象データ取得失敗 — {e}")
            err += len(targets_spec)
            continue

        if not targets:
            _p(f"  [SKIP] {slug} ({spot_name}): 気象データなし (×{len(targets_spec)})")
            skip += len(targets_spec)
            continue

        got_dates = {t["date_label"] for t in targets}
        for label, _ in targets_spec:
            if label not in got_dates:
                _p(f"  [SKIP] {slug} ({spot_name}) {label}: その日の気象データが取得結果に無い")
                skip += 1

        for t in targets:
            label    = t["date_label"]
            date_str = t["date"]
            day      = t["day"]

            p = pick_period(day)
            if not p:
                _p(f"  [SKIP] {slug} ({spot_name}) {label}: periodなし")
                skip += 1
                continue

            user_msg = build_user_message(
                spot, p, user_tmpl,
                month=int(date_str[5:7]),
                date_label=label,
            )

            try:
                comment, usage = call_claude_with_retry(system_tmpl, user_msg)
            except Exception as e:
                _p(f"  [ERROR] {slug} {label}: {e}")
                err += 1
                continue

            comment = _scrub_placeholders(comment, label, spot_name)

            record = {
                "ts":          now.isoformat(),
                "slot":        slot,
                "date_label":  label,
                "date":        date_str,
                "slug":        slug,
                "spot_name":   spot_name,
                "spot_type":   (spot.get("classification") or {}).get("primary_type", ""),
                "wave":        p.get("wave_height_raw"),
                "wind":        p.get("wind_speed_raw"),
                "weather":     p.get("sky", ""),
                "user_prompt": user_msg,
                "comment":     comment,
                "char_len":    len(comment),
                "tokens":      usage,
            }

            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

            wave_str = _fmt(p.get("wave_height_raw")) + "m"
            wind_str = _fmt(p.get("wind_speed_raw")) + "m/s"
            _p(f"  [{slug}] {label} 波{wave_str} 風{wind_str} ({len(comment)}字)")
            _p(f"  --- 入力データ ---")
            for line in user_msg.splitlines():
                _p(f"    {line}")
            _p(f"  --- 生成コメント ---")
            _p(f"    {comment}")
            _p()
            ok += 1

            time.sleep(0.3)  # レート制限対策

    summary = f"完了: 成功{ok}件 / スキップ{skip}件 / エラー{err}件"
    _p(summary)

    if not args.no_mail:
        subject = f"[葵コメント] {today}/{tomorrow} {slot} (成功{ok}件 / スキップ{skip}件 / エラー{err}件)"
        send_mail(subject, buf.getvalue())


if __name__ == "__main__":
    main()
