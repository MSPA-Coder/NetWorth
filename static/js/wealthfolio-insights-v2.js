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
      if (frame) frame.setAttribute("data-chart-state", "unavailable");
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
}());
