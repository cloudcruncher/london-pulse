// SQL lab: DuckDB-WASM in the browser over the published parquet files. Lazy-loaded on first run.
const DUCK = 'https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const PRESETS = [
  ['5-star share by borough', `SELECT authority, count(*) AS rated,\n  round(100.0 * count(*) FILTER (rating = '5') / count(*), 1) AS pct_five_star\nFROM venues WHERE rating IN ('0','1','2','3','4','5')\nGROUP BY 1 ORDER BY 3 DESC`],
  ['Awaiting inspection by postcode district', `SELECT split_part(postcode, ' ', 1) AS district, count(*) AS awaiting\nFROM venues WHERE rating = 'AwaitingInspection' AND postcode <> ''\nGROUP BY 1 ORDER BY 2 DESC LIMIT 25`],
  ['Most common venue names', `SELECT upper(name) AS name, count(*) AS n FROM venues\nGROUP BY 1 ORDER BY 2 DESC LIMIT 25`],
  ['Coffee-named venues by borough', `SELECT authority, count(*) AS coffee_named FROM venues\nWHERE regexp_matches(lower(name), 'coffee|espresso|roastery|barista|caffe|caffè')\nGROUP BY 1 ORDER BY 2 DESC`],
  ['Removed from the register, latest day', `SELECT event_date, name, authority, postcode, old_rating FROM events\nWHERE event = 'removed' AND event_date = (SELECT max(event_date) FROM events)\nORDER BY authority, name`],
  ['New premises by borough, all days', `SELECT authority, count(*) AS new_premises FROM events\nWHERE event = 'new' GROUP BY 1 ORDER BY 2 DESC`],
  ['Rating drops', `SELECT event_date, name, authority, old_rating, new_rating FROM events\nWHERE event = 'rating_changed' AND try_cast(new_rating AS INT) < try_cast(old_rating AS INT)\nORDER BY event_date DESC LIMIT 50`],
  ['Venue growth over time', `SELECT snapshot_date, sum(premises) AS premises, sum(eating_drinking) AS eating_drinking\nFROM history GROUP BY 1 ORDER BY 1`],
];

let dbp, lastRows = [], lastCols = [];
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

export function initSql() {
  const box = $('sqlbox'), stat = t => { $('sqlstat').textContent = t; };
  box.value = PRESETS[0][1];
  $('sqlpresets').innerHTML = PRESETS.map(([l], i) => `<button class="chip" data-i="${i}">${esc(l)}</button>`).join('');
  $('sqlpresets').onclick = e => { const b = e.target.closest('[data-i]'); if (b) { box.value = PRESETS[+b.dataset.i][1]; run(); } };
  async function run() {
    try {
      dbp ??= boot(stat);
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
      if (!dbp || String(err).includes('fetch')) dbp = null;
      stat('Error: ' + (err.message || err));
    }
  }
  $('sqlrun').onclick = run; $('sqlcsv').onclick = csv;
  box.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); run(); } });
}
