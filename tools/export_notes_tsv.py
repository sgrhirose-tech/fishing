#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spots/*.json から notes フィールドを TSV に書き出す。

使い方:
    python tools/export_notes_tsv.py --pref chiba tokyo kanagawa [--output-dir tsv/notes]

出力ファイル:
    {output-dir}/{pref}_{n}.tsv  (1ファイル最大50件)

TSV フォーマット:
    ヘッダー行 (slug / area_name / name / notes) + エリア区切りコメント行
    # 始まり行・空行は import 時にスキップされる
"""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT   = Path(__file__).parent.parent
SPOTS_DIR   = REPO_ROOT / "spots"
CHUNK_SIZE  = 50


def load_spots(spots_dir: Path, prefs: list[str] | None) -> list[dict]:
    spots = []
    for p in sorted(spots_dir.glob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARNING] {p.name}: {e}", file=sys.stderr)
            continue
        pref_slug = data.get("area", {}).get("pref_slug", "")
        if prefs and pref_slug not in prefs:
            continue
        spots.append({
            "slug":      data.get("slug", p.stem),
            "name":      data.get("name", ""),
            "area_name": data.get("area", {}).get("area_name", ""),
            "pref_slug": pref_slug,
            "notes":     data.get("info", {}).get("notes", ""),
        })
    return spots


def write_chunk(chunk: list[dict], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["slug\tarea_name\tname\tnotes"]
    current_area = None
    for s in chunk:
        if s["area_name"] != current_area:
            current_area = s["area_name"]
            lines.append(f"# === {current_area} ===")
        notes = s["notes"].replace("\t", " ").replace("\n", " ")
        lines.append(f"{s['slug']}\t{s['area_name']}\t{s['name']}\t{notes}")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="spots/*.json の notes を TSV に書き出す")
    parser.add_argument(
        "--pref", nargs="+",
        help="対象の pref_slug（例: chiba tokyo kanagawa）省略時は全スポット",
    )
    parser.add_argument(
        "--output-dir", default=str(REPO_ROOT / "tsv" / "notes"),
        help="出力先ディレクトリ (default: tsv/notes/)",
    )
    args = parser.parse_args()

    spots = load_spots(SPOTS_DIR, args.pref)
    if not spots:
        print("対象スポットが見つかりませんでした。")
        return

    # area_name → slug でソート
    spots.sort(key=lambda s: (s["area_name"], s["slug"]))

    # pref ごとにグループ化
    by_pref: dict[str, list[dict]] = {}
    for s in spots:
        by_pref.setdefault(s["pref_slug"], []).append(s)

    out_dir = Path(args.output_dir)
    generated = []
    for pref, pref_spots in sorted(by_pref.items()):
        # 50件ごとにチャンク
        chunks = [pref_spots[i:i + CHUNK_SIZE] for i in range(0, len(pref_spots), CHUNK_SIZE)]
        for n, chunk in enumerate(chunks, 1):
            fname = f"{pref}_{n}.tsv"
            out_path = out_dir / fname
            write_chunk(chunk, out_path)
            generated.append((out_path, len(chunk)))

    print(f"\n出力完了: {len(generated)} ファイル")
    for p, count in generated:
        print(f"  {p}  ({count}件)")


if __name__ == "__main__":
    main()
