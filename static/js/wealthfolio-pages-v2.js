/*
 * Navigation helpers for derived Wealthfolio pages.
 *
 * The server remains the source of truth. This script only carries the
 * current read-only query scope to links and GET forms so that a period,
 * date, search or sort choice survives drill-down and back navigation.
 */
(function () {
  "use strict";

  function ready(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, { once: true });
    } else callback();
  }

  ready(function () {
    var preserved = new Set([
      "periodo", "data", "busca", "dimensao", "ordenar", "direcao",
      "conta", "categoria", "natureza", "status", "stage", "pagina",
    ]);
    var current = new URL(window.location.href);

    function mergeQuery(raw) {
      if (!raw || raw === "#") return raw;
      var target;
      try { target = new URL(raw, window.location.href); } catch (_error) { return raw; }
      if (target.origin !== window.location.origin) return raw;
      preserved.forEach(function (key) {
        if (!target.searchParams.has(key) && current.searchParams.has(key)) {
          target.searchParams.set(key, current.searchParams.get(key));
        }
      });
      return target.pathname + target.search + target.hash;
    }

    document.querySelectorAll("[data-wf2-preserve-query]").forEach(function (link) {
      link.setAttribute("href", mergeQuery(link.getAttribute("href")));
    });

    document.querySelectorAll("form[data-wf2-query-form]").forEach(function (form) {
      if ((form.getAttribute("method") || "get").toLowerCase() !== "get") return;
      preserved.forEach(function (key) {
        if (!current.searchParams.has(key) || form.elements.namedItem(key)) return;
        var input = document.createElement("input");
        input.type = "hidden";
        input.name = key;
        input.value = current.searchParams.get(key);
        form.appendChild(input);
      });
    });

    // Keep keyboard focus visible after a route transition and make the
    // main landmark the target for screen-reader users who activate a link.
    var main = document.getElementById("main-content");
    if (main && window.location.hash === "#main-content") main.focus({ preventScroll: true });
  });
}());
