#!/usr/bin/env python3
"""
シロギス釣り場アドバイザー【Pythonista 3 版】
iPhone の「Pythonista 3」アプリで動かすためのバージョンです。

釣り場の固定情報(底質・海方向・地形)は spots/ フォルダ内の JSON から読み込みます。
JSON ファイルは build_spots.py で事前に生成してください。

使い方:
1. build_spots.py を実行して spots/ フォルダを生成
2. このファイルと spots/ フォルダを Pythonista の同じディレクトリに配置
3. 再生ボタンで実行、または Apple Shortcuts から呼び出す
"""

import os
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

JST = timezone(timedelta(hours=9))

# ============================================================
# API キー読み込み (keys.txt から)
# ============================================================

def _load_api_keys(filename="keys.txt"):
    """keys.txt から KEY=VALUE 形式で API キーを読み込み、os.environ にセット。"""
    keys = {}
    p = Path(__file__).parent / filename
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                keys[k.strip()] = v.strip()
    # 後方互換: api_key.txt → ANTHROPIC_API_KEY
    legacy = Path(__file__).parent / "api_key.txt"
    if legacy.exists() and "ANTHROPIC_API_KEY" not in keys:
        keys["ANTHROPIC_API_KEY"] = legacy.read_text(encoding="utf-8").strip()
    # app/ モジュールが os.environ から読めるようにセット
    for k, v in keys.items():
        if k not in os.environ:
            os.environ[k] = v
    return keys


_API_KEYS = _load_api_keys()

# ============================================================
# 共有モジュールのインポート
# ============================================================

from app.spots import (
    load_spots, load_spot,
    spot_lat, spot_lon, spot_name, spot_slug, spot_area, spot_area_name,
    spot_bearing, spot_kisugo, spot_terrain,
    get_marine_proxy_dict, get_area_centers, assign_area,
)
from app.weather import (
    fetch_weather, fetch_marine, fetch_marine_weatherapi,
    fetch_marine_with_fallback, fetch_sst_noaa, estimate_wave_from_wind,
)
from app.scoring import (
    score_spot, direction_label, weather_code_label,
    calc_wind_score, calc_wave_score, calc_temp_score,
    calc_air_temp_score, calc_seabed_score, angle_diff,
)

# ============================================================
# Pythonista 固有モジュール（PC環境では None になる）
# ============================================================

try:
    import dialogs as _dialogs_module
except ImportError:
    _dialogs_module = None

try:
    import clipboard as _clipboard_module
except ImportError:
    _clipboard_module = None

try:
    import console as _console_module
except ImportError:
    _console_module = None


# ============================================================
# Pythonista UI: エリア選択
# ============================================================

def _select_areas(area_names: list):
    """エリアを複数選択して list を返す。全選択/キャンセルなら None を返す。"""
    if _dialogs_module:
        try:
            chosen = _dialogs_module.list_dialog("エリアを選択(複数可)", area_names, multiple=True)
            return chosen if chosen else None
        except TypeError:
            pass  # multiple 非対応の旧バージョン

    # フォールバック: コンソール入力
    print("エリアを選択してください(複数可, カンマ区切り。Enter でスキップ):")
    for i, name in enumerate(area_names, 1):
        print(f"  {i}. {name}")
    try:
        ans = input("番号> ").strip()
        if not ans:
            return None
        selected = []
        for token in ans.split(","):
            token = token.strip()
            if token.isdigit():
                idx = int(token) - 1
                if 0 <= idx < len(area_names):
                    selected.append(area_names[idx])
        return selected or None
    except EOFError:
        return None


# ============================================================
# テキストレポート生成
# ============================================================

RANK_MARKS = ["1位", "2位", "3位", "4位", "5位"]


def generate_report(scored_spots: list, target_date: str) -> str:
    ranked = sorted(scored_spots, key=lambda x: x["total"], reverse=True)
    _dt = datetime.now(JST)
    now_str = f"{_dt.year}年{_dt.month:02d}月{_dt.day:02d}日 {_dt.hour:02d}:{_dt.minute:02d}"

    lines = []
    lines.append("=" * 62)
    lines.append(f"   シロギス釣り場おすすめレポート  {target_date}")
    lines.append("=" * 62)
    lines.append(f"   作成: {now_str} JST")
    lines.append("")

    lines.append("【おすすめ釣り場 トップ5】")
    lines.append("")
    for i, r in enumerate(ranked[:5]):
        spot = r["spot"]
        d = r["details"]
        mark = RANK_MARKS[i]
        area = spot_area(spot)

        lines.append(f"  {mark}: {spot_name(spot)}({area})  [{r['total']}点]")
        lines.append(f"         底質   : {d['seabed']}")
        if d.get("terrain"):
            lines.append(f"         地形   : {d['terrain']}")
        lines.append(f"         海水温 : {d['sst']}")
        lines.append(f"         天気   : {d['sky']}")
        lines.append(f"         最高気温: {d['temp_max']}")
        lines.append(f"         朝6時  : {d['temp_6am']}")
        lines.append(f"         波高   : {d['wave_height']}")
        lines.append(f"         周期   : {d['wave_period']}")
        lines.append(f"         風速   : {d['wind_speed']}")
        lines.append(f"         風向   : {d['wind_dir']}")
        lines.append(f"         降水量 : {d['precip']}")

        if d.get("rain_warning"):
            lines.append(f"         !! {d['rain_warning']}")

        sf = d.get("surfer_friendly")
        if sf is False:
            lines.append("         >> オフショア: 釣り場が空きやすい")
        elif sf is True:
            lines.append("         >> オンショア: 向かい風に注意")

        lines.append("")

    lines.append("【エリア別ベスト】")
    areas = {}
    for r in ranked:
        area = spot_area_name(r["spot"])
        if area and area not in areas:
            areas[area] = r
    for area, r in areas.items():
        lines.append(f"  {area}: {spot_name(r['spot'])} ({r['total']}点)")

    lines.append("")
    lines.append("【スコアの見方】")
    lines.append("  105点満点(底質15点 + 風40点 + 波30点 + 水温15点 + 気温5点)")
    lines.append("  雨が多い場合はペナルティあり(最大-30点)")
    lines.append("")
    lines.append("【注意事項】")
    lines.append("  ・予報は数値モデルによる推定値です。出発前に最新情報をご確認ください")
    lines.append("  ・天候の急変には十分注意してください")
    lines.append("  ・気象データ: Open-Meteo API(ECMWFモデル、約9kmメッシュ)")
    lines.append("=" * 62)

    return "\n".join(lines)


def generate_markdown_table(scored_spots: list, target_date: str) -> str:
    """生データをマークダウン表として返す。"""
    ranked = sorted(scored_spots, key=lambda x: x["total"], reverse=True)
    _dt = datetime.now(JST)
    now_str = f"{_dt.year}年{_dt.month:02d}月{_dt.day:02d}日 {_dt.hour:02d}:{_dt.minute:02d}"

    def _fmt(v, fmt="{:.1f}", suffix="", na="-"):
        return fmt.format(v) + suffix if v is not None else na

    lines = [
        f"# シロギス釣り場 生データ — {target_date}",
        f"作成: {now_str} JST",
        "",
        "| 順位 | スポット名 | エリア | 総合点 | 天気 | 最高気温 | 朝6時気温 | 風速 | 風向 | 降水量 | 水温 | 波高 | 周期 | 底質スコア | 波データ元 |",
        "| ---: | --- | --- | ---: | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for i, r in enumerate(ranked, 1):
        d = r["details"]
        wd = d.get("_wind_dir_raw")
        wind_dir_str = direction_label(wd) if wd is not None else "-"
        lines.append(
            f"| {i} "
            f"| {spot_name(r['spot'])} "
            f"| {spot_area(r['spot'])} "
            f"| {r['total']}点 "
            f"| {d.get('sky') or '-'} "
            f"| {_fmt(d.get('_temp_max_raw'), suffix='°C')} "
            f"| {_fmt(d.get('_temp_6am_raw'), suffix='°C')} "
            f"| {_fmt(d.get('_wind_speed_raw'), suffix='m/s')} "
            f"| {wind_dir_str} "
            f"| {_fmt(d.get('_precip_raw'), suffix='mm')} "
            f"| {_fmt(d.get('_sst_raw'), suffix='°C')} "
            f"| {_fmt(d.get('_wave_height_raw'), suffix='m')} "
            f"| {_fmt(d.get('_wave_period_raw'), suffix='s')} "
            f"| {_fmt(d.get('_kisugo_raw'), '{:.0f}')} "
            f"| {d.get('wave_source') or '-'} |"
        )

    return "\n".join(lines)


# ============================================================
# Claude API によるAIコメント（オプション）
# ============================================================

def claude_ai_comment(scored_spots: list) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("[情報] ANTHROPIC_API_KEY が未設定のためAIアドバイスをスキップします")
        return ""

    ranked = sorted(scored_spots, key=lambda x: x["total"], reverse=True)
    top5 = []
    for i, r in enumerate(ranked[:5]):
        d = r["details"]
        top5.append({
            "rank": i + 1,
            "name": spot_name(r["spot"]),
            "area": spot_area(r["spot"]),
            "score": r["total"],
            "seabed": d["seabed"],
            "terrain": d.get("terrain", ""),
            "sky": d.get("sky", ""),
            "temp_max": d.get("temp_max", ""),
            "temp_6am": d.get("temp_6am", ""),
            "wind_speed": d["wind_speed"],
            "wind_dir": d["wind_dir"],
            "precip": d["precip"],
            "rain_warning": d.get("rain_warning", "なし"),
            "sst": d["sst"],
            "wave_height": d.get("wave_height", d.get("wave", "")),
            "wave_period": d.get("wave_period", ""),
        })

    # ai_prompt.md を読み込み（なければインラインフォールバック）
    prompt_file = Path(__file__).parent / "ai_prompt.md"
    if prompt_file.exists():
        template = prompt_file.read_text(encoding="utf-8")
        prompt = template.replace("{top5_data}", json.dumps(top5, ensure_ascii=False, indent=2))
    else:
        prompt = f"""あなたは投げ釣りでシロギス(白ギス)を専門とする釣りガイドです。
以下の釣り場スコアデータをもとに、明日の釣行計画に役立つ具体的なアドバイスを
日本語で書いてください。

## 上位5釣り場のデータ
{json.dumps(top5, ensure_ascii=False, indent=2)}

## シロギス釣りの基礎知識
- シロギスは砂地を好む魚。岩礁や泥地には少ない
- 適水温は18〜26°C、最も活性が高いのは20〜24°C
- 投げ釣りは追い風(オフショア)だと仕掛けが遠くまで飛ぶ
- 波高0.5m以上は釣りにくい。1m以上は危険
- 大雨の後は海が濁り釣果が落ちやすい

## 出力形式
1. **1位のおすすめポイント**: 具体的なアドバイス(2〜3文)
2. **2位・3位**: 簡単なコメント(各1〜2文)
3. **総合コメント**: 今日の全体的な釣況(1〜2文)

親しみやすい言葉で、釣り師が聞いて役立つ情報を簡潔に伝えてください。"""

    body = json.dumps({
        "model": "claude-opus-4-6",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["content"][0]["text"]
    except Exception as e:
        print(f"[警告] Claude API エラー: {e}")
        return ""


# ============================================================
# メイン処理（Pythonista版）
# ============================================================

def main():
    # 0〜2時台は当日、3時以降は翌日の予報を取得
    now = datetime.now(JST)
    days_ahead = 1 if now.hour >= 3 else 0
    target_date = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    if _console_module:
        _console_module.clear()

    spots = load_spots()
    if not spots:
        print("[エラー] spotsフォルダにデータがありません。")
        print("build_spots.py を先に実行してください。")
        return

    # エリア絞り込み
    area_centers = get_area_centers()
    selected_areas = _select_areas(list(area_centers.keys())) if area_centers else None
    if selected_areas:
        spots = [s for s in spots if assign_area(s) in selected_areas]
        if not spots:
            print(f"[エラー] 選択エリアのスポットが見つかりません: {', '.join(selected_areas)}")
            return

    area_label = f"【{'・'.join(selected_areas)}】" if selected_areas else ""
    print("シロギス釣り場アドバイザー")
    print(f"対象日: {target_date} {area_label}")
    print(f"釣り場数: {len(spots)}か所")
    print("気象・海洋データを取得しています...\n")

    scored_spots = []
    for spot in spots:
        lat = spot_lat(spot)
        lon = spot_lon(spot)
        name = spot_name(spot)
        print(f"  {name}...", end="", flush=True)

        weather = fetch_weather(lat, lon, target_date)
        marine = fetch_marine_weatherapi(lat, lon, target_date)
        if not marine:
            marine = fetch_marine(lat, lon, target_date)
        area = assign_area(spot)
        fetch_km = area_centers[area][2] if area in area_centers else 50
        sst = fetch_sst_noaa(lat, lon, target_date)
        result = score_spot(spot, weather, marine, sst_noaa=sst, fetch_km=fetch_km)
        scored_spots.append(result)

        d = result["details"]
        missing = []
        if d.get("wind_speed") == "データなし":
            missing.append("×風")
        ws = d.get("wave_source")
        if ws is None:
            missing.append("×波")
        elif ws in ("estimate", "open-meteo"):
            missing.append("△波")
        if d.get("sst") == "データなし":
            missing.append("×水温")
        suffix = f"({'、'.join(missing)})" if missing else ""
        print(f" {result['total']}点{suffix}")

    print()
    report = generate_report(scored_spots, target_date)

    ai_text = claude_ai_comment(scored_spots)
    if ai_text:
        report += "\n\n" + "=" * 62 + "\n"
        report += "【AIアドバイス(Claude)】\n"
        report += "=" * 62 + "\n"
        report += ai_text + "\n"
        report += "=" * 62

    print(report)

    if _clipboard_module:
        _clipboard_module.set(report)
        print("\nレポートをクリップボードにコピーしました")
        print("メモ帳やメッセージアプリに貼り付けて使えます")

    # ファイル保存
    results_dir = Path(__file__).parent.parent / "results"
    results_dir.mkdir(exist_ok=True)
    now_str = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    output_file = results_dir / f"fishing_report_{now_str}.txt"
    try:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"レポートを保存しました: {output_file}")
    except Exception as e:
        print(f"[情報] ファイル保存をスキップ: {e}")

    # マークダウン生データ表を保存
    md_table = generate_markdown_table(scored_spots, target_date)
    md_file = results_dir / f"fishing_data_{now_str}.md"
    try:
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(md_table)
        print(f"生データ表を保存しました: {md_file}")
    except Exception as e:
        print(f"[情報] 生データ表の保存をスキップ: {e}")


if __name__ == "__main__":
    main()
