// Offline shell + stale-while-revalidate for data. Bump VERSION when shell files change.
const VERSION = 'lp-v23';
const SHELL = ['./', 'index.html', 'assets/app.css', 'assets/app.js', 'assets/sql.js', 'assets/mapbox.js', 'config.js', 'assets/icon.svg', 'manifest.webmanifest'];

self.addEventListener('install', e => e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith('lp-') && k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim())));

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET' || new URL(req.url).origin !== location.origin) return;
  if (req.url.endsWith('.parquet')) return;   // large; leave to the HTTP cache
  e.respondWith(caches.open(VERSION).then(async cache => {
    const hit = await cache.match(req);
    const refresh = fetch(req).then(res => { if (res.ok) cache.put(req, res.clone()); return res; });
    if (hit) { refresh.catch(() => {}); return hit; }   // cached: serve now, update in background
    return refresh;                                      // first visit: network
  }));
});
