/**
 * Leaflet マップ初期化ユーティリティ
 * OSM タイル使用（無料・APIキー不要）
 */

const OSM_TILE = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const OSM_ATTR = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

const AREA_COLORS = {
  'sagamibay': '#2196F3',
  'miura':     '#4CAF50',
  'tokyobay':  '#FF9800',
  'uchibo':    '#9C27B0',
  'sotobo':    '#F44336',
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
 * TOPページ: 全域マップ（26か所のマーカー）
 */
function initTopMap(elementId, spots) {
  const map = L.map(elementId).setView([35.20, 139.65], 9);
  L.tileLayer(OSM_TILE, { attribution: OSM_ATTR }).addTo(map);
  spots.forEach(spot => _makeMarker(spot, map));
}

/**
 * 一覧ページ: 全スポットマップ
 */
function initSpotListMap(elementId, spots) {
  const map = L.map(elementId).setView([35.20, 139.65], 9);
  L.tileLayer(OSM_TILE, { attribution: OSM_ATTR }).addTo(map);
  spots.forEach(spot => _makeMarker(spot, map));
}

/**
 * 詳細ページ: 単一スポットマップ
 */
function initDetailMap(elementId, spot) {
  const lat = spot.location?.latitude ?? spot.lat;
  const lon = spot.location?.longitude ?? spot.lon;
  const map = L.map(elementId).setView([lat, lon], 15);
  L.tileLayer(OSM_TILE, { attribution: OSM_ATTR }).addTo(map);

  const icon = L.divIcon({
    html: '<div style="background:#1a6b9e;width:14px;height:14px;border-radius:50%;border:2px solid white;box-shadow:0 1px 4px rgba(0,0,0,.5)"></div>',
    iconSize: [18, 18],
    iconAnchor: [9, 9],
    className: '',
  });
  L.marker([lat, lon], { icon }).addTo(map)
    .bindPopup(`<b>${spot.name}</b>`).openPopup();
  return map;
}
