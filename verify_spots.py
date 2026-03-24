"""
verify_spots.py — スポットデータ確認・修正ツール

spots/*.json を読み込み、Leaflet.js マップ付きの HTML ビューワーを生成する。
Pythonista (iOS) では ui.WebView で開き、編集内容を spots/*.json に保存できる。
デスクトップでは webbrowser で開く（読み取り専用）。
"""
from pathlib import Path
import json
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
const BEARING_OPTIONS_HTML = `__BEARING_OPTIONS__`;
const BOTTOM_OPTIONS_HTML = `__BOTTOM_OPTIONS__`;

let currentIndex = 0;
let pendingBearing = null;   // 変更中の bearing 値
let pendingBottom  = null;   // 変更中の bottom_type.value

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
  L.marker([lat, lon]).addTo(spotLayer).bindPopup(SPOTS[currentIndex].name);
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
  const lat = s.location.latitude;
  const lon = s.location.longitude;
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
  const s = SPOTS[currentIndex];
  pendingBearing = Number(sel.value);
  updateBearingArrow(s.location.latitude, s.location.longitude, pendingBearing);
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
  if (Object.keys(changes).length === 0) return;
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
  pendingBearing = null;
  pendingBottom  = null;
  document.getElementById('save-msg').textContent = '✓ 保存しました';
  setTimeout(() => {
    document.getElementById('save-bar').style.display = 'none';
    document.getElementById('save-msg').textContent = '';
  }, 2000);
}

function val(v, unit) {
  if (v === null || v === undefined) return '<span class="missing">データなし</span>';
  return '<span class="ok">' + v + (unit ? ' ' + unit : '') + '</span>';
}

function showSpot(idx) {
  pendingBearing = null;
  pendingBottom  = null;
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
  document.getElementById('nav-count').textContent = (idx+1) + ' / ' + SPOTS.length;
  document.getElementById('btn-prev').disabled = (idx === 0);
  document.getElementById('btn-next').disabled = (idx === SPOTS.length - 1);

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
    ['緯度', val(lat)],
    ['経度', val(lon)],
    ['section', '海・地形'],
    ['海の方向', bearingCell],
    ['底質タイプ', bottomCell],
    ['最適マッチ', val(bmName)],
    ['底質スコア', val(df.bottom_kisugo_score)],
    ['地形メモ', val(df.terrain_summary)],
    ['section', '等深線距離'],
    ['20m', val(cd['20m'], 'm')],
    ['50m', val(cd['50m'], 'm')],
    ['100m', val(cd['100m'], 'm')],
    ['150m', val(cd['150m'], 'm')],
    ['200m', val(cd['200m'], 'm')],
  ];

  let html = '';
  for (const r of rows) {
    if (r[0] === 'section') {
      html += '<tr class="section-header"><td colspan="2">' + r[1] + '</td></tr>';
    } else {
      html += '<tr><td>' + r[0] + '</td><td>' + r[1] + '</td></tr>';
    }
  }
  document.getElementById('info-table').innerHTML = html;
}

function navigate(delta) {
  const next = currentIndex + delta;
  if (next < 0 || next >= SPOTS.length) return;
  currentIndex = next;
  showSpot(currentIndex);
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
});
document.getElementById('info-table').addEventListener('change', function(e) {
  if (e.target.id === 'bottom-select') onBottomChange();
});

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

    def webview_should_start_load(self, webview, url, nav_type):
        if url.startswith("pythonista://save"):
            self._handle_save(url)
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
            if "bottom_type" not in data["physical_features"]:
                data["physical_features"]["bottom_type"] = {}
            data["physical_features"]["bottom_type"]["value"] = v if v != "" else None

        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"保存完了: {filename}")


def generate_html(spots: list) -> str:
    spots_json = json.dumps(spots, ensure_ascii=False)
    return (
        HTML_TEMPLATE
        .replace("__SPOTS_JSON__", spots_json)
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

    html = generate_html(spots)
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
