/*
 * Presentation-only shell behavior. Financial state remains server-owned and
 * URL-addressable; this file handles privacy, mobile navigation and keyboard
 * escape only.
 */
(function () {
  "use strict";

  function ready(callback) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", callback, { once: true });
    } else callback();
  }

  ready(function () {
    var shell = document.querySelector("[data-wf2-shell]");
    if (!shell) return;

    var privacy = shell.querySelector("[data-wf2-privacy]");
    var menu = shell.querySelector("[data-wf2-menu]");
    var sidebar = shell.querySelector("[data-wf2-sidebar]");

    function numericAttribute(node, name, minimum, maximum) {
      var raw = (node.getAttribute(name) || "").replace(",", ".");
      var value = Number(raw);
      if (!Number.isFinite(value)) return minimum;
      return Math.min(maximum, Math.max(minimum, value));
    }

    // The CSP intentionally blocks inline style attributes. Apply the small
    // set of server-published numeric presentation values through the local,
    // same-origin script instead.
    shell.querySelectorAll("[data-wf2-height-px]").forEach(function (node) {
      node.style.height = numericAttribute(node, "data-wf2-height-px", 2, 225) + "px";
    });
    shell.querySelectorAll("[data-wf2-width-percent]").forEach(function (node) {
      node.style.width = numericAttribute(node, "data-wf2-width-percent", 0, 100) + "%";
    });
    shell.querySelectorAll("[data-wf2-flex-percent]").forEach(function (node) {
      node.style.flexBasis = numericAttribute(node, "data-wf2-flex-percent", 0, 100) + "%";
    });
    shell.querySelectorAll("[data-wf-block]").forEach(function (node) {
      node.style.setProperty("--wf-block", numericAttribute(node, "data-wf-block", 2, 100));
    });

    function setPrivacy(hidden, persist) {
      document.body.classList.toggle("wf2-values-hidden", hidden);
      if (privacy) privacy.checked = hidden;
      if (persist) {
        try { window.sessionStorage.setItem("wealthfolio:hide-values", hidden ? "1" : "0"); } catch (_error) {}
      }
    }

    var initialHidden = false;
    try { initialHidden = window.sessionStorage.getItem("wealthfolio:hide-values") === "1"; } catch (_error) {}
    setPrivacy(Boolean(privacy && privacy.checked) || initialHidden, false);
    if (privacy) privacy.addEventListener("change", function () { setPrivacy(privacy.checked, true); });

    // Keep the current read-only scope when moving between shell areas. The
    // destination may replace a value (for example, its active tab), but
    // period/date/search filters should not disappear on a drill-down.
    var preservedQuery = new Set([
      "periodo", "data", "busca", "dimensao", "ordenar", "direcao",
      "grupo", "conta", "categoria", "natureza", "status", "stage", "pagina",
    ]);
    var currentUrl = new URL(window.location.href);
    shell.querySelectorAll("[data-wf2-preserve-query]").forEach(function (link) {
      var raw = link.getAttribute("href");
      if (!raw || raw === "#") return;
      var target;
      try { target = new URL(raw, window.location.href); } catch (_error) { return; }
      if (target.origin !== window.location.origin) return;
      preservedQuery.forEach(function (key) {
        if (!target.searchParams.has(key) && currentUrl.searchParams.has(key)) {
          target.searchParams.set(key, currentUrl.searchParams.get(key));
        }
      });
      link.setAttribute("href", target.pathname + target.search + target.hash);
    });

    function closeMenu(restoreFocus) {
      if (!sidebar || !menu) return;
      sidebar.classList.remove("is-open");
      menu.setAttribute("aria-expanded", "false");
      if (restoreFocus) menu.focus();
    }
    if (menu && sidebar) {
      menu.addEventListener("click", function () {
        var open = sidebar.classList.toggle("is-open");
        menu.setAttribute("aria-expanded", open ? "true" : "false");
      });
      sidebar.querySelectorAll("a").forEach(function (link) { link.addEventListener("click", function () { closeMenu(false); }); });
      document.addEventListener("keydown", function (event) { if (event.key === "Escape") closeMenu(true); });
    }

    // Account groups are server-expanded through relative GET links. Once a
    // group is open, this presentation-only toggle lets the user collapse or
    // reopen its already-rendered children without mutating financial state.
    shell.querySelectorAll("[data-wf2-account-toggle]").forEach(function (toggle) {
      var controls = toggle.getAttribute("aria-controls");
      var children = controls ? document.getElementById(controls) : null;
      if (!children) return;

      toggle.addEventListener("click", function (event) {
        event.preventDefault();
        var expanded = toggle.getAttribute("aria-expanded") === "true";
        toggle.setAttribute("aria-expanded", expanded ? "false" : "true");
        toggle.parentElement.classList.toggle("is-expanded", !expanded);
        children.hidden = expanded;
      });
    });
  });
}());
