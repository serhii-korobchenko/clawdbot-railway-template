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

  function renderEvidenceActivityChart() {
    const canvas = document.getElementById("evidence-activity-chart");
    const dataNode = document.getElementById("activity-chart-data");

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

    const truncate = (value, max = 44) => {
      const text = String(value || "");
      return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
    };

    const isMobile = window.matchMedia("(max-width: 760px)").matches;
    const wrapper = canvas.closest(".activity-chart-wrap");
    if (wrapper) {
      const rowHeight = isMobile ? 44 : 54;
      const minHeight = isMobile ? 280 : 320;
      wrapper.style.height = `${Math.max(minHeight, points.length * rowHeight)}px`;
    }

    new Chart(canvas, {
      type: "bar",
      data: {
        labels: points.map((point) => point.title),
        datasets: [
          { label: "🟢 Indicator", data: points.map((point) => point.indicator_count), stack: "activity" },
          { label: "🔴 Counterindicator", data: points.map((point) => point.counterindicator_count), stack: "activity" },
          { label: "⚪ Neutral", data: points.map((point) => point.neutral_count), stack: "activity" },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: {
            beginAtZero: true,
            stacked: true,
            ticks: {
              precision: 0,
            },
            title: {
              display: true,
              text: "Кількість official evidence",
            },
          },
          y: {
            stacked: true,
            ticks: {
              autoSkip: false,
              callback(value, index) {
                if (isMobile) {
                  return String(index + 1);
                }
                return truncate(this.getLabelForValue(value));
              },
            },
          },
        },
        plugins: {
          legend: { display: true, position: "bottom" },
          tooltip: {
            mode: "index",
            intersect: false,
            callbacks: {
              title: (items) => {
                const point = points[items[0]?.dataIndex] || {};
                return point.title || "";
              },
              label: (context) => `${context.dataset.label}: ${context.raw}`,
              afterBody: (items) => {
                const point = points[items[0]?.dataIndex] || {};
                return [
                  `Усього: ${point.evidence_count ?? 0}`,
                  `Останнє evidence: ${point.latest_evidence_at || "—"}`,
                ];
              },
            },
          },
        },
      },
    });
  }


  function renderCandidateActivityChart() {
    const canvas = document.getElementById("candidate-activity-chart");
    const dataNode = document.getElementById("candidate-activity-chart-data");
    if (!canvas || !dataNode || typeof Chart === "undefined") return;
    let points = [];
    try { points = JSON.parse(dataNode.dataset.points || "[]"); } catch { return; }
    if (!Array.isArray(points) || points.length === 0) return;

    const eventIds = [...new Set(points.map((p) => p.event_id))];
    const labels = [...new Set(points.map((p) => p.refresh_id))];
    const byKey = new Map(points.map((p) => [`${p.event_id}:${p.refresh_id}`, p]));
    const datasets = eventIds.map((eventId) => {
      const sample = points.find((p) => p.event_id === eventId) || {};
      return {
        label: sample.event_title || eventId,
        data: labels.map((refreshId) => byKey.get(`${eventId}:${refreshId}`)?.candidate_count ?? null),
        tension: 0,
        fill: false,
      };
    });

    new Chart(canvas, {
      type: "line",
      data: { labels: labels.map((id) => `#${id}`), datasets },
      options: {
        responsive: true, maintainAspectRatio: false,
        scales: { y: { beginAtZero: true, ticks: { precision: 0 }, title: { display: true, text: "Кількість candidates" } } },
        plugins: {
          legend: { display: eventIds.length > 1, position: "bottom" },
          tooltip: { callbacks: {
            afterLabel: (context) => {
              const eventId = eventIds[context.datasetIndex];
              const refreshId = labels[context.dataIndex];
              const point = byKey.get(`${eventId}:${refreshId}`) || {};
              return [`Refresh #${refreshId}`, `Час: ${point.started_at || "—"}`];
            }
          }}
        }
      }
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

  const renderCharts = () => {
    renderProbabilityChart();
    renderEvidenceActivityChart();
    renderCandidateActivityChart();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", renderCharts);
  } else {
    renderCharts();
  }
})();
