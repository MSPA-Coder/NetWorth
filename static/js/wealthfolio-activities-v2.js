(function () {
  "use strict";

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
