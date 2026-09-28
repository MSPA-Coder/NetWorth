/* Wealthfolio-inspired Insights chart renderer.
 * It only draws a chart when the view-model publishes a numeric series.
 * Missing/invalid data intentionally remains the server-rendered unavailable state.
 */
(function () {
  "use strict";

  function readSeries(node) {
    try {
      var value = JSON.parse(node.getAttribute("data-wf-performance-series") || "null");
      if (!Array.isArray(value)) return [];
      return value.map(function (point) {
        if (typeof point === "number") return point;
        if (Array.isArray(point)) return Number(point[1]);
        if (point && typeof point === "object") return Number(point.value ?? point.valor ?? point.return ?? point.retorno);
        return NaN;
      }).filter(Number.isFinite);
    } catch (_error) {
      return [];
    }
  }

  function drawChart(frame) {
    var svg = frame.querySelector("[data-wf-performance-chart]");
    var series = readSeries(frame);
    if (!svg || series.length < 2) {
      if (frame) {
        frame.setAttribute("data-chart-state", "unavailable");
        var message = frame.querySelector("[data-wf-performance-state]");
        if (!message) {
          message = document.createElement("span");
          message.setAttribute("data-wf-performance-state", "");
          message.setAttribute("role", "status");
          message.className = "wf-insights-v2__chart-caption";
          frame.appendChild(message);
        }
        message.textContent = "Série insuficiente para desenhar o gráfico; a origem publicou menos de dois pontos.";
      }
      return;
    }
    var width = 760;
    var height = 280;
    var pad = 22;
    var min = Math.min.apply(Math, series);
    var max = Math.max.apply(Math, series);
    var span = max - min || 1;
    var points = series.map(function (value, index) {
      var x = pad + (index / (series.length - 1)) * (width - pad * 2);
      var y = height - pad - ((value - min) / span) * (height - pad * 2);
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    svg.innerHTML = '<line x1="22" y1="258" x2="738" y2="258" class="wf-insights-v2__svg-axis" />' +
      '<polyline points="' + points + '" class="wf-insights-v2__svg-line" />';
    frame.setAttribute("data-chart-state", "ready");
  }

  document.querySelectorAll("[data-wf-performance-series]").forEach(drawChart);

  document.querySelectorAll("[data-wf-income-percent]").forEach(function (bar) {
    var value = Number(bar.getAttribute("data-wf-income-percent"));
    if (Number.isFinite(value)) {
      bar.style.setProperty("--wf-income-width", Math.max(0, Math.min(100, value)) + "%");
    }
  });

  document.querySelectorAll("[data-insights-widget-percent]").forEach(function (bar) {
    var raw = (bar.getAttribute("data-insights-widget-percent") || "").replace(",", ".");
    var value = Number(raw);
    if (Number.isFinite(value)) {
      bar.style.setProperty("--wf-widget-percent", Math.max(0, Math.min(100, value)) + "%");
    }
  });

  // Wealthfolio applies the account picker as soon as a value is chosen.
  // Keep the native form as a no-JS fallback while preserving the existing
  // GET contract (tab, period and group remain unchanged).
  document.querySelectorAll("[data-wf2-insights-filter]").forEach(function (form) {
    var account = form.querySelector("[data-wf2-insights-account]");
    if (!account) return;
    account.addEventListener("change", function () {
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    });
  });

  // Local presentation preferences only: widget visibility and order never
  // alter financial data or server-side filters. Storage is optional so the
  // dashboard remains fully usable when the browser disables localStorage.
  document.querySelectorAll("[data-insights-widget-area]").forEach(function (area) {
    var grid = area.querySelector("[data-insights-widget-grid]");
    var editButton = area.querySelector("[data-widget-edit]");
    var resetButton = area.querySelector("[data-widget-reset]");
    var manager = area.querySelector("[data-widget-manager]");
    var status = area.querySelector("[data-widget-status]");
    if (!grid || !editButton || !manager || !resetButton) return;

    var storageKey = "networth.insights.widgets.v1";
    var widgets = Array.prototype.slice.call(grid.querySelectorAll("[data-insights-widget]"));
    var allowed = widgets.map(function (widget) { return widget.getAttribute("data-insights-widget"); });
    var state = { order: allowed.slice(), hidden: [] };
    var draggedKey = null;

    function readState() {
      try {
        var saved = JSON.parse(window.localStorage.getItem(storageKey) || "null");
        if (!saved || typeof saved !== "object") return;
        var savedOrder = Array.isArray(saved.order) ? saved.order : [];
        state.order = savedOrder.filter(function (key, index) {
          return allowed.indexOf(key) !== -1 && savedOrder.indexOf(key) === index;
        });
        allowed.forEach(function (key) { if (state.order.indexOf(key) === -1) state.order.push(key); });
        state.hidden = Array.isArray(saved.hidden) ? saved.hidden.filter(function (key) { return allowed.indexOf(key) !== -1; }) : [];
      } catch (_error) { /* defaults are the safe fallback */ }
    }

    function saveState() {
      try { window.localStorage.setItem(storageKey, JSON.stringify(state)); } catch (_error) { /* preference remains active until reload */ }
    }

    function announce(message) {
      if (!status) return;
      status.textContent = message;
      status.hidden = false;
    }

    function syncManager() {
      manager.replaceChildren();
      widgets.forEach(function (widget) {
        var key = widget.getAttribute("data-insights-widget");
        var heading = widget.querySelector("h2");
        var label = document.createElement("label");
        var checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = state.hidden.indexOf(key) === -1;
        checkbox.setAttribute("data-widget-visible", key);
        var text = document.createElement("span");
        text.textContent = heading ? heading.textContent : key;
        label.appendChild(checkbox);
        label.appendChild(text);
        manager.appendChild(label);
      });
    }

    function applyState() {
      state.order.forEach(function (key) {
        var widget = widgets.find(function (candidate) { return candidate.getAttribute("data-insights-widget") === key; });
        if (widget) grid.appendChild(widget);
      });
      widgets.forEach(function (widget) {
        var key = widget.getAttribute("data-insights-widget");
        widget.hidden = state.hidden.indexOf(key) !== -1;
      });
      syncManager();
    }

    function setEditing(editing) {
      editButton.setAttribute("aria-expanded", editing ? "true" : "false");
      editButton.textContent = editing ? "Concluir personalização" : "Personalizar";
      manager.hidden = !editing;
      resetButton.hidden = !editing;
      grid.classList.toggle("is-customizing", editing);
      area.querySelectorAll("[data-widget-controls]").forEach(function (controls) { controls.hidden = !editing; });
      widgets.forEach(function (widget) {
        widget.draggable = editing;
        if (editing) widget.setAttribute("aria-grabbed", "false");
        else widget.removeAttribute("aria-grabbed");
      });
      if (editing) announce("Altere a ordem ou marque os widgets que deseja exibir.");
    }

    readState();
    applyState();
    setEditing(false);

    editButton.addEventListener("click", function () {
      var editing = editButton.getAttribute("aria-expanded") !== "true";
      setEditing(editing);
      if (!editing) announce("Preferências de widgets aplicadas.");
    });

    resetButton.addEventListener("click", function () {
      state = { order: allowed.slice(), hidden: [] };
      applyState();
      saveState();
      announce("Widgets restaurados ao padrão.");
    });

    manager.addEventListener("change", function (event) {
      var checkbox = event.target.closest("[data-widget-visible]");
      if (!checkbox) return;
      var key = checkbox.getAttribute("data-widget-visible");
      state.hidden = checkbox.checked ? state.hidden.filter(function (item) { return item !== key; }) : state.hidden.concat([key]);
      applyState();
      saveState();
    });

    grid.addEventListener("click", function (event) {
      var hideButton = event.target.closest("[data-widget-hide]");
      if (hideButton) {
        var hiddenWidget = hideButton.closest("[data-insights-widget]");
        if (!hiddenWidget) return;
        var hiddenKey = hiddenWidget.getAttribute("data-insights-widget");
        state.hidden = state.hidden.indexOf(hiddenKey) === -1 ? state.hidden.concat([hiddenKey]) : state.hidden;
        applyState();
        saveState();
        announce("Widget ocultado. Reative-o na lista de visibilidade.");
        return;
      }
      var moveButton = event.target.closest("[data-widget-move]");
      if (!moveButton) return;
      var moving = moveButton.closest("[data-insights-widget]");
      if (!moving) return;
      var current = state.order.indexOf(moving.getAttribute("data-insights-widget"));
      var next = moveButton.getAttribute("data-widget-move") === "up" ? current - 1 : current + 1;
      if (current < 0 || next < 0 || next >= state.order.length) return;
      var swap = state.order[current];
      state.order[current] = state.order[next];
      state.order[next] = swap;
      applyState();
      saveState();
      var movedHeading = moving.querySelector("h2");
      announce((movedHeading ? movedHeading.textContent : "Widget") + " reordenado.");
    });

    // Wealthfolio's layout editor is pointer-oriented. Keep the existing
    // keyboard move buttons, and add native drag-and-drop as a dependency-free
    // equivalent that persists the same order preference.
    grid.addEventListener("dragstart", function (event) {
      if (editButton.getAttribute("aria-expanded") !== "true") return;
      if (event.target.closest("button, a, input")) {
        event.preventDefault();
        return;
      }
      var widget = event.target.closest("[data-insights-widget]");
      if (!widget) return;
      draggedKey = widget.getAttribute("data-insights-widget");
      widget.setAttribute("aria-grabbed", "true");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData("text/plain", draggedKey);
      }
    });

    grid.addEventListener("dragover", function (event) {
      if (!draggedKey || editButton.getAttribute("aria-expanded") !== "true") return;
      var target = event.target.closest("[data-insights-widget]");
      if (!target || target.getAttribute("data-insights-widget") === draggedKey) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    });

    grid.addEventListener("drop", function (event) {
      if (!draggedKey || editButton.getAttribute("aria-expanded") !== "true") return;
      var target = event.target.closest("[data-insights-widget]");
      if (!target) return;
      var targetKey = target.getAttribute("data-insights-widget");
      if (!targetKey || targetKey === draggedKey) return;
      event.preventDefault();
      var from = state.order.indexOf(draggedKey);
      var to = state.order.indexOf(targetKey);
      if (from < 0 || to < 0) return;
      state.order.splice(from, 1);
      state.order.splice(to, 0, draggedKey);
      applyState();
      saveState();
      announce("Widget reordenado.");
    });

    grid.addEventListener("dragend", function () {
      widgets.forEach(function (widget) { widget.setAttribute("aria-grabbed", "false"); });
      draggedKey = null;
    });
  });
}());
