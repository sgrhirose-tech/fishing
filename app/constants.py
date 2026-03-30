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
