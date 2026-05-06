#!/usr/bin/env python3
"""
気象庁 推算潮位表 年次ダウンロードスクリプト。

気象庁の年次潮位推算値テキストファイルをダウンロードし、
data/jma_tides/{station_code}_{YYYY}.json に保存する。

データ仕様:
  出典: 気象庁 (https://www.data.jma.go.jp/kaiyou/db/tide/suisan/)
  ライセンス: CC-BY 4.0 互換（出典表記必要）
  内容: 年次推算潮位表（満潮・干潮時刻・潮高）
  更新頻度: 年1回（翌年分は当年12月頃公開）

ファイルフォーマット（1行1日, 1ファイル1局, UTF-8）:
  bytes  0-71  : 時別潮位 24時間分（3バイト×24 = 72バイト, cm整数）
  bytes 72-73  : 年（2桁 YY, 2000年代を前提）
  bytes 74-75  : 月（2桁 MM）
  bytes 76-77  : 日（2桁 DD）
  bytes 78-79  : 局コード（2文字）
  bytes 80-107 : 満潮4スロット（各7バイト: HHMM + 3桁cm）
  bytes 108-135: 干潮4スロット（同上）
  "9999999"    : データなし（スロット未使用）

出典: フォーマット検証 https://github.com/ngs/jma-tides-swift (MIT License)

Usage:
    # 日本国内ネットワークから実行すること（気象庁は海外IP制限あり）
    python scripts/fetch_jma_tides.py                    # 今年・来年の全対象局
    python scripts/fetch_jma_tides.py --year 2026        # 指定年のみ
    python scripts/fetch_jma_tides.py --stations TK QS   # 指定局のみ
    python scripts/fetch_jma_tides.py --force            # 既存ファイルも上書き
    python scripts/fetch_jma_tides.py --dry-run          # 保存なし（テスト）
    python scripts/fetch_jma_tides.py --list-stations    # 全局コード・名称を表示
"""

import argparse
import json
import math
import os
import pathlib
import smtplib
import ssl
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText

_REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))

JMA_BASE_URL     = "https://www.data.jma.go.jp/kaiyou/data/db/tide/suisan/txt"
JMA_BASE_URL_ALT = "https://www.data.jma.go.jp/gmd/kaiyou/data/db/tide/suisan/txt"
USER_AGENT       = "TsuricastBot/1.0 (personal-use)"
REQUEST_TIMEOUT  = 20
REQUEST_INTERVAL = 2.0
RETRY_COUNT      = 3
RETRY_WAIT       = 15

OUTPUT_DIR = _REPO_ROOT / "data" / "jma_tides"
JST = timezone(timedelta(hours=9))

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

# ─────────────────────────────────────────────────────────
# 気象庁推算潮位表 全局一覧
# (code, 局名, 読み, 緯度, 経度)
# 出典: https://github.com/ngs/jma-tides-swift Locations.swift
# ─────────────────────────────────────────────────────────
JMA_ALL_STATIONS: list[tuple[str, str, str, float, float]] = [
    ("WN", "稚内",          "わっかない",       45.242, 141.406),
    ("KE", "枝幸",          "えさし",           44.562, 142.346),
    ("A0", "紋別",          "もんべつ",         44.212, 143.216),
    ("AS", "網走",          "あばしり",         44.012, 144.166),
    ("A6", "羅臼",          "らうす",           44.012, 145.116),
    ("NM", "根室",          "ねむろ",           43.212, 145.346),
    ("HN", "花咲",          "はなさき",         43.172, 145.330),
    ("KP", "霧多布",        "きりたっぷ",       43.050, 145.060),
    ("KR", "釧路",          "くしろ",           42.592, 144.216),
    ("B1", "十勝",          "とかち",           42.182, 143.186),
    ("A9", "浦河",          "うらかわ",         42.102, 142.456),
    ("C8", "苫小牧東",      "とまこまいひがし", 42.362, 141.480),
    ("TM", "苫小牧西",      "とまこまいにし",   42.382, 141.360),
    ("SO", "白老",          "しらおい",         42.312, 141.180),
    ("A8", "室蘭",          "むろらん",         42.212, 140.560),
    ("A3", "森",            "もり",             42.072, 140.350),
    ("HK", "函館",          "はこだて",         41.472, 140.420),
    ("Q0", "吉岡",          "よしおか",         41.272, 140.130),
    ("A5", "松前",          "まつまえ",         41.252, 140.050),
    ("ES", "江差",          "えさし",           41.522, 140.070),
    ("ZP", "奥尻",          "おくしり",         42.052, 139.280),
    ("OR", "奥尻港",        "おくしりこう",     42.102, 139.300),
    ("SE", "瀬棚",          "せたな",           42.272, 139.500),
    ("B6", "寿都",          "すつつ",           42.482, 140.130),
    ("B5", "岩内",          "いわない",         42.592, 140.300),
    ("Z8", "忍路",          "おしょろ",         43.132, 140.510),
    ("B3", "小樽",          "おたる",           43.122, 140.990),
    ("IK", "石狩新港",      "いしかりしんみなと", 43.132, 141.170),
    ("B2", "留萌",          "るもい",           43.572, 141.370),
    ("F3", "沓形",          "くつがた",         45.112, 141.070),
    ("Q1", "竜飛",          "たっぴ",           41.152, 140.220),
    ("AO", "青森",          "あおもり",         40.502, 140.450),
    ("ZA", "浅虫",          "あさむし",         40.542, 140.510),
    ("Q2", "大湊",          "おおみなと",       41.152, 141.080),
    ("B4", "大間",          "おおま",           41.322, 140.530),
    ("SH", "下北",          "しもきた",         41.222, 141.130),
    ("XS", "むつ小川原",    "むつおがわら",     40.562, 141.220),
    ("HG", "八戸港",        "はちのへこう",     40.322, 141.320),
    ("XT", "久慈",          "くじ",             40.122, 141.470),
    ("MY", "宮古",          "みやこ",           39.392, 141.580),
    ("Q6", "釜石",          "かまいし",         39.162, 141.520),
    ("OF", "大船渡",        "おおふなと",       39.012, 141.440),
    ("AY", "鮎川",          "あゆかわ",         38.182, 141.290),
    ("E6", "石巻",          "いしのまき",       38.242, 141.150),
    ("SG", "塩釜",          "しおがま",         38.192, 141.010),
    ("SD", "仙台新港",      "せんだいしんみなと", 38.162, 140.990),
    ("ZM", "相馬",          "そうま",           37.502, 140.570),
    ("ON", "小名浜",        "おなはま",         36.562, 140.530),
    ("D1", "日立",          "ひたち",           36.302, 140.370),
    ("D3", "大洗",          "おおあらい",       36.182, 140.330),
    ("D2", "鹿島",          "かしま",           35.562, 140.410),
    ("CS", "銚子漁港",      "ちょうしぎょこう", 35.452, 140.510),
    ("ZF", "勝浦",          "かつうら",         35.082, 140.140),
    ("MR", "布良",          "めら",             34.552, 139.490),
    ("TT", "館山",          "たてやま",         34.592, 139.500),
    ("KZ", "木更津",        "きさらづ",         35.222, 139.540),
    ("QL", "千葉",          "ちば",             35.342, 140.020),
    ("CB", "千葉港",        "ちばみなと",       35.362, 140.050),
    ("TK", "東京",          "とうきょう",       35.392, 139.450),
    ("KW", "川崎",          "かわさき",         35.312, 139.440),
    ("YK", "京浜港",        "けいひんこう",     35.282, 139.370),
    ("QS", "横浜",          "よこはま",         35.272, 139.380),
    ("HM", "本牧",          "ほんもく",         35.262, 139.390),
    ("QN", "横須賀",        "よこすか",         35.172, 139.380),
    ("Z1", "油壺",          "あぶらつぼ",       35.102, 139.360),
    ("OK", "岡田",          "おかだ",           34.472, 139.220),
    ("QO", "神津島",        "こうづしま",       34.132, 139.070),
    ("MJ", "三宅島（坪田）","みやけじまつぼた", 34.032, 139.320),
    ("QP", "三宅島（阿古）","みやけじまあこ",   34.042, 139.280),
    ("D4", "八丈島（八重根）","はちじょうじまやえね", 33.062, 139.450),
    ("QQ", "八丈島（神湊）","はちじょうじまこうのみなと", 33.082, 139.470),
    ("CC", "父島",          "ちちじま",         27.062, 142.110),
    ("MC", "南鳥島",        "みなみとりしま",   24.172, 153.580),
    ("D8", "湘南港",        "しょうなんこう",   35.182, 139.280),
    ("OD", "小田原",        "おだわら",         35.142, 139.080),
    ("Z3", "伊東",          "いとう",           34.542, 139.070),
    ("D6", "下田",          "しもだ",           34.412, 138.570),
    ("QK", "南伊豆",        "みなみいず",       34.382, 138.520),
    ("G9", "石廊崎",        "いろうざき",       34.372, 138.500),
    ("Z4", "田子",          "たご",             34.482, 138.450),
    ("UC", "内浦",          "うちうら",         35.012, 138.520),
    ("SM", "清水港",        "しみずこう",       35.012, 138.300),
    ("Z5", "焼津",          "やいづ",           34.522, 138.190),
    ("OM", "御前崎",        "おまえざき",       34.372, 138.120),
    ("MI", "舞阪",          "まいさか",         34.412, 137.360),
    ("I4", "赤羽根",        "あかはね",         34.362, 137.100),
    ("G4", "三河",          "みかわ",           34.442, 137.180),
    ("G5", "形原",          "かたはら",         34.472, 137.100),
    ("G8", "衣浦",          "きぬうら",         34.532, 136.560),
    ("ZD", "鬼崎",          "おにざき",         34.542, 136.480),
    ("NG", "名古屋",        "なごや",           35.052, 136.520),
    ("G3", "四日市港",      "よっかいちこう",   34.582, 136.370),
    ("TB", "鳥羽",          "とば",             34.292, 136.480),
    ("OW", "尾鷲",          "おわせ",           34.052, 136.110),
    ("KN", "熊野",          "くまの",           33.562, 136.090),
    ("UR", "浦神",          "うらがみ",         33.342, 135.530),
    ("KS", "串本",          "くしもと",         33.292, 135.450),
    ("SR", "白浜",          "しらはま",         33.412, 135.220),
    ("GB", "御坊",          "ごぼう",           33.512, 135.090),
    ("H1", "下津",          "しもつ",           34.072, 135.070),
    ("Z9", "海南",          "かいなん",         34.092, 135.110),
    ("WY", "和歌山",        "わかやま",         34.132, 135.080),
    ("TN", "淡輪",          "たんのわ",         34.202, 135.100),
    ("KK", "関空島",        "かんくうとう",     34.262, 135.110),
    ("J2", "岸和田",        "きしわだ",         34.282, 135.210),
    ("IO", "泉大津",        "いずみおおつ",     34.312, 135.230),
    ("SI", "堺",            "さかい",           34.362, 135.270),
    ("OS", "大阪",          "おおさか",         34.392, 135.250),
    ("AM", "尼崎",          "あまがさき",       34.422, 135.230),
    ("J5", "西宮",          "にしのみや",       34.432, 135.190),
    ("KB", "神戸",          "こうべ",           34.412, 135.100),
    ("AK", "明石",          "あかし",           34.392, 134.580),
    ("ST", "洲本",          "すもと",           34.212, 134.530),
    ("EI", "江井",          "えい",             34.282, 134.490),
    ("K1", "姫路（飾磨）",  "ひめじしかま",     34.472, 134.390),
    ("SB", "三蟠",          "さんばん",         34.362, 133.580),
    ("UN", "宇野",          "うの",             34.292, 133.560),
    ("MM", "水島",          "みずしま",         34.322, 133.430),
    ("LG", "乙島",          "おつとう",         34.302, 133.400),
    ("IZ", "糸崎",          "いとざき",         34.242, 133.040),
    ("TH", "竹原",          "たけはら",         34.202, 132.540),
    ("Q9", "呉",            "こう",             34.142, 132.320),
    ("Q8", "広島",          "ひろしま",         34.212, 132.270),
    ("QA", "徳山",          "とくやま",         34.022, 131.470),
    ("J9", "三田尻",        "みたじり",         34.022, 131.340),
    ("WH", "宇部",          "うべ",             33.562, 131.140),
    ("CF", "長府",          "ちょうふ",         34.012, 130.990),
    ("MT", "松山",          "まつやま",         33.522, 132.420),
    ("M3", "波止浜",        "はしはま",         34.062, 132.550),
    ("L0", "今治",          "いまばり",         34.042, 132.990),
    ("NI", "新居浜",        "にいはま",         33.582, 133.150),
    ("TX", "多度津",        "たどつ",           34.172, 133.440),
    ("TA", "高松",          "たかまつ",         34.212, 134.020),
    ("KM", "小松島",        "こまつしま",       34.012, 134.340),
    ("KC", "高知",          "こうち",           33.302, 133.330),
    ("TS", "土佐清水",      "とさしみず",       32.472, 132.570),
    ("UW", "宇和島",        "うわじま",         33.142, 132.320),
    ("MO", "門司",          "もじ",             33.572, 130.560),
    ("QC", "大分",          "おおいた",         33.162, 131.400),
    ("MG", "宮崎",          "みやざき",         31.542, 131.260),
    ("KG", "鹿児島",        "かごしま",         31.362, 130.330),
    ("ZO", "沖縄",          "おきなわ",         26.112, 127.480),
    ("NH", "那覇",          "なは",             26.132, 127.390),
    ("O5", "八代",          "はちだい",         32.312, 130.330),
    ("NS", "長崎",          "ながさき",         32.442, 129.510),
    ("QD", "佐世保",        "させぼ",           33.092, 129.420),
    ("FK", "深浦",          "ふかうら",         40.392, 139.550),
    ("S1", "秋田",          "あきた",           39.452, 140.030),
    ("I5", "新潟東港",      "にいがたひがしこう", 37.592, 139.120),
    ("T3", "直江津",        "なおえつ",         37.112, 138.140),
    ("MZ", "舞鶴",          "まいづる",         35.292, 135.220),
    ("XM", "敦賀",          "つるが",           35.402, 136.030),
    ("T1", "金沢",          "かなざわ",         36.372, 136.350),
]

# ─────────────────────────────────────────────────────────
# dict 形式に変換（コードで高速引き当て）
# ─────────────────────────────────────────────────────────
def _ddm_to_dd(ddm: float) -> float:
    """DDM（度・十進法分）→ 十進法度 変換。
    気象庁 Locations.swift の座標形式: 35.392 = 35°39.2' → 35.6533°
    """
    deg = int(ddm)
    return deg + (ddm - deg) * 100 / 60


# 十進法度（DD）に変換した局辞書
JMA_STATIONS: dict[str, tuple[str, str, float, float]] = {
    code: (name, kana, _ddm_to_dd(lat), _ddm_to_dd(lon))
    for code, name, kana, lat, lon in JMA_ALL_STATIONS
}

# ─────────────────────────────────────────────────────────
# スポットで実際に使用する対象局（地理的に関連する局のみ）
# ─────────────────────────────────────────────────────────
TARGET_STATIONS = [
    # 東京湾・内房・外房
    "TK",  # 東京
    "KW",  # 川崎
    "QS",  # 横浜
    "HM",  # 本牧
    "QN",  # 横須賀
    "Z1",  # 油壺
    "CB",  # 千葉港
    "QL",  # 千葉
    "KZ",  # 木更津
    "TT",  # 館山
    "MR",  # 布良
    "ZF",  # 勝浦（千葉）
    "CS",  # 銚子漁港
    # 相模湾・伊豆
    "D8",  # 湘南港
    "OD",  # 小田原
    "Z3",  # 伊東
    "D6",  # 下田
    "QK",  # 南伊豆
    "G9",  # 石廊崎
    "Z4",  # 田子
    "UC",  # 内浦
    # 駿河湾・遠州灘
    "SM",  # 清水港
    "Z5",  # 焼津
    "OM",  # 御前崎
    "MI",  # 舞阪
    "I4",  # 赤羽根
    # 三河湾・伊勢湾
    "G4",  # 三河
    "G5",  # 形原
    "G8",  # 衣浦
    "ZD",  # 鬼崎
    "NG",  # 名古屋
    "G3",  # 四日市港
    # 志摩・熊野灘
    "TB",  # 鳥羽
    "OW",  # 尾鷲
    "KN",  # 熊野
    "UR",  # 浦神
    "KS",  # 串本
    # 紀伊水道・大阪湾
    "SR",  # 白浜（和歌山）
    "GB",  # 御坊
    "H1",  # 下津
    "Z9",  # 海南
    "WY",  # 和歌山
    "TN",  # 淡輪
    "J2",  # 岸和田
    "IO",  # 泉大津
    "SI",  # 堺
    "OS",  # 大阪
]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """2点間の大圏距離 (km) を返す。"""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearest_station(lat: float, lon: float) -> tuple[str, str, float]:
    """指定座標に最も近い JMA 局を返す (code, name, distance_km)。座標は十進法度。"""
    best_code = best_name = ""
    best_dist = float("inf")
    for code, (name, _kana, s_lat, s_lon) in JMA_STATIONS.items():
        d = _haversine_km(lat, lon, s_lat, s_lon)
        if d < best_dist:
            best_dist = d
            best_code = code
            best_name = name
    return best_code, best_name, best_dist


def _is_valid_time(s: str) -> bool:
    if len(s) != 4 or not s.isdigit():
        return False
    hh, mm = int(s[:2]), int(s[2:])
    return 0 <= hh <= 23 and 0 <= mm <= 59


def _parse_hourly(line: str) -> list[dict]:
    """bytes 0-71 から時別潮位 24点（整時ごと）をパース。"""
    hourly = []
    for i in range(24):
        h_str = line[i * 3:(i + 1) * 3]
        try:
            cm = int(h_str)
        except ValueError:
            continue
        hourly.append({"time": f"{i:02d}:00", "cm": float(cm)})
    return hourly


def _parse_tide_slots(line: str, start: int) -> list[dict]:
    """line[start:start+28] から4スロット（各7文字: HHMM + 3桁cm）をパース。"""
    entries = []
    for i in range(4):
        offset    = start + i * 7
        time_str  = line[offset:offset + 4]
        height_str = line[offset + 4:offset + 7]
        if time_str == "9999" or not _is_valid_time(time_str):
            continue
        try:
            cm = int(height_str)
        except ValueError:
            continue
        hh, mm = int(time_str[:2]), int(time_str[2:])
        entries.append({"time": f"{hh:02d}:{mm:02d}", "cm": float(cm)})
    return entries


def parse_jma_text(content: str, station_code: str, year: int) -> dict:
    """
    JMA 推算潮位表テキストをパースして内部 JSON 形式に変換する。
    フォーマット検証: https://github.com/ngs/jma-tides-swift (MIT License)
    """
    lines = content.splitlines()
    days: dict[str, dict] = {}

    for line in lines:
        if len(line) < 136:
            continue
        st = line[78:80]
        if st.strip() != station_code:
            continue
        try:
            yy = int(line[72:74])
            mo = int(line[74:76])
            dy = int(line[76:78])
        except ValueError:
            continue
        if not (1 <= mo <= 12 and 1 <= dy <= 31):
            continue
        full_year = 2000 + yy
        if full_year != year:
            continue

        date_str = f"{full_year:04d}-{mo:02d}-{dy:02d}"
        hourly = _parse_hourly(line)
        flood  = _parse_tide_slots(line, 80)
        ebb    = _parse_tide_slots(line, 108)
        days[date_str] = {"flood": flood, "ebb": ebb, "hourly": hourly}

    info = JMA_STATIONS.get(station_code)
    return {
        "_meta": {
            "station_code": station_code,
            "station_name": info[0] if info else station_code,
            "year": year,
            "source": "気象庁 推算潮位表",
            "license": "CC-BY 4.0互換（出典: 気象庁）",
            "fetched_at": datetime.now(JST).isoformat(),
        },
        "days": days,
    }


def download_station_year(station_code: str, year: int) -> str | None:
    """指定局・指定年の潮位表テキストをダウンロードして文字列で返す。失敗時は None。"""
    urls = [
        f"{JMA_BASE_URL}/{year}/{station_code}.txt",
        f"{JMA_BASE_URL_ALT}/{year}/{station_code}.txt",
    ]
    for url in urls:
        for attempt in range(1, RETRY_COUNT + 1):
            req = urllib.request.Request(url, headers={
                "User-Agent":      USER_AGENT,
                "Accept":          "text/plain,*/*",
                "Accept-Language": "ja,en;q=0.9",
                "Referer":         "https://www.data.jma.go.jp/kaiyou/db/tide/suisan/",
            })
            try:
                with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT, context=_SSL_CTX) as resp:
                    raw = resp.read()
                try:
                    return raw.decode("utf-8")
                except UnicodeDecodeError:
                    return raw.decode("shift_jis", errors="replace")
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    break  # 次の URL を試す
                if e.code == 403:
                    print(f"    [403] 日本国内ネットワークから実行してください: {url}")
                    return None
                if e.code == 429:
                    print(f"    [429] rate-limit, 30s 待機 (attempt {attempt})")
                    time.sleep(30)
                else:
                    print(f"    [HTTP {e.code}] {e} (attempt {attempt})")
                    if attempt < RETRY_COUNT:
                        time.sleep(RETRY_WAIT)
            except Exception as e:
                print(f"    [ERROR] {e} (attempt {attempt})")
                if attempt < RETRY_COUNT:
                    time.sleep(RETRY_WAIT)
    return None


def save_json(data: dict, station_code: str, year: int) -> pathlib.Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / f"{station_code}_{year}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _send_reminder_mail(subject: str, body: str) -> None:
    mail_from = os.environ.get("MAIL_FROM", "")
    mail_to   = os.environ.get("MAIL_TO", "")
    password  = os.environ.get("MAIL_PASSWORD", "")
    if not (mail_from and mail_to and password):
        print("[mail] MAIL_FROM / MAIL_TO / MAIL_PASSWORD 未設定のため送信スキップ")
        return
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"]    = mail_from
    msg["To"]      = mail_to
    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo(); smtp.starttls()
        smtp.login(mail_from, password)
        smtp.sendmail(mail_from, mail_to, msg.as_string())
    print(f"[mail] 送信完了: {subject}")


def check_next_year() -> None:
    """来年分の JMA データが存在するか確認し、なければメールで通知する。"""
    next_year = datetime.now(JST).year + 1
    missing = [c for c in TARGET_STATIONS
               if not (OUTPUT_DIR / f"{c}_{next_year}.json").exists()]
    if not missing:
        print(f"[OK] {next_year}年分データは全局揃っています。")
        return

    msg = (
        f"気象庁 推算潮位表 {next_year}年分データが未取得です。\n\n"
        f"不足局: {', '.join(missing[:10])}{'...' if len(missing) > 10 else ''} "
        f"（{len(missing)}/{len(TARGET_STATIONS)} 局）\n\n"
        f"日本国内ネットワークから以下を実行してください:\n"
        f"  python scripts/fetch_jma_tides.py --year {next_year}\n"
        f"  git add data/jma_tides/ && git commit -m '潮汐: 気象庁データ {next_year}年分追加' && git push\n"
    )
    print(msg)
    _send_reminder_mail(
        subject=f"[Tsuricast] 気象庁潮位データ更新のお知らせ（{next_year}年分）",
        body=msg,
    )
    sys.exit(1)  # Render ログに記録されるよう非ゼロ終了


def main() -> None:
    parser = argparse.ArgumentParser(description="気象庁 推算潮位表 ダウンロード")
    parser.add_argument("--year", type=int, nargs="+", default=None,
                        help="対象年（省略時: 当年・来年）")
    parser.add_argument("--stations", nargs="+", default=None,
                        help="対象局コード（省略時: TARGET_STATIONS）")
    parser.add_argument("--force", action="store_true",
                        help="既存ファイルも上書き")
    parser.add_argument("--dry-run", action="store_true",
                        help="ダウンロードのみ・ファイル保存なし")
    parser.add_argument("--list-stations", action="store_true",
                        help="対象局コード・名称・座標を表示して終了")
    parser.add_argument("--check-next-year", action="store_true",
                        help="来年分データの有無を確認し、不足時はメール通知して終了")
    args = parser.parse_args()

    if args.check_next_year:
        check_next_year()
        return

    if args.list_stations:
        stations = args.stations or TARGET_STATIONS
        print(f"{'コード':6s} {'局名':14s} {'緯度':8s} {'経度':9s}")
        print("-" * 45)
        for code in stations:
            info = JMA_STATIONS.get(code)
            if info:
                print(f"{code:6s} {info[0]:14s} {info[2]:8.3f} {info[3]:9.3f}")
            else:
                print(f"{code:6s} (不明)")
        return

    now = datetime.now(JST)
    years = args.year or [now.year, now.year + 1]
    stations = args.stations or TARGET_STATIONS

    ok = err = skip = 0
    for year in years:
        for code in stations:
            out_path = OUTPUT_DIR / f"{code}_{year}.json"
            if out_path.exists() and not args.force:
                print(f"  [SKIP] {code} {year}: 既存ファイルあり（--force で上書き）")
                skip += 1
                continue

            info = JMA_STATIONS.get(code)
            st_name = info[0] if info else code
            print(f"  [{code}] {st_name} {year} ... ", end="", flush=True)

            content = download_station_year(code, year)
            if content is None:
                print("FAIL")
                err += 1
                continue

            data = parse_jma_text(content, code, year)
            n_days = len(data["days"])
            print(f"OK ({n_days}日分)")

            if not args.dry_run:
                path = save_json(data, code, year)
                print(f"    -> {path.relative_to(_REPO_ROOT)}")

            time.sleep(REQUEST_INTERVAL)
            ok += 1

    print(f"\n完了: 成功{ok} / スキップ{skip} / エラー{err}")


if __name__ == "__main__":
    main()
