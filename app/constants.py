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
    "suruga",
    "enshu",
    # 愛知県
    "higashi-mikawa",
    "nishi-mikawa",
    "owari",
    # 三重県
    "mie-north",
    "mie-south",
})

VALID_PREF_SLUGS: frozenset[str] = frozenset({
    "kanagawa",
    "tokyo",
    "chiba",
    "shizuoka",
    "aichi",
    "mie",
})
