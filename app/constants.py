"""
アプリ全体で使用する定数定義。
slug のバリデーション基準はここが唯一の正解とする。
"""

VALID_AREA_SLUGS: frozenset[str] = frozenset({
    "sagamibay",
    "miura",
    "tokyobay",
    "uchibo",
    "sotobo",
    "kujukuri",
    # 静岡県
    "higashi-izu",
    "minami-izu",
    "nishi-izu",
    "suruga-bay",   # 駿河湾
    "enshu-nada",   # 遠州灘（静岡・愛知 共用）
    # 愛知県
    "mikawa-bay",   # 三河湾
    "isewan",       # 伊勢湾（愛知・三重 共用）
    # 三重県
    "shima-minami-ise",  # 志摩・南伊勢（志摩市・南伊勢町・大紀町）
    "kumano-nada",  # 熊野灘
})

VALID_PREF_SLUGS: frozenset[str] = frozenset({
    "kanagawa",
    "tokyo",
    "chiba",
    "shizuoka",
    "aichi",
    "mie",
})

REGIONS: list[dict] = [
    {"slug": "hokkaido", "name": "北海道", "prefs": ["hokkaido"]},
    {"slug": "tohoku",   "name": "東北",   "prefs": ["aomori", "iwate", "miyagi", "akita", "yamagata", "fukushima"]},
    {"slug": "kanto",    "name": "関東",   "prefs": ["ibaraki", "chiba", "tokyo", "kanagawa"]},           # 08茨城 12千葉 13東京 14神奈川
    {"slug": "hokuriku", "name": "北陸",   "prefs": ["niigata", "toyama", "ishikawa", "fukui"]},           # 15新潟 16富山 17石川 18福井
    {"slug": "tokai",    "name": "東海",   "prefs": ["shizuoka", "aichi", "mie"]},                         # 22静岡 23愛知 24三重
    {"slug": "kansai",   "name": "関西",   "prefs": ["kyoto", "osaka", "hyogo", "wakayama"]},              # 26京都 27大阪 28兵庫 30和歌山
    {"slug": "chugoku",  "name": "中国",   "prefs": ["tottori", "shimane", "okayama", "hiroshima", "yamaguchi"]},
    {"slug": "shikoku",  "name": "四国",   "prefs": ["tokushima", "kagawa", "ehime", "kochi"]},
    {"slug": "kyushu",   "name": "九州",   "prefs": ["fukuoka", "saga", "nagasaki", "kumamoto", "oita", "miyazaki", "kagoshima", "okinawa"]},
]

VALID_REGION_SLUGS: frozenset[str] = frozenset(r["slug"] for r in REGIONS)

# 都道府県slug → 地方slug の逆引き
PREF_TO_REGION: dict[str, str] = {
    pref: r["slug"] for r in REGIONS for pref in r["prefs"]
}

# 地方slug → 地方名の逆引き
REGION_NAMES: dict[str, str] = {r["slug"]: r["name"] for r in REGIONS}
