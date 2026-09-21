(function () {
  "use strict";

  // The shared query helper intentionally covers shell-wide filters. Keep the
  // activity-specific instrument filter when navigating within this page.
  var current = new URL(window.location.href);
  document.querySelectorAll("[data-wf2-preserve-query]").forEach(function (link) {
    var raw = link.getAttribute("href");
    if (!raw || raw === "#") return;
    var target;
    try { target = new URL(raw, window.location.href); } catch (_error) { return; }
    if (target.origin !== current.origin) return;
    if (!target.searchParams.has("instrumento") && current.searchParams.has("instrumento")) {
      target.searchParams.set("instrumento", current.searchParams.get("instrumento"));
    }
    link.setAttribute("href", target.pathname + target.search + target.hash);
  });

  document.querySelectorAll("[data-wf2-activity-view]").forEach(function (button) {
    button.addEventListener("click", function () {
      var ledger = document.querySelector(".wf2a-ledger");
      if (!ledger) return;
      var mode = button.getAttribute("data-wf2-activity-view") || "list";
      ledger.classList.toggle("is-grid", mode === "grid");
      document.querySelectorAll("[data-wf2-activity-view]").forEach(function (item) {
        var active = item === button;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-pressed", active ? "true" : "false");
      });
    });
  });
}());
