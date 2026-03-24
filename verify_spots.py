"""
verify_spots.py — スポットデータ確認ツール

spots/*.json を読み込み、Leaflet.js マップ付きの HTML ビューワーを生成する。
生成した HTML を ~/Documents/spots_viewer.html に保存し、ブラウザで開く。
"""
from pathlib import Path
import json
import math
import webbrowser


def load_spots(spots_dir: Path):
    spots = []
    for f in sorted(spots_dir.glob("*.json")):
        try:
            spots.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"読み込みエラー {f.name}: {e}")
    return spots


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
#panel td { padding: 5px 10px; border-bottom: 1px solid #e0e0e0; vertical-align: top; }
#panel td:first-child { width: 38%; color: #555; font-weight: 600; white-space: nowrap; }
.missing { color: #d32f2f; font-weight: bold; }
.ok { color: #1a6e1a; }
.section-header td {
  background: #2c6fad; color: white; font-weight: bold; padding: 5px 10px;
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

<div id="map"></div>

<div id="panel">
  <table id="info-table"></table>
</div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const SPOTS = __SPOTS_JSON__;

let currentIndex = 0;
const map = L.map('map');
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '© OpenStreetMap contributors',
  maxZoom: 18,
}).addTo(map);

// 可変レイヤーグループ（スポット切り替え時にクリア）
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

function val(v, unit) {
  if (v === null || v === undefined) return '<span class="missing">データなし</span>';
  return '<span class="ok">' + v + (unit ? ' ' + unit : '') + '</span>';
}

function showSpot(idx) {
  const s = SPOTS[idx];
  const lat = s.location.latitude;
  const lon = s.location.longitude;

  // ナビ更新
  document.getElementById('spot-name').textContent = s.name;
  document.getElementById('nav-count').textContent = (idx+1) + ' / ' + SPOTS.length;
  document.getElementById('btn-prev').disabled = (idx === 0);
  document.getElementById('btn-next').disabled = (idx === SPOTS.length - 1);

  // マップ更新
  spotLayer.clearLayers();
  map.setView([lat, lon], 14);

  L.marker([lat, lon]).addTo(spotLayer).bindPopup(s.name);

  const bearing = s.physical_features && s.physical_features.sea_bearing_deg;
  if (bearing !== null && bearing !== undefined) {
    const tip = bearing2latlon(lat, lon, bearing, 400);
    L.polyline([[lat, lon], tip], {color: '#1565c0', weight: 4}).addTo(spotLayer);
    L.circleMarker(tip, {radius: 7, color: '#1565c0', fillColor: '#1565c0', fillOpacity: 1}).addTo(spotLayer)
      .bindTooltip('海方向 ' + Math.round(bearing) + '°');
  } else {
    L.marker([lat, lon], {
      icon: L.divIcon({className: '', html: '<div style="background:rgba(255,255,255,0.85);padding:3px 6px;border:1px solid #d32f2f;color:#d32f2f;font-size:12px;border-radius:4px;white-space:nowrap">海方向データなし</div>', iconAnchor:[0,0]})
    }).addTo(spotLayer);
  }

  // 情報パネル更新
  const pf = s.physical_features || {};
  const bt = pf.bottom_type || {};
  const df = s.derived_features || {};
  const cd = df.contour_distances_m || {};
  const area = s.area || {};

  const rows = [
    ['section', '基本情報'],
    ['都道府県', val(area.prefecture)],
    ['市区町村', val(area.city)],
    ['緯度', val(lat)],
    ['経度', val(lon)],
    ['section', '海・地形'],
    ['海の方向', val(bearing !== null && bearing !== undefined ? Math.round(bearing) + '°' : null)],
    ['底質タイプ', val(bt.value)],
    ['最適マッチ', val(bt.best_match)],
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

if (SPOTS.length > 0) {
  showSpot(0);
} else {
  document.getElementById('spot-name').textContent = 'スポットデータなし';
}
</script>
</body>
</html>
"""


def generate_html(spots: list) -> str:
    spots_json = json.dumps(spots, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__SPOTS_JSON__", spots_json)


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

    # Pythonista (iOS) では webview モジュールでインアプリ表示する
    try:
        import webview  # type: ignore  # Pythonista built-in
        webview.open("file://" + str(out))
    except ImportError:
        # デスクトップ環境のフォールバック
        webbrowser.open(out.as_uri())


if __name__ == "__main__":
    main()
