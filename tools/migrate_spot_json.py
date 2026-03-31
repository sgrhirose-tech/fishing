#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
スポットJSON一括移行スクリプト

変更内容:
  - physical_features から depth_near_m, depth_far_m を削除
  - info から photo_url を削除
  - derived_features.terrain_summary → seabed_summary（傾斜キーワードを除去）

対象: spots/*.json, unadjusted/*.json

使い方:
  python tools/migrate_spot_json.py          # ドライラン
  python tools/migrate_spot_json.py --apply  # 実際に上書き保存
"""

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

TARGET_DIRS = [
    REPO_ROOT / "spots_wip",
    REPO_ROOT / "unadjusted",
]

# terrain_summary から除去する傾斜キーワード
SLOPE_KEYWORDS = {"急深", "やや急深", "遠浅", "傾斜不明", "地形情報不足"}


def strip_slope(summary: str) -> str:
    """terrain_summary から傾斜キーワードを除去して底質のみを返す。"""
    if not summary:
        return ""
    parts = [p.strip() for p in summary.split("、")]
    seabed_parts = [p for p in parts if p not in SLOPE_KEYWORDS]
    return "、".join(seabed_parts)


def migrate_spot(data: dict) -> tuple[dict, list[str]]:
    """スポットdictを移行し、(新dict, 変更リスト) を返す。"""
    changes = []

    # ── physical_features ──
    pf = data.get("physical_features", {})
    for key in ("depth_near_m", "depth_far_m"):
        if key in pf:
            pf.pop(key)
            changes.append(f"physical_features.{key} 削除")

    # ── info ──
    info = data.get("info", {})
    if "photo_url" in info:
        info.pop("photo_url")
        changes.append("info.photo_url 削除")

    # ── derived_features ──
    df = data.get("derived_features", {})
    if "terrain_summary" in df:
        old_val = df.pop("terrain_summary")
        new_val = strip_slope(old_val)
        df["seabed_summary"] = new_val
        if old_val != new_val:
            changes.append(f"terrain_summary→seabed_summary: {old_val!r} → {new_val!r}")
        else:
            changes.append(f"terrain_summary→seabed_summary（値変更なし）: {new_val!r}")
    elif "seabed_summary" not in df:
        df["seabed_summary"] = ""
        changes.append("seabed_summary 追加（空）")

    return data, changes


def process_dir(d: Path, apply: bool) -> tuple[int, int]:
    files = sorted(f for f in d.glob("*.json") if not f.name.startswith("_"))
    changed = total = 0
    for f in files:
        total += 1
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [スキップ] {f.name}: {e}")
            continue

        new_data, changes = migrate_spot(data)
        if not changes:
            continue
        changed += 1
        print(f"  {f.name}:")
        for c in changes:
            print(f"    - {c}")
        if apply:
            f.write_text(json.dumps(new_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed, total


def main():
    apply = "--apply" in sys.argv
    if not apply:
        print("【ドライラン】変更内容を表示します。適用するには --apply を付けてください。\n")
    else:
        print("【適用モード】ファイルを上書き保存します。\n")

    total_changed = total_files = 0
    for d in TARGET_DIRS:
        if not d.exists():
            print(f"--- {d.name}/ (存在しないためスキップ)")
            continue
        print(f"--- {d.name}/ ---")
        changed, total = process_dir(d, apply)
        total_changed += changed
        total_files += total
        if changed == 0:
            print("  （変更なし）")
        print()

    action = "修正完了" if apply else "変更対象"
    print(f"{action}: {total_changed}/{total_files} 件")
    if not apply and total_changed > 0:
        print("適用するには: python tools/migrate_spot_json.py --apply")


if __name__ == "__main__":
    main()
