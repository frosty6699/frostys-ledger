const V = '202609262304';
const PAGES = 'ledger-pages', ASSETS = 'ledger-assets-' + V, IMGS = 'ledger-img', FONTS = 'ledger-fonts';
self.addEventListener('install', e => {
  self.skipWaiting();
  e.waitUntil(caches.open(PAGES).then(c => c.addAll(['./', 'archive.html'])).catch(() => {}));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(keys => Promise.all(keys.filter(k => k.startsWith('ledger-assets-') && k !== ASSETS).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
function trim(cache, max) {
  cache.keys().then(keys => { if (keys.length > max) cache.delete(keys[0]).then(() => trim(cache, max)); });
}
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req)
      .then(res => { const copy = res.clone(); caches.open(PAGES).then(c => c.put(req, copy)); return res; })
      .catch(() => caches.match(req, {ignoreSearch: true}).then(r => r || caches.match('./'))));
  } else if (url.origin === location.origin) {
    e.respondWith(caches.open(ASSETS).then(c => c.match(req).then(r => r || fetch(req).then(res => {
      if (res.ok) c.put(req, res.clone());
      return res;
    }))));
  } else if (/fonts\.(googleapis|gstatic)\.com$/.test(url.hostname)) {
    e.respondWith(caches.open(FONTS).then(c => c.match(req).then(r => {
      const net = fetch(req).then(res => { c.put(req, res.clone()); return res; }).catch(() => r);
      return r || net;
    })));
  } else if (req.destination === 'image') {
    e.respondWith(caches.open(IMGS).then(c => c.match(req).then(r => r || fetch(req).then(res => {
      c.put(req, res.clone()).then(() => trim(c, 40)).catch(() => {});
      return res;
    }).catch(() => r || Response.error()))));
  }
});
