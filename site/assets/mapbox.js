// Optional Mapbox GL view. Only used when config.js supplies a (URL-restricted, public) token.
const V = '3.29.0', BASE = `https://api.mapbox.com/mapbox-gl-js/v${V}/mapbox-gl`;
const RATING_COLOR = { '5': '#2e9e5b', '4': '#7bbf4a', '3': '#e0b93a', '2': '#e8863a', '1': '#d9534f', '0': '#a31d1d', AwaitingInspection: '#3b82c4' };
let map, loading, ready = false, V_, last;

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

const TYPE_COLOR = ['#d9794b', '#3b82c4', '#7a5cc4'];
const EV_COLOR = { new: '#1a8a4a', removed: '#c0392b', rating_changed: '#d4a017' };

// Build GeoJSON for the venues the map controls currently select (M.idx), coloured per mode.
function venueData(M) {
  const dim = M.mode === 'awaiting';
  return { type: 'FeatureCollection', features: M.idx.map(i => {
    const [lon, lat, t, r, b, name, pc] = M.v.venues[i], rating = M.v.ratings[r];
    const c = M.mode === 'type' ? TYPE_COLOR[t % 3] : dim ? (rating === 'AwaitingInspection' ? RATING_COLOR.AwaitingInspection : '#6b6b6b') : (RATING_COLOR[rating] || '#8a8a8a');
    return { type: 'Feature', geometry: { type: 'Point', coordinates: [lon, lat] }, properties: { name, pc, rating, type: M.v.types[t], borough: M.v.boroughs[b], c, o: dim && rating !== 'AwaitingInspection' ? .25 : .9 } };
  }) };
}
function eventData(M) {
  return { type: 'FeatureCollection', features: (M.showEv ? M.evRaw || [] : []).map(e => ({ type: 'Feature',
    geometry: { type: 'Point', coordinates: [e.lon, e.lat] }, properties: { name: e.name, authority: e.authority, event: e.event, c: EV_COLOR[e.event], change: e.event === 'rating_changed' ? `${e.old_rating} → ${e.new_rating}` : '' } })) };
}

// Called by the app whenever filters, colour mode or the events toggle change.
export function glUpdate(M) {
  last = M;
  if (!map || !ready) return;
  map.getSource('v')?.setData(venueData(M));
  map.getSource('ev')?.setData(eventData(M));
  map.setLayoutProperty('heat', 'visibility', M.glHeat === false ? 'none' : 'visible');
}
export function glFit(M) {
  if (!map || !M.idx.length) return;
  let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
  const n = M.idx.length, trim = n > 200 ? Math.floor(n * .005) : 0;
  const lons = M.idx.map(i => M.v.venues[i][0]).sort((a, b) => a - b), lats = M.idx.map(i => M.v.venues[i][1]).sort((a, b) => a - b);
  map.fitBounds([[lons[trim], lats[trim]], [lons[n - 1 - trim], lats[n - 1 - trim]]], { padding: 60, duration: 900, maxZoom: 15 });
}
export function glHeat(on) { if (map && ready) map.setLayoutProperty('heat', 'visibility', on ? 'visible' : 'none'); }

export async function showGl(el, token, M, onFail) {
  try {
    await load();
    mapboxgl.accessToken = token;
    if (map) { map.resize(); glUpdate(M); return; }
    const add = () => {
      map.addSource('v', { type: 'geojson', data: venueData(M) });
      map.addSource('ev', { type: 'geojson', data: eventData(M) });
      map.addLayer({ id: 'heat', type: 'heatmap', source: 'v', maxzoom: 12, paint: {
        'heatmap-weight': 1,
        'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 8, .07, 12, .35],
        'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 8, 3, 12, 14],
        'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'], 0, 'rgba(0,0,0,0)', .2, 'rgba(59,130,196,.45)', .5, 'rgba(232,134,58,.7)', .8, 'rgba(240,200,80,.85)', 1, 'rgba(255,245,200,.95)'],
        'heatmap-opacity': ['interpolate', ['linear'], ['zoom'], 10, .85, 12, 0] } });
      map.addLayer({ id: 'dots', type: 'circle', source: 'v', minzoom: 10, paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 10, 1.5, 15, 6, 18, 11],
        'circle-color': ['get', 'c'], 'circle-opacity': ['get', 'o'], 'circle-stroke-width': .5, 'circle-stroke-color': 'rgba(0,0,0,.4)' } });
      map.addLayer({ id: 'rings', type: 'circle', source: 'ev', paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 8, 3, 15, 12], 'circle-color': 'rgba(0,0,0,0)',
        'circle-stroke-width': 2.5, 'circle-stroke-color': ['get', 'c'] } });
      const pop = (e, html) => new mapboxgl.Popup({ closeButton: true, maxWidth: '260px' }).setLngLat(e.lngLat).setHTML(html).addTo(map);
      map.on('click', 'dots', e => {
        const p = e.features[0].properties, q = encodeURIComponent(`${p.name} ${p.pc}`);
        pop(e, `<b>${esc(p.name)}</b><br>${esc(p.type)}<br>${esc(p.borough)} · ${esc(p.pc)}<br>Hygiene rating: <b>${esc(p.rating)}</b><br><a href="https://www.google.com/maps/search/?api=1&query=${q}" target="_blank" rel="noopener">Open in Google Maps ↗</a>`);
      });
      map.on('click', 'rings', e => {
        const p = e.features[0].properties;
        pop(e, `<b>${esc(p.name)}</b><br>${{ new: 'New on the register', removed: 'Removed from the register', rating_changed: 'Re-rated ' + esc(p.change) }[p.event]}<br>${esc(p.authority)}`);
      });
      // hovering a dot highlights it and shows the name
      const hover = new mapboxgl.Popup({ closeButton: false, closeOnClick: false, offset: 8, className: 'lp-hover' });
      map.on('mousemove', 'dots', e => { map.getCanvas().style.cursor = 'pointer'; hover.setLngLat(e.features[0].geometry.coordinates).setText(e.features[0].properties.name).addTo(map); });
      map.on('mouseleave', 'dots', () => { map.getCanvas().style.cursor = ''; hover.remove(); });
      map.on('mouseenter', 'rings', () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'rings', () => { map.getCanvas().style.cursor = ''; });
      ready = true;
      glUpdate(last || M);
    };
    map = new mapboxgl.Map({ container: el, style: style(), center: [-0.1276, 51.507], zoom: 9.6, attributionControl: true });
    map.addControl(new mapboxgl.NavigationControl({ showCompass: true }), 'bottom-right');
    map.addControl(new mapboxgl.GeolocateControl({ positionOptions: { enableHighAccuracy: true }, trackUserLocation: false, showUserLocation: true }), 'bottom-right');
    map.addControl(new mapboxgl.FullscreenControl(), 'bottom-right');
    map.on('style.load', () => { ready = false; try { add(); } catch (err) { console.error('GLADD', err.message); onFail('Map layers failed: ' + err.message); } });
    map.once('load', () => map.resize());
    new ResizeObserver(() => map.resize()).observe(el);
    map.on('error', e => { if (e.error?.message && !e.error.status) console.warn('Mapbox:', e.error.message); if (e.error?.status === 401 || e.error?.status === 403) onFail('Mapbox rejected the token (check its URL restrictions).'); });
  } catch (err) { onFail(err.message); }
}
export function restyleGl() { if (map) map.setStyle(style()); }
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
