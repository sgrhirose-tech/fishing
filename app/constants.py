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
    # 大阪府
    "osakawan",     # 大阪湾
    # 和歌山県
    "kii-suido-wakayama",  # 紀伊水道（和歌山）
})

VALID_PREF_SLUGS: frozenset[str] = frozenset({
    "kanagawa",
    "tokyo",
    "chiba",
    "shizuoka",
    "aichi",
    "mie",
    "osaka",
    "wakayama",
})

REGIONS: list[dict] = [
    {"slug": "hokkaido", "name": "北海道", "prefs": ["hokkaido"]},
    {"slug": "tohoku",   "name": "東北",   "prefs": ["aomori", "iwate", "miyagi", "akita", "yamagata", "fukushima"]},
    {"slug": "kanto",    "name": "関東",   "prefs": ["ibaraki", "chiba", "tokyo", "kanagawa"]},           # 08茨城 12千葉 13東京 14神奈川
    {"slug": "hokuriku", "name": "北陸",   "prefs": ["niigata", "toyama", "ishikawa", "fukui"]},           # 15新潟 16富山 17石川 18福井
    {"slug": "tokai",    "name": "東海",   "prefs": ["shizuoka", "aichi", "mie"]},                         # 22静岡 23愛知 24三重
    {"slug": "kansai",   "name": "関西",   "prefs": ["kyoto", "osaka", "wakayama"]},                      # 26京都 27大阪 30和歌山
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

# ── スポット並び順用 ──────────────────────────────────────────────

# JIS 都道府県コード
PREF_CODE: dict[str, int] = {
    "chiba":     12,
    "tokyo":     13,
    "kanagawa":  14,
    "shizuoka":  22,
    "aichi":     23,
    "mie":       24,
    "osaka":     27,
    "wakayama":  30,
}

# エリアの地理的並び順 (pref_slug, area_slug) → 順序整数（小=北東）
_AREA_SORT: dict[tuple[str, str], int] = {
    ("chiba",    "tokyobay"):           1,
    ("chiba",    "kujukuri"):           2,
    ("chiba",    "sotobo"):             3,
    ("chiba",    "uchibo"):             4,
    ("tokyo",    "tokyobay"):           1,
    ("kanagawa", "tokyobay"):           1,
    ("kanagawa", "miura"):              2,
    ("kanagawa", "sagamibay"):          3,
    ("shizuoka", "higashi-izu"):        1,
    ("shizuoka", "minami-izu"):         2,
    ("shizuoka", "nishi-izu"):          3,
    ("shizuoka", "suruga-bay"):         4,
    ("shizuoka", "enshu-nada"):         5,
    ("aichi",    "enshu-nada"):         1,
    ("aichi",    "mikawa-bay"):         2,
    ("aichi",    "isewan"):             3,
    ("mie",      "isewan"):             1,
    ("mie",      "shima-minami-ise"):   2,
    ("mie",      "kumano-nada"):        3,
    ("osaka",    "osakawan"):           1,
    ("wakayama", "kii-suido-wakayama"): 1,
    ("wakayama", "kumano-nada"):        2,
}

# JIS 市区町村コード（5桁）
CITY_CODE: dict[str, int] = {
    # 千葉県 (12)
    "chiba":            12100,
    "ichikawa":         12203,
    "kisarazu":         12204,
    "ichihara":         12208,
    "tateyama":         12213,
    "katsuura":         12214,
    "asahi":            12215,
    "sousa":            12220,
    "futtsu":           12221,
    "urayasu":          12227,
    "minamiboso":       12230,
    "isumi":            12231,
    "kamogawa":         12232,
    "yokoshibahikari":  12236,
    "sanmu":            12237,
    "oamishiarasato":   12238,
    "kujukuri":         12421,
    "chousei":          12422,
    "ichinomiya":       12423,
    "onjuku":           12424,
    "kyonan":           12435,
    # 東京都 (13)
    "koto":             13108,
    "ota":              13111,
    # 神奈川県 (14)
    "yokohama":         14100,
    "kawasaki":         14130,
    "odawara":          14201,
    "yokosuka":         14202,
    "hiratsuka":        14203,
    "kamakura":         14204,
    "fujisawa":         14205,
    "chigasaki":        14208,
    "miura":            14209,
    "hayama":           14341,
    "manazuru":         14342,
    "yugawara":         14343,
    "oiso":             14382,
    "ninomiya":         14383,
    # 静岡県 (22)
    "shizuoka":         22100,
    "hamamatsu":        22130,
    "numazu":           22203,
    "atami":            22204,
    "ito":              22207,
    "fuji":             22209,
    "iwata":            22210,
    "yaizu":            22211,
    "kakegawa":         22212,
    "shimoda":          22216,
    "kosai":            22218,
    "izu":              22219,
    "omaezaki":         22220,
    "makinohara":       22223,
    "higashiizu":       22341,
    "minamiizu":        22343,
    "matsuzaki":        22344,
    "nishiizu":         22345,
    "yoshida":          22424,
    # 愛知県 (23)
    "nagoya":           23100,
    "toyohashi":        23201,
    "hekinan":          23207,
    "handa":            23208,
    "nishio":           23212,
    "gamagori":         23213,
    "tokoname":         23214,
    "tokai":            23215,
    "chita":            23219,
    "tahara":           23231,
    "minamichita":      23442,
    "taketoyo":         23443,
    "yatomi":           23462,
    "tobishima":        23463,
    # 三重県 (24)
    "kuwana":           24201,
    "yokkaichi":        24202,
    "tsu":              24203,
    "suzuka":           24205,
    "owase":            24209,
    "kumano":           24212,
    "ise":              24214,
    "toba":             24215,
    "shima":            24216,
    "kawagoe":          24403,
    "meiwa":            24441,
    "minamiise":        24471,
    "taiki":            24472,
    "kihoku":           24543,
    "kiho":             24561,
    # 大阪府 (27)
    "osaka":            27100,
    "sakai":            27140,
    "kishiwada":        27201,
    "izumiotsu":        27208,
    "kaizuka":          27210,
    "takaishi":         27212,
    "izumisano":        27213,
    "sennan":           27214,
    "hannan":           27215,
    "tadaoka":          27321,
    "tajiri":           27322,
    "misaki":           27341,
    # 和歌山県 (30)
    "wakayama":         30201,
    "kainan":           30202,
    "arida":            30204,
    "gobo":             30205,
    "shingu":           30207,
    "tanabe":           30209,
    "yuasa":            30304,
    "hirokawa":         30305,
    "yura":             30341,
    "shirahama":        30423,
    "susami":           30427,
    "kushimoto":        30428,
    "nachikatsuura":    30504,
    "taiji":            30505,
}

# 同一 city_slug が複数都道府県にまたがる場合の上書き
_CITY_CODE_OVERRIDE: dict[tuple[str, str], int] = {
    ("aichi",    "mihama"): 23441,  # 知多郡美浜町
    ("wakayama", "mihama"): 30343,  # 日高郡美浜町
}
