// Optional Mapbox GL view. Only used when config.js supplies a (URL-restricted, public) token.
const V = '3.29.0', BASE = `https://api.mapbox.com/mapbox-gl-js/v${V}/mapbox-gl`;
const RATING_COLOR = { '5': '#2e9e5b', '4': '#7bbf4a', '3': '#e0b93a', '2': '#e8863a', '1': '#d9534f', '0': '#a31d1d', AwaitingInspection: '#3b82c4' };
let map, loading, ready = false, V_, last, layerMode = 'dots';

function load() {
  return loading ??= new Promise((ok, fail) => {
    const l = Object.assign(document.createElement('link'), { rel: 'stylesheet', href: BASE + '.css', integrity: 'sha384-XUbQaovfoSbaMso2Q1a1bLMGwU+1h7twi9V0vkuM6eOCZd0i52f6iAuxtHaP1nDO', crossOrigin: 'anonymous' });
    const s = Object.assign(document.createElement('script'), { src: BASE + '.js', integrity: 'sha384-zISDt21I0YwhTG3+pbVatm8HWRUzSPOGUn7O8zZhCmqD3PTTZC0OefBAT2TDkSJd', crossOrigin: 'anonymous', onload: ok, onerror: () => fail(new Error('Mapbox failed to load')) });
    document.head.append(l, s);
  });
}
const style = () => {
  const t = document.documentElement.dataset.theme;
  const dark = t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  return `mapbox://styles/mapbox/${dark ? 'dark' : 'light'}-v11`;
};

const TYPE_COLOR = ['#d9794b', '#3b82c4', '#7a5cc4', '#4aa89a'];
const EV_COLOR = { new: '#1a8a4a', removed: '#c0392b', rating_changed: '#d4a017' };

// Build GeoJSON for the venues the map controls currently select (M.idx), coloured per mode.
function venueData(M) {
  const dim = M.mode === 'awaiting';
  return { type: 'FeatureCollection', features: M.idx.map(i => {
    const [lon, lat, t, r, b, name, pc] = M.v.venues[i], rating = M.v.ratings[r];
    const c = M.mode === 'type' ? TYPE_COLOR[t % 4] : dim ? (rating === 'AwaitingInspection' ? RATING_COLOR.AwaitingInspection : '#6b6b6b') : (RATING_COLOR[rating] || '#8a8a8a');
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
}
export function glFit(M) {
  if (!map || !M.idx.length) return;
  let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
  const n = M.idx.length, trim = n > 200 ? Math.floor(n * .005) : 0;
  const lons = M.idx.map(i => M.v.venues[i][0]).sort((a, b) => a - b), lats = M.idx.map(i => M.v.venues[i][1]).sort((a, b) => a - b);
  map.fitBounds([[lons[trim], lats[trim]], [lons[n - 1 - trim], lats[n - 1 - trim]]], { padding: 60, duration: 900, maxZoom: 15 });
}
export function glHeat(on) { if (map && ready && layerMode === 'dots') map.setLayoutProperty('heat', 'visibility', on ? 'visible' : 'none'); }

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
      map.addSource('hex', { type: 'geojson', data: 'api/v1/hex.geojson' });
      map.addLayer({ id: 'hex3d', type: 'fill-extrusion', source: 'hex', layout: { visibility: 'none' }, paint: {
        'fill-extrusion-opacity': .82, 'fill-extrusion-height': 0, 'fill-extrusion-color': '#888' } });
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
      map.on('click', 'hex3d', e => {
        const p = e.features[0].properties;
        pop(e, `<b>${esc(p.borough)}</b> · ~0.7 km² area<br>${p.n} food and drink venues<br>Rated 5: ${p.five_pct ?? '–'}% · rated 0–2: ${p.low_pct ?? '–'}%<br>Awaiting inspection: ${p.awaiting} (${p.awaiting_pct}%)<br>Coffee-named: ${p.coffee} · pubs: ${p.pubs} · takeaways: ${p.takeaway_pct}%`);
      });
      map.on('mouseenter', 'hex3d', () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', 'hex3d', () => { map.getCanvas().style.cursor = ''; });
      ready = true;
      glLayer(layerMode);
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

// Analysis layers: H3 hexagons computed in DuckDB (spatial + h3 extensions), drawn as 3D columns or flat choropleths.
const RAMP = (prop, lo, hi, cols) => ['interpolate', ['linear'], ['coalesce', ['get', prop], lo], ...cols.flatMap((c, i) => [lo + (hi - lo) * i / (cols.length - 1), c])];
export const LAYERS = {
  dots: { label: 'Venues (dots and heatmap)' },
  density: { label: '3D: venue density', height: ['*', ['get', 'n'], 3], color: RAMP('n', 0, 400, ['#2b3a67', '#6a5acd', '#d9794b', '#ffd27a']), legend: ['Fewer venues', 'More venues'], tip: 'Column height and colour show how many food and drink businesses sit in each ~0.7 km² hexagon.' },
  five: { label: 'Hygiene: share rated 5', height: ['*', ['coalesce', ['get', 'five_pct'], 0], 6], color: RAMP('five_pct', 30, 90, ['#a31d1d', '#e8863a', '#e0b93a', '#7bbf4a', '#2e9e5b']), legend: ['30% rated 5', '90%'], tip: 'Red hexagons have the lowest share of top-rated venues; green the highest. Hexagons with fewer than 5 rated venues are blank.' },
  awaiting: { label: 'New openings: awaiting inspection', height: ['*', ['get', 'awaiting'], 40], color: RAMP('awaiting_pct', 0, 12, ['#2b3a67', '#3b82c4', '#7fd1f0', '#fff3b0']), legend: ['0% awaiting', '12%+'], tip: 'Venues registered but not yet inspected, a proxy for recent openings. Taller means more of them.' },
  coffee: { label: 'Coffee-named venues', height: ['*', ['get', 'coffee'], 90], color: RAMP('coffee', 0, 20, ['#3a2a20', '#7a4a2a', '#c27a3a', '#ffd27a']), legend: ['No coffee-named venues', '20+'], tip: 'Venues with coffee, espresso, roastery or similar in the name. Tall brown columns are coffee hotspots; flat dark ones are dense areas with none.' },
  specialty: { label: 'Specialty coffee and bakeries', height: ['*', ['get', 'specialty'], 160], color: RAMP('specialty', 0, 6, ['#2b2433', '#7a4a2a', '#d9954b', '#ffe3a0']), legend: ['None', '6+'], tip: 'Sites of known London specialty roasters and bakeries (Caravan, Monmouth, GAIL\'s and others). Name-matched, so a good guide rather than a full census.' },
  chains: { label: 'High-street chain share', height: ['*', ['get', 'chain_pct'], 14], color: RAMP('chain_pct', 0, 25, ['#2f6f5e', '#7bbf4a', '#e0b93a', '#c4452f']), legend: ['0% chains', '25%+'], tip: 'Share of venues that are Pret, Costa, Starbucks, Greggs, McDonald\'s and similar. Green hexagons have few of the ~20 tracked chains; other chains count as independent.' },
  takeaway: { label: 'Takeaway share', height: ['*', ['get', 'takeaway_pct'], 12], color: RAMP('takeaway_pct', 0, 60, ['#2b3a67', '#6a5acd', '#d9794b', '#ff6b4a']), legend: ['0% takeaways', '60%+'], tip: 'Share of venues that are takeaways or sandwich shops.' },
};
export function glLayer(mode) {
  layerMode = mode;
  if (!map || !ready) return;
  const L = LAYERS[mode], hexOn = mode !== 'dots';
  map.setLayoutProperty('hex3d', 'visibility', hexOn ? 'visible' : 'none');
  for (const id of ['dots', 'heat']) map.setLayoutProperty(id, 'visibility', hexOn ? 'none' : 'visible');
  if (hexOn) {
    map.setPaintProperty('hex3d', 'fill-extrusion-height', L.height);
    map.setPaintProperty('hex3d', 'fill-extrusion-color', L.color);
  }
  map.easeTo({ pitch: hexOn ? 58 : 0, bearing: hexOn ? -18 : 0, duration: 900 });
}
