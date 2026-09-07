// Service Worker móvil: cachea SÓLO el shell de la app; la API (fetch al
// backend /api/*) se sirve siempre en red para no servir datos obsoletos.

const OTTER_CACHE = 'otter-shell-v1'

const OTTER_SHELL = ['/', '/m/', '/m/manifest.webmanifest']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(OTTER_CACHE)
      .then((cache) => cache.addAll(OTTER_SHELL))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== OTTER_CACHE).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url)
  if (url.pathname.startsWith('/api/')) {
    return
  }
  event.respondWith(
    caches.match(event.request).then((hit) => hit || fetch(event.request)),
  )
})