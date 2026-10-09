'use strict';
// Postcode report card. Loaded on demand like sql.js. Uses globals from app.js: $, esc, fmt, getApi, api, km, dec, toast, whoNearby, areaLocate.
// Thresholds, distributions and the sample checks come from api/v1/report.json; nothing here computes a London threshold.

const LETTERS = ['A', 'B', 'C'], SHAPES = ['●', '■', '▲'];
const OUTCODE = /^[A-Z]{1,2}\d[A-Z\d]?$/;
const FULL_PC = /^[A-Z]{1,2}\d[A-Z\d]? ?\d[A-Z]{2}$/;
const LICENCE = 'Postcode location: postcodes.io (ONS Postcode Directory). Contains OS data (c) Crown copyright and database right; Contains Royal Mail data (c) Royal Mail copyright and database right; Source: ONS, OGL v3.';
const PILLS = {
  crime_rate: ['Fewer recorded crimes than most of London', 'Around the London middle', 'More recorded crimes than most of London'],
  venues_800: ['Fewer nearby than most', 'Around the London middle', 'More choice than most'],
  walk_min: ['Quicker to a station than most', 'Around the London middle', 'Slower to a station than most'],
};
const BADGE_HELP = { measured: 'Counted directly from the source data', modelled: 'An estimate built from a model or from distances', proxy: 'A stand-in that hints at the real thing' };
const money = n => '£' + fmt(Math.round(n / 100) * 100);
const ord = n => { const s = ['th', 'st', 'nd', 'rd'], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); };
const tenths = p => { const t = Math.round(p / 10); return t <= 0 ? 'fewer than 1 in 10' : t >= 10 ? 'nearly all' : `about ${t} in 10`; };
const num1 = n => (Math.round(n * 10) / 10).toLocaleString('en-GB');
const priceK = n => n >= 1e6 ? '£' + num1(n / 1e6) + 'm' : '£' + fmt(Math.round(n / 1000)) + 'k';
const rentFmt = n => '£' + fmt(Math.round(n));
const monthName = ym => /^\d{4}-\d\d$/.test(ym || '') ? new Date(ym + '-15').toLocaleDateString('en-GB', { month: 'long', year: 'numeric' }) : String(ym || '');
const boroKey = n => String(n || '').toLowerCase().replace(/&/g, 'and').replace(/^(the )?(london|royal) borough of /, '').replace(/^city of (?!london)/, '').replace(/[^a-z]+/g, ' ').trim();

let R = null, token = 0, lastArg = null;

// ---------- locate ----------
const normPc = q => { const k = q.toUpperCase().replace(/\s+/g, ''); return FULL_PC.test(k) ? k.slice(0, -3) + ' ' + k.slice(-3) : q.trim().toUpperCase(); };
const inLondonBox = (lon, lat) => lon > -0.6 && lon < 0.4 && lat > 51.2 && lat < 51.75;

async function locate(q) {
  const label = normPc(q);
  const approx = async why => {
    const c = await areaLocate(q).catch(() => null);
    if (!c) return { label, err: `Could not place "${q}". Try a full postcode such as E8 3QW, or a district such as E8.` };
    if (/\(nearest district\)$/.test(c.label || '')) why = why.replace('the middle of the postcode from venue locations', 'the middle of the postcode district');
    return { label, lon: c.lon, lat: c.lat, method: 'approx', note: why, outcode: label.split(' ')[0] };
  };
  if (!FULL_PC.test(label)) return approx(OUTCODE.test(label) ? 'Approximate location: the middle of the postcode district, from venue locations.' : "Approximate location: the middle of the borough's venues.");
  let res;
  try {
    const ac = new AbortController(), t = setTimeout(() => ac.abort(), 8000);
    res = await fetch('https://api.postcodes.io/postcodes/' + encodeURIComponent(label.replace(' ', '')), { signal: ac.signal });
    clearTimeout(t);
  } catch { return approx('Approximate location: the postcode lookup could not be reached, so this uses the middle of the postcode from venue locations.'); }
  if (res.status === 404) return { label, err: `${label} is not a current postcode.` };
  if (!res.ok) return approx('Approximate location: the postcode lookup is unavailable, so this uses the middle of the postcode from venue locations.');
  const r = (await res.json().catch(() => ({}))).result;
  if (!r || r.latitude == null || r.longitude == null) return { label, err: `${label} is not a current postcode.` };
  if (r.region !== 'London' || r.country !== 'England' || !inLondonBox(r.longitude, r.latitude)) return { label, err: 'Outside London; this covers the 33 London local authorities only.' };
  return { label, lon: r.longitude, lat: r.latitude, method: 'postcodes.io', outcode: r.outcode, admin: r.admin_district };
}

// ---------- metrics (mirrors the Python in src/london_pulse/report.py) ----------
export async function metricsFor(c, outcode) {
  R ??= await getApi('report');
  const r = (R.catchment_m || 800) / 1000;
  const [V, S, CH, w, P] = await Promise.all([getApi('venues'), getApi('stations'), getApi('character').catch(() => null), whoNearby(c, r), getApi('prices').catch(() => { delete api.prices; return null; })]);
  const v = {};
  if (w) { v.crime_rate = w.rate; v.income_dep_pct = w.incDep; v.income_ahc = w.incAhc; }
  const near = [];
  for (const x of V.venues) { const d = km(c.lon, c.lat, x[0], x[1]); if (d <= r) near.push({ x, d }); }
  const rated = near.filter(n => /^[0-5]$/.test(V.ratings[n.x[3]]));
  v.venues_800 = near.length;
  v.five_pct_800 = rated.length ? rated.filter(n => V.ratings[n.x[3]] === '5').length / rated.length * 100 : null;
  const stn = S.stations.map(s => ({ ...s, d: km(c.lon, c.lat, s.lon, s.lat) })).sort((a, b) => a.d - b.d);
  v.walk_min = stn.length ? stn[0].d * 1000 / 80 : null;
  const in1 = stn.filter(s => s.d <= 1);
  v.lines_1km = new Set(in1.flatMap(s => s.lines)).size;
  const district = CH?.districts.find(d => d.name === outcode) || null;
  v.fresh_pct = district ? district.fresh_pct : null;
  const pubs = near.filter(n => V.types[n.x[2]] === 'Pub/bar/nightclub').length;
  near.sort((a, b) => a.d - b.d);
  const price = P && R.metrics.median_price ? priceFor(P, c.label, outcode, near[0]?.x[6]) : null, rent = P?.rents && R.metrics.rent_2bed ? rentFor(P.rents, c.admin || w?.rows[0]?.borough) : null;
  if (price) v.median_price = price.level === 'sector' ? price.all : null;
  if (rent) v.rent_2bed = rent.two_bed;
  return { values: v, price, rent, prices: P, w, near: near.slice(0, 6).map(n => ({ name: n.x[5], pc: n.x[6], d: n.d, id: n.x[7] })), pubs, nStn: in1.length, stn: stn[0] || null, stnIn: in1, district, V };
}

// ---------- prices and rents (api/v1/prices.json; absent file means the two lines are left out) ----------
// Sector = postcode without its last two letters ("E8 3QW" -> "E8 3"). A typed district alone gives the district figure.
function priceFor(P, label, outcode, nearestPc) {
  const lab = String(label || '').trim().toUpperCase();
  if (lab && !FULL_PC.test(lab) && !OUTCODE.test(lab)) return { level: 'borough', name: label }; // a borough name: no sale price, not a nearby sector
  let full = null, dName = outcode;
  if (FULL_PC.test(lab)) full = lab;
  else if (OUTCODE.test(lab)) dName = lab;
  else if (FULL_PC.test(String(nearestPc || '').toUpperCase().replace(/\s+/g, ''))) full = normPc(nearestPc); // no label at all (Python parity path)
  const sector = full ? full.slice(0, -2) : null;
  if (full) dName = full.split(' ')[0];
  const s = sector && P.sectors?.[sector], d = P.districts?.[dName];
  if (s && s[0] != null) return { level: 'sector', name: sector, all: s[0], n: s[1], flat: s[2], nf: s[3], house: s[4], nh: s[5] };
  if (d && d[0] != null) return { level: 'district', name: dName, all: d[0], n: d[1], sectorTooFew: !!sector, sectorN: s ? s[1] : null };
  return { level: 'none', name: sector || dName, n: s ? s[1] : d ? d[1] : null };
}
function rentFor(rents, borough) {
  if (!borough) return null;
  const k = boroKey(borough), code = Object.keys(rents.boroughs || {}).find(c => boroKey(rents.boroughs[c].name) === k);
  return code ? { code, ...rents.boroughs[code] } : { name: borough, two_bed: null, unmatched: true };
}

// ---------- distribution helpers ----------
function pctOf(key, val) {
  const q = R.metrics[key].q;
  if (val <= q[0]) return 0;
  if (val >= q[q.length - 1]) return 100;
  const i = q.findIndex(x => x >= val);
  if (q[i] === val) return i * 5; // ties: the lower index of the tied run
  return ((i - 1) + (val - q[i - 1]) / (q[i] - q[i - 1])) * 5;
}
function label(key, val, M) {
  const m = R.metrics[key], tone = PILLS[key];
  if (val == null) return { cls: 'n', text: 'Description only', why: 'No value for this place.' };
  if (m.direction === '0' || !tone) return { cls: 'n', text: 'Description only' };
  if (key === 'crime_rate' && M.w && M.w.busyShare >= .25) return { cls: 'n', text: 'Busy centre: not rated', caveat: 'Busy centre: recorded crime here reflects visitors and workers as much as residents.' };
  const low = val <= m.lo, high = val >= m.hi, good = m.direction === '-' ? low : high, bad = m.direction === '-' ? high : low;
  if (good) return { cls: 'g', text: tone[m.direction === '-' ? 0 : 2] };
  if (bad) return { cls: 'a', text: tone[m.direction === '-' ? 2 : 0] };
  return { cls: 'n', text: tone[1] };
}

// ---------- rows ----------
const dist = d => d < 1 ? Math.round(d * 1000) + ' m' : d.toFixed(1) + ' km';
const lsoaRows = (M, cols) => {
  const P = M.w.ar.proof, month = M.w.ar.crime_months[M.w.ar.crime_months.length - 1], fill = (t, o) => t.replace(/\{(\w+)\}/g, (_, k) => encodeURIComponent(o[k]));
  return `<div class="tablewrap"><table><thead><tr><th>Neighbourhood (LSOA)</th>${cols.map(c => `<th>${c[0]}</th>`).join('')}<th>Check at source</th></tr></thead><tbody>${M.w.rows.slice(0, 12).map(r =>
    `<tr><th>${esc(r.name)}<br><small>${esc(r.code)}</small></th>${cols.map(c => `<td>${c[1](r)}</td>`).join('')}<td>${cols.src(r, P, fill, month)}</td></tr>`).join('')}</tbody></table></div>${M.w.rows.length > 12 ? `<p class="note">Showing the nearest 12 of ${M.w.rows.length}.</p>` : ''}`;
};
const link = (u, t) => `<a href="${esc(u)}" target="_blank" rel="noopener">${t}</a>`;
const crimeSum = r => r.crimes.reduce((a, b) => a + b, 0);

const ROWS = [
  { id: 'safety', title: 'Safety', key: 'crime_rate', noun: 'neighbourhoods',
    phrase: (v) => `Recorded crime here (${fmt(Math.round(v))} per 1,000 residents a year)`, fv: v => fmt(Math.round(v)),
    formula: 'Crimes recorded by the police in the catchment over the latest months, scaled to a year, divided by residents, per 1,000. Only shown where enough people live nearby.',
    area: true,
    facts: M => {
      const w = M.w; if (!w) return [];
      const f = w.ar.categories, tot = f.map((_, i) => w.rows.reduce((a, r) => a + (r.crimes[i] || 0), 0)).map((n, i) => [f[i], n]).filter(x => x[1]).sort((a, b) => b[1] - a[1]).slice(0, 3);
      return [tot.length ? 'Most common recorded: ' + tot.map(([k, n]) => `${esc(k.replace(/-/g, ' '))} (${fmt(n)})`).join(', ') : 'No recorded crimes in these months',
        `${Math.round(w.busyShare * 100)}% of nearby residents live in busy centres`];
    },
    dont: 'How it feels on a street, unreported crime, road danger.',
    cols: M => { const c = [['Residents', r => fmt(r.pop)], ['Crimes, ' + M.w.ar.crime_months.length + ' months', r => fmt(crimeSum(r))], ['Busy centre', r => r.busy ? 'yes' : 'no']]; c.src = (r, P, fill, m) => link(fill(P.crime, { lat: r.lat, lng: r.lon, month: m }), 'police data nearby'); return c; } },
  { id: 'who', title: 'Who lives nearby', key: 'income_dep_pct', noun: 'neighbourhoods',
    phrase: v => `Income deprivation nearby (${Math.round(v)}% of residents)`, fv: v => Math.round(v) + '%',
    formula: 'Share of residents in income-deprived households (IMD 2025), averaged across the catchment weighted by residents.',
    area: true,
    facts: M => M.w ? [`About ${fmt(Math.round(M.w.pop / 100) * 100)} residents in ${fmt(M.w.hh)} households`] : [],
    dont: 'Individuals; anything after Census 2021.',
    cols: M => { const c = [['Residents', r => fmt(r.pop)], ['Income-deprived', r => r.income_dep_pct == null ? '–' : r.income_dep_pct + '%']]; c.src = (r, P) => link(P.deprivation, 'IMD 2025 (look up ' + esc(r.code) + ')'); return c; } },
  { id: 'food', title: 'Food and nightlife', key: 'venues_800', noun: 'neighbourhoods',
    phrase: v => `The number of food and drink businesses within 800 m (${fmt(v)})`, fv: v => fmt(v),
    formula: 'Registered food and drink businesses within 800 m of the postcode, straight-line distance, from the FSA register.',
    facts: (M, vals) => [vals.five_pct_800 == null ? 'No rated venues nearby' : `${Math.round(vals.five_pct_800)}% of rated venues have the top hygiene rating of 5`, `${fmt(M.pubs)} pubs and bars`],
    dont: 'Taste, price, opening hours.', list: M => M.near.length ? `<p>Nearest registered businesses:</p><ul class="list">${M.near.map(n => `<li>${n.id ? link('https://ratings.food.gov.uk/business/' + n.id, esc(n.name)) : esc(n.name)}<span>${dist(n.d)} · ${esc(n.pc)}</span></li>`).join('')}</ul>` : '' },
  { id: 'transport', title: 'Transport', key: 'walk_min', noun: 'neighbourhoods',
    phrase: v => `Walking time to the nearest station (${Math.max(1, Math.round(v))} min)`, fv: v => Math.max(1, Math.round(v)) + ' min',
    formula: 'Straight-line distance to the nearest station in the TfL list, at 80 m a minute. A model, not a route.',
    facts: (M, vals) => [M.stn ? `Nearest: ${esc(M.stn.name)} (${dist(M.stn.d)})` : 'No station found', `${vals.lines_1km} rail and tube lines within 1 km`],
    dont: 'Buses, frequency, step-free access.', list: M => M.stnIn.length ? `<p>Stations within 1 km:</p><ul class="list">${M.stnIn.slice(0, 8).map(s => `<li>${esc(s.name)}<span>${dist(s.d)} · ${esc(s.lines.slice(0, 3).join(', '))}</span></li>`).join('')}</ul>` : '' },
  { id: 'afford', title: 'Affordability', key: 'income_ahc', noun: 'neighbourhoods',
    phrase: v => `Modelled household income after housing costs (${money(v)} a year)`, fv: v => money(v),
    formula: 'ONS modelled household income after housing costs for the surrounding area (MSOA), averaged across the catchment weighted by residents. An estimate, not a count.',
    area: true,
    facts: M => {
      const w = M.w; if (!w) return [];
      const out = [];
      if (w.incLo && w.incHi && w.msoas.length === 1) out.push(`ONS 95% range ${money(w.incLo)} to ${money(w.incHi)}`);
      else if (w.incLo && w.incHi && Number.isFinite(w.incMin) && Number.isFinite(w.incMax)) out.push(`Range across ${w.msoas.length || 'several'} ONS estimates ${money(w.incMin)} to ${money(w.incMax)}`);
      const pc = x => x == null ? '–' : Math.round(x) + '%', other = Math.max(0, 100 - (w.council + w.social + w.rent + w.owned));
      const seg = (v, cls, nm) => v >= 1 ? `<span class="${cls}" title="${nm} ${Math.round(v)}%" style="width:${v}%"></span>` : '';
      out.push(`<div class="tenure" role="img" aria-label="Homes by tenure, not rated: council ${pc(w.council)}, housing association ${pc(w.social)}, private rent ${pc(w.rent)}, owned ${pc(w.owned)}">${seg(w.council, 't-council', 'Council')}${seg(w.social, 't-social', 'Housing association')}${seg(w.rent, 't-rent', 'Private rent')}${seg(w.owned, 't-own', 'Owned')}${seg(other, 't-other', 'Other')}</div><span class="rc-key">Homes by tenure (Census 2021, not rated): council ${pc(w.council)} · housing association ${pc(w.social)} · private rent ${pc(w.rent)} · owned ${pc(w.owned)}</span>`);
      return out;
    },
    dont: 'Council tax, service charges, what is currently advertised, the condition of homes, and rents for a specific street.',
    sub: true,
    cols: M => { const c = [['Residents', r => fmt(r.pop)], ['Income after housing', r => r.net_income_ahc ? money(r.net_income_ahc) : '–'], ['MSOA', r => esc(r.msoa || '–')]]; c.src = (r, P) => link(P.income, 'ONS income estimates (MSOA ' + esc(r.msoa || '') + ')'); return c; } },
  { id: 'changing', title: 'What is changing', key: 'fresh_pct', noun: 'postcode districts',
    phrase: v => `The share of venues newly registered and awaiting inspection (${num1(v)}%)`, fv: v => num1(v) + '%',
    formula: "Share of the postcode district's food and drink businesses that are new and awaiting a first FSA inspection. An approximate hint of new supply, not a count of openings.",
    facts: M => M.district ? [`Stage: ${esc(M.district.stage)}`, M.district.signature.length ? 'Signature: ' + M.district.signature.map(s => esc(s.label)).join(', ') : 'No standout type'] : ['No data for this postcode district'],
    dont: 'Planning applications, confirmed closures.' },
];

// ---------- rendering ----------
function stripSvg(row, items) {
  const m = R.metrics[row.key], e = m.hist.edges, cnt = m.hist.counts, W = 320, PAD = 8, x0 = e[0], x1 = e[e.length - 1];
  const X = v => PAD + (Math.min(Math.max(v, x0), x1) - x0) / (x1 - x0 || 1) * (W - 2 * PAD);
  const mx = Math.max(1, ...cnt), base = 40, med = m.q[10];
  const bg = m.direction === '0' ? '' : `<rect class="bg-${m.direction === '-' ? 'g' : 'a'}" x="${PAD}" y="2" width="${(X(m.lo) - PAD).toFixed(1)}" height="${base - 2}"/><rect class="bg-${m.direction === '-' ? 'a' : 'g'}" x="${X(m.hi).toFixed(1)}" y="2" width="${(W - PAD - X(m.hi)).toFixed(1)}" height="${base - 2}"/>`;
  const bars = cnt.map((n, i) => { const h = n / mx * (base - 6); return `<rect class="bar" x="${X(e[i]).toFixed(1)}" y="${(base - h).toFixed(1)}" width="${Math.max(1, X(e[i + 1]) - X(e[i]) - 1).toFixed(1)}" height="${h.toFixed(1)}"/>`; }).join('');
  const mk = items.filter(it => it.val != null).map(it => {
    const x = X(it.val), off = it.val < x0 ? -1 : it.val > x1 ? 1 : 0, y = 50, t = LETTERS[it.i];
    const shape = it.i === 0 ? `<circle cx="${x.toFixed(1)}" cy="${y}" r="4.5"/>` : it.i === 1 ? `<rect x="${(x - 4).toFixed(1)}" y="${y - 4}" width="8" height="8"/>` : `<path d="M${x.toFixed(1)} ${y - 5}l5 9h-10z"/>`;
    return `<g class="mk" data-i="${it.i}">${shape}${items.length > 1 ? `<text x="${(x + 7).toFixed(1)}" y="${y + 3.5}" class="ml">${t}</text>` : ''}${off ? `<path class="arrow" d="M${(x + off * 9).toFixed(1)} ${y}l${-off * 4} -3v6z"/>` : ''}</g>`;
  }).join('');
  const aria = `${m.label}, distribution across London ${row.noun}. Typical value ${row.fv(med)}. ` + items.map(it => it.val == null ? `${items.length > 1 ? LETTERS[it.i] + ': ' : ''}no value` : `${items.length > 1 ? LETTERS[it.i] + ': ' : 'This place: '}${row.fv(it.val)}, higher than about ${Math.round(pctOf(row.key, it.val))}% of London ${row.noun}${it.val < x0 || it.val > x1 ? ' (off the scale shown)' : ''}`).join('. ') + '.';
  return `<svg class="strip" viewBox="0 0 320 64" role="img" aria-label="${esc(aria)}">${bg}${bars}<line class="axis" x1="${PAD}" x2="${W - PAD}" y1="${base + 1}" y2="${base + 1}"/><line class="med" x1="${X(med).toFixed(1)}" x2="${X(med).toFixed(1)}" y1="2" y2="${base + 4}"><title>London middle</title></line>${mk}<text class="ax" x="${PAD}" y="63">${esc(row.fv(x0))}</text><text class="ax" x="${W - PAD}" y="63" text-anchor="end">${esc(row.fv(x1))}</text></svg>`;
}

function workingHtml(row, cx) {
  const m = R.metrics[row.key], M = cx.M, val = cx.vals[row.key];
  const srcs = (m.source_ids || []).map(id => R.sources.find(s => s.id === id)).filter(Boolean);
  const where = val == null ? '' : `<p><b>Where it sits:</b> ${esc(row.fv(val))} is higher than about ${Math.round(pctOf(row.key, val))}% of ${m.n.toLocaleString('en-GB')} London ${row.noun} (${esc(m.as_of)}).</p>`;
  const catch_ = row.area ? `Neighbourhoods (LSOAs, about 1,700 people each) whose centre is within ${R.catchment_m} m, or the nearest one within 2 km if none; weighted by residents${M.w?.single ? '. Here only the nearest neighbourhood was used.' : '.'}` : row.key === 'fresh_pct' ? `The postcode district ${esc(cx.outcode || '')}.` : `Straight-line distance from the postcode point${row.key === 'venues_800' ? `, within ${R.catchment_m} m` : ''}.`;
  return `<div class="rc-work">${items(cx)}<p><b>How it is worked out:</b> ${row.formula}</p><p><b>Catchment:</b> ${catch_}</p>${where}
    <p><b>Thresholds:</b> the lower third of London sits at or below ${esc(row.fv(m.lo))}, the upper third at or above ${esc(row.fv(m.hi))} (n = ${m.n.toLocaleString('en-GB')}). ${m.direction === '0' ? 'This row is never rated.' : ''}</p>
    ${row.area && M.w && val != null ? lsoaRows(M, row.cols(M)) : ''}${row.list ? row.list(M) : ''}
    <p class="note"><b>Location used:</b> ${cx.method === 'postcodes.io' ? 'postcodes.io (ONS Postcode Directory)' : 'approximate, from venue locations'}.</p>
    <ul class="list">${srcs.map(s => `<li><span>${link(s.url, '<b>' + esc(s.name) + '</b>')} · ${esc(s.publisher)} · ${esc(s.vintage)} · ${esc(s.licence)}<br><small>${esc(s.caveat)}</small></span></li>`).join('')}</ul></div>`;
}
const items = cx => `<h4>${cx.multi ? LETTERS[cx.i] + ': ' : ''}${esc(cx.label)}</h4>`;

// Sale price and private rent lines under Affordability. Never rated; each has its own badge, strip and working.
const PRICE_ROW = { key: 'median_price', noun: 'postcode sectors', fv: priceK };
const RENT_ROW = { key: 'rent_2bed', noun: 'London boroughs', fv: rentFmt };
function priceBlocks(cxs, multi) {
  const P = cxs[0].M.prices; if (!P) return '';
  const who = cx => multi ? `<span class="mkkey" aria-hidden="true">${SHAPES[cx.i]} ${LETTERS[cx.i]}</span> <span class="sr">${LETTERS[cx.i]}: ${esc(cx.label)}. </span>` : '';
  const srcOf = key => (R.metrics[key].source_ids || []).map(id => R.sources.find(s => s.id === id)).filter(Boolean);
  const srcList = key => `<ul class="list">${srcOf(key).map(s => `<li><span>${link(s.url, '<b>' + esc(s.name) + '</b>')} · ${esc(s.publisher)} · ${esc(s.vintage)} · ${esc(s.licence)}<br><small>${esc(s.caveat)}</small></span></li>`).join('')}</ul>`;
  const out = [];
  if (R.metrics.median_price) {
    const m = R.metrics.median_price, win = P.window ? `${monthName(P.window.from)} to ${monthName(P.window.to)}` : `the 12 months to ${monthName(P.as_of)}`, to = monthName(P.window?.to || P.as_of);
    const lis = cxs.map(cx => {
      const p = cx.M.price; let head, cav = '';
      if (p && p.level === 'borough') head = 'No sale price is shown for a whole borough. Type a postcode or a postcode district such as E8 to see one.';
      else if (!p || p.level === 'none') head = `Too few sales to show a typical price${p?.n != null ? ` (${p.n} in the 12 months to ${to})` : ''}.`;
      else if (p.level === 'sector') {
        head = `Typical sale price, this postcode sector (${esc(p.name)}): ${money(p.all)}, from ${fmt(p.n)} sales in the 12 months to ${to}.`;
        cav = [p.flat != null ? `Flats ${money(p.flat)} (${fmt(p.nf)} sales)` : `Flats: too few sales to show a typical price`, p.house != null ? `houses ${money(p.house)} (${fmt(p.nh)} sales)` : `houses: too few sales to show a typical price`].join(' · ');
      } else {
        head = p.sectorTooFew ? `Too few sales in this postcode sector to show a typical price. Postcode district ${esc(p.name)}: ${money(p.all)}, from ${fmt(p.n)} sales in the 12 months to ${to}.` : `Typical sale price, postcode district ${esc(p.name)}: ${money(p.all)}, from ${fmt(p.n)} sales in the 12 months to ${to}.`;
        cav = 'This is the wider district, so it is not placed on the bar chart.';
      }
      return `<li>${who(cx)}<span class="rc-head">${head}</span>${cav ? `<small class="rc-cav">${cav}</small>` : ''}</li>`;
    }).join('');
    const work = cxs.map(cx => { const p = cx.M.price, val = cx.vals.median_price;
      return `<div class="rc-work">${items({ ...cx, multi })}${p && p.level !== 'none' && p.level !== 'borough' ? `<p><b>Area used:</b> ${p.level === 'sector' ? 'postcode sector' : 'postcode district'} ${esc(p.name)}${p.level === 'sector' ? ' (the postcode without its last two letters)' : ''}; ${fmt(p.n)} sales, ${esc(win)}.</p>` : `<p><b>Area used:</b> ${p?.name ? esc(p.name) : 'none'}; fewer than ${P.min_sales || 10} sales, so no typical price is shown.</p>`}
        ${val != null ? `<p><b>Where it sits:</b> ${esc(priceK(val))} is higher than about ${Math.round(pctOf('median_price', val))}% of ${m.n.toLocaleString('en-GB')} London postcode sectors (${esc(m.as_of)}).</p>` : ''}</div>`; }).join('');
    out.push(`<section class="rc-sub" aria-labelledby="rc-price-h"><div class="rc-subhead"><h4 id="rc-price-h">Typical sale price</h4><span class="badge" title="${esc(BADGE_HELP[m.badge])}">${esc(m.badge)}</span></div>
      <ul class="rc-vals">${lis}</ul>${stripSvg(PRICE_ROW, cxs.map(cx => ({ i: cx.i, val: cx.vals.median_price })))}
      <p class="note">Sale prices are for homes that sold, not all homes; a few sales can swing a small area. A sector's typical price mostly reflects its mix of flats and houses and of leasehold and freehold homes.</p>
      <details class="proof"><summary>Show the working</summary>
        <p><b>How it is worked out:</b> the median of individual sale prices (the middle sale, not the average) for standard market sales recorded by HM Land Registry in the postcode sector, ${esc(win)}. A typical price is shown only where there are at least ${P.min_sales || 10} sales. The bar chart spreads the sector medians across London postcode sectors with enough sales. It is never rated: a lower price is not better or worse.</p>
        ${work}<p class="note">Check it yourself: ${link(P.proof?.ppd || 'https://landregistry.data.gov.uk/app/ppd/', 'search sold prices by postcode at HM Land Registry')}. Data to ${esc(monthName(P.as_of))}.</p>${srcList('median_price')}</details></section>`);
  }
  if (R.metrics.rent_2bed && P.rents) {
    const m = R.metrics.rent_2bed, RT = P.rents;
    const lis = cxs.map(cx => {
      const r = cx.M.rent; let head, cav = '';
      if (!r || r.two_bed == null) head = `No two-bed private rent figure for ${r?.name ? esc(r.name) : 'this borough'}.`;
      else {
        head = `Average two-bed private rent, ${esc(r.name)}: ${rentFmt(r.two_bed)} a month (ONS, borough average, official statistics in development).`;
        cav = [r.one_bed != null ? `One-bed ${rentFmt(r.one_bed)}` : null, r.three_bed != null ? `three-bed ${rentFmt(r.three_bed)}` : null, r.annual_change_pct != null ? `${num1(r.annual_change_pct)}% change over the year (all private rents)` : null].filter(Boolean).join(' · ');
      }
      return `<li>${who(cx)}<span class="rc-head">${head}</span>${cav ? `<small class="rc-cav">${cav}</small>` : ''}</li>`;
    }).join('');
    const work = cxs.map(cx => { const r = cx.M.rent, val = cx.vals.rent_2bed;
      return `<div class="rc-work">${items({ ...cx, multi })}<p><b>Area used:</b> ${r?.name ? 'borough ' + esc(r.name) : 'none'} (from the postcode lookup, or the nearest neighbourhood if that was unavailable).</p>
        ${val != null ? `<p><b>Where it sits:</b> ${esc(rentFmt(val))} a month is higher than about ${Math.round(pctOf('rent_2bed', val))}% of ${m.n.toLocaleString('en-GB')} London boroughs (${esc(m.as_of)}).</p>` : ''}</div>`; }).join('');
    out.push(`<section class="rc-sub" aria-labelledby="rc-rent-h"><div class="rc-subhead"><h4 id="rc-rent-h">Private rent, two-bed</h4><span class="badge" title="${esc(BADGE_HELP[m.badge])}">${esc(m.badge)}</span></div>
      <ul class="rc-vals">${lis}</ul>${stripSvg(RENT_ROW, cxs.map(cx => ({ i: cx.i, val: cx.vals.rent_2bed })))}
      <p class="note">Official statistics in development: local-authority estimates are best read as trends. This is a modelled borough average, not a specific street, and covers private rents only (shared and social rent are excluded).</p>
      <details class="proof"><summary>Show the working</summary>
        <p><b>How it is worked out:</b> the ONS Price Index of Private Rents publishes an average monthly private rent for each borough by number of bedrooms; the latest month is ${esc(monthName(RT.as_of))}. The bar chart spreads the two-bed figure across the ${m.n.toLocaleString('en-GB')} London boroughs with one. It is never rated.</p>
        ${work}${RT.caveat ? `<p class="note">${esc(RT.caveat)}</p>` : ''}${srcList('rent_2bed')}</details></section>`);
  }
  return out.join('');
}

function rowHtml(row, cxs) {
  const m = R.metrics[row.key], multi = cxs.length > 1;
  const vals = cxs.map(cx => {
    const val = cx.vals[row.key], L = label(row.key, val, cx.M);
    const none = val == null;
    const head = none ? (row.area ? 'No neighbourhood data within 2 km, or too few residents for a rate.' : 'No value for this place.') : `${row.phrase(val)} is higher than in ${tenths(pctOf(row.key, val))} London ${row.noun}.`;
    return `<li>${multi ? `<span class="mkkey" aria-hidden="true">${SHAPES[cx.i]} ${LETTERS[cx.i]}</span> <span class="sr">${LETTERS[cx.i]}: ${esc(cx.label)}. </span>` : ''}<span class="pill ${L.cls}">${esc(L.text)}</span><span class="rc-head">${head}</span>${L.caveat ? `<small class="rc-cav">${esc(L.caveat)}</small>` : ''}</li>`;
  }).join('');
  const facts = cxs.map(cx => row.facts ? `<ul class="rc-facts">${multi ? `<li class="rc-who">${SHAPES[cx.i]} ${LETTERS[cx.i]}</li>` : ''}${(row.facts ? row.facts(cx.M, cx.vals) : []).map(f => `<li>${f}</li>`).join('')}</ul>` : '').join('');
  return `<article class="rc-row" id="rc-${row.id}" aria-labelledby="rc-${row.id}-h"><header><h3 id="rc-${row.id}-h">${row.title}</h3><span class="badge" title="${esc(BADGE_HELP[m.badge])}">${esc(m.badge)}</span></header>
    <ul class="rc-vals">${vals}</ul>${stripSvg(row, cxs.map(cx => ({ i: cx.i, val: cx.vals[row.key] })))}${facts}${row.sub ? priceBlocks(cxs, multi) : ''}
    <p class="rc-dont"><b>What we don't cover:</b> ${row.dont}</p>
    <details class="proof"><summary>Show the working</summary>${cxs.map(cx => workingHtml(row, { ...cx, multi })).join('')}</details></article>`;
}

function cardHtml(cxs, notices) {
  const multi = cxs.length > 1, names = cxs.map(c => c.label);
  const dates = [...new Set(Object.values(R.metrics).map(m => m.as_of))].sort().join(', ');
  return `<div class="rc card"><h2 id="rc-title" tabindex="-1">${multi ? 'Report card: ' + names.map(esc).join(' and ') : 'Report card: ' + esc(names[0])}</h2>
    ${notices.map(n => `<p class="note rc-notice" role="status">${esc(n)}</p>`).join('')}
    ${multi ? `<p class="rc-legend">${cxs.map(c => `<span>${SHAPES[c.i]} ${LETTERS[c.i]} ${esc(c.label)}</span>`).join('')}</p>` : ''}
    ${ROWS.filter(r => R.metrics[r.key]).map(r => rowHtml(r, cxs)).join('')}
    <p class="rc-foot"><b>No overall score. These describe places, not the people in them. Compare, then visit.</b></p>
    <p class="note">Data dates: ${esc(dates)}. Each bar chart shows how London's neighbourhoods are spread; markers show where each place falls. Sources are listed under "Show the working" in each row. ${esc(R.licence)}.</p>
    <p class="note">${esc(LICENCE)}</p>
    <p class="row"><button class="btn" id="rpcopy" type="button">Copy link</button></p></div>`;
}

// ---------- page glue ----------
function rpInputs() { return [...document.querySelectorAll('.rpq')]; }
function setInputs(list) {
  const extra = $('rpextra'); extra.innerHTML = '';
  $('rpq').value = list[0] || '';
  list.slice(1, 3).forEach((v, k) => addInput(v, k + 2));
  $('rpadd').hidden = list.length >= 3;
}
function addInput(v = '', n) {
  const have = rpInputs().length; if (have >= 3) return;
  n ??= have + 1;
  const d = document.createElement('div');
  d.className = 'row filters rp-extra';
  d.innerHTML = `<label class="sr" for="rpq${n}">Postcode ${LETTERS[n - 1]}</label><input id="rpq${n}" class="rpq" type="search" placeholder="N16 5AA" autocomplete="off" value="${esc(v)}"><button class="btn" type="button" aria-label="Remove postcode ${LETTERS[n - 1]}">Remove</button>`;
  d.querySelector('button').onclick = () => { d.remove(); $('rpadd').hidden = false; $('rpq').focus(); };
  d.querySelector('input').onkeydown = e => { if (e.key === 'Enter') submit(); };
  $('rpextra').appendChild(d);
  if (rpInputs().length >= 3) $('rpadd').hidden = true;
}
const codesFrom = () => [...new Set(rpInputs().map(i => normPc(i.value.trim())).filter(Boolean))].slice(0, 3);
const hashFor = list => '#report/' + list.map(encodeURIComponent).join(',');
function submit() {
  const list = codesFrom(); if (!list.length) { $('rpq').focus(); return; }
  const h = hashFor(list);
  if (location.hash === h) { lastArg = null; openArg(list.map(encodeURIComponent).join(',')); } else location.hash = h;
}

export function initReport() {
  $('rpgo').onclick = submit;
  $('rpq').onkeydown = e => { if (e.key === 'Enter') submit(); };
  $('rpadd').onclick = () => { addInput(); rpInputs().at(-1)?.focus(); };
}

export async function openArg(arg) {
  arg = arg || '';
  if (arg === lastArg && $('rpout').children.length) return;
  lastArg = arg;
  const list = [...new Set(arg.split(',').map(s => normPc(dec(s))).filter(Boolean))].slice(0, 3);
  const out = $('rpout'), my = ++token;
  $('rpstatus').textContent = '';
  if (!list.length) { out.innerHTML = '<p class="note">Enter a postcode to see its report card, or add up to two more to compare.</p>'; setInputs([]); return; }
  setInputs(list);
  out.innerHTML = '<p class="note">Loading…</p>';
  try {
    R ??= await getApi('report');
  } catch {
    delete api.report;
    out.innerHTML = '<p class="note">The report card data is not available yet. It appears after the next daily update.</p>';
    lastArg = null;
    return;
  }
  const located = await Promise.all(list.map(q => locate(q)));
  const done = await Promise.all(located.map(async (l, i) => {
    if (l.err) return { ...l, i, fail: true };
    try { const M = await metricsFor(l, l.outcode); return { ...l, i, M, vals: M.values, outcode: l.outcode }; }
    catch (e) { return { ...l, i, fail: true, err: 'Could not build this report (' + e.message + ').' }; }
  }));
  if (my !== token) return;
  const ok = done.filter(d => !d.fail), notices = done.filter(d => d.fail).map(d => d.err).concat(ok.filter(d => d.note).map(d => `${d.label}: ${d.note}`));
  if (!ok.length) { out.innerHTML = notices.map(n => `<p class="note rc-notice" role="status">${esc(n)}</p>`).join(''); return; }
  out.innerHTML = cardHtml(ok, notices);
  $('rpstatus').textContent = 'Report ready for ' + ok.map(d => d.label).join(' and ');
  try { history.replaceState(null, '', hashFor(ok.map(d => d.label))); lastArg = ok.map(d => encodeURIComponent(d.label)).join(','); } catch { /* sandboxed frame */ }
  $('rpcopy').onclick = async () => { try { await navigator.clipboard.writeText(location.href); toast('Link copied.'); } catch { toast(location.href); } };
  $('rc-title')?.focus({ preventScroll: true });
}
