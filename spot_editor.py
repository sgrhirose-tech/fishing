"""
spot_editor.py — Pythonista用スポット編集ツール

unadjusted/*.json を Leaflet.js WebView で編集・保存する。
全フィールド対応。新規スポット作成も可（APIコールなし）。

使い方:
  Pythonista: スクリプトを実行 → fullscreen WebView が開く
  デスクトップ: python spot_editor.py → ブラウザで HTML を開く（参照確認用）
"""

import json
import math
import os
import re
import sys
import urllib.parse

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
AREAS_FILE = os.path.join(REPO_ROOT, "spots", "_marine_areas.json")
FISH_MASTER_FILE = os.path.join(REPO_ROOT, "data", "fish_master.json")
AVAILABLE_DIRS = {
    "unadjusted": os.path.join(REPO_ROOT, "unadjusted"),
    "spots_wip":  os.path.join(REPO_ROOT, "spots_wip"),
    "spots":      os.path.join(REPO_ROOT, "spots"),
}
DEFAULT_DIR_KEY = "spots_wip"

# slug バリデーション用定数（app/constants.py と同一の値を保つこと）
_VALID_AREA_SLUGS = {
    "sagamibay", "miura", "tokyobay", "uchibo", "sotobo", "kujukuri",
    "higashi-izu", "minami-izu", "nishi-izu",
    "suruga-bay", "enshu-nada", "mikawa-bay", "isewan", "shima-minami-ise", "kumano-nada",
    "osakawan", "harimanada", "awajishima", "kii-suido-wakayama", "kii-suido-tokushima",
}
_VALID_PREF_SLUGS = {
    "kanagawa", "tokyo", "chiba", "shizuoka", "aichi", "mie",
    "osaka", "hyogo", "wakayama", "tokushima",
}
_CITY_SLUG_RE = re.compile(r'^[a-z0-9\-]+$')


def _validate_area(area: dict):
    """area フィールドのバリデーション。エラー文字列を返す（問題なければ None）。"""
    a = area.get("area_slug", "")
    p = area.get("pref_slug", "")
    c = area.get("city_slug", "")
    if a and a not in _VALID_AREA_SLUGS:
        return f'area_slug "{a}" は無効です。有効値: {sorted(_VALID_AREA_SLUGS)}'
    if p and p not in _VALID_PREF_SLUGS:
        return f'pref_slug "{p}" は無効です。有効値: {sorted(_VALID_PREF_SLUGS)}'
    if c and not _CITY_SLUG_RE.match(c):
        return f'city_slug "{c}" は英小文字・数字・ハイフンのみ使用可能です'
    return None

SEABED_TYPE_OPTIONS = [
    ("sand",       "砂"),
    ("sand_mud",   "砂泥"),
    ("sand_rock",  "砂・岩礁混"),
    ("rock",       "岩礁"),
    ("mud",        "泥"),
    ("gravel",     "砂礫"),
    ("mixed",      "混合"),
]

CLASSIFICATION_TYPE_OPTIONS = [
    ("sand_beach",       "砂浜"),
    ("rocky_shore",      "磯・岩場"),
    ("breakwater",       "防波堤・堤防"),
    ("fishing_facility", "漁港・釣り施設"),
    ("unknown",          "不明"),
]

BEARING_OPTIONS = list(range(0, 360, 5))

# area_name → (area_slug, pref_slug, prefecture)
AREA_MAP = {
    "相模湾":     ("sagamibay",      "kanagawa", "神奈川県"),
    "三浦半島":   ("miura",          "kanagawa", "神奈川県"),
    "東京湾":     ("tokyobay",       "kanagawa", "神奈川県"),
    "内房":       ("uchibo",         "chiba",    "千葉県"),
    "外房":       ("sotobo",         "chiba",    "千葉県"),
    "九十九里":   ("kujukuri",       "chiba",    "千葉県"),
    "東伊豆":     ("higashi-izu",  "shizuoka", "静岡県"),
    "南伊豆":     ("minami-izu",   "shizuoka", "静岡県"),
    "西伊豆":     ("nishi-izu",    "shizuoka", "静岡県"),
    "駿河湾":     ("suruga-bay",   "shizuoka", "静岡県"),
    "遠州灘":     ("enshu-nada",   "shizuoka", "静岡県"),
    "三河湾":     ("mikawa-bay",   "aichi",    "愛知県"),
    "伊勢湾":         ("isewan",            "aichi",    "愛知県"),
    "志摩・南伊勢":   ("shima-minami-ise", "mie",      "三重県"),
    "熊野灘":         ("kumano-nada",       "mie",      "三重県"),
}


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

_FISH_LIST: list[list[str]] = []

def _load_fish_master() -> list[list[str]]:
    """fish_master.json から [[slug, name], ...] リストを読み込む。ファイルがなければ空リスト。"""
    global _FISH_LIST
    if not os.path.exists(FISH_MASTER_FILE):
        return []
    with open(FISH_MASTER_FILE, encoding="utf-8") as f:
        data = json.load(f)
    _FISH_LIST = [[v["slug"], name] for name, v in data.items() if "slug" in v]
    return _FISH_LIST


def _load_name_to_slug() -> dict:
    """fish_master.json から {日本語名: slug} の辞書を返す。"""
    if not os.path.exists(FISH_MASTER_FILE):
        return {}
    with open(FISH_MASTER_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return {name: v["slug"] for name, v in data.items() if "slug" in v}


# ---------------------------------------------------------------------------
# target_fish 抽出バッチ（notes テキストから魚種を自動タグ付け）
# ---------------------------------------------------------------------------

# 検索対象の表記（正規名 or エイリアス）→ 正規名 のマッピング。
# リスト順に検索するため、長い名前を先に記載すること（部分マッチ防止）。
FISH_NORMALIZE: dict = {
    "アオリイカ":   "アオリイカ",
    "ソウダガツオ": "ソウダガツオ",
    "ウミタナゴ":   "ウミタナゴ",
    "イシガキダイ": "イシガキダイ",
    "コウイカ":     "コウイカ",
    "イシダイ":     "イシダイ",
    "シマアジ":     "シマアジ",
    "シロギス":     "シロギス",
    "タチウオ":     "タチウオ",
    "マゴチ":       "マゴチ",
    "マダイ":       "マダイ",
    "メジナ":       "メジナ",
    "クロダイ":     "クロダイ",
    "カサゴ":       "カサゴ",
    "カレイ":       "カレイ",
    "カマス":       "カマス",
    "メバル":       "メバル",
    "ヒラメ":       "ヒラメ",
    "サヨリ":       "サヨリ",
    "スズキ":       "スズキ",
    "イワシ":       "イワシ",
    "サバ":         "サバ",
    "ハゼ":         "ハゼ",
    "タコ":         "タコ",
    "アジ":         "アジ",
    "ブリ":         "ブリ",
    # エイリアス
    "チヌ":         "クロダイ",
    "シーバス":     "スズキ",
    "キス":         "シロギス",
    "イナダ":       "ブリ",
    "ワラサ":       "ブリ",
    "ワカシ":       "ブリ",
    "ショゴ":       "カンパチ",
    "キビレ":       "クロダイ",
}


def extract_fish_from_notes(notes: str, name_to_slug: dict) -> list:
    """notes テキストから魚種を抽出し、スラッグのリストを返す（重複なし・出現順）。"""
    found = []
    seen = set()
    text = notes or ""
    for pattern, canonical in FISH_NORMALIZE.items():
        if pattern in text:
            slug = name_to_slug.get(canonical)
            if slug and slug not in seen:
                found.append(slug)
                seen.add(slug)
    return found


def run_extract_fish(dir_key: str = "spots", dry_run: bool = False) -> None:
    """
    spots/ または spots_wip/ 内の全スポット JSON を対象に notes から魚種を抽出し、
    target_fish フィールドを更新する。

    使い方（CLI）:
      python spot_editor.py --extract-fish
      python spot_editor.py --extract-fish --dir spots_wip
      python spot_editor.py --extract-fish --dry-run
    """
    spots_dir = AVAILABLE_DIRS.get(dir_key)
    if not spots_dir or not os.path.isdir(spots_dir):
        print(f"[エラー] ディレクトリが見つかりません: {dir_key}")
        return

    name_to_slug = _load_name_to_slug()
    json_files = sorted(
        p for p in os.listdir(spots_dir)
        if p.endswith(".json") and not p.startswith("_")
    )
    if not json_files:
        print(f"[WARN] {spots_dir} に JSON ファイルが見つかりません")
        return

    updated = skipped = 0
    for filename in json_files:
        path = os.path.join(spots_dir, filename)
        with open(path, encoding="utf-8") as f:
            spot = json.load(f)

        slug = spot.get("slug", filename[:-5])
        notes = spot.get("info", {}).get("notes", "")
        fish_list = extract_fish_from_notes(notes, name_to_slug)

        if spot.get("target_fish") == fish_list:
            skipped += 1
            continue

        if dry_run:
            print(f"  [DRY] {slug}: {fish_list}")
        else:
            spot["target_fish"] = fish_list
            with open(path, "w", encoding="utf-8") as f:
                json.dump(spot, f, ensure_ascii=False, indent=2)
                f.write("\n")
            print(f"  [OK]  {slug}: {fish_list}")
        updated += 1

    label = "更新予定" if dry_run else "更新"
    print(f"\n{label}: {updated} 件 / スキップ（変更なし）: {skipped} 件")


def _load_marine_areas():
    with open(AREAS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("areas", {})


def _assign_marine_area(lat, lon):
    """Return the nearest area_name by Euclidean distance to center_lat/lon."""
    areas = _load_marine_areas()
    best_name = None
    best_dist = float("inf")
    for name, info in areas.items():
        dlat = lat - info["center_lat"]
        dlon = lon - info["center_lon"]
        dist = math.sqrt(dlat * dlat + dlon * dlon)
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def _dir_key_for(path):
    """絶対パスから AVAILABLE_DIRS のキーを逆引き。不明なら DEFAULT_DIR_KEY。"""
    abs_path = os.path.abspath(path)
    for k, v in AVAILABLE_DIRS.items():
        if abs_path == os.path.abspath(v):
            return k
    return DEFAULT_DIR_KEY


def load_spots(dir_path=None):
    d = dir_path or AVAILABLE_DIRS[DEFAULT_DIR_KEY]
    dir_key = _dir_key_for(d)
    spots = []
    for fname in sorted(os.listdir(d)):
        if not fname.endswith(".json") or fname.startswith("_"):
            continue
        path = os.path.join(d, fname)
        with open(path, encoding="utf-8") as f:
            spot = json.load(f)
        spot["_filename"] = fname
        spot["_dir_key"]  = dir_key
        spots.append(spot)
    return spots


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

def _make_seabed_options_html(selected="sand"):
    parts = []
    for val, label in SEABED_TYPE_OPTIONS:
        sel = ' selected' if val == selected else ''
        parts.append(f'<option value="{val}"{sel}>{label}</option>')
    return "\n".join(parts)


def _make_bearing_options_html():
    parts = []
    for deg in BEARING_OPTIONS:
        parts.append(f'<option value="{deg}">{deg}°</option>')
    return "\n".join(parts)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>スポットエディタ</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, sans-serif; font-size: 14px; background: #f0f0f0; }

/* ---- layout ---- */
#app { display: flex; height: 100vh; flex-direction: column; }
#toolbar { background: #2c3e50; color: white; padding: 8px 12px; display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
#toolbar h1 { font-size: 15px; flex: 1; }
#main { display: flex; flex: 1; overflow: hidden; }
#sidebar { width: 220px; background: white; overflow-y: auto; border-right: 1px solid #ddd; flex-shrink: 0; }
#map { flex: 1; }
#panel { width: 320px; background: white; overflow-y: auto; border-left: 1px solid #ddd; flex-shrink: 0; display: flex; flex-direction: column; }

/* ---- sidebar ---- */
#area-filter { width: 100%; padding: 8px; border: none; border-bottom: 1px solid #ddd; font-size: 13px; }
#name-filter  { width: 100%; padding: 8px; border: none; border-bottom: 1px solid #ddd; font-size: 13px; box-sizing: border-box; }
.spot-item { padding: 8px 10px; cursor: pointer; border-bottom: 1px solid #f0f0f0; font-size: 13px; }
.spot-item:hover { background: #e8f4fd; }
.spot-item.active { background: #3498db; color: white; }
.spot-item small { display: block; color: #888; font-size: 11px; }
.spot-item.active small { color: #cce; }

/* ---- panel ---- */
#panel-header { background: #2c3e50; color: white; padding: 8px 12px; font-size: 14px; font-weight: bold; flex-shrink: 0; display: flex; justify-content: space-between; align-items: center; }
#panel-header-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
#panel-header-links { display: none; gap: 4px; flex-shrink: 0; margin-left: 8px; }
#panel-header-links a { background: rgba(255,255,255,0.15); color: white; text-decoration: none; padding: 2px 7px; border-radius: 3px; font-size: 11px; font-weight: normal; white-space: nowrap; }
#panel-header-links a:hover { background: rgba(255,255,255,0.3); }
#save-bar { background: #e67e22; color: white; padding: 8px 12px; display: none; justify-content: space-between; align-items: center; font-size: 13px; flex-shrink: 0; }
#save-bar button { background: white; color: #e67e22; border: none; padding: 4px 12px; border-radius: 4px; cursor: pointer; font-weight: bold; }
#action-bar { background: #f8f9fa; border-bottom: 1px solid #ddd; padding: 6px 10px; display: none; gap: 6px; align-items: center; flex-shrink: 0; }
#action-bar button { border: none; padding: 5px 10px; border-radius: 4px; cursor: pointer; font-size: 12px; font-weight: bold; }
#btn-refetch-access { background: #3498db; color: white; }
#btn-refetch-physical { background: #27ae60; color: white; }
#action-bar button:disabled { opacity: 0.5; cursor: not-allowed; }
#delete-bar { display: none; padding: 10px; border-top: 2px solid #e74c3c; flex-shrink: 0; }
#btn-delete-spot { background: white; color: #e74c3c; border: 1px solid #e74c3c; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 12px; width: 100%; }
#btn-delete-spot:hover { background: #e74c3c; color: white; }
#info-table { flex: 1; padding: 10px; overflow-y: auto; }
.field-row { margin-bottom: 10px; }
.field-row label { display: block; font-size: 11px; color: #888; margin-bottom: 2px; text-transform: uppercase; }
.field-row input[type=text],
.field-row input[type=number],
.field-row textarea,
.field-row select { width: 100%; padding: 5px 7px; border: 1px solid #ddd; border-radius: 4px; font-size: 13px; font-family: inherit; }
.field-row textarea { resize: vertical; min-height: 60px; }
.field-row input[readonly] { background: #f7f7f7; color: #666; }
.lead-text-preview { background: #f0f4f8; border: 1px solid #d0dce8; border-radius: 4px; padding: 7px 9px; font-size: 13px; line-height: 1.6; color: #555; white-space: pre-wrap; }
.section-title { font-size: 11px; font-weight: bold; color: #2c3e50; background: #f0f4f8; padding: 4px 6px; margin: 12px -10px 8px; }
.bearing-row { display: flex; gap: 6px; align-items: center; }
.bearing-row select { flex: 1; }
.bearing-row button { padding: 5px 8px; border: 1px solid #ddd; border-radius: 4px; background: #f7f7f7; cursor: pointer; font-size: 13px; }
#bearing-arrow { font-size: 22px; line-height: 1; min-width: 24px; text-align: center; }

/* ---- new spot modal ---- */
#modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.5); z-index: 9999; align-items: center; justify-content: center; }
#modal-overlay.show { display: flex; }
#modal { background: white; border-radius: 8px; padding: 20px; width: 280px; }
#modal h2 { font-size: 15px; margin-bottom: 14px; }
#modal .field-row { margin-bottom: 10px; }
#modal .modal-btns { display: flex; gap: 8px; margin-top: 16px; }
#modal .modal-btns button { flex: 1; padding: 8px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }
#btn-modal-ok { background: #3498db; color: white; }
#btn-modal-cancel { background: #eee; }
</style>
</head>
<body>
<div id="app">
  <div id="toolbar">
    <h1>🎣 スポットエディタ</h1>
    <select id="dir-select" onchange="changeDir(this.value)" style="padding:4px 8px;border:1px solid #555;border-radius:4px;background:#2c4a6e;color:white;font-size:13px;cursor:pointer;">
      __DIR_OPTIONS_HTML__
    </select>
    <button id="btn-new" style="background:#27ae60;color:white;border:none;padding:5px 12px;border-radius:4px;cursor:pointer;font-size:13px;">＋新規</button>
  </div>
  <div id="js-error" style="display:none;background:#c0392b;color:white;padding:6px 12px;font-size:12px;"></div>
  <div id="main">
    <div id="sidebar">
      <select id="area-filter"><option value="">全エリア</option></select>
      <input type="text" id="name-filter" placeholder="スポット名で絞り込み">
      <div id="spot-list"></div>
    </div>
    <div id="map"></div>
    <div id="panel">
      <div id="panel-header">
        <span id="panel-header-name">スポットを選択してください</span>
        <div id="panel-header-links">
          <a id="gmap-coord-link" href="#" target="_blank">📍 地図</a>
          <a id="gmap-search-link" href="#" target="_blank">🔍 検索</a>
        </div>
      </div>
      <div id="save-bar">
        <span>未保存の変更があります</span>
        <button id="btn-save" data-action="save">保存</button>
      </div>
      <div id="action-bar">
        <button id="btn-refetch-access" data-action="refetch-access">アクセス再取得</button>
        <button id="btn-refetch-physical" data-action="refetch-physical">物理データ再取得</button>
      </div>
      <div id="info-table"></div>
      <datalist id="photo-datalist"></datalist>
      <div id="delete-bar">
        <button id="btn-delete-spot" data-action="delete-spot">🗑 このスポットを削除</button>
      </div>
    </div>
  </div>
</div>

<!-- New spot modal -->
<div id="modal-overlay">
  <div id="modal">
    <h2>新規スポット</h2>
    <div class="field-row"><label>スポット名</label><input type="text" id="new-name" placeholder="例: 辻堂海岸"></div>
    <div class="field-row"><label>スラッグ（英字・アンダースコアのみ）</label><input type="text" id="new-slug" placeholder="例: tsujido"></div>
    <div class="field-row"><label>緯度</label><input type="number" id="new-lat" step="0.0001" placeholder="35.3184"></div>
    <div class="field-row"><label>経度</label><input type="number" id="new-lon" step="0.0001" placeholder="139.4441"></div>
    <div class="modal-btns">
      <button id="btn-modal-cancel">キャンセル</button>
      <button id="btn-modal-ok">作成</button>
    </div>
  </div>
</div>

<script>
// ---- data injected by Python ----
var SAVE_MODE = '__SAVE_MODE__';
var CURRENT_DIR_KEY = '__DIR_KEY__';
var SPOTS = __SPOTS_JSON__;
var AREA_SLUG_MAP = {
  "相模湾":   ["sagamibay",  "kanagawa", "神奈川県"],
  "三浦半島": ["miura",      "kanagawa", "神奈川県"],
  "東京湾":   ["tokyobay",   "kanagawa", "神奈川県"],
  "内房":     ["uchibo",     "chiba",    "千葉県"],
  "外房":     ["sotobo",     "chiba",    "千葉県"],
  "九十九里": ["kujukuri",   "chiba",    "千葉県"],
  "東伊豆":   ["higashi-izu", "shizuoka", "静岡県"],
  "南伊豆":   ["minami-izu",  "shizuoka", "静岡県"],
  "西伊豆":   ["nishi-izu",   "shizuoka", "静岡県"],
  "駿河湾":   ["suruga-bay",  "shizuoka", "静岡県"],
  "遠州灘":   ["enshu-nada",  "shizuoka", "静岡県"],
  "三河湾":   ["mikawa-bay",  "aichi",    "愛知県"],
  "伊勢湾":         ["isewan",            "aichi",    "愛知県"],
  "志摩・南伊勢":   ["shima-minami-ise",     "mie",       "三重県"],
  "熊野灘":         ["kumano-nada",           "mie",       "三重県"],
  "大阪湾":         ["osakawan",              "osaka",     "大阪府"],
  "播磨灘":         ["harimanada",            "hyogo",     "兵庫県"],
  "淡路島":         ["awajishima",            "hyogo",     "兵庫県"],
  "紀伊水道（和歌山）": ["kii-suido-wakayama", "wakayama",  "和歌山県"],
  "紀伊水道（徳島）":   ["kii-suido-tokushima","tokushima", "徳島県"]
};
var SEABED_OPTIONS = __SEABED_OPTIONS_JSON__;
var BEARING_OPTIONS = __BEARING_OPTIONS_JSON__;
var CLASSIFICATION_OPTIONS = __CLASSIFICATION_OPTIONS_JSON__;
var FISH_LIST = __FISH_NAMES_JSON__; // [[slug, name], ...]
var SPOT_PHOTOS = __SPOT_PHOTOS_JSON__; // ["akasuka-gyoko.png", ...]

// ---- error display (debug) ----
window.onerror = function(msg, src, line) {
  var el = document.getElementById('js-error');
  if (el) { el.style.display = 'block'; el.textContent = 'JS Error: ' + msg + ' (line ' + line + ')'; }
  return false;
};

// ---- directory switch ----
function changeDir(key) {
  if (key === CURRENT_DIR_KEY) return;
  if (SAVE_MODE === 'http') {
    window.location.href = '/?dir=' + encodeURIComponent(key);
  } else {
    window.location.href = 'pythonista://changedir?dir=' + encodeURIComponent(key);
  }
}

// ---- state ----
var currentIdx = -1;
var dirty = false;
var map = null, marker = null, bearingLayer = null;
var mapReady = false;

// ---- init map (deferred, optional) ----
window.addEventListener('load', function() {
  try {
    map = L.map('map').setView([35.25, 139.5], 9);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© OpenStreetMap contributors', maxZoom: 18
    }).addTo(map);
    bearingLayer = L.layerGroup().addTo(map);
    marker = L.marker([35.25, 139.5], {draggable: true}).addTo(map);
    marker.on('dragend', function(e) {
      var ll = e.target.getLatLng();
      var tbl = document.getElementById('info-table');
      if (tbl) {
        var latI = tbl.querySelector('[data-field="latitude"]');
        var lonI = tbl.querySelector('[data-field="longitude"]');
        if (latI) latI.value = ll.lat.toFixed(7);
        if (lonI) lonI.value = ll.lng.toFixed(7);
      }
      markDirty();
    });
    mapReady = true;
  } catch(e) {
    document.getElementById('map').innerHTML =
      '<p style="padding:20px;color:#999;font-size:12px;">地図を読み込めませんでした<br>' + e.message + '</p>';
  }
});

// ---- area-filter を AREA_SLUG_MAP から動的生成 ----
(function() {
  var sel = document.getElementById('area-filter');
  Object.keys(AREA_SLUG_MAP).forEach(function(name) {
    var opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  });
})();

// ---- sidebar list ----
function buildList() {
  var areaFilter = document.getElementById('area-filter').value;
  var nameFilter = document.getElementById('name-filter').value.trim();
  var list = document.getElementById('spot-list');
  list.innerHTML = '';
  SPOTS.forEach(function(s, i) {
    var areaName = (s.area && s.area.area_name) || '';
    if (areaFilter && areaName !== areaFilter) return;
    if (nameFilter && (s.name || s.slug || '').indexOf(nameFilter) === -1) return;
    var div = document.createElement('div');
    div.className = 'spot-item' + (i === currentIdx ? ' active' : '');
    div.dataset.idx = i;
    div.dataset.action = 'select';
    div.innerHTML = '<strong>' + (s.name || s.slug || '(無名)') + '</strong><small>' + areaName + '</small>';
    list.appendChild(div);
  });
}

document.getElementById('area-filter').addEventListener('change', function() {
  buildList();
});
document.getElementById('name-filter').addEventListener('input', function() {
  buildList();
});

// ---- bearing arrow ----
function bearingToArrow(deg) {
  var arrows = ['↑','↗','→','↘','↓','↙','←','↖'];
  return arrows[Math.round(deg / 45) % 8];
}

function updateBearingArrow(deg) {
  var el = document.getElementById('bearing-arrow');
  if (el) el.textContent = bearingToArrow(parseFloat(deg) || 0);
  if (mapReady) updateMapArrow(deg);
}

function updateMapArrow(deg) {
  if (!mapReady) return;
  bearingLayer.clearLayers();
  var pos = marker.getLatLng();
  var rad = (parseFloat(deg) || 0) * Math.PI / 180;
  var dist = 0.08;
  var endLat = pos.lat + dist * Math.cos(rad);
  var endLon = pos.lng + dist * Math.sin(rad);
  L.polyline([[pos.lat, pos.lng], [endLat, endLon]], {color: '#e74c3c', weight: 3}).addTo(bearingLayer);
}

function adjustBearing(delta) {
  var sel = document.getElementById('bearing-select');
  if (!sel) return;
  var cur = parseFloat(sel.value) || 0;
  var newVal = ((cur + delta) % 360 + 360) % 360;
  // snap to nearest 5°
  newVal = Math.round(newVal / 5) * 5 % 360;
  sel.value = newVal;
  updateBearingArrow(newVal);
  markDirty();
}

// ---- show spot panel ----
function showSpot(idx) {
  currentIdx = idx;
  dirty = false;
  document.getElementById('save-bar').style.display = 'none';
  buildList();

  var s = SPOTS[idx];
  var loc = s.location || {};
  var area = s.area || {};
  var phys = s.physical_features || {};
  var der = s.derived_features || {};
  var info = s.info || {};

  var lat = loc.latitude || 35.25;
  var lon = loc.longitude || 139.5;
  if (mapReady) {
    marker.setLatLng([lat, lon]);
    map.setView([lat, lon], 13);
    updateMapArrow(phys.sea_bearing_deg || 0);
  }

  document.getElementById('panel-header-name').textContent = s.name || s.slug || '(無名)';
  var gmapCoord = document.getElementById('gmap-coord-link');
  var gmapSearch = document.getElementById('gmap-search-link');
  gmapCoord.href = 'https://www.google.com/maps?q=' + lat + ',' + lon + '&z=15';
  gmapSearch.href = 'https://www.google.com/maps/search/' + encodeURIComponent((s.name || s.slug) + ' 釣り場');
  document.getElementById('panel-header-links').style.display = 'flex';
  document.getElementById('action-bar').style.display = 'flex';
  document.getElementById('delete-bar').style.display = 'block';

  // seabed select options
  var seabedOpts = SEABED_OPTIONS.map(function(o) {
    var sel = o[0] === (phys.seabed_type || 'sand') ? ' selected' : '';
    return '<option value="' + o[0] + '"' + sel + '>' + o[1] + '</option>';
  }).join('');

  // bearing select options
  var bearingVal = phys.sea_bearing_deg || 0;
  var snappedBearing = Math.round(bearingVal / 5) * 5 % 360;
  var bearingOpts = BEARING_OPTIONS.map(function(d) {
    var sel = d === snappedBearing ? ' selected' : '';
    return '<option value="' + d + '"' + sel + '>' + d + '°</option>';
  }).join('');

  var surferOpts = [
    '<option value="true"' + (phys.surfer_spot === true ? ' selected' : '') + '>あり</option>',
    '<option value="false"' + (phys.surfer_spot !== true ? ' selected' : '') + '>なし</option>'
  ].join('');

  // classification select options
  var currentType = (s.classification && s.classification.primary_type) ? s.classification.primary_type : 'unknown';
  var classifOpts = CLASSIFICATION_OPTIONS.map(function(o) {
    var sel = o[0] === currentType ? ' selected' : '';
    return '<option value="' + o[0] + '"' + sel + '>' + o[1] + '</option>';
  }).join('');

  // area_name options
  var areaNames = Object.keys(AREA_SLUG_MAP);
  var areaNameOpts = areaNames.map(function(n) {
    var sel = n === (area.area_name || '') ? ' selected' : '';
    return '<option value="' + n + '"' + sel + '>' + n + '</option>';
  }).join('');

  document.getElementById('info-table').innerHTML =
    '<div class="section-title">基本情報</div>' +
    row('name',  '名前',   'text',   s.name  || '') +
    row('slug',  'スラッグ', 'text', s.slug  || '', true) +

    '<div class="section-title">エリア</div>' +
    '<div class="field-row"><label>エリア名</label>' +
      '<select data-field="area_name" id="sel-area-name">' + areaNameOpts + '</select>' +
    '</div>' +
    row('area_slug',   'エリアスラッグ', 'text', area.area_slug   || '') +
    row('prefecture',  '都道府県',       'text', area.prefecture  || '') +
    row('pref_slug',   '都道府県スラッグ','text', area.pref_slug   || '') +
    row('city',        '市区町村',       'text', area.city        || '') +
    row('city_slug',   '市区町村スラッグ','text', area.city_slug   || '') +

    '<div class="section-title">位置</div>' +
    row('latitude',  '緯度',  'number', lat,    false, '0.0000001') +
    row('longitude', '経度',  'number', lon,    false, '0.0000001') +

    '<div class="section-title">海・地形</div>' +
    '<div class="field-row"><label>海方位</label>' +
      '<div class="bearing-row">' +
        '<button data-action="bearing-minus">−5°</button>' +
        '<select id="bearing-select" data-field="sea_bearing_deg">' + bearingOpts + '</select>' +
        '<button data-action="bearing-plus">+5°</button>' +
        '<span id="bearing-arrow">' + bearingToArrow(snappedBearing) + '</span>' +
      '</div>' +
    '</div>' +
    '<div class="field-row"><label>底質タイプ</label>' +
      '<select data-field="seabed_type">' + seabedOpts + '</select>' +
    '</div>' +
    '<div class="field-row"><label>サーファー</label>' +
      '<select data-field="surfer_spot">' + surferOpts + '</select>' +
    '</div>' +

    '<div class="section-title">施設区分</div>' +
    '<div class="field-row"><label>施設種別</label>' +
      '<select data-field="primary_type">' + classifOpts + '</select>' +
    '</div>' +

    '<div class="section-title">水深</div>' +
    row('depth_near_m', '手前(m)',  'number', phys.depth_near_m != null ? phys.depth_near_m : '') +
    row('depth_far_m',  '沖合(m)',  'number', phys.depth_far_m  != null ? phys.depth_far_m  : '') +

    '<div class="section-title">スコア・地形</div>' +
    row('bottom_kisugo_score', 'キスゴスコア(0-100)', 'number', der.bottom_kisugo_score != null ? der.bottom_kisugo_score : '') +
    row('terrain_summary',     '地形サマリ',           'text',   der.terrain_summary     || '') +

    '<div class="section-title">アクセス・情報</div>' +
    (info.lead_text ? '<div class="field-row"><label>AI生成リード文</label><div class="lead-text-preview">' + escHtml(info.lead_text) + '</div></div>' : '') +
    rowArea('description', '紹介文',     info.description || '') +
    rowArea('notes',     '備考',       info.notes     || '') +
    row('access',        'アクセス',   'text',  info.access    || '') +
    '<div class="field-row"><label>写真URL</label><input type="text" data-field="photo_url" value="' + escHtml(info.photo_url || '') + '" list="photo-datalist"></div>' +

    '<div class="section-title">対象魚種</div>' +
    (function() {
      var currentFish = s.target_fish || [];
      var checks = FISH_LIST.map(function(pair) {
        var slug = pair[0], name = pair[1];
        var checked = currentFish.indexOf(slug) >= 0 ? ' checked' : '';
        return '<label style="display:inline-flex;align-items:center;gap:2px;margin:2px 4px;">' +
          '<input type="checkbox" name="target_fish" value="' + escHtml(slug) + '"' + checked + '>' +
          escHtml(name) + '</label>';
      }).join('');
      return '<div class="field-row"><label>魚種</label>' +
        '<div style="display:flex;flex-wrap:wrap;border:1px solid #ccc;padding:6px;border-radius:4px;">' +
        checks + '</div></div>';
    })() +
    '';

  // event listeners for live bearing update
  var bsel = document.getElementById('bearing-select');
  if (bsel) {
    bsel.addEventListener('change', function() {
      updateBearingArrow(this.value);
      markDirty();
    });
  }
  document.getElementById('sel-area-name').addEventListener('change', onAreaNameChange);
}

function row(field, label, type, val, readonly, step) {
  var ro = readonly ? ' readonly' : '';
  var stepAttr = step ? ' step="' + step + '"' : (type === 'number' ? ' step="any"' : '');
  return '<div class="field-row"><label>' + label + '</label>' +
    '<input type="' + type + '" data-field="' + field + '" value="' + escHtml(String(val)) + '"' + ro + stepAttr + '>' +
    '</div>';
}

function rowArea(field, label, val) {
  return '<div class="field-row"><label>' + label + '</label>' +
    '<textarea data-field="' + field + '">' + escHtml(String(val)) + '</textarea>' +
    '</div>';
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function onAreaNameChange() {
  var areaName = document.getElementById('sel-area-name').value;
  var info = AREA_SLUG_MAP[areaName];
  if (!info) return;
  setField('area_slug',  info[0]);
  setField('pref_slug',  info[1]);
  setField('prefecture', info[2]);
  markDirty();
}

function setField(field, val) {
  var el = document.querySelector('[data-field="' + field + '"]');
  if (el) el.value = val;
}

function markDirty() {
  if (!dirty) {
    dirty = true;
    document.getElementById('save-bar').style.display = 'flex';
  }
}

// ---- collect & save ----
function saveChanges() {
  if (currentIdx < 0) return;
  var s = SPOTS[currentIdx];
  function fv(field) {
    var el = document.querySelector('[data-field="' + field + '"]');
    return el ? el.value : null;
  }
  function fvNum(field) {
    var v = fv(field);
    return (v !== null && v !== '') ? parseFloat(v) : null;
  }
  function fvInt(field) {
    var v = fv(field);
    return (v !== null && v !== '') ? parseInt(v, 10) : null;
  }

  var payload = {
    _filename: s._filename,
    _dir_key:  s._dir_key || CURRENT_DIR_KEY,
    name:      fv('name'),
    slug:      s.slug,
    location: {
      latitude:  fvNum('latitude'),
      longitude: fvNum('longitude')
    },
    area: {
      area_name:   fv('area_name'),
      area_slug:   fv('area_slug'),
      prefecture:  fv('prefecture'),
      pref_slug:   fv('pref_slug'),
      city:        fv('city'),
      city_slug:   fv('city_slug')
    },
    physical_features: {
      sea_bearing_deg: fvNum('sea_bearing_deg'),
      seabed_type:     fv('seabed_type'),
      surfer_spot:     fv('surfer_spot') === 'true',
      depth_near_m:    fvNum('depth_near_m'),
      depth_far_m:     fvNum('depth_far_m')
    },
    derived_features: {
      bottom_kisugo_score: fvInt('bottom_kisugo_score'),
      terrain_summary:     fv('terrain_summary')
    },
    info: {
      description: fv('description'),
      notes:     fv('notes'),
      access:    fv('access'),
      photo_url: fv('photo_url')
    },
    primary_type: fv('primary_type'),
    target_fish: Array.from(
      document.querySelectorAll('input[name="target_fish"]:checked')
    ).map(function(cb) { return cb.value; })
  };

  // update in-memory SPOTS
  s.name = payload.name;
  s.location = payload.location;
  s.area = payload.area;
  s.physical_features = payload.physical_features;
  s.derived_features = payload.derived_features;
  s.info = payload.info;

  dirty = false;
  document.getElementById('save-bar').style.display = 'none';
  document.getElementById('panel-header-name').textContent = payload.name || s.slug || '(無名)';
  buildList();

  if (SAVE_MODE === 'http') {
    fetch('/save', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})
      .then(function(r){ return r.json(); })
      .then(function(res){ if (!res.ok) alert('保存エラー: ' + (res.error || '')); })
      .catch(function(e){ alert('保存失敗: ' + e); });
  } else {
    var encoded = encodeURIComponent(JSON.stringify(payload));
    window.location.href = 'pythonista://save?data=' + encoded;
  }
}

// ---- refetch / delete ----
function refetchAccess() {
  if (currentIdx < 0) return;
  var s = SPOTS[currentIdx];
  var btn = document.getElementById('btn-refetch-access');
  btn.disabled = true;
  btn.textContent = '取得中…';
  fetch('/refetch_access', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({slug: s.slug, dir_key: s._dir_key || CURRENT_DIR_KEY})})
    .then(function(r){ return r.json(); })
    .then(function(res){
      btn.disabled = false;
      btn.textContent = 'アクセス再取得';
      if (!res.ok) { alert('エラー: ' + (res.error || '')); return; }
      if (res.access !== undefined) {
        var el = document.querySelector('[data-field="access"]');
        if (el) { el.value = res.access; markDirty(); }
      }
      alert('アクセス情報を更新しました');
    })
    .catch(function(e){ btn.disabled = false; btn.textContent = 'アクセス再取得'; alert('失敗: ' + e); });
}

function refetchPhysical() {
  if (currentIdx < 0) return;
  var s = SPOTS[currentIdx];
  var btn = document.getElementById('btn-refetch-physical');
  btn.disabled = true;
  btn.textContent = '取得中…';
  fetch('/refetch_physical', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({slug: s.slug, dir_key: s._dir_key || CURRENT_DIR_KEY})})
    .then(function(r){ return r.json(); })
    .then(function(res){
      btn.disabled = false;
      btn.textContent = '物理データ再取得';
      if (!res.ok) { alert('エラー: ' + (res.error || '')); return; }
      alert('物理データを更新しました。リロードして確認してください。');
    })
    .catch(function(e){ btn.disabled = false; btn.textContent = '物理データ再取得'; alert('失敗: ' + e); });
}

function deleteSpot() {
  if (currentIdx < 0) return;
  var s = SPOTS[currentIdx];
  if (!confirm('「' + (s.name || s.slug) + '」を削除しますか？\nこの操作は取り消せません。')) return;
  fetch('/delete', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({filename: s._filename, dir_key: s._dir_key || CURRENT_DIR_KEY})})
    .then(function(r){ return r.json(); })
    .then(function(res){
      if (!res.ok) { alert('削除エラー: ' + (res.error || '')); return; }
      SPOTS.splice(currentIdx, 1);
      currentIdx = -1;
      dirty = false;
      document.getElementById('panel-header-name').textContent = 'スポットを選択してください';
      document.getElementById('panel-header-links').style.display = 'none';
      document.getElementById('action-bar').style.display = 'none';
      document.getElementById('delete-bar').style.display = 'none';
      document.getElementById('save-bar').style.display = 'none';
      document.getElementById('info-table').innerHTML = '';
      buildList();
    })
    .catch(function(e){ alert('削除失敗: ' + e); });
}

// ---- new spot modal ----
function openNewSpotModal() {
  document.getElementById('new-name').value = '';
  document.getElementById('new-slug').value = '';
  document.getElementById('new-lat').value  = '';
  document.getElementById('new-lon').value  = '';
  document.getElementById('modal-overlay').classList.add('show');
  document.getElementById('new-name').focus();
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('show');
}

function submitNewSpot() {
  var name = document.getElementById('new-name').value.trim();
  var slug = document.getElementById('new-slug').value.trim().replace(/[^a-z0-9_]/g, '');
  var lat  = parseFloat(document.getElementById('new-lat').value);
  var lon  = parseFloat(document.getElementById('new-lon').value);

  if (!name || !slug) { alert('名前とスラッグを入力してください'); return; }
  if (isNaN(lat) || isNaN(lon)) { alert('緯度・経度を入力してください'); return; }
  if (SPOTS.find(function(s){ return s.slug === slug; })) { alert('そのスラッグは既に存在します: ' + slug); return; }

  closeModal();
  var payload = { name: name, slug: slug, lat: lat, lon: lon, dir_key: CURRENT_DIR_KEY };
  if (SAVE_MODE === 'http') {
    fetch('/newspot', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)})
      .then(function(r){ return r.json(); })
      .then(function(res){
        if (res.ok) onNewSpotComplete(JSON.stringify(res.spot));
        else alert('作成エラー: ' + (res.error || ''));
      })
      .catch(function(e){ alert('作成失敗: ' + e); });
  } else {
    var encoded = encodeURIComponent(JSON.stringify(payload));
    window.location.href = 'pythonista://newspot?data=' + encoded;
  }
}

function onNewSpotComplete(spotJson) {
  var spot = JSON.parse(spotJson);
  SPOTS.push(spot);
  var newIdx = SPOTS.length - 1;
  showSpot(newIdx);
  buildList();
}

// ---- event delegation ----
document.addEventListener('click', function(e) {
  var t = e.target;
  var action = t.dataset && t.dataset.action;
  if (!action) { t = t.parentElement; action = t && t.dataset && t.dataset.action; }
  if (!action) return;

  if (action === 'select') {
    var idx = parseInt(t.dataset.idx, 10);
    if (isNaN(idx)) return;
    if (dirty) {
      if (!confirm('未保存の変更があります。破棄しますか？')) return;
    }
    showSpot(idx);
    return;
  }
  if (action === 'save')             { saveChanges(); return; }
  if (action === 'bearing-minus')    { adjustBearing(-5); return; }
  if (action === 'bearing-plus')     { adjustBearing(+5); return; }
  if (action === 'refetch-access')   { refetchAccess(); return; }
  if (action === 'refetch-physical') { refetchPhysical(); return; }
  if (action === 'delete-spot')      { deleteSpot(); return; }
});

document.getElementById('info-table').addEventListener('input', function(e) {
  var field = e.target.dataset && e.target.dataset.field;
  if (field === 'sea_bearing_deg') return; // handled by change
  if (field) markDirty();
});
document.getElementById('info-table').addEventListener('change', function(e) {
  var field = e.target.dataset && e.target.dataset.field;
  if (field) markDirty();
  if (e.target.name === 'target_fish') markDirty();
});

document.getElementById('btn-new').addEventListener('click', openNewSpotModal);
document.getElementById('btn-modal-cancel').addEventListener('click', closeModal);
document.getElementById('btn-modal-ok').addEventListener('click', submitNewSpot);

// ---- init ----
(function() {
  var dl = document.getElementById('photo-datalist');
  SPOT_PHOTOS.forEach(function(f) {
    var opt = document.createElement('option');
    opt.value = '/static/img/spots/' + f;
    dl.appendChild(opt);
  });
})();
buildList();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 共通保存ロジック（Pythonista delegate・HTTP サーバー共用）
# ---------------------------------------------------------------------------

def _save_spot(payload):
    filename = payload.get("_filename")
    if not filename:
        raise ValueError("missing _filename")

    area_err = _validate_area(payload.get("area", {}))
    if area_err:
        return {"ok": False, "error": area_err}

    dir_path = AVAILABLE_DIRS.get(payload.get("_dir_key", ""), AVAILABLE_DIRS[DEFAULT_DIR_KEY])
    path = os.path.join(dir_path, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"file not found: {path}")

    with open(path, encoding="utf-8") as f:
        spot = json.load(f)

    if payload.get("name"):
        spot["name"] = payload["name"]

    loc = payload.get("location", {})
    if loc.get("latitude") is not None:
        spot.setdefault("location", {})["latitude"]  = loc["latitude"]
    if loc.get("longitude") is not None:
        spot.setdefault("location", {})["longitude"] = loc["longitude"]

    area = payload.get("area", {})
    spot.setdefault("area", {}).update({k: v for k, v in area.items() if v is not None and v != ""})

    phys = payload.get("physical_features", {})
    pf = spot.setdefault("physical_features", {})
    if phys.get("sea_bearing_deg") is not None:
        pf["sea_bearing_deg"] = phys["sea_bearing_deg"]
    if phys.get("seabed_type"):
        pf["seabed_type"] = phys["seabed_type"]
    if phys.get("surfer_spot") is not None:
        pf["surfer_spot"] = phys["surfer_spot"]
    if phys.get("depth_near_m") is not None:
        pf["depth_near_m"] = phys["depth_near_m"]
    if phys.get("depth_far_m") is not None:
        pf["depth_far_m"] = phys["depth_far_m"]

    der = payload.get("derived_features", {})
    df = spot.setdefault("derived_features", {})
    if der.get("bottom_kisugo_score") is not None:
        df["bottom_kisugo_score"] = der["bottom_kisugo_score"]
    if der.get("terrain_summary") is not None:
        df["terrain_summary"] = der["terrain_summary"]

    new_type = payload.get("primary_type")
    if new_type:
        clf = spot.setdefault("classification", {})
        clf["primary_type"] = new_type
        clf["source"]       = "manual"
        clf["confidence"]   = 1.0

    info = payload.get("info", {})
    inf = spot.setdefault("info", {})
    for key in ("description", "notes", "access", "photo_url"):
        if info.get(key) is not None:
            if info[key] == "":
                inf.pop(key, None)  # 空文字は削除（フィールド肥大化防止）
            else:
                inf[key] = info[key]

    if "target_fish" in payload:
        spot["target_fish"] = [f for f in payload["target_fish"] if isinstance(f, str)]

    with open(path, "w", encoding="utf-8") as f:
        json.dump(spot, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"[save] OK: {filename}")


def _create_spot(payload):
    """新規スポットを作成し、_filename 付きの dict を返す。スラッグ重複時は None。"""
    name = payload.get("name", "").strip()
    slug = payload.get("slug", "").strip()
    lat  = float(payload.get("lat", 35.3))
    lon  = float(payload.get("lon", 139.5))

    if not name or not slug:
        raise ValueError("missing name or slug")

    dir_key  = payload.get("dir_key", DEFAULT_DIR_KEY)
    dir_path = AVAILABLE_DIRS.get(dir_key, AVAILABLE_DIRS[DEFAULT_DIR_KEY])
    filename = f"{slug}.json"
    path = os.path.join(dir_path, filename)
    if os.path.exists(path):
        return None  # 重複

    area_name = _assign_marine_area(lat, lon)
    area_info = AREA_MAP.get(area_name, ("", "", ""))

    spot = {
        "slug": slug,
        "name": name,
        "location": {"latitude": lat, "longitude": lon},
        "area": {
            "prefecture": area_info[2],
            "pref_slug":  area_info[1],
            "area_name":  area_name or "",
            "area_slug":  area_info[0],
            "city":       "",
            "city_slug":  ""
        },
        "physical_features": {
            "sea_bearing_deg": 180,
            "seabed_type":     "sand",
            "depth_near_m":    None,
            "depth_far_m":     None,
            "surfer_spot":     False
        },
        "derived_features": {
            "bottom_kisugo_score": None,
            "terrain_summary":     ""
        },
        "info": {
            "notes":     "",
            "access":    "",
            "photo_url": f"https://raw.githubusercontent.com/sgrhirose-tech/fishing/resources/photos/{slug}.jpg"
        }
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(spot, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"[newspot] created: {path}")
    spot["_filename"] = filename
    spot["_dir_key"]  = dir_key
    return spot


# ---------------------------------------------------------------------------
# refetch / delete ヘルパー（HTTP モード専用）
# ---------------------------------------------------------------------------

def _run_refetch_access(payload):
    import subprocess
    slug = payload.get("slug", "")
    if not slug:
        return {"ok": False, "error": "slug が指定されていません"}
    result = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "tools", "refetch_access.py"),
         "--slug", slug, "--apply"],
        capture_output=True, text=True, cwd=REPO_ROOT
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or result.stdout.strip()}
    # 更新後のファイルから access を読み返す
    spot_path = os.path.join(REPO_ROOT, "spots", f"{slug}.json")
    if os.path.exists(spot_path):
        with open(spot_path, encoding="utf-8") as f:
            updated = json.load(f)
        return {"ok": True, "access": updated.get("info", {}).get("access", "")}
    return {"ok": True}


def _run_refetch_physical(payload):
    import subprocess
    slug = payload.get("slug", "")
    if not slug:
        return {"ok": False, "error": "slug が指定されていません"}
    result = subprocess.run(
        [sys.executable, os.path.join(REPO_ROOT, "tools", "refetch_physical_data.py"),
         "--slug", slug, "--apply"],
        capture_output=True, text=True, cwd=REPO_ROOT
    )
    if result.returncode != 0:
        return {"ok": False, "error": result.stderr.strip() or result.stdout.strip()}
    return {"ok": True}


def _delete_spot(payload):
    filename = payload.get("filename", "")
    dir_key  = payload.get("dir_key", DEFAULT_DIR_KEY)
    if not filename:
        return {"ok": False, "error": "filename が指定されていません"}
    dir_path = AVAILABLE_DIRS.get(dir_key, AVAILABLE_DIRS[DEFAULT_DIR_KEY])
    path = os.path.join(dir_path, filename)
    if not os.path.exists(path):
        return {"ok": False, "error": f"ファイルが見つかりません: {filename}"}
    os.remove(path)
    print(f"[delete] {path}")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Pythonista delegate
# ---------------------------------------------------------------------------

class SpotDelegate(object):
    def __init__(self, wv, spots, tmp_path):
        self.wv = wv
        self.spots = spots
        self.tmp_path = tmp_path

    def webview_should_start_load(self, wv, url, nav_type):
        if url.startswith("pythonista://save?"):
            qs = url.split("?", 1)[1]
            params = urllib.parse.parse_qs(qs)
            raw = params.get("data", ["{}"])[0]
            self._handle_save(raw)
            return False
        if url.startswith("pythonista://newspot?"):
            qs = url.split("?", 1)[1]
            params = urllib.parse.parse_qs(qs)
            raw = params.get("data", ["{}"])[0]
            self._handle_newspot(raw)
            return False
        if url.startswith("pythonista://changedir?"):
            qs = url.split("?", 1)[1]
            params = urllib.parse.parse_qs(qs)
            dir_key = params.get("dir", [DEFAULT_DIR_KEY])[0]
            if dir_key in AVAILABLE_DIRS:
                self._reload(dir_key)
            return False
        return True

    def _reload(self, dir_key):
        spots = load_spots(AVAILABLE_DIRS[dir_key])
        html  = build_html(spots, save_mode='pythonista', dir_key=dir_key)
        with open(self.tmp_path, "w", encoding="utf-8") as f:
            f.write(html)
        self.wv.load_url("file://" + self.tmp_path)

    def webview_did_finish_load(self, wv):
        pass

    def _handle_save(self, raw):
        try:
            payload = json.loads(urllib.parse.unquote(raw))
        except Exception as e:
            print(f"[save] JSON parse error: {e}")
            return
        try:
            result = _save_spot(payload)
            if result and not result.get('ok'):
                msg = result.get('error', '保存エラー').replace("'", "\\'")
                self.wv.eval_js(f"alert('保存エラー: {msg}');")
        except Exception as e:
            print(f"[save] error: {e}")

    def _handle_newspot(self, raw):
        try:
            payload = json.loads(urllib.parse.unquote(raw))
        except Exception as e:
            print(f"[newspot] JSON parse error: {e}")
            return
        try:
            spot = _create_spot(payload)
        except Exception as e:
            print(f"[newspot] error: {e}")
            return
        if spot is None:
            print(f"[newspot] already exists: {payload.get('slug')}")
            return
        spot_js = json.dumps(spot, ensure_ascii=False)
        self.wv.evaluate_javascript(f"onNewSpotComplete({json.dumps(spot_js, ensure_ascii=False)})")


# ---------------------------------------------------------------------------
# Mac HTTP サーバー
# ---------------------------------------------------------------------------

import http.server as _http_server


class SpotHTTPHandler(_http_server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ('/', '/index.html'):
            params  = urllib.parse.parse_qs(parsed.query)
            dir_key = params.get('dir', [DEFAULT_DIR_KEY])[0]
            if dir_key not in AVAILABLE_DIRS:
                dir_key = DEFAULT_DIR_KEY
            spots = load_spots(AVAILABLE_DIRS[dir_key])
            body  = build_html(spots, save_mode='http', dir_key=dir_key).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            payload = json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception as e:
            self._respond(400, {'ok': False, 'error': str(e)})
            return
        if self.path == '/save':
            try:
                result = _save_spot(payload)
                if result and not result.get('ok'):
                    self._respond(400, result)
                else:
                    self._respond(200, {'ok': True})
            except Exception as e:
                self._respond(500, {'ok': False, 'error': str(e)})
        elif self.path == '/newspot':
            try:
                spot = _create_spot(payload)
                if spot:
                    self._respond(200, {'ok': True, 'spot': spot})
                else:
                    self._respond(400, {'ok': False, 'error': 'スラッグが重複しています'})
            except Exception as e:
                self._respond(500, {'ok': False, 'error': str(e)})
        elif self.path == '/refetch_access':
            try:
                result = _run_refetch_access(payload)
                self._respond(200, result)
            except Exception as e:
                self._respond(500, {'ok': False, 'error': str(e)})
        elif self.path == '/refetch_physical':
            try:
                result = _run_refetch_physical(payload)
                self._respond(200, result)
            except Exception as e:
                self._respond(500, {'ok': False, 'error': str(e)})
        elif self.path == '/delete':
            try:
                result = _delete_spot(payload)
                self._respond(200, result)
            except Exception as e:
                self._respond(500, {'ok': False, 'error': str(e)})
        else:
            self.send_error(404)

    def _respond(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # ログ抑制


# ---------------------------------------------------------------------------
# Build HTML
# ---------------------------------------------------------------------------

def build_html(spots, save_mode='pythonista', dir_key=None):
    if dir_key is None:
        dir_key = DEFAULT_DIR_KEY
    spots_js = json.dumps(spots, ensure_ascii=False)
    seabed_js = json.dumps([[v, l] for v, l in SEABED_TYPE_OPTIONS], ensure_ascii=False)
    bearing_js = json.dumps(BEARING_OPTIONS, ensure_ascii=False)
    classif_js = json.dumps([[v, l] for v, l in CLASSIFICATION_TYPE_OPTIONS], ensure_ascii=False)
    fish_names_js = json.dumps(_load_fish_master(), ensure_ascii=False)  # [[slug, name], ...]
    dir_opts = "".join(
        f'<option value="{k}"{"" if k != dir_key else " selected"}>{k}</option>'
        for k in AVAILABLE_DIRS
    )
    spots_img_dir = os.path.join(REPO_ROOT, "static", "img", "spots")
    spot_photos = sorted(
        f for f in os.listdir(spots_img_dir)
        if os.path.splitext(f)[1].lower() in ('.jpg', '.jpeg', '.png', '.webp')
    ) if os.path.isdir(spots_img_dir) else []
    spot_photos_js = json.dumps(spot_photos, ensure_ascii=False)

    html = HTML_TEMPLATE
    html = html.replace("__SAVE_MODE__",           save_mode)
    html = html.replace("__DIR_KEY__",             dir_key)
    html = html.replace("__DIR_OPTIONS_HTML__",    dir_opts)
    html = html.replace("__SPOTS_JSON__",          spots_js)
    html = html.replace("__SEABED_OPTIONS_JSON__",          seabed_js)
    html = html.replace("__BEARING_OPTIONS_JSON__",         bearing_js)
    html = html.replace("__CLASSIFICATION_OPTIONS_JSON__",  classif_js)
    html = html.replace("__FISH_NAMES_JSON__",              fish_names_js)
    html = html.replace("__SPOT_PHOTOS_JSON__",             spot_photos_js)
    return html


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # --extract-fish モード: WebUI を起動せずバッチ処理して終了
    if "--extract-fish" in sys.argv:
        dir_key  = "spots"
        dry_run  = "--dry-run" in sys.argv
        if "--dir" in sys.argv:
            idx = sys.argv.index("--dir")
            if idx + 1 < len(sys.argv):
                dir_key = sys.argv[idx + 1]
        print(f"[開始] target_fish 抽出  dir={dir_key}  dry-run={dry_run}")
        run_extract_fish(dir_key=dir_key, dry_run=dry_run)
        return

    spots = load_spots()

    if sys.platform == 'ios':
        # Pythonista (iPhone/iPad)
        import ui

        html = build_html(spots, save_mode='pythonista')

        import tempfile
        tmp_path = os.path.join(tempfile.gettempdir(), "spot_editor_ui.html")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(html)

        wv = ui.WebView(name="スポットエディタ")
        wv.flex = "WH"
        delegate = SpotDelegate(wv, spots, tmp_path)
        wv.delegate = delegate
        wv.load_url("file://" + tmp_path)
        wv.present("fullscreen")

    else:
        # Mac fallback: ローカル HTTP サーバー
        import socket
        import webbrowser

        with socket.socket() as _s:
            _s.bind(('', 0))
            port = _s.getsockname()[1]

        srv = _http_server.HTTPServer(('localhost', port), SpotHTTPHandler)

        url = f'http://localhost:{port}/'
        print(f"スポットエディタ: {url}")
        print("終了するには Ctrl+C")
        webbrowser.open(url)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            print("\n終了")


if __name__ == "__main__":
    main()
