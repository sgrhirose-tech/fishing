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
})

VALID_PREF_SLUGS: frozenset[str] = frozenset({
    "kanagawa",
    "tokyo",
    "chiba",
})
