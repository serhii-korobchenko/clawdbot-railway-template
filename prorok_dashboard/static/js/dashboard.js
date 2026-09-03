(() => {
  function renderProbabilityChart() {
    const canvas = document.getElementById("probability-chart");
    const dataNode = document.getElementById("chart-data");

    if (!canvas || !dataNode || typeof Chart === "undefined") {
      return;
    }

    let points = [];
    try {
      points = JSON.parse(dataNode.dataset.points || "[]");
    } catch {
      return;
    }

    if (!Array.isArray(points) || points.length === 0) {
      return;
    }

    new Chart(canvas, {
      type: "line",
      data: {
        labels: points.map((point) => point.assessed_at),
        datasets: [{
          label: "Probability",
          data: points.map((point) => point.probability),
          tension: 0,
          fill: false,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          y: {
            min: 0,
            max: 100,
            ticks: {
              callback: (value) => `${value}%`,
            },
          },
        },
        plugins: {
          legend: {
            display: false,
          },
          tooltip: {
            callbacks: {
              afterLabel: (context) => {
                const point = points[context.dataIndex] || {};
                const lines = [];
                if (point.confidence) {
                  lines.push(`Confidence: ${point.confidence}`);
                }
                if (point.delta !== null && point.delta !== undefined) {
                  const prefix = point.delta > 0 ? "+" : "";
                  lines.push(`Change: ${prefix}${point.delta} pp`);
                }
                return lines;
              },
            },
          },
        },
      },
    });
  }

  function syncFilterUrl({ push = false } = {}) {
    const statusField = document.getElementById("status-field");
    const searchField = document.getElementById("event-search");
    if (!statusField || !searchField) {
      return;
    }

    const url = new URL(window.location.href);
    const status = statusField.value.trim();
    const query = searchField.value.trim();

    if (status) {
      url.searchParams.set("status", status);
    } else {
      url.searchParams.delete("status");
    }

    if (query) {
      url.searchParams.set("q", query);
    } else {
      url.searchParams.delete("q");
    }

    const state = { status, q: query };
    if (push) {
      window.history.pushState(state, "", url);
    } else {
      window.history.replaceState(state, "", url);
    }
  }

  document.body.addEventListener("htmx:afterRequest", (event) => {
    if (event.detail && event.detail.successful === false) {
      return;
    }

    const trigger = event.detail && event.detail.elt;
    if (!trigger) {
      return;
    }

    if (trigger.classList && trigger.classList.contains("status-tab")) {
      const statusField = document.getElementById("status-field");
      const status = trigger.dataset.status || "";
      if (statusField) {
        statusField.value = status;
      }

      document.querySelectorAll(".status-tab").forEach((tab) => {
        tab.classList.remove("is-active");
      });
      trigger.classList.add("is-active");
      syncFilterUrl({ push: true });
      return;
    }

    if (trigger.id === "event-search") {
      syncFilterUrl({ push: false });
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderProbabilityChart);
  } else {
    renderProbabilityChart();
  }
})();
