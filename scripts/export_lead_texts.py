#!/usr/bin/env python3
"""施設区分ごとのリード文を TSV に書き出す。

出力: docs/lead_texts_by_type.tsv
カラム: primary_type  name  slug  lead_text
"""

import csv
import json
from pathlib import Path

SPOTS_DIR = Path(__file__).parent.parent / "spots"
OUTPUT = Path(__file__).parent.parent / "docs" / "lead_texts_by_type.tsv"

TYPE_ORDER = [
    "fishing_facility",
    "sand_beach",
    "breakwater",
    "rocky_shore",
    "unknown",
]

def main():
    rows = []
    for path in SPOTS_DIR.glob("*.json"):
        if path.name.startswith("_"):
            continue
        with open(path, encoding="utf-8") as f:
            spot = json.load(f)

        lead = (spot.get("info") or {}).get("lead_text") or ""
        lead = lead.strip()
        if not lead:
            continue

        ptype = (spot.get("classification") or {}).get("primary_type") or "unknown"
        name = spot.get("name", "")
        slug = spot.get("slug", path.stem)

        # 改行・タブをスペースに正規化
        lead_flat = lead.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")

        rows.append({
            "primary_type": ptype,
            "name": name,
            "slug": slug,
            "lead_text": lead_flat,
        })

    # primary_type の指定順でソート（それ以外は末尾、name順）
    def sort_key(r):
        try:
            order = TYPE_ORDER.index(r["primary_type"])
        except ValueError:
            order = len(TYPE_ORDER)
        return (order, r["name"])

    rows.sort(key=sort_key)

    with open(OUTPUT, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["primary_type", "name", "slug", "lead_text"],
                                delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)

    # 区分別サマリー
    from collections import Counter
    counts = Counter(r["primary_type"] for r in rows)
    print(f"[OK] {len(rows)} 件を書き出しました → {OUTPUT}")
    print()
    for t in TYPE_ORDER:
        print(f"  {t}: {counts.get(t, 0)} 件")
    others = sum(v for k, v in counts.items() if k not in TYPE_ORDER)
    if others:
        print(f"  その他: {others} 件")

if __name__ == "__main__":
    main()
