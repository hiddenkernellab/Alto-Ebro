// Caché de interfaz exclusivamente. Los datos hidrológicos deben venir siempre de red.
const NAME = 'alto-ebro-ui-v05';
const ASSETS = ['./', './index.html', './manifest.webmanifest', './icons/icon-192.png', './icons/icon-512.png'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(NAME).then(c=>c.addAll(ASSETS)));self.skipWaiting();});
self.addEventListener('activate',e=>{e.waitUntil(Promise.all([caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==NAME).map(k=>caches.delete(k)))),self.clients.claim()]));});
self.addEventListener('fetch',e=>{
  const req=e.request;
  if(req.method!=='GET')return;
  const u=new URL(req.url);
  if(u.pathname.endsWith('/data/saih.json')||u.hostname.includes('open-meteo.com'))return; // siempre red
  if(u.origin!==location.origin)return;
  e.respondWith(fetch(req).then(res=>{if(res.ok){const cp=res.clone();caches.open(NAME).then(c=>c.put(req,cp));}return res;}).catch(()=>caches.match(req)));
});
