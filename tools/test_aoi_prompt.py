#!/usr/bin/env python3
import ssl; ssl._create_default_https_context = ssl._create_unverified_context  # ローカル実行時のSSL証明書エラー回避
"""
葵コメント プロトタイププロンプト テスト生成ツール

test_aoi_prompt.md に書いたプロンプトを使って、
駿河湾・遠州灘の全スポットでコメント生成し、結果をメール添付で受け取る。
本番の aoi_prompt.md には一切触れない。

Usage:
    python tools/test_aoi_prompt.py [--slugs slug1 ...] [--area 駿河湾 遠州灘] [--model haiku|sonnet] [--no-mail]

test_aoi_prompt.md の書き方:
    ## SYSTEM のみ   → SYSTEMをテスト版、USERを本番から補完
    ## USER のみ     → USERをテスト版、SYSTEMを本番から補完
    ## SYSTEM + ## USER → 両方テスト版
    空ファイル       → エラー終了
"""

import argparse
import io
import json
import os
import random
import re
import smtplib
import sys
import time
from datetime import datetime, timedelta, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText as _MIMEText
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from app.aoi import (
    MODELS, TONE_HINTS,
    _fmt, _fmt_precip_mmh, _scrub_placeholders,
    build_user_message, call_claude_with_retry,
    get_spot_targets, load_prompt, pick_period,
)
import app.aoi as _aoi_mod

JST = timezone(timedelta(hours=9))
LOG_PATH = ROOT / "logs" / "aoi_test_comments.jsonl"
TEST_PROMPT_PATH = ROOT / "test_aoi_prompt.md"

TARGET_AREAS = ["駿河湾", "遠州灘"]


def load_spots_by_area(area_names: list[str]) -> list[str]:
    """指定エリアの非禁止スポットの slug リストを返す。"""
    spots_dir = ROOT / "spots"
    slugs = []
    for f in sorted(spots_dir.glob("*.json")):
        if f.stem.startswith("_"):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("banned"):
                continue
            if data.get("area", {}).get("area_name") in area_names:
                slugs.append(data["slug"])
        except Exception:
            continue
    return slugs


def load_test_prompt() -> tuple[str, str, str, list[str]]:
    """test_aoi_prompt.md から (system, user, version, used_sections) を返す。

    省略されたセクションは本番 aoi_prompt.md から補完する。
    空ファイルの場合はエラー終了。
    """
    text = TEST_PROMPT_PATH.read_text(encoding="utf-8").strip()
    if not text:
        sys.exit(
            "❌ test_aoi_prompt.md が空です。\n"
            "   ## SYSTEM か ## USER セクション（または両方）を追記してから実行してください。"
        )

    prod_system, prod_user = load_prompt()

    system_match = re.search(r"## SYSTEM\n(.*?)(?=## USER|\Z)", text, re.DOTALL)
    user_match   = re.search(r"## USER\n(.*)", text, re.DOTALL)

    if not system_match and not user_match:
        sys.exit(
            "❌ test_aoi_prompt.md に ## SYSTEM / ## USER セクションが見つかりません。\n"
            "   aoi_prompt.md と同じ形式で記述してください。"
        )

    system = system_match.group(1).strip() if system_match else prod_system
    user   = user_match.group(1).strip()   if user_match   else prod_user

    using = []
    if system_match: using.append("SYSTEM=テスト版")
    else:            using.append("SYSTEM=本番")
    if user_match:   using.append("USER=テスト版")
    else:            using.append("USER=本番")

    lines = text.splitlines()
    version = lines[1].strip() if len(lines) >= 2 else (lines[0].strip() if lines else "unknown")

    return system, user, version, using


def send_mail_with_attachment(
    subject: str,
    body: str,
    attachment_name: str,
    attachment_text: str,
) -> None:
    """Gmail SMTP でメール送信（テキストファイル添付）。環境変数未設定時はスキップ。"""
    mail_from = os.environ.get("MAIL_FROM", "")
    mail_to   = os.environ.get("MAIL_TO", "")
    password  = os.environ.get("MAIL_PASSWORD", "")
    if not (mail_from and mail_to and password):
        print("[mail] MAIL_FROM / MAIL_TO / MAIL_PASSWORD が未設定のため送信スキップ")
        return

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"]    = mail_from
    msg["To"]      = mail_to
    msg.attach(_MIMEText(body, "plain", "utf-8"))

    part = MIMEBase("text", "plain")
    part.set_payload(attachment_text.encode("utf-8"))
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=attachment_name)
    msg.attach(part)

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(mail_from, password)
        smtp.sendmail(mail_from, mail_to, msg.as_string())

    print(f"[mail] 送信完了: {subject}")


def main() -> None:
    parser = argparse.ArgumentParser(description="葵コメント プロトタイププロンプト テスト")
    parser.add_argument("--slugs", nargs="+", default=None,
                        help="対象スポット slug（省略時: --area のエリア全スポット）")
    parser.add_argument("--area", nargs="+", default=None,
                        help=f"対象エリア（省略時: {' '.join(TARGET_AREAS)}）")
    parser.add_argument("--no-mail", action="store_true",
                        help="メール送信を抑制")
    parser.add_argument("--model", choices=["haiku", "sonnet"], default=None,
                        help="使用モデル（デフォルト: haiku）")
    args = parser.parse_args()

    model_key = args.model or os.environ.get("AOI_MODEL", "haiku")
    _aoi_mod.MODEL = MODELS.get(model_key, MODELS["haiku"])

    system_tmpl, user_tmpl, prompt_version, used_sections = load_test_prompt()
    prompt_config_str = ", ".join(used_sections)
    print(f"[プロンプト] {prompt_version}")
    print(f"[プロンプト構成] {prompt_config_str}")

    now      = datetime.now(JST)
    today    = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    targets_spec = [("今日", today), ("明日", tomorrow)]

    target_areas = args.area or TARGET_AREAS
    slugs = args.slugs or load_spots_by_area(target_areas)

    buf = io.StringIO()

    header = (
        f"# {prompt_version}\n"
        f"# プロンプト構成: {prompt_config_str}\n"
        f"# 実行日時: {now.strftime('%Y-%m-%d %H:%M')} JST\n"
        f"# モデル: {_aoi_mod.MODEL}\n"
        f"# 対象エリア: {', '.join(target_areas)}（{len(slugs)}スポット）\n"
        f"{'=' * 40}\n"
    )
    buf.write(header)

    def _p(msg: str = "") -> None:
        print(msg)
        buf.write(msg + "\n")

    _p(f"=== 葵コメント テスト生成 {now.strftime('%Y-%m-%d %H:%M')} JST ===")
    _p(f"対象日: {today} (今日) / {tomorrow} (明日)")
    _p(f"モデル: {_aoi_mod.MODEL}  対象: {len(slugs)}スポット × {len(targets_spec)}日 = {len(slugs)*len(targets_spec)}コメント")
    _p()

    from app.spots import load_spot

    LOG_PATH.parent.mkdir(exist_ok=True)
    ok = err = skip = 0

    for slug in slugs:
        spot = load_spot(slug)
        if not spot:
            _p(f"  [SKIP] {slug}: スポット不明")
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
            _p(f"  [SKIP] {slug} ({spot_name}): 気象データなし")
            skip += len(targets_spec)
            continue

        got_dates = {t["date_label"] for t in targets}
        for label, _ in targets_spec:
            if label not in got_dates:
                _p(f"  [SKIP] {slug} ({spot_name}) {label}: その日の気象データなし")
                skip += 1

        for t in targets:
            label    = t["date_label"]
            date_str = t["date"]
            day      = t["day"]

            p = pick_period(day)
            if not p:
                _p(f"  [SKIP] {slug} ({spot_name}) {label}: period なし")
                skip += 1
                continue

            tone_hint = random.choice(TONE_HINTS)
            user_msg = build_user_message(
                spot, day, user_tmpl,
                month=int(date_str[5:7]),
                date_label=label,
                tone_hint=tone_hint,
            )

            try:
                comment, usage = call_claude_with_retry(system_tmpl, user_msg)
            except Exception as e:
                _p(f"  [ERROR] {slug} {label}: {e}")
                err += 1
                continue

            comment = _scrub_placeholders(comment, label, spot_name)

            record = {
                "ts":            now.isoformat(),
                "slot":          "test",
                "prompt_version": prompt_version,
                "prompt_config": prompt_config_str,
                "date_label":    label,
                "date":          date_str,
                "slug":          slug,
                "spot_name":     spot_name,
                "spot_type":     (spot.get("classification") or {}).get("primary_type", ""),
                "wave":          p.get("wave_height_raw"),
                "wind":          p.get("wind_speed_raw"),
                "weather":       p.get("sky", ""),
                "tone_hint":     tone_hint,
                "user_prompt":   user_msg,
                "comment":       comment,
                "char_len":      len(comment),
                "tokens":        usage,
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

            time.sleep(0.3)

    summary = f"完了: 成功{ok}件 / スキップ{skip}件 / エラー{err}件"
    _p(summary)

    if not args.no_mail:
        ts_str   = now.strftime("%Y%m%d_%H%M%S")
        filename = f"aoi_test_{ts_str}.txt"
        subject  = (
            f"[葵テスト] {today} / モデル: {model_key} / "
            f"{len(slugs)}スポット ({summary})"
        )
        mail_body = f"{prompt_version}\n{prompt_config_str}\n\n{summary}"
        send_mail_with_attachment(subject, mail_body, filename, buf.getvalue())


if __name__ == "__main__":
    main()
