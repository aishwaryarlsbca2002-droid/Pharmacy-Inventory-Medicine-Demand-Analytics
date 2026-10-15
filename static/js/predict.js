/* ===================================================================
   predict.js  -  draws recent actual sales and the forecast on one
   chart, so the prediction can be judged against what came before.

   The data is embedded in the page by Flask inside a <script type=
   "application/json"> tag rather than fetched, because the forecast was
   already computed when the page was built. Reading it from a JSON tag
   avoids writing Python values straight into JavaScript code, which is
   how template injection bugs happen.
   =================================================================== */

document.addEventListener("DOMContentLoaded", function () {
  var holder = document.getElementById("forecast-data");
  if (!holder) return;                     // no forecast on screen yet

  var data = JSON.parse(holder.textContent);

  var css = getComputedStyle(document.documentElement);
  var GREEN = css.getPropertyValue("--green").trim() || "#0C6B4A";
  var AMBER = css.getPropertyValue("--amber").trim() || "#9A6407";
  var LINE = css.getPropertyValue("--line").trim() || "#DBE3DD";
  var MUTED = css.getPropertyValue("--muted").trim() || "#5D6E65";

  Chart.defaults.font.family =
    "Archivo, system-ui, -apple-system, 'Segoe UI', sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.color = MUTED;

  /* Both series share one x axis. The actual series is padded with nulls
     where the forecast runs, and vice versa, so the two lines meet
     exactly at today rather than overlapping. */
  var actualCount = data.actual.length;
  var forecastCount = data.forecast.length;

  var actualValues = data.actual.concat(new Array(forecastCount).fill(null));
  var forecastValues = new Array(actualCount - 1).fill(null)
      .concat([data.actual[actualCount - 1]])   // join the two lines up
      .concat(data.forecast);

  new Chart(document.getElementById("chart-forecast"), {
    type: "line",
    data: {
      labels: data.labels,
      datasets: [
        { label: "Actual sales", data: actualValues,
          borderColor: GREEN, backgroundColor: GREEN,
          borderWidth: 2, tension: 0.25, pointRadius: 2 },
        { label: "Forecast", data: forecastValues,
          borderColor: AMBER, backgroundColor: AMBER,
          borderWidth: 2, borderDash: [6, 4], tension: 0.25, pointRadius: 3 },
      ],
    },
    options: {
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "top", align: "end",
                  labels: { boxWidth: 12, usePointStyle: true } },
      },
      scales: {
        x: { grid: { display: false },
             ticks: { maxRotation: 0, autoSkipPadding: 14 } },
        y: { beginAtZero: true, grid: { color: LINE },
             border: { display: false },
             title: { display: true, text: "Units", color: MUTED } },
      },
    },
  });
});