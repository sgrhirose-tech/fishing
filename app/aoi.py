"""
葵ちゃんコメント生成・キャッシュ・レート制限モジュール。
Web エンドポイント (app/main.py) とバッチスクリプト (tools/generate_aoi_comments.py) の両方から使う。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import smtplib
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).parent.parent
JST = timezone(timedelta(hours=9))

MODELS: dict[str, str] = {
    "haiku":  "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
}
_model_key = os.environ.get("AOI_MODEL", "haiku")
MODEL: str = MODELS.get(_model_key, MODELS["haiku"])
MAX_TOKENS: int = 200
AOI_PROMPT_PATH = ROOT / "aoi_prompt.md"
_AOI_CACHE_DIR  = Path(os.environ.get("AOI_CACHE_DIR", str(ROOT / "data" / "cache")))
_WEB_LOG_PATH   = _AOI_CACHE_DIR / "aoi_web.jsonl"

# in-memory ログ蓄積（同一プロセス内の高速参照用）
_web_log_memory: list[dict] = []
_web_log_memory_lock = threading.Lock()

# ── 方位定数 ──────────────────────────────────────────────────────────────────

COMPASS16_TO_DEG: dict[str, float] = {
    "北": 0,    "北北東": 22.5,  "北東": 45,   "東北東": 67.5,
    "東": 90,   "東南東": 112.5, "南東": 135,  "南南東": 157.5,
    "南": 180,  "南南西": 202.5, "南西": 225,  "西南西": 247.5,
    "西": 270,  "西北西": 292.5, "北西": 315,  "北北西": 337.5,
}

_TIDE_ACTIVITY: dict[str, str] = {
    "大潮": "活発", "中潮": "活発",
    "小潮": "穏やか", "長潮": "穏やか", "若潮": "穏やか",
}

# ── ユーティリティ ────────────────────────────────────────────────────────────

def deg_to_8dir(deg: float) -> str:
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
    """潮汐名から潮の活発さを返す。パース失敗時は None。"""
    for name, activity in _TIDE_ACTIVITY.items():
        if name in tide_info:
            return activity
    return None


def _fmt(v, digits: int = 1) -> str:
    if v is None:
        return "ー"
    return f"{v:.{digits}f}"


def _fmt_precip_mmh(v) -> str:
    if v is None:
        return "-"
    return str(int(round(v)))


def _scrub_placeholders(comment: str, date_label: str, spot_name: str) -> str:
    return (
        comment
        .replace("{date_label}", date_label)
        .replace("{spot_name}", spot_name)
    )


def build_user_message(spot: dict, period: dict, user_tmpl: str,
                       month: int = 0, date_label: str = "明日") -> str:
    """USER テンプレートにスポット・気象データを埋め込んで返す。"""
    sky_raw = period.get("sky", "")
    weather = re.sub(r"[^\w\s・℃°％\-]", "", sky_raw).strip()
    weather = re.sub(r"\s+", " ", weather).strip() or "ー"

    precip_str = period.get("precip", "0.0mm")
    rain = re.sub(r"[^\d.]", "", precip_str) or "0.0"

    spot_type = (spot.get("classification") or {}).get("primary_type") or "fishing_facility"

    spot_facing_deg   = (spot.get("physical_features") or {}).get("sea_bearing_deg")
    wind_dir_compass  = period.get("wind_dir_compass", "ー")
    tide_info         = period.get("tide", "ー")

    spot_facing   = deg_to_8dir(float(spot_facing_deg)) if spot_facing_deg is not None else None
    wind_relative = calc_wind_relative(wind_dir_compass, period.get("wind_speed_raw"), spot_facing_deg)
    tide_activity = calc_tide_activity(tide_info)

    wind_relative_clause = f"（{wind_relative}）" if wind_relative else ""
    tide_activity_clause = f"（潮の動き：{tide_activity}）" if tide_activity else ""
    facing_line          = f"\n釣り場の正面：{spot_facing}" if spot_facing else ""

    mapping = {
        "date_label":           date_label,
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
        "precip_morning":       _fmt_precip_mmh(period.get("precip_max_morning_raw")),
        "precip_noon":          _fmt_precip_mmh(period.get("precip_max_noon_raw")),
        "precip_evening":       _fmt_precip_mmh(period.get("precip_max_evening_raw")),
        "precip_night":         _fmt_precip_mmh(period.get("precip_max_night_raw")),
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


def load_prompt() -> tuple[str, str]:
    """aoi_prompt.md から SYSTEM / USER テキストを返す。"""
    text = AOI_PROMPT_PATH.read_text(encoding="utf-8")
    system_match = re.search(r"## SYSTEM\n(.*?)## USER", text, re.DOTALL)
    user_match   = re.search(r"## USER\n(.*)", text, re.DOTALL)
    if not system_match or not user_match:
        raise ValueError("aoi_prompt.md に ## SYSTEM / ## USER セクションが見つかりません")
    return system_match.group(1).strip(), user_match.group(1).strip()


# ── 気象データ取得 ────────────────────────────────────────────────────────────

def get_spot_targets(spot: dict, targets_spec: list[tuple[str, str]]) -> list[dict]:
    """(date_label, date_str) のリストに対応するスコア結果を返す。

    targets_spec: [("今日", "2026-04-27"), ("明日", "2026-04-28"), ...]
    SSTは1回だけ取得して全日に流用する。
    """
    from .weather import (
        fetch_weather_range, fetch_marine_range,
        fetch_sst_noaa, fetch_marine_with_fallback,
    )
    from .scoring import score_7days
    from .spots import spot_lat, spot_lon, assign_area, get_area_centers

    if not targets_spec:
        return []

    lat, lon = spot_lat(spot), spot_lon(spot)
    dates = [d for _, d in targets_spec]
    start_date, end_date = min(dates), max(dates)

    weather = fetch_weather_range(lat, lon, start_date, end_date)
    marine  = fetch_marine_range(lat, lon, start_date, end_date)
    if not marine:
        marine = fetch_marine_with_fallback(lat, lon, start_date)
    sst = fetch_sst_noaa(lat, lon, start_date)

    area = assign_area(spot)
    area_centers = get_area_centers()
    fetch_km = area_centers[area][2] if area in area_centers else 50

    days = score_7days(spot, weather, marine, sst=sst, fetch_km=fetch_km)
    by_date = {d.get("date"): d for d in days}

    out = []
    for label, date_str in targets_spec:
        day = by_date.get(date_str)
        if day:
            out.append({"date_label": label, "date": date_str, "day": day})
    return out


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


# ── API 呼び出し ──────────────────────────────────────────────────────────────

def call_claude(system_prompt: str, user_message: str) -> tuple[str, dict]:
    """Claude を呼び出しコメントと usage を返す。MODEL / MAX_TOKENS はモジュール変数を参照。"""
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
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ],
        "messages": [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": "<mode>"},
        ],
    }

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(payload).encode(),
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "prompt-caching-2024-07-31,extended-cache-ttl-2025-04-11",
            "content-type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    comment = "<mode>" + data["content"][0]["text"].strip().replace("\n", "")
    usage = data.get("usage", {})
    return comment, usage


def call_claude_with_retry(system_prompt: str, user_message: str,
                           max_attempts: int = 2) -> tuple[str, dict]:
    """call_claude を1回リトライするラッパー。5xx/タイムアウトのみリトライ、4xx は即 raise。"""
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return call_claude(system_prompt, user_message)
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                raise
            last_err = e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
        if attempt < max_attempts:
            time.sleep(1.0 * attempt)
    assert last_err is not None
    raise last_err


# ── メール送信 ────────────────────────────────────────────────────────────────

def send_mail(subject: str, body: str) -> None:
    """Gmail SMTP でメール送信。環境変数未設定時はスキップ。"""
    mail_from = os.environ.get("MAIL_FROM", "")
    mail_to   = os.environ.get("MAIL_TO", "")
    password  = os.environ.get("MAIL_PASSWORD", "")
    if not (mail_from and mail_to and password):
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


# ── Webエンドポイント生成ログ ─────────────────────────────────────────────────

def _log_web_generation(slug: str, spot_name: str, date_label: str, date_str: str,
                        mode: str, comment: str, user_msg: str, usage: dict) -> None:
    """Web エンドポイント経由で生成したコメントをメモリとファイルに記録する。"""
    record = {
        "ts":          datetime.now(JST).isoformat(),
        "source":      "web",
        "slug":        slug,
        "spot_name":   spot_name,
        "date_label":  date_label,
        "date":        date_str,
        "mode":        mode,
        "comment":     comment,
        "char_len":    len(comment),
        "user_prompt": user_msg,
        "tokens":      usage,
    }
    with _web_log_memory_lock:
        _web_log_memory.append(record)
    try:
        _WEB_LOG_PATH.parent.mkdir(exist_ok=True)
        with open(_WEB_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def get_web_log_records(target_date: str) -> list[dict]:
    """永続ディスクのログファイルから指定日付のレコードを返す（再起動後も読める）。"""
    records: list[dict] = []
    try:
        with open(_WEB_LOG_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get("ts", "").startswith(target_date):
                        records.append(r)
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return records


def clear_web_log_records(before_date: str) -> None:
    """before_date より前の日付のレコードをログファイルから削除する。"""
    try:
        with open(_WEB_LOG_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        kept = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                r = json.loads(stripped)
                if r.get("ts", "")[:10] >= before_date:
                    kept.append(line)
            except json.JSONDecodeError:
                pass
        with open(_WEB_LOG_PATH, "w", encoding="utf-8") as f:
            f.writelines(kept)
    except FileNotFoundError:
        pass
    # in-memory も合わせてクリーン
    with _web_log_memory_lock:
        _web_log_memory[:] = [r for r in _web_log_memory if r.get("ts", "")[:10] >= before_date]


def format_aoi_report(target_date: str, records: list[dict]) -> str:
    """日次レポートテキストを生成する。"""
    lines: list[str] = []
    lines.append(f"=== 葵ちゃんコメント生成レポート {target_date} ===\n")

    total = len(records)
    mode_counter = Counter(r.get("mode", "?") for r in records)

    lines.append(f"合計生成数  : {total}件")
    lines.append("")
    lines.append("モード分布:")
    for mode in ["good", "unsure", "ng", "danger"]:
        lines.append(f"  {mode:8s}: {mode_counter.get(mode, 0)}件")
    lines.append("")

    total_input  = sum(r.get("tokens", {}).get("input_tokens", 0) for r in records)
    total_cache  = sum(r.get("tokens", {}).get("cache_read_input_tokens", 0) for r in records)
    total_output = sum(r.get("tokens", {}).get("output_tokens", 0) for r in records)
    if total > 0:
        cost = (total_input / 1_000_000) * 0.80 * 150 \
             + (total_cache  / 1_000_000) * 0.08 * 150 \
             + (total_output / 1_000_000) * 4.00 * 150
        lines.append(f"推定コスト  : {cost:.1f}円")
        lines.append(f"  入力トークン    : {total_input:,} tok")
        lines.append(f"  キャッシュ読出  : {total_cache:,} tok")
        lines.append(f"  出力トークン    : {total_output:,} tok")
    lines.append("")

    lines.append("=" * 60)
    lines.append("詳細ログ")
    lines.append("=" * 60)

    for r in records:
        ts    = r.get("ts", "")[:19].replace("T", " ")
        name  = r.get("spot_name", r.get("slug", ""))
        label = r.get("date_label", "")
        mode  = r.get("mode", "?")
        tok   = r.get("tokens", {})
        lines.append("")
        lines.append(f"[{ts}] {name} / {label} / mode:{mode}")
        lines.append(
            f"  入力: {tok.get('input_tokens', '-')}tok"
            f"  キャッシュ読出: {tok.get('cache_read_input_tokens', '-')}tok"
            f"  出力: {tok.get('output_tokens', '-')}tok"
        )
        lines.append(f"  {r.get('comment', '')}")

    if not records:
        lines.append("\n（本日の生成レコードなし）")

    lines.append("")
    lines.append(f"--- 生成: {datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST ---")
    return "\n".join(lines)


def send_aoi_report_email(target_date: str, records: list[dict]) -> None:
    """日次レポートをテキストファイル添付でメール送信する。"""
    mail_from = os.environ.get("MAIL_FROM", "")
    mail_to   = os.environ.get("MAIL_TO", "")
    password  = os.environ.get("MAIL_PASSWORD", "")
    if not (mail_from and mail_to and password):
        print(f"[aoi] MAIL 環境変数未設定のためレポートをスキップ ({target_date})")
        return

    total = len(records)
    mode_counter = Counter(r.get("mode", "?") for r in records)
    body_text = (
        f"葵ちゃんコメント生成レポート {target_date}\n\n"
        f"合計: {total}件\n"
        f"モード: good={mode_counter['good']} / unsure={mode_counter['unsure']} "
        f"/ ng={mode_counter['ng']} / danger={mode_counter['danger']}\n\n"
        f"詳細は添付ファイルをご覧ください。"
    )
    report_text = format_aoi_report(target_date, records)

    msg = MIMEMultipart()
    msg["Subject"] = f"[Tsuricast] 葵ちゃんコメント生成ログ {target_date}"
    msg["From"]    = mail_from
    msg["To"]      = mail_to
    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    attachment = MIMEText(report_text, "plain", "utf-8")
    attachment.add_header(
        "Content-Disposition", "attachment",
        filename=f"aoi_report_{target_date}.txt",
    )
    msg.attach(attachment)

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(mail_from, password)
        smtp.sendmail(mail_from, mail_to, msg.as_string())

    print(f"[aoi] 日次レポートメール送信完了: {mail_to} ({total}件)")


# ── モード判定 ────────────────────────────────────────────────────────────────

_MODE_RE = re.compile(r"<mode>(good|unsure|ng|danger)</mode>[ \t]*\n?", re.IGNORECASE)


def parse_mode_from_response(response: str) -> tuple[str, str]:
    """Claude レスポンスから <mode> タグを抽出し (mode, 本文) を返す。
    タグが見つからない・不正な場合は mode='unsure' にフォールバック。
    """
    m = _MODE_RE.search(response)
    if m:
        mode    = m.group(1).lower()
        comment = _MODE_RE.sub("", response, count=1).strip()
    else:
        mode    = "unsure"
        comment = re.sub(r"^<mode>", "", response).strip()
    return mode, comment


def calc_weather_hash(
    wave, wind_speed, precip_morning, precip_noon,
    precip_evening, precip_night, weather: str,
) -> str:
    """気象データの16文字ハッシュ（キャッシュ再生成判断用）。"""
    key = (
        f"{round(float(wave), 1)}_{round(float(wind_speed))}_"
        f"{precip_morning}_{precip_noon}_{precip_evening}_{precip_night}_{weather}"
    )
    return hashlib.sha256(key.encode()).hexdigest()[:16]


# ── レート制限 ────────────────────────────────────────────────────────────────

class AoiRateLimiter:
    """時間帯別の API 呼び出し回数制限。

    キャッシュヒット時はノーカウント。新規生成（API呼び出し）だけを制限する。

    - 昼間 (JST 6:00-22:00): RATE_LIMIT_DAY 回/h
    - 夜間 (JST 22:00-6:00): RATE_LIMIT_NIGHT 回/h（クローラー対策で厳しく）
    - 1日合計: RATE_LIMIT_DAILY 回

    制限超過時はメールで即時通知して False を返す。
    """

    RATE_LIMIT_DAY   = 50
    RATE_LIMIT_NIGHT = 10
    RATE_LIMIT_DAILY = 300
    RATE_LIMIT_IP_DAY   = 20
    RATE_LIMIT_IP_NIGHT = 5

    def __init__(self) -> None:
        self._lock              = threading.Lock()
        self._hourly: dict[str, int] = {}
        self._daily:  dict[str, int] = {}
        self._ip_hourly: dict[str, int] = {}
        self._alerted_hour: set[str] = set()
        self._alerted_daily_date     = ""

    def check_and_consume(self, client_ip: str | None = None) -> bool:
        """呼び出し可能なら True（カウント増）、超過なら False（ノーカウント）。"""
        now      = datetime.now(JST)
        hour_key = now.strftime("%Y-%m-%dT%H")
        date_key = now.strftime("%Y-%m-%d")
        is_night = now.hour < 6 or now.hour >= 22
        limit_h    = self.RATE_LIMIT_NIGHT    if is_night else self.RATE_LIMIT_DAY
        limit_ip_h = self.RATE_LIMIT_IP_NIGHT if is_night else self.RATE_LIMIT_IP_DAY

        with self._lock:
            hourly_count = self._hourly.get(hour_key, 0)
            daily_count  = self._daily.get(date_key, 0)

            if hourly_count >= limit_h:
                if hour_key not in self._alerted_hour:
                    self._alerted_hour.add(hour_key)
                    self._send_alert(
                        f"[Tsuricast 警告] 葵ちゃん時間レート制限超過 {hour_key}",
                        f"{hour_key} の新規生成が上限 {limit_h} 回/h を超えました。"
                        f"\n今日の累計: {daily_count} 回",
                    )
                return False

            if daily_count >= self.RATE_LIMIT_DAILY:
                if self._alerted_daily_date != date_key:
                    self._alerted_daily_date = date_key
                    self._send_alert(
                        f"[Tsuricast 警告] 葵ちゃん1日上限超過 {date_key}",
                        f"{date_key} の新規生成が1日上限 {self.RATE_LIMIT_DAILY} 回を超えました。",
                    )
                return False

            if client_ip:
                ip_key = f"{client_ip}@{hour_key}"
                ip_count = self._ip_hourly.get(ip_key, 0)
                if ip_count >= limit_ip_h:
                    return False
                self._ip_hourly[ip_key] = ip_count + 1

            self._hourly[hour_key] = hourly_count + 1
            self._daily[date_key]  = daily_count  + 1
            return True

    @staticmethod
    def _send_alert(subject: str, body: str) -> None:
        try:
            send_mail(subject, body)
        except Exception:
            pass


_rate_limiter = AoiRateLimiter()


# ── コストアラート ────────────────────────────────────────────────────────────

class AoiCostTracker:
    """日次・月次の API コストを追跡し、閾値超過でメール通知する。

    - 1日合計 ALERT_DAILY_YEN 円超で警告（1日1回）
    - 月間合計 ALERT_MONTHLY_YEN 円超で警告（月1回）
    """

    ALERT_DAILY_YEN   = 300
    ALERT_MONTHLY_YEN = 5_000
    _USD_TO_JPY       = 150

    def __init__(self) -> None:
        self._lock                   = threading.Lock()
        self._daily:  dict[str, float] = {}
        self._monthly: dict[str, float] = {}
        self._alerted_daily:  set[str]  = set()
        self._alerted_monthly: set[str] = set()

    def record(self, usage: dict) -> None:
        """usage dict からコストを計算して累計に加算し、閾値超過をチェックする。"""
        cost = (
            usage.get("input_tokens", 0)                  / 1_000_000 * 0.80 * self._USD_TO_JPY
            + usage.get("cache_creation_input_tokens", 0) / 1_000_000 * 1.00 * self._USD_TO_JPY
            + usage.get("cache_read_input_tokens", 0)     / 1_000_000 * 0.08 * self._USD_TO_JPY
            + usage.get("output_tokens", 0)               / 1_000_000 * 4.00 * self._USD_TO_JPY
        )
        now       = datetime.now(JST)
        date_key  = now.strftime("%Y-%m-%d")
        month_key = now.strftime("%Y-%m")

        with self._lock:
            daily_total   = self._daily.get(date_key, 0.0) + cost
            monthly_total = self._monthly.get(month_key, 0.0) + cost
            self._daily[date_key]    = daily_total
            self._monthly[month_key] = monthly_total

            if daily_total >= self.ALERT_DAILY_YEN and date_key not in self._alerted_daily:
                self._alerted_daily.add(date_key)
                self._send_alert(
                    f"[Tsuricast 警告] 葵ちゃん1日コスト {self.ALERT_DAILY_YEN}円超 {date_key}",
                    f"{date_key} の推定コストが {daily_total:.0f}円 に達しました"
                    f"（上限 {self.ALERT_DAILY_YEN}円）。\n"
                    f"月間累計: {monthly_total:.0f}円",
                )

            if monthly_total >= self.ALERT_MONTHLY_YEN and month_key not in self._alerted_monthly:
                self._alerted_monthly.add(month_key)
                self._send_alert(
                    f"[Tsuricast 警告] 葵ちゃん月間コスト {self.ALERT_MONTHLY_YEN}円超 {month_key}",
                    f"{month_key} の推定コストが {monthly_total:.0f}円 に達しました"
                    f"（上限 {self.ALERT_MONTHLY_YEN}円）。",
                )

    @staticmethod
    def _send_alert(subject: str, body: str) -> None:
        try:
            send_mail(subject, body)
        except Exception:
            pass


_cost_tracker = AoiCostTracker()


# ── コメントキャッシュ ────────────────────────────────────────────────────────

_CACHE_PATH = _AOI_CACHE_DIR / "aoi_cache.json"


class AoiCache:
    """スポットコメントの in-memory + JSON ディスクキャッシュ。

    キャッシュキー: "{slug}:{date_label}:{date}"
      例: "chigasaki-kaigan:今日:2026-04-27"
    値: {comment, mode, weather_hash, expires_at (ISO8601 JST)}
    TTL: 当日 JST 0:00 まで
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load_disk()

    def get(self, key: str) -> dict | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if self._is_expired(entry):
                del self._data[key]
                return None
            return entry

    def set(self, key: str, value: dict) -> None:
        expires_at = self._next_midnight_jst().isoformat()
        entry = {**value, "expires_at": expires_at}
        with self._lock:
            self._data[key] = entry
        self._save_disk()

    @staticmethod
    def _next_midnight_jst() -> datetime:
        now = datetime.now(JST)
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    @staticmethod
    def _is_expired(entry: dict) -> bool:
        try:
            exp = datetime.fromisoformat(entry["expires_at"])
            return datetime.now(JST) >= exp
        except Exception:
            return True

    def _load_disk(self) -> None:
        try:
            with open(_CACHE_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            valid = {k: v for k, v in raw.items() if not self._is_expired(v)}
            self._data = valid
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def _save_disk(self) -> None:
        try:
            _CACHE_PATH.parent.mkdir(exist_ok=True)
            with self._lock:
                snapshot = dict(self._data)
            with open(_CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(snapshot, f, ensure_ascii=False)
        except Exception:
            pass


_cache = AoiCache()

# ── 同時実行制御（同一スポット+日付への並列リクエストを1回に絞る） ────────────

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_LOCK = threading.Lock()


def _get_lock(key: str) -> threading.Lock:
    with _LOCKS_LOCK:
        if key not in _LOCKS:
            _LOCKS[key] = threading.Lock()
        return _LOCKS[key]


# ── メイン生成関数 ────────────────────────────────────────────────────────────

def get_or_generate_comment(
    slug: str,
    spot: dict,
    date_label: str,
    date_str: str,
    client_ip: str | None = None,
) -> dict | None:
    """キャッシュからコメントを返す。なければ生成してキャッシュに保存する。

    Args:
        slug: スポット slug
        spot: スポット dict
        date_label: "今日" or "明日"
        date_str: "2026-04-27" 形式
        client_ip: クライアントIP（IP別レート制限用、省略可）

    Returns:
        {"comment": str, "mode": str} or None (失敗・レート制限超過時)
    """
    cache_key = f"{slug}:{date_label}:{date_str}"

    # 1. キャッシュ確認（ロックなし・レート制限なし）
    cached = _cache.get(cache_key)
    if cached:
        return {"comment": cached["comment"], "mode": cached["mode"]}

    # 2. 同一キーへの並列生成を防ぐロック（最大5秒待機）
    lock = _get_lock(cache_key)
    acquired = lock.acquire(timeout=5.0)
    if not acquired:
        return None

    try:
        # 3. ロック取得後に再確認（別スレッドが先に生成済みの場合）
        cached = _cache.get(cache_key)
        if cached:
            return {"comment": cached["comment"], "mode": cached["mode"]}

        # 4. レート制限チェック（API呼び出し直前にのみ消費）
        if not _rate_limiter.check_and_consume(client_ip):
            return None

        # 5. 気象データ取得
        targets = get_spot_targets(spot, [(date_label, date_str)])
        if not targets:
            return None

        day = targets[0]["day"]
        period = pick_period(day)
        if not period:
            return None

        # 6. プロンプト読み込み
        try:
            system_tmpl, user_tmpl = load_prompt()
        except Exception:
            return None

        # 7. USER部組み立て
        month = int(date_str[5:7])
        user_msg = build_user_message(spot, period, user_tmpl, month=month, date_label=date_label)

        # 8. API呼び出し
        try:
            comment, _usage = call_claude_with_retry(system_tmpl, user_msg)
        except Exception:
            return None

        # 9. モード抽出 → プレースホルダーサニタイズ
        mode, comment = parse_mode_from_response(comment)
        comment = _scrub_placeholders(comment, date_label, spot.get("name", ""))

        # 10. キャッシュ保存
        _cache.set(cache_key, {"comment": comment, "mode": mode})

        # 11. コスト追跡（閾値超過でメール通知）
        _cost_tracker.record(_usage)

        # 12. ログ追記
        _log_web_generation(slug, spot.get("name", slug), date_label, date_str,
                            mode, comment, user_msg, _usage)

        return {"comment": comment, "mode": mode}

    finally:
        lock.release()
