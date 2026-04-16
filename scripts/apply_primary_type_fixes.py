#!/usr/bin/env python3
"""TSV の指示に従い spots/*.json の classification.primary_type を修正する。

入力: docs/fix_order_primary_type.tsv  (カラム: primary_type, fixed_type, name, slug)
"""

import csv
import json
from pathlib import Path

SPOTS_DIR = Path(__file__).parent.parent / "spots"
TSV = Path(__file__).parent.parent / "docs" / "fix_order_primary_type.tsv"


def main():
    with open(TSV, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    ok, missing, mismatch = [], [], []

    for row in rows:
        slug = row["slug"].strip()
        expected_old = row["primary_type"].strip()
        new_type = row["fixed_type"].strip()
        name = row["name"].strip()

        path = SPOTS_DIR / f"{slug}.json"
        if not path.exists():
            missing.append(slug)
            continue

        with open(path, encoding="utf-8") as f:
            spot = json.load(f)

        classification = spot.setdefault("classification", {})
        actual = classification.get("primary_type", "")

        if actual != expected_old:
            mismatch.append((slug, actual, expected_old))
            # 不一致でも指定の値に上書きする（意図的修正なので続行）

        classification["primary_type"] = new_type
        # source を manual に更新してレビュー済みを記録
        classification["source"] = "manual"

        with open(path, "w", encoding="utf-8") as f:
            json.dump(spot, f, ensure_ascii=False, indent=2)
            f.write("\n")

        ok.append((slug, actual, new_type))
        print(f"  [OK] {slug}: {actual} -> {new_type}")

    print()
    print(f"更新: {len(ok)} 件")

    if missing:
        print(f"\n[WARN] JSON が見つからないスラッグ ({len(missing)} 件):")
        for s in missing:
            print(f"  {s}")

    if mismatch:
        print(f"\n[INFO] 既存の primary_type が TSV と異なっていたスラッグ ({len(mismatch)} 件):")
        for slug, actual, expected in mismatch:
            print(f"  {slug}: actual={actual}, expected={expected}")


if __name__ == "__main__":
    main()
