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
import re
import smtplib
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

JST = timezone(timedelta(hours=9))
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 200
AOI_PROMPT_PATH = ROOT / "aoi_prompt.md"
LOG_PATH = ROOT / "logs" / "aoi_comments.jsonl"

# 16方位 → 度数
COMPASS16_TO_DEG: dict[str, float] = {
    "北": 0,   "北北東": 22.5, "北東": 45,  "東北東": 67.5,
    "東": 90,  "東南東": 112.5,"南東": 135, "南南東": 157.5,
    "南": 180, "南南西": 202.5,"南西": 225, "西南西": 247.5,
    "西": 270, "西北西": 292.5,"北西": 315, "北北西": 337.5,
}

# 潮汐名 → 活発さ
_TIDE_ACTIVITY: dict[str, str] = {
    "大潮": "活発", "中潮": "活発",
    "小潮": "穏やか", "長潮": "穏やか", "若潮": "穏やか",
}


def deg_to_8dir(deg: float) -> str:
    """度数を8方位文字列（N/NE/…/NW）に変換。"""
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(deg / 45) % 8]


def calc_wind_relative(
    wind_dir_compass: str, wind_speed_raw, spot_facing_deg
) -> str | None:
    """釣り人から見た風の相対方向を5区分で返す。
    風速 < 1.0 m/s → 'ほぼ無風'
    spot_facing_deg が None またはコンパス未知 → None
    """
    try:
        spd = float(wind_speed_raw)
    except (TypeError, ValueError):
        spd = None
    if spd is not None and spd < 1.0:
        return "ほぼ無風"
    if spot_facing_deg is None:
        return None
    wind_deg = COMPASS16_TO_DEG.get(wind_dir_compass)
    if wind_deg is None:
        return None
    diff = (wind_deg - float(spot_facing_deg) + 360) % 360
    if diff < 22.5 or diff >= 337.5:
        return "向かい風"
    if diff < 67.5:
        return "斜め向かい風"
    if diff < 112.5:
        return "横風"
    if diff < 157.5:
        return "斜め追い風"
    if diff < 202.5:
        return "追い風"
    if diff < 247.5:
        return "斜め追い風"
    if diff < 292.5:
        return "横風"
    return "斜め向かい風"


def calc_tide_activity(tide_info: str) -> str | None:
    """潮汐名から潮の活発さを3区分で返す。パース失敗時は None。"""
    for name, activity in _TIDE_ACTIVITY.items():
        if name in tide_info:
            return activity
    return None

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


def load_prompt() -> tuple[str, str]:
    """aoi_prompt.md から SYSTEM / USER テキストを返す。"""
    text = AOI_PROMPT_PATH.read_text(encoding="utf-8")
    system_match = re.search(r"## SYSTEM\n(.*?)## USER", text, re.DOTALL)
    user_match = re.search(r"## USER\n(.*)", text, re.DOTALL)
    if not system_match or not user_match:
        raise ValueError("aoi_prompt.md に ## SYSTEM / ## USER セクションが見つかりません")
    return system_match.group(1).strip(), user_match.group(1).strip()


def get_spot_data(spot: dict, tomorrow: str) -> dict | None:
    """翌日の朝の気象データを取得してスコアリング済み period を返す。"""
    from app.weather import (
        fetch_weather_range, fetch_marine_range,
        fetch_sst_noaa, fetch_marine_with_fallback,
    )
    from app.scoring import score_7days
    from app.spots import spot_lat, spot_lon, assign_area, get_area_centers

    lat, lon = spot_lat(spot), spot_lon(spot)
    weather = fetch_weather_range(lat, lon, tomorrow, tomorrow)
    marine = fetch_marine_range(lat, lon, tomorrow, tomorrow)
    if not marine:
        marine = fetch_marine_with_fallback(lat, lon, tomorrow)
    sst = fetch_sst_noaa(lat, lon, tomorrow)

    area = assign_area(spot)
    area_centers = get_area_centers()
    fetch_km = area_centers[area][2] if area in area_centers else 50

    days = score_7days(spot, weather, marine, sst=sst, fetch_km=fetch_km)
    if not days:
        return None
    return days[0]


def pick_period(day: dict, pref: str = "朝") -> dict | None:
    """指定時間帯のperiodを返す。なければ best_period、それもなければ最初。"""
    periods = day.get("periods", [])
    for p in periods:
        if p.get("period") == pref:
            return p
    best = day.get("best_period")
    if best:
        for p in periods:
            if p.get("period") == best:
                return p
    return periods[0] if periods else None


def _fmt(v, digits: int = 1) -> str:
    """float を文字列化、None なら ー。"""
    if v is None:
        return "ー"
    return f"{v:.{digits}f}"


def build_user_message(spot: dict, period: dict, user_tmpl: str, month: int = 0) -> str:
    """USER テンプレートに値を埋めて返す。"""
    sky_raw = period.get("sky", "")
    weather = re.sub(r"[^\w\s・℃°％\-]", "", sky_raw).strip()
    weather = re.sub(r"\s+", " ", weather).strip() or "ー"

    precip_str = period.get("precip", "0.0mm")
    rain = re.sub(r"[^\d.]", "", precip_str) or "0.0"

    spot_type = (spot.get("classification") or {}).get("primary_type") or "fishing_facility"

    # --- 拡張3変数 ---
    spot_facing_deg = (spot.get("physical_features") or {}).get("sea_bearing_deg")
    wind_dir_compass = period.get("wind_dir_compass", "ー")
    tide_info = period.get("tide", "ー")

    spot_facing   = deg_to_8dir(float(spot_facing_deg)) if spot_facing_deg is not None else None
    wind_relative = calc_wind_relative(wind_dir_compass, period.get("wind_speed_raw"), spot_facing_deg)
    tide_activity = calc_tide_activity(tide_info)

    # null の場合は括弧ごと / 行ごと省略
    wind_relative_clause  = f"（{wind_relative}）" if wind_relative else ""
    tide_activity_clause  = f"（潮の動き：{tide_activity}）" if tide_activity else ""
    facing_line           = f"\n釣り場の正面：{spot_facing}" if spot_facing else ""

    mapping = {
        "spot_name":            spot.get("name", ""),
        "weather":              weather,
        "temp_min":             _fmt(period.get("temp_min_raw")),
        "temp_max":             _fmt(period.get("temp_max_raw")),
        "wave":                 _fmt(period.get("wave_height_raw")),
        "wind_dir":             wind_dir_compass,
        "wind_speed":           _fmt(period.get("wind_speed_raw")),
        "period":               _fmt(period.get("wave_period_raw")),
        "sea_temp":             _fmt(period.get("sst_raw")),
        "tide_info":            tide_info,
        "rain":                 rain,
        "spot_type":            spot_type,
        "month":                str(month),
        "wind_relative_clause": wind_relative_clause,
        "tide_activity_clause": tide_activity_clause,
        "facing_line":          facing_line,
    }

    msg = user_tmpl
    for k, v in mapping.items():
        msg = msg.replace("{" + k + "}", v)
    return msg


def call_claude(system_prompt: str, user_message: str) -> tuple[str, dict]:
    """Claude Haiku を呼び出しコメントと usage を返す。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY が設定されていません")

    payload = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},  # プロンプトキャッシュで節約
            }
        ],
        "messages": [{"role": "user", "content": user_message}],
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31",
            "content-type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    comment = data["content"][0]["text"].strip().replace("\n", "")
    usage = data.get("usage", {})
    return comment, usage


def send_mail(subject: str, body: str) -> None:
    """Gmail SMTP でメール送信。環境変数未設定時はスキップ。"""
    mail_from = os.environ.get("MAIL_FROM", "")
    mail_to   = os.environ.get("MAIL_TO", "")
    password  = os.environ.get("MAIL_PASSWORD", "")
    if not (mail_from and mail_to and password):
        print("⚠ MAIL_FROM / MAIL_TO / MAIL_PASSWORD が未設定のためメール送信をスキップ")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = mail_from
    msg["To"]      = mail_to

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(mail_from, password)
        smtp.sendmail(mail_from, mail_to, msg.as_string())
    print(f"✉ メール送信: {mail_to}")


def main() -> None:
    parser = argparse.ArgumentParser(description="葵コメント監視バッチ")
    parser.add_argument("--slot", choices=["朝", "夜"], default=None,
                        help="時間帯（省略時は時刻で自動判定）")
    parser.add_argument("--slugs", nargs="+", default=None,
                        help="対象スポットslug（省略時はデフォルトリスト）")
    parser.add_argument("--no-mail", action="store_true",
                        help="メール送信を抑制（ローカルテスト用）")
    args = parser.parse_args()

    now = datetime.now(JST)
    slot = args.slot or ("朝" if now.hour < 12 else "夜")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    slugs = args.slugs or DEFAULT_SLUGS

    buf = io.StringIO()

    def _p(msg: str = "") -> None:
        print(msg)
        buf.write(msg + "\n")

    _p(f"=== 葵コメント生成 {now.strftime('%Y-%m-%d %H:%M')} JST　スロット:{slot}　対象:{tomorrow} ===")
    _p(f"モデル: {MODEL}  max_tokens: {MAX_TOKENS}  対象: {len(slugs)}スポット")
    _p()

    system_tmpl, user_tmpl = load_prompt()

    from app.spots import load_spot

    LOG_PATH.parent.mkdir(exist_ok=True)
    ok = err = skip = 0

    for slug in slugs:
        spot = load_spot(slug)
        if not spot:
            _p(f"  [SKIP] {slug}: スポット不明")
            skip += 1
            continue

        spot_name = spot.get("name", slug)
        try:
            day = get_spot_data(spot, tomorrow)
            if not day:
                _p(f"  [SKIP] {slug} ({spot_name}): 気象データなし")
                skip += 1
                continue

            p = pick_period(day)
            if not p:
                _p(f"  [SKIP] {slug} ({spot_name}): periodなし")
                skip += 1
                continue

            user_msg = build_user_message(spot, p, user_tmpl, month=int(tomorrow[5:7]))
            comment, usage = call_claude(system_tmpl, user_msg)

            record = {
                "ts":          now.isoformat(),
                "slot":        slot,
                "date":        tomorrow,
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
            _p(f"  [{slug}] 波{wave_str} 風{wind_str} ({len(comment)}字)")
            _p(f"  --- 入力データ ---")
            for line in user_msg.splitlines():
                _p(f"    {line}")
            _p(f"  --- 生成コメント ---")
            _p(f"    {comment}")
            _p()
            ok += 1

        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            _p(f"  [ERROR] {slug}: HTTP {e.code} — {body[:120]}")
            err += 1
        except Exception as e:
            _p(f"  [ERROR] {slug}: {e}")
            err += 1

        time.sleep(0.3)  # レート制限対策

    summary = f"完了: 成功{ok}件 / スキップ{skip}件 / エラー{err}件"
    _p(summary)

    if not args.no_mail:
        subject = f"[葵コメント] {tomorrow} {slot} (成功{ok}件 / スキップ{skip}件 / エラー{err}件)"
        send_mail(subject, buf.getvalue())


if __name__ == "__main__":
    main()
