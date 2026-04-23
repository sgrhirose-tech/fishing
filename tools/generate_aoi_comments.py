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
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

JST = timezone(timedelta(hours=9))
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 200
AOI_PROMPT_PATH = ROOT / "aoi_prompt.md"
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


def build_user_message(spot: dict, period: dict, user_tmpl: str) -> str:
    """USER テンプレートに値を埋めて返す。"""
    sky_raw = period.get("sky", "")
    # emoji を除去
    weather = re.sub(r"[^\w\s・℃°％\-]", "", sky_raw).strip()
    weather = re.sub(r"\s+", " ", weather).strip() or "ー"

    # precip は "0.0mm" 形式なので数値部分のみ抽出
    precip_str = period.get("precip", "0.0mm")
    rain = re.sub(r"[^\d.]", "", precip_str) or "0.0"

    spot_type = (spot.get("classification") or {}).get("primary_type") or "fishing_facility"

    mapping = {
        "spot_name":  spot.get("name", ""),
        "weather":    weather,
        "temp":       _fmt(period.get("temp_raw")),
        "wave":       _fmt(period.get("wave_height_raw")),
        "wind_dir":   period.get("wind_dir_compass", "ー"),
        "wind_speed": _fmt(period.get("wind_speed_raw")),
        "period":     _fmt(period.get("wave_period_raw")),
        "sea_temp":   _fmt(period.get("sst_raw")),
        "tide_info":  period.get("tide", "ー"),
        "rain":       rain,
        "spot_type":  spot_type,
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

    comment = data["content"][0]["text"].strip()
    usage = data.get("usage", {})
    return comment, usage


def main() -> None:
    parser = argparse.ArgumentParser(description="葵コメント監視バッチ")
    parser.add_argument("--slot", choices=["朝", "夜"], default=None,
                        help="時間帯（省略時は時刻で自動判定）")
    parser.add_argument("--slugs", nargs="+", default=None,
                        help="対象スポットslug（省略時はデフォルトリスト）")
    args = parser.parse_args()

    now = datetime.now(JST)
    slot = args.slot or ("朝" if now.hour < 12 else "夜")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    slugs = args.slugs or DEFAULT_SLUGS

    print(f"=== 葵コメント生成 {now.strftime('%Y-%m-%d %H:%M')} JST　スロット:{slot}　対象:{tomorrow} ===")
    print(f"モデル: {MODEL}  max_tokens: {MAX_TOKENS}  対象: {len(slugs)}スポット\n")

    system_tmpl, user_tmpl = load_prompt()

    from app.spots import load_spot

    LOG_PATH.parent.mkdir(exist_ok=True)
    ok = err = skip = 0

    for slug in slugs:
        spot = load_spot(slug)
        if not spot:
            print(f"  [SKIP] {slug}: スポット不明")
            skip += 1
            continue

        spot_name = spot.get("name", slug)
        try:
            day = get_spot_data(spot, tomorrow)
            if not day:
                print(f"  [SKIP] {slug} ({spot_name}): 気象データなし")
                skip += 1
                continue

            p = pick_period(day)
            if not p:
                print(f"  [SKIP] {slug} ({spot_name}): periodなし")
                skip += 1
                continue

            user_msg = build_user_message(spot, p, user_tmpl)
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
            print(f"  [{slug}] 波{wave_str} 風{wind_str} ({len(comment)}字)")
            print(f"  --- 入力データ ---")
            for line in user_msg.splitlines():
                print(f"    {line}")
            print(f"  --- 生成コメント ---")
            print(f"    {comment}")
            print()
            ok += 1

        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            print(f"  [ERROR] {slug}: HTTP {e.code} — {body[:120]}")
            err += 1
        except Exception as e:
            print(f"  [ERROR] {slug}: {e}")
            err += 1

        time.sleep(0.3)  # レート制限対策

    print(f"完了: 成功{ok}件 / スキップ{skip}件 / エラー{err}件 → {LOG_PATH}")


if __name__ == "__main__":
    main()
