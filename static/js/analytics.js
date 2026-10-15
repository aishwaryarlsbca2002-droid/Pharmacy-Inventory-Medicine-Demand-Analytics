/* ===================================================================
   analytics.js  -  the charts on the analytics page.

   How it works:
     1. Ask the server for the numbers:  GET /api/analytics?days=365
     2. The server replies with JSON. No HTML, no page reload.
     3. Chart.js draws them.

   Changing the period repeats steps 1 to 3, so only the data travels
   over the network, not a whole new page. That is the AJAX-style
   request the project asks for.
   =================================================================== */

document.addEventListener("DOMContentLoaded", function () {
  var picker = document.getElementById("range-picker");
  if (!picker) return;                   // not on the analytics page

  /* colours taken from the stylesheet, so the charts match the app */
  var css = getComputedStyle(document.documentElement);
  var COLOR = {
    green: css.getPropertyValue("--green").trim() || "#0C6B4A",
    amber: css.getPropertyValue("--amber").trim() || "#9A6407",
    red: css.getPropertyValue("--red").trim() || "#A42A1E",
    ink: css.getPropertyValue("--ink").trim() || "#0E1F18",
    muted: css.getPropertyValue("--muted").trim() || "#5D6E65",
    line: css.getPropertyValue("--line").trim() || "#DBE3DD",
  };

  /* a spread of tints for the category chart */
  var PALETTE = ["#0C6B4A", "#118A5F", "#3FA37B", "#9A6407", "#C08A2E",
                 "#A42A1E", "#5D6E65", "#8CA79A", "#B9C9C0"];

  Chart.defaults.font.family =
    "Archivo, system-ui, -apple-system, 'Segoe UI', sans-serif";
  Chart.defaults.font.size = 12;
  Chart.defaults.color = COLOR.muted;

  var charts = {};                       // keeps one Chart per canvas

  /* ---------------- helpers ---------------- */

  function money(n) {
    return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  }

  function axes(extra) {
    var base = {
      x: { grid: { display: false }, ticks: { color: COLOR.muted } },
      y: { beginAtZero: true, grid: { color: COLOR.line },
           border: { display: false }, ticks: { color: COLOR.muted } },
    };
    return Object.assign(base, extra || {});
  }

  /* Chart.js throws if you draw over a canvas that already has a chart,
     so an existing one is destroyed before redrawing. */
  function draw(id, config) {
    if (charts[id]) charts[id].destroy();
    charts[id] = new Chart(document.getElementById(id), config);
  }

  /* ---------------- the four charts ---------------- */

  function trendChart(d) {
    draw("chart-trend", {
      data: {
        labels: d.labels,
        datasets: [
          { type: "bar", label: "Units", data: d.units,
            backgroundColor: COLOR.green, borderRadius: 3,
            yAxisID: "y", order: 2 },
          { type: "line", label: "Revenue", data: d.revenue,
            borderColor: COLOR.amber, backgroundColor: COLOR.amber,
            borderWidth: 2, tension: 0.3, pointRadius: 3,
            yAxisID: "y1", order: 1 },
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
          x: { grid: { display: false } },
          y: { beginAtZero: true, position: "left",
               grid: { color: COLOR.line }, border: { display: false },
               title: { display: true, text: "Units", color: COLOR.muted } },
          y1: { beginAtZero: true, position: "right",
                grid: { display: false }, border: { display: false },
                title: { display: true, text: "Revenue", color: COLOR.muted } },
        },
      },
    });
  }

  function topChart(d) {
    draw("chart-top", {
      type: "bar",
      data: {
        labels: d.labels,
        datasets: [{ label: "Units", data: d.units,
                     backgroundColor: COLOR.green, borderRadius: 3 }],
      },
      options: {
        indexAxis: "y",                  // horizontal, so names stay readable
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              afterLabel: function (ctx) {
                return "Revenue: " + money(d.revenue[ctx.dataIndex]);
              },
            },
          },
        },
        scales: axes(),
      },
    });
  }

  function categoryChart(d) {
    draw("chart-category", {
      type: "doughnut",
      data: {
        labels: d.labels,
        datasets: [{ data: d.revenue, backgroundColor: PALETTE,
                     borderColor: "#fff", borderWidth: 2 }],
      },
      options: {
        maintainAspectRatio: false,
        cutout: "58%",
        plugins: {
          legend: { position: "right",
                    labels: { boxWidth: 11, usePointStyle: true,
                              padding: 10, font: { size: 11.5 } } },
          tooltip: {
            callbacks: {
              label: function (ctx) {
                var total = ctx.dataset.data.reduce(function (a, b) {
                  return a + b;
                }, 0);
                var share = (100 * ctx.parsed / total).toFixed(1);
                return ctx.label + ": " + money(ctx.parsed) + " (" + share + "%)";
              },
            },
          },
        },
      },
    });
  }

  function weekdayChart(d) {
    /* Sunday is coloured differently because it is the outlier the
       staffing and ordering decisions hang on. */
    var colors = d.labels.map(function (name) {
      return name === "Sunday" ? COLOR.amber : COLOR.green;
    });

    draw("chart-weekday", {
      type: "bar",
      data: {
        labels: d.labels.map(function (n) { return n.slice(0, 3); }),
        datasets: [{ label: "Average units", data: d.averages,
                     backgroundColor: colors, borderRadius: 3 }],
      },
      options: {
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: axes(),
      },
    });
  }

  function gapTable(rows) {
    var body = document.getElementById("gap-rows");
    body.innerHTML = "";

    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="4" class="empty">No sales in this period.</td></tr>';
      return;
    }

    rows.forEach(function (r) {
      var tr = document.createElement("tr");
      tr.innerHTML =
        "<td>" + r.name + "</td>" +
        '<td class="num">' + r.unit_rank + "</td>" +
        '<td class="num">' + r.revenue_rank + "</td>" +
        '<td class="num">' + money(r.revenue) + "</td>";
      body.appendChild(tr);
    });
  }

  /* ---------------- load and redraw ---------------- */

  function render(payload) {
    document.getElementById("kpi-revenue").textContent =
      money(payload.headline.revenue);
    document.getElementById("kpi-units").textContent =
      money(payload.headline.units);
    document.getElementById("kpi-transactions").textContent =
      money(payload.headline.transactions);
    document.getElementById("kpi-perday").textContent =
      money(payload.headline.revenue_per_day);
    document.getElementById("kpi-stockvalue").textContent =
      money(payload.stock.stock_value);

    document.getElementById("range-label").textContent =
      payload.range.start + " to " + payload.range.end;

    trendChart(payload.monthly);
    topChart(payload.top);
    categoryChart(payload.categories);
    weekdayChart(payload.weekday);
    gapTable(payload.gaps);
  }

  function load(days) {
    fetch("/api/analytics?days=" + days)
      .then(function (response) {
        if (!response.ok) throw new Error("Request failed: " + response.status);
        return response.json();
      })
      .then(render)
      .catch(function (error) {
        document.getElementById("range-label").textContent =
          "Could not load the figures. " + error.message;
      });
  }

  picker.addEventListener("click", function (event) {
    var button = event.target.closest(".range-btn");
    if (!button) return;
    picker.querySelectorAll(".range-btn").forEach(function (b) {
      b.classList.toggle("active", b === button);
    });
    load(button.dataset.days);
  });

  load(365);                             // the view the page opens on
});