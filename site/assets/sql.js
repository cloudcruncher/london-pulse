// SQL lab: DuckDB-WASM in the browser over the published parquet files. Lazy-loaded on first run.
const DUCK = 'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const RATED = `rating IN ('0','1','2','3','4','5')`;
const EAT = `business_type IN ('Restaurant/Cafe/Canteen','Takeaway/sandwich shop','Pub/bar/nightclub')`;
const COFFEE = `regexp_matches(lower(name), 'coffee|espresso|roastery|barista|caffe|caffè')`;

// [key, group, label, sql]
const PRESETS = [
  ['five-star', 'Ratings', '5-star share by borough', `SELECT authority, count(*) AS rated,\n  round(100.0 * count(*) FILTER (rating = '5') / count(*), 1) AS pct_five_star\nFROM venues WHERE ${RATED}\nGROUP BY 1 ORDER BY 3 DESC`],
  ['stale', 'Ratings', 'Whose inspections are oldest?', `-- share of rated venues last inspected over 2 years ago\nSELECT authority, count(*) AS rated,\n  round(100.0 * count(*) FILTER (rating_date < current_date - INTERVAL 2 YEAR) / count(*), 1) AS pct_older_than_2y\nFROM venues WHERE ${RATED} AND ${EAT} AND rating_date IS NOT NULL\nGROUP BY 1 HAVING count(*) >= 300 ORDER BY 3 DESC`],
  ['by-type', 'Ratings', 'Pubs vs restaurants vs takeaways', `SELECT business_type, count(*) AS rated,\n  round(100.0 * count(*) FILTER (rating = '5') / count(*), 1) AS pct_five_star,\n  round(100.0 * count(*) FILTER (rating IN ('0','1','2')) / count(*), 1) AS pct_low\nFROM venues WHERE ${RATED} AND ${EAT}\nGROUP BY 1 ORDER BY 3 DESC`],
  ['weak-districts', 'Ratings', 'Postcode districts with the most low ratings', `SELECT split_part(postcode, ' ', 1) AS district, count(*) AS rated,\n  round(100.0 * count(*) FILTER (rating IN ('0','1','2')) / count(*), 1) AS pct_low\nFROM venues WHERE ${RATED} AND ${EAT} AND postcode <> ''\nGROUP BY 1 HAVING count(*) >= 150 ORDER BY 3 DESC LIMIT 25`],
  ['urgent', 'Ratings', 'Venues rated 0 (urgent improvement)', `SELECT name, business_type, authority, postcode, rating_date\nFROM venues WHERE rating = '0' ORDER BY rating_date DESC LIMIT 100`],
  ['awaiting', 'New openings', 'Awaiting inspection by postcode district', `SELECT split_part(postcode, ' ', 1) AS district, count(*) AS awaiting\nFROM venues WHERE rating = 'AwaitingInspection' AND ${EAT} AND postcode <> ''\nGROUP BY 1 ORDER BY 2 DESC LIMIT 25`],
  ['awaiting-share', 'New openings', 'Share awaiting inspection by borough', `SELECT authority, count(*) AS venues,\n  round(100.0 * count(*) FILTER (rating = 'AwaitingInspection') / count(*), 1) AS pct_awaiting\nFROM venues WHERE ${EAT} GROUP BY 1 ORDER BY 3 DESC`],
  ['removed', 'Day over day', 'Removed from the register, latest day', `SELECT event_date, name, authority, postcode, old_rating FROM events\nWHERE event = 'removed' AND event_date = (SELECT max(event_date) FROM events)\nORDER BY authority, name`],
  ['new', 'Day over day', 'New premises by borough, all days', `SELECT authority, count(*) AS new_premises FROM events\nWHERE event = 'new' GROUP BY 1 ORDER BY 2 DESC`],
  ['drops', 'Day over day', 'Rating drops', `SELECT event_date, name, authority, old_rating, new_rating FROM events\nWHERE event = 'rating_changed' AND try_cast(new_rating AS INT) < try_cast(old_rating AS INT)\nORDER BY event_date DESC LIMIT 50`],
  ['rises', 'Day over day', 'Rating improvements', `SELECT event_date, name, authority, old_rating, new_rating FROM events\nWHERE event = 'rating_changed' AND try_cast(new_rating AS INT) > try_cast(old_rating AS INT)\nORDER BY event_date DESC LIMIT 50`],
  ['growth', 'Day over day', 'Venue count over time', `SELECT snapshot_date, sum(premises) AS premises, sum(eating_drinking) AS eating_drinking\nFROM history GROUP BY 1 ORDER BY 1`],
  ['takeaway', 'High street character', 'Takeaway share by borough', `SELECT authority, count(*) AS venues,\n  round(100.0 * count(*) FILTER (business_type = 'Takeaway/sandwich shop') / count(*), 1) AS pct_takeaway\nFROM venues WHERE ${EAT} GROUP BY 1 HAVING count(*) >= 300 ORDER BY 3 DESC`],
  ['coffee', 'High street character', 'Coffee-named venues by borough', `SELECT authority, count(*) AS coffee_named FROM venues\nWHERE ${COFFEE}\nGROUP BY 1 ORDER BY 2 DESC`],
  ['names', 'High street character', 'Most common venue names', `SELECT upper(name) AS name, count(*) AS n FROM venues\nGROUP BY 1 ORDER BY 2 DESC LIMIT 25`],
  ['pubs', 'High street character', 'Pubs and bars per borough', `SELECT authority, count(*) AS pubs FROM venues\nWHERE business_type = 'Pub/bar/nightclub' GROUP BY 1 ORDER BY 2 DESC`],
  ['district', 'Explore', 'Everything in one postcode district (edit E8)', `SELECT name, business_type, rating, postcode FROM venues\nWHERE split_part(postcode, ' ', 1) = 'E8' ORDER BY name LIMIT 200`],
];

// Question builder: measure x group-by -> SQL
const MEASURES = {
  venues: ['number of venues', 'count(*) AS venues'],
  five: ['share rated 5 (%)', `round(100.0 * count(*) FILTER (rating = '5') / nullif(count(*) FILTER (${RATED}), 0), 1) AS pct_five_star`],
  low: ['venues rated 0 to 2', `count(*) FILTER (rating IN ('0','1','2')) AS low_rated`],
  awaiting: ['venues awaiting first inspection', `count(*) FILTER (rating = 'AwaitingInspection') AS awaiting`],
  coffee: ['coffee-named venues', `count(*) FILTER (${COFFEE}) AS coffee_named`],
};
const GROUPS = {
  authority: ['borough', 'authority'], type: ['venue type', 'business_type'],
  district: ['postcode district', `split_part(postcode, ' ', 1)`], rating: ['hygiene rating', 'rating'],
};

let dbp, lastRows = [], lastCols = [], boot_;
async function boot(stat) {
  stat('Loading DuckDB…');
  const duck = await import(DUCK);
  const bundle = await duck.selectBundle(duck.getJsDelivrBundles());
  const url = URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker}");`], { type: 'text/javascript' }));
  const db = new duck.AsyncDuckDB(new duck.ConsoleLogger(duck.LogLevel.WARNING), new Worker(url));
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(url);
  stat('Loading data…');
  const conn = await db.connect();
  for (const t of ['venues', 'events', 'history']) {
    const buf = new Uint8Array(await (await fetch(`api/v1/${t}.parquet`)).arrayBuffer());
    await db.registerFileBuffer(`${t}.parquet`, buf);
    await conn.query(`CREATE VIEW ${t} AS SELECT * FROM parquet_scan('${t}.parquet')`);
  }
  return conn;
}

function csv() {
  const q = v => { const s = String(v ?? ''); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  const text = [lastCols.join(','), ...lastRows.map(r => lastCols.map(c => q(r[c])).join(','))].join('\n');
  const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(new Blob([text], { type: 'text/csv' })), download: 'london-pulse-query.csv' });
  a.click(); URL.revokeObjectURL(a.href);
}

let run;
export function openArg(arg) {   // #sql/<preset-key> or #sql/q=<encoded sql>
  if (!arg || !run) return;
  if (arg.startsWith('q=')) { try { $('sqlbox').value = decodeURIComponent(arg.slice(2)); run(); } catch { /* bad link */ } return; }
  const p = PRESETS.find(x => x[0] === arg);
  if (p) { $('sqlbox').value = p[3]; run(); }
}

export function initSql() {
  const box = $('sqlbox'), stat = t => { $('sqlstat').textContent = t; };
  box.value = PRESETS[0][3];
  const groups = [...new Set(PRESETS.map(p => p[1]))];
  $('sqlpresets').innerHTML = groups.map(g => `<div class="pgroup"><small>${esc(g)}</small><div class="chips">${PRESETS.filter(p => p[1] === g).map(p => `<button class="chip" data-k="${p[0]}">${esc(p[2])}</button>`).join('')}</div></div>`).join('');
  $('sqlpresets').onclick = e => { const b = e.target.closest('[data-k]'); if (b) { box.value = PRESETS.find(p => p[0] === b.dataset.k)[3]; run(); } };

  // builder
  $('b-measure').innerHTML = Object.entries(MEASURES).map(([k, [l]]) => `<option value="${k}">${esc(l)}</option>`).join('');
  $('b-group').innerHTML = Object.entries(GROUPS).map(([k, [l]]) => `<option value="${k}">${esc(l)}</option>`).join('');
  $('b-type').insertAdjacentHTML('beforeend', ['Restaurant/Cafe/Canteen', 'Takeaway/sandwich shop', 'Pub/bar/nightclub'].map(t => `<option>${esc(t)}</option>`).join(''));
  fetch('api/v1/boroughs.json').then(r => r.json()).then(d => $('b-borough').insertAdjacentHTML('beforeend', d.boroughs.map(b => `<option>${esc(b.name)}</option>`).join('')));
  $('b-go').onclick = () => {
    const [, m] = MEASURES[$('b-measure').value], [, g] = GROUPS[$('b-group').value];
    const where = [EAT, $('b-type').value && `business_type = '${$('b-type').value.replace(/'/g, "''")}'`, $('b-borough').value && `authority = '${$('b-borough').value.replace(/'/g, "''")}'`].filter(Boolean);
    box.value = `SELECT ${g} AS ${$('b-group').value === 'authority' ? 'borough' : $('b-group').value}, ${m}\nFROM venues\nWHERE ${where.join('\n  AND ')}\nGROUP BY 1 ORDER BY 2 DESC LIMIT ${$('b-limit').value}`;
    run();
  };

  $('schema').innerHTML = `<p class="note"><code>venues</code> one row per FSA-registered business today (~82k). <code>events</code> every new / removed / re-rated premises since tracking began. <code>history</code> daily counts by borough.</p>
    <ul class="cols-list"><li><code>venues</code>: fhrsid, name, business_type, address, postcode, rating, rating_date, authority, lon, lat</li>
    <li><code>events</code>: event_date, event (new | removed | rating_changed), fhrsid, name, business_type, authority, postcode, old_rating, new_rating, lon, lat</li>
    <li><code>history</code>: snapshot_date, authority, premises, eating_drinking, five_star, awaiting</li></ul>
    <p class="note">Ratings are text: '0'-'5', 'AwaitingInspection', 'Exempt'. It's DuckDB SQL, so <code>FILTER</code>, <code>regexp_matches</code>, <code>split_part</code> and <code>INTERVAL</code> all work.</p>`;

  run = async () => {
    try {
      boot_ ??= boot(stat); dbp = boot_;
      const conn = await dbp;
      stat('Running…');
      const t0 = performance.now();
      const res = await conn.query(box.value);
      lastRows = res.toArray().map(r => Object.fromEntries(Object.entries(r.toJSON()).map(([k, v]) => [k, typeof v === 'bigint' ? Number(v) : v])));
      lastCols = res.schema.fields.map(f => f.name);
      const shown = lastRows.slice(0, 500);
      $('sqlout').innerHTML = `<thead><tr>${lastCols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${shown.map(r => `<tr>${lastCols.map(c => `<td>${esc(r[c] instanceof Date ? r[c].toISOString().slice(0, 10) : r[c])}</td>`).join('')}</tr>`).join('')}</tbody>`;
      stat(`${lastRows.length.toLocaleString('en-GB')} rows${lastRows.length > 500 ? ' (first 500 shown)' : ''} · ${Math.round(performance.now() - t0)} ms`);
      $('sqlcsv').hidden = !lastRows.length;
    } catch (err) {
      if (String(err).includes('fetch')) boot_ = null;
      stat('Error: ' + (err.message || err));
    }
  };
  $('sqlrun').onclick = run; $('sqlcsv').onclick = csv;
  $('sqlshare').onclick = async () => {
    const link = `${location.origin}${location.pathname}#sql/q=${encodeURIComponent(box.value)}`;
    try { await navigator.clipboard.writeText(link); stat('Link copied'); } catch { stat('Copy failed: ' + link); }
  };
  box.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); run(); } });
}
