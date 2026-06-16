const CACHE_NAME = "scrapower-v1";
self.addEventListener("install", (event) => {
  console.log("[scrapower:sw] installed");
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  console.log("[scrapower:sw] activated");
  self.clients.claim();
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith("/static/") || url.pathname.startsWith("/blobs/")) {
    event.respondWith(cacheFirst(event.request));
  }
});
async function cacheFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response("Offline", { status: 503 });
  }
}
