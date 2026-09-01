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

  document.body.addEventListener("htmx:afterRequest", (event) => {
    const trigger = event.detail && event.detail.elt;
    if (!trigger || !trigger.classList || !trigger.classList.contains("status-tab")) {
      return;
    }
    document.querySelectorAll(".status-tab").forEach((tab) => {
      tab.classList.remove("is-active");
    });
    trigger.classList.add("is-active");
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderProbabilityChart);
  } else {
    renderProbabilityChart();
  }
})();
