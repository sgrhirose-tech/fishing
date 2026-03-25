"""
verify_spots.py — スポットデータ確認・修正ツール

spots/*.json を読み込み、Leaflet.js マップ付きの HTML ビューワーを生成する。
Pythonista (iOS) では ui.WebView で開き、編集内容を spots/*.json に保存できる。
デスクトップでは webbrowser で開く（読み取り専用）。
"""
from pathlib import Path
import json
import threading
import webbrowser


def load_spots(spots_dir: Path):
    spots = []
    for f in sorted(spots_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            data["_filename"] = f.name  # 保存時に使用
            spots.append(data)
        except Exception as e:
            print(f"読み込みエラー {f.name}: {e}")
    return spots


def _load_marine_areas(spots_dir: Path) -> dict:
    """spots/_marine_areas.json からエリア名→(center_lat, center_lon) を返す"""
    path = spots_dir / "_marine_areas.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            name: (v["center_lat"], v["center_lon"])
            for name, v in data.get("areas", {}).items()
            if "center_lat" in v and "center_lon" in v
        }
    except Exception as e:
        print(f"[警告] _marine_areas.json 読み込み失敗: {e}")
        return {}


def _assign_marine_area(spot: dict, marine_areas: dict) -> str:
    """スポット座標に最近傍のエリア名を返す（nearest-neighbor）"""
    loc = spot.get("location") or {}
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    if lat is None or lon is None:
        return ""
    best, best_d = "", float("inf")
    for name, (clat, clon) in marine_areas.items():
        d = (lat - clat) ** 2 + (lon - clon) ** 2
        if d < best_d:
            best_d = d
            best = name
    return best


# 主要方位ラベル（5° 刻み select 用）
COMPASS_LABELS = {
    0: "北", 45: "北東", 90: "東", 135: "南東",
    180: "南", 225: "南西", 270: "西", 315: "北西",
}

def _bearing_options_html():
    """0〜355° を 5° 刻みの <option> タグとして生成"""
    lines = ['<option value="">-- 選択 --</option>']
    for deg in range(0, 360, 5):
        label = f"{deg}°"
        if deg in COMPASS_LABELS:
            label += f" ({COMPASS_LABELS[deg]})"
        lines.append(f'<option value="{deg}">{label}</option>')
    return "\n".join(lines)


BOTTOM_OPTIONS = [
    "砂", "砂泥", "泥", "砂礫", "礫", "石・岩", "貝殻", "さんご", "溶岩", "混合",
]

def _bottom_options_html():
    lines = ['<option value="">-- 選択 --</option>']
    for v in BOTTOM_OPTIONS:
        lines.append(f'<option value="{v}">{v}</option>')
    return "\n".join(lines)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>スポット確認ビューワー</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, sans-serif; font-size: 14px; display: flex; flex-direction: column; height: 100vh; }

/* ナビゲーションバー */
#nav {
  display: flex; align-items: center; justify-content: space-between;
  padding: 8px 12px; background: #2c6fad; color: white; flex-shrink: 0;
}
#nav button {
  background: white; color: #2c6fad; border: none; border-radius: 6px;
  padding: 6px 14px; font-size: 15px; font-weight: bold; cursor: pointer;
  min-width: 60px;
}
#nav button:disabled { opacity: 0.4; cursor: default; }
#nav-title { text-align: center; flex: 1; font-size: 15px; font-weight: bold; line-height: 1.3; }
#nav-count { font-size: 12px; opacity: 0.85; }

/* マップ */
#map { flex: 1; min-height: 0; }

/* 情報パネル */
#panel {
  height: 45vh; overflow-y: auto; background: #f9f9f9;
  border-top: 2px solid #2c6fad; flex-shrink: 0;
}
#panel table { width: 100%; border-collapse: collapse; }
#panel td { padding: 5px 10px; border-bottom: 1px solid #e0e0e0; vertical-align: middle; }
#panel td:first-child { width: 38%; color: #555; font-weight: 600; white-space: nowrap; }
.missing { color: #d32f2f; font-weight: bold; }
.ok { color: #1a6e1a; }
.section-header td {
  background: #2c6fad; color: white; font-weight: bold; padding: 5px 10px;
}

/* 編集コントロール */
.edit-row { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.adj-btn {
  background: #e3f2fd; border: 1px solid #90caf9; border-radius: 5px;
  padding: 3px 10px; font-size: 14px; cursor: pointer; color: #1565c0; font-weight: bold;
}
.adj-btn:active { background: #bbdefb; }
.bearing-val { font-weight: bold; color: #1565c0; min-width: 45px; text-align: center; }
select.edit-select {
  border: 1px solid #90caf9; border-radius: 5px; padding: 4px 6px; font-size: 13px;
  background: white; color: #333;
}

/* 保存ボタン */
#save-bar {
  padding: 8px 12px; background: #fff8e1; border-top: 1px solid #ffe082;
  display: none; flex-shrink: 0;
}
#save-btn {
  width: 100%; padding: 8px; background: #f57c00; color: white;
  border: none; border-radius: 8px; font-size: 15px; font-weight: bold; cursor: pointer;
}
#save-btn:active { background: #e65100; }
#save-msg { font-size: 12px; color: #f57c00; margin-top: 4px; text-align: center; }

/* エリアフィルターバー */
#area-bar {
  display: flex; align-items: center; gap: 8px;
  padding: 6px 12px; background: #e3f2fd; border-bottom: 1px solid #90caf9;
  flex-shrink: 0;
}
#area-bar label { font-weight: bold; color: #1565c0; font-size: 14px; white-space: nowrap; }
#area-bar select { border: 1px solid #90caf9; border-radius: 5px; padding: 4px 8px; font-size: 14px; background: white; color: #333; }

/* ── 新規登録モーダル ── */
#newspot-overlay {
  display: none; position: fixed; inset: 0;
  background: rgba(0,0,0,0.55); z-index: 9000;
  align-items: center; justify-content: center;
}
#newspot-overlay.open { display: flex; }
#newspot-card {
  background: white; border-radius: 12px;
  padding: 20px 18px 16px; width: 88%; max-width: 380px;
  box-shadow: 0 8px 32px rgba(0,0,0,0.28);
}
#newspot-card h2 { font-size: 16px; font-weight: bold; color: #1565c0; margin-bottom: 14px; text-align: center; }
.ns-field { margin-bottom: 12px; }
.ns-field label { display: block; font-size: 12px; color: #555; font-weight: 600; margin-bottom: 4px; }
.ns-field input {
  width: 100%; border: 1px solid #90caf9; border-radius: 6px;
  padding: 8px 10px; font-size: 15px; background: #fafafa;
}
.ns-field input:focus { outline: none; border-color: #1565c0; background: white; }
.ns-field input.input-error { border-color: #d32f2f; background: #fff8f8; }
#ns-error { font-size: 12px; color: #d32f2f; min-height: 18px; margin-bottom: 8px; text-align: center; }
#ns-progress {
  display: none; font-size: 12px; color: #555;
  background: #f5f5f5; border-radius: 6px;
  padding: 8px 10px; margin-bottom: 10px; line-height: 1.8;
}
#ns-progress.visible { display: block; }
.ns-btn-row { display: flex; gap: 10px; justify-content: flex-end; margin-top: 4px; }
#ns-submit-btn {
  background: #1565c0; color: white; border: none;
  border-radius: 8px; padding: 9px 20px; font-size: 14px; font-weight: bold; cursor: pointer; flex: 1;
}
#ns-submit-btn:disabled { background: #90caf9; cursor: default; }
#ns-cancel-btn {
  background: #e0e0e0; color: #333; border: none;
  border-radius: 8px; padding: 9px 16px; font-size: 14px; cursor: pointer;
}
</style>
</head>
<body>

<div id="nav">
  <button id="btn-prev" onclick="navigate(-1)">◀ 前へ</button>
  <div id="nav-title">
    <div id="spot-name">—</div>
    <div id="nav-count"></div>
  </div>
  <button id="btn-next" onclick="navigate(1)">次へ ▶</button>
</div>

<div id="area-bar">
  <label>エリア</label>
  <select id="area-select">
    <option value="">すべて</option>
  </select>
  <button id="new-spot-btn" style="
    margin-left:auto; background:#1565c0; color:white;
    border:none; border-radius:6px; padding:5px 12px;
    font-size:13px; font-weight:bold; cursor:pointer; white-space:nowrap;">
    ＋ 新規登録
  </button>
</div>

<div id="newspot-overlay">
  <div id="newspot-card">
    <h2>＋ 新規スポット登録</h2>
    <div class="ns-field">
      <label for="ns-name">スポット名</label>
      <input type="text" id="ns-name" placeholder="例: 野球場下" autocomplete="off">
    </div>
    <div class="ns-field">
      <label for="ns-lat">緯度</label>
      <input type="number" id="ns-lat" placeholder="例: 35.3179094" step="any">
    </div>
    <div class="ns-field">
      <label for="ns-lon">経度</label>
      <input type="number" id="ns-lon" placeholder="例: 139.4054069" step="any">
    </div>
    <div id="ns-error"></div>
    <div id="ns-progress"></div>
    <div class="ns-btn-row">
      <button id="ns-cancel-btn">キャンセル</button>
      <button id="ns-submit-btn">登録開始</button>
    </div>
  </div>
</div>

<div id="map"></div>

<div id="panel">
  <table id="info-table"></table>
</div>

<div id="save-bar">
  <button id="save-btn" onclick="saveChanges()">💾 保存</button>
  <div id="save-msg"></div>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const SPOTS = __SPOTS_JSON__;
const AREA_NAMES = __AREA_NAMES_JSON__;
const BEARING_OPTIONS_HTML = `__BEARING_OPTIONS__`;
const BOTTOM_OPTIONS_HTML = `__BOTTOM_OPTIONS__`;

let currentIndex = 0;
let filteredSpots = [];   // 現在のフィルター結果
let filteredPos   = 0;    // filteredSpots 内の位置
let pendingBearing = null;   // 変更中の bearing 値
let pendingBottom  = null;   // 変更中の bottom_type.value
let pendingCoords  = null;   // ドラッグ後の新座標 {lat, lon}

// ドラッグ後は新座標、未変更なら元の座標を返す
function getEffectiveLoc() {
  if (pendingCoords !== null) return [pendingCoords.lat, pendingCoords.lon];
  const s = SPOTS[currentIndex];
  return [s.location.latitude, s.location.longitude];
}

// pendingBearing 優先、なければ元の bearing を返す
function getEffectiveBearing() {
  if (pendingBearing !== null) return pendingBearing;
  const s = SPOTS[currentIndex];
  return s.physical_features ? s.physical_features.sea_bearing_deg : null;
}

const map = L.map('map');
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '© OpenStreetMap contributors', maxZoom: 18,
}).addTo(map);

const spotLayer = L.layerGroup().addTo(map);

function bearing2latlon(lat, lon, bearingDeg, distM) {
  const R = 6371000;
  const b = bearingDeg * Math.PI / 180;
  const la = lat * Math.PI / 180;
  const lo = lon * Math.PI / 180;
  const d = distM / R;
  const lat2 = Math.asin(Math.sin(la)*Math.cos(d) + Math.cos(la)*Math.sin(d)*Math.cos(b));
  const lon2 = lo + Math.atan2(Math.sin(b)*Math.sin(d)*Math.cos(la), Math.cos(d)-Math.sin(la)*Math.sin(lat2));
  return [lat2 * 180/Math.PI, lon2 * 180/Math.PI];
}

// マップ上の矢印を更新（bearing が null なら消す）
function updateBearingArrow(lat, lon, bearing) {
  spotLayer.clearLayers();
  const marker = L.marker([lat, lon], {draggable: true})
    .addTo(spotLayer)
    .bindPopup(SPOTS[currentIndex].name);
  marker.on('dragend', function(e) {
    const p = e.target.getLatLng();
    pendingCoords = {lat: p.lat, lon: p.lng};
    document.getElementById('coord-lat').textContent = p.lat.toFixed(6);
    document.getElementById('coord-lon').textContent = p.lng.toFixed(6);
    updateBearingArrow(p.lat, p.lng, getEffectiveBearing());
    markDirty();
  });
  if (bearing !== null && bearing !== undefined && bearing !== '') {
    const tip = bearing2latlon(lat, lon, Number(bearing), 400);
    L.polyline([[lat, lon], tip], {color: '#1565c0', weight: 4}).addTo(spotLayer);
    L.circleMarker(tip, {radius: 7, color: '#1565c0', fillColor: '#1565c0', fillOpacity: 1})
      .addTo(spotLayer)
      .bindTooltip('海方向 ' + Math.round(Number(bearing)) + '°');
  } else {
    L.marker([lat, lon], {
      icon: L.divIcon({className: '', html: '<div style="background:rgba(255,255,255,0.85);padding:3px 6px;border:1px solid #d32f2f;color:#d32f2f;font-size:12px;border-radius:4px;white-space:nowrap">海方向データなし</div>', iconAnchor:[0,0]})
    }).addTo(spotLayer);
  }
}

// ±5° 調整ボタン
function adjustBearing(delta) {
  const s = SPOTS[currentIndex];
  const [lat, lon] = getEffectiveLoc();
  const base = pendingBearing !== null ? pendingBearing
    : (s.physical_features && s.physical_features.sea_bearing_deg !== null
       ? s.physical_features.sea_bearing_deg : 0);
  pendingBearing = ((Number(base) + delta) % 360 + 360) % 360;
  document.getElementById('bearing-val').textContent = Math.round(pendingBearing) + '°';
  updateBearingArrow(lat, lon, pendingBearing);
  markDirty();
}

// select から bearing を適用
function applyBearing() {
  const sel = document.getElementById('bearing-select');
  if (sel.value === '') return;
  const [lat, lon] = getEffectiveLoc();
  pendingBearing = Number(sel.value);
  updateBearingArrow(lat, lon, pendingBearing);
  document.getElementById('bearing-display').textContent = Math.round(pendingBearing) + '°';
  markDirty();
}

// 底質タイプ select 変更
function onBottomChange() {
  const sel = document.getElementById('bottom-select');
  if (sel.value === '') { pendingBottom = null; }
  else { pendingBottom = sel.value; }
  markDirty();
}

function markDirty() {
  document.getElementById('save-bar').style.display = 'block';
  document.getElementById('save-msg').textContent = '';
}

function saveChanges() {
  const changes = {};
  if (pendingBearing !== null) changes.sea_bearing_deg = pendingBearing;
  if (pendingBottom  !== null) changes.bottom_type_value = pendingBottom;
  if (pendingCoords  !== null) { changes.location_lat = pendingCoords.lat; changes.location_lon = pendingCoords.lon; }
  if (Object.keys(changes).length === 0) return;

  // 保存後にバックグラウンド再取得が走るかを判定
  const willRefetch = ('location_lat' in changes) && (getEffectiveBearing() !== null);

  const payload = JSON.stringify({filename: SPOTS[currentIndex]._filename, changes});
  // Python delegate が 'pythonista://save?data=...' を捕捉して JSON に書き戻す
  window.location.href = 'pythonista://save?data=' + encodeURIComponent(payload);
  // ローカルの SPOTS データも更新
  if (pendingBearing !== null) {
    SPOTS[currentIndex].physical_features.sea_bearing_deg = pendingBearing;
  }
  if (pendingBottom !== null) {
    if (!SPOTS[currentIndex].physical_features.bottom_type) {
      SPOTS[currentIndex].physical_features.bottom_type = {};
    }
    SPOTS[currentIndex].physical_features.bottom_type.value = pendingBottom;
  }
  if (pendingCoords !== null) {
    SPOTS[currentIndex].location.latitude  = pendingCoords.lat;
    SPOTS[currentIndex].location.longitude = pendingCoords.lon;
  }
  pendingBearing = null;
  pendingBottom  = null;
  pendingCoords  = null;

  if (willRefetch) {
    // 再取得中ステータスを表示。完了は onRefetchComplete が save-bar を閉じる
    const statusEl = document.getElementById('refetch-status');
    if (statusEl) { statusEl.textContent = '再取得中...'; statusEl.style.color = '#888'; }
    document.getElementById('save-msg').textContent = '✓ 保存しました（底質・水深を再取得中）';
  } else {
    document.getElementById('save-msg').textContent = '✓ 保存しました';
    setTimeout(() => {
      document.getElementById('save-bar').style.display = 'none';
      document.getElementById('save-msg').textContent = '';
    }, 2000);
  }
}

function val(v, unit) {
  if (v === null || v === undefined) return '<span class="missing">データなし</span>';
  return '<span class="ok">' + v + (unit ? ' ' + unit : '') + '</span>';
}

function showSpot(idx) {
  pendingBearing = null;
  pendingBottom  = null;
  pendingCoords  = null;
  document.getElementById('save-bar').style.display = 'none';

  const s = SPOTS[idx];
  const lat = s.location.latitude;
  const lon = s.location.longitude;
  const pf  = s.physical_features || {};
  const bt  = pf.bottom_type || {};
  const df  = s.derived_features || {};
  const cd  = df.contour_distances_m || {};
  const area = s.area || {};

  // ナビ更新
  document.getElementById('spot-name').textContent = s.name;
  document.getElementById('nav-count').textContent = (filteredPos+1) + ' / ' + filteredSpots.length;
  document.getElementById('btn-prev').disabled = (filteredPos === 0);
  document.getElementById('btn-next').disabled = (filteredPos === filteredSpots.length - 1);

  // マップ更新
  map.setView([lat, lon], 14);
  updateBearingArrow(lat, lon, pf.sea_bearing_deg);

  // 海の方向フィールド HTML
  const bearing = pf.sea_bearing_deg;
  let bearingCell;
  if (bearing !== null && bearing !== undefined) {
    bearingCell = `
      <div class="edit-row">
        <button class="adj-btn" data-action="adj-minus">-5°</button>
        <span class="bearing-val" id="bearing-val">${Math.round(bearing)}°</span>
        <button class="adj-btn" data-action="adj-plus">+5°</button>
      </div>`;
  } else {
    bearingCell = `
      <span class="missing" id="bearing-display">データなし</span>
      <div class="edit-row" style="margin-top:4px">
        <select class="edit-select" id="bearing-select">${BEARING_OPTIONS_HTML}</select>
        <button class="adj-btn" data-action="apply-bearing">適用</button>
      </div>`;
  }

  // 底質タイプフィールド HTML
  const btVal = bt.value;
  let bottomCell;
  if (btVal !== null && btVal !== undefined) {
    bottomCell = `<span class="ok">${btVal}</span>`;
  } else {
    bottomCell = `
      <select class="edit-select" id="bottom-select">
        ${BOTTOM_OPTIONS_HTML}
      </select>`;
  }

  // best_match の name を表示
  const bmName = bt.best_match ? bt.best_match.name : null;

  const rows = [
    ['section', '基本情報'],
    ['都道府県', val(area.prefecture)],
    ['市区町村', val(area.city)],
    ['緯度', `<span id="coord-lat" class="ok">${lat}</span>`],
    ['経度', `<span id="coord-lon" class="ok">${lon}</span>`],
    ['section', '海・地形'],
    ['海の方向', bearingCell],
    ['底質タイプ', `<span id="cell-bottom-type">${bottomCell}</span>`],
    ['最適マッチ', `<span id="cell-bottom-best">${val(bmName)}</span>`],
    ['底質スコア', `<span id="cell-bottom-score">${val(df.bottom_kisugo_score)}</span>`],
    ['地形メモ', `<span id="cell-terrain">${val(df.terrain_summary)}</span>`],
    ['refetch', ''],
    ['section', '等深線距離'],
    ['20m', `<span id="cell-depth-20">${val(cd['20m'], 'm')}</span>`],
    ['50m', `<span id="cell-depth-50">${val(cd['50m'], 'm')}</span>`],
    ['100m', `<span id="cell-depth-100">${val(cd['100m'], 'm')}</span>`],
    ['150m', `<span id="cell-depth-150">${val(cd['150m'], 'm')}</span>`],
    ['200m', `<span id="cell-depth-200">${val(cd['200m'], 'm')}</span>`],
  ];

  let html = '';
  for (const r of rows) {
    if (r[0] === 'section') {
      html += '<tr class="section-header"><td colspan="2">' + r[1] + '</td></tr>';
    } else if (r[0] === 'refetch') {
      html += '<tr><td colspan="2" style="text-align:center;padding:8px 10px">' +
        '<div class="edit-row" style="justify-content:center">' +
        '<button class="adj-btn" data-action="do-refetch" style="background:#e8f5e9;border-color:#a5d6a7;color:#2e7d32;padding:5px 16px">底質・水深を再取得</button>' +
        '<span id="refetch-status" style="font-size:12px;color:#888;margin-left:8px"></span>' +
        '</div></td></tr>';
    } else {
      html += '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td></tr>';
    }
  }
  document.getElementById('info-table').innerHTML = html;
}

function applyAreaFilter() {
  const area = document.getElementById('area-select').value;
  filteredSpots = area === '' ? SPOTS.slice() : SPOTS.filter(s => s._marine_area === area);
  filteredPos = 0;
  if (filteredSpots.length > 0) {
    currentIndex = SPOTS.indexOf(filteredSpots[0]);
    showSpot(currentIndex);
  } else {
    document.getElementById('spot-name').textContent = 'スポットなし';
    document.getElementById('nav-count').textContent = '0 / 0';
    document.getElementById('btn-prev').disabled = true;
    document.getElementById('btn-next').disabled = true;
    spotLayer.clearLayers();
  }
}

function navigate(delta) {
  const next = filteredPos + delta;
  if (next < 0 || next >= filteredSpots.length) return;
  filteredPos = next;
  currentIndex = SPOTS.indexOf(filteredSpots[filteredPos]);
  showSpot(currentIndex);
}

function doRefetch() {
  const [lat, lon] = getEffectiveLoc();
  const bearing = getEffectiveBearing();
  if (bearing === null || bearing === undefined || bearing === '') {
    alert('海の方向が設定されていません。先に海の方向を設定してください。');
    return;
  }
  const statusEl = document.getElementById('refetch-status');
  if (statusEl) { statusEl.textContent = '再取得中...'; statusEl.style.color = '#888'; }
  const payload = JSON.stringify({
    filename: SPOTS[currentIndex]._filename,
    lat: lat,
    lon: lon,
    sea_bearing_deg: Number(bearing),
  });
  window.location.href = 'pythonista://refetch?data=' + encodeURIComponent(payload);
}

function onRefetchComplete(data) {
  const statusEl = document.getElementById('refetch-status');
  if (data.status === 'error') {
    if (statusEl) { statusEl.textContent = '失敗: ' + data.message; statusEl.style.color = '#d32f2f'; }
    return;
  }
  // SPOTS の in-memory データを更新
  const s = SPOTS[currentIndex];
  const df = s.derived_features || (s.derived_features = {});
  const pf = s.physical_features || (s.physical_features = {});
  const bt = pf.bottom_type || (pf.bottom_type = {});
  if (data.bottom_type_value    !== undefined) bt.value                  = data.bottom_type_value;
  if (data.bottom_best_match    !== undefined) { if (!bt.best_match) bt.best_match = {}; bt.best_match.name = data.bottom_best_match; }
  if (data.bottom_kisugo_score  !== undefined) df.bottom_kisugo_score    = data.bottom_kisugo_score;
  if (data.terrain_summary      !== undefined) df.terrain_summary        = data.terrain_summary;
  if (data.contour_distances_m  !== undefined) df.contour_distances_m    = data.contour_distances_m;
  // セルを個別更新（pendingBearing / pendingCoords は維持）
  function setCell(id, content) { const el = document.getElementById(id); if (el) el.innerHTML = content; }
  setCell('cell-bottom-type',  data.bottom_type_value  ? `<span class="ok">${data.bottom_type_value}</span>`              : val(null));
  setCell('cell-bottom-best',  val(data.bottom_best_match   !== undefined ? data.bottom_best_match   : null));
  setCell('cell-bottom-score', val(data.bottom_kisugo_score !== undefined ? data.bottom_kisugo_score : null));
  setCell('cell-terrain',      val(data.terrain_summary     !== undefined ? data.terrain_summary     : null));
  const cd = data.contour_distances_m || {};
  setCell('cell-depth-20',  val(cd['20m']  !== undefined ? cd['20m']  : null, 'm'));
  setCell('cell-depth-50',  val(cd['50m']  !== undefined ? cd['50m']  : null, 'm'));
  setCell('cell-depth-100', val(cd['100m'] !== undefined ? cd['100m'] : null, 'm'));
  setCell('cell-depth-150', val(cd['150m'] !== undefined ? cd['150m'] : null, 'm'));
  setCell('cell-depth-200', val(cd['200m'] !== undefined ? cd['200m'] : null, 'm'));
  if (statusEl) { statusEl.textContent = '✓ 再取得完了'; statusEl.style.color = '#2e7d32'; }
  // 保存フローで呼ばれた場合に save-bar を閉じる
  setTimeout(() => {
    document.getElementById('save-bar').style.display = 'none';
    document.getElementById('save-msg').textContent = '';
  }, 2000);
}

function openNewSpotModal() {
  ['ns-name','ns-lat','ns-lon'].forEach(id => {
    const el = document.getElementById(id);
    el.value = '';
    el.classList.remove('input-error');
  });
  document.getElementById('ns-error').textContent = '';
  document.getElementById('ns-progress').textContent = '';
  document.getElementById('ns-progress').classList.remove('visible');
  document.getElementById('ns-submit-btn').disabled = false;
  document.getElementById('ns-submit-btn').textContent = '登録開始';
  document.getElementById('newspot-overlay').classList.add('open');
  setTimeout(() => document.getElementById('ns-name').focus(), 80);
}

function closeNewSpotModal() {
  document.getElementById('newspot-overlay').classList.remove('open');
}

function submitNewSpot() {
  const nameEl = document.getElementById('ns-name');
  const latEl  = document.getElementById('ns-lat');
  const lonEl  = document.getElementById('ns-lon');
  const errEl  = document.getElementById('ns-error');
  errEl.textContent = '';
  [nameEl, latEl, lonEl].forEach(el => el.classList.remove('input-error'));

  const name = nameEl.value.trim();
  const lat  = parseFloat(latEl.value);
  const lon  = parseFloat(lonEl.value);

  let err = '';
  if (!name)                                            { nameEl.classList.add('input-error'); err = 'スポット名を入力してください'; }
  else if (isNaN(lat) || lat < -90  || lat > 90)       { latEl.classList.add('input-error');  err = '緯度が不正です（-90〜90）'; }
  else if (isNaN(lon) || lon < -180 || lon > 180)      { lonEl.classList.add('input-error');  err = '経度が不正です（-180〜180）'; }
  if (err) { errEl.textContent = err; return; }

  document.getElementById('ns-submit-btn').disabled = true;
  document.getElementById('ns-cancel-btn').disabled = true;
  document.getElementById('ns-submit-btn').textContent = '取得中...';
  document.getElementById('ns-progress').classList.add('visible');
  document.getElementById('ns-progress').innerHTML = '⏳ 開始中...';

  const payload = JSON.stringify({name, lat, lon});
  window.location.href = 'pythonista://newspot?data=' + encodeURIComponent(payload);
}

function onNewSpotProgress(data) {
  const labels = {
    1: '🧭 海の方向を算出中...',
    2: '📍 住所を取得中...',
    3: '🪨 底質データを取得中...',
    4: '📏 水深データを取得中...',
    5: '💾 JSON を生成中...',
  };
  const el = document.getElementById('ns-progress');
  if (el) el.innerHTML = labels[data.step] || data.message || '';
}

function onNewSpotComplete(data) {
  document.getElementById('ns-cancel-btn').disabled = false;
  if (data.status === 'error') {
    document.getElementById('ns-error').textContent = '❌ ' + data.message;
    document.getElementById('ns-submit-btn').disabled = false;
    document.getElementById('ns-submit-btn').textContent = '登録開始';
    document.getElementById('ns-progress').classList.remove('visible');
    return;
  }
  const spot = data.spot;
  SPOTS.push(spot);

  // area-select に新エリアがなければ追加
  const areaEl = document.getElementById('area-select');
  if (spot._marine_area && ![...areaEl.options].some(o => o.value === spot._marine_area)) {
    const opt = document.createElement('option');
    opt.value = spot._marine_area;
    opt.textContent = spot._marine_area;
    areaEl.appendChild(opt);
  }

  // フィルターをリセットして新スポットに移動
  areaEl.value = '';
  filteredSpots = SPOTS.slice();
  filteredPos   = filteredSpots.length - 1;
  currentIndex  = SPOTS.length - 1;

  closeNewSpotModal();
  showSpot(currentIndex);
  document.getElementById('save-msg').textContent = '✓ 登録完了: ' + spot.name;
  document.getElementById('save-bar').style.display = 'block';
  setTimeout(() => {
    document.getElementById('save-bar').style.display = 'none';
    document.getElementById('save-msg').textContent = '';
  }, 3000);
}

// イベント委譲: innerHTML で挿入した要素の onclick は WKWebView でブロックされるため
// data-action 属性 + addEventListener で統一処理する
document.getElementById('info-table').addEventListener('click', function(e) {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const a = btn.dataset.action;
  if (a === 'adj-minus')          adjustBearing(-5);
  else if (a === 'adj-plus')      adjustBearing(+5);
  else if (a === 'apply-bearing') applyBearing();
  else if (a === 'do-refetch')    doRefetch();
});
document.getElementById('info-table').addEventListener('change', function(e) {
  if (e.target.id === 'bottom-select') onBottomChange();
});

// エリアセレクトを構築
const areaSelectEl = document.getElementById('area-select');
for (const name of AREA_NAMES) {
  const opt = document.createElement('option');
  opt.value = name;
  opt.textContent = name;
  areaSelectEl.appendChild(opt);
}
areaSelectEl.addEventListener('change', applyAreaFilter);

// 新規登録モーダルのイベント
document.getElementById('new-spot-btn').addEventListener('click', openNewSpotModal);
document.getElementById('ns-cancel-btn').addEventListener('click', closeNewSpotModal);
document.getElementById('ns-submit-btn').addEventListener('click', submitNewSpot);
document.getElementById('newspot-overlay').addEventListener('click', function(e) {
  if (e.target === this) closeNewSpotModal();
});

// 初期表示
filteredSpots = SPOTS.slice();
filteredPos = 0;
if (SPOTS.length > 0) {
  showSpot(0);
} else {
  document.getElementById('spot-name').textContent = 'スポットデータなし';
}
</script>
</body>
</html>
"""


class SpotDelegate:
    """Pythonista ui.WebView デリゲート — 保存リクエストを捕捉して JSON に書き戻す"""

    def __init__(self, spots_dir: Path):
        self.spots_dir = spots_dir
        self.webview = None  # set on first delegate call for evaluate_javascript

    def webview_should_start_load(self, webview, url, nav_type):
        self.webview = webview
        if url.startswith("pythonista://save"):
            try:
                self._handle_save(url)
            except Exception as e:
                import traceback; traceback.print_exc()
                self._send_refetch_result({"status": "error", "message": str(e)})
            return False
        if url.startswith("pythonista://refetch"):
            try:
                self._handle_refetch(url)
            except Exception as e:
                import traceback; traceback.print_exc()
                self._send_refetch_result({"status": "error", "message": str(e)})
            return False
        if url.startswith("pythonista://newspot"):
            try:
                self._handle_newspot(url)
            except Exception as e:
                import traceback; traceback.print_exc()
                self._send_newspot_result({"status": "error", "message": str(e)})
            return False
        return True

    def _handle_save(self, url: str):
        from urllib.parse import urlparse, parse_qs, unquote
        qs = parse_qs(urlparse(url).query)
        payload = json.loads(unquote(qs["data"][0]))
        filename = payload["filename"]
        changes = payload["changes"]

        path = self.spots_dir / filename
        data = json.loads(path.read_text(encoding="utf-8"))

        if "sea_bearing_deg" in changes:
            v = changes["sea_bearing_deg"]
            data["physical_features"]["sea_bearing_deg"] = float(v) if v != "" else None

        if "bottom_type_value" in changes:
            v = changes["bottom_type_value"]
            if not data["physical_features"].get("bottom_type"):  # None / 未存在 どちらも対応
                data["physical_features"]["bottom_type"] = {}
            data["physical_features"]["bottom_type"]["value"] = v if v != "" else None

        if "location_lat" in changes and "location_lon" in changes:
            data["location"]["latitude"]  = float(changes["location_lat"])
            data["location"]["longitude"] = float(changes["location_lon"])
            data["location"]["coordinate_source"] = "manual drag"

        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"保存完了: {filename}")

        # ── Feature 1: 座標変更 → バックグラウンドで底質・水深を再取得 ──
        coord_changed  = "location_lat" in changes and "location_lon" in changes
        bottom_changed = "bottom_type_value" in changes

        if coord_changed:
            bearing = data["physical_features"].get("sea_bearing_deg")
            if bearing is None:
                self._send_refetch_result({
                    "status": "error",
                    "message": "海の方向が未設定のため底質・水深を再取得できません",
                })
                return
            self._run_refetch(
                filename,
                float(changes["location_lat"]),
                float(changes["location_lon"]),
                float(bearing),
            )

        # ── Feature 2: 底質手動変更 → 派生フィーチャーを再計算（同期） ──
        elif bottom_changed:
            try:
                import sys
                sys.path.insert(0, str(self.spots_dir.parent))
                from build_spots_complete import derive_features_from_physical
            except ImportError as e:
                self._send_refetch_result({"status": "error", "message": f"再計算失敗: {e}"})
                return

            v = changes.get("bottom_type_value")
            if v:
                # best_match.name も手動値に合わせて更新（最適マッチ表示を一致させる）
                if not data["physical_features"].get("bottom_type"):
                    data["physical_features"]["bottom_type"] = {}
                bt = data["physical_features"]["bottom_type"]
                bt["best_match"] = {"name": v}   # null の場合も直接代入で上書き

            new_df = derive_features_from_physical(data)
            data["derived_features"] = new_df
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            bt = data["physical_features"].get("bottom_type") or {}
            self._send_refetch_result({
                "status": "ok",
                "filename": filename,
                "bottom_type_value": bt.get("value"),
                "bottom_best_match": (bt.get("best_match") or {}).get("name"),
                "bottom_kisugo_score": new_df.get("bottom_kisugo_score"),
                "terrain_summary": new_df.get("terrain_summary"),
                "contour_distances_m": new_df.get("contour_distances_m", {}),
            })

    def _handle_refetch(self, url: str):
        from urllib.parse import urlparse, parse_qs, unquote
        qs = parse_qs(urlparse(url).query)
        payload = json.loads(unquote(qs["data"][0]))
        self._run_refetch(
            payload["filename"],
            float(payload["lat"]),
            float(payload["lon"]),
            float(payload["sea_bearing_deg"]),
        )

    def _run_refetch(self, filename: str, lat: float, lon: float, sea_bearing_deg: float):
        """バックグラウンドスレッドで底質・水深 API を再取得し JSON を更新する。"""
        def run():
            try:
                import sys
                sys.path.insert(0, str(self.spots_dir.parent))
                from build_spots_complete import (
                    query_bottom_types, query_depth_contours,
                    summarize_depth_profile_from_contours, derive_features_from_physical,
                )
            except ImportError as e:
                self._send_refetch_result({"status": "error", "message": f"インポート失敗: {e}"})
                return

            try:
                from datetime import datetime, timezone
                updated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

                print(f"  底質再取得中... lat={lat}, lon={lon}, bearing={sea_bearing_deg}")
                bottom_data = query_bottom_types(lat, lon, sea_bearing_deg)
                print("  等深線再取得中...")
                depth_raw = query_depth_contours(lat, lon)
                depth_summary = summarize_depth_profile_from_contours(depth_raw["nearest_contours"])

                path = self.spots_dir / filename
                data = json.loads(path.read_text(encoding="utf-8"))
                data["physical_features"]["bottom_type"] = {
                    **bottom_data,
                    "source_system": "海しる",
                    "last_updated": updated_at,
                }
                data["physical_features"]["depth_profile"] = {
                    **depth_summary,
                    "raw_contours": depth_raw["nearest_contours"],
                    "status": "取得済み",
                    "source_system": "海しる",
                    "last_updated": updated_at,
                }
                data["derived_features"] = derive_features_from_physical(data)
                data["metadata"]["updated_at"] = updated_at
                path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"  再取得完了: {filename}")

                df = data["derived_features"]
                bt = data["physical_features"]["bottom_type"]
                result = {
                    "status": "ok",
                    "filename": filename,
                    "bottom_type_value": bt.get("value"),
                    "bottom_best_match": (bt.get("best_match") or {}).get("name"),
                    "bottom_kisugo_score": df.get("bottom_kisugo_score"),
                    "terrain_summary": df.get("terrain_summary"),
                    "contour_distances_m": df.get("contour_distances_m", {}),
                }
            except Exception as e:
                import traceback
                traceback.print_exc()
                result = {"status": "error", "message": str(e)}

            self._send_refetch_result(result)

        threading.Thread(target=run, daemon=True).start()

    def _send_refetch_result(self, result: dict):
        if self.webview:
            js = "onRefetchComplete(" + json.dumps(result, ensure_ascii=False) + ")"
            self.webview.evaluate_javascript(js)

    def _handle_newspot(self, url: str):
        from urllib.parse import urlparse, parse_qs, unquote
        qs = parse_qs(urlparse(url).query)
        payload = json.loads(unquote(qs["data"][0]))
        name = str(payload["name"]).strip()
        lat  = float(payload["lat"])
        lon  = float(payload["lon"])
        if not name:
            self._send_newspot_result({"status": "error", "message": "スポット名が空です"}); return
        if not (-90 <= lat <= 90):
            self._send_newspot_result({"status": "error", "message": f"緯度が不正: {lat}"}); return
        if not (-180 <= lon <= 180):
            self._send_newspot_result({"status": "error", "message": f"経度が不正: {lon}"}); return
        self._run_newspot(name, lat, lon)

    def _run_newspot(self, name: str, lat: float, lon: float):
        """バックグラウンドスレッドで新規スポットを構築して JSON に書き出す。"""
        spots_dir = self.spots_dir

        def run():
            import sys; sys.path.insert(0, str(spots_dir.parent))
            try:
                from build_spots_complete import (
                    calculate_sea_bearing, reverse_geocode,
                    query_bottom_types, query_depth_contours,
                    summarize_depth_profile_from_contours,
                    derive_features_from_physical, build_spot_json, slugify_filename,
                )
            except ImportError as e:
                self._send_newspot_result({"status": "error", "message": f"インポート失敗: {e}"}); return

            def progress(step):
                if self.webview:
                    self.webview.evaluate_javascript(
                        "onNewSpotProgress(" + json.dumps({"step": step}, ensure_ascii=False) + ")")

            try:
                # ── Step 1: 海方向 ──
                progress(1)
                sea_bearing = calculate_sea_bearing(lat, lon)
                item = {
                    "name": name, "lat": lat, "lon": lon,
                    "sea_bearing_deg": sea_bearing,
                    "sea_bearing_source": "OSM Overpass coastline",
                    "sea_bearing_status": "auto" if sea_bearing is not None else "failed",
                }

                # ── Step 2: 住所 ──
                progress(2)
                reverse_geo = None
                try:
                    reverse_geo = reverse_geocode(lat, lon)
                except Exception as e:
                    print(f"  reverse geocode 失敗: {e}")

                # ── Step 3: 底質 ──
                progress(3)
                if sea_bearing is not None:
                    bottom_data = query_bottom_types(lat, lon, sea_bearing)
                else:
                    bottom_data = {"value": None, "matched_layers": [],
                                   "best_match": None, "status": "海方向未取得のためスキップ"}

                # ── Step 4: 水深 ──
                progress(4)
                depth_raw = query_depth_contours(lat, lon)
                depth_summary = summarize_depth_profile_from_contours(depth_raw["nearest_contours"])

                # ── Step 5: JSON 生成・書き出し ──
                progress(5)
                index = sum(1 for f in spots_dir.glob("*.json") if not f.name.startswith("_")) + 1
                spot = build_spot_json(
                    item=item, reverse_geo=reverse_geo,
                    bottom_data=bottom_data, depth_summary=depth_summary,
                    depth_raw=depth_raw, index=index,
                )
                spot["metadata"]["json_created_by"] = "verify_spots.py"

                filename = slugify_filename(name) + ".json"
                target = spots_dir / filename
                if target.exists():
                    filename = slugify_filename(name) + f"_{index:02d}.json"
                    target = spots_dir / filename
                spot["_filename"] = filename
                target.write_text(json.dumps(spot, ensure_ascii=False, indent=2), encoding="utf-8")
                print(f"新規登録完了: {filename}")

                marine_areas = _load_marine_areas(spots_dir)
                spot["_marine_area"] = _assign_marine_area(spot, marine_areas) if marine_areas else ""

                self._send_newspot_result({"status": "ok", "spot": spot})

            except Exception as e:
                import traceback; traceback.print_exc()
                self._send_newspot_result({"status": "error", "message": str(e)})

        threading.Thread(target=run, daemon=True).start()

    def _send_newspot_result(self, result: dict):
        if self.webview:
            js = "onNewSpotComplete(" + json.dumps(result, ensure_ascii=False) + ")"
            self.webview.evaluate_javascript(js)


def generate_html(spots: list, area_names: list = None) -> str:
    spots_json = json.dumps(spots, ensure_ascii=False)
    area_names_json = json.dumps(area_names or [], ensure_ascii=False)
    return (
        HTML_TEMPLATE
        .replace("__SPOTS_JSON__", spots_json)
        .replace("__AREA_NAMES_JSON__", area_names_json)
        .replace("__BEARING_OPTIONS__", _bearing_options_html())
        .replace("__BOTTOM_OPTIONS__", _bottom_options_html())
    )


def main():
    spots_dir = Path(__file__).parent / "spots"
    if not spots_dir.exists():
        print(f"エラー: spots ディレクトリが見つかりません: {spots_dir}")
        print("先に build_spots_complete.py を実行してください。")
        return

    spots = load_spots(spots_dir)
    if not spots:
        print("spots/*.json が見つかりませんでした。")
        return

    marine_areas = _load_marine_areas(spots_dir)
    area_names = list(marine_areas.keys())
    for spot in spots:
        spot["_marine_area"] = _assign_marine_area(spot, marine_areas) if marine_areas else ""

    html = generate_html(spots, area_names)
    out = Path.home() / "Documents" / "spots_viewer.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"生成完了: {out}")
    print(f"スポット数: {len(spots)}")

    # Pythonista (iOS): ui.WebView でインアプリ表示（デリゲートで保存対応）
    try:
        import ui  # type: ignore  # Pythonista built-in
        delegate = SpotDelegate(spots_dir)
        web = ui.WebView()
        web.delegate = delegate
        web.load_url("file://" + str(out))
        web.present("fullscreen")
    except ImportError:
        # デスクトップ環境: ブラウザで開く（読み取り専用）
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
