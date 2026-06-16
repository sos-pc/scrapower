// Scrapower Service Worker — caches WASM modules and Pyodide.
// Runs even when the tab is closed, handles background caching.

const CACHE_NAME = "scrapower-v1";

self.addEventListener("install", (event: ExtendableEvent) => {
  console.log("[scrapower:sw] installed");
  (self as any).skipWaiting();
});

self.addEventListener("activate", (event: ExtendableEvent) => {
  console.log("[scrapower:sw] activated");
  (self as any).clients.claim();
});

self.addEventListener("fetch", (event: FetchEvent) => {
  const url = new URL(event.request.url);

  // Cache static assets and blob downloads
  if (
    url.pathname.startsWith("/static/") ||
    url.pathname.startsWith("/blobs/")
  ) {
    event.respondWith(cacheFirst(event.request));
  }
  // Pass through everything else (API calls, WebSocket)
});

async function cacheFirst(request: Request): Promise<Response> {
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
