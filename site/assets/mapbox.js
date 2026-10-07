// Optional Mapbox GL view. Only used when config.js supplies a (URL-restricted, public) token.
const V = '3.29.0', BASE = `https://api.mapbox.com/mapbox-gl-js/v${V}/mapbox-gl`;
const RATING_COLOR = { '5': '#2e9e5b', '4': '#7bbf4a', '3': '#e0b93a', '2': '#e8863a', '1': '#d9534f', '0': '#a31d1d', AwaitingInspection: '#3b82c4' };
let map, loading;

function load() {
  return loading ??= new Promise((ok, fail) => {
    const l = Object.assign(document.createElement('link'), { rel: 'stylesheet', href: BASE + '.css' });
    const s = Object.assign(document.createElement('script'), { src: BASE + '.js', onload: ok, onerror: () => fail(new Error('Mapbox failed to load')) });
    document.head.append(l, s);
  });
}
const style = () => {
  const t = document.documentElement.dataset.theme;
  const dark = t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  return `mapbox://styles/mapbox/${dark ? 'dark' : 'light'}-v11`;
};

export async function showGl(el, token, v, onFail) {
  try {
    await load();
    mapboxgl.accessToken = token;
    const data = { type: 'FeatureCollection', features: v.venues.map(([lon, lat, t, r, b, name, pc]) => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] },
      properties: { name, pc, rating: v.ratings[r], type: v.types[t], borough: v.boroughs[b] } })) };
    const add = () => {
      map.addSource('v', { type: 'geojson', data });
      map.addLayer({ id: 'heat', type: 'heatmap', source: 'v', maxzoom: 12, paint: { 'heatmap-opacity': ['interpolate', ['linear'], ['zoom'], 10, .7, 12, 0], 'heatmap-intensity': .6 } });
      map.addLayer({ id: 'dots', type: 'circle', source: 'v', minzoom: 10, paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 1.5, 15, 6],
        'circle-color': ['match', ['get', 'rating'], ...Object.entries(RATING_COLOR).flat(), '#8a8a8a'],
        'circle-opacity': .85, 'circle-stroke-width': .5, 'circle-stroke-color': '#0006' } });
      map.on('click', 'dots', e => {
        const p = e.features[0].properties;
        new mapboxgl.Popup().setLngLat(e.lngLat).setHTML(`<b>${esc(p.name)}</b><br>${esc(p.type)}<br>${esc(p.borough)} · ${esc(p.pc)}<br>Rating: ${esc(p.rating)}`).addTo(map);
      });
      map.on('mouseenter', 'dots', () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'dots', () => { map.getCanvas().style.cursor = ''; });
    };
    if (map) { map.resize(); return; }
    map = new mapboxgl.Map({ container: el, style: style(), center: [-0.1276, 51.507], zoom: 9.6, attributionControl: true });
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'bottom-right');
    map.on('style.load', add);
    map.on('error', e => { if (e.error?.status === 401 || e.error?.status === 403) onFail('Mapbox rejected the token (check its URL restrictions).'); });
  } catch (err) { onFail(err.message); }
}
export function restyleGl() { if (map) map.setStyle(style()); }
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
