#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
スポット名キーワードによる unknown 分類補完ツール。

OSM自動分類（refetch_physical_data.py --classification-only）後も
unknown のままのスポットを、日本語スポット名のキーワードで補完する。

使い方:
  python tools/classify_by_name.py           # ドライラン（候補一覧表示）
  python tools/classify_by_name.py --apply   # spots/ に書き込み（自動候補のみ）
  python tools/classify_by_name.py --all     # 分類済みも含めた全件表示（確認用）
"""

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# (keyword, classification, confidence)
NAME_KEYWORDS = [
    ("砂浜",   "sand_beach",       0.80),
    ("ビーチ", "sand_beach",       0.75),
    ("海岸",   "sand_beach",       0.70),
    ("浜",     "sand_beach",       0.60),
    ("磯",     "rocky_shore",      0.75),
    ("岩場",   "rocky_shore",      0.80),
    ("崎",     "rocky_shore",      0.55),
    ("鼻",     "rocky_shore",      0.55),
    ("防波堤", "breakwater",       0.90),
    ("堤防",   "breakwater",       0.85),
    ("波止",   "breakwater",       0.85),
    ("テトラ", "breakwater",       0.85),
    ("漁港",   "fishing_facility", 0.90),
    ("岸壁",   "fishing_facility", 0.85),
    ("ふ頭",     "fishing_facility", 0.85),
    ("ふ頭公園", "fishing_facility", 0.85),
    ("埠頭",     "fishing_facility", 0.85),
    ("埠頭公園", "fishing_facility", 0.85),
    ("桟橋",   "fishing_facility", 0.80),
    ("港",     "fishing_facility", 0.75),
]


def match_name(name: str) -> tuple[str | None, float, list[str]]:
    """
    スポット名にキーワードマッチを試みる。

    Returns:
        (classification, confidence, matched_keywords)
        マッチなしは (None, 0.0, [])
        バッティング（同 confidence で異分類）は (None, 0.0, []) で呼び出し元が処理
    """
    # classification → (max_confidence, matched_keywords)
    hits: dict[str, tuple[float, list[str]]] = {}

    for kw, cls, conf in NAME_KEYWORDS:
        if name.endswith(kw):
            if cls not in hits or conf > hits[cls][0]:
                hits[cls] = (conf, [kw])
            elif conf == hits[cls][0]:
                hits[cls][1].append(kw)

    if not hits:
        return None, 0.0, []

    # 最高 confidence を持つ分類を選ぶ
    best_conf = max(c for c, _ in hits.values())
    best_classes = [cls for cls, (c, _) in hits.items() if c == best_conf]

    if len(best_classes) > 1:
        # 同 confidence で異分類バッティング → 要目視
        all_kws = []
        for cls in best_classes:
            all_kws.extend(hits[cls][1])
        return None, best_conf, all_kws  # classification=None でバッティング通知

    cls = best_classes[0]
    return cls, best_conf, hits[cls][1]


def is_unknown(spot: dict) -> bool:
    clf = spot.get("classification")
    if clf is None:
        return True
    return clf.get("primary_type", "unknown") == "unknown"


def load_spots(spots_dir: Path) -> list[tuple[Path, dict]]:
    files = sorted(f for f in spots_dir.glob("*.json") if not f.name.startswith("_"))
    result = []
    for path in files:
        try:
            spot = json.loads(path.read_text(encoding="utf-8"))
            result.append((path, spot))
        except Exception as e:
            print(f"[警告] {path.name} 読み込みエラー: {e}")
    return result


def run(apply: bool, show_all: bool) -> None:
    spots_dir = REPO_ROOT / "spots_wip"
    all_spots = load_spots(spots_dir)

    if not all_spots:
        print(f"{spots_dir} に JSON ファイルが見つかりません。")
        return

    unknowns = [(p, s) for p, s in all_spots if is_unknown(s)]
    classified = [(p, s) for p, s in all_spots if not is_unknown(s)]

    print(f"全スポット: {len(all_spots)}件")
    print(f"unknown / 未分類: {len(unknowns)}件")
    print(f"分類済み: {len(classified)}件")

    if show_all:
        print(f"\n{'─'*70}")
        print("【分類済みスポット一覧】")
        print(f"  {'スラッグ':<30}  {'名前':<20}  {'分類':<20}  conf  source")
        print(f"  {'─'*30}  {'─'*20}  {'─'*20}  ────  ──────")
        for _, spot in classified:
            clf = spot.get("classification", {})
            slug = spot.get("slug", "?")
            name = spot.get("name", "?")
            pt   = clf.get("primary_type", "?")
            conf = clf.get("confidence", 0)
            src  = clf.get("source", "?")
            print(f"  {slug:<30}  {name:<20}  {pt:<20}  {conf:.2f}  {src}")

    if not unknowns:
        print("\nunknown スポットはありません。")
        return

    # マッチング
    auto_candidates: list[tuple[str, str, str, float, list[str]]] = []  # (slug, name, cls, conf, kws)
    batting:         list[tuple[str, str, float, list[str]]] = []        # (slug, name, conf, kws)
    park_only:       list[tuple[str, str]] = []                          # (slug, name)
    no_match:        list[tuple[str, str]] = []                          # (slug, name)

    for _, spot in unknowns:
        slug = spot.get("slug", "?")
        name = spot.get("name", "?")
        cls, conf, kws = match_name(name)

        if cls is not None:
            auto_candidates.append((slug, name, cls, conf, kws))
        elif kws:
            # conf != 0 だがバッティング
            batting.append((slug, name, conf, kws))
        elif "公園" in name:
            park_only.append((slug, name))
        else:
            no_match.append((slug, name))

    review_total = len(batting) + len(park_only) + len(no_match)

    print(f"\n{'─'*70}")
    print(f"[自動候補あり] {len(auto_candidates)}件:")
    if auto_candidates:
        print(f"  {'スラッグ':<30}  {'名前':<20}  {'分類':<20}  conf  (キーワード)")
        print(f"  {'─'*30}  {'─'*20}  {'─'*20}  ────  ─────────")
        for slug, name, cls, conf, kws in auto_candidates:
            kw_str = "、".join(kws)
            print(f"  {slug:<30}  {name:<20}  {cls:<20}  {conf:.2f}  ({kw_str})")

    print(f"\n[要目視] {review_total}件:")
    if batting:
        print(f"  # 同confidenceで複数分類がバッティング")
        for slug, name, conf, kws in batting:
            kw_str = "、".join(kws)
            print(f"  {slug:<30}  {name:<20}  → ?  conf={conf:.2f}  kws=({kw_str})")
    if park_only:
        print(f"  # 「公園」のみマッチ（釣り施設 or 岸壁の可能性）")
        for slug, name in park_only:
            print(f"  {slug:<30}  {name:<20}  → ?  ← 釣り施設 or 岸壁の可能性")
    if no_match:
        print(f"  # キーワードなし")
        for slug, name in no_match:
            print(f"  {slug:<30}  {name:<20}  → ?")

    if not apply:
        print(f"\n（ドライラン: 書き込みなし。--apply で書き込み）")
        return

    # --apply: 自動候補のみ書き込み
    print(f"\n{'─'*70}")
    print(f"書き込み中... ({len(auto_candidates)}件)")
    written = 0
    errors = 0
    for slug, name, cls, conf, kws in auto_candidates:
        path = spots_dir / f"{slug}.json"
        try:
            spot = json.loads(path.read_text(encoding="utf-8"))
            spot["classification"] = {
                "primary_type": cls,
                "confidence": conf,
                "secondary_flags": [],
                "source": "name_keyword",
                "osm_evidence": [f"keyword:{kw}" for kw in kws],
            }
            path.write_text(json.dumps(spot, ensure_ascii=False, indent=2), encoding="utf-8")
            written += 1
        except Exception as e:
            print(f"  [エラー] {slug}: {e}")
            errors += 1

    print(f"完了: {written}件 書き込み、{errors}件 エラー")
    if review_total > 0:
        print(f"要目視 {review_total}件 は手動で確認してください。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="スポット名キーワードによる unknown 分類補完"
    )
    parser.add_argument("--apply", action="store_true",
                        help="spots/ に書き込む（自動候補のみ）")
    parser.add_argument("--all", action="store_true",
                        help="分類済みスポットも含めて全件表示")
    args = parser.parse_args()

    run(apply=args.apply, show_all=args.all)


if __name__ == "__main__":
    main()
