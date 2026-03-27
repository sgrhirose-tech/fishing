"""Apply personal spot data directly to web app spot JSON files."""

import json
import os

SPOTS_DIR = os.path.join(os.path.dirname(__file__), "spots")

# Data extracted from claude/add-fishing-direction-dZd1s branch personal JSONs
# Format: slug -> {lat, lon, bearing, score, terrain}
PERSONAL_DATA = {
    "isshiki":    {"lat": 35.255,              "lon": 139.58,             "bearing": 295.4,  "score": 35,  "terrain": "石・岩主体、近傍に石要素あり、遠浅"},
    "miura":      {"lat": 35.18461319415856,   "lon": 139.65657234191897, "bearing": 120.6,  "score": 85,  "terrain": "砂主体、遠浅"},
    "kurihama":   {"lat": 35.22500870594622,   "lon": 139.71407890319827, "bearing": 117.5,  "score": 80,  "terrain": "砂主体、貝殻混じり、近傍に石要素あり、遠浅"},
    "ninomiya":   {"lat": 35.29530728683009,   "lon": 139.2632102966309,  "bearing": 169.5,  "score": 85,  "terrain": "砂主体、急深寄り"},
    "kozu":       {"lat": 35.277787188529054,  "lon": 139.21023130416873, "bearing": 150.7,  "score": 85,  "terrain": "砂主体、急深寄り"},
    "ohama":      {"lat": 35.2580417,          "lon": 139.5792023,        "bearing": 255.4,  "score": 35,  "terrain": "石・岩主体、近傍に石要素あり、遠浅"},
    "oiso":       {"lat": 35.309269,           "lon": 139.319336,         "bearing": 124.0,  "score": 80,  "terrain": "砂主体、近傍に石要素あり、遠浅"},
    "moriya":     {"lat": 35.137457,           "lon": 140.262372,         "bearing": 230.5,  "score": 35,  "terrain": "石・岩主体、近傍に石要素あり、遠浅"},
    "futtsu":     {"lat": 35.306298442374455,  "lon": 139.81278419494632, "bearing": 196.2,  "score": 85,  "terrain": "砂主体"},
    "hiratsuka":  {"lat": 35.3144875,          "lon": 139.3548865,        "bearing": 179.7,  "score": 85,  "terrain": "砂主体、遠浅"},
    "onjuku":     {"lat": 35.181,              "lon": 140.352956,         "bearing": 164.3,  "score": 85,  "terrain": "砂主体"},
    "miyuki":     {"lat": 35.244917246361666,  "lon": 139.1598701477051,  "bearing": 140.1,  "score": 85,  "terrain": "砂主体、急深寄り"},
    "morito":     {"lat": 35.275965441200434,  "lon": 139.5709562301636,  "bearing": 270.4,  "score": 85,  "terrain": "砂主体、遠浅"},
    "tsukui":     {"lat": 35.19548370283157,   "lon": 139.6678161621094,  "bearing": 135.3,  "score": 35,  "terrain": "石・岩主体、近傍に石要素あり、遠浅"},
    "chojakasaki":{"lat": 35.2544826,          "lon": 139.5796825,        "bearing": 290.4,  "score": 35,  "terrain": "石・岩主体、近傍に石要素あり、遠浅"},
    "katase_east":{"lat": 35.3073343,          "lon": 139.4864056,        "bearing": 160.2,  "score": 80,  "terrain": "砂主体、貝殻混じり、近傍に石要素あり、遠浅"},
    "katase":     {"lat": 35.308771,           "lon": 139.479586,         "bearing": 225.0,  "score": 80,  "terrain": "砂主体、貝殻混じり、近傍に石要素あり、遠浅"},
    "akiya":      {"lat": 35.23699711024434,   "lon": 139.60061073303225, "bearing": 260.2,  "score": 80,  "terrain": "砂主体、近傍に石要素あり、遠浅"},
    "tsujido":    {"lat": 35.3184181572513,    "lon": 139.444055557251,   "bearing": 181.2,  "score": 85,  "terrain": "砂主体、遠浅"},
    "zushi":      {"lat": 35.288773945160344,  "lon": 139.5733165740967,  "bearing": 271.1,  "score": 35,  "terrain": "石・岩主体、近傍に石要素あり、遠浅"},
    "sakawa":     {"lat": 35.2620713,          "lon": 139.1866594,        "bearing": 135.5,  "score": 85,  "terrain": "砂主体、急深寄り"},
}


def main():
    updated = 0
    for slug, data in sorted(PERSONAL_DATA.items()):
        path = os.path.join(SPOTS_DIR, f"{slug}.json")
        if not os.path.exists(path):
            print(f"SKIP: {path} not found")
            continue

        with open(path, encoding="utf-8") as f:
            spot = json.load(f)

        spot["location"]["latitude"]  = data["lat"]
        spot["location"]["longitude"] = data["lon"]
        spot["physical_features"]["sea_bearing_deg"]      = data["bearing"]
        spot["derived_features"]["bottom_kisugo_score"]   = data["score"]
        spot["derived_features"]["terrain_summary"]       = data["terrain"]

        with open(path, "w", encoding="utf-8") as f:
            json.dump(spot, f, ensure_ascii=False, indent=2)
            f.write("\n")

        print(f"OK  {slug}: bearing={data['bearing']}, score={data['score']}, terrain={data['terrain']}")
        updated += 1

    print(f"\nDone: {updated} spots updated.")


if __name__ == "__main__":
    main()
