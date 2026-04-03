#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Claude API を使い、指定エリアの海釣りスポット一覧を TSV で生成する。

生成される TSV は build_spots.py の入力フォーマットに準拠。
lat/lon は 0（build_spots.py が Google Places で補完）、
access は空欄（refetch_access.py が Directions API で補完）。

使い方:
  python3 tools/generate_spots.py --area 三河湾
  python3 tools/generate_spots.py --area 伊勢湾 --count 30
  python3 tools/generate_spots.py --area 三河湾 --model claude-haiku-4-5
  python3 tools/generate_spots.py --list-areas      # 対応エリア一覧を表示

出力: tsv/<area_slug>.tsv
"""

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import List

import anthropic
from pydantic import BaseModel

REPO_ROOT        = Path(__file__).parent.parent
TSV_DIR          = REPO_ROOT / "tsv"
FISH_MASTER_PATH = REPO_ROOT / "data" / "fish_master.json"
CONFIG_FILE      = REPO_ROOT / "config.json"


def load_anthropic_key() -> str:
    """config.json から anthropic_api_key を読む。なければ環境変数にフォールバック。"""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        key = cfg.get("anthropic_api_key", "")
        if key and not key.startswith("sk-ant-..."):
            return key
    import os
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        print("[エラー] config.json の anthropic_api_key、または環境変数 ANTHROPIC_API_KEY が未設定です。")
        sys.exit(1)
    return key


def load_fish_names() -> List[str]:
    """fish_master.json から標準和名一覧を読み込む。"""
    with open(FISH_MASTER_PATH, encoding="utf-8") as f:
        return list(json.load(f).keys())

# build_spots.py と共通のエリアマップ
AREA_MAP = {
    "相模湾":       ("sagamibay",         "kanagawa", "神奈川県"),
    "三浦半島":     ("miura",             "kanagawa", "神奈川県"),
    "東京湾":       ("tokyobay",          "kanagawa", "神奈川県"),
    "内房":         ("uchibo",            "chiba",    "千葉県"),
    "外房":         ("sotobo",            "chiba",    "千葉県"),
    "九十九里":     ("kujukuri",          "chiba",    "千葉県"),
    "東伊豆":       ("higashi-izu",       "shizuoka", "静岡県"),
    "南伊豆":       ("minami-izu",        "shizuoka", "静岡県"),
    "西伊豆":       ("nishi-izu",         "shizuoka", "静岡県"),
    "駿河湾":       ("suruga-bay",        "shizuoka", "静岡県"),
    "遠州灘":       ("enshu-nada",        "shizuoka", "静岡県"),
    "三河湾":       ("mikawa-bay",        "aichi",    "愛知県"),
    "伊勢湾":       ("isewan",            "aichi",    "愛知県"),
    "志摩・南伊勢": ("shima-minami-ise",  "mie",      "三重県"),
    "熊野灘":       ("kumano-nada",       "mie",      "三重県"),
    "大阪湾":       ("osakawan",          "osaka",    "大阪府"),
    "播磨灘":       ("harimanada",        "hyogo",    "兵庫県"),
    "淡路島":       ("awajishima",        "hyogo",    "兵庫県"),
    "紀伊水道（和歌山）": ("kii-suido-wakayama", "wakayama", "和歌山県"),
    "紀伊水道（徳島）":   ("kii-suido-tokushima", "tokushima", "徳島県"),
}

DEFAULT_MODEL = "claude-sonnet-4-6"


# ──────────────────────────────────────────
# 構造化出力スキーマ
# ──────────────────────────────────────────

class SpotEntry(BaseModel):
    name:  str  # 釣り場名（日本語）
    slug:  str  # ローマ字スラッグ（ハイフン区切り・小文字）
    notes: str  # 特徴とターゲット魚種（100字以内）


class SpotList(BaseModel):
    spots: List[SpotEntry]


# ──────────────────────────────────────────
# プロンプト
# ──────────────────────────────────────────

_SYSTEM_PROMPT_TEMPLATE = """\
あなたは、日本国内の海釣りスポットデータを整備する実務アシスタントです。
目的は、公開情報をもとに、釣り場データを正確・網羅的・再利用しやすい形で整えることです。
最優先事項
	1.	正確性
	2.	網羅性
	3.	表記統一
	4.	実務でそのまま使えること
	5.	作業を止めすぎないこと
基本方針
	∙	公式情報、自治体情報、施設公式、信頼できる公開釣り場情報を優先して参照する。
	∙	地域区分、市町村、施設名、漁港名、海岸名は、できるだけ公式表記に寄せる。
	∙	重複を避ける。
	∙	不確実な情報は断定せず、必要なら notes に短く含意する。
	∙	ただし、不確実性が一部あっても全体作業は止めず、実務的な最善解で完成させる。
あなたの行動規範
	∙	ユーザーの意図を先読みして、必要な整理を自分で補う。
	∙	出力形式、列順、列名は厳密に守る。
	∙	既存データとの重複確認を必ず行う。
	∙	件数が多くても、雑にせず最後までそろえる。
	∙	説明は最小限でよい。成果物を優先する。
	∙	あいまいな点は、最も自然で実務的な解釈を採用して進める。
	∙	禁止情報や立入規制が強く確認できる場所は除外を検討する。
	∙	slug は URL 向けに英小文字とハイフンで統一する。
	∙	表記ゆれを減らす。
出力列定義
以下の列を省略せずにこの順番で出力してください。
	1.	name
	2.	lat
	3.	lon
	4.	slug
	5.	notes
	6.	access
	7.	area
各列のルール
	∙	name:
	∙	日本語の自然な釣り場名
	∙	漁港、海岸、公園、河口、堤防など実用上の名称を採用
	∙	lat / lon:
	∙	ともに必ず 0 を固定入力する
	∙	slug:
	∙	英小文字 + ハイフン
	∙	可能な限り短く分かりやすく
	∙	重複しないこと
	∙	notes:
	∙	100文字前後までの短い特徴
	∙	魚種名は必ず以下の標準和名を使うこと（俗称・別名・略称は不可）: {fish_list}
	∙	例: 「シーバス」→「スズキ」、「チヌ」→「クロダイ」、「タイ」→「マダイ」
	∙	有料・許可制・時間制限など条件付きの場合は notes の末尾に明記する（例:「有料釣り場」「遊漁券必要」「立入許可要」）
	∙	access:
	∙	空欄固定
	∙	area:
	∙	指定された地域区分名を厳密に使う
網羅方針
	∙	港・漁港・海岸・河口・海浜公園・釣り公園・堤防・護岸・地磯のうち、一般の釣り人が実際に使いやすい場所を優先して拾う。
重複除外ルール
	∙	名前違いでも実質同じ場所なら除外する。
	∙	完全重複は除外する。
	∙	隣接する別ポイントとして意味がある場合のみ分ける。
禁止・注意
	∙	根拠が薄い情報をもっともらしく断定しない。
	∙	実用性の低い観光地名だけを釣り場として入れない。
	∙	沿岸自治体でない場所を混ぜない。
	∙	列順や列名を勝手に変えない。
最終出力ルール
	∙	最終成果物以外は不要。
	∙	説明・補足・作業ログ・コメントは一切出力しない。
	∙	データのみ出力すること。
"""

def build_system_prompt(fish_names: List[str]) -> str:
    fish_list = "、".join(fish_names)
    return _SYSTEM_PROMPT_TEMPLATE.format(fish_list=fish_list)


def build_user_prompt(area_name: str, prefecture: str, count: int) -> str:
    return (
        f"【対象エリア】{area_name}（{prefecture}）\n\n"
        f"【地域区分】{area_name}\n\n"
        f"【除外条件】釣り禁止スポット\n\n"
        f"上記エリアの海釣りスポットを{count}件程度列挙してください。"
    )


# ──────────────────────────────────────────
# API 呼び出し
# ──────────────────────────────────────────

def generate_spots(area_name: str, prefecture: str,
                   count: int, model: str) -> List[SpotEntry]:
    client     = anthropic.Anthropic(api_key=load_anthropic_key())
    fish_names = load_fish_names()

    response = client.messages.parse(
        model=model,
        max_tokens=4096,
        system=build_system_prompt(fish_names),
        messages=[{
            "role": "user",
            "content": build_user_prompt(area_name, prefecture, count),
        }],
        output_format=SpotList,
    )

    return response.parsed_output.spots


# ──────────────────────────────────────────
# TSV 出力
# ──────────────────────────────────────────

def write_tsv(spots: List[SpotEntry], area_name: str,
              area_slug: str, out_path: Path) -> None:
    out_path.parent.mkdir(exist_ok=True)
    fieldnames = ["name", "lat", "lon", "slug", "notes", "access", "area"]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for spot in spots:
            writer.writerow({
                "name":   spot.name,
                "lat":    0,
                "lon":    0,
                "slug":   spot.slug,
                "notes":  spot.notes,
                "access": "",        # refetch_access.py で補完
                "area":   area_name,
            })


# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Claude API を使い、指定エリアの釣りスポット TSV を生成する"
    )
    parser.add_argument("--area",        metavar="AREA",
                        help="対象エリア名（例: 三河湾）")
    parser.add_argument("--prefecture",  metavar="PREF",
                        help="都道府県名で絞り込み（例: 兵庫県）。--area 省略時は全エリアを処理")
    parser.add_argument("--all",         action="store_true",
                        help="AREA_MAP の全エリアを処理")
    parser.add_argument("--count",       type=int, default=25,
                        help="生成するスポット数の目安（デフォルト: 25）")
    parser.add_argument("--model",       default=DEFAULT_MODEL,
                        help=f"使用するモデル（デフォルト: {DEFAULT_MODEL}）")
    parser.add_argument("--list-areas",  action="store_true",
                        help="対応エリア一覧を表示して終了")
    args = parser.parse_args()

    # ── エリアリスト決定 ─────────────────────────────────
    if args.all:
        targets = list(AREA_MAP.items())
    elif args.prefecture:
        targets = [(a, v) for a, v in AREA_MAP.items() if v[2] == args.prefecture]
        if not targets:
            print(f"[エラー] '{args.prefecture}' に対応するエリアがありません")
            print("都道府県名は AREA_MAP の登録名と一致させてください（例: 兵庫県）")
            sys.exit(1)
    elif args.area:
        if args.area not in AREA_MAP:
            print(f"[エラー] 未対応のエリアです: {args.area}")
            print("対応エリア: " + "、".join(AREA_MAP.keys()))
            sys.exit(1)
        targets = [(args.area, AREA_MAP[args.area])]
    elif args.list_areas:
        targets = list(AREA_MAP.items())
    else:
        parser.print_help()
        sys.exit(1)

    # --area + --prefecture 両方指定時の整合チェック
    if args.area and args.prefecture and not args.all:
        _, _, pref = AREA_MAP[args.area]
        if pref != args.prefecture:
            print(f"[エラー] {args.area} は {pref} のエリアです（{args.prefecture} ではありません）")
            sys.exit(1)

    if args.list_areas:
        print(f"対応エリア一覧{f'（{args.prefecture}）' if args.prefecture else ''}:")
        for area, (slug, pref_slug, prefecture) in targets:
            print(f"  {area:12s}  ({prefecture})")
        return

    # ── バッチ処理 ──────────────────────────────────────
    ok_count = skip_count = 0

    for i, (area_name, (area_slug, _, prefecture)) in enumerate(targets, 1):
        if len(targets) > 1:
            print(f"\n[{i}/{len(targets)}] {area_name}（{prefecture}）")
            print("-" * 40)

        out_path = TSV_DIR / f"{area_slug}.tsv"

        print(f"エリア  : {area_name}（{prefecture}）")
        print(f"目安件数: {args.count}件")
        print(f"モデル  : {args.model}")
        print(f"出力先  : {out_path.relative_to(REPO_ROOT)}")
        print()
        print("Claude API に問い合わせ中...")

        try:
            spots = generate_spots(area_name, prefecture, args.count, args.model)
            print(f"→ {len(spots)}件 生成完了\n")
            for s in spots:
                print(f"  {s.name}  ({s.slug})")
                print(f"    {s.notes}")
            write_tsv(spots, area_name, area_slug, out_path)
            print(f"\n→ {out_path.relative_to(REPO_ROOT)} に保存しました")
            ok_count += 1
        except Exception as e:
            print(f"[エラー] {area_name}: {e}")
            skip_count += 1

        if i < len(targets):
            import time as _time
            _time.sleep(2)

    if len(targets) > 1:
        print(f"\n── 完了 ── 成功: {ok_count}件 / エラー: {skip_count}件")
    else:
        print()
        print("次のステップ:")
        print(f"  python3 tools/build_spots.py             # 座標・物理データ補完")
        print(f"  python3 tools/refetch_access.py --apply  # アクセス情報補完")


if __name__ == "__main__":
    main()
