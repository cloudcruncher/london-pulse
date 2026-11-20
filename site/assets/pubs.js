'use strict';
// Pubs tab. Loaded on demand like report.js. Uses globals from app.js: $, esc, fmt, km, areaLocate, toast, css.
// Data: api/v1/pubs.json (see docs/PUBS-SPEC.md). Rows are positional arrays described by `fields`.

const REPO = 'https://github.com/cloudcruncher/london-pulse';
const SHAPE = { pe: '◆', corp: '■', family: '▲', indie: '●', unknown: '○' };
const CAT_ORDER = ['pe', 'corp', 'family', 'indie', 'unknown'];
const EVIDENCE = {
  'osm:operator': 'OSM operator tag', 'osm:brand': 'OSM brand tag', 'osm:wikidata': 'OSM Wikidata link',
  'camden:licensee': 'Camden licensing register', name: 'Name match', override: 'Reviewed correction',
};
const FULL_PC = /^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$/;
const COS = Math.cos(51.5 * Math.PI / 180);
const PAGE = 40;

const S = { data: null, pubs: [], groups: {}, cats: {}, cat: '', cask: false, borough: '', q: '', loc: null, sort: 'indie', shown: PAGE, list: [], sel: null, v: { ox: 0, oy: 0, s: 1, s0: 1, w: 0, h: 0 }, ready: null };

const catColor = c => css('--pb-' + c) || '#888';
const catLabel = c => S.cats[c]?.label || c;
const distText = d => d < 1 ? `${Math.round(d * 1000 / 10) * 10} m` : `${d.toFixed(1)} km`;
const osmUrl = id => /^[nwr]\d+$/.test(id || '') ? `https://www.openstreetmap.org/${{ n: 'node', w: 'way', r: 'relation' }[id[0]]}/${id.slice(1)}` : null;
const safeHttp = u => { try { if (typeof u === 'string' && !/^[a-z][a-z0-9+.-]*:/i.test(u.trim()) && /^[\w-]+(\.[\w-]+)+([/?#].*)?$/.test(u.trim())) u = 'https://' + u.trim(); const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.href : null; } catch { return null; } };
const confText = p => p.category === 'unknown' ? '' : p.confidence === 'confirmed' ? 'confirmed by two sources' : 'likely (one source)';

export function initPubs() {
  S.ready = fetch('api/v1/pubs.json').then(r => { if (!r.ok) throw new Error(r.status); return r.json(); }).then(load).catch(() => {
    $('pb-status').textContent = 'The pubs data is not available yet. It is refreshed monthly; please check back soon.';
    $('pb-count').textContent = '';
    return false;
  });
  const redraw = () => { S.shown = PAGE; apply(!S.loc); };
  let t;
  $('pb-q').oninput = e => { clearTimeout(t); t = setTimeout(() => { S.q = e.target.value.trim().toLowerCase(); redraw(); }, 120); };
  $('pb-borough').onchange = e => { S.borough = e.target.value; redraw(); };
  $('pb-cask').onchange = e => { S.cask = e.target.checked; redraw(); };
  $('pb-sort').onchange = e => { S.sort = e.target.value; redraw(); };
  $('pb-go').onclick = () => findPostcode();
  $('pb-pc').onkeydown = e => { if (e.key === 'Enter') findPostcode(); };
  $('pb-geo').onclick = useGeo;
  $('pb-clear').onclick = () => { setLoc(null); $('pb-pc').value = ''; $('pb-status').textContent = ''; };
  $('pb-more').onclick = () => { S.shown += PAGE; drawList(); };
  $('pb-counts').onclick = e => { const b = e.target.closest('button[data-cat]'); if (!b) return; S.cat = S.cat === b.dataset.cat ? '' : b.dataset.cat; redraw(); };
  $('pb-list').onclick = e => { const b = e.target.closest('button[data-i]'); if (b) select(S.pubs[+b.dataset.i], true); };
  initMap();
  addEventListener('resize', () => { if (!$('v-pubs').hidden) resize(); });
  addEventListener('keydown', e => { if (e.key === 'Escape' && !$('pb-drawer').hidden) closeDetail(); });
}

export async function onShow(arg) {
  const ok = await S.ready;
  if (ok === false) return;
  resize();
  if (arg) { const p = S.pubs.find(x => x.osm === dec(arg)); if (p) select(p, true); }
}
export function redraw() { drawCounts(); drawMap(); }

function load(d) {
  S.data = d;
  S.cats = d.categories || {};
  S.groups = Object.fromEntries((d.groups || []).map(g => [g.id, g]));
  const f = d.fields;
  S.pubs = d.pubs.map((r, i) => { const p = {}; f.forEach((k, i) => { p[k] = r[i]; }); p.x = p.lon * COS; p.y = -p.lat; p.dist = null; p.i = i; p.nm = (p.name || '').toLowerCase(); return p; });
  const bs = [...new Set(S.pubs.map(p => p.borough).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  $('pb-borough').innerHTML = '<option value="">All boroughs</option>' + bs.map(b => `<option>${esc(b)}</option>`).join('');
  S.fit = true;
  const l = d.licence ? ` Licence: ${esc(d.licence)}.` : '';
  $('pb-foot').innerHTML = `Pub locations: © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a> (ODbL). Contains OGL data: FSA, London Borough of Camden. ${esc(d.method || '')}${l} Data as of ${esc(d.as_of || '')}. These are signals from open data, not verdicts: "No group link found" does not mean a pub is independent, and tenanted pubs can be owned by a group while run by a tenant. Something wrong? Open a pub and use "Suggest a correction".`;
  resize();
  apply(true);
  return true;
}

// ---------- filtering ----------
const passes = (p, skipCat) => (skipCat || !S.cat || p.category === S.cat) && (!S.cask || p.cask === 1) && (!S.borough || p.borough === S.borough)
  && (!S.q || p.nm.includes(S.q) || (p.postcode || '').toLowerCase().replace(/\s/g, '').startsWith(S.q.replace(/\s/g, '')));

function apply(refit) {
  const L = S.pubs.filter(p => passes(p));
  if (S.loc) for (const p of S.pubs) p.dist = km(S.loc.lon, S.loc.lat, p.lon, p.lat);
  const rank = p => p.category === 'indie' ? 0 : 1;
  if (S.loc) L.sort((a, b) => (S.sort === 'indie' ? rank(a) - rank(b) : 0) || a.dist - b.dist);
  else L.sort((a, b) => rank(a) - rank(b) || a.nm.localeCompare(b.nm));   // independents first, then A-Z
  S.list = L;
  if (refit) fit();
  drawCounts(); drawList(); drawMap();
}

function drawCounts() {
  const n = Object.fromEntries(CAT_ORDER.map(c => [c, 0]));
  for (const p of S.pubs) if (passes(p, true)) n[p.category] = (n[p.category] || 0) + 1;
  $('pb-counts').innerHTML = CAT_ORDER.filter(c => S.cats[c]).map(c => `<button type="button" class="pb-cat${S.cat === c ? ' on' : ''}" data-cat="${c}" aria-pressed="${S.cat === c}" style="--c:${catColor(c)}">
    <span class="pb-shape" aria-hidden="true">${SHAPE[c]}</span><b>${fmt(n[c])}</b><small>${esc(catLabel(c))}</small></button>`).join('');
}

function listItem(p) {
  const i = p.i;
  const cl = confText(p), lab = esc(catLabel(p.category)) + (p.category === 'indie' || cl.startsWith('likely') ? ' (likely)' : '');
  return `<li><button type="button" data-i="${i}" style="--c:${catColor(p.category)}">
    <span class="pb-shape" aria-hidden="true">${SHAPE[p.category] || '○'}</span>
    <span class="pb-li"><b>${esc(p.name || 'Unnamed pub')}</b><small>${lab}${p.cask === 1 ? ' · Cask ale' : ''}</small><small>${esc([p.borough, p.postcode].filter(Boolean).join(' · '))}</small></span>
    ${p.dist != null && S.loc ? `<span class="pb-dist">${distText(p.dist)}</span>` : ''}</button></li>`;
}

function drawList() {
  const n = S.list.length, show = S.list.slice(0, S.shown);
  $('pb-count').textContent = `${fmt(n)} pub${n === 1 ? '' : 's'}${S.loc ? ', nearest first' + (S.sort === 'indie' ? ', independents ahead' : '') : ''}`;
  $('pb-list').innerHTML = n ? show.map(listItem).join('') : '<li class="pb-empty">No pubs match these filters.</li>';
  $('pb-more').hidden = S.shown >= n;
  $('pb-more').textContent = `Show more (${fmt(n - S.shown)} left)`;
}

// ---------- location ----------
function setLoc(c) {
  S.loc = c; S.shown = PAGE;
  $('pb-sort').hidden = !c; $('pb-clear').hidden = !c;
  if (!c) for (const p of S.pubs) p.dist = null;
  apply(false);
  if (c) {   // frame the location and its 12 nearest listed pubs
    const v = S.v, pts = S.list.slice(0, 12).map(p => [p.x, p.y]).concat([[c.lon * COS, -c.lat]]);
    const xs = pts.map(q => q[0]), ys = pts.map(q => q[1]), x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    v.s = Math.min(v.w / Math.max(x1 - x0, .004), v.h / Math.max(y1 - y0, .004)) * .8;
    v.ox = v.w / 2 - (x0 + x1) / 2 * v.s; v.oy = v.h / 2 - (y0 + y1) / 2 * v.s; drawMap();
  }
}
async function findPostcode() {
  const q = $('pb-pc').value.trim(), st = $('pb-status');
  if (!q) { st.textContent = 'Enter a postcode or district first, for example E8 3QW or N16.'; return; }
  await S.ready;
  st.textContent = 'Finding that postcode…';
  const key = q.toUpperCase().replace(/\s+/g, '');
  let c = null, note = '';
  if (FULL_PC.test(key)) {
    try {
      const ac = new AbortController(), t = setTimeout(() => ac.abort(), 8000);
      const res = await fetch('https://api.postcodes.io/postcodes/' + encodeURIComponent(key), { signal: ac.signal });
      clearTimeout(t);
      if (res.status === 404) { st.textContent = `${q.toUpperCase()} is not a current postcode.`; return; }
      const r = res.ok ? (await res.json().catch(() => ({}))).result : null;
      if (r && r.latitude != null) {
        if (r.region !== 'London' || r.longitude < -0.6 || r.longitude > 0.4 || r.latitude < 51.2 || r.latitude > 51.75) { st.textContent = 'Outside London: this covers the 33 London local authorities only.'; return; }
        c = { lon: r.longitude, lat: r.latitude };
      }
    } catch { /* fall back to district below */ }
  }
  if (!c) { const a = await areaLocate(q).catch(() => null); if (a) { c = { lon: a.lon, lat: a.lat }; note = ' (approximate: middle of the district)'; } }
  if (!c) { st.textContent = `Couldn't place "${q}". Try a postcode such as E8 3QW, or a district such as E8 or N16.`; return; }
  c.label = q.toUpperCase();
  setLoc(c);
  const first = S.list[0];
  st.textContent = `Pubs near ${c.label}${note}, ${S.sort === 'indie' ? 'independents first' : 'nearest first'}.${first ? '' : ' No pubs match the current filters.'} Your postcode is sent to postcodes.io to find its location.`;
}
function useGeo() {
  const st = $('pb-status');
  if (!navigator.geolocation) { st.textContent = 'Location is not available in this browser. Enter a postcode instead.'; return; }
  st.textContent = 'Asking for your location…';
  navigator.geolocation.getCurrentPosition(async pos => {
    await S.ready;
    const { longitude: lon, latitude: lat } = pos.coords;
    if (lon < -0.6 || lon > 0.4 || lat < 51.2 || lat > 51.75) { st.textContent = 'You appear to be outside London. Enter a London postcode instead.'; return; }
    setLoc({ lon, lat, label: 'you' });
    st.textContent = 'Pubs near you. Your location stays in your browser.';
  }, () => { st.textContent = 'Could not get your location. Enter a postcode instead.'; }, { timeout: 10000, maximumAge: 60000 });
}

// ---------- detail ----------
function modelText(g) {
  if (!g) return '';
  const o = esc(g.name);
  if (g.model === 'tenanted') return `Owned by ${o}; run by a tenant publican, usually tied to the group's beer.`;
  if (g.model === 'managed') return `Run directly by ${o}.`;
  return `${o} runs some pubs directly and leases others to tenants; open data does not show which this is.`;
}
function select(p, open) {
  S.sel = p;
  const v = S.v, sx = p.x * v.s + v.ox, sy = p.y * v.s + v.oy;
  if (open && (sx < 20 || sy < 20 || sx > v.w - 20 || sy > v.h - 20)) { v.ox += v.w / 2 - sx; v.oy += v.h / 2 - sy; }
  drawMap();
  if (!open) return;
  const g = S.groups[p.group], cl = confText(p), osm = osmUrl(p.osm);
  const chips = [];
  if (osm) chips.push(`<a class="pb-chip" href="${osm}" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>`);
  if (p.fhrsid) chips.push(`<a class="pb-chip" href="https://ratings.food.gov.uk/business/${encodeURIComponent(p.fhrsid)}" target="_blank" rel="noopener noreferrer">FSA hygiene record</a>`);
  for (const e of p.evidence || []) {
    const lab = EVIDENCE[e] || e;
    chips.push(e.startsWith('camden:') ? `<a class="pb-chip" href="https://opendata.camden.gov.uk/d/rv9z-rsui" target="_blank" rel="noopener noreferrer">${esc(lab)}</a>` : `<span class="pb-chip">${esc(lab)}</span>`);
  }
  const web = safeHttp(p.website);
  let status;
  if (p.category === 'unknown') status = '<p><b>No group link found.</b> That is not a claim that this pub is independent: it only means none of our open sources link it to a group.</p>';
  else if (p.category === 'indie') status = `<p><b>Likely independent.</b> ${g ? esc(g.note || '') : 'No big-group link found, and the operator or licensee looks like a small independent business.'}</p>`;
  else status = `<p><b>${esc(catLabel(p.category))}${cl ? ` (${cl})` : ''}.</b></p>`;
  const cask = p.cask === 1 ? 'Cask ale: listed on OpenStreetMap.' : p.cask === 0 ? 'Cask ale: OpenStreetMap says none.' : 'Cask ale: not known.';
  const craft = p.craft === 1 ? 'Craft beer: listed on OpenStreetMap.' : 'Craft beer: not known.';
  const body = [
    `Pub: ${p.name}`, `OSM id: ${p.osm || ''}`, `FSA id: ${p.fhrsid ?? ''}`, `Borough: ${p.borough || ''}`, '',
    'Please fill in what you know (and add a source link):',
    `- Owner / group: ${g ? g.name : ''}`, '- Independent? (yes / no): ', '- Cask ale lines: ', '- Keg lines: ', '- Source link: ', '',
    `Currently shown as: ${catLabel(p.category)}${cl ? ' (' + cl + ')' : ''}`,
  ].join('\n');
  const fix = `${REPO}/issues/new?title=${encodeURIComponent('Pub ownership: ' + p.name)}&body=${encodeURIComponent(body)}`;
  const d = $('pb-drawer');
  d.hidden = false;
  d.innerHTML = `<button class="icon-btn x" id="pb-x" aria-label="Close">✕</button>
    <h2>${esc(p.name || 'Unnamed pub')}</h2>
    <p class="pb-badge" style="--c:${catColor(p.category)}"><span class="pb-shape" aria-hidden="true">${SHAPE[p.category] || '○'}</span>${esc(catLabel(p.category))}${cl ? ` · ${cl}` : ''}</p>
    <p class="mutedtxt">${esc([p.borough, p.postcode].filter(Boolean).join(' · '))}${p.dist != null && S.loc ? ' · ' + distText(p.dist) + ' away' : ''}</p>
    ${status}
    ${g ? `<h3>Owner</h3><p><b>${esc(g.owner || g.name)}</b> (${esc(g.name)})</p><p>${modelText(g)}</p><p>${esc(g.note || '')} ${safeHttp(g.src) ? `<a href="${esc(safeHttp(g.src))}" target="_blank" rel="noopener noreferrer">Source</a>` : ''}</p>` : ''}
    <h3>Beer</h3><p>${cask}<br>${craft}<br>Keg lines: no open data yet.</p><p class="mutedtxt">Is a beer from an independent brewery? Check it on SIBA's <a href="https://indiebeer.uk/index.php#beerChecker" target="_blank" rel="noopener noreferrer">Indie Beer brewery checker</a>.</p>
    <h3>Evidence</h3><div class="pb-chips">${chips.join('') || '<span class="mutedtxt">None recorded.</span>'}</div>
    ${web ? `<p><a href="${esc(web)}" target="_blank" rel="noopener noreferrer">Pub website</a></p>` : ''}
    <p style="margin-top:18px"><a class="btn primary" href="${esc(fix)}" target="_blank" rel="noopener noreferrer">Suggest a correction</a></p>
    <p class="note">Opens a pre-filled GitHub issue; nothing is sent until you submit it.</p>`;
  $('pb-x').onclick = closeDetail;
  $('pb-x').focus();
}
function closeDetail() { $('pb-drawer').hidden = true; S.sel = null; drawMap(); }

// ---------- map ----------
const cv = () => $('pb-map');
function resize() {
  const c = cv(), r = c.getBoundingClientRect();
  if (!r.width) return;
  const dpr = devicePixelRatio || 1, first = !S.v.w;
  c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr);
  S.v.w = r.width; S.v.h = r.height;
  if (first || S.fit) fit(); else drawMap();
}
function fit() {
  const v = S.v;
  if (!v.w) return;
  const L = S.list.length ? S.list : S.pubs;
  if (!L.length) return;
  let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
  for (const p of L) { if (p.x < x0) x0 = p.x; if (p.x > x1) x1 = p.x; if (p.y < y0) y0 = p.y; if (p.y > y1) y1 = p.y; }
  const dx = Math.max(x1 - x0, .004), dy = Math.max(y1 - y0, .004);
  v.s0 = Math.min(v.w / dx, v.h / dy) * .88;
  v.s = v.s0; v.ox = v.w / 2 - (x0 + x1) / 2 * v.s; v.oy = v.h / 2 - (y0 + y1) / 2 * v.s;
  S.fit = false;
}
let raf = 0;
function drawMap() {
  if (raf) return;
  raf = requestAnimationFrame(() => { raf = 0; paint(); });
}
function shapePath(ctx, c, x, y, r) {
  if (c === 'pe') { ctx.moveTo(x, y - r * 1.3); ctx.lineTo(x + r * 1.1, y); ctx.lineTo(x, y + r * 1.3); ctx.lineTo(x - r * 1.1, y); ctx.closePath(); }
  else if (c === 'corp') ctx.rect(x - r, y - r, r * 2, r * 2);
  else if (c === 'family') { ctx.moveTo(x, y - r * 1.2); ctx.lineTo(x + r * 1.1, y + r * .9); ctx.lineTo(x - r * 1.1, y + r * .9); ctx.closePath(); }
  else { ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, 7); }
}
function paint() {
  const c = cv(), v = S.v;
  if (!v.w) return;
  const ctx = c.getContext('2d'), dpr = devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, v.w, v.h);
  const zoom = Math.log2(v.s / v.s0), r = Math.max(3, Math.min(8, 3.2 + zoom * .9));
  const by = {}; for (const k of CAT_ORDER) by[k] = [];
  for (const p of S.list) { const x = p.x * v.s + v.ox, y = p.y * v.s + v.oy; if (x > -10 && y > -10 && x < v.w + 10 && y < v.h + 10) (by[p.category] || by.unknown).push(x, y); }
  for (const k of ['unknown', 'corp', 'pe', 'family', 'indie']) {
    const a = by[k]; if (!a.length) continue;
    const col = catColor(k);
    ctx.beginPath();
    for (let i = 0; i < a.length; i += 2) shapePath(ctx, k, a[i], a[i + 1], k === 'indie' ? r * 1.1 : r);
    ctx.globalAlpha = k === 'unknown' ? .75 : .92;
    if (k === 'unknown') { ctx.strokeStyle = col; ctx.lineWidth = 1.6; ctx.stroke(); }
    else { ctx.fillStyle = col; ctx.fill(); ctx.strokeStyle = css('--surface'); ctx.lineWidth = .8; ctx.stroke(); }
  }
  ctx.globalAlpha = 1;
  if (S.loc) {
    const x = S.loc.lon * COS * v.s + v.ox, y = -S.loc.lat * v.s + v.oy;
    ctx.strokeStyle = css('--ink'); ctx.fillStyle = css('--accent'); ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(x, y, 7, 0, 7); ctx.fill(); ctx.stroke();
  }
  if (S.sel) {
    const x = S.sel.x * v.s + v.ox, y = S.sel.y * v.s + v.oy;
    ctx.strokeStyle = css('--ink'); ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(x, y, r + 6, 0, 7); ctx.stroke();
  }
}
function nearestPub(mx, my) {
  const v = S.v; let best = null, bd = 18 * 18;
  for (const p of S.list) { const dx = p.x * v.s + v.ox - mx, dy = p.y * v.s + v.oy - my, d = dx * dx + dy * dy; if (d < bd) { bd = d; best = p; } }
  return best;
}
function zoomAt(cx, cy, f) {
  const v = S.v, s = Math.max(v.s0 * .6, Math.min(v.s0 * 400, v.s * f)), k = s / v.s;
  v.ox = cx - (cx - v.ox) * k; v.oy = cy - (cy - v.oy) * k; v.s = s; drawMap();
}
function initMap() {
  const c = cv(), ptrs = new Map(), tip = $('pb-tip');
  let moved = 0, pinch = 0;
  c.addEventListener('pointerdown', e => { c.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, [e.offsetX, e.offsetY]); moved = 0; pinch = 0; });
  c.addEventListener('pointermove', e => {
    const prev = ptrs.get(e.pointerId);
    if (!prev) {
      const p = e.pointerType === 'mouse' ? nearestPub(e.offsetX, e.offsetY) : null;
      if (p) { tip.hidden = false; tip.innerHTML = `<b>${esc(p.name)}</b>${esc(catLabel(p.category))}`; tip.style.left = Math.min(e.offsetX + 12, S.v.w - 200) + 'px'; tip.style.top = (e.offsetY + 12) + 'px'; c.style.cursor = 'pointer'; }
      else { tip.hidden = true; c.style.cursor = ''; }
      return;
    }
    tip.hidden = true;
    if (ptrs.size === 1) { S.v.ox += e.offsetX - prev[0]; S.v.oy += e.offsetY - prev[1]; moved += Math.abs(e.offsetX - prev[0]) + Math.abs(e.offsetY - prev[1]); ptrs.set(e.pointerId, [e.offsetX, e.offsetY]); drawMap(); }
    else if (ptrs.size === 2) {
      ptrs.set(e.pointerId, [e.offsetX, e.offsetY]);
      const [a, b] = [...ptrs.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
      if (pinch) zoomAt((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, d / pinch);
      pinch = d; moved = 99;
    }
  });
  const end = e => {
    const had = ptrs.delete(e.pointerId);
    if (had && !ptrs.size && moved < 6 && e.type === 'pointerup') { const p = nearestPub(e.offsetX, e.offsetY); if (p) select(p, true); }
    if (ptrs.size < 2) pinch = 0;
  };
  c.addEventListener('pointerup', end); c.addEventListener('pointercancel', end);
  c.addEventListener('pointerleave', () => { tip.hidden = true; });
  c.addEventListener('wheel', e => { e.preventDefault(); zoomAt(e.offsetX, e.offsetY, Math.exp(-e.deltaY * .0015)); }, { passive: false });
  $('pb-zin').onclick = () => zoomAt(S.v.w / 2, S.v.h / 2, 1.6);
  $('pb-zout').onclick = () => zoomAt(S.v.w / 2, S.v.h / 2, 1 / 1.6);
  $('pb-zreset').onclick = () => { fit(); drawMap(); };
}
