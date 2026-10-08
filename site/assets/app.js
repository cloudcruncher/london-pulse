'use strict';
// London Pulse front end. Reads the static JSON API under api/v1/ (same files a future app can use).

const $ = id => document.getElementById(id);
const fmt = n => Number(n).toLocaleString('en-GB');
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const api = {};
const getApi = name => (api[name] ??= fetch(`api/v1/${name}.json`).then(r => { if (!r.ok) throw new Error(name); return r.json(); }));
const dateLong = iso => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });

const RATING_LABEL = { '5': '5 · very good', '4': '4 · good', '3': '3 · satisfactory', '2': '2 · improvement needed', '1': '1 · major improvement', '0': '0 · urgent improvement', AwaitingInspection: 'Awaiting inspection', Exempt: 'Exempt', AwaitingPublication: 'Awaiting publication' };
const RATING_VAR = { '5': '--r5', '4': '--r4', '3': '--r3', '2': '--r2', '1': '--r1', '0': '--r0', AwaitingInspection: '--raw', Exempt: '--rex', AwaitingPublication: '--rex' };
const TYPE_COLORS = ['#d9794b', '#3b82c4', '#7a5cc4', '#4aa89a'];
const TYPE_SHORT = { 'Restaurant/Cafe/Canteen': 'Restaurants & cafés', 'Takeaway/sandwich shop': 'Takeaways', 'Pub/bar/nightclub': 'Pubs & bars', 'Other catering premises': 'Delivery & other catering' };

// ---------- theme ----------
(() => {
  const saved = (() => { try { return localStorage.getItem('lp-theme'); } catch { return null; } })();
  if (saved) document.documentElement.dataset.theme = saved;
  $('theme').onclick = () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === 'dark'
      : matchMedia('(prefers-color-scheme: dark)').matches;
    const next = dark ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('lp-theme', next); } catch { /* private mode */ }
    if (current === 'map') { mapRedraw(); import('./mapbox.js').then(m => m.restyleGl()); }
  };
})();

// ---------- count-up numbers ----------
function countUp(el, to, suffix = '') {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) { el.textContent = fmt(to) + suffix; return; }
  const t0 = performance.now();
  const step = t => {
    const p = Math.min(1, (t - t0) / 700);
    el.textContent = fmt(Math.round(to * (1 - Math.pow(1 - p, 3)))) + suffix;
    if (p < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ---------- router ----------
const VIEWS = ['overview', 'insights', 'map', 'area', 'changes', 'boroughs', 'brands', 'craft', 'sql', 'about'];
const inited = {};
let current = null;

function route() {
  const [name, ...rest] = (location.hash.replace('#', '') || 'overview').split('/'), arg = rest.join('/') || undefined;
  const view = VIEWS.includes(name) ? name : 'overview';
  VIEWS.forEach(v => { $('v-' + v).hidden = v !== view; });
  document.querySelectorAll('.tabs a').forEach(a => a.classList.toggle('on', a.dataset.tab === view));
  current = view;
  document.body.dataset.view = view;
  if (!inited[view]) { inited[view] = true; init[view]?.(); }
  if (view === 'map') requestAnimationFrame(() => { mapResize(); if (arg) mapFocusBorough(dec(arg)); });
  if (view === 'brands' && inited.brandsReady) brandOpen(arg);
  if (view === 'area' && inited.areaReady) areaOpen((arg || '').split('/'));
  if (view === 'map' && arg?.startsWith('q=')) { mapSearch(dec(arg.slice(2))); }
  if (view === 'sql' && arg) sqlMod?.then(m => m.openArg(arg)).catch(() => {});
  if (view !== 'boroughs') closeDrawer();
  window.scrollTo({ top: 0 });
}
addEventListener('hashchange', route);

// ---------- overview ----------
const init = {};
init.overview = async () => {
  const [sum, bor, ev] = await Promise.all([getApi('summary'), getApi('boroughs'), getApi('events')]);
  $('asof').textContent = `Updated ${dateLong(sum.as_of)}`;
  const t = sum.totals, B = bor.boroughs;
  const rated = sum.rating_distribution.filter(r => /^[0-5]$/.test(r.rating)).reduce((a, r) => a + r.n, 0);
  const five = sum.rating_distribution.find(r => r.rating === '5')?.n ?? 0;
  const kp = [[t.premises, 'food businesses registered', ''], [t.eating_drinking, 'restaurants, cafés, takeaways and pubs', ''],
    [Math.round(five / rated * 100), 'of rated venues score the top 5', '%'], [t.awaiting, 'registered, awaiting first inspection', ''], [t.low_rated, 'rated 0–2, improvement needed', '']];
  $('kpis').innerHTML = kp.map(([, s]) => `<div class="kpi"><b>0</b><small>${s}</small></div>`).join('');
  $('kpis').querySelectorAll('b').forEach((b, i) => countUp(b, kp[i][0], kp[i][2]));

  // auto-written insights
  const big = B.filter(b => b.premises >= 300);
  const top = (k, d = -1) => [...big].sort((a, b) => d * (a[k] - b[k]))[0];
  const avgAwait = B.reduce((a, b) => a + b.awaiting, 0) / B.reduce((a, b) => a + b.premises, 0) * 100;
  const hiA = top('awaiting_pct'), hiF = top('five_star_pct'), loF = top('five_star_pct', 1), hiC = top('coffee_named'), hiL = top('low_rated');
  const card = (h, p, hash) => `<a class="insight" href="${hash}" style="text-decoration:none;color:inherit"><h3>${h}</h3><p>${p}</p></a>`;
  $('insights').innerHTML = [
    card('Newest-looking borough', `${esc(hiA.name)} has the highest share of venues awaiting a first inspection: ${hiA.awaiting_pct}% against ${avgAwait.toFixed(1)}% across London.`, `#map/${encodeURIComponent(hiA.name)}`),
    card('Highest hygiene standard', `${esc(hiF.name)} leads, with ${hiF.five_star_pct}% of rated venues scoring 5. ${esc(loF.name)} is lowest at ${loF.five_star_pct}%.`, '#boroughs'),
    card('Coffee country', `${esc(hiC.name)} has the most venues with "coffee" or similar in the name: ${hiC.coffee_named}.`, '#craft'),
    card('Most low ratings', `${esc(hiL.name)} has ${hiL.low_rated} venues rated 0–2, the most of any borough.`, `#map/${encodeURIComponent(hiL.name)}`),
  ].join('');

  // data stories (computed in the pipeline; each links to the query behind it)
  const st = sum.stories;
  if (st) {
    const [t1, t2] = [st.by_type[0], st.by_type[st.by_type.length - 1]];
    const nm = x => esc(TYPE_SHORT[x.type] || x.type).toLowerCase(), cap = t => t[0].toUpperCase() + t.slice(1);
    const story = (big, h, p, key) => `<a class="insight" href="#sql/${key}" style="text-decoration:none;color:inherit"><span class="big">${big}</span><h3>${h}</h3><p>${p}</p></a>`;
    $('stories').innerHTML = [
      story(`${st.stale.london_pct}%`, 'Of ratings are over two years old', `${esc(st.stale.worst[0].name)} is the stalest at ${st.stale.worst[0].pct_stale}%, followed by ${esc(st.stale.worst[1].name)} and ${esc(st.stale.worst[2].name)}. ${esc(st.stale.best.name)} is the freshest at ${st.stale.best.pct_stale}%. A rating is only as current as its last visit.`, 'stale'),
      story(`${t1.five_star_pct}% vs ${t2.five_star_pct}%`, 'The hygiene gap between venue types', `${cap(nm(t1))} score 5 ${t1.five_star_pct}% of the time; ${nm(t2)} only ${t2.five_star_pct}%, and are ${(t2.low_pct / t1.low_pct).toFixed(1)}x as likely to be rated 0–2.`, 'by-type'),
      story(`${st.takeaway.highest.takeaway_pct}%`, 'Takeaway share, high to low', `${esc(st.takeaway.highest.name)} is ${st.takeaway.highest.takeaway_pct}% takeaways; ${esc(st.takeaway.lowest.name)} just ${st.takeaway.lowest.takeaway_pct}%. A quick read on what a high street is for.`, 'takeaway'),
      story(esc(st.awaiting_hotspots[0].district), 'Where new openings cluster', `${esc(st.awaiting_hotspots[0].district)} has ${st.awaiting_hotspots[0].awaiting} venues awaiting a first inspection, then ${st.awaiting_hotspots.slice(1, 4).map(d => esc(d.district)).join(', ')}.`, 'awaiting'),
      story(`${st.weak_districts[0].low_pct}%`, 'Weakest postcode district', `${esc(st.weak_districts[0].district)} has ${st.weak_districts[0].low_pct}% of rated venues at 0–2, against ${Math.min(...st.by_type.map(t => t.low_pct))}–${Math.max(...st.by_type.map(t => t.low_pct))}% across venue types. Districts with 150+ rated venues only.`, 'weak-districts'),
      story(`${st.top_names_share}%`, 'No brand dominates', `The five most common names (${st.top_names.map(n => esc(n.name.toLowerCase().replace(/\b\w/g, c => c.toUpperCase()))).slice(0, 3).join(', ')} …) together make up just ${st.top_names_share}% of venues, so London's food scene is a long tail of independents. (Counts exact names, so it understates chains with varied names.)`, 'names'),
    ].join('');
  }

  // changes
  const e = ev.events;
  if (!e.days) {
    $('changes').innerHTML = `<p style="margin:0"><b>Tracking started ${dateLong(sum.as_of)}.</b> Each day's register is compared with the day before. From tomorrow, new premises, removals and rating changes appear here by borough.</p>`;
  } else {
    const sumKind = k => e.by_day.filter(r => r.event === k).reduce((a, r) => a + Number(r.n), 0);
    let h = `<p style="margin-top:0">Last ${e.days} day${e.days > 1 ? 's' : ''}: <b>${fmt(sumKind('new'))}</b> new · <b>${fmt(sumKind('removed'))}</b> removed · <b>${fmt(sumKind('rating_changed'))}</b> re-rated (all premises).</p><div class="cols">`;
    for (const [k, label] of [['new', 'New eating and drinking premises'], ['removed', 'Removed from register'], ['rating_changed', 'Re-rated']]) {
      const items = e.recent[k];
      h += `<div><h3 style="margin-top:0">${label}</h3><ul class="list">${items.length ? items.slice(0, 8).map(i =>
        `<li>${esc(i.name)}<span>${esc(i.authority)}${k === 'rating_changed' ? ` · ${esc(i.old_rating)} → ${esc(i.new_rating)}` : ''}</span></li>`).join('') : '<li><span>None yet</span></li>'}</ul></div>`;
    }
    $('changes').innerHTML = h + '</div>';
  }

  const total = sum.rating_distribution.reduce((a, r) => a + r.n, 0);
  $('dist').innerHTML = sum.rating_distribution.map(r => `<div><span>${RATING_LABEL[r.rating] ?? esc(r.rating)}</span>
    <span class="track"><span class="fill" style="width:${(r.n / total * 100).toFixed(1)}%;background:var(${RATING_VAR[r.rating] ?? '--rex'})"></span></span><span class="num">${fmt(r.n)}</span></div>`).join('');
  $('newest').innerHTML = sum.newest_unrated.slice(0, 10).map(n =>
    `<li>${esc(n.name)}<span>${esc(n.authority)} · ${esc(n.postcode)}</span></li>`).join('');
};

// ---------- boroughs ----------
init.boroughs = async () => {
  const [bor, hist] = await Promise.all([getApi('boroughs'), getApi('history')]);
  const B = bor.boroughs;
  const cols = [['name', 'Borough'], ['premises', 'Venues'], ['five_star_pct', '% rated 5'], ['low_rated', 'Rated 0–2'], ['awaiting_pct', '% awaiting'], ['coffee_named', 'Coffee-named']];
  let key = 'premises', dir = -1;
  const mx = k => Math.max(...B.map(b => b[k]));
  const render = () => {
    const rows = [...B].sort((a, b) => key === 'name' ? -dir * a.name.localeCompare(b.name) : dir * (a[key] - b[key]));
    $('btable').innerHTML = '<thead><tr>' + cols.map(([k, l]) => `<th data-k="${k}" class="${k === key ? 's' : ''}">${l}</th>`).join('') + '</tr></thead><tbody>' +
      rows.map(b => `<tr data-b="${esc(b.name)}">` + cols.map(([k]) => k === 'name' ? `<td>${esc(b.name)}</td>` :
        (k === 'premises' || k === 'coffee_named') ? `<td>${fmt(b[k])}<span class="mini" style="width:${Math.round(b[k] / mx(k) * 48)}px"></span></td>` :
        `<td>${k.endsWith('pct') ? b[k] + '%' : fmt(b[k])}</td>`).join('') + '</tr>').join('') + '</tbody>';
    $('btable').querySelectorAll('th').forEach(th => th.onclick = () => { const k = th.dataset.k; dir = k === key ? -dir : -1; key = k; render(); });
    $('btable').querySelectorAll('tbody tr').forEach(tr => tr.onclick = () => openDrawer(tr.dataset.b, B, hist.history));
  };
  render();
};

function closeDrawer() { $('drawer').hidden = true; }
async function openDrawer(name, B, history) {
  const b = B.find(x => x.name === name);
  const ev = (await getApi('events')).events;
  const mine = (ev.by_borough || []).filter(r => r.name === name);
  const evn = k => mine.filter(r => r.event === k).reduce((a, r) => a + Number(r.n), 0);
  const series = history.filter(h => h.authority === name).map(h => Number(h.eating_drinking));
  const spark = series.length > 1 ? sparkline(series) : '<p class="mutedtxt">A trend line appears once there are two or more days of data.</p>';
  const avgFive = B.reduce((a, x) => a + x.five_star_pct, 0) / B.length;
  $('drawer').hidden = false;
  $('drawer').innerHTML = `<button class="icon-btn x" id="dx" aria-label="Close">✕</button><h2>${esc(b.name)}</h2>
    <div class="kpis" style="grid-template-columns:1fr 1fr;margin:0 0 12px">
      <div class="kpi"><b>${fmt(b.premises)}</b><small>eating & drinking venues</small></div>
      <div class="kpi"><b>${b.five_star_pct}%</b><small>rated 5 (London avg ${avgFive.toFixed(0)}%)</small></div>
      <div class="kpi"><b>${fmt(b.awaiting)}</b><small>awaiting inspection (${b.awaiting_pct}%)</small></div>
      <div class="kpi"><b>${fmt(b.coffee_named)}</b><small>coffee-named venues</small></div></div>
    <h3>Change, last ${ev.days || 0} day${ev.days === 1 ? '' : 's'}</h3>
    <p>${ev.days ? `<b>${evn('new')}</b> new · <b>${evn('removed')}</b> removed · <b>${evn('rating_changed')}</b> re-rated` : '<span class="mutedtxt">Tracking started today.</span>'}</p>
    <h3>Venue count</h3>${spark}
    <p style="margin-top:20px"><a class="btn primary" href="#map/${encodeURIComponent(b.name)}">See ${esc(b.name)} on the map</a></p>`;
  $('dx').onclick = closeDrawer;
}
function sparkline(v) {
  const w = 300, h = 56, min = Math.min(...v), max = Math.max(...v), r = max - min || 1;
  const pts = v.map((y, i) => `${(i / (v.length - 1) * w).toFixed(1)},${(h - 6 - (y - min) / r * (h - 12)).toFixed(1)}`).join(' ');
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="var(--accent)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
// ---------- craft drinks (Companies House) ----------
init.craft = async () => {
  let data;
  try { data = await getApi('companies'); } catch { $('cbody').innerHTML = '<div class="card">Company data is not available yet.</div>'; return; }
  const keys = Object.keys(data.groups);
  let sel = keys[0];
  const seg = () => { $('cseg').innerHTML = keys.map(k => `<button class="${k === sel ? 'on' : ''}" data-k="${k}">${esc(data.groups[k].label)}</button>`).join('');
    $('cseg').querySelectorAll('button').forEach(b => b.onclick = () => { sel = b.dataset.k; seg(); body(); }); };
  const body = () => {
    const g = data.groups[sel];
    const max = Math.max(1, ...g.by_month.map(m => m.n));
    const bw = 100 / Math.max(g.by_month.length, 1);
    const bars = g.by_month.map((m, i) => { const h = m.n / max * 120; return `<g><title>${m.month}: ${m.n} new</title><rect x="${(i * bw + bw * .15).toFixed(2)}%" y="${140 - h}" width="${(bw * .7).toFixed(2)}%" height="${h}"/>
      ${i % 3 === 0 ? `<text x="${(i * bw + bw / 2).toFixed(2)}%" y="160" text-anchor="${i === 0 ? 'start' : 'middle'}">${MONTHS[+m.month.slice(5) - 1]} ${m.month.slice(2, 4)}</text>` : ''}</g>`; }).join('');
    $('cbody').innerHTML = `<div class="kpis" style="margin-top:0"><div class="kpi"><b>${fmt(g.active)}</b><small>active London companies (SIC ${g.sic})</small></div>
      <div class="kpi"><b>${fmt(g.formed_last_12m)}</b><small>formed in the last 12 months</small></div></div>
      <h2>New companies per month</h2><div class="card"><svg class="chart" width="100%" height="170">${bars}</svg></div>
      <div class="cols"><div><h2>Where they register</h2><div class="card"><ul class="list">${g.top_districts.map(d => `<li>${esc(d.district)}<span>${d.n}</span></li>`).join('')}</ul></div></div>
      <div><h2>Newest companies</h2><div class="card"><ul class="list">${g.recent.map(r => `<li><a href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.name)}</a><span>${esc(r.postcode)} · ${esc(r.incorporated)}</span></li>`).join('')}</ul></div></div></div>
      <p class="note" style="margin-top:14px">${esc(data.note)} Snapshot ${dateLong(data.as_of)}. Source: Companies House.</p>`;
  };
  seg(); body();
};

// ---------- insights ----------
const KIND_LABEL = { signature: 'Signature', stage: 'Where it is heading', chains: 'Chains and independents', fresh: 'Fresh supply', pace: 'Pace of change',
  momentum: 'Momentum', district: 'Hotspot', mix: 'Changing mix', season: 'Seasonality', survival: 'Survival', who: 'Who is opening' };
const STAGE_CLASS = { 'Hot and still growing': 'stg-hot', 'Established scene': 'stg-est', Emerging: 'stg-emerging', Steady: '' };
const STAGE_HELP = { 'Hot and still growing': 'strong specialty scene and lots of new supply', 'Established scene': 'strong scene, little new supply',
  Emerging: 'lots of new supply, scene not formed yet', Steady: 'no strong signal either way' };
const signed = n => (n > 0 ? '+' : '') + n;
const headlineCard = h => {
  const inner = `<h3>${esc(KIND_LABEL[h.kind] || 'Insight')}</h3><p>${esc(h.text)}</p>`;
  return h.area ? `<a class="insight k" href="#area/${encodeURIComponent(h.area)}">${inner}</a>` : `<div class="insight k">${inner}</div>`;
};
init.insights = async () => {
  const [ch, op] = await Promise.all([getApi('character').catch(() => null), getApi('operators').catch(() => null)]);
  if (!ch && !op) { $('ins-reads').innerHTML = '<div class="card">Insights are not available yet. They appear after the next daily update.</div>'; return; }
  const reads = [...(ch?.headlines || []), ...(op?.headlines || [])];
  const drawReads = all => { $('ins-reads').innerHTML = (all ? reads : reads.slice(0, 9)).map(headlineCard).join('');
    $('ins-reads-more').innerHTML = !all && reads.length > 9 ? `<p><button class="btn" id="ins-reads-all">Show all ${reads.length} reads</button></p>` : '';
    if ($('ins-reads-all')) $('ins-reads-all').onclick = () => drawReads(true); };
  drawReads(false);

  if (ch) {
    let level = 'boroughs', stage = 'All', showAll = false;
    const draw = () => {
      $('ins-level').innerHTML = [['boroughs', 'Boroughs'], ['districts', 'Postcode districts']].map(([k, l]) => `<button class="${k === level ? 'on' : ''}" data-k="${k}">${l}</button>`).join('');
      $('ins-level').querySelectorAll('button').forEach(b => b.onclick = () => { level = b.dataset.k; stage = 'All'; showAll = false; draw(); });
      const all = ch[level], stages = ['All', ...Object.keys(STAGE_CLASS)];
      $('ins-stages').innerHTML = stages.map(st => `<button class="chip ${st === stage ? 'on' : ''}" data-s="${esc(st)}" title="${esc(STAGE_HELP[st] || '')}">${esc(st)}${st === 'All' ? '' : ' · ' + all.filter(a => a.stage === st).length}</button>`).join('');
      $('ins-stages').querySelectorAll('button').forEach(b => b.onclick = () => { stage = b.dataset.s; showAll = false; draw(); });
      const rows = all.filter(a => stage === 'All' || a.stage === stage);
      const shown = showAll ? rows : rows.slice(0, 25);
      $('ins-areas').innerHTML = shown.map(a => `<li><div class="at"><a href="#area/${encodeURIComponent(a.name)}"><b>${esc(a.name)}</b></a><span class="stg ${STAGE_CLASS[a.stage]}">${esc(a.stage)}</span></div>
        <div class="sig">${a.signature.length ? a.signature.map(m => `<span class="sigchip">${esc(m.label)} ${m.lq}×</span>`).join('') : '<span class="note" style="margin:0">No standout type</span>'}</div>
        <div class="ameta">${fmt(a.venues)} venues · scene ${a.scene_per_100} per 100 · new supply ${a.fresh_pct}% · tracked chains ${a.chain_pct}%</div></li>`).join('') || '<li><span>None</span></li>';
      $('ins-more').innerHTML = rows.length > 25 && !showAll ? `<p><button class="btn" id="ins-all">Show all ${rows.length}</button></p>` : '';
      if ($('ins-all')) $('ins-all').onclick = () => { showAll = true; draw(); };
    };
    draw();
  } else { $('ins-areas').innerHTML = '<li><span>Not available yet</span></li>'; }

  if (op) {
    const M = op.momentum, keys = Object.keys(M);
    $('ins-mom').innerHTML = keys.map(k => {
      const m = M[k], max = Math.max(1, ...m.by_month.map(x => x.n)), bw = 100 / Math.max(m.by_month.length, 1);
      const bars = m.by_month.map((x, i) => `<rect x="${(i * bw + bw * .12).toFixed(2)}%" y="${56 - x.n / max * 52}" width="${(bw * .76).toFixed(2)}%" height="${x.n / max * 52}"><title>${esc(x.month)}: ${x.n}</title></rect>`).join('');
      return `<div class="card mom"><h3>${esc(m.label.split(' (')[0])}</h3>
        <b class="big ${m.change_pct >= 0 ? 'up' : 'down'}">${m.change_pct == null ? '–' : signed(m.change_pct) + '%'}</b>
        <span class="verdict ${m.verdict || ''}">${m.verdict || 'too few to call'}</span>
        <small>${fmt(m.last6)} new companies in 6 months vs ${fmt(m.prior6)} before · ${m.yoy_pct == null ? '' : signed(m.yoy_pct) + '% on the year · '}${m.prior_cohort_winding_pct ?? '–'}% of last year's cohort already winding down</small>
        <svg class="chart" width="100%" height="56" aria-hidden="true">${bars}</svg></div>`;
    }).join('');
    const D = op.districts.filter(d => d.change_pct != null && d.formed_12m >= 40);
    const li = (d, right) => `<li><a href="#area/${encodeURIComponent(d.district)}">${esc(d.district)}</a><span>${right}</span></li>`;
    $('ins-up').innerHTML = [...D].sort((a, b) => b.change_pct - a.change_pct).slice(0, 8).map(d => li(d, `${d.last6} vs ${d.prior6} · ${signed(d.change_pct)}%`)).join('');
    $('ins-down').innerHTML = [...D].sort((a, b) => a.change_pct - b.change_pct).slice(0, 8).map(d => li(d, `${d.last6} vs ${d.prior6} · ${signed(d.change_pct)}%`)).join('');
    $('ins-mix').innerHTML = op.districts.filter(d => d.formed_12m >= 40).sort((a, b) => b.mix_shift_pts - a.mix_shift_pts).slice(0, 8)
      .map(d => li(d, `${d.mix_new_pct}% of new vs ${d.mix_stock_pct}% of existing`)).join('');
    $('ins-wind').innerHTML = op.districts.filter(d => d.winding_pct != null).sort((a, b) => b.winding_pct - a.winding_pct).slice(0, 8)
      .map(d => li(d, `${d.winding_pct}% of food and drink companies`)).join('');

    const p = op.new_company_profile, w = op.who_is_opening;
    $('ins-who-note').textContent = `From Companies House. ${w.matched} of ${fmt(w.new_premises_awaiting)} new premises (${w.match_rate_pct}%) could be matched to a limited company by name and district; sole traders and partnerships are not on the register. Labels such as "linked" and "likely first venue" are inferred, because the free data carries no directors.`;
    $('ins-who-kpis').innerHTML = [[fmt(p.companies), 'food and drink companies formed in the last year'], [p.standalone_pct + '%', 'stand alone: no shared name or address with another'],
      [p.linked_pct + '%', 'look linked to other food and drink companies (groups, serial operators)'], [p.prior_cohort_winding_pct + '%', 'of the year before already struck off or wound up']]
      .map(([b, s]) => `<div class="kpi"><b>${esc(b)}</b><small>${esc(s)}</small></div>`).join('');
    const bar = (rows, color) => { const max = Math.max(1, ...rows.map(r => r[1])); return rows.map(([l, n]) => `<div><span>${esc(l)}</span><span class="track"><span class="fill" style="width:${n / max * 100}%;background:${color}"></span></span><span class="num">${fmt(n)}</span></div>`).join(''); };
    const m = w.of_matched;
    $('ins-match').innerHTML = bar([['New company (under 1 year)', m.new_entrant], ['Young (1 to 3 years)', m.young], ['Established (3+ years)', m.established], ['of which linked to others', m.linked]], css('--accent'));
    $('ins-age').innerHTML = bar(p.stock_by_age.map(a => [a.age_band, a.n]), '#3b82c4');
    $('ins-examples').innerHTML = w.examples.map(e => `<li><div><a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.name)}</a><br><span class="note" style="margin:0">${esc(e.read)} · ${esc(e.company)}</span></div><span>${esc(e.postcode)}</span></li>`).join('');

    const sk = Object.keys(op.seasonality);
    let ssel = sk[0];
    const sdraw = () => {
      $('ins-season-seg').innerHTML = sk.map(k => `<button class="${k === ssel ? 'on' : ''}" data-k="${k}">${esc(op.seasonality[k].label.split(' (')[0])}</button>`).join('');
      $('ins-season-seg').querySelectorAll('button').forEach(b => b.onclick = () => { ssel = b.dataset.k; sdraw(); });
      const ms = op.seasonality[ssel].months, max = Math.max(120, ...ms.map(x => x.index)), bw = 100 / 12;
      $('ins-season').innerHTML = ms.map((x, i) => { const h = x.index / max * 120;
        return `<g><title>${MONTHS[x.month - 1]}: index ${x.index}</title><rect x="${(i * bw + bw * .15).toFixed(2)}%" y="${140 - h}" width="${(bw * .7).toFixed(2)}%" height="${h}"/>
          <text x="${(i * bw + bw / 2).toFixed(2)}%" y="160" text-anchor="middle">${MONTHS[x.month - 1]}</text></g>`; }).join('');
    };
    sdraw();
    $('ins-method').textContent = `${op.method} Companies House snapshot ${dateLong(op.as_of)}.` + (ch ? ` ${ch.method}` : '');
  } else {
    ['ins-mom', 'ins-up', 'ins-down', 'ins-mix', 'ins-wind'].forEach(id => { $(id).innerHTML = '<p class="note">Company data is not available yet.</p>'; });
  }
};

// ---------- freshness banner ----------
fetch('api/v1/status.json').then(r => r.json()).then(st => {
  const age = (Date.now() - new Date(st.generated_at)) / 36e5;
  if (age > 48) { $('stale').hidden = false; $('stale').textContent = `Data last refreshed ${dateLong(st.generated_at)}. The daily update may be delayed.`; }
}).catch(() => {});

// ---------- changes ----------
init.changes = async () => {
  const ev = (await getApi('events')).events;
  const rows = ev.latest || [];
  $('chnote').textContent = ev.days
    ? `Premises added to, removed from or re-rated on the FSA register on ${dateLong(ev.latest_date)}, compared with the day before. A removal is a signal, not proof of closure.`
    : 'Tracking has just started. Each day the register is compared with the day before; changes will appear here from the next daily update.';
  const uniq = k => [...new Set(rows.map(r => r[k]))].filter(Boolean).sort();
  $('c-borough').insertAdjacentHTML('beforeend', uniq('authority').map(a => `<option>${esc(a)}</option>`).join(''));
  $('c-type').insertAdjacentHTML('beforeend', uniq('type').map(a => `<option value="${esc(a)}">${esc(TYPE_SHORT[a] || a)}</option>`).join(''));
  const draw = () => {
    const f = ['c-borough:authority', 'c-event:event', 'c-type:type'].map(x => x.split(':')).map(([id, k]) => [$(id).value, k]);
    const out = rows.filter(r => f.every(([v, k]) => !v || r[k] === v));
    $('chlist').innerHTML = out.length ? `<ul class="list">${out.slice(0, 200).map(r => `<li><div><b class="ev-${r.event}">${{ new: '＋ New', removed: '− Removed', rating_changed: '↻ Re-rated' }[r.event]}</b> ${esc(r.name)}</div><span>${esc(r.authority)} · ${esc(r.postcode)}${r.event === 'rating_changed' ? ` · ${esc(r.old_rating)} → ${esc(r.new_rating)}` : ''}</span></li>`).join('')}</ul>${out.length > 200 ? `<p class="note">Showing 200 of ${fmt(out.length)}. Use the SQL lab for the rest.</p>` : ''}` : '<p style="margin:0">Nothing to show yet.</p>';
  };
  ['c-borough', 'c-event', 'c-type'].forEach(id => $(id).onchange = draw);
  const days = {}; (ev.by_day || []).forEach(r => { (days[r.event_date] ??= {})[r.event] = Number(r.n); });
  const keys = Object.keys(days).sort();
  $('chtrend').innerHTML = keys.length ? `<table><thead><tr><th>Day</th><th>New</th><th>Removed</th><th>Re-rated</th></tr></thead><tbody>${keys.reverse().slice(0, 14).map(d => `<tr><td>${dateLong(d)}</td><td>${fmt(days[d].new || 0)}</td><td>${fmt(days[d].removed || 0)}</td><td>${fmt(days[d].rating_changed || 0)}</td></tr>`).join('')}</tbody></table>` : '';
  draw();
};

// ---------- area guide ----------
const dec = s => { try { return decodeURIComponent(s); } catch { return s; } };
const km = (a, b, c, d) => { const r = Math.PI / 180, x = (c - a) * r * Math.cos((b + d) / 2 * r), y = (d - b) * r; return 6371 * Math.hypot(x, y); };
const BEER_RE = /brew|taproom|tap room|beer|ale house|alehouse|craft/i;
let AV = null, AB = null;
init.area = () => {
  const go = () => { const v = $('areaq').value.trim(); if (v) location.hash = '#area/' + encodeURIComponent(v) + '/' + $('arearad').value; };
  $('areago').onclick = go; $('areaq').onkeydown = e => { if (e.key === 'Enter') go(); };
  $('arearad').onchange = () => { if ($('areaq').value.trim()) go(); };
  $('arealoc').onclick = () => navigator.geolocation
    ? navigator.geolocation.getCurrentPosition(p => areaRun({ lon: p.coords.longitude, lat: p.coords.latitude, label: 'your location' }, +$('arearad').value),
        () => { $('areaout').innerHTML = '<p class="note">Location permission was declined. Type a postcode instead.</p>'; })
    : null;
  inited.areaReady = true;
  areaOpen(location.hash.split('/').slice(1));
};
async function areaOpen([q, rad]) {
  if (!q) return;
  q = dec(q); $('areaq').value = q; if (rad) $('arearad').value = rad;
  $('areaout').innerHTML = '<p class="note">Loading…</p>';
  const c = await areaLocate(q);
  if (!c) { $('areaout').innerHTML = `<p class="note">Couldn't place "${esc(q)}". Try a postcode such as E8 3QW, or a district such as E8, N16 or SW11.</p>`; return; }
  await areaRun(c, +(rad || 1000)).catch(e => { $('areaout').innerHTML = `<p class="note">Something went wrong building this area (${esc(e.message)}). Please try again.</p>`; });
}
async function areaLocate(q) {
  AV ??= await fetch('api/v1/venues.json').then(r => r.json());
  const key = q.toUpperCase().replace(/\s+/g, ''), pc = x => (x || '').toUpperCase().replace(/\s+/g, '');
  const V = AV.venues;
  let hits = V.filter(v => pc(v[6]) === key), approx = false, label = q.toUpperCase();
  if (!hits.length) { hits = V.filter(v => pc(v[6]).startsWith(key) && /^[A-Z]{1,2}\d[A-Z\d]?$/.test(key)); approx = hits.length > 0 && !/\d[A-Z]{2}$/.test(key) ? false : true; }
  if (!hits.length && /\d[A-Z]{2}$/.test(key)) { const d = key.slice(0, -3); hits = V.filter(v => pc(v[6]).startsWith(d) && pc(v[6]).length === d.length + 3); approx = hits.length > 0; label = d + ' (nearest district)'; }
  if (!hits.length) { const b = AV.boroughs.findIndex(n => n.toLowerCase() === q.toLowerCase()); if (b >= 0) hits = V.filter(v => v[4] === b); }
  if (!hits.length) return null;
  const med = a => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)]; };
  return { lon: med(hits.map(h => h[0])), lat: med(hits.map(h => h[1])), label, approx };
}
async function areaRun(c, radiusM, quiet = false) {
  AV ??= await fetch('api/v1/venues.json').then(r => r.json());
  AB ??= (await getApi('brands')).brands;
  const sum = await getApi('summary');
  const [stn, crm] = await Promise.all([getApi('stations').catch(() => null), getApi('crime').catch(() => null)]);
  const r = radiusM / 1000, T = AV.types, R = AV.ratings;
  // brand lookup by name+postcode (brands.json lists every matched site)
  const brandOf = new Map();
  for (const b of AB) if (b.curated) for (const s of b.sites) brandOf.set(s[0] + '|' + s[1], b);
  const near = AV.venues.map(v => ({ v, d: km(c.lon, c.lat, v[0], v[1]) })).filter(x => x.d <= r).sort((a, b) => a.d - b.d)
    .map(({ v, d }) => ({ d, name: v[5], pc: v[6], type: T[v[2]], rating: R[v[3]], borough: AV.boroughs[v[4]], brand: brandOf.get(v[5] + '|' + v[6]) }));
  if (!near.length) { $('areaout').innerHTML = `<p class="note">No registered food businesses within ${radiusM} m of ${esc(c.label)}. Try a larger radius.</p>`; return; }
  const rated = near.filter(n => /^[0-5]$/.test(n.rating)), five = rated.filter(n => n.rating === '5').length, low = rated.filter(n => +n.rating <= 2).length;
  const pctFive = rated.length ? Math.round(five / rated.length * 100) : null;
  const lonFive = (() => { const t = sum.rating_distribution.filter(x => /^[0-5]$/.test(x.rating)); const all = t.reduce((a, x) => a + x.n, 0); return Math.round((t.find(x => x.rating === '5')?.n || 0) / all * 100); })();
  const kind = n => n.brand?.kind;
  const spec = near.filter(n => ['Specialty coffee', 'Bakery'].includes(kind(n)));
  const chains = near.filter(n => ['Chain', 'Coffee chain', 'Restaurant group'].includes(kind(n)));
  const pubs = near.filter(n => n.type === 'Pub/bar/nightclub');
  const beer = near.filter(n => BEER_RE.test(n.name) && n.type !== 'Takeaway/sandwich shop');
  const takeaways = near.filter(n => n.type === 'Takeaway/sandwich shop').length;
  const awaiting = near.filter(n => n.rating === 'AwaitingInspection').length;
  const indie = near.filter(n => !n.brand && n.rating === '5' && n.type === 'Restaurant/Cafe/Canteen');
  const kpi = (b, s, extra = '') => `<div class="kpi"><b>${b}</b><small>${s}</small>${extra}</div>`;
  // transport: nearest stations, walking at ~80 m/min
  const allStn = [];
  for (const x of stn ? stn.stations.map(x => ({ ...x, d: km(c.lon, c.lat, x.lon, x.lat) })).sort((a, b) => a.d - b.d) : []) {
    const dup = allStn.find(y => y.name === x.name && Math.abs(y.d - x.d) < .5);
    if (dup) { dup.lines = [...new Set([...dup.lines, ...x.lines])]; dup.modes = [...new Set([...dup.modes, ...x.modes])]; } else allStn.push({ ...x, lines: [...x.lines], modes: [...x.modes] });
  }
  const stations = allStn.slice(0, 5), stnIn = allStn.filter(x => x.d <= Math.max(r, 1)), lineSet = new Set(stnIn.flatMap(x => x.lines));
  const MODES = [['tube', 'Tube'], ['elizabeth-line', 'Elizabeth line'], ['overground', 'Overground'], ['dlr', 'DLR'], ['national-rail', 'National Rail'], ['tram', 'Tram']];
  const nearestMode = MODES.map(([m, nm]) => [nm, allStn.find(x => x.modes.includes(m))]).filter(x => x[1] && x[1].d <= 3);
  const walk = d => Math.max(1, Math.round(d * 1000 / 80)) + ' min walk';
  // crime: sum grid cells whose centre falls inside the radius (counts only; no individual incidents)
  let crimeHtml = '', crimeKpi = '', crimeTot = null, crimeRatio = null, crimeBand = '';
  if (crm) {
    const cs = crm.cell, inR = crm.cells.filter(([ix, iy]) => km(c.lon, c.lat, (ix + .5) * cs, (iy + .5) * cs) <= r);
    const tot = inR.reduce((a, row) => a + row[2].reduce((x, y) => x + y, 0), 0);
    const prev = inR.reduce((a, row) => a + (row[row.length - 1] || 0), 0);
    const byCat = crm.categories.map((cat, i) => [cat, inR.reduce((a, row) => a + row[2][i], 0)]).filter(x => x[1]).sort((a, b) => b[1] - a[1]);
    const nAll = crm.cells.length, lonMean = crm.categories.map((_, i) => crm.cells.reduce((a, row) => a + row[2][i], 0) / nAll), lonTot = lonMean.reduce((a, x) => a + x, 0);
    const ratio = inR.length ? tot / inR.length / lonTot : 0;
    const band = ratio < .6 ? ['well below', 'up'] : ratio < .85 ? ['below', 'up'] : ratio <= 1.15 ? ['close to', ''] : ratio <= 1.6 ? ['above', 'down'] : ['well above', 'down'];
    crimeRatio = ratio; crimeBand = band[0];
    const vsLon = (cat, n) => { const i = crm.categories.indexOf(cat), x = inR.length && lonMean[i] ? n / inR.length / lonMean[i] : null; return x == null ? '' : `<small class="ratio ${x > 1.15 ? 'down' : x < .85 ? 'up' : ''}">${x.toFixed(1)}x London avg</small>`; };
    const label = n => n.replace(/-/g, ' ').replace(/^./, ch => ch.toUpperCase());
    const mName = new Date(crm.as_of + '-01').toLocaleDateString('en-GB', { month: 'long', year: 'numeric' });
    const trend = prev ? Math.round((tot - prev) / prev * 100) : null; crimeTot = tot;
    crimeKpi = kpi(fmt(tot), `crimes recorded, ${mName}`, `<span class="cmp ${band[1]}">${ratio.toFixed(1)}x London avg (${band[0]})</span>` + (trend == null ? '' : `<span class="cmp">${trend >= 0 ? '▲' : '▼'} ${Math.abs(trend)}% vs previous month</span>`));
    crimeHtml = `<div><h3 style="margin-top:0">Recorded crime, ${mName}</h3><ul class="list">${byCat.slice(0, 5).map(([cat, n]) => `<li>${esc(label(cat))}<span>${fmt(n)} ${vsLon(cat, n)}</span></li>`).join('')}</ul>
      <p class="note"><b>Overall ${band[0]} the London average</b> (${ratio.toFixed(1)}x): this area records ${ratio.toFixed(1)} crimes for every 1 in an average populated 500 m grid square of London. Police-recorded counts, not a measure of how safe a street feels; busy centres, nightlife and stations record far more than quiet residential streets, so compare with similar places using the compare box below. Source: data.police.uk.</p></div>`;
  }
  const stnHtml = stations.length ? `<div><h3 style="margin-top:0">Nearest stations</h3><ul class="list">${stations.map(x => `<li>${esc(x.name)}<span>${walk(x.d)} · ${esc(x.lines.slice(0, 3).join(', '))}${x.lines.length > 3 ? '…' : ''}</span></li>`).join('')}</ul><h3>By type of service</h3><ul class="list">${nearestMode.map(([nm, x]) => `<li>${nm}<span>${esc(x.name)} · ${walk(x.d)}</span></li>`).join('') || '<li><span>None within 3 km</span></li>'}</ul><p class="note">${stnIn.length} station${stnIn.length === 1 ? '' : 's'} and ${lineSet.size} line${lineSet.size === 1 ? '' : 's'} within ${Math.max(r, 1)} km. Straight-line distance; walking time is an estimate. Source: TfL.</p></div>` : '';
  const dist = d => d < 1 ? Math.round(d * 1000) + ' m' : d.toFixed(1) + ' km';
  const li = (n, extra = '') => `<li>${esc(n.name)}<span>${dist(n.d)} · ${esc(n.pc)}${extra}</span></li>`;
  const list = (title, arr, note = '', max = 8) => `<div><h3 style="margin-top:0">${title}</h3><ul class="list">${arr.length ? arr.slice(0, max).map(n => li(n, n.brand ? ' · ' + esc(n.brand.name) : '')).join('') : '<li><span>None found in this area</span></li>'}</ul>${note}</div>`;
  const diff = pctFive == null ? '' : `<span class="cmp ${pctFive >= lonFive ? 'up' : 'down'}">${pctFive >= lonFive ? '▲' : '▼'} London ${lonFive}%</span>`;
  const metrics = { label: c.label, n: near.length, pctFive, spec: spec.length, pubs: pubs.length, chainPct: Math.round(chains.length / near.length * 100), awaiting, takeawayPct: Math.round(takeaways / near.length * 100), walk: stations[0] ? Math.max(1, Math.round(stations[0].d * 1000 / 80)) : null, station: stations[0]?.name, crime: crimeTot, crimeX: crimeRatio, lines: lineSet.size, stns: stnIn.length, lonFive };
  if (quiet) return metrics;
  $('areaout').innerHTML = `<h3 style="margin:14px 0 0">Within ${dist(r)} of ${esc(c.label)}</h3>${c.approx ? '<p class="note">Centred on the middle of the postcode district.</p>' : ''}
    <div class="areagrid">${kpi(fmt(near.length), 'food and drink businesses')}${kpi(pctFive == null ? '–' : pctFive + '%', 'of rated venues score 5', diff)}${kpi(spec.length, 'specialty coffee and bakeries')}${kpi(pubs.length, 'pubs and bars')}${kpi(Math.round(chains.length / near.length * 100) + '%', 'are well-known chains')}${kpi(awaiting, 'newly registered, awaiting inspection')}${stations.length ? kpi(Math.max(1, Math.round(stations[0].d * 1000 / 80)) + ' min', 'walk to ' + esc(stations[0].name)) + kpi(lineSet.size, `rail and tube lines within ${Math.max(r, 1)} km`) : ''}${crimeKpi}</div>
    ${stnHtml || crimeHtml ? `<div class="cols">${stnHtml}${crimeHtml}</div>` : ''}
    <div class="cols">${list('Specialty coffee and bakeries', spec, '', 10)}${list('Pubs and bars nearby', pubs.filter(n => n.rating === '5' || n.rating === '4'), '<p class="note">Rated 4 or 5, nearest first.</p>')}</div>
    <div class="cols" style="margin-top:14px">${list('Breweries, taprooms and beer venues', beer)}${list('Independent restaurants and cafés rated 5', indie, '<p class="note">Not part of a tracked brand, nearest first.</p>')}</div>
    <p class="note" style="margin-top:14px">${takeaways} of ${near.length} are takeaways (${Math.round(takeaways / near.length * 100)}%); ${low} rated venue${low === 1 ? ' is' : 's are'} at 0–2. <a href="#map/q=${encodeURIComponent(c.label.split(' ')[0].toLowerCase())}">See this area on the map</a>.
    Hygiene ratings measure food safety, not taste or quality. Brand matching is by name and can miss sites. Data: Food Standards Agency, Open Government Licence.</p>
    <h3>Compare with another area</h3><div class="row filters"><input id="areaq2" type="search" placeholder="Another postcode or district, e.g. N16" autocomplete="off" aria-label="Second area"><button class="btn" id="areacmpgo">Compare</button></div><div id="areacmp"></div>`;
  const cmp = () => areaCompare(c, radiusM, $('areaq2').value.trim());
  $('areacmpgo').onclick = cmp; $('areaq2').onkeydown = e => { if (e.key === 'Enter') cmp(); };
  return metrics;
}
async function areaCompare(c1, radiusM, q2) {
  const out = $('areacmp'); if (!q2) return;
  out.innerHTML = '<p class="note">Comparing…</p>';
  const c2 = await areaLocate(q2);
  if (!c2) { out.innerHTML = `<p class="note">Couldn't place "${esc(q2)}".</p>`; return; }
  const [a, b] = await Promise.all([areaRun(c1, radiusM, true), areaRun(c2, radiusM, true)]);
  const row = (name, k, f = x => x, better) => { const x = a[k], y = b[k]; const win = z => better && x != null && y != null && x !== y && ((better === 'hi' ? z > (z === x ? y : x) : z < (z === x ? y : x))) ? ' class="win"' : ''; return `<tr><th>${name}</th><td${win(x)}>${x == null ? '–' : f(x)}</td><td${win(y)}>${y == null ? '–' : f(y)}</td></tr>`; };
  out.innerHTML = `<div class="tablewrap"><table><thead><tr><th></th><th>${esc(a.label)}</th><th>${esc(b.label)}</th></tr></thead><tbody>
    ${row('Food and drink businesses', 'n', fmt)}${row('Rated 5 for hygiene', 'pctFive', x => x + '%', 'hi')}${row('Specialty coffee and bakeries', 'spec', fmt, 'hi')}${row('Pubs and bars', 'pubs', fmt)}${row('Well-known chains', 'chainPct', x => x + '%', 'lo')}${row('Takeaway share', 'takeawayPct', x => x + '%')}${row('Awaiting inspection', 'awaiting', fmt)}${row('Walk to nearest station (min)', 'walk', x => x, 'lo')}${row('Crimes recorded last month', 'crime', fmt, 'lo')}${row('Crime vs London average', 'crimeX', x => x.toFixed(1) + 'x', 'lo')}${row('Rail and tube lines nearby', 'lines', fmt, 'hi')}${row('Stations nearby', 'stns', fmt, 'hi')}</tbody></table></div><p class="note">Highlighted cells lead on that measure. Crime counts rise with footfall, so compare similar places. Within ${radiusM >= 1000 ? radiusM / 1000 + ' km' : radiusM + ' m'} of each.</p>`;
}

// ---------- brands ----------
let BR = null, brandKind = '';
init.brands = async () => {
  BR = (await getApi('brands')).brands;
  const kinds = ['', ...new Set(BR.map(b => b.kind))];
  $('brandkinds').innerHTML = kinds.map(k => `<button class="chip${k === brandKind ? ' on' : ''}" data-k="${esc(k)}">${esc(k || 'All')}</button>`).join('');
  $('brandkinds').onclick = e => { const b = e.target.closest('[data-k]'); if (!b) return; brandKind = b.dataset.k; $('brandkinds').querySelectorAll('.chip').forEach(c => c.classList.toggle('on', c === b)); brandGrid(); };
  let t; $('brandq').oninput = () => { clearTimeout(t); t = setTimeout(brandGrid, 150); };
  inited.brandsReady = true;
  brandGrid();
  brandOpen(location.hash.split('/')[1]);
};
function brandGrid() {
  const q = $('brandq').value.trim().toLowerCase();
  let list = BR.filter(b => (!brandKind || b.kind === brandKind) && (!q || b.name.toLowerCase().includes(q)));
  const shown = list.slice(0, 60);
  $('brandgrid').innerHTML = shown.map(b => `<button class="bcard" data-id="${esc(b.id)}"><span class="n">${fmt(b.n)}</span><b>${esc(b.name)}</b><small>${b.boroughs} borough${b.boroughs === 1 ? '' : 's'} · ${esc(b.kind)}</small></button>`).join('')
    + (list.length > shown.length ? `<p class="note" style="grid-column:1/-1">Showing the biggest ${shown.length} of ${fmt(list.length)}. Search to narrow down.</p>` : '')
    + (!list.length ? '<p class="note" style="grid-column:1/-1">No brand with that name has 3+ sites. Try the SQL lab for a one-off name search.</p>' : '');
  $('brandgrid').onclick = e => { const c = e.target.closest('[data-id]'); if (c) { location.hash = '#brands/' + c.dataset.id; } };
}
function brandOpen(id) {
  const d = $('branddetail'), b = BR && BR.find(x => x.id === id);
  if (!b) { d.hidden = true; return; }
  const max = b.top[0][1];
  const rated = b.five_star_pct == null ? '–' : b.five_star_pct + '%';
  d.hidden = false;
  d.innerHTML = `<h3>${esc(b.name)}</h3><small>${esc(b.kind)} · FSA-registered sites today</small>
    <div class="bstats"><div><b>${fmt(b.n)}</b>sites</div><div><b>${b.boroughs}</b>of 33 boroughs</div><div><b>${rated}</b>rated 5</div><div><b>${b.avg_rating ?? '–'}</b>avg rating</div></div>
    <div class="bbars">${b.top.map(([n, c]) => `<div><span>${esc(n)}</span><span class="t"><span class="f" style="width:${c / max * 100}%"></span></span><span>${c}</span></div>`).join('')}</div>
    <div class="row" style="margin-top:10px"><a class="btn primary" href="#map/q=${encodeURIComponent(b.name.replace(/[’'].*$/, '').toLowerCase())}">Show on map</a>
    <a class="btn" href="#sql/q=${encodeURIComponent(`SELECT name, postcode, authority, rating, rating_date FROM venues\nWHERE ${b.sql}\nORDER BY authority, name`)}">Query in SQL lab</a>
    <button class="btn" id="bclose">Close</button></div>
    <div class="sites"><ul class="list">${b.sites.slice(0, 120).map(s => `<li>${esc(s[0])}<span>${esc(s[3])} · ${esc(s[1])} · ${esc(RATING_LABEL[s[2]]?.split(' ')[0] ?? s[2])}</span></li>`).join('')}</ul>${b.n > 120 ? `<p class="note">First 120 of ${fmt(b.n)} sites. Use the SQL lab for the full list.</p>` : ''}</div>
    <p class="note">By name on the FSA register; ${b.curated ? 'hand-picked brand' : 'automatically found repeated name'}. Counts are premises, not company ownership.</p>`;
  $('bclose').onclick = () => { location.hash = '#brands'; };
  d.scrollIntoView({ block: 'nearest' });
}
function mapSearch(text) {   // used by brand links: prefill the map search once the map is ready
  const go = () => { if (!M.ready) return setTimeout(go, 150); $('q').value = text; M.q = text.toLowerCase(); refilter(); if (M.q.length >= 3) fitFiltered(); };
  go();
}

let sqlMod;
init.sql = () => { sqlMod = import('./sql.js').then(m => { m.initSql(); return m; }); sqlMod.catch(e => { $('sqlstat').textContent = 'Could not load the SQL lab: ' + e.message; }); };

// ---------- map ----------
const M = { ready: false, v: null, pts: [], idx: [], groups: [], scale: 1, ox: 0, oy: 0, mode: 'rating', types: new Set(), borough: '', q: '', grid: new Map(), hover: -1, dirty: true };
const COS = Math.cos(51.5 * Math.PI / 180);
const world = (lon, lat) => [lon * COS, -lat];

init.map = async () => {
  const canvas = $('map'); M.c = canvas; M.ctx = canvas.getContext('2d');
  M.v = await fetch('api/v1/venues.json').then(r => r.json());
  M.pts = M.v.venues.map(([lon, lat, t, r, b, name, pc]) => { const [x, y] = world(lon, lat); return { x, y, t, r, b, name, pc }; });
  M.v.types.forEach((_, i) => M.types.add(i));
  $('f-borough').insertAdjacentHTML('beforeend', M.v.boroughs.map(b => `<option>${esc(b)}</option>`).join(''));
  $('f-types').innerHTML = M.v.types.map((t, i) => `<button class="chip on" data-i="${i}">${esc(TYPE_SHORT[t] || t)}</button>`).join('');
  $('f-types').querySelectorAll('.chip').forEach(c => c.onclick = () => { const i = +c.dataset.i; M.types.has(i) ? M.types.delete(i) : M.types.add(i); c.classList.toggle('on'); refilter(); });
  $('f-borough').onchange = e => { M.borough = e.target.value; refilter(); fitFiltered(); };
  $('f-mode').onchange = e => { M.mode = e.target.value; legend(); refilter(false); };
  let tmr; $('q').oninput = e => { clearTimeout(tmr); tmr = setTimeout(() => { M.q = e.target.value.trim().toLowerCase(); refilter(); if (M.q.length >= 3) fitFiltered(); }, 250); };
  $('zin').onclick = () => zoomBy(1.5); $('zout').onclick = () => zoomBy(1 / 1.5); $('zreset').onclick = fitAll;
  bindGestures(canvas);
  getApi('events').then(e => { M.evRaw = e.events.latest || []; M.ev = M.evRaw.map(r => { const [x, y] = world(r.lon, r.lat); return { x, y, k: r.event }; }); glSync(); });
  $('f-ev').onchange = e => { M.showEv = e.target.checked; mapRedraw(); glSync(); };
  // filters panel: collapsible so it never hides the map; starts closed on phones
  const mtSet = open => { $('mt-body').hidden = !open; $('mt-toggle').setAttribute('aria-expanded', open); $('mt-toggle').textContent = open ? 'Filters ▾' : 'Filters ▸'; };
  $('mt-toggle').onclick = () => mtSet($('mt-body').hidden);
  mtSet(!matchMedia('(max-width:760px)').matches);
  const tok = window.LP_CONFIG?.mapboxToken;
  if (tok) startGl(tok);   // Mapbox is the map when a token is configured; the canvas map is only the fallback
  M.ready = true;
  mapResize(); fitAll(); refilter(); legend();
  const arg = (location.hash.split('/')[1]); if (arg) mapFocusBorough(dec(arg));
  addEventListener('resize', () => { if (current === 'map') mapResize(); });
};

async function startGl(tok) {
  const gl = $('gl'), box = document.querySelector('.mapbox'), canvas = $('map');
  const fallback = msg => { M.glOn = false; box.classList.remove('gl'); gl.hidden = true; canvas.style.visibility = ''; $('gl-layer').hidden = true; $('gl-note').hidden = true; $('glheat').hidden = true; toast(msg + ' Showing the basic map.'); mapRedraw(); };
  try {
    const mb = await import('./mapbox.js');
    M.glOn = true; box.classList.add('gl'); gl.hidden = false; canvas.style.visibility = 'hidden';
    const sel = $('gl-layer'); sel.hidden = false;
    sel.innerHTML = Object.entries(mb.LAYERS).map(([k, l]) => `<option value="${k}">${esc(l.label)}</option>`).join('');
    const layer = () => {
      const L = mb.LAYERS[sel.value]; mb.glLayer(sel.value);
      $('glheat').hidden = sel.value !== 'dots'; $('f-mode').hidden = sel.value !== 'dots'; $('legend').hidden = sel.value !== 'dots';
      const n = $('gl-note'); n.hidden = !L.tip; n.innerHTML = L.tip ? `${esc(L.tip)}<span class="ramp"><i style="background:linear-gradient(90deg,${L.color.slice(3).filter((_, i) => i % 2 === 1).join(',')})"></i><small>${esc(L.legend[0])}</small><small>${esc(L.legend[1])}</small></span>` : '';
    };
    sel.onchange = layer; $('glheat').hidden = false;
    await mb.showGl(gl, tok, M, fallback);
    layer();
  } catch (err) { fallback(err.message || 'Mapbox failed to load.'); }
}

function glSync(fit = false) {   // keep the Mapbox view in step with the shared filters
  if (!M.glOn) return;
  import('./mapbox.js').then(m => { m.glUpdate(M); if (fit) m.glFit(M); });
}

function mapResize() {
  if (!M.ready) return;
  const r = M.c.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
  M.w = r.width; M.h = r.height; M.c.width = r.width * dpr; M.c.height = r.height * dpr; M.dpr = dpr;
  if (!M.fitted) { fitAll(); M.fitted = true; }
  mapRedraw();
}
function bounds(arr) { let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9; for (const p of arr) { if (p.x < x0) x0 = p.x; if (p.x > x1) x1 = p.x; if (p.y < y0) y0 = p.y; if (p.y > y1) y1 = p.y; } return { x0, x1, y0, y1 }; }
function fitBounds(b) {
  const pad = 56, w = Math.max(b.x1 - b.x0, 1e-4), h = Math.max(b.y1 - b.y0, 1e-4);
  M.scale = Math.min((M.w - pad * 2) / w, (M.h - pad * 2) / h);
  M.ox = M.w / 2 - (b.x0 + b.x1) / 2 * M.scale; M.oy = M.h / 2 - (b.y0 + b.y1) / 2 * M.scale;
  mapRedraw();
}
function robustBounds(arr) {   // trim 0.5% outliers each side so a few stray points don't shrink the view
  const xs = arr.map(p => p.x).sort((a, b) => a - b), ys = arr.map(p => p.y).sort((a, b) => a - b), lo = Math.floor(arr.length * .005), hi = arr.length - 1 - lo;
  return { x0: xs[lo], x1: xs[hi], y0: ys[lo], y1: ys[hi] };
}
function fitAll() { if (M.pts.length && M.w) fitBounds(robustBounds(M.pts)); }
function fitFiltered() { glSync(true); const f = M.idx.map(i => M.pts[i]); if (f.length) fitBounds(f.length > 200 ? robustBounds(f) : bounds(f)); }
function mapFocusBorough(name) {
  if (!M.ready) return;
  if (!M.v.boroughs.includes(name)) return;
  $('f-borough').value = name; M.borough = name; refilter(); fitFiltered();
}
function zoomBy(f, cx = M.w / 2, cy = M.h / 2) {
  const s = Math.min(Math.max(M.scale * f, 1500), 4e6);
  M.ox = cx - (cx - M.ox) * (s / M.scale); M.oy = cy - (cy - M.oy) * (s / M.scale); M.scale = s; mapRedraw();
}

function color(p) {
  if (M.mode === 'type') return TYPE_COLORS[p.t % 4];
  const key = M.v.ratings[p.r];
  if (M.mode === 'awaiting') return key === 'AwaitingInspection' ? css('--raw') : null;
  return css(RATING_VAR[key] || '--rex');
}
function legend() {
  let items;
  if (M.mode === 'type') items = M.v.types.map((t, i) => [TYPE_COLORS[i % 4], TYPE_SHORT[t] || t]);
  else if (M.mode === 'awaiting') items = [[css('--raw'), 'Awaiting first inspection'], [css('--rex'), 'Other venues (faded)']];
  else items = ['5', '4', '3', '2', '1', '0', 'AwaitingInspection'].map(k => [css(RATING_VAR[k]), k === 'AwaitingInspection' ? 'Awaiting' : k]);
  $('legend').innerHTML = items.map(([c, l]) => `<span><i style="background:${c}"></i>${esc(l)}</span>`).join('');
}
function refilter(rebuildIndex = true) {
  const q = M.q;
  M.idx = [];
  M.pts.forEach((p, i) => {
    if (!M.types.has(p.t)) return;
    if (M.borough && M.v.boroughs[p.b] !== M.borough) return;
    if (q.length >= 3 && !(p.name.toLowerCase().includes(q) || p.pc.toLowerCase().replace(/\s/g, '').includes(q.replace(/\s/g, '')))) return;
    M.idx.push(i);
  });
  if (rebuildIndex) {
    M.grid.clear();
    for (const i of M.idx) { const p = M.pts[i], k = Math.floor(p.x / .002) + ',' + Math.floor(p.y / .002); (M.grid.get(k) || M.grid.set(k, []).get(k)).push(i); }
  }
  const byColor = new Map();
  for (const i of M.idx) { const c = color(M.pts[i]); (byColor.get(c ?? 'dim') || byColor.set(c ?? 'dim', []).get(c ?? 'dim')).push(i); }
  M.groups = [...byColor.entries()];
  $('mapcount').textContent = `${fmt(M.idx.length)} venue${M.idx.length === 1 ? '' : 's'}`;
  mapRedraw(); glSync();
}
function mapRedraw() {
  if (!M.ready || !M.w) return;
  M.dirty = true;
  if (M.raf) return;
  M.raf = requestAnimationFrame(() => {
    M.raf = 0; M.dirty = false;
    const ctx = M.ctx, d = M.dpr;
    ctx.setTransform(d, 0, 0, d, 0, 0);
    ctx.clearRect(0, 0, M.w, M.h);
    const sz = Math.min(Math.max(M.scale / 28000, 1.6), 7), dimC = css('--bar');
    const draw = (c, list) => {
      ctx.fillStyle = c === 'dim' ? dimC : c;
      ctx.globalAlpha = c === 'dim' ? .5 : .88;
      for (const i of list) {
        const p = M.pts[i], x = p.x * M.scale + M.ox, y = p.y * M.scale + M.oy;
        if (x < -8 || y < -8 || x > M.w + 8 || y > M.h + 8) continue;
        ctx.fillRect(x - sz / 2, y - sz / 2, sz, sz);
      }
    };
    if (M.mode === 'awaiting') {   // faded context first, highlight on top
      const dim = M.idx.filter(i => color(M.pts[i]) === null);
      draw('dim', dim);
    }
    for (const [c, list] of M.groups) if (c !== 'dim') draw(c, list);
    ctx.globalAlpha = 1;
    if (M.showEv && M.ev) {
      ctx.lineWidth = 2;
      for (const p of M.ev) {
        const x = p.x * M.scale + M.ox, y = p.y * M.scale + M.oy;
        if (x < -12 || y < -12 || x > M.w + 12 || y > M.h + 12) continue;
        ctx.strokeStyle = p.k === 'new' ? '#1a8a4a' : p.k === 'removed' ? '#c0392b' : '#d4a017';
        ctx.beginPath(); ctx.arc(x, y, sz + 5, 0, 7); ctx.stroke();
      }
    }
    if (M.hover >= 0) {
      const p = M.pts[M.hover], x = p.x * M.scale + M.ox, y = p.y * M.scale + M.oy;
      ctx.strokeStyle = css('--ink'); ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, sz + 4, 0, 7); ctx.stroke();
    }
  });
}
function nearest(mx, my) {
  const wx = (mx - M.ox) / M.scale, wy = (my - M.oy) / M.scale, cx = Math.floor(wx / .002), cy = Math.floor(wy / .002);
  const reach = Math.max(1, Math.ceil(14 / M.scale / .002));
  let best = -1, bd = (14 / M.scale) ** 2;
  for (let dx = -reach; dx <= reach; dx++) for (let dy = -reach; dy <= reach; dy++) {
    for (const i of M.grid.get((cx + dx) + ',' + (cy + dy)) || []) {
      const p = M.pts[i], d = (p.x - wx) ** 2 + (p.y - wy) ** 2;
      if (d < bd) { bd = d; best = i; }
    }
  }
  return best;
}
function showTip(i, mx, my) {
  const tip = $('tip');
  if (i < 0) { tip.hidden = true; if (M.hover !== -1) { M.hover = -1; mapRedraw(); } return; }
  const p = M.pts[i]; M.hover = i;
  tip.hidden = false;
  tip.innerHTML = `<b>${esc(p.name)}</b>${esc(TYPE_SHORT[M.v.types[p.t]] || '')} · ${esc(M.v.boroughs[p.b])}<br>${esc(p.pc)} · ${esc(RATING_LABEL[M.v.ratings[p.r]] || '')}`;
  const bw = M.c.getBoundingClientRect().width;
  tip.style.left = Math.min(Math.max(mx + 14, 8), bw - 250) + 'px'; tip.style.top = Math.max(my - 10, 80) + 'px';
  mapRedraw();
}
function bindGestures(c) {
  const ptrs = new Map(); let last = 0, moved = 0;
  c.addEventListener('pointerdown', e => { c.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, [e.offsetX, e.offsetY]); moved = 0; last = pinchDist(); });
  c.addEventListener('pointermove', e => {
    const prev = ptrs.get(e.pointerId);
    if (!prev) { if (e.pointerType === 'mouse') showTip(nearest(e.offsetX, e.offsetY), e.offsetX, e.offsetY); return; }
    if (ptrs.size === 1) { M.ox += e.offsetX - prev[0]; M.oy += e.offsetY - prev[1]; moved += Math.abs(e.offsetX - prev[0]) + Math.abs(e.offsetY - prev[1]); mapRedraw(); }
    ptrs.set(e.pointerId, [e.offsetX, e.offsetY]);
    if (ptrs.size === 2) { const d = pinchDist(); if (last) { const [a, b] = [...ptrs.values()]; zoomBy(d / last, (a[0] + b[0]) / 2, (a[1] + b[1]) / 2); } last = d; moved = 99; }
  });
  const up = e => { const wasTap = ptrs.size === 1 && moved < 6; ptrs.delete(e.pointerId); last = 0; if (wasTap) showTip(nearest(e.offsetX, e.offsetY), e.offsetX, e.offsetY); };
  c.addEventListener('pointerup', up); c.addEventListener('pointercancel', e => { ptrs.delete(e.pointerId); });
  c.addEventListener('pointerleave', e => { if (e.pointerType === 'mouse') showTip(-1); });
  c.addEventListener('wheel', e => { e.preventDefault(); zoomBy(Math.exp(-e.deltaY * 0.0015), e.offsetX, e.offsetY); }, { passive: false });
  function pinchDist() { if (ptrs.size < 2) return 0; const [a, b] = [...ptrs.values()]; return Math.hypot(a[0] - b[0], a[1] - b[1]); }
}

function toast(msg) { const t = $('toast'); t.textContent = msg; t.hidden = false; setTimeout(() => { t.hidden = true; }, 5000); }

// ---------- boot ----------
route();
if ('serviceWorker' in navigator && location.protocol === 'https:') navigator.serviceWorker.register('sw.js').catch(() => {});
