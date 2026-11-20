'use strict';
// Pubs tab. Loaded on demand like report.js. Uses globals from app.js: $, esc, fmt, km, dec, areaLocate, toast, css.
// Data: api/v1/pubs.json (required) and api/v1/pubs-insights.json (optional; see docs/PUBS-SPEC.md, sections B and C).
// Two modes: Explore (map, list, group pages) and Insights (charts, borough compare). The whole view lives in the URL:
//   #pubs?mode=insights&cat=indie&b=Hackney&g=stonegate&q=...&cask=1&cmp=Hackney|Camden&p=n123
// A postcode is never written to the URL (privacy: it would be sent to postcodes.io by whoever opens the link).
// An old link that carries pc= only pre-fills the box; nothing is looked up until the reader asks.

const REPO = 'https://github.com/cloudcruncher/london-pulse';
const SHAPE = { pe: '◆', corp: '■', family: '▲', indie: '●', unknown: '○' };
const CAT_ORDER = ['pe', 'corp', 'family', 'indie', 'unknown'];
const GROUPISH = ['pe', 'corp', 'family'];
const RATINGS = ['5', '4', '3', '2', '1', '0'];
// Core caveats, shown even when pubs-insights.json is missing (mirrors NOTES in src/london_pulse/pubs_insights.py)
const NOTES = [
  "'No group link found' is not the same as independent: most of those pubs simply have no open record of their owner.",
  'Groups are easier to identify than independents, so shares of traced pubs overstate groups; shares of all pubs are the floor.',
  "Independent labels are 'likely': no group link from any source plus a positive sign of independence.",
  'Most independents are found in Camden, the only borough that publishes its licence register, so borough and owner-type comparisons partly reflect where evidence exists rather than where independents are.',
  'Hygiene ratings measure food safety on the day of inspection, not the quality of the pub or its beer.',
  'Deprivation and income describe the small area a pub stands in, not its customers.',
  'Cask figures count only pubs whose OpenStreetMap entry says whether they serve real ale.',
];
// category names as they read mid-sentence ("58% of likely independent pubs", "5 corporate group")
const CAT_SENT = { pe: 'PE-owned or investor-owned', corp: 'corporate group', family: 'family brewer', indie: 'likely independent', unknown: 'untraced' };
const catSent = c => CAT_SENT[c] || catSent(c);
const regNames = () => (S.openReg && S.openReg.length ? S.openReg : ['Camden']).map(bName);
const camdenNote = () => { const r = regNames(); return r.length === 1 ? `Most likely independents are in ${esc(r[0])}, the only borough with an open licence register, so this partly compares ${esc(r[0])} with the rest of London.` : `Most likely independents are in ${esc(r.join(' and '))}, the only boroughs with open licence registers, so this partly compares them with the rest of London.`; };
const EVIDENCE = {
  'osm:operator': 'OSM operator tag', 'osm:brand': 'OSM brand tag', 'osm:wikidata': 'OSM Wikidata link', 'osm:website': 'OSM website (group domain)',
  'osm:brewery': 'OSM brewery tag (tied house)', 'osm:wikidata-owner': 'OSM brand/operator tag, owner resolved via Wikidata',
  'wikidata:owner': "Wikidata (pub's own item)", 'fsa:name': 'FSA business name',
  'camden:licensee': 'Camden licensing register', name: 'Name match', override: 'Reviewed correction',
};
const SHORT_B = { 'City of London Corporation': 'City of London', 'Richmond-Upon-Thames': 'Richmond upon Thames', 'Kingston-Upon-Thames': 'Kingston upon Thames' };
const FULL_PC = /^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$/;
const COS = Math.cos(51.5 * Math.PI / 180);
const PAGE = 40;
const CLUSTER_PX = 38;

const S = {
  data: null, pubs: [], groups: {}, cats: {}, boroughs: [], ins: null, insFile: false,
  mode: 'explore', cat: '', cask: false, borough: '', g: '', q: '', qRaw: '', loc: null, sort: 'indie',
  cmp: [], bsort: 'total', bview: 'count', lsort: ['n', -1],
  shown: PAGE, list: [], pass: null, ver: 0, sel: null, hits: [], cl: null, clKey: '',
  v: { ox: 0, oy: 0, s: 1, s0: 1, w: 0, h: 0 }, ready: null, insReady: null, lastHash: null, applying: false, insW: 0,
};

const catLabel = c => S.cats[c]?.label || c;
const catName = c => c === 'indie' ? 'Likely independent' : catLabel(c);   // framing: independent is always "likely"
const bName = b => SHORT_B[b] || b;
const pct = (x, d = 0) => x == null || !isFinite(x) ? '–' : `${(x * 100).toFixed(d)}%`;
const plural = (n, w, ws = w + 's') => `${fmt(n)} ${n === 1 ? w : ws}`;
const distText = d => d < 1 ? `${Math.round(d * 1000 / 10) * 10} m` : `${d.toFixed(1)} km`;
const osmUrl = id => /^[nwr]\d+$/.test(id || '') ? `https://www.openstreetmap.org/${{ n: 'node', w: 'way', r: 'relation' }[id[0]]}/${id.slice(1)}` : null;
const safeHttp = u => { try { if (typeof u === 'string' && !/^[a-z][a-z0-9+.-]*:/i.test(u.trim()) && /^[\w-]+(\.[\w-]+)+([/?#].*)?$/.test(u.trim())) u = 'https://' + u.trim(); const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.href : null; } catch { return null; } };
const confText = p => p.category === 'unknown' ? '' : p.confidence === 'confirmed' ? 'confirmed by two sources' : 'likely (one source)';
const ratingNum = r => /^[0-5]$/.test(String(r ?? '')) ? +r : null;
const hhiNorm = h => h == null ? null : h > 1 ? h / 10000 : h;
const dateText = s => { const d = new Date(s); return isNaN(d) ? '' : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }); };
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

// ======================================================================= lifecycle
export function initPubs() {
  S.ready = fetch('api/v1/pubs.json').then(r => { if (!r.ok) throw new Error(r.status); return r.json(); }).then(load).catch(() => {
    $('pb-status').textContent = 'The pubs data is not available yet. It is refreshed monthly; please check back soon.';
    $('pb-count').textContent = '';
    $('pb-counts').innerHTML = ''; $('pb-maphint').hidden = true;
    $('pb-insights').innerHTML = '<p class="pb-emptybox">The pubs data is not available yet, so there is nothing to analyse. Please check back soon.</p>';
    return false;
  });
  S.insReady = fetch('api/v1/pubs-insights.json').then(r => r.ok ? r.json() : null).then(j => j && j.london ? j : null).catch(() => null);
  const redraw = (push = false) => { S.shown = PAGE; apply(!S.loc); writeUrl(push); };
  let t;
  $('pb-q').oninput = e => { clearTimeout(t); t = setTimeout(() => { S.qRaw = e.target.value.trim().slice(0, 80); S.q = S.qRaw.toLowerCase(); redraw(); }, 140); };
  $('pb-borough').onchange = e => { S.borough = e.target.value; redraw(); };
  $('pb-gsel').onchange = e => { openGroup(e.target.value); };
  $('pb-cask').onchange = e => { S.cask = e.target.checked; redraw(); };
  $('pb-sort').onchange = e => { S.sort = e.target.value; S.shown = PAGE; apply(false); };
  $('pb-go').onclick = () => findPostcode();
  $('pb-pc').onkeydown = e => { if (e.key === 'Enter') findPostcode(); };
  $('pb-geo').onclick = useGeo;
  $('pb-clear').onclick = () => { setLoc(null); $('pb-pc').value = ''; $('pb-status').textContent = ''; writeUrl(); };
  $('pb-more').onclick = () => { S.shown += PAGE; drawList(); };
  $('pb-counts').onclick = e => { const b = e.target.closest('button[data-cat]'); if (!b) return; S.cat = S.cat === b.dataset.cat ? '' : b.dataset.cat; redraw(); };
  $('pb-list').onclick = e => { const b = e.target.closest('button[data-i]'); if (b) { S.opener = b; select(S.pubs[+b.dataset.i], true); } };
  $('pb-active').onclick = e => {
    const b = e.target.closest('button[data-clear]'); if (!b) return;
    const k = b.dataset.clear;
    if (k === 'all') { S.cat = ''; S.borough = ''; S.q = ''; S.qRaw = ''; S.cask = false; S.g = ''; }
    else if (k === 'q') { S.q = ''; S.qRaw = ''; } else if (k === 'cask') S.cask = false; else if (k === 'g') S.g = ''; else if (k === 'b') S.borough = ''; else if (k === 'cat') S.cat = '';
    syncControls(); redraw(k === 'g' || k === 'all');
  };
  $('pb-group').onclick = e => {
    const a = e.target.closest('[data-act]'); if (!a) return;
    if (a.dataset.act === 'allpubs') openGroup('');
    else if (a.dataset.act === 'borough') { S.borough = S.borough === a.dataset.b ? '' : a.dataset.b; syncControls(); redraw(); }
  };
  document.querySelector('.pb-modes').onclick = e => { const b = e.target.closest('button[data-mode]'); if (b) setMode(b.dataset.mode, true); };
  document.querySelector('.pb-modes').onkeydown = e => {   // tablist arrow keys
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    const m = S.mode === 'explore' ? 'insights' : 'explore'; setMode(m, true); $('pb-tab-' + m).focus();
  };
  $('pb-share').onclick = copyLink;
  initMap();
  initInsightsEvents();
  addEventListener('resize', () => { if (!$('v-pubs').hidden) { resize(); if (S.mode === 'insights') drawCharts(); } });
  addEventListener('keydown', e => { if (e.key === 'Escape' && !$('pb-drawer').hidden) closeDetail(); });
}

export async function onShow(arg) {
  const ok = await S.ready;
  if (ok === false) return;
  resize();
  if (location.hash === S.lastHash) return;   // our own replaceState/pushState: already rendered
  if (arg && !arg.startsWith('?')) {         // v1 link: #pubs/<osm id>
    const p = S.pubs.find(x => x.osm === dec(arg));
    applyParams(new URLSearchParams(p ? 'p=' + p.osm : ''));
    return;
  }
  applyParams(new URLSearchParams((arg || '').replace(/^\?/, '')));
}
export function redraw() { drawMap(); }

function load(d) {
  S.data = d;
  S.cats = d.categories || {};
  S.groups = Object.fromEntries((d.groups || []).map(g => [g.id, g]));
  const f = d.fields;
  S.pubs = d.pubs.map((r, i) => { const p = {}; f.forEach((k, j) => { p[k] = r[j]; }); p.x = p.lon * COS; p.y = -p.lat; p.dist = null; p.i = i; p.nm = (p.name || '').toLowerCase(); p.pcn = (p.postcode || '').toLowerCase().replace(/\s/g, ''); return p; });
  S.pass = new Uint8Array(S.pubs.length);
  S.boroughs = [...new Set(S.pubs.map(p => p.borough).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  $('pb-borough').innerHTML = '<option value="">All boroughs</option>' + S.boroughs.map(b => `<option value="${esc(b)}">${esc(bName(b))}</option>`).join('');
  const gn = {}; for (const p of S.pubs) if (p.group) gn[p.group] = (gn[p.group] || 0) + 1;
  S.gcount = gn;
  // boroughs with an open licensing register: it surfaces independents there that other boroughs can't show
  S.openReg = [...new Set(S.pubs.filter(p => (p.evidence || []).some(e => e.endsWith(':licensee'))).map(p => p.borough).filter(Boolean))].sort();
  $('pb-gsel').innerHTML = '<option value="">All owners</option>' + CAT_ORDER.filter(c => c !== 'unknown').map(c => {
    const gs = Object.values(S.groups).filter(g => g.category === c && gn[g.id]).sort((a, b) => gn[b.id] - gn[a.id]);
    return gs.length ? `<optgroup label="${esc(catName(c))}">${gs.map(g => `<option value="${esc(g.id)}">${esc(g.name)} (${fmt(gn[g.id])})</option>`).join('')}</optgroup>` : '';
  }).join('');
  const big = Object.keys(gn).sort((a, b) => gn[b] - gn[a]);
  S.cmp = S.boroughs.slice().sort((a, b) => S.pubs.filter(p => p.borough === b).length - S.pubs.filter(p => p.borough === a).length).slice(0, 2);
  S.bigGroups = big;
  const l = d.licence ? ` Licence: ${esc(d.licence)}.` : '';
  $('pb-foot').innerHTML = `Pub locations: © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap contributors</a> (ODbL). Contains OGL data: FSA, London Borough of Camden. ${esc(d.method || '')}${l} Data as of ${esc(d.as_of || '')}. These are signals from open data, not verdicts: "No group link found" does not mean a pub is independent, and tenanted pubs can be owned by a group while run by a tenant. Something wrong? Open a pub and use "Suggest a correction".`;
  $('pb-maphint').hidden = true;
  S.fit = true;
  return true;
}

// ======================================================================= URL state
function urlState() {
  const u = new URLSearchParams();
  if (S.mode === 'insights') u.set('mode', 'insights');
  if (S.g) u.set('g', S.g);
  if (S.cat) u.set('cat', S.cat);
  if (S.borough) u.set('b', S.borough);
  if (S.qRaw) u.set('q', S.qRaw);
  if (S.cask) u.set('cask', '1');
  if (S.mode === 'insights' && S.cmpSet && S.cmp.length === 2) u.set('cmp', S.cmp.join('|'));
  if (S.sel && !$('pb-drawer').hidden && S.mode === 'explore') u.set('p', S.sel.osm);
  const s = u.toString();
  return '#pubs' + (s ? '?' + s : '');
}
function writeUrl(push = false) {
  if (S.applying || $('v-pubs').hidden) return;
  const h = urlState();
  S.lastHash = h;
  if (h === location.hash) return;
  try { history[push ? 'pushState' : 'replaceState'](null, '', h); } catch { /* sandboxed */ }
}
function applyParams(u) {
  S.applying = true;
  S.mode = u.get('mode') === 'insights' ? 'insights' : 'explore';
  const cat = u.get('cat'); S.cat = cat && S.cats[cat] ? cat : '';
  const b = u.get('b'); S.borough = b && S.boroughs.includes(b) ? b : '';
  const g = u.get('g'); S.g = g && S.groups[g] ? g : '';
  S.qRaw = (u.get('q') || '').trim().slice(0, 80); S.q = S.qRaw.toLowerCase();
  S.cask = u.get('cask') === '1';
  const cmp = (u.get('cmp') || '').split('|').filter(x => S.boroughs.includes(x));
  if (cmp.length === 2) { S.cmp = cmp; S.cmpSet = true; }
  const pc = (u.get('pc') || '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 8);
  syncControls();
  S.shown = PAGE;
  apply(true);
  renderMode();
  const p = u.get('p'), pub = p && S.mode === 'explore' ? S.pubs.find(x => x.osm === p) : null;
  if (pub) select(pub, true); else if (!$('pb-drawer').hidden) { $('pb-drawer').hidden = true; S.sel = null; }
  S.applying = false;
  writeUrl(false);
  if (pc && pc !== S.loc?.pc) { $('pb-pc').value = pc; $('pb-status').textContent = `This link mentions ${pc}. Press “Find near me” to search near it.`; }
}
function syncControls() {
  $('pb-q').value = S.qRaw; $('pb-borough').value = S.borough; $('pb-cask').checked = S.cask; $('pb-gsel').value = S.g;
}
async function copyLink() {
  writeUrl(false);
  const url = location.href;
  try { await navigator.clipboard.writeText(url); toast('Link to this view copied'); }
  catch { prompt('Copy this link', url); }
}

// ======================================================================= modes
function setMode(m, push) {
  if (m === S.mode) return;
  S.mode = m;
  if (m === 'insights' && !$('pb-drawer').hidden) { $('pb-drawer').hidden = true; S.sel = null; }
  renderMode();
  writeUrl(push);
}
function renderMode() {
  const ins = S.mode === 'insights';
  for (const m of ['explore', 'insights']) {
    const on = (m === 'insights') === ins, t = $('pb-tab-' + m);
    t.setAttribute('aria-selected', on); t.classList.toggle('on', on); t.tabIndex = on ? 0 : -1;
    $('pb-' + m).hidden = !on;
  }
  if (ins) buildInsights(); else { resize(); drawMap(); }
}

// ======================================================================= filtering (Explore)
const passes = (p, skipCat) => (skipCat || !S.cat || p.category === S.cat) && (!S.cask || p.cask === 1) && (!S.borough || p.borough === S.borough)
  && (!S.g || p.group === S.g) && (!S.q || p.nm.includes(S.q) || p.pcn.startsWith(S.q.replace(/\s/g, '')));

function apply(refit) {
  const L = [];
  for (const p of S.pubs) { const ok = passes(p); S.pass[p.i] = ok ? 1 : 0; if (ok) L.push(p); }
  if (S.loc) for (const p of S.pubs) p.dist = km(S.loc.lon, S.loc.lat, p.lon, p.lat);
  const rank = p => p.category === 'indie' ? 0 : 1;
  if (S.loc) L.sort((a, b) => (S.sort === 'indie' ? rank(a) - rank(b) : 0) || a.dist - b.dist);
  else L.sort((a, b) => rank(a) - rank(b) || a.nm.localeCompare(b.nm));   // independents first, then A-Z
  S.list = L; S.ver++;
  if (refit) fit();
  drawGroup(); drawCounts(); drawActive(); drawList(); drawMap();
}

function drawCounts() {
  const n = Object.fromEntries(CAT_ORDER.map(c => [c, 0]));
  let tot = 0;
  for (const p of S.pubs) if (passes(p, true)) { n[p.category] = (n[p.category] || 0) + 1; tot++; }
  const cs = CAT_ORDER.filter(c => S.cats[c]);
  $('pb-mix').innerHTML = tot ? cs.filter(c => n[c]).map(c => `<span class="pb-c-${c}${S.cat && S.cat !== c ? ' dim' : ''}" style="flex-grow:${n[c]}" title="${esc(catName(c))}: ${fmt(n[c])}"></span>`).join('') : '';
  $('pb-counts').innerHTML = cs.map(c => `<button type="button" class="pb-cat${S.cat === c ? ' on' : ''}" data-cat="${c}" aria-pressed="${S.cat === c}" style="--c:var(--pb-${c})">
    <span class="pb-shape" aria-hidden="true">${SHAPE[c]}</span><b>${fmt(n[c])}</b><small>${esc(catLabel(c))}${c === 'indie' ? ' (likely)' : ''}</small><i>${tot ? pct(n[c] / tot) : ''}</i></button>`).join('');
}

function drawActive() {
  const chips = [];
  if (S.g) chips.push(['g', `Owner: ${S.groups[S.g]?.name || S.g}`]);
  if (S.cat) chips.push(['cat', catName(S.cat)]);
  if (S.borough) chips.push(['b', bName(S.borough)]);
  if (S.cask) chips.push(['cask', 'Cask ale']);
  if (S.qRaw) chips.push(['q', `“${S.qRaw}”`]);
  $('pb-active').innerHTML = chips.length ? chips.map(([k, t]) => `<button type="button" class="pb-fchip" data-clear="${k}" aria-label="Remove filter ${esc(t)}">${esc(t)}<span aria-hidden="true">✕</span></button>`).join('')
    + (chips.length > 1 ? '<button type="button" class="pb-fchip clear" data-clear="all">Clear all</button>' : '') : '';
}

function listItem(p) {
  const cl = confText(p), lab = esc(catLabel(p.category)) + (p.category === 'indie' || cl.startsWith('likely') ? ' (likely)' : '');
  const g = p.group && S.groups[p.group];
  return `<li><button type="button" data-i="${p.i}" style="--c:var(--pb-${esc(p.category)})"${S.sel === p ? ' aria-current="true"' : ''}>
    <span class="pb-shape" aria-hidden="true">${SHAPE[p.category] || '○'}</span>
    <span class="pb-li"><b>${esc(p.name || 'Unnamed pub')}</b><small>${lab}${g && p.category !== 'indie' ? ': ' + esc(g.name) : ''}${p.cask === 1 ? ' · Cask ale' : ''}</small><small>${esc([bName(p.borough || ''), p.postcode].filter(Boolean).join(' · '))}</small></span>
    ${p.dist != null && S.loc ? `<span class="pb-dist">${distText(p.dist)}</span>` : ''}</button></li>`;
}

function drawList() {
  const n = S.list.length, show = S.list.slice(0, S.shown);
  $('pb-count').textContent = `${fmt(n)} pub${n === 1 ? '' : 's'}${S.loc ? ', nearest first' + (S.sort === 'indie' ? ', independents ahead' : '') : ''}`;
  $('pb-list').innerHTML = n ? show.map(listItem).join('')
    : `<li class="pb-empty"><b>No pubs match these filters.</b> ${S.cask ? 'Few pubs have a cask-ale tag on OpenStreetMap, so try turning “Cask ale” off. ' : ''}Remove a filter above to see more.</li>`;
  $('pb-more').hidden = S.shown >= n;
  $('pb-more').textContent = `Show more (${fmt(n - S.shown)} left)`;
}

// ======================================================================= group page (drill-down)
function openGroup(id) {
  S.g = id && S.groups[id] ? id : '';
  if (S.g) { S.cat = ''; S.borough = ''; S.q = ''; S.qRaw = ''; }
  S.mode = 'explore'; S.shown = PAGE;
  syncControls(); renderMode();
  if (!$('pb-drawer').hidden) { $('pb-drawer').hidden = true; S.sel = null; }
  apply(true); writeUrl(true);
  if (S.g) $('pb-group').scrollIntoView({ behavior: reduced() ? 'auto' : 'smooth', block: 'start' });
}
function modelText(g) {
  if (!g) return '';
  const o = esc(g.name);
  if (g.model === 'tenanted') return `Owned by ${o}; run by a tenant publican, usually tied to the group's beer.`;
  if (g.model === 'managed') return `Run directly by ${o}.`;
  return `${o} runs some pubs directly and leases others to tenants; open data does not show which is which.`;
}
function drawGroup() {
  const box = $('pb-group'), g = S.groups[S.g];
  document.querySelector('.pb-overview').hidden = !!g;
  if (!g) { box.hidden = true; box.innerHTML = ''; return; }
  const L = S.pubs.filter(p => p.group === g.id), n = L.length;
  const bc = {}; for (const p of L) if (p.borough) bc[p.borough] = (bc[p.borough] || 0) + 1;
  const bs = Object.entries(bc).sort((a, b) => b[1] - a[1]), mx = bs[0]?.[1] || 1;
  const cask = L.filter(p => p.cask === 1).length, rs = L.map(p => ratingNum(p.rating)).filter(x => x != null);
  const ig = S.ins?.groups?.find?.(x => x.id === g.id);
  const rAvg = rs.length ? rs.reduce((a, b) => a + b, 0) / rs.length : ig?.rating_avg, rN = rs.length || ig?.rating_n || 0;
  const known = S.pubs.filter(p => p.category !== 'unknown').length;
  const src = safeHttp(g.src), top = 8;
  box.hidden = false;
  box.style.setProperty('--c', `var(--pb-${g.category})`);
  box.innerHTML = `<button type="button" class="pb-back" data-act="allpubs"><span aria-hidden="true">‹</span> All pubs</button>
    <div class="pb-ghead"><span class="pb-gmark" aria-hidden="true">${SHAPE[g.category]}</span>
      <div><h3 id="pb-gtitle">${esc(g.name)}</h3><p>${esc(catName(g.category))}${g.owner && g.owner !== g.name ? `. Owned by <b>${esc(g.owner)}</b>` : ''}</p></div></div>
    <p>${modelText(g)} ${esc(g.note || '')} ${src ? `<a href="${esc(src)}" target="_blank" rel="noopener noreferrer">Source</a>` : ''}</p>
    <dl class="pb-gstats">
      <div><dt>Pubs we can link</dt><dd>${fmt(n)}</dd></div>
      <div><dt>Share of traced London pubs</dt><dd>${known ? pct(n / known, 1) : '–'}</dd></div>
      <div><dt>Boroughs</dt><dd>${fmt(bs.length)}</dd></div>
      <div><dt>Tagged as serving cask ale</dt><dd>${n ? pct(cask / n) : '–'}</dd></div>
      <div><dt>Average food hygiene rating</dt><dd>${rAvg != null ? rAvg.toFixed(1) + `<small> of ${fmt(rN)} rated</small>` : '–'}</dd></div>
    </dl>
    <h4>Where its pubs are</h4>
    <ol class="pb-gb">${bs.slice(0, top).map(([b, k]) => `<li><button type="button" data-act="borough" data-b="${esc(b)}" aria-pressed="${S.borough === b}" aria-label="${esc(bName(b))}: ${k} pubs. Show only these">
      <span>${esc(bName(b))}</span><i style="--w:${(k / mx * 100).toFixed(1)}%"></i><b>${fmt(k)}</b></button></li>`).join('')}</ol>
    ${bs.length > top ? `<p class="note">And ${plural(bs.length - top, 'more borough')}. Tap a borough to show only its pubs.</p>` : '<p class="note">Tap a borough to show only its pubs.</p>'}
    <p class="note">These are the pubs we could link to ${esc(g.name)} in open data. The group may own more.</p>`;
}

// ======================================================================= location
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
  c.label = q.toUpperCase(); c.pc = key.slice(0, 8);
  if (S.mode !== 'explore') { S.mode = 'explore'; renderMode(); }
  setLoc(c);
  writeUrl(false);
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
    setLoc({ lon, lat, label: 'you' });   // never written to the URL
    st.textContent = 'Pubs near you. Your location stays in your browser.';
  }, () => { st.textContent = 'Could not get your location. Enter a postcode instead.'; }, { timeout: 10000, maximumAge: 60000 });
}

// ======================================================================= detail drawer
function select(p, open) {
  S.sel = p;
  const v = S.v, sx = p.x * v.s + v.ox, sy = p.y * v.s + v.oy;
  if (open && v.w && (sx < 20 || sy < 20 || sx > v.w - 20 || sy > v.h - 20)) { v.ox += v.w / 2 - sx; v.oy += v.h / 2 - sy; }
  drawMap();
  if (!open) return;
  const g = S.groups[p.group], cl = confText(p), osm = osmUrl(p.osm);
  const chips = [];
  if (osm) chips.push(`<a class="pb-chip" href="${osm}" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>`);
  if (p.fhrsid) chips.push(`<a class="pb-chip" href="https://ratings.food.gov.uk/business/${encodeURIComponent(p.fhrsid)}" target="_blank" rel="noopener noreferrer">FSA hygiene record</a>`);
  for (const e of p.evidence || []) {
    const lab = EVIDENCE[e] || e;
    // a pub's own Wikidata item links out only when a valid QID is present; otherwise plain text
    const qid = e === 'wikidata:owner' && /^Q\d+$/.test(p.wikidata || '') ? p.wikidata : '';
    chips.push(e.startsWith('camden:') ? `<a class="pb-chip" href="https://opendata.camden.gov.uk/d/rv9z-rsui" target="_blank" rel="noopener noreferrer">${esc(lab)}</a>`
      : qid ? `<a class="pb-chip" href="https://www.wikidata.org/wiki/${qid}" target="_blank" rel="noopener noreferrer">${esc(lab)}</a>` : `<span class="pb-chip">${esc(lab)}</span>`);
  }
  const web = safeHttp(p.website);
  let status;
  if (p.category === 'unknown') status = '<p><b>No group link found.</b> That is not a claim that this pub is independent: it only means none of our open sources link it to a group.</p>';
  else if (p.category === 'indie') status = `<p><b>Likely independent.</b> ${g ? esc(g.note || '') : 'No big-group link found, and the operator or licensee looks like a small independent business.'}</p>`;
  else status = `<p><b>${esc(catLabel(p.category))}${cl ? ` (${cl})` : ''}.</b></p>`;
  const cask = p.cask === 1 ? 'Cask ale: listed on OpenStreetMap.' : p.cask === 0 ? 'Cask ale: OpenStreetMap says none.' : 'Cask ale: not known.';
  const craft = p.craft === 1 ? 'Craft beer: listed on OpenStreetMap.' : 'Craft beer: not known.';
  const rn = ratingNum(p.rating);
  const rating = p.rating != null && p.rating !== ''
    ? `<h3>Food hygiene</h3><p class="pb-rating">${rn != null ? `<span class="pb-rbadge pb-bg-r${rn}" aria-hidden="true">${rn}</span>` : ''}<span>${rn != null ? `Rated <b>${rn} out of 5</b>` : `FSA status: <b>${esc(String(p.rating).replace(/([a-z])([A-Z])/g, '$1 $2'))}</b>`}${p.rating_date ? `, inspected ${esc(dateText(p.rating_date))}` : ''}.</span></p><p class="mutedtxt">Measures food safety on the day of inspection, not the beer or service.</p>` : '';
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
    <p class="pb-badge" style="--c:var(--pb-${esc(p.category)})"><span class="pb-shape" aria-hidden="true">${SHAPE[p.category] || '○'}</span>${esc(catLabel(p.category))}${cl ? ` · ${cl}` : ''}</p>
    <p class="mutedtxt">${esc([bName(p.borough || ''), p.postcode].filter(Boolean).join(' · '))}${p.dist != null && S.loc ? ' · ' + distText(p.dist) + ' away' : ''}</p>
    ${status}
    ${g ? `<h3>Owner</h3><p><b>${esc(g.owner || g.name)}</b> (${esc(g.name)})</p><p>${modelText(g)}</p><p>${esc(g.note || '')} ${safeHttp(g.src) ? `<a href="${esc(safeHttp(g.src))}" target="_blank" rel="noopener noreferrer">Source</a>` : ''}</p>
      ${S.g !== g.id && (S.gcount[g.id] || 0) > 1 ? `<p><button type="button" class="btn" id="pb-seeg">See all ${fmt(S.gcount[g.id])} ${esc(g.name)} pubs</button></p>` : ''}` : ''}
    <h3>Beer</h3><p>${cask}<br>${craft}<br>Keg lines: no open data yet.</p><p class="mutedtxt">Is a beer from an independent brewery? Check it on SIBA's <a href="https://indiebeer.uk/index.php#beerChecker" target="_blank" rel="noopener noreferrer">Indie Beer brewery checker</a>.</p>
    ${rating}
    <h3>Evidence</h3><div class="pb-chips">${chips.join('') || '<span class="mutedtxt">None recorded.</span>'}</div>
    ${web ? `<p><a href="${esc(web)}" target="_blank" rel="noopener noreferrer">Pub website</a></p>` : ''}
    <p style="margin-top:18px"><a class="btn primary" href="${esc(fix)}" target="_blank" rel="noopener noreferrer">Suggest a correction</a></p>
    <p class="note">Opens a pre-filled GitHub issue; nothing is sent until you submit it.</p>`;
  $('pb-x').onclick = closeDetail;
  if ($('pb-seeg')) $('pb-seeg').onclick = () => openGroup(g.id);
  $('pb-x').focus();
  writeUrl(false);
}
function closeDetail() {
  $('pb-drawer').hidden = true; S.sel = null; drawMap(); writeUrl(false);
  if (S.opener?.isConnected) S.opener.focus(); else $('pb-map').focus();
  S.opener = null;
}

// ======================================================================= map
const cv = () => $('pb-map');
function resize() {
  const c = cv(), r = c.getBoundingClientRect();
  if (!r.width) return;
  const dpr = devicePixelRatio || 1, first = !S.v.w;
  if (Math.round(r.width * dpr) !== c.width || Math.round(r.height * dpr) !== c.height) { c.width = Math.round(r.width * dpr); c.height = Math.round(r.height * dpr); }
  S.v.w = r.width; S.v.h = r.height;
  if (first || S.fit) fit(); else drawMap();
}
function fit() {
  const v = S.v;
  if (!v.w) { S.fit = true; return; }
  const L = S.list.length ? S.list : S.pubs;
  if (!L.length) return;
  let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
  for (const p of L) { if (p.x < x0) x0 = p.x; if (p.x > x1) x1 = p.x; if (p.y < y0) y0 = p.y; if (p.y > y1) y1 = p.y; }
  // the zoom baseline (s0) is always all of London, so "zoomed in" means the same thing whatever the filter
  let a0 = 1e9, a1 = -1e9, b0 = 1e9, b1 = -1e9;
  for (const p of S.pubs) { if (p.x < a0) a0 = p.x; if (p.x > a1) a1 = p.x; if (p.y < b0) b0 = p.y; if (p.y > b1) b1 = p.y; }
  v.s0 = Math.min(v.w / Math.max(a1 - a0, .004), v.h / Math.max(b1 - b0, .004)) * .9;
  const dx = Math.max(x1 - x0, .006), dy = Math.max(y1 - y0, .006);
  v.s = Math.min(v.w / dx, v.h / dy, v.s0 * 60) * .86;
  v.ox = v.w / 2 - (x0 + x1) / 2 * v.s; v.oy = v.h / 2 - (y0 + y1) / 2 * v.s;
  S.fit = false;
  drawMap();
}
let raf = 0;
function drawMap() {
  if (raf || S.mode !== 'explore') return;
  raf = requestAnimationFrame(() => { raf = 0; paint(); });
}
function shapePath(ctx, c, x, y, r) {
  if (c === 'pe') { ctx.moveTo(x, y - r * 1.3); ctx.lineTo(x + r * 1.1, y); ctx.lineTo(x, y + r * 1.3); ctx.lineTo(x - r * 1.1, y); ctx.closePath(); }
  else if (c === 'corp') { ctx.moveTo(x - r, y - r); ctx.rect(x - r, y - r, r * 2, r * 2); }
  else if (c === 'family') { ctx.moveTo(x, y - r * 1.2); ctx.lineTo(x + r * 1.1, y + r * .9); ctx.lineTo(x - r * 1.1, y + r * .9); ctx.closePath(); }
  else { ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, 7); }
}
const zoomLevel = () => Math.log2(S.v.s / S.v.s0);
const clustering = () => S.list.length > 250 && zoomLevel() < 1.6;
function clusters() {   // grid clusters in world space, cached per filter version and half-zoom step (so panning is free)
  const v = S.v, step = Math.floor(zoomLevel() * 2), key = S.ver + '|' + step + '|' + v.s0;
  if (S.clKey === key) return S.cl;
  const cw = CLUSTER_PX / (v.s0 * Math.pow(2, step / 2)), m = new Map();
  for (const p of S.list) {
    const k = Math.floor(p.x / cw) + ':' + Math.floor(p.y / cw);
    let c = m.get(k);
    if (!c) { c = { n: 0, x: 0, y: 0, by: {}, p }; m.set(k, c); }
    c.n++; c.x += p.x; c.y += p.y; c.by[p.category] = (c.by[p.category] || 0) + 1;
  }
  const out = [...m.values()];
  for (const c of out) { c.x /= c.n; c.y /= c.n; }
  out.sort((a, b) => a.n - b.n);   // big clusters drawn last, on top
  S.cl = out; S.clKey = key;
  return out;
}
function paint() {
  const c = cv(), v = S.v;
  if (!v.w) return;
  const ctx = c.getContext('2d'), dpr = devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, v.w, v.h);
  const col = Object.fromEntries(CAT_ORDER.map(k => [k, css('--pb-' + k) || '#888'])), surf = css('--surface'), ink = css('--ink'), muted = css('--muted');
  const z = zoomLevel(), r = Math.max(3, Math.min(8, 3.4 + z * .9)), inView = (x, y, m = 12) => x > -m && y > -m && x < v.w + m && y < v.h + m;
  const hits = [];
  // context: every pub outside the filter as a faint dot, so a group or borough is seen against all of London
  if (S.list.length < S.pubs.length) {
    ctx.fillStyle = muted; ctx.globalAlpha = .22; ctx.beginPath();
    for (const p of S.pubs) { if (S.pass[p.i]) continue; const x = p.x * v.s + v.ox, y = p.y * v.s + v.oy; if (inView(x, y, 2)) ctx.rect(x - 1, y - 1, 2, 2); }
    ctx.fill(); ctx.globalAlpha = 1;
  }
  const by = {}; for (const k of CAT_ORDER) by[k] = [];
  const big = [];
  if (clustering()) {
    // density underlay: every matching pub as a small dot, so the shape of the city stays visible under the clusters
    ctx.globalAlpha = .45;
    for (const k of CAT_ORDER) {
      ctx.fillStyle = col[k]; ctx.beginPath();
      for (const p of S.list) { if (p.category !== k) continue; const x = p.x * v.s + v.ox, y = p.y * v.s + v.oy; if (inView(x, y, 2)) ctx.rect(x - 1.1, y - 1.1, 2.2, 2.2); }
      ctx.fill();
    }
    ctx.globalAlpha = 1;
    for (const cl of clusters()) {
      const x = cl.x * v.s + v.ox, y = cl.y * v.s + v.oy;
      if (!inView(x, y, 30)) continue;
      if (cl.n === 1) { (by[cl.p.category] || by.unknown).push(x, y); hits.push({ x, y, r: 10, p: cl.p }); }
      else big.push([cl, x, y]);
    }
  } else {
    for (const p of S.list) { const x = p.x * v.s + v.ox, y = p.y * v.s + v.oy; if (inView(x, y)) { (by[p.category] || by.unknown).push(x, y); hits.push({ x, y, r: 12, p }); } }
  }
  for (const k of ['unknown', 'corp', 'pe', 'family', 'indie']) {
    const a = by[k]; if (!a.length) continue;
    const rr = k === 'indie' ? r * 1.1 : r;
    ctx.beginPath();
    for (let i = 0; i < a.length; i += 2) shapePath(ctx, k, a[i], a[i + 1], rr);
    if (k === 'unknown') { ctx.globalAlpha = .8; ctx.fillStyle = surf; ctx.fill(); ctx.strokeStyle = col.unknown; ctx.lineWidth = 1.6; ctx.stroke(); }
    else { ctx.globalAlpha = 1; ctx.strokeStyle = surf; ctx.lineWidth = 2; ctx.stroke(); ctx.fillStyle = col[k]; ctx.fill(); }
  }
  ctx.globalAlpha = 1;
  // clusters: a ring split by ownership mix, count in the middle
  ctx.font = '650 10.5px system-ui,-apple-system,"Segoe UI",sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
  for (const [cl, x, y] of big) {
    const R = Math.min(18, 7 + Math.sqrt(cl.n) * .8);
    ctx.beginPath(); ctx.arc(x, y, R + 1.5, 0, 7); ctx.fillStyle = surf; ctx.fill();
    let a0 = -Math.PI / 2;
    const cats = CAT_ORDER.filter(k => cl.by[k]), gap = cats.length > 1 ? .07 : 0;
    ctx.lineWidth = 3.5;
    for (const k of cats) {
      const a = cl.by[k] / cl.n * Math.PI * 2;
      ctx.beginPath(); ctx.arc(x, y, R - 2.5, a0 + gap / 2, a0 + Math.max(a - gap / 2, .02)); ctx.strokeStyle = col[k]; ctx.stroke();
      a0 += a;
    }
    ctx.fillStyle = ink; ctx.fillText(cl.n > 999 ? Math.round(cl.n / 100) / 10 + 'k' : String(cl.n), x, y + .5);
    hits.push({ x, y, r: R + 4, cl });
  }
  if (S.loc) {
    const x = S.loc.lon * COS * v.s + v.ox, y = -S.loc.lat * v.s + v.oy;
    ctx.strokeStyle = surf; ctx.lineWidth = 3; ctx.fillStyle = css('--accent');
    ctx.beginPath(); ctx.arc(x, y, 7, 0, 7); ctx.stroke(); ctx.fill();
    ctx.strokeStyle = css('--accent'); ctx.lineWidth = 1.5; ctx.globalAlpha = .35; ctx.beginPath(); ctx.arc(x, y, 14, 0, 7); ctx.stroke(); ctx.globalAlpha = 1;
  }
  if (S.sel) {
    const p = S.sel, x = p.x * v.s + v.ox, y = p.y * v.s + v.oy;
    ctx.beginPath(); shapePath(ctx, p.category, x, y, r * 1.3); ctx.fillStyle = col[p.category] || col.unknown; ctx.strokeStyle = surf; ctx.lineWidth = 2; ctx.stroke(); ctx.fill();
    ctx.strokeStyle = ink; ctx.lineWidth = 2.5; ctx.beginPath(); ctx.arc(x, y, r + 8, 0, 7); ctx.stroke();
    const label = p.name || 'Unnamed pub';
    ctx.font = '650 12px system-ui,-apple-system,"Segoe UI",sans-serif'; ctx.textAlign = x > v.w - 140 ? 'right' : 'left'; ctx.textBaseline = 'middle';
    const tx = x + (ctx.textAlign === 'right' ? -(r + 14) : r + 14);
    ctx.lineWidth = 4; ctx.strokeStyle = surf; ctx.lineJoin = 'round'; ctx.strokeText(label, tx, y); ctx.fillStyle = ink; ctx.fillText(label, tx, y);
  }
  S.hits = hits;
  const hint = $('pb-zhint');
  if (hint) hint.hidden = !clustering();
}
function hitAt(mx, my) {
  let best = null, bd = Infinity;
  for (const h of S.hits) { const dx = h.x - mx, dy = h.y - my, d = dx * dx + dy * dy; if (d < h.r * h.r * (h.cl ? 1 : 1.6) && d < bd) { bd = d; best = h; } }
  return best;
}
function zoomAt(cx, cy, f) {
  const v = S.v, s = Math.max(v.s0 * .6, Math.min(v.s0 * 400, v.s * f)), k = s / v.s;
  v.ox = cx - (cx - v.ox) * k; v.oy = cy - (cy - v.oy) * k; v.s = s; drawMap();
}
function showTip(h, x, y) {
  const tip = $('pb-tip');
  tip.replaceChildren();
  const b = document.createElement('b');
  if (h.cl) {
    b.textContent = `${fmt(h.cl.n)} pubs`;
    tip.append(b);
    for (const k of CAT_ORDER) if (h.cl.by[k]) { const s = document.createElement('span'); s.className = 'pb-tiprow'; s.style.setProperty('--c', `var(--pb-${k})`); s.textContent = `${SHAPE[k]} ${fmt(h.cl.by[k])} ${catSent(k)}`; tip.append(s); }
    const s = document.createElement('small'); s.textContent = 'Click to zoom in'; tip.append(s);
  } else {
    b.textContent = h.p.name || 'Unnamed pub'; tip.append(b);
    const g = h.p.group && S.groups[h.p.group];
    tip.append(document.createTextNode(catName(h.p.category) + (g && h.p.category !== 'indie' ? `: ${g.name}` : '')));
  }
  tip.hidden = false;
  tip.style.left = Math.max(4, Math.min(x + 14, S.v.w - 220)) + 'px'; tip.style.top = Math.min(y + 14, S.v.h - 80) + 'px';
}
function initMap() {
  const c = cv(), ptrs = new Map(), tip = $('pb-tip');
  let moved = 0, pinch = 0;
  c.addEventListener('pointerdown', e => { c.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, [e.offsetX, e.offsetY]); moved = 0; pinch = 0; c.classList.add('drag'); });
  c.addEventListener('pointermove', e => {
    const prev = ptrs.get(e.pointerId);
    if (!prev) {
      const h = e.pointerType === 'mouse' ? hitAt(e.offsetX, e.offsetY) : null;
      if (h) { showTip(h, e.offsetX, e.offsetY); c.style.cursor = 'pointer'; } else { tip.hidden = true; c.style.cursor = ''; }
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
    c.classList.remove('drag');
    if (had && !ptrs.size && moved < 6 && e.type === 'pointerup') {
      const h = hitAt(e.offsetX, e.offsetY);
      if (h?.cl) zoomAt(h.x, h.y, 2.4);
      else if (h) { S.opener = null; select(h.p, true); }
    }
    if (ptrs.size < 2) pinch = 0;
  };
  c.addEventListener('pointerup', end); c.addEventListener('pointercancel', end);
  c.addEventListener('pointerleave', () => { tip.hidden = true; });
  c.addEventListener('wheel', e => { e.preventDefault(); zoomAt(e.offsetX, e.offsetY, Math.exp(-e.deltaY * .0015)); }, { passive: false });
  c.addEventListener('keydown', e => {   // keyboard: arrows pan, + and - zoom, 0 resets
    const v = S.v, k = e.key, P = 60;
    if (k === 'ArrowLeft') v.ox += P; else if (k === 'ArrowRight') v.ox -= P; else if (k === 'ArrowUp') v.oy += P; else if (k === 'ArrowDown') v.oy -= P;
    else if (k === '+' || k === '=') return void (e.preventDefault(), zoomAt(v.w / 2, v.h / 2, 1.6));
    else if (k === '-' || k === '_') return void (e.preventDefault(), zoomAt(v.w / 2, v.h / 2, 1 / 1.6));
    else if (k === '0') return void (e.preventDefault(), fit());
    else return;
    e.preventDefault(); drawMap();
  });
  $('pb-zin').onclick = () => zoomAt(S.v.w / 2, S.v.h / 2, 1.6);
  $('pb-zout').onclick = () => zoomAt(S.v.w / 2, S.v.h / 2, 1 / 1.6);
  $('pb-zreset').onclick = () => fit();
  $('pb-legend').innerHTML = CAT_ORDER.map(k => `<span style="--c:var(--pb-${k})"><i class="pb-shape" aria-hidden="true">${SHAPE[k]}</i>${k === 'indie' ? 'Independent (likely)' : k === 'unknown' ? 'No group link found' : esc({ pe: 'PE / investor-owned', corp: 'Corporate group', family: 'Family brewer' }[k])}</span>`).join('')
    + '<span class="pb-lgcl" id="pb-zhint"><i aria-hidden="true"></i>Nearby pubs grouped; ring shows the ownership mix. Zoom in for each pub.</span>';
}

// ======================================================================= insights: data
function computeInsights() {   // the subset of pubs-insights.json that pubs.json alone supports
  const P = S.pubs, zero = () => Object.fromEntries(CAT_ORDER.map(c => [c, 0]));
  const summ = L => {
    const by = zero(), g = {}, dist = Object.fromEntries(RATINGS.map(r => [r, 0])); let cask = 0, caskK = 0, rs = 0, rn = 0;
    for (const p of L) {
      by[p.category] = (by[p.category] || 0) + 1;
      if (p.group && GROUPISH.includes(p.category)) g[p.group] = (g[p.group] || 0) + 1;   // as the server: an indie-category pub is its own owner
      if (p.cask === 1) cask++; if (p.cask != null) caskK++;
      const r = ratingNum(p.rating); if (r != null) { rs += r; rn++; dist[r]++; }
    }
    const known = L.length - by.unknown, gN = GROUPISH.reduce((a, c) => a + by[c], 0), inG = Object.values(g).reduce((a, b) => a + b, 0);
    let hhi = null;
    if (known) { hhi = (known - inG) / known ** 2; for (const n of Object.values(g)) hhi += (n / known) ** 2; }   // each traced pub outside a group counts as its own owner
    return { by, g, known, gN, cask, caskK, hhi, rating_avg: rn ? rs / rn : null, rating_n: rn, rating_dist: dist };
  };
  const L = summ(P), top = Object.entries(L.g).sort((a, b) => b[1] - a[1]);
  const london = { total: P.length, by_category: L.by, known: L.known, group_share: L.known ? L.gN / L.known : null, indie_share_of_known: L.known ? L.by.indie / L.known : null,
    cask: L.cask, top_groups: top.slice(0, 10).map(([id, n]) => ({ id, n, share: n / L.known })), hhi: L.hhi, top5_share: L.known ? top.slice(0, 5).reduce((a, x) => a + x[1], 0) / L.known : null };
  const groupBy = key => { const m = new Map(); for (const p of P) { const k = key(p); if (k == null) continue; if (!m.has(k)) m.set(k, []); m.get(k).push(p); } return m; };
  const boroughs = [...groupBy(p => p.borough || null)].map(([b, BL]) => {
    const s = summ(BL), tg = Object.entries(s.g).sort((a, c) => c[1] - a[1])[0];
    return { borough: b, total: BL.length, by_category: s.by, group_share: s.known ? s.gN / s.known : null, indie_n: s.by.indie, cask_n: s.cask, top_group: tg?.[0] || null, top_group_n: tg?.[1] || 0,
      hhi: s.hhi, groups: s.g, imd_decile_avg: null, income_avg: null, rating_avg: s.rating_avg, rating_n: s.rating_n, pubs_per_km2: null };
  });
  const groups = [...groupBy(p => p.group || null)].map(([id, GL]) => {
    const s = summ(GL), bc = {}; for (const p of GL) if (p.borough) bc[p.borough] = (bc[p.borough] || 0) + 1;
    return { id, n: GL.length, boroughs: Object.keys(bc).length, cask_n: s.cask, cask_known: s.caskK, rating_avg: s.rating_avg, rating_n: s.rating_n, rating_dist: s.rating_dist, top_boroughs: Object.entries(bc).sort((a, b) => b[1] - a[1]).slice(0, 5) };
  }).sort((a, b) => b.n - a.n);
  const by_category = Object.fromEntries(CAT_ORDER.map(c => { const s = summ(P.filter(p => p.category === c)); return [c, { n: s.by[c], cask_n: s.cask, cask_known: s.caskK, rating_avg: s.rating_avg, rating_n: s.rating_n, rating_dist: s.rating_dist, imd_decile_avg: null }]; }));
  return { london, boroughs, groups, by_category, deprivation: null, churn: null, notes: [] };
}
async function getInsights() {
  if (S.ins) return S.ins;
  let file = await S.insReady; const base = computeInsights();
  // a file built from a different pubs.json would contradict the map: fall back to figures computed here
  if (file && file.as_of && S.data?.as_of && file.as_of !== S.data.as_of) file = null;
  S.insFile = !!file;
  const ins = { ...base };
  if (file) for (const k of ['as_of', 'method', 'notes', 'london', 'boroughs', 'groups', 'by_category', 'deprivation', 'churn']) if (k in file && (file[k] != null || k === 'deprivation' || k === 'churn')) ins[k] = file[k];
  // pubs.json is the source of truth for which groups exist; keep only rows we can name
  ins.groups = (Array.isArray(ins.groups) ? ins.groups : base.groups).filter(g => S.groups[g.id] && g.n > 0);
  ins.boroughs = (Array.isArray(ins.boroughs) ? ins.boroughs : base.boroughs).filter(b => b && b.borough);
  S.ins = ins;
  return ins;
}

// ======================================================================= insights: charts (inline SVG, measured width)
const svgEsc = esc;
function roundRight(x, y, w, h, r) {   // a bar segment with a 4px rounded data end and a square baseline
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  return `M${x},${y}h${w - r}a${r},${r} 0 0 1 ${r},${r}v${h - 2 * r}a${r},${r} 0 0 1 ${-r},${r}h${-(w - r)}z`;
}
function roundTop(x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  return `M${x},${y + h}v${-(h - r)}a${r},${r} 0 0 1 ${r},${-r}h${w - 2 * r}a${r},${r} 0 0 1 ${r},${r}v${h - r}z`;
}
// rows: [{label, value, segs:[[cls, n, name]], total, aria, act}] ; scale: max of row totals (or 1 for shares)
function hbars(rows, w, { scale, act, valueW = 112 } = {}) {
  const narrow = w < 560, BH = 14, labelW = narrow ? 0 : Math.min(190, Math.round(w * .28));
  const rowH = narrow ? 40 : 26, top = 4, plotX = labelW, plotW = Math.max(40, w - labelW - (narrow ? 0 : valueW) - 4);
  const mx = scale || Math.max(1, ...rows.map(r => r.total));
  let out = '';
  rows.forEach((r, i) => {
    const y0 = top + i * rowH, by = narrow ? y0 + 20 : y0 + (rowH - BH) / 2;
    let x = plotX, segs = '';
    const live = r.segs.filter(s => s[1] > 0);
    live.forEach((s, j) => {
      const full = s[1] / mx * plotW, last = j === live.length - 1, sw = Math.max(last ? full : full - 2, .5);
      segs += last ? `<path class="pb-f-${s[0]}" d="${roundRight(x, by, sw, BH, 4)}"/>` : `<rect class="pb-f-${s[0]}" x="${x.toFixed(1)}" y="${by}" width="${sw.toFixed(1)}" height="${BH}"/>`;
      x += full;
    });
    const lab = `<text class="pb-tl" x="${narrow ? 0 : labelW - 10}" y="${narrow ? y0 + 12 : y0 + rowH / 2}" text-anchor="${narrow ? 'start' : 'end'}" dominant-baseline="middle">${svgEsc(r.label)}</text>`;
    const val = `<text class="pb-tv" x="${narrow ? w : plotX + plotW + 8}" y="${narrow ? y0 + 12 : y0 + rowH / 2}" text-anchor="${narrow ? 'end' : 'start'}" dominant-baseline="middle">${svgEsc(r.value)}</text>`;
    out += `<g class="pb-row${r.dim ? ' pb-dim' : ''}" data-i="${i}"><rect class="pb-hit" x="0" y="${y0}" width="${w}" height="${rowH}" rx="6" tabindex="0" role="${act ? 'button' : 'img'}" aria-label="${svgEsc(r.aria)}"/>${segs}${lab}${val}</g>`;
  });
  return `<svg class="pb-svg" width="${w}" height="${top + rows.length * rowH + 4}" viewBox="0 0 ${w} ${top + rows.length * rowH + 4}">${out}</svg>`;
}

const CHART = {};   // key -> (w) => svg ; filled by buildInsights
function drawCharts() {
  const box = $('pb-insights');
  if (box.hidden || !S.ins) return;
  for (const el of box.querySelectorAll('[data-chart]')) {
    const f = CHART[el.dataset.chart], w = Math.floor(el.clientWidth);
    if (f && w > 0) { el.innerHTML = f(w); el.dataset.w = w; }
  }
}
function legendHtml(keys, label) {
  return `<div class="pb-lg" aria-hidden="true">${keys.map(k => `<span><i class="pb-bg-${k}"></i>${esc(label(k))}</span>`).join('')}</div>`;
}
function tableHtml(caption, head, rows) {
  return `<details class="pb-tbl"><summary>Show as a table</summary><div class="tablewrap"><table><caption class="sr">${esc(caption)}</caption><thead><tr>${head.map((h, i) => `<th${i ? ' class="num"' : ''}>${esc(h)}</th>`).join('')}</tr></thead>
    <tbody>${rows.map(r => `<tr>${r.map((c, i) => `<td${i ? ' class="num"' : ''}>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div></details>`;
}
function card(id, title, take, body, cav, extra = '') {
  return `<section class="pb-card" id="pb-s-${id}" aria-labelledby="pb-h-${id}"><h3 id="pb-h-${id}">${esc(title)}</h3><p class="pb-take">${take}</p>${body}<p class="pb-cav">${cav}</p>${extra}</section>`;
}
function hhiWords(h) {
  h = hhiNorm(h);
  if (h == null || h <= 0) return null;
  const eq = 1 / h, pts = Math.round(h * 10000);
  const level = pts < 1000 ? 'not concentrated' : pts < 1800 ? 'moderately concentrated' : 'highly concentrated';
  return { eq, pts, level };
}
function waffle(by, total) {   // 100 cells of all pubs (incl. no group link), largest remainder rounding
  const cats = CAT_ORDER, raw = cats.map(c => (by[c] || 0) / total * 100), cells = raw.map(Math.floor);
  let left = 100 - cells.reduce((a, b) => a + b, 0);
  raw.map((x, i) => [x - Math.floor(x), i]).sort((a, b) => b[0] - a[0]).forEach(([, i]) => { if (left > 0) { cells[i]++; left--; } });
  let html = '';
  cats.forEach((c, i) => { for (let k = 0; k < cells[i]; k++) html += `<i class="pb-bg-${c}"></i>`; });
  return { html, cells: Object.fromEntries(cats.map((c, i) => [c, cells[i]])) };
}

async function buildInsights() {
  const box = $('pb-insights');
  if (S.insBuilt) { drawCharts(); return; }
  const ok = await S.ready; if (ok === false) return;
  const I = await getInsights();
  S.insBuilt = true;
  const L = I.london, by = L.by_category || {}, known = L.known || (L.total - (by.unknown || 0));
  const gname = id => S.groups[id]?.name || id;
  const parts = [];

  // ---- 1. London headline
  if (known > 0) {
    const gN = GROUPISH.reduce((a, c) => a + (by[c] || 0), 0);
    const w = waffle(by, L.total), gs = L.group_share ?? gN / known, gsT = L.group_share_of_total ?? gN / L.total;
    const hh = hhiWords(L.hhi), unk = by.unknown || 0;
    const t5 = L.top5_share, tg = (L.top_groups || [])[0];
    parts.push(`<section class="pb-hero" aria-labelledby="pb-h-hero">
      <div class="pb-hero-txt">
        <p class="pb-kicker">London, ${fmt(L.total)} pubs</p>
        <h3 id="pb-h-hero" class="pb-big">At least ${Math.floor(gsT * 100 + 1e-9)} in every 100 London pubs belong to a pub group or brewer.</h3>
        <p class="pb-sub">Of the pubs we could trace to an owner, ${Math.round(gs * 100)} in 100 belong to groups. Groups are easier to trace than independents, so the true share is somewhere between.</p>
        <p class="pb-take">${tg ? `The biggest, ${esc(gname(tg.id))}, has ${fmt(tg.n)}. ` : ''}${t5 != null ? `The five largest owners hold ${pct(t5)} of traced pubs between them. ` : ''}${hh ? `Taken together, ownership is ${hh.level}: about the same as ${fmt(Math.max(1, Math.round(hh.eq)))} equal-sized owners sharing every traced pub (HHI ${fmt(hh.pts)}).` : ''}</p>
        <p class="pb-cav">We could not link ${fmt(unk)} of ${fmt(L.total)} pubs (${pct(unk / L.total)}) to any owner. That does not make them independent: many will belong to groups that open data doesn't show yet. Independents are “likely”, not confirmed. HHI is the standard concentration measure, used here on pub counts; it is not a competition ruling. HHI is likely an upper estimate, because groups are easier to trace than independents.</p>
      </div>
      <figure class="pb-waffle-fig">
        <div class="pb-waffle" role="img" aria-label="Of every 100 London pubs: ${CAT_ORDER.map(c => `${w.cells[c]} ${catSent(c)}`).join(', ')}.">${w.html}</div>
        <figcaption>${CAT_ORDER.map(c => `<span><i class="pb-bg-${c}"></i><b>${w.cells[c]}</b> ${esc(catName(c))}</span>`).join('')}<span class="pb-wnote">Each square is 1 in 100 of all ${fmt(L.total)} pubs on the map. Grey means no group link found, not independent.</span></figcaption>
      </figure>
    </section>`);
  } else parts.push('<p class="pb-emptybox">We could not link any pubs to an owner yet, so there is no ownership mix to show.</p>');

  // ---- 2. Group league table
  const G = I.groups.map(g => ({ ...g, meta: S.groups[g.id], share: known ? g.n / known : null, caskShare: g.n ? (g.cask_n || 0) / g.n : null }));
  if (G.length) {
    const t = G[0], t5 = G.slice(0, 5).reduce((a, g) => a + g.n, 0);
    parts.push(card('league', 'Who owns the most', `<b>${esc(t.meta.name)}</b> is the biggest owner we can see, with ${plural(t.n, 'pub')}. The top five hold ${pct(t5 / known)} of traced pubs.`,
      `<div class="tablewrap pb-league"><table><thead><tr></tr></thead><tbody></tbody></table></div>`,
      `Counts include only pubs we could link, so real totals are higher. Tenanted pubs are owned by the group but run by a tenant. ${esc(regNames().join(' and '))}'s open licence register links more of its pubs to an owner than other boroughs manage, so groups strong there can look bigger. Tap a group to see its pubs on the map.`));
  }

  // ---- 3. Boroughs
  const B = I.boroughs;
  if (B.length) {
    const tot = B.reduce((t, b) => t + b.total, 0), known = B.reduce((t, b) => t + (b.total - (b.by_category?.unknown || 0)), 0);
    const most = B.slice().sort((a, b) => b.total - a.total)[0];
    const reg = (S.openReg || []).filter(n => B.some(b => b.borough === n));
    const regTxt = reg.length ? ` ${esc(reg.map(bName).join(' and '))} ${reg.length === 1 ? 'shows' : 'show'} more likely independents because ${reg.length === 1 ? 'its' : 'their'} licensing register is open data, which lets us identify small operators. That does not necessarily mean ${reg.length === 1 ? 'it has' : 'they have'} more independents than other boroughs.` : '';
    parts.push(card('boroughs', 'Ownership by borough', `We could link ${pct(tot ? known / tot : 0)} of pubs on the map to an owner; the rest show as “No group link found”. ${most ? `${esc(bName(most.borough))} has the most pubs (${fmt(most.total)}).` : ''}${regTxt}`,
      `<div class="pb-ctl"><div class="seg pb-seg" role="group" aria-label="Bar length"><button type="button" data-bview="count" aria-pressed="true">Number of pubs</button><button type="button" data-bview="share" aria-pressed="false">Share of traced pubs</button></div>
       <label class="pb-sortlab">Sort by <select id="pb-bsort"><option value="total">Total pubs</option><option value="indie">Likely independents</option><option value="share">Group share of traced</option><option value="az">Name</option></select></label></div>
       ${legendHtml(CAT_ORDER, catName)}<div class="pb-chart" data-chart="boroughs"></div>`,
      `In share mode, pubs with no group link are left out. Group pubs are easy to trace from brand tags, but independents mostly only show up through a licensing register, so in most boroughs the share is close to 100% almost by construction.${reg.length ? ` ${esc(reg.map(bName).join(' and '))} ${reg.length === 1 ? 'is' : 'are'} lower partly because ${reg.length === 1 ? 'its' : 'their'} licensing register is open.` : ''} Tap a borough to explore it on the map.`,
      tableHtml('Pubs by borough and owner type', ['Borough', 'Pubs', 'Traced', 'Group share of traced', ...CAT_ORDER.map(catName)], B.map(b => [bName(b.borough), fmt(b.total), fmt(b.total - (b.by_category?.unknown || 0)), pct(b.group_share), ...CAT_ORDER.map(c => fmt(b.by_category?.[c] || 0))]))));
  }

  // ---- 4 + 5. Cask and hygiene by owner type, side by side
  const C = I.by_category || {};
  const pair = [];
  const caskRows = CAT_ORDER.filter(c => C[c]?.n);
  if (caskRows.length) {
    const sh = c => C[c].n ? (C[c].cask_n || 0) / C[c].n : 0;
    const best = caskRows.slice().sort((a, b) => sh(b) - sh(a))[0], tagged = caskRows.reduce((a, c) => a + (C[c].cask_known || 0), 0), tot = caskRows.reduce((a, c) => a + C[c].n, 0);
    pair.push(card('cask', 'Cask ale by owner type', `${esc(catName(best))} pubs are the most often tagged as serving cask ale: ${pct(sh(best))}.`,
      `<div class="pb-chart" data-chart="cask"></div>`,
      `Only ${fmt(tagged)} of ${fmt(tot)} pubs (${pct(tagged / tot)}) say anything about cask ale on OpenStreetMap, so this shows what volunteers have tagged as much as what is on the bar.`,
      tableHtml('Cask ale by owner type', ['Owner type', 'Pubs', 'Tagged cask', 'Share'], caskRows.map(c => [catName(c), fmt(C[c].n), fmt(C[c].cask_n || 0), pct(sh(c))]))));
  }
  const ratRows = CAT_ORDER.filter(c => C[c]?.rating_n > 0);
  if (ratRows.length) {
    const five = c => (C[c].rating_dist?.['5'] || 0) / C[c].rating_n;
    const srt = ratRows.slice().sort((a, b) => five(b) - five(a)), hi = srt[0], lo = srt[srt.length - 1];
    const X = C.ex_camden && typeof C.ex_camden === 'object' ? C.ex_camden : null;
    const fiveX = c => X?.[c]?.rating_n > 0 ? (X[c].rating_dist?.['5'] || 0) / X[c].rating_n : null;
    const outside = srt.length > 1 && fiveX(hi) != null && fiveX(lo) != null ? ` Outside ${esc(regNames().join(' and '))}: ${pct(fiveX(hi))} against ${pct(fiveX(lo))} (of ${fmt(X[hi].rating_n)} and only ${fmt(X[lo].rating_n)} rated pubs).` : '';
    pair.push(card('hygiene', 'Food hygiene by owner type', srt.length > 1 ? `${pct(five(hi))} of ${esc(catSent(hi))} pubs with a rating score the top 5, against ${pct(five(lo))} of ${esc(catSent(lo))} pubs.${outside}` : `${pct(five(hi))} of rated pubs score the top 5.`,
      `${legendHtml(RATINGS.map(r => 'r' + r), k => k.slice(1))}<div class="pb-chart" data-chart="hygiene"></div>`,
      `FSA ratings measure food safety on the day of inspection, not beer or service. Pubs that serve no food are often exempt and are left out. ${camdenNote()}`,
      tableHtml('Food hygiene rating by owner type', ['Owner type', 'Rated', 'Average', ...RATINGS.map(r => 'Score ' + r)], ratRows.map(c => [catName(c), fmt(C[c].rating_n), C[c].rating_avg != null ? (+C[c].rating_avg).toFixed(2) : '–', ...RATINGS.map(r => fmt(C[c].rating_dist?.[r] || 0))]))));
  }
  if (pair.length) parts.push(`<div class="pb-pair">${pair.join('')}</div>`);

  // ---- 6. Deprivation
  const D = Array.isArray(I.deprivation) ? I.deprivation.filter(d => d && d.decile >= 1 && d.decile <= 10).sort((a, b) => a.decile - b.decile) : null;
  if (D && D.length) {
    const share = rows => { let g = 0, k = 0; for (const d of rows) for (const c of CAT_ORDER) if (c !== 'unknown') { k += d.by_category?.[c] || 0; if (GROUPISH.includes(c)) g += d.by_category?.[c] || 0; } return k ? g / k : null; };
    const a = share(D.filter(d => d.decile <= 3)), b = share(D.filter(d => d.decile >= 8));
    parts.push(card('dep', 'Ownership and deprivation', a != null && b != null ? `In London's most deprived areas (deciles 1 to 3) groups own ${pct(a)} of traced pubs; in the least deprived (8 to 10) they own ${pct(b)}.` : 'The ownership mix of traced pubs in each deprivation decile.',
      `${legendHtml(CAT_ORDER.filter(c => c !== 'unknown'), catName)}<div class="pb-chart" data-chart="dep"></div>`,
      'Deprivation is the 2025 Index of Multiple Deprivation for the small area each pub is in (1 = most deprived tenth of England), not a measure of its customers. Shares leave out pubs with no group link. This shows a pattern, not a cause. ' + camdenNote(),
      tableHtml('Ownership by deprivation decile', ['Decile', 'Pubs', ...CAT_ORDER.map(catName)], D.map(d => [String(d.decile), fmt(d.total), ...CAT_ORDER.map(c => fmt(d.by_category?.[c] || 0))]))));
  }

  // ---- 7. Churn
  const CH = I.churn && I.churn.by_category ? I.churn : null;
  if (CH) {
    const rows = CAT_ORDER.filter(c => CH.by_category[c]).map(c => ({ c, ...CH.by_category[c] }));
    const nN = rows.reduce((a, r) => a + (r.new || 0), 0), nR = rows.reduce((a, r) => a + (r.removed || 0), 0);
    parts.push(card('churn', 'Openings and closures on the hygiene register', `In the last ${plural(CH.window_days, 'day')}, the FSA register gained ${plural(nN, 'pub record')} and lost ${fmt(nR)} among the pubs on this map.`,
      `<div class="pb-chart" data-chart="churn"></div>`,
      'A new or removed record often means a change of operator or a re-registration, not a pub opening or closing for good. Only pubs linked to an FSA record are counted.',
      tableHtml('Register changes by owner type', ['Owner type', 'New', 'Removed', 'Net'], rows.map(r => [catName(r.c), fmt(r.new || 0), fmt(r.removed || 0), (r.new - r.removed > 0 ? '+' : '') + fmt((r.new || 0) - (r.removed || 0))]))));
    CHART.churn = w => hbarsPair(rows, w);
  }

  // ---- 8. Borough compare
  if (B.length >= 2) {
    const opts = sel => B.map(b => b.borough).sort((a, b) => a.localeCompare(b)).map(b => `<option value="${esc(b)}"${b === sel ? ' selected' : ''}>${esc(bName(b))}</option>`).join('');
    parts.push(`<section class="pb-card" id="pb-s-cmp" aria-labelledby="pb-h-cmp"><h3 id="pb-h-cmp">Compare two boroughs</h3>
      <div class="pb-cmpsel"><label>First borough<select id="pb-cmp-a">${opts(S.cmp[0])}</select></label><label>Second borough<select id="pb-cmp-b">${opts(S.cmp[1])}</select></label></div>
      <div id="pb-cmp-out"></div></section>`);
  }
  if (!S.insFile) parts.push(`<p class="note pb-calcnote">Worked out in your browser from the pubs data. Deprivation, area and register-change figures will appear when the full insights file is published.</p>`);
  const notes = S.insFile && Array.isArray(I.notes) && I.notes.length ? I.notes : NOTES;
  parts.push(`<ul class="note pb-notes">${notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>`);

  box.innerHTML = parts.join('');
  box.setAttribute('aria-busy', 'false');

  // ---- chart functions (measured width; redrawn on resize)
  CHART.boroughs = w => {
    const rows = B.slice();
    const tr = b => b.total - (b.by_category?.unknown || 0);
    const k = { share: (a, b) => (b.group_share ?? -1) - (a.group_share ?? -1), indie: (a, b) => (b.indie_n || 0) - (a.indie_n || 0), total: (a, b) => b.total - a.total, az: (a, b) => a.borough.localeCompare(b.borough) }[S.bsort];
    rows.sort((a, b) => k(a, b) || a.borough.localeCompare(b.borough));
    const share = S.bview === 'share';
    S.bRows = rows;
    const thin = b => tr(b) < 20 || (b.indie_n || 0) < 5;   // share mostly reflects what we could trace
    const cap = share ? '<p class="pb-bcap">Share of traced pubs (no group link left out). Faded rows (*) have fewer than 20 traced pubs or fewer than 5 likely independents, so their share says more about what we could trace than who owns the pubs.</p>'
      : '<p class="pb-bcap">Number of pubs, including those with no group link found.</p>';
    return cap + hbars(rows.map(b => ({
      dim: share && thin(b),
      label: bName(b.borough), total: share ? 1 : b.total,
      segs: CAT_ORDER.filter(c => !share || c !== 'unknown').map(c => [c, share ? (tr(b) ? (b.by_category?.[c] || 0) / tr(b) : 0) : (b.by_category?.[c] || 0), c]),
      value: share ? (S.bsort === 'indie' ? `${fmt(b.indie_n || 0)} indie` : `${pct(b.group_share)} groups${thin(b) ? '*' : ''}`) : (S.bsort === 'indie' ? `${fmt(b.indie_n || 0)} indie` : `${fmt(b.total)} pubs`),
      aria: `${bName(b.borough)}: ${fmt(b.total)} pubs, ${fmt(tr(b))} traced, groups own ${pct(b.group_share)}, ${fmt(b.indie_n || 0)} likely independent.${share && thin(b) ? ' Low evidence: share mostly reflects what we could trace.' : ''} Open on the map.`,
    })), w, { scale: share ? 1 : undefined, act: true, valueW: 96 });
  };
  CHART.cask = w => hbars(caskRows.map(c => { const s = C[c].n ? (C[c].cask_n || 0) / C[c].n : 0; return { label: catName(c), total: s, segs: [[c, s]], value: `${pct(s)} of ${fmt(C[c].n)}`, aria: `${catName(c)}: ${pct(s)} of ${fmt(C[c].n)} pubs tagged as serving cask ale` }; }), w,
    { scale: Math.max(.01, ...caskRows.map(c => C[c].n ? (C[c].cask_n || 0) / C[c].n : 0)) * 1.05, valueW: 104 });
  CHART.hygiene = w => hbars(ratRows.map(c => ({ label: catName(c), total: 1, segs: RATINGS.map(r => ['r' + r, (C[c].rating_dist?.[r] || 0) / C[c].rating_n]), value: `avg ${C[c].rating_avg != null ? (+C[c].rating_avg).toFixed(1) : '–'}`,
    aria: `${catName(c)}: ${fmt(C[c].rating_n)} rated, ${RATINGS.map(r => `${pct((C[c].rating_dist?.[r] || 0) / C[c].rating_n)} score ${r}`).join(', ')}` })), w, { scale: 1, valueW: 64 });
  if (D && D.length) CHART.dep = w => depColumns(D, w);
  drawCharts();
  drawLeague();
  drawCompare();
}

function depColumns(D, w) {   // 100% stacked columns, one per IMD decile, of traced pubs
  const H = 200, top = 20, axisH = 40, left = 34, plotW = w - left - 4, slot = plotW / D.length, bw = Math.min(28, slot - 6);
  const cats = CAT_ORDER.filter(c => c !== 'unknown');
  let g = '';
  for (const t of [0, .5, 1]) { const y = top + H - t * H; g += `<line class="pb-grid" x1="${left}" x2="${w - 4}" y1="${y}" y2="${y}"/><text class="pb-tl" x="${left - 6}" y="${y}" text-anchor="end" dominant-baseline="middle">${t * 100}%</text>`; }
  D.forEach((d, i) => {
    const known = cats.reduce((a, c) => a + (d.by_category?.[c] || 0), 0), x = left + i * slot + (slot - bw) / 2;
    let y = top + H, segs = '';
    const live = cats.filter(c => d.by_category?.[c]);
    live.forEach((c, j) => {
      const hgt = known ? d.by_category[c] / known * H : 0, last = j === live.length - 1, sh = Math.max(last ? hgt : hgt - 2, .5);
      y -= hgt;
      segs += last ? `<path class="pb-f-${c}" d="${roundTop(x, y, bw, sh, 4)}"/>` : `<rect class="pb-f-${c}" x="${x.toFixed(1)}" y="${(y + 2).toFixed(1)}" width="${bw.toFixed(1)}" height="${sh.toFixed(1)}"/>`;
    });
    const gs = known ? GROUPISH.reduce((a, c) => a + (d.by_category?.[c] || 0), 0) / known : null;
    g += `<g class="pb-row"><rect class="pb-hit" x="${(left + i * slot).toFixed(1)}" y="${top - 16}" width="${slot.toFixed(1)}" height="${H + 16 + 22}" rx="6" tabindex="0" role="img" aria-label="Decile ${d.decile}: ${fmt(d.total)} pubs, ${fmt(known)} traced, groups own ${pct(gs)}"/>${segs}
      <text class="pb-tl" x="${(left + i * slot + slot / 2).toFixed(1)}" y="${top + H + 16}" text-anchor="middle">${d.decile}</text></g>`;
  });
  g += `<text class="pb-tl" x="${left}" y="${top + H + 34}">1 = most deprived</text><text class="pb-tl" x="${w - 4}" y="${top + H + 34}" text-anchor="end">10 = least deprived</text>`;
  return `<svg class="pb-svg" width="${w}" height="${top + H + axisH}" viewBox="0 0 ${w} ${top + H + axisH}">${g}</svg>`;
}
function hbarsPair(rows, w) {   // churn: new (right) and removed (left) from a shared centre line
  const narrow = w < 560, labelW = narrow ? 0 : 170, rowH = narrow ? 40 : 28, BH = 12, mid = labelW + (w - labelW) / 2, half = (w - labelW) / 2 - 44;
  const mx = Math.max(1, ...rows.flatMap(r => [r.new || 0, r.removed || 0]));
  let g = `<text class="pb-tl" x="${mid - 6}" y="10" text-anchor="end">Removed</text><text class="pb-tl" x="${mid + 6}" y="10">New</text>`;
  rows.forEach((r, i) => {
    const y0 = 18 + i * rowH, by = narrow ? y0 + 20 : y0 + (rowH - BH) / 2, wn = (r.new || 0) / mx * half, wr = (r.removed || 0) / mx * half;
    g += `<g class="pb-row"><rect class="pb-hit" x="0" y="${y0}" width="${w}" height="${rowH}" rx="6" tabindex="0" role="img" aria-label="${svgEsc(catName(r.c))}: ${fmt(r.new || 0)} new, ${fmt(r.removed || 0)} removed"/>
      <text class="pb-tl" x="${narrow ? 0 : labelW - 10}" y="${narrow ? y0 + 12 : y0 + rowH / 2}" text-anchor="${narrow ? 'start' : 'end'}" dominant-baseline="middle">${svgEsc(catName(r.c))}</text>
      ${wn ? `<path class="pb-f-${r.c}" d="${roundRight(mid + 1, by, wn, BH, 4)}"/>` : ''}${wr ? `<path class="pb-f-${r.c} pb-ghost" transform="translate(${2 * mid - 1},0) scale(-1,1)" d="${roundRight(mid, by, wr, BH, 4)}"/>` : ''}
      <text class="pb-tv" x="${mid + wn + 6}" y="${by + BH / 2}" dominant-baseline="middle">${fmt(r.new || 0)}</text><text class="pb-tv" x="${mid - wr - 6}" y="${by + BH / 2}" text-anchor="end" dominant-baseline="middle">${fmt(r.removed || 0)}</text></g>`;
  });
  const h = 18 + rows.length * rowH + 4;
  return `<svg class="pb-svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><line class="pb-axis" x1="${mid}" x2="${mid}" y1="14" y2="${h}"/>${g}</svg>`;
}

function drawLeague() {
  const wrap = document.querySelector('.pb-league'); if (!wrap || !S.ins) return;
  const I = S.ins, by = I.london.by_category || {}, known = I.london.known || (I.london.total - (by.unknown || 0));
  const G = I.groups.map(g => ({ ...g, name: S.groups[g.id].name, cat: S.groups[g.id].category, share: known ? g.n / known : 0, cask: g.n ? (g.cask_n || 0) / g.n : 0, rating: g.rating_avg ?? -1 }));
  const [k, dir] = S.lsort, mx = Math.max(...G.map(g => g.n));
  G.sort((a, b) => (k === 'name' ? a.name.localeCompare(b.name) : k === 'cat' ? CAT_ORDER.indexOf(a.cat) - CAT_ORDER.indexOf(b.cat) : (a[k] ?? 0) - (b[k] ?? 0)) * dir || b.n - a.n);
  const cols = [['name', 'Group', 1], ['cat', 'Type', 1], ['n', 'Pubs', -1], ['share', 'Share of traced', -1], ['boroughs', 'Boroughs', -1], ['cask', 'Cask tagged', -1], ['rating', 'Avg hygiene', -1]];
  wrap.querySelector('thead tr').innerHTML = cols.map(([key, lab, d0], i) => `<th class="${i > 1 ? 'num ' : ''}pb-col-${key}" aria-sort="${k === key ? (dir > 0 ? 'ascending' : 'descending') : 'none'}"><button type="button" data-lsort="${key}" data-d0="${d0}">${esc(lab)}<span aria-hidden="true">${k === key ? (dir > 0 ? ' ▲' : ' ▼') : ''}</span></button></th>`).join('');
  wrap.querySelector('tbody').innerHTML = G.map(g => `<tr data-g="${esc(g.id)}" style="--c:var(--pb-${esc(g.cat)})">
    <td><button type="button" class="pb-glink" data-g="${esc(g.id)}" aria-label="${esc(g.name)}: ${fmt(g.n)} pubs. Show on map"><span class="pb-shape" aria-hidden="true">${SHAPE[g.cat]}</span>${esc(g.name)}</button></td>
    <td class="pb-col-cat">${esc(catName(g.cat))}</td>
    <td class="num pb-col-n"><span class="pb-inbar"><i class="pb-bg-${esc(g.cat)}" style="width:${(g.n / mx * 100).toFixed(1)}%"></i></span>${fmt(g.n)}</td>
    <td class="num pb-col-share">${pct(g.share, 1)}</td><td class="num pb-col-boroughs">${fmt(g.boroughs || 0)}</td>
    <td class="num pb-col-cask">${pct(g.cask)}</td><td class="num pb-col-rating">${g.rating >= 0 ? (+g.rating).toFixed(1) : '–'}</td></tr>`).join('');
}

function drawCompare() {
  const out = $('pb-cmp-out'); if (!out || !S.ins) return;
  const B = S.ins.boroughs, [a, b] = S.cmp.map(n => B.find(x => x.borough === n));
  if (!a || !b) { out.innerHTML = '<p class="note">Pick two boroughs.</p>'; return; }
  const tr = x => x.total - (x.by_category?.unknown || 0);
  const gname = id => id ? (S.groups[id]?.name || id) : '–';
  const hw = x => { const h = hhiWords(x.hhi); return h ? `like ${fmt(Math.max(1, Math.round(h.eq)))} equal owners` : '–'; };
  const rows = [
    ['Pubs', x => fmt(x.total)],
    ['Pubs per km²', x => x.pubs_per_km2 != null ? (+x.pubs_per_km2).toFixed(1) : null],
    ['Linked to an owner', x => `${fmt(tr(x))} (${pct(tr(x) / x.total)})`],
    ['Share of traced pubs owned by groups', x => pct(x.group_share)],
    ['Likely independent', x => fmt(x.indie_n || 0)],
    ['Tagged as serving cask ale', x => fmt(x.cask_n || 0)],
    ['Biggest owner', x => x.top_group ? `${gname(x.top_group)} (${fmt(x.top_group_n || 0)})` : '–'],
    ['How concentrated', hw],
    ['Average food hygiene rating', x => x.rating_avg != null ? `${(+x.rating_avg).toFixed(2)} (${fmt(x.rating_n || 0)} rated)` : null],
    ['Average deprivation decile (1 = most deprived)', x => x.imd_decile_avg != null ? (+x.imd_decile_avg).toFixed(1) : null],
    ['Average household income (MSOA, after housing costs)', x => x.income_avg != null ? '£' + fmt(Math.round(x.income_avg / 100) * 100) : null],
  ].filter(([, f]) => f(a) != null || f(b) != null);
  const bar = x => { const t = tr(x); return `<div class="pb-cmpbar" role="img" aria-label="${esc(bName(x.borough))} traced pubs: ${CAT_ORDER.filter(c => c !== 'unknown').map(c => `${x.by_category?.[c] || 0} ${catSent(c)}`).join(', ')}">${t ? CAT_ORDER.filter(c => c !== 'unknown' && x.by_category?.[c]).map(c => `<i class="pb-bg-${c}" style="flex-grow:${x.by_category[c]}"></i>`).join('') : '<em>No traced pubs</em>'}</div>`; };
  const ga = Object.entries(a.groups || {}), gb = Object.entries(b.groups || {});
  const both = ga.filter(([id]) => b.groups?.[id]).length;
  const take = `${esc(bName(a.borough))} has ${plural(a.total, 'pub')} (${pct(a.total ? tr(a) / a.total : 0)} linked to an owner) and ${esc(bName(b.borough))} has ${fmt(b.total)} (${pct(b.total ? tr(b) / b.total : 0)})${ga.length && gb.length ? `; ${plural(both, 'group')} ${both === 1 ? 'is' : 'are'} in both` : ''}.`;
  const reg = [a, b].filter(x => (S.openReg || []).includes(x.borough)).map(x => esc(bName(x.borough)));
  const regNote = reg.length && reg.length < 2 ? `<p class="pb-cav pb-regnote"><b>${reg[0]} is not a like-for-like comparison.</b> Its licensing register is open data, which lets us identify independent operators there. Other boroughs don't publish one, so ${reg[0]} shows more likely independents and a lower group share, not necessarily because it has more independents.</p>` : '';
  out.innerHTML = `${take ? `<p class="pb-take">${take}</p>` : ''}
    <div class="pb-cmpbars"><div><b>${esc(bName(a.borough))}</b>${bar(a)}</div><div><b>${esc(bName(b.borough))}</b>${bar(b)}</div></div>
    ${legendHtml(CAT_ORDER.filter(c => c !== 'unknown'), catName)}
    <div class="tablewrap"><table class="pb-cmptbl"><thead><tr><th scope="col"><span class="sr">Measure</span></th><th scope="col" class="num">${esc(bName(a.borough))}</th><th scope="col" class="num">${esc(bName(b.borough))}</th></tr></thead>
    <tbody>${rows.map(([lab, f]) => `<tr><th scope="row">${esc(lab)}</th><td class="num">${esc(f(a) ?? '–')}</td><td class="num">${esc(f(b) ?? '–')}</td></tr>`).join('')}</tbody></table></div>
    ${regNote}<p class="pb-cav">Bars show traced pubs only. Pubs with no group link found are not counted as independent. Concentration counts each group as one owner and each other traced pub as its own.</p>
    <p><button type="button" class="btn" data-explore="${esc(a.borough)}">See ${esc(bName(a.borough))} on the map</button> <button type="button" class="btn" data-explore="${esc(b.borough)}">See ${esc(bName(b.borough))} on the map</button></p>`;
}

function initInsightsEvents() {
  const box = $('pb-insights'), tip = $('pb-itip');
  const openBorough = name => { S.borough = name; S.g = ''; S.cat = ''; S.q = ''; S.qRaw = ''; S.mode = 'explore'; syncControls(); renderMode(); apply(true); writeUrl(true); $('v-pubs').scrollIntoView({ behavior: reduced() ? 'auto' : 'smooth' }); };
  box.addEventListener('click', e => {
    const t = e.target;
    const gl = t.closest('[data-g]'); if (gl && gl.closest('.pb-league')) return openGroup(gl.dataset.g);
    const ls = t.closest('[data-lsort]');
    if (ls) { const k = ls.dataset.lsort; S.lsort = S.lsort[0] === k ? [k, -S.lsort[1]] : [k, +ls.dataset.d0]; drawLeague(); box.querySelector(`[data-lsort="${k}"]`)?.focus(); return; }
    const bv = t.closest('[data-bview]');
    if (bv) { S.bview = bv.dataset.bview; box.querySelectorAll('[data-bview]').forEach(x => x.setAttribute('aria-pressed', x === bv)); drawCharts(); return; }
    const ex = t.closest('[data-explore]'); if (ex) return openBorough(ex.dataset.explore);
    const row = t.closest('[data-chart="boroughs"] .pb-row'); if (row && S.bRows) return openBorough(S.bRows[+row.dataset.i].borough);
  });
  box.addEventListener('keydown', e => {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-chart="boroughs"] .pb-hit')) { e.preventDefault(); openBorough(S.bRows[+e.target.closest('.pb-row').dataset.i].borough); }
  });
  box.addEventListener('change', e => {
    if (e.target.id === 'pb-bsort') { S.bsort = e.target.value; drawCharts(); }
    if (e.target.id === 'pb-cmp-a' || e.target.id === 'pb-cmp-b') { S.cmp = [$('pb-cmp-a').value, $('pb-cmp-b').value]; S.cmpSet = true; drawCompare(); writeUrl(false); }
  });
  // one tooltip for every chart mark: same text on hover and on keyboard focus, set with textContent
  const show = (el, x, y) => {
    const lab = el.getAttribute('aria-label'); if (!lab) return;
    const [head, ...rest] = lab.split(': ');
    tip.replaceChildren(); const b = document.createElement('b'); b.textContent = head; tip.append(b, document.createTextNode(rest.join(': ')));
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(x + 14, innerWidth - r.width - 8)) + 'px'; tip.style.top = Math.max(8, Math.min(y + 14, innerHeight - r.height - 8)) + 'px';
  };
  box.addEventListener('pointermove', e => { const h = e.target.closest?.('.pb-hit'); if (h && e.pointerType === 'mouse') show(h, e.clientX, e.clientY); else tip.hidden = true; });
  box.addEventListener('pointerleave', () => { tip.hidden = true; });
  box.addEventListener('focusin', e => { if (e.target.matches('.pb-hit')) { const r = e.target.getBoundingClientRect(); show(e.target, r.left + Math.min(r.width / 2, 200), r.top + r.height / 2); } });
  box.addEventListener('focusout', () => { tip.hidden = true; });
  addEventListener('scroll', () => { tip.hidden = true; }, { passive: true });
}
