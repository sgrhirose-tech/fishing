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

DEFAULT_MODEL = "claude-haiku-4-5"


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
あなたは日本の海釣りスポットに詳しい専門家です。
指定されたエリアの実在する海釣りスポットを正確に列挙してください。

ルール:
- 実在するスポットのみ（架空・不確かな場所は含めない）
- 漁港・堤防・砂浜・磯・公園・河口・護岸など種別は問わない
- slug は名称のローマ字表記をハイフン区切り・小文字で（例: kamogawa-ko）
- notes は釣り場の特徴と主なターゲット魚種を100字以内で簡潔に
- 同じ場所の別名・重複は避ける

魚種名は必ず以下の標準和名を使うこと（俗称・別名・略称は不可）:
{fish_list}

例: 「シーバス」→「スズキ」、「チヌ」→「クロダイ」、「タイ」→「マダイ」
"""

def build_system_prompt(fish_names: List[str]) -> str:
    fish_list = "、".join(fish_names)
    return _SYSTEM_PROMPT_TEMPLATE.format(fish_list=fish_list)


def build_user_prompt(area_name: str, prefecture: str, count: int) -> str:
    return (
        f"エリア: {area_name}（{prefecture}）\n\n"
        f"このエリアの海釣りスポットを{count}件程度列挙してください。"
    )


# ──────────────────────────────────────────
# API 呼び出し
# ──────────────────────────────────────────

def generate_spots(area_name: str, prefecture: str,
                   count: int, model: str) -> List[SpotEntry]:
    client     = anthropic.Anthropic()  # ANTHROPIC_API_KEY を環境変数から読む
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
    parser.add_argument("--area",       metavar="AREA",
                        help="対象エリア名（例: 三河湾）")
    parser.add_argument("--count",      type=int, default=25,
                        help="生成するスポット数の目安（デフォルト: 25）")
    parser.add_argument("--model",      default=DEFAULT_MODEL,
                        help=f"使用するモデル（デフォルト: {DEFAULT_MODEL}）")
    parser.add_argument("--list-areas", action="store_true",
                        help="対応エリア一覧を表示して終了")
    args = parser.parse_args()

    if args.list_areas:
        print("対応エリア一覧:")
        for area, (slug, pref_slug, prefecture) in AREA_MAP.items():
            print(f"  {area:12s}  ({prefecture})")
        return

    if not args.area:
        parser.print_help()
        sys.exit(1)

    if args.area not in AREA_MAP:
        print(f"[エラー] 未対応のエリアです: {args.area}")
        print("対応エリア: " + "、".join(AREA_MAP.keys()))
        sys.exit(1)

    area_slug, _, prefecture = AREA_MAP[args.area]
    out_path = TSV_DIR / f"{area_slug}.tsv"

    print(f"エリア  : {args.area}（{prefecture}）")
    print(f"目安件数: {args.count}件")
    print(f"モデル  : {args.model}")
    print(f"出力先  : {out_path.relative_to(REPO_ROOT)}")
    print()
    print("Claude API に問い合わせ中...")

    spots = generate_spots(args.area, prefecture, args.count, args.model)

    print(f"→ {len(spots)}件 生成完了\n")
    for s in spots:
        print(f"  {s.name}  ({s.slug})")
        print(f"    {s.notes}")

    write_tsv(spots, args.area, area_slug, out_path)
    print(f"\n→ {out_path.relative_to(REPO_ROOT)} に保存しました")
    print()
    print("次のステップ:")
    print(f"  python3 tools/build_spots.py          # 座標・物理データ補完")
    print(f"  python3 tools/refetch_access.py --apply  # アクセス情報補完")


if __name__ == "__main__":
    main()
