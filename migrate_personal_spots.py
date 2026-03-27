"""
Migrate personal spot JSONs from the claude/add-fishing-direction-dZd1s branch
into the web app's spots/*.json files.

Migrates: latitude, longitude, sea_bearing_deg, bottom_kisugo_score, terrain_summary
Preserves: area hierarchy, access, notes, photo_url, surfer_spot, depth fields
"""

import json
import os
import re

SPOTS_DIR = os.path.join(os.path.dirname(__file__), "spots")

# Personal JSON name → web app slug mapping
NAME_TO_SLUG = {
    "一色海岸": "isshiki",
    "三浦海岸": "miura",
    "久里浜海岸": "kurihama",
    "二宮海岸": "ninomiya",
    "国府津海岸": "kozu",
    "大浜海岸": "ohama",
    "大磯海水浴場": "oiso",
    "守屋海水浴場": "moriya",
    "富津海水浴場": "futtsu",
    "平塚海水浴場": "hiratsuka",
    "御宿海岸": "onjuku",
    "御幸ノ浜": "miyuki",
    "森戸海岸": "morito",
    "津久井浜": "tsukui",
    "長者ヶ崎海岸": "chojakasaki",
    "片瀬東浜海水浴場": "katase_east",
    "片瀬西浜・鵠沼海水浴場": "katase",
    "秋谷海岸": "akiya",
    "辻堂海岸": "tsujido",
    "逗子海岸": "zushi",
    "酒匂海岸": "sakawa",
}


def clean_terrain_summary(summary: str) -> str:
    """Remove シロギス-specific suffix from terrain_summary."""
    # Remove "。シロギス投げ釣り向きの地形" or similar suffixes
    summary = re.sub(r"[。.]\s*シロギス.*$", "", summary)
    return summary.strip()


def migrate_spot(personal_data: dict, slug: str) -> bool:
    """Update a web app spot JSON with data from a personal JSON."""
    spot_path = os.path.join(SPOTS_DIR, f"{slug}.json")
    if not os.path.exists(spot_path):
        print(f"  SKIP: {spot_path} not found")
        return False

    with open(spot_path, encoding="utf-8") as f:
        spot = json.load(f)

    name = personal_data.get("name", "")
    loc = personal_data.get("location", {})
    phys = personal_data.get("physical_features", {})
    derived = personal_data.get("derived_features", {})

    changed = []

    # Update coordinates
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    if lat is not None and lon is not None:
        spot.setdefault("location", {})
        spot["location"]["latitude"] = lat
        spot["location"]["longitude"] = lon
        changed.append(f"lat={lat:.6f}, lon={lon:.6f}")

    # Update sea_bearing_deg
    bearing = phys.get("sea_bearing_deg")
    if bearing is not None:
        spot.setdefault("physical_features", {})
        spot["physical_features"]["sea_bearing_deg"] = round(bearing, 1)
        changed.append(f"sea_bearing_deg={bearing:.1f}")

    # Update bottom_kisugo_score
    score = derived.get("bottom_kisugo_score")
    if score is not None:
        spot.setdefault("derived_features", {})
        spot["derived_features"]["bottom_kisugo_score"] = score
        changed.append(f"bottom_kisugo_score={score}")

    # Update terrain_summary
    terrain = derived.get("terrain_summary")
    if terrain:
        terrain = clean_terrain_summary(terrain)
        spot.setdefault("derived_features", {})
        spot["derived_features"]["terrain_summary"] = terrain
        changed.append(f'terrain_summary="{terrain}"')

    if changed:
        with open(spot_path, "w", encoding="utf-8") as f:
            json.dump(spot, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"  OK  [{name}] → {slug}.json: {', '.join(changed)}")
        return True
    else:
        print(f"  --  [{name}] → {slug}.json: no changes")
        return False


def main():
    # Personal JSONs should be placed in a temp dir or passed as args.
    # Expected: personal JSONs are in a sibling directory "spots_personal/"
    # or provided via stdin. For this migration, we read from spots_personal/.
    personal_dir = os.path.join(os.path.dirname(__file__), "spots_personal")
    if not os.path.isdir(personal_dir):
        print(f"ERROR: {personal_dir} not found.")
        print("Place personal JSON files in spots_personal/ then run this script.")
        return

    updated = 0
    skipped = 0
    unmatched = []

    for filename in sorted(os.listdir(personal_dir)):
        if not filename.endswith(".json") or filename.startswith("_"):
            continue

        personal_path = os.path.join(personal_dir, filename)
        with open(personal_path, encoding="utf-8") as f:
            personal_data = json.load(f)

        name = personal_data.get("name", filename[:-5])
        slug = NAME_TO_SLUG.get(name)

        if slug is None:
            unmatched.append(name)
            print(f"  ??  [{name}] → no slug mapping, skipped")
            skipped += 1
            continue

        if migrate_spot(personal_data, slug):
            updated += 1
        else:
            skipped += 1

    print()
    print(f"Done: {updated} updated, {skipped} skipped")
    if unmatched:
        print(f"Unmatched spots ({len(unmatched)}): {', '.join(unmatched)}")


if __name__ == "__main__":
    main()
