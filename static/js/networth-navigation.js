/* Preserva a posição ao navegar entre telas server-side.
 *
 * Os cartões do NetWorth são links GET por desenho: cada estado pode ser
 * copiado e reaberto. O navegador, porém, começa a nova resposta no topo.
 * Guardamos uma única navegação de estado do conteúdo principal e a consumimos
 * depois do carregamento. A navegação global, links para CB/CRV, novas abas e
 * cliques modificados ficam intocados.
 */
(function () {
  "use strict";

  var STORAGE_KEY = "networth:scroll-restoration";
  var MAX_AGE_MS = 10000;

  function read(key) {
    try {
      return window.sessionStorage.getItem(key);
    } catch (_error) {
      return null;
    }
  }

  function remove(key) {
    try {
      window.sessionStorage.removeItem(key);
    } catch (_error) {
      // Storage may be disabled; navigation continues normally.
    }
  }

  function write(key, value) {
    try {
      window.sessionStorage.setItem(key, value);
    } catch (_error) {
      // Storage may be disabled; navigation continues normally.
    }
  }

  function restore() {
    var raw = read(STORAGE_KEY);
    if (!raw) return;

    var pending;
    try {
      pending = JSON.parse(raw);
    } catch (_error) {
      remove(STORAGE_KEY);
      return;
    }
    remove(STORAGE_KEY);

    if (!pending || pending.url !== window.location.href) return;
    if (typeof pending.at !== "number" || Date.now() - pending.at > MAX_AGE_MS) return;

    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(function () {
        window.scrollTo(pending.left || 0, pending.top || 0);
      });
    });
  }

  function remember(event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

    var anchor = event.target.closest ? event.target.closest("a[href]") : null;
    if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
    // The global header/sidebar are navigation, not in-page state changes.
    // Only controls inside the dashboard content should restore their scroll.
    if (!anchor.closest || !anchor.closest("main")) return;

    var target;
    try {
      target = new URL(anchor.href, window.location.href);
    } catch (_error) {
      return;
    }
    if (target.origin !== window.location.origin) return;
    if (target.href === window.location.href && target.hash) return;

    write(STORAGE_KEY, JSON.stringify({
      url: target.href,
      top: window.scrollY,
      left: window.scrollX,
      at: Date.now()
    }));
  }

  function rememberForm(event) {
    var form = event.target;
    if (!form || (form.method || "get").toLowerCase() !== "get" || form.target === "_blank") return;
    if (!form.closest || !form.closest("main")) return;

    var target;
    try {
      target = new URL(form.action || window.location.href, window.location.href);
      var query = new URLSearchParams(new FormData(form));
      target.search = query.toString();
    } catch (_error) {
      return;
    }
    if (target.origin !== window.location.origin) return;

    write(STORAGE_KEY, JSON.stringify({
      url: target.href,
      top: window.scrollY,
      left: window.scrollX,
      at: Date.now()
    }));
  }

  document.addEventListener("click", remember, true);
  document.addEventListener("submit", rememberForm, true);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", restore, { once: true });
  } else {
    restore();
  }
}());
