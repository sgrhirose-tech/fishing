/**
 * Leaflet マップ初期化ユーティリティ
 * OSM タイル使用（無料・APIキー不要）
 */

const OSM_TILE = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const OSM_ATTR = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

// マップ初期オプション: マウスドラッグ・スクロールホイール・1本指タッチを無効化
const _MAP_OPTS = {
  dragging:        false,
  touchZoom:       true,
  scrollWheelZoom: false,
  boxZoom:         false,
  doubleClickZoom: false,
};

// 2本指タッチ時のみ dragging を有効にするヘルパー
function _enableTwoFingerInteraction(map) {
  map.on('touchstart', function(e) {
    if (e.originalEvent.touches.length >= 2) map.dragging.enable();
  });
  map.on('touchend touchcancel', function(e) {
    if (e.originalEvent.touches.length < 2) map.dragging.disable();
  });
}

const AREA_COLORS = {
  'sagamibay':   '#2196F3',
  'miura':       '#4CAF50',
  'tokyobay':    '#FF9800',
  'uchibo':      '#9C27B0',
  'sotobo':      '#F44336',
  'kujukuri':    '#FF5722',
  'higashi-izu': '#E91E63',
  'minami-izu':  '#00BCD4',
  'nishi-izu':   '#8BC34A',
};

function _makeMarker(spot, map) {
  const color = AREA_COLORS[spot.area?.area_slug] || '#1a6b9e';
  const icon = L.divIcon({
    html: `<div style="background:${color};width:12px;height:12px;border-radius:50%;border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,.4)"></div>`,
    iconSize: [16, 16],
    iconAnchor: [8, 8],
    className: '',
  });

  const lat = spot.location?.latitude ?? spot.lat;
  const lon = spot.location?.longitude ?? spot.lon;
  const slug = spot.slug;
  const areaSlug = spot.area?.area_slug ?? '';
  const prefSlug = spot.area?.pref_slug ?? '';
  const citySlug = spot.area?.city_slug ?? '';

  const url = prefSlug && areaSlug && citySlug && slug
    ? `/${prefSlug}/${areaSlug}/${citySlug}/${slug}`
    : '#';

  const marker = L.marker([lat, lon], { icon }).addTo(map);
  marker.bindPopup(
    `<b>${spot.name}</b><br>` +
    `${spot.area?.area_name ?? ''} / ${spot.area?.city ?? ''}<br>` +
    `<a href="${url}">詳細・気象情報 →</a>`
  );
  return marker;
}

/**
 * スポット群に合わせてマップの中心・ズームを自動調整する
 */
function _fitSpots(map, spots, opts) {
  if (!spots || spots.length === 0) return;
  const latlngs = spots.map(s => [
    s.location?.latitude ?? s.lat,
    s.location?.longitude ?? s.lon,
  ]);
  map.fitBounds(L.latLngBounds(latlngs), opts || { padding: [40, 40], maxZoom: 14 });
}

/**
 * TOPページ: 全域マップ（現在地に自動センタリング）
 */
function initTopMap(elementId, spots) {
  const map = L.map(elementId, _MAP_OPTS).setView([35.20, 139.65], 9);
  L.tileLayer(OSM_TILE, { attribution: OSM_ATTR }).addTo(map);
  spots.forEach(spot => _makeMarker(spot, map));
  _fitSpots(map, spots);

  // 現在地に戻るコントロール
  const LocControl = L.Control.extend({
    options: { position: 'bottomright' },
    onAdd: function() {
      const btn = L.DomUtil.create('button', 'map-loc-btn');
      btn.textContent = '📍 現在地';
      btn.title = '現在地に移動';
      let userMarker = null;
      L.DomEvent.on(btn, 'click', function(e) {
        L.DomEvent.stopPropagation(e);
        if (!navigator.geolocation) return;
        btn.textContent = '📍 取得中…';
        navigator.geolocation.getCurrentPosition(function(pos) {
          const lat = pos.coords.latitude;
          const lon = pos.coords.longitude;
          map.setView([lat, lon], 11);
          if (userMarker) userMarker.remove();
          userMarker = L.circleMarker([lat, lon], {
            radius: 8, color: '#1a6b9e', fillColor: '#4a9fd4',
            fillOpacity: 0.9, weight: 2,
          }).addTo(map).bindPopup('現在地').openPopup();
          btn.textContent = '📍 現在地';
        }, function() {
          btn.textContent = '📍 現在地';
        });
      });
      return btn;
    },
  });
  new LocControl().addTo(map);

  // ページロード時に自動センタリング（失敗はサイレント）
  if (navigator.geolocation) {
    navigator.geolocation.getCurrentPosition(function(pos) {
      map.setView([pos.coords.latitude, pos.coords.longitude], 10);
    }, function() {});
  }

  _enableTwoFingerInteraction(map);
}

/**
 * 一覧ページ: 全スポットマップ
 */
function initSpotListMap(elementId, spots) {
  const map = L.map(elementId, _MAP_OPTS).setView([35.20, 139.65], 9);
  L.tileLayer(OSM_TILE, { attribution: OSM_ATTR }).addTo(map);
  spots.forEach(spot => _makeMarker(spot, map));
  _fitSpots(map, spots);
  _enableTwoFingerInteraction(map);
}

/**
 * 詳細ページ: 単一スポットマップ
 */
function initDetailMap(elementId, spot) {
  const lat = spot.location?.latitude ?? spot.lat;
  const lon = spot.location?.longitude ?? spot.lon;
  const map = L.map(elementId, _MAP_OPTS).setView([lat, lon], 15);
  L.tileLayer(OSM_TILE, { attribution: OSM_ATTR }).addTo(map);

  const icon = L.divIcon({
    html: '<div style="background:#1a6b9e;width:14px;height:14px;border-radius:50%;border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,.5)"></div>',
    iconSize: [18, 18],
    iconAnchor: [9, 9],
    className: '',
  });
  L.marker([lat, lon], { icon }).addTo(map)
    .bindPopup(`<b>${spot.name}</b>`).openPopup();
  _enableTwoFingerInteraction(map);
  return map;
}
