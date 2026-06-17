// Scrapower Embed — injectable sur n'importe quel site
// <script src="https://scrapower.talos-int.com/embed.js"></script>
(function () {
  var COORDINATOR = "https://scrapower.talos-int.com";

  // Éviter les doubles injections
  if (document.getElementById("scrapower-widget")) return;
  if (document.querySelector('script[data-scrapower-loaded]')) return;

  // Marqueur pour éviter les doubles chargements
  var marker = document.createElement("script");
  marker.setAttribute("data-scrapower-loaded", "true");
  document.head.appendChild(marker);

  // Charger le worker principal
  var script = document.createElement("script");
  script.type = "module";
  script.src = COORDINATOR + "/static/worker.js";
  script.onerror = function () {
    console.warn("[scrapower] Failed to load worker.js");
  };
  document.head.appendChild(script);

  // Service Worker (pour persistance onglet)
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker
      .register(COORDINATOR + "/sw.js", { scope: "/" })
      .catch(function () {});
  }
})();
