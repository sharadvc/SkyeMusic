const CACHE_NAME = "skye-player-v1"
const STATIC_ASSETS = ["/", "/index.html", "/manifest.json"]

self.addEventListener("install", (evt) => {
  evt.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(STATIC_ASSETS)
    })
  )
  self.skipWaiting()
})

self.addEventListener("activate", (evt) => {
  evt.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
      )
    })
  )
  self.clients.claim()
})

self.addEventListener("fetch", (evt) => {
  const url = new URL(evt.request.url)
  // Network-first strategy for API calls, Cache-first for static build assets
  if (url.pathname.startsWith("/api/")) {
    return
  }

  evt.respondWith(
    caches.match(evt.request).then((cachedResp) => {
      if (cachedResp) {
        // Fetch in background to revalidate cache
        fetch(evt.request).then((networkResp) => {
          if (networkResp.status === 200) {
            caches.open(CACHE_NAME).then((cache) => cache.put(evt.request, networkResp))
          }
        }).catch(() => {})
        return cachedResp
      }
      return fetch(evt.request)
    })
  )
})
