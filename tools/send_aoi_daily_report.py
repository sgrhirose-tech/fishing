#!/usr/bin/env python3
"""
葵ちゃんコメント日次レポート メール送信

毎日23時(JST)に Render の cron job として実行する。
当日分の生成ログをまとめてテキストファイル添付でメール送信する。

対象ログ:
  - logs/aoi_web.jsonl     : Webエンドポイント経由の生成コメント
  - logs/aoi_comments.jsonl: バッチ経由の生成コメント

Usage:
    python tools/send_aoi_daily_report.py [--date 2026-04-27]
    python tools/send_aoi_daily_report.py --yesterday   # 前日分
"""

import argparse
import json
import os
import smtplib
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

JST = timezone(timedelta(hours=9))

LOG_FILES = {
    "web":   ROOT / "logs" / "aoi_web.jsonl",
    "batch": ROOT / "logs" / "aoi_comments.jsonl",
}


def load_records(target_date: str) -> list[dict]:
    """対象日付の生成レコードを両ログファイルから収集する。ts の日付で絞る。"""
    records = []
    for source, path in LOG_FILES.items():
        if not path.exists():
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get("ts", "").startswith(target_date):
                        r.setdefault("source", source)
                        records.append(r)
                except json.JSONDecodeError:
                    continue
    records.sort(key=lambda r: r.get("ts", ""))
    return records


def format_report(target_date: str, records: list[dict]) -> str:
    """レポートテキストを生成する。"""
    lines = []

    # ── サマリー ──
    lines.append(f"=== 葵ちゃんコメント生成レポート {target_date} ===\n")

    web_recs   = [r for r in records if r.get("source") == "web"]
    batch_recs = [r for r in records if r.get("source") != "web"]
    total = len(records)

    mode_counter = Counter(r.get("mode", "?") for r in records)

    lines.append(f"合計生成数  : {total}件")
    lines.append(f"  Webエンドポイント: {len(web_recs)}件")
    lines.append(f"  バッチ          : {len(batch_recs)}件")
    lines.append("")
    lines.append("モード分布:")
    for mode in ["good", "unsure", "ng", "danger"]:
        lines.append(f"  {mode:8s}: {mode_counter.get(mode, 0)}件")
    lines.append("")

    # コスト概算（Haiku: input $0.80/MTok, output $4.00/MTok、プロンプトキャッシュ読出 $0.08/MTok）
    total_input_tokens  = sum(r.get("tokens", {}).get("input_tokens", 0) for r in records)
    total_cache_read    = sum(r.get("tokens", {}).get("cache_read_input_tokens", 0) for r in records)
    total_output_tokens = sum(r.get("tokens", {}).get("output_tokens", 0) for r in records)

    if total > 0:
        cost_input  = (total_input_tokens  / 1_000_000) * 0.80 * 150   # USD→円換算150
        cost_cache  = (total_cache_read    / 1_000_000) * 0.08 * 150
        cost_output = (total_output_tokens / 1_000_000) * 4.00 * 150
        cost_total  = cost_input + cost_cache + cost_output
        lines.append(f"推定コスト  : {cost_total:.1f}円")
        lines.append(f"  入力トークン    : {total_input_tokens:,} tok")
        lines.append(f"  キャッシュ読出  : {total_cache_read:,} tok")
        lines.append(f"  出力トークン    : {total_output_tokens:,} tok")
    lines.append("")

    # ── 詳細ログ ──
    lines.append("=" * 60)
    lines.append("詳細ログ")
    lines.append("=" * 60)

    for r in records:
        ts       = r.get("ts", "")[:19].replace("T", " ")
        slug     = r.get("slug", "")
        name     = r.get("spot_name", slug)
        label    = r.get("date_label", "")
        mode     = r.get("mode", "?")
        comment  = r.get("comment", "")
        char_len = r.get("char_len", len(comment))
        source   = r.get("source", "")
        tokens   = r.get("tokens", {})
        in_tok   = tokens.get("input_tokens", "-")
        cache_tok= tokens.get("cache_read_input_tokens", "-")
        out_tok  = tokens.get("output_tokens", "-")

        lines.append("")
        lines.append(f"[{ts}] {name} / {label} / mode:{mode} / {source}")
        lines.append(f"  入力: {in_tok}tok  キャッシュ読出: {cache_tok}tok  出力: {out_tok}tok  ({char_len}字)")

        prompt = r.get("user_prompt", "")
        if prompt:
            lines.append("  --- 入力データ ---")
            for pl in prompt.splitlines():
                lines.append(f"    {pl}")

        lines.append("  --- 生成コメント ---")
        lines.append(f"    {comment}")

    if not records:
        lines.append("\n（本日の生成レコードなし）")

    lines.append("")
    lines.append(f"--- 生成: {datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST ---")
    return "\n".join(lines)


def send_report(target_date: str, body_text: str, report_text: str) -> None:
    """レポートをテキストファイル添付でメール送信する。"""
    mail_from = os.environ.get("MAIL_FROM", "")
    mail_to   = os.environ.get("MAIL_TO", "")
    password  = os.environ.get("MAIL_PASSWORD", "")

    if not (mail_from and mail_to and password):
        print("⚠ MAIL_FROM / MAIL_TO / MAIL_PASSWORD が未設定のためメール送信をスキップ")
        print(report_text)
        return

    msg = MIMEMultipart()
    msg["Subject"] = f"[Tsuricast] 葵ちゃんコメント生成ログ {target_date}"
    msg["From"]    = mail_from
    msg["To"]      = mail_to

    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    attachment = MIMEText(report_text, "plain", "utf-8")
    attachment.add_header(
        "Content-Disposition", "attachment",
        filename=f"aoi_report_{target_date}.txt"
    )
    msg.attach(attachment)

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(mail_from, password)
        smtp.sendmail(mail_from, mail_to, msg.as_string())

    print(f"✉ レポートメール送信完了: {mail_to}")


def main() -> None:
    parser = argparse.ArgumentParser(description="葵ちゃんコメント日次レポート送信")
    parser.add_argument("--date", default=None, help="対象日 YYYY-MM-DD（省略時は当日）")
    parser.add_argument("--yesterday", action="store_true", help="前日分を送る")
    args = parser.parse_args()

    now = datetime.now(JST)
    if args.yesterday:
        target_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    else:
        target_date = args.date or now.strftime("%Y-%m-%d")

    print(f"対象日: {target_date}")

    records = load_records(target_date)
    print(f"レコード数: {len(records)}件")

    report_text = format_report(target_date, records)

    total = len(records)
    mode_counter = Counter(r.get("mode", "?") for r in records)
    body_text = (
        f"葵ちゃんコメント生成レポート {target_date}\n\n"
        f"合計: {total}件\n"
        f"モード: good={mode_counter['good']} / unsure={mode_counter['unsure']} "
        f"/ ng={mode_counter['ng']} / danger={mode_counter['danger']}\n\n"
        f"詳細は添付ファイルをご覧ください。"
    )

    send_report(target_date, body_text, report_text)


if __name__ == "__main__":
    main()
