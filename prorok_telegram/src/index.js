import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { promisify } from "node:util";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const API_BASE = process.env.PROROK_API_BASE_URL || "http://127.0.0.1:18880";
const API_TOKEN = process.env.PROROK_API_TOKEN || "";
const CALLBACK_NAMESPACE = "prorok";
const EVENT_TOKEN_LENGTH = 12;
const GLOBAL_EVIDENCE_PAGE_SIZE = 5;
const EVENT_LIST_PAGE_SIZE = 8;
const DECISION_CLI = process.env.PROROK_DECISION_CLI || "/app/prorok/prorok_refresh_decision_cli.py";
const EVIDENCE_ASSESSMENT_CLI = process.env.PROROK_EVIDENCE_ASSESSMENT_CLI || "/app/prorok/prorok_evidence_assessment_cli.py";
const DELETE_CLI = process.env.PROROK_DELETE_CLI || "/app/prorok/prorok_delete_cli.py";
const STATUS_CLI = process.env.PROROK_STATUS_CLI || "/app/prorok/prorok_event_status_cli.py";
const REFRESH_ALL_CLI = process.env.PROROK_REFRESH_ALL_CLI || "/app/prorok/prorok_refresh_all_dry_run_quiet.py";
const PROROK_DB_PATH = process.env.PROROK_DB_PATH || "/data/workspace/prorok/prorok.sqlite3";
const PYTHON_BIN = process.env.PROROK_PYTHON_BIN || "python3";
const execFileAsync = promisify(execFile);
const CUSTOM_PROBABILITY_VALUES = [
  0, 5,
  10, 15, 20,
  25, 30, 35,
  40, 45, 50,
  55, 60, 65, 70, 75,
  80, 85, 90,
  95, 100,
];

function callbackValue(payload) {
  return `${CALLBACK_NAMESPACE}:${payload}`;
}

function eventToken(eventId) {
  return createHash("sha256").update(String(eventId)).digest("hex").slice(0, EVENT_TOKEN_LENGTH);
}

function textBlock(text) {
  return { type: "text", text };
}

function buttonsBlock(buttons) {
  return { type: "buttons", buttons };
}

function button(label, payload, style, reusable = true) {
  return {
    label,
    value: callbackValue(payload),
    ...(style ? { style } : {}),
    reusable,
  };
}

function mainPresentation() {
  return {
    title: "PROROK",
    tone: "info",
    blocks: [
      textBlock("Оберіть розділ керування прогнозами."),
      buttonsBlock([
        button("📊 Прогнози", "events", "primary"),
        button("🧾 Evidence", "evidence:all:0"),
        button("📥 Candidates", "candidates", "primary"),
      ]),
      buttonsBlock([
        button("🔄 Останнє оновлення", "refresh"),
      ]),
      buttonsBlock([
        button("🗂 Архів", "archive"),
        button("⚙️ Керування", "manage"),
      ]),
    ],
  };
}

async function apiGet(path) {
  if (!API_TOKEN) {
    throw new Error("PROROK_API_TOKEN is not configured");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${API_TOKEN}` },
  });
  if (!response.ok) {
    throw new Error(`PROROK API HTTP ${response.status}`);
  }
  return await response.json();
}

async function allEvents() {
  const data = await apiGet("/api/v1/events");
  return Array.isArray(data.items) ? data.items : [];
}

async function activeEvents() {
  const data = await apiGet("/api/v1/events?status=active");
  return Array.isArray(data.items) ? data.items : [];
}

async function archivedEvents() {
  const data = await apiGet("/api/v1/events?status=archived");
  return Array.isArray(data.items) ? data.items : [];
}

function eventStatusIcon(status) {
  if (status === "active") return "🟢";
  if (status === "paused") return "⏸";
  if (status === "resolved") return "✅";
  if (status === "archived") return "🗂";
  return "•";
}

async function resolveEventId(token, { activeOnly = false } = {}) {
  const items = activeOnly ? await activeEvents() : await allEvents();
  const matches = items.filter((event) => eventToken(event.event_id) === token);
  if (matches.length === 1) return String(matches[0].event_id);
  if (matches.length > 1) throw new Error("Event callback token collision");
  throw new Error(activeOnly ? "Event is no longer active or callback is stale" : "Event callback is stale");
}

function currentAssessmentLine(event) {
  const a = event.current_assessment;
  if (!a) return "Поточна оцінка: немає";
  const confidence = a.confidence ? ` · ${a.confidence}` : "";
  return `Поточна оцінка: ${a.probability_percent}%${confidence}`;
}

function shortText(value, max = 700) {
  const text = String(value || "").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

function evidenceDirectionLabel(direction) {
  if (direction === "indicator") return "🟢 indicator";
  if (direction === "counterindicator") return "🔴 counterindicator";
  return "⚪ neutral";
}

function statusTransitionLabel(fromStatus, toStatus) {
  if (fromStatus === "active" && toStatus === "paused") return "⏸ Поставити на паузу";
  if (fromStatus === "paused" && toStatus === "active") return "▶️ Відновити";
  if ((fromStatus === "active" || fromStatus === "paused") && toStatus === "archived") {
    return "🗂 Архівувати";
  }
  if (fromStatus === "archived" && toStatus === "active") return "♻️ Відновити як active";
  return `${fromStatus} → ${toStatus}`;
}

function allowedStatusTransitions(status) {
  if (status === "active") return [["paused", "primary"], ["archived", "danger"]];
  if (status === "paused") return [["active", "success"], ["archived", "danger"]];
  if (status === "archived") return [["active", "success"]];
  return [];
}

function telegramActorSnapshot(ctx) {
  const raw =
    ctx?.callback?.senderId ??
    ctx?.callback?.sender_id ??
    ctx?.callback?.from?.id ??
    ctx?.auth?.senderId ??
    ctx?.auth?.sender_id ??
    ctx?.senderId ??
    ctx?.sender_id ??
    ctx?.from?.id ??
    null;
  if (raw === null || raw === undefined || String(raw).trim() === "") {
    return "telegram:authorized";
  }
  return `telegram:${shortText(String(raw), 120)}`;
}

async function eventsPresentation() {
  const items = await activeEvents();
  const blocks = [textBlock(`Активні прогнози: ${items.length}`)];

  if (!items.length) {
    blocks.push(textBlock("Активних прогнозів немає."));
  } else {
    for (const event of items.slice(0, 12)) {
      const token = eventToken(event.event_id);
      blocks.push(
        buttonsBlock([
          button(
            `${event.current_assessment?.probability_percent ?? "—"}% · ${event.title}`.slice(0, 80),
            `event:${token}`,
          ),
        ]),
      );
    }
  }

  blocks.push(buttonsBlock([button("◀️ Назад", "home")]));
  return { title: "📊 Прогнози", tone: "neutral", blocks };
}

async function eventPresentation(eventId) {
  const data = await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}`);
  const event = data.event;
  const current = data.current_assessment;
  const probability = current ? `${current.probability_percent}%` : "—";
  const confidence = current?.confidence || "—";
  const horizon = event.forecast_horizon || "—";
  const token = eventToken(event.event_id);
  const blocks = [
    textBlock(event.question),
    textBlock(
      [
        `Ймовірність: ${probability}`,
        `Впевненість: ${confidence}`,
        `Горизонт: ${horizon}`,
        `Статус: ${event.status}`,
        `Assessment: ${data.assessments?.length ?? 0}`,
        `Evidence: ${data.evidence?.length ?? 0}`,
      ].join("\n"),
    ),
  ];

  const detailButtons = [];
  if (event.status === "active") {
    detailButtons.push(button("🎯 Рекомендація", `recommendation:${token}`, "primary"));
  }
  detailButtons.push(
    button("🧾 Evidence", `event-evidence:${token}`),
    button("📈 Історія", `event-history:${token}`),
  );
  blocks.push(buttonsBlock(detailButtons));

  for (const [toStatus, style] of allowedStatusTransitions(event.status)) {
    blocks.push(
      buttonsBlock([
        button(
          statusTransitionLabel(event.status, toStatus),
          `status:${token}:${event.status}:${toStatus}`,
          style,
        ),
      ]),
    );
  }

  blocks.push(
    buttonsBlock([
      button("🗑 Видалити подію", `delete-event:${token}`, "danger"),
    ]),
  );

  const back =
    event.status === "archived"
      ? { label: "◀️ До архіву", payload: "archive" }
      : event.status === "active"
        ? { label: "◀️ До прогнозів", payload: "events" }
        : { label: "◀️ До керування", payload: "manage-events:0" };

  blocks.push(
    buttonsBlock([
      button(back.label, back.payload),
      button("🏠 Головне меню", "home"),
    ]),
  );

  return { title: event.title, tone: "neutral", blocks };
}

function recommendationStatusLabel(status) {
  if (status === "actionable") return "потребує рішення";
  if (status === "decided") return "рішення вже зафіксовано";
  if (status === "stale") return "застаріла";
  if (status === "no_change") return "зміна не рекомендована";
  return String(status || "невідомо");
}

async function recommendationPresentation(eventId) {
  const data = await apiGet(
    `/api/v1/events/${encodeURIComponent(eventId)}/latest-recommendation`,
  );
  const rec = data.recommendation;
  const token = eventToken(eventId);
  const blocks = [];

  if (!rec) {
    blocks.push(textBlock("Для цієї події валідної рекомендації поки немає."));
  } else {
    const decision = rec.decision;
    blocks.push(
      textBlock(
        [
          `Поточна оцінка: ${rec.current_probability ?? "—"}%`,
          `Рекомендація: ${rec.recommended_probability}%`,
          `Baseline: ${rec.baseline_probability}% · assessment #${rec.baseline_assessment_id}`,
          `Confidence рекомендації: ${rec.recommendation_confidence || "—"}`,
          rec.probability_delta !== null && rec.probability_delta !== undefined
            ? `Зміна: ${rec.probability_delta > 0 ? "+" : ""}${rec.probability_delta} п.п.`
            : null,
          rec.net_evidence_impact
            ? `Вплив evidence: ${rec.net_evidence_impact} · ${rec.net_evidence_direction || "—"}`
            : null,
          rec.baseline_incorporation
            ? `Враховано в baseline: ${rec.baseline_incorporation}`
            : null,
          rec.category_transition !== null && rec.category_transition !== undefined
            ? `Зміна категорії: ${rec.category_transition ? "так" : "ні"}`
            : null,
          `Статус: ${recommendationStatusLabel(rec.status)}`,
          rec.recommendation_reason
            ? `Причина: ${shortText(rec.recommendation_reason, 700)}`
            : null,
          rec.delta_justification
            ? `Чому саме ця оцінка: ${shortText(rec.delta_justification, 700)}`
            : null,
          decision
            ? `Рішення: ${decision.decision_type} → ${decision.selected_probability}% · ${decision.decided_at}`
            : null,
        ]
          .filter(Boolean)
          .join("\n"),
      ),
    );

    const resultId = rec.refresh_event_result_id;
    try {
      const candidateData = await apiGet(
        `/api/v1/evidence/candidates?event_id=${encodeURIComponent(eventId)}&validation_state=accepted&sort=newest`,
      );
      const sourceCandidates = (Array.isArray(candidateData.items) ? candidateData.items : []).filter(
        (item) =>
          Number(item.refresh_event_result_id) === Number(resultId) &&
          item.url,
      );
      if (sourceCandidates.length) {
        const sourceLines = sourceCandidates.flatMap((item, index) => {
          const sourceLabel = shortText(item.source || item.title || `Джерело #${index + 1}`, 120);
          return [`${index + 1}. ${sourceLabel}`, item.url];
        });
        blocks.push(
          textBlock(
            ["Джерела Candidate Evidence:", "", ...sourceLines].join("\n"),
          ),
        );
      }
    } catch {
      // Recommendation remains usable even if candidate source lookup is unavailable.
    }

    if (rec.actionable) {
      blocks.push(
        buttonsBlock([
          button(
            `✅ Прийняти ${rec.recommended_probability}%`,
            `decision-accept:${token}:${resultId}`,
            "success",
          ),
          button(
            "✏️ Власна оцінка",
            `decision-custom:${token}:${resultId}`,
            "primary",
          ),
          button(
            `➖ Залишити ${rec.current_probability ?? rec.baseline_probability}%`,
            `decision-keep:${token}:${resultId}`,
          ),
        ]),
      );
    } else if (rec.can_keep_current) {
      const resultId = rec.refresh_event_result_id;
      blocks.push(
        buttonsBlock([
          button(
            `✅ Прийняти evidence · залишити ${rec.current_probability ?? rec.baseline_probability}%`,
            `decision-keep:${token}:${resultId}`,
            "success",
          ),
        ]),
      );
    } else if (rec.is_stale) {
      blocks.push(
        textBlock(
          "Ця рекомендація більше не може бути застосована: поточний official assessment вже відрізняється від baseline.",
        ),
      );
    }
  }

  blocks.push(
    buttonsBlock([
      button("◀️ До події", `event-any:${token}`),
      button("📊 До прогнозів", "events"),
    ]),
  );
  return { title: "🎯 Рекомендація", tone: "neutral", blocks };
}

async function loadDecisionRecommendation(eventId, expectedResultId, decisionType = null) {
  const data = await apiGet(
    `/api/v1/events/${encodeURIComponent(eventId)}/latest-recommendation`,
  );
  const rec = data.recommendation;

  if (!rec) {
    return { rec: null, problem: "Рекомендація більше недоступна." };
  }
  if (String(rec.refresh_event_result_id) !== String(expectedResultId)) {
    return {
      rec,
      problem: [
        "Ця кнопка належить до попередньої рекомендації.",
        `Було: refresh_event_result_id #${expectedResultId}`,
        `Зараз: refresh_event_result_id #${rec.refresh_event_result_id}`,
      ].join("\n"),
    };
  }
  const canApply =
    rec.actionable || (decisionType === "keep_current" && rec.can_keep_current);
  if (!canApply) {
    return {
      rec,
      problem: `Рекомендація зараз має статус: ${recommendationStatusLabel(rec.status)}.`,
    };
  }
  return { rec, problem: null };
}

async function runDecisionCli(refreshEventResultId, decisionType, probability = null) {
  const args = [
    DECISION_CLI,
    "--db",
    PROROK_DB_PATH,
    "apply",
    String(refreshEventResultId),
    "--decision",
    decisionType,
    "--source",
    "telegram",
  ];
  if (probability !== null && probability !== undefined) {
    args.push("--probability", String(probability));
  }

  return await execFileAsync(PYTHON_BIN, args, {
    timeout: 20000,
    maxBuffer: 64 * 1024,
    env: process.env,
  });
}

function decisionErrorText(error) {
  const stderr = String(error?.stderr || "").trim();
  const stdout = String(error?.stdout || "").trim();
  const message = stderr || stdout || String(error?.message || error);
  return shortText(message, 900);
}

async function appliedDecisionPresentation(eventId, expectedResultId, decisionType, probability = null) {
  const token = eventToken(eventId);
  const { rec, problem } = await loadDecisionRecommendation(
    eventId,
    expectedResultId,
    decisionType,
  );

  if (problem) {
    return {
      title: "PROROK · Рішення не застосовано",
      tone: "neutral",
      blocks: [
        textBlock(`${problem}\n\nЖодних змін не виконано.`),
        buttonsBlock([
          button("🎯 Актуальна рекомендація", `recommendation:${token}`, "primary"),
          button("◀️ До події", `event-any:${token}`),
        ]),
      ],
    };
  }

  try {
    await runDecisionCli(expectedResultId, decisionType, probability);
  } catch (error) {
    return {
      title: "PROROK · Рішення не застосовано",
      tone: "neutral",
      blocks: [
        textBlock(
          [
            "Deterministic decision CLI відхилив операцію.",
            decisionErrorText(error),
            "",
            "Якщо стан змінився паралельно, відкрийте актуальну рекомендацію ще раз.",
          ].join("\n"),
        ),
        buttonsBlock([
          button("🎯 Актуальна рекомендація", `recommendation:${token}`, "primary"),
          button("◀️ До події", `event-any:${token}`),
        ]),
      ],
    };
  }

  const after = await apiGet(
    `/api/v1/events/${encodeURIComponent(eventId)}/latest-recommendation`,
  );
  const afterRec = after.recommendation;
  const decision = afterRec?.decision || null;

  return {
    title: "✅ PROROK · Рішення збережено",
    tone: "neutral",
    blocks: [
      textBlock(
        [
          `refresh_event_result_id: #${expectedResultId}`,
          decision ? `Рішення: ${decision.decision_type}` : `Рішення: ${decisionType}`,
          `Обрана ймовірність: ${decision?.selected_probability ?? probability ?? rec.recommended_probability}%`,
          `Official forecast: ${afterRec?.current_probability ?? "—"}%`,
          decision?.assessment_id
            ? `Новий assessment: #${decision.assessment_id}`
            : "Новий assessment: не створювався",
          decision?.decided_at ? `Зафіксовано: ${decision.decided_at}` : null,
        ]
          .filter(Boolean)
          .join("\n"),
      ),
      buttonsBlock([
        button("🎯 Переглянути рекомендацію", `recommendation:${token}`),
        button("◀️ До події", `event-any:${token}`, "primary"),
      ]),
    ],
  };
}

async function customProbabilityPresentation(eventId, expectedResultId) {
  const token = eventToken(eventId);
  const { rec, problem } = await loadDecisionRecommendation(eventId, expectedResultId);

  if (problem) {
    return {
      title: "PROROK · Власна оцінка",
      tone: "neutral",
      blocks: [
        textBlock(`${problem}\n\nЖодних змін не виконано.`),
        buttonsBlock([button("🎯 Актуальна рекомендація", `recommendation:${token}`, "primary")]),
      ],
    };
  }

  const blocks = [
    textBlock(
      [
        `Поточна оцінка: ${rec.current_probability}%`,
        `Рекомендація: ${rec.recommended_probability}%`,
        "",
        "Оберіть власну оцінку. Після вибору буде окремий екран підтвердження.",
      ].join("\n"),
    ),
  ];

  for (let i = 0; i < CUSTOM_PROBABILITY_VALUES.length; i += 3) {
    const row = CUSTOM_PROBABILITY_VALUES.slice(i, i + 3).map((value) => {
      const prefix =
        value === rec.recommended_probability ? "⭐ " :
        value === rec.current_probability ? "• " :
        "";
      return button(
        `${prefix}${value}%`,
        `decision-custom-value:${token}:${expectedResultId}:${value}`,
      );
    });
    blocks.push(buttonsBlock(row));
  }

  blocks.push(
    buttonsBlock([
      button("◀️ До рекомендації", `recommendation:${token}`),
      button("🏠 Головне меню", "home"),
    ]),
  );

  return { title: "✏️ PROROK · Власна оцінка", tone: "neutral", blocks };
}

async function customProbabilityConfirmPresentation(eventId, expectedResultId, probability) {
  const token = eventToken(eventId);
  const { rec, problem } = await loadDecisionRecommendation(eventId, expectedResultId);

  if (problem) {
    return {
      title: "PROROK · Підтвердження",
      tone: "neutral",
      blocks: [
        textBlock(`${problem}\n\nЖодних змін не виконано.`),
        buttonsBlock([button("🎯 Актуальна рекомендація", `recommendation:${token}`, "primary")]),
      ],
    };
  }

  if (!CUSTOM_PROBABILITY_VALUES.includes(probability)) {
    throw new Error("Invalid PROROK custom probability value");
  }

  return {
    title: "PROROK · Підтвердження",
    tone: "neutral",
    blocks: [
      textBlock(
        [
          `Поточний official forecast: ${rec.current_probability}%`,
          `Рекомендація системи: ${rec.recommended_probability}%`,
          `Ваша оцінка: ${probability}%`,
          "",
          `Після підтвердження буде створено новий official assessment ${probability}%.`,
        ].join("\n"),
      ),
      buttonsBlock([
        button(
          `✅ Підтвердити ${probability}%`,
          `decision-custom-apply:${token}:${expectedResultId}:${probability}`,
          "success",
        ),
        button("◀️ Змінити", `decision-custom:${token}:${expectedResultId}`),
      ]),
      buttonsBlock([button("❌ Скасувати", `recommendation:${token}`)]),
    ],
  };
}

function parseDecisionRoute(payload, prefix) {
  const raw = payload.slice(prefix.length);
  const [token, resultId, ...extra] = raw.split(":");
  if (!token || !resultId || extra.length || !/^\d+$/.test(resultId)) {
    throw new Error("Invalid PROROK decision callback payload");
  }
  return { token, resultId };
}

function parseCustomDecisionRoute(payload, prefix) {
  const raw = payload.slice(prefix.length);
  const [token, resultId, rawProbability, ...extra] = raw.split(":");
  if (
    !token ||
    !resultId ||
    !rawProbability ||
    extra.length ||
    !/^\d+$/.test(resultId) ||
    !/^\d+$/.test(rawProbability)
  ) {
    throw new Error("Invalid PROROK custom decision callback payload");
  }
  const probability = Number.parseInt(rawProbability, 10);
  if (!CUSTOM_PROBABILITY_VALUES.includes(probability)) {
    throw new Error("Unsupported PROROK custom probability value");
  }
  return { token, resultId, probability };
}

function parseStatusRoute(payload, prefix) {
  const raw = payload.slice(prefix.length);
  const [token, fromStatus, toStatus, ...extra] = raw.split(":");
  const validStatuses = ["active", "paused", "resolved", "archived"];
  if (
    !token ||
    !validStatuses.includes(fromStatus) ||
    !validStatuses.includes(toStatus) ||
    extra.length
  ) {
    throw new Error("Invalid PROROK status callback payload");
  }
  return { token, fromStatus, toStatus };
}

function parseEvidenceDeleteRoute(payload, prefix) {
  const raw = payload.slice(prefix.length);
  const [token, rawEvidenceId, ...extra] = raw.split(":");
  if (!token || !rawEvidenceId || extra.length || !/^\d+$/.test(rawEvidenceId)) {
    throw new Error("Invalid PROROK evidence delete callback payload");
  }
  return { token, evidenceId: Number.parseInt(rawEvidenceId, 10) };
}

function parseGlobalEvidenceDetailRoute(payload) {
  const raw = payload.slice("evidence-detail:".length);
  const [token, rawEvidenceId, filter, rawPage, ...extra] = raw.split(":");
  if (
    !token ||
    !rawEvidenceId ||
    !/^\d+$/.test(rawEvidenceId) ||
    !["all", "indicator", "counterindicator"].includes(filter) ||
    !rawPage ||
    !/^\d+$/.test(rawPage) ||
    extra.length
  ) {
    throw new Error("Invalid PROROK evidence detail callback payload");
  }
  return {
    token,
    evidenceId: Number.parseInt(rawEvidenceId, 10),
    filter,
    page: Number.parseInt(rawPage, 10),
  };
}

function parseCliKeyValues(stdout) {
  const result = {};
  for (const rawLine of String(stdout || "").split(/\r?\n/)) {
    const line = rawLine.trim();
    const index = line.indexOf(":");
    if (index <= 0) continue;
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim();
    if (key) result[key] = value;
  }
  return result;
}

async function runStatusCli(eventId, fromStatus, toStatus, actorSnapshot) {
  return await execFileAsync(
    PYTHON_BIN,
    [
      STATUS_CLI,
      "--db",
      PROROK_DB_PATH,
      "set-status",
      String(eventId),
      "--from-status",
      fromStatus,
      "--to-status",
      toStatus,
      "--source",
      "telegram",
      "--actor",
      actorSnapshot,
    ],
    {
      timeout: 20000,
      maxBuffer: 64 * 1024,
      env: process.env,
    },
  );
}

async function runDeleteCli(command, targetId, actorSnapshot) {
  return await execFileAsync(
    PYTHON_BIN,
    [
      DELETE_CLI,
      "--db",
      PROROK_DB_PATH,
      command,
      String(targetId),
      "--source",
      "telegram",
      "--actor",
      actorSnapshot,
    ],
    {
      timeout: 20000,
      maxBuffer: 64 * 1024,
      env: process.env,
    },
  );
}

async function loadEvidenceForDeletion(eventId, evidenceId) {
  const data = await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}`);
  const event = data.event;
  const evidence = Array.isArray(data.evidence) ? data.evidence : [];
  const item = evidence.find((candidate) => Number(candidate.evidence_id) === Number(evidenceId)) || null;
  return { data, event, item };
}

async function statusConfirmationPresentation(eventId, expectedStatus, targetStatus) {
  const data = await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}`);
  const event = data.event;
  const token = eventToken(event.event_id);

  if (event.status !== expectedStatus) {
    return {
      title: "PROROK · Статус уже змінився",
      tone: "neutral",
      blocks: [
        textBlock(
          [
            `Подія: ${event.title}`,
            `Очікувався статус: ${expectedStatus}`,
            `Поточний статус: ${event.status}`,
            "",
            "Стара кнопка більше не застосовується. Жодних змін не виконано.",
          ].join("\n"),
        ),
        buttonsBlock([button("◀️ До події", `event-any:${token}`, "primary")]),
      ],
    };
  }

  const transitions = allowedStatusTransitions(event.status).map(([status]) => status);
  if (!transitions.includes(targetStatus)) {
    throw new Error(`Unsupported PROROK status transition: ${event.status} -> ${targetStatus}`);
  }

  return {
    title: "PROROK · Підтвердження статусу",
    tone: "neutral",
    blocks: [
      textBlock(
        [
          `Подія: ${event.title}`,
          `event_id: ${event.event_id}`,
          `Поточний статус: ${event.status}`,
          `Новий статус: ${targetStatus}`,
          "",
          targetStatus === "archived"
            ? "Подія буде прибрана з active/paused списків і з'явиться в архіві."
            : targetStatus === "paused"
              ? "Подія залишиться в системі, але не буде active."
              : "Подія знову стане active.",
          "Після підтвердження deterministic CLI змінить production DB та запише audit.",
        ].join("\n"),
      ),
      buttonsBlock([
        button(
          `✅ Підтвердити: ${expectedStatus} → ${targetStatus}`,
          `status-apply:${token}:${expectedStatus}:${targetStatus}`,
          "success",
        ),
      ]),
      buttonsBlock([button("❌ Скасувати", `event-any:${token}`)]),
    ],
  };
}

async function appliedStatusPresentation(eventId, expectedStatus, targetStatus, ctx) {
  const token = eventToken(eventId);
  const actor = telegramActorSnapshot(ctx);

  try {
    const { stdout } = await runStatusCli(
      eventId,
      expectedStatus,
      targetStatus,
      actor,
    );
    const fields = parseCliKeyValues(stdout);
    const after = await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}`);
    const event = after.event;

    return {
      title: "✅ PROROK · Статус змінено",
      tone: "neutral",
      blocks: [
        textBlock(
          [
            `Подія: ${event.title}`,
            `Було: ${expectedStatus}`,
            `Стало: ${event.status}`,
            `status_change_id: ${fields.status_change_id || "—"}`,
            `Час: ${fields.changed_at || event.updated_at || "—"}`,
            `Actor: ${fields.actor_snapshot || actor}`,
            fields.idempotent_replay === "true"
              ? "Повторне натискання: без нової зміни."
              : null,
          ].filter(Boolean).join("\n"),
        ),
        buttonsBlock([
          button("◀️ До події", `event-any:${token}`, "primary"),
          button("⚙️ До керування", "manage"),
        ]),
      ],
    };
  } catch (error) {
    return {
      title: "PROROK · Статус не змінено",
      tone: "neutral",
      blocks: [
        textBlock(
          [
            "Deterministic status CLI відхилив операцію.",
            decisionErrorText(error),
            "",
            "Жодної часткової зміни не повинно бути committed: CLI працює в одній SQLite transaction.",
          ].join("\n"),
        ),
        buttonsBlock([
          button("◀️ До події", `event-any:${token}`),
          button("⚙️ До керування", "manage"),
        ]),
      ],
    };
  }
}

async function eventDeleteConfirmationPresentation(eventId) {
  const data = await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}`);
  const event = data.event;
  const token = eventToken(event.event_id);
  const assessmentCount = Array.isArray(data.assessments) ? data.assessments.length : 0;
  const evidenceCount = Array.isArray(data.evidence) ? data.evidence.length : 0;

  return {
    title: "⚠️ PROROK · Видалення події",
    tone: "neutral",
    blocks: [
      textBlock(
        [
          `Подія: ${event.title}`,
          `event_id: ${event.event_id}`,
          `Assessment буде видалено: ${assessmentCount}`,
          `Evidence буде видалено: ${evidenceCount}`,
          "",
          "Це hard delete. Refresh/decision audit history зберігається за правилами schema v6, а сам факт видалення буде записано у deletion_audit.",
          "Натискання кнопки нижче одразу змінить production DB.",
        ].join("\n"),
      ),
      buttonsBlock([
        button("✅ Підтвердити видалення", `delete-event-apply:${token}`, "danger"),
        button("❌ Скасувати", `event-any:${token}`),
      ]),
    ],
  };
}

async function evidenceDeleteConfirmationPresentation(eventId, evidenceId) {
  const { event, item } = await loadEvidenceForDeletion(eventId, evidenceId);
  const token = eventToken(event.event_id);

  if (!item) {
    return {
      title: "PROROK · Evidence недоступний",
      tone: "neutral",
      blocks: [
        textBlock(`Evidence #${evidenceId} більше не існує в цій події. Жодних змін не виконано.`),
        buttonsBlock([
          button("◀️ До події", `event-any:${token}`),
          button("🏠 Головне меню", "home"),
        ]),
      ],
    };
  }

  const source = item.source || {};
  const sourceLabel = source.title || source.domain || source.url || "невідоме джерело";

  return {
    title: "⚠️ PROROK · Видалення evidence",
    tone: "neutral",
    blocks: [
      textBlock(
        [
          `Evidence #${item.evidence_id}`,
          `Подія: ${event.title}`,
          `Напрям: ${item.direction}${item.strength ? ` · ${item.strength}` : ""}`,
          `Summary: ${shortText(item.summary, 500)}`,
          `Джерело: ${shortText(sourceLabel, 180)}`,
          "",
          "Буде видалено тільки цей evidence. Source збережеться. Факт видалення буде записано у deletion_audit.",
          "Натискання кнопки нижче одразу змінить production DB.",
        ].join("\n"),
      ),
      buttonsBlock([
        button(
          `✅ Видалити evidence #${item.evidence_id}`,
          `delete-evidence-apply:${token}:${item.evidence_id}`,
          "danger",
        ),
        button("❌ Скасувати", `event-any:${token}`),
      ]),
    ],
  };
}

async function appliedEventDeletePresentation(eventId, ctx) {
  const data = await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}`);
  const event = data.event;
  const assessmentCount = Array.isArray(data.assessments) ? data.assessments.length : 0;
  const evidenceCount = Array.isArray(data.evidence) ? data.evidence.length : 0;
  const actor = telegramActorSnapshot(ctx);

  try {
    const { stdout } = await runDeleteCli("delete-event", eventId, actor);
    const fields = parseCliKeyValues(stdout);
    return {
      title: "✅ PROROK · Подію видалено",
      tone: "neutral",
      blocks: [
        textBlock(
          [
            `Подія: ${event.title}`,
            `event_id: ${eventId}`,
            `deletion_id: ${fields.deletion_id || "—"}`,
            `Assessment видалено: ${fields.assessment_count_deleted || assessmentCount}`,
            `Evidence видалено: ${fields.evidence_count_deleted || evidenceCount}`,
            `Sources збережено: ${fields.source_rows_preserved || "—"}`,
            `Actor: ${fields.actor_snapshot || actor}`,
          ].join("\n"),
        ),
        buttonsBlock([
          button("📊 До прогнозів", "events", "primary"),
          button("🏠 Головне меню", "home"),
        ]),
      ],
    };
  } catch (error) {
    return {
      title: "PROROK · Подію не видалено",
      tone: "neutral",
      blocks: [
        textBlock(
          [
            "Deterministic delete CLI відхилив операцію.",
            decisionErrorText(error),
            "",
            "Жоден частковий delete не повинен бути committed: CLI працює в одній SQLite transaction.",
          ].join("\n"),
        ),
        buttonsBlock([
          button("◀️ До події", `event-any:${eventToken(eventId)}`),
          button("🏠 Головне меню", "home"),
        ]),
      ],
    };
  }
}

async function appliedEvidenceDeletePresentation(eventId, evidenceId, ctx) {
  const { event, item } = await loadEvidenceForDeletion(eventId, evidenceId);
  const token = eventToken(eventId);

  if (!item) {
    return {
      title: "PROROK · Evidence не видалено",
      tone: "neutral",
      blocks: [
        textBlock(`Evidence #${evidenceId} більше не існує. Жодних змін не виконано.`),
        buttonsBlock([
          button("🧾 Evidence події", `event-evidence:${token}`),
          button("◀️ До події", `event-any:${token}`),
        ]),
      ],
    };
  }

  const actor = telegramActorSnapshot(ctx);

  try {
    const { stdout } = await runDeleteCli("delete-evidence", evidenceId, actor);
    const fields = parseCliKeyValues(stdout);
    return {
      title: "✅ PROROK · Evidence видалено",
      tone: "neutral",
      blocks: [
        textBlock(
          [
            `Evidence #${evidenceId}`,
            `Подія: ${event.title}`,
            `deletion_id: ${fields.deletion_id || "—"}`,
            `Source збережено: #${fields.source_id_preserved || item.source?.source_id || "—"}`,
            `Actor: ${fields.actor_snapshot || actor}`,
          ].join("\n"),
        ),
        buttonsBlock([
          button("🧾 Evidence події", `event-evidence:${token}`, "primary"),
          button("◀️ До події", `event-any:${token}`),
        ]),
      ],
    };
  } catch (error) {
    return {
      title: "PROROK · Evidence не видалено",
      tone: "neutral",
      blocks: [
        textBlock(
          [
            "Deterministic delete CLI відхилив операцію.",
            decisionErrorText(error),
            "",
            "Жоден частковий delete не повинен бути committed: CLI працює в одній SQLite transaction.",
          ].join("\n"),
        ),
        buttonsBlock([
          button("🧾 Evidence події", `event-evidence:${token}`),
          button("◀️ До події", `event-any:${token}`),
        ]),
      ],
    };
  }
}

function parseEvidenceAssessmentRoute(payload, prefix, withProbability = false) {
  const raw = payload.slice(prefix.length);
  const parts = raw.split(":");
  if ((withProbability && parts.length !== 4) || (!withProbability && parts.length !== 3)) {
    throw new Error("Invalid PROROK evidence assessment callback payload");
  }
  const [token, evidenceRaw, baselineRaw, probabilityRaw] = parts;
  if (!token || !/^\d+$/.test(evidenceRaw) || !/^\d+$/.test(baselineRaw)) {
    throw new Error("Invalid PROROK evidence assessment callback payload");
  }
  const result = { token, evidenceId: Number(evidenceRaw), baselineAssessmentId: Number(baselineRaw) };
  if (withProbability) {
    const probability = Number(probabilityRaw);
    if (!CUSTOM_PROBABILITY_VALUES.includes(probability)) throw new Error("Invalid PROROK evidence assessment probability");
    result.probability = probability;
  }
  return result;
}

async function evidenceAssessmentChoicePresentation(eventId, evidenceId, baselineAssessmentId) {
  const data = await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}`);
  const item = (data.evidence || []).find((x) => Number(x.evidence_id) === Number(evidenceId));
  const current = data.current_assessment;
  const token = eventToken(eventId);
  if (!item || !current || Number(current.assessment_id) !== Number(baselineAssessmentId)) {
    return { title: "PROROK · Переоцінка недоступна", tone: "neutral", blocks: [
      textBlock("Evidence або baseline змінилися. Відкрийте evidence ще раз."),
      buttonsBlock([button("🧾 Evidence події", `event-evidence:${token}`)])
    ]};
  }
  const blocks=[textBlock([`Evidence #${evidenceId}`, shortText(item.summary,500), `Поточний прогноз: ${current.probability_percent}% · Assessment #${current.assessment_id}`, "Оберіть нову оцінку:"].join("\n"))];
  for(let i=0;i<CUSTOM_PROBABILITY_VALUES.length;i+=3){
    blocks.push(buttonsBlock(CUSTOM_PROBABILITY_VALUES.slice(i,i+3).map(v=>button(`${v===current.probability_percent?"● ":""}${v}%`,`evidence-assess-value:${token}:${evidenceId}:${baselineAssessmentId}:${v}`))));
  }
  blocks.push(buttonsBlock([button("❌ Скасувати",`event-evidence:${token}`)]));
  return {title:"📊 Переоцінка за evidence",tone:"neutral",blocks};
}

async function evidenceAssessmentConfirmPresentation(eventId,evidenceId,baselineAssessmentId,probability){
  const data=await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}`);
  const item=(data.evidence||[]).find((x)=>Number(x.evidence_id)===Number(evidenceId));
  const current=data.current_assessment; const token=eventToken(eventId);
  if(!item||!current||Number(current.assessment_id)!==Number(baselineAssessmentId)) return evidenceAssessmentChoicePresentation(eventId,evidenceId,baselineAssessmentId);
  const delta=probability-Number(current.probability_percent);
  return {title:"Підтвердити переоцінку",tone:"neutral",blocks:[
    textBlock([`Evidence #${evidenceId}`,shortText(item.summary,500),`Поточний прогноз: ${current.probability_percent}%`,`Новий прогноз: ${probability}%`,`Зміна: ${delta>0?"+":""}${delta} п.п.`,delta===0?"Evidence буде зафіксовано як оцінене без зміни прогнозу.":"Evidence буде зафіксовано як підстава зміни прогнозу."].join("\n")),
    buttonsBlock([button("✅ Застосувати",`evidence-assess-apply:${token}:${evidenceId}:${baselineAssessmentId}:${probability}`,"success"),button("◀️ Змінити",`evidence-assess:${token}:${evidenceId}:${baselineAssessmentId}`)]),
    buttonsBlock([button("❌ Скасувати",`event-evidence:${token}`)])
  ]};
}

async function appliedEvidenceAssessmentPresentation(eventId,evidenceId,baselineAssessmentId,probability,ctx){
  const token=eventToken(eventId);
  const args=[EVIDENCE_ASSESSMENT_CLI,"--db",PROROK_DB_PATH,eventId,String(evidenceId),"--baseline-assessment-id",String(baselineAssessmentId),"--probability",String(probability),"--source","telegram"];
  const actor=telegramActorSnapshot(ctx); if(actor) args.push("--actor",String(actor));
  try{ await execFileAsync(PYTHON_BIN,args,{timeout:20000,maxBuffer:64*1024,env:process.env}); }
  catch(error){ return {title:"PROROK · Переоцінку не застосовано",tone:"neutral",blocks:[textBlock([decisionErrorText(error),"","Жодних змін не виконано."].join("\n")),buttonsBlock([button("🧾 Evidence події",`event-evidence:${token}`)])]}; }
  const after=await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}`);
  return {title:"✅ Прогноз оновлено",tone:"neutral",blocks:[textBlock([`Evidence #${evidenceId}`,`Новий прогноз: ${after.current_assessment?.probability_percent ?? probability}%`,`Assessment #${after.current_assessment?.assessment_id ?? "—"}`].join("\n")),buttonsBlock([button("🧾 Evidence події",`event-evidence:${token}`),button("📈 Історія",`event-history:${token}`)])]};
}

async function eventEvidencePresentation(eventId) {
  const data = await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}`);
  const event = data.event;
  const evidence = Array.isArray(data.evidence) ? data.evidence : [];
  const token = eventToken(event.event_id);
  const blocks = [textBlock(`Evidence: ${evidence.length}`)];

  if (!evidence.length) {
    blocks.push(textBlock("Для цієї події evidence поки немає."));
  } else {
    for (const item of evidence.slice(0, 10)) {
      const source = item.source || {};
      const sourceLabel = source.title || source.domain || source.url || "невідоме джерело";
      blocks.push(
        textBlock(
          [
            `#${item.evidence_id} · ${item.direction}${item.strength ? ` · ${item.strength}` : ""}`,
            shortText(item.summary, 550),
            `Джерело: ${shortText(sourceLabel, 180)}`,
            `Дата: ${item.created_at || "—"}`,
            `Relevance: ${item.relevance ?? "—"} · Credibility: ${item.credibility ?? "—"}`,
          ].join("\n"),
        ),
      );
      const baselineId = data.current_assessment?.assessment_id;
      blocks.push(
        buttonsBlock([
          ...(baselineId ? [button(`📊 Переоцінити за #${item.evidence_id}`, `evidence-assess:${token}:${item.evidence_id}:${baselineId}`, "primary")] : []),
          button(`🗑 Видалити evidence #${item.evidence_id}`, `delete-evidence:${token}:${item.evidence_id}`, "danger"),
        ]),
      );
    }
    if (evidence.length > 10) blocks.push(textBlock(`Показано 10 з ${evidence.length} evidence.`));
  }

  blocks.push(
    buttonsBlock([
      button("◀️ До події", `event-any:${token}`),
      button("📊 До прогнозів", "events"),
    ]),
  );
  return { title: `🧾 ${event.title}`, tone: "neutral", blocks };
}

async function eventHistoryPresentation(eventId) {
  const data = await apiGet(`/api/v1/events/${encodeURIComponent(eventId)}`);
  const event = data.event;
  const assessments = Array.isArray(data.assessments) ? data.assessments : [];
  const token = eventToken(event.event_id);
  const blocks = [textBlock(`Assessment history: ${assessments.length}`)];

  if (!assessments.length) {
    blocks.push(textBlock("Історії оцінок поки немає."));
  } else {
    for (const item of assessments.slice(0, 12)) {
      const delta = item.delta_from_previous;
      const deltaText = delta === null || delta === undefined ? "—" : `${delta > 0 ? "+" : ""}${delta} п.п.`;
      blocks.push(
        textBlock(
          [
            `${item.assessed_at || "—"} · ${item.probability_percent}%`,
            `Δ: ${deltaText} · confidence: ${item.confidence || "—"}`,
            item.rationale ? `Причина: ${shortText(item.rationale, 420)}` : null,
          ].filter(Boolean).join("\n"),
        ),
      );
    }
    if (assessments.length > 12) blocks.push(textBlock(`Показано 12 з ${assessments.length} assessment.`));
  }

  blocks.push(
    buttonsBlock([
      button("◀️ До події", `event-any:${token}`),
      button("📊 До прогнозів", "events"),
    ]),
  );
  return { title: `📈 ${event.title}`, tone: "neutral", blocks };
}

async function collectGlobalEvidence() {
  const events = await allEvents();
  const details = await Promise.all(
    events.map(async (event) => {
      try {
        return await apiGet(`/api/v1/events/${encodeURIComponent(event.event_id)}`);
      } catch {
        return null;
      }
    }),
  );

  const rows = [];
  for (const detail of details.filter(Boolean)) {
    const event = detail.event;
    for (const item of Array.isArray(detail.evidence) ? detail.evidence : []) {
      rows.push({ event, item });
    }
  }

  rows.sort((a, b) => String(b.item.created_at || "").localeCompare(String(a.item.created_at || "")));
  return rows;
}

async function globalEvidenceDetailPresentation(eventId, evidenceId, filter = "all", page = 0) {
  const { event, item } = await loadEvidenceForDeletion(eventId, evidenceId);
  const token = eventToken(event.event_id);
  const safeFilter = ["all", "indicator", "counterindicator"].includes(filter) ? filter : "all";
  const safePage = Math.max(Number(page) || 0, 0);

  if (!item) {
    return {
      title: "🧾 Evidence недоступний",
      tone: "neutral",
      blocks: [
        textBlock(`Evidence #${evidenceId} більше не існує. Жодних змін не виконано.`),
        buttonsBlock([
          button("◀️ До evidence", `evidence:${safeFilter}:${safePage}`),
          button("◀️ До керування", "manage"),
        ]),
        buttonsBlock([button("🏠 Головне меню", "home")]),
      ],
    };
  }

  const source = item.source || {};
  const sourceLabel = source.title || source.domain || source.url || "невідоме джерело";

  return {
    title: `🧾 Evidence #${item.evidence_id}`,
    tone: "neutral",
    blocks: [
      textBlock(
        [
          `Подія: ${event.title}`,
          `Напрям: ${evidenceDirectionLabel(item.direction)}${item.strength ? ` · ${item.strength}` : ""}`,
          `Summary: ${shortText(item.summary, 1100)}`,
          `Relevance: ${item.relevance ?? "—"}`,
          `Credibility: ${item.credibility ?? "—"}`,
          `Створено: ${item.created_at || "—"}`,
          "",
          `Джерело: ${shortText(sourceLabel, 300)}`,
          source.url ? `URL: ${source.url}` : null,
          source.published_at ? `Опубліковано: ${source.published_at}` : null,
          source.domain ? `Домен: ${source.domain}` : null,
        ].filter(Boolean).join("\n"),
      ),
      buttonsBlock([
        ...(event.current_assessment?.assessment_id ? [button("📊 Переоцінити прогноз", `evidence-assess:${token}:${item.evidence_id}:${event.current_assessment.assessment_id}`, "primary")] : []),
        button("↗️ Відкрити подію", `event-any:${token}`),
        button(
          `🗑 Видалити #${item.evidence_id}`,
          `delete-evidence:${token}:${item.evidence_id}`,
          "danger",
        ),
      ]),
      buttonsBlock([
        button("◀️ До evidence", `evidence:${safeFilter}:${safePage}`),
        button("◀️ До керування", "manage"),
      ]),
      buttonsBlock([button("🏠 Головне меню", "home")]),
    ],
  };
}

async function globalEvidencePresentation(filter = "all", page = 0) {
  const rows = await collectGlobalEvidence();
  const filtered = filter === "all" ? rows : rows.filter(({ item }) => item.direction === filter);
  const maxPage = Math.max(0, Math.ceil(filtered.length / GLOBAL_EVIDENCE_PAGE_SIZE) - 1);
  const safePage = Math.min(Math.max(Number(page) || 0, 0), maxPage);
  const start = safePage * GLOBAL_EVIDENCE_PAGE_SIZE;
  const pageRows = filtered.slice(start, start + GLOBAL_EVIDENCE_PAGE_SIZE);
  const blocks = [
    textBlock(`Evidence: ${filtered.length} · сторінка ${safePage + 1}/${maxPage + 1}`),
    buttonsBlock([
      button(filter === "all" ? "✅ Усі" : "Усі", "evidence:all:0"),
      button(filter === "indicator" ? "✅ 🟢 Indicators" : "🟢 Indicators", "evidence:indicator:0"),
      button(
        filter === "counterindicator" ? "✅ 🔴 Counter" : "🔴 Counter",
        "evidence:counterindicator:0",
      ),
    ]),
  ];

  if (!pageRows.length) {
    blocks.push(textBlock("Evidence за цим фільтром немає."));
  } else {
    for (const { event, item } of pageRows) {
      const source = item.source || {};
      const sourceLabel = source.title || source.domain || source.url || "невідоме джерело";
      const token = eventToken(event.event_id);
      blocks.push(
        textBlock(
          [
            `#${item.evidence_id} · ${evidenceDirectionLabel(item.direction)}${item.strength ? ` · ${item.strength}` : ""}`,
            `Подія: ${shortText(event.title, 180)}`,
            shortText(item.summary, 420),
            `Джерело: ${shortText(sourceLabel, 180)}`,
            `Дата: ${item.created_at || "—"}`,
          ].join("\n"),
        ),
      );
      blocks.push(
        buttonsBlock([
          button(
            `🔎 #${item.evidence_id} · Деталі`,
            `evidence-detail:${token}:${item.evidence_id}:${filter}:${safePage}`,
          ),
          button(`🗑 #${item.evidence_id}`, `delete-evidence:${token}:${item.evidence_id}`, "danger"),
        ]),
      );
    }
  }

  const pager = [];
  if (safePage > 0) pager.push(button("◀️ Попередня", `evidence:${filter}:${safePage - 1}`));
  if (safePage < maxPage) pager.push(button("Наступна ▶️", `evidence:${filter}:${safePage + 1}`));
  if (pager.length) blocks.push(buttonsBlock(pager));
  blocks.push(
    buttonsBlock([
      button("◀️ До керування", "manage"),
      button("🏠 Головне меню", "home"),
    ]),
  );

  return { title: "🧾 Evidence", tone: "neutral", blocks };
}

function refreshOutcomeLabel(outcome) {
  if (outcome === "no_new_evidence") return "нових evidence немає";
  if (outcome === "new_evidence") return "знайдено нові evidence";
  if (outcome === "execution_failed") return "помилка виконання";
  if (outcome === "parse_failed") return "помилка обробки";
  return String(outcome || "невідомо");
}

async function latestRefreshPresentation() {
  const data = await apiGet("/api/v1/refresh/latest");
  const refresh = data.refresh;
  const results = Array.isArray(data.results) ? data.results : [];
  const blocks = [];

  if (!refresh) {
    blocks.push(textBlock("Історії refresh поки немає."));
  } else {
    blocks.push(
      textBlock(
        [
          `Refresh #${refresh.refresh_id} · ${refresh.status}`,
          `Початок: ${refresh.started_at || "—"}`,
          `Завершення: ${refresh.finished_at || "—"}`,
          `Перевірено подій: ${refresh.events_checked}`,
          `Подій з новими evidence: ${refresh.events_with_new_evidence}`,
          `Нових evidence: ${refresh.new_evidence_count}`,
          `Рекомендацій: ${refresh.recommendations_count}`,
          `Без зміни: ${refresh.no_change_count}`,
          `Помилок: ${refresh.error_count}`,
        ].join("\n"),
      ),
    );

    for (const item of results.slice(0, 8)) {
      const forecastLine =
        item.recommended_probability === null || item.recommended_probability === undefined
          ? `Прогноз: ${item.current_probability ?? item.baseline_probability ?? "—"}%`
          : `Прогноз: ${item.baseline_probability ?? "—"}% → ${item.recommended_probability}%`;
      const decisionLine = item.decision
        ? `Рішення: ${item.decision.decision_type} → ${item.decision.selected_probability}%`
        : null;
      blocks.push(
        textBlock(
          [
            shortText(item.event_title_snapshot || item.event_id || "Подія", 180),
            `Результат: ${refreshOutcomeLabel(item.outcome)}`,
            `Evidence: +${item.new_evidence_count} · 🟢 ${item.indicator_count} · 🔴 ${item.counterindicator_count}`,
            forecastLine,
            decisionLine,
          ].filter(Boolean).join("\n"),
        ),
      );
    }

    if (results.length > 8) {
      blocks.push(textBlock(`Показано 8 з ${results.length} результатів.`));
    }
  }

  blocks.push(
    buttonsBlock([
      button("↻ Оновити", "refresh"),
      button("🏠 Головне меню", "home"),
    ]),
  );
  return { title: "🔄 Останнє оновлення", tone: "neutral", blocks };
}

async function archivePresentation(page = 0) {
  const items = await archivedEvents();
  const maxPage = Math.max(0, Math.ceil(items.length / EVENT_LIST_PAGE_SIZE) - 1);
  const safePage = Math.min(Math.max(Number(page) || 0, 0), maxPage);
  const start = safePage * EVENT_LIST_PAGE_SIZE;
  const pageItems = items.slice(start, start + EVENT_LIST_PAGE_SIZE);
  const blocks = [
    textBlock(`Архівних прогнозів: ${items.length} · сторінка ${safePage + 1}/${maxPage + 1}`),
  ];

  if (!pageItems.length) {
    blocks.push(textBlock("Архів порожній."));
  } else {
    for (const event of pageItems) {
      const token = eventToken(event.event_id);
      blocks.push(
        buttonsBlock([
          button(
            `${event.current_assessment?.probability_percent ?? "—"}% · ${event.title}`.slice(0, 80),
            `event-any:${token}`,
          ),
        ]),
      );
    }
  }

  const pager = [];
  if (safePage > 0) pager.push(button("◀️ Попередня", `archive:${safePage - 1}`));
  if (safePage < maxPage) pager.push(button("Наступна ▶️", `archive:${safePage + 1}`));
  if (pager.length) blocks.push(buttonsBlock(pager));
  blocks.push(buttonsBlock([button("🏠 Головне меню", "home")]));

  return { title: "🗂 Архів", tone: "neutral", blocks };
}

async function candidatesPresentation() {
  const data = await apiGet("/api/v1/evidence/candidates?validation_state=accepted&sort=newest");
  const candidates = (Array.isArray(data.items) ? data.items : []).filter(
    (item) => !item.decision_type && item.event_id,
  );

  const byEvent = new Map();
  for (const item of candidates) {
    if (!byEvent.has(item.event_id)) byEvent.set(item.event_id, []);
    byEvent.get(item.event_id).push(item);
  }

  const pendingGroups = [];
  for (const [eventId, items] of byEvent.entries()) {
    let rec = null;
    try {
      const recommendationData = await apiGet(
        `/api/v1/events/${encodeURIComponent(eventId)}/latest-recommendation`,
      );
      rec = recommendationData.recommendation || null;
    } catch {
      rec = null;
    }

    if (!rec || rec.decision || (!rec.actionable && !rec.can_keep_current)) continue;
    const matching = items.filter(
      (item) => Number(item.refresh_event_result_id) === Number(rec.refresh_event_result_id),
    );
    if (matching.length) pendingGroups.push({ eventId, items: matching, rec });
  }

  const pendingCount = pendingGroups.reduce((sum, group) => sum + group.items.length, 0);
  const blocks = [textBlock(`Candidates, що очікують рішення: ${pendingCount}`)];

  if (!pendingGroups.length) {
    blocks.push(textBlock("Немає candidate evidence, для яких зараз потрібне рішення."));
  } else {
    for (const group of pendingGroups) {
      const token = eventToken(group.eventId);
      const first = group.items[0];
      const eventTitle = first.event_title || group.eventId;
      blocks.push(
        textBlock(
          [
            shortText(eventTitle, 180),
            `Refresh #${first.refresh_id} · candidates: ${group.items.length}`,
            ...group.items.slice(0, 3).map(
              (item) =>
                `#${item.ordinal} · ${evidenceDirectionLabel(item.direction)}${item.strength ? ` · ${item.strength}` : ""}\n${shortText(item.summary || item.title || "Без опису", 260)}`,
            ),
            group.items.length > 3 ? `Ще candidates: ${group.items.length - 3}` : null,
          ].filter(Boolean).join("\n"),
        ),
      );
      blocks.push(
        buttonsBlock([
          button(`🎯 Рішення · Refresh #${first.refresh_id}`, `recommendation:${token}`, "primary"),
        ]),
      );
    }
  }

  blocks.push(buttonsBlock([button("🏠 Головне меню", "home")]));
  return { title: "📥 Candidate Evidence", tone: "neutral", blocks };
}

async function recommendationsPresentation() {
  const items = await activeEvents();
  const blocks = [textBlock(`Активні прогнози: ${items.length}`)];

  if (!items.length) {
    blocks.push(textBlock("Активних прогнозів немає."));
  } else {
    for (const [index, event] of items.entries()) {
      const number = index + 1;
      const token = eventToken(event.event_id);
      let recommendation = null;
      try {
        const data = await apiGet(
          `/api/v1/events/${encodeURIComponent(event.event_id)}/latest-recommendation`,
        );
        recommendation = data.recommendation || null;
      } catch {
        recommendation = null;
      }

      const current = event.current_assessment?.probability_percent;
      const currentText = current === null || current === undefined ? "—" : `${current}%`;

      if (!recommendation) {
        blocks.push(
          textBlock(
            [
              `${number}. ${shortText(event.title, 180)}`,
              `Поточна оцінка: ${currentText}`,
              "Рекомендація: немає",
            ].join("\n"),
          ),
        );
        blocks.push(
          buttonsBlock([
            button(
              `${number} · ↗️ ${shortText(event.title, 48)} — рекомендації немає`.slice(0, 80),
              `event-any:${token}`,
            ),
          ]),
        );
        continue;
      }

      const recommended = recommendation.recommended_probability;
      const recommendedText =
        recommended === null || recommended === undefined ? "—" : `${recommended}%`;
      const statusText = recommendationStatusLabel(recommendation.status);

      blocks.push(
        textBlock(
          [
            `${number}. ${shortText(event.title, 180)}`,
            `Поточна оцінка: ${currentText}`,
            `Рекомендація: ${recommendedText}`,
            `Статус: ${statusText}`,
          ].join("\n"),
        ),
      );

      const row = [
        button(
          `${number} · 🎯 ${shortText(event.title, 48)} — рекомендація ${recommendedText}`.slice(0, 80),
          `recommendation:${token}`,
          "primary",
        ),
      ];
      blocks.push(buttonsBlock(row));
    }
  }

  blocks.push(
    buttonsBlock([
      button("◀️ До керування", "manage"),
      button("🏠 Головне меню", "home"),
    ]),
  );

  return { title: "🎯 Рекомендації", tone: "neutral", blocks };
}

async function manualRefreshPresentation() {
  try {
    const { stdout = "" } = await execFileAsync(
      PYTHON_BIN,
      [
        REFRESH_ALL_CLI,
        "--trigger-source",
        "telegram",
        "--start-at",
        "2m",
        "--spacing-minutes",
        "3",
      ],
      {
        timeout: 60000,
        maxBuffer: 256 * 1024,
        env: process.env,
      },
    );

    const refreshId = stdout.match(/^refresh_id:\s*(\d+)/m)?.[1] || "—";
    const targets = stdout.match(/^targets:\s*(\d+)/m)?.[1] || "—";
    return {
      title: "🔄 Перевірку запущено",
      tone: "neutral",
      blocks: [
        textBlock(
          [
            `Refresh #${refreshId}`,
            `Активних подій: ${targets}`,
            "Перевірки заплановано тим самим PROROK pipeline, що використовується ранковим оновленням.",
          ].join("\n"),
        ),
        buttonsBlock([
          button("🔄 Останнє оновлення", "refresh", "primary"),
          button("◀️ До керування", "manage"),
        ]),
      ],
    };
  } catch (error) {
    const detail = [
      String(error?.stderr || ""),
      String(error?.stdout || ""),
      String(error?.message || error),
    ].join("\n");

    if (detail.includes("refresh already running")) {
      const running = detail.match(/refresh already running:\s*([^\n]+)/)?.[1] || "";
      return {
        title: "⏳ Перевірка вже виконується",
        tone: "neutral",
        blocks: [
          textBlock(
            running
              ? `Новий запуск не створено. Активний refresh: ${running}`
              : "Новий запуск не створено, оскільки попередній refresh ще виконується.",
          ),
          buttonsBlock([
            button("🔄 Останнє оновлення", "refresh", "primary"),
            button("◀️ До керування", "manage"),
          ]),
        ],
      };
    }

    throw error;
  }
}

function managePresentation() {
  return {
    title: "⚙️ Керування",
    tone: "neutral",
    blocks: [
      textBlock(
        "Керування використовує deterministic PROROK operations. Видалення події або evidence завжди має окремий екран підтвердження.",
      ),
      buttonsBlock([button("🗂 Керування подіями", "manage-events:0", "primary")]),
      buttonsBlock([button("🧾 Керування evidence", "evidence:all:0")]),
      buttonsBlock([button("🎯 Рекомендації", "recommendations")]),
      buttonsBlock([button("🔄 Запустити перевірку", "refresh-run", "primary")]),
      buttonsBlock([button("🏠 Головне меню", "home")]),
    ],
  };
}

async function manageEventsPresentation(page = 0) {
  const items = await allEvents();
  const maxPage = Math.max(0, Math.ceil(items.length / EVENT_LIST_PAGE_SIZE) - 1);
  const safePage = Math.min(Math.max(Number(page) || 0, 0), maxPage);
  const start = safePage * EVENT_LIST_PAGE_SIZE;
  const pageItems = items.slice(start, start + EVENT_LIST_PAGE_SIZE);
  const blocks = [
    textBlock(`Подій: ${items.length} · сторінка ${safePage + 1}/${maxPage + 1}`),
  ];

  if (!pageItems.length) {
    blocks.push(textBlock("Подій немає."));
  } else {
    for (const event of pageItems) {
      const token = eventToken(event.event_id);
      blocks.push(
        buttonsBlock([
          button(
            `${eventStatusIcon(event.status)} ${event.current_assessment?.probability_percent ?? "—"}% · ${event.title}`.slice(0, 80),
            `event-any:${token}`,
          ),
        ]),
      );
    }
  }

  const pager = [];
  if (safePage > 0) pager.push(button("◀️ Попередня", `manage-events:${safePage - 1}`));
  if (safePage < maxPage) pager.push(button("Наступна ▶️", `manage-events:${safePage + 1}`));
  if (pager.length) blocks.push(buttonsBlock(pager));
  blocks.push(
    buttonsBlock([
      button("◀️ До керування", "manage"),
      button("🏠 Головне меню", "home"),
    ]),
  );

  return { title: "🗂 Керування подіями", tone: "neutral", blocks };
}

async function renderPayload(payload, ctx = null) {
  if (!payload || payload === "home") return mainPresentation();
  if (payload === "events") return await eventsPresentation();
  if (payload === "candidates") return await candidatesPresentation();
  if (payload.startsWith("evidence-assess-apply:")) {
    const r=parseEvidenceAssessmentRoute(payload,"evidence-assess-apply:",true);
    const eventId=await resolveEventId(r.token,{activeOnly:true});
    return await appliedEvidenceAssessmentPresentation(eventId,r.evidenceId,r.baselineAssessmentId,r.probability,ctx);
  }
  if (payload.startsWith("evidence-assess-value:")) {
    const r=parseEvidenceAssessmentRoute(payload,"evidence-assess-value:",true);
    const eventId=await resolveEventId(r.token,{activeOnly:true});
    return await evidenceAssessmentConfirmPresentation(eventId,r.evidenceId,r.baselineAssessmentId,r.probability);
  }
  if (payload.startsWith("evidence-assess:")) {
    const r=parseEvidenceAssessmentRoute(payload,"evidence-assess:",false);
    const eventId=await resolveEventId(r.token,{activeOnly:true});
    return await evidenceAssessmentChoicePresentation(eventId,r.evidenceId,r.baselineAssessmentId);
  }
  if (payload.startsWith("evidence-detail:")) {
    const { token, evidenceId, filter, page } = parseGlobalEvidenceDetailRoute(payload);
    const eventId = await resolveEventId(token);
    return await globalEvidenceDetailPresentation(eventId, evidenceId, filter, page);
  }
  if (payload.startsWith("evidence:")) {
    const [, filter = "all", rawPage = "0"] = payload.split(":");
    const safeFilter = ["all", "indicator", "counterindicator"].includes(filter) ? filter : "all";
    return await globalEvidencePresentation(safeFilter, Number.parseInt(rawPage, 10) || 0);
  }
  if (payload.startsWith("status-apply:")) {
    const { token, fromStatus, toStatus } = parseStatusRoute(payload, "status-apply:");
    const eventId = await resolveEventId(token);
    return await appliedStatusPresentation(eventId, fromStatus, toStatus, ctx);
  }
  if (payload.startsWith("status:")) {
    const { token, fromStatus, toStatus } = parseStatusRoute(payload, "status:");
    const eventId = await resolveEventId(token);
    return await statusConfirmationPresentation(eventId, fromStatus, toStatus);
  }
  if (payload.startsWith("delete-event-apply:")) {
    const token = payload.slice("delete-event-apply:".length);
    if (!token || token.includes(":")) throw new Error("Invalid PROROK event delete callback payload");
    const eventId = await resolveEventId(token);
    return await appliedEventDeletePresentation(eventId, ctx);
  }
  if (payload.startsWith("delete-event:")) {
    const token = payload.slice("delete-event:".length);
    if (!token || token.includes(":")) throw new Error("Invalid PROROK event delete callback payload");
    const eventId = await resolveEventId(token);
    return await eventDeleteConfirmationPresentation(eventId);
  }
  if (payload.startsWith("delete-evidence-apply:")) {
    const { token, evidenceId } = parseEvidenceDeleteRoute(payload, "delete-evidence-apply:");
    const eventId = await resolveEventId(token);
    return await appliedEvidenceDeletePresentation(eventId, evidenceId, ctx);
  }
  if (payload.startsWith("delete-evidence:")) {
    const { token, evidenceId } = parseEvidenceDeleteRoute(payload, "delete-evidence:");
    const eventId = await resolveEventId(token);
    return await evidenceDeleteConfirmationPresentation(eventId, evidenceId);
  }
  if (payload.startsWith("recommendation:")) {
    const eventId = await resolveEventId(payload.slice("recommendation:".length), { activeOnly: true });
    return await recommendationPresentation(eventId);
  }
  if (payload.startsWith("decision-accept:")) {
    const { token, resultId } = parseDecisionRoute(payload, "decision-accept:");
    const eventId = await resolveEventId(token, { activeOnly: true });
    return await appliedDecisionPresentation(eventId, resultId, "accept_recommendation");
  }
  if (payload.startsWith("decision-custom-value:")) {
    const { token, resultId, probability } = parseCustomDecisionRoute(
      payload,
      "decision-custom-value:",
    );
    const eventId = await resolveEventId(token, { activeOnly: true });
    return await customProbabilityConfirmPresentation(eventId, resultId, probability);
  }
  if (payload.startsWith("decision-custom-apply:")) {
    const { token, resultId, probability } = parseCustomDecisionRoute(
      payload,
      "decision-custom-apply:",
    );
    const eventId = await resolveEventId(token, { activeOnly: true });
    return await appliedDecisionPresentation(
      eventId,
      resultId,
      "custom_probability",
      probability,
    );
  }
  if (payload.startsWith("decision-custom:")) {
    const { token, resultId } = parseDecisionRoute(payload, "decision-custom:");
    const eventId = await resolveEventId(token, { activeOnly: true });
    return await customProbabilityPresentation(eventId, resultId);
  }
  if (payload.startsWith("decision-keep:")) {
    const { token, resultId } = parseDecisionRoute(payload, "decision-keep:");
    const eventId = await resolveEventId(token, { activeOnly: true });
    return await appliedDecisionPresentation(eventId, resultId, "keep_current");
  }
  if (payload.startsWith("event-evidence:")) {
    const eventId = await resolveEventId(payload.slice("event-evidence:".length));
    return await eventEvidencePresentation(eventId);
  }
  if (payload.startsWith("event-history:")) {
    const eventId = await resolveEventId(payload.slice("event-history:".length));
    return await eventHistoryPresentation(eventId);
  }
  if (payload.startsWith("event-any:")) {
    const eventId = await resolveEventId(payload.slice("event-any:".length));
    return await eventPresentation(eventId);
  }
  if (payload.startsWith("event:")) {
    const eventId = await resolveEventId(payload.slice("event:".length), { activeOnly: true });
    return await eventPresentation(eventId);
  }
  if (payload === "refresh-run") return await manualRefreshPresentation();
  if (payload === "refresh") return await latestRefreshPresentation();
  if (payload === "archive") return await archivePresentation(0);
  if (payload.startsWith("archive:")) {
    const rawPage = payload.slice("archive:".length);
    if (!/^\d+$/.test(rawPage)) throw new Error("Invalid PROROK archive page");
    return await archivePresentation(Number.parseInt(rawPage, 10));
  }
  if (payload === "manage") return managePresentation();
  if (payload === "recommendations") return await recommendationsPresentation();
  if (payload.startsWith("manage-events:")) {
    const rawPage = payload.slice("manage-events:".length);
    if (!/^\d+$/.test(rawPage)) throw new Error("Invalid PROROK manage-events page");
    return await manageEventsPresentation(Number.parseInt(rawPage, 10));
  }
  return mainPresentation();
}

export default definePluginEntry({
  id: "prorok-telegram",
  name: "PROROK Telegram Control UI",
  description: "Deterministic native Telegram control surface for PROROK.",
  register(api) {
    api.registerCommand({
      name: "prorok",
      description: "Open the PROROK control menu.",
      acceptsArgs: false,
      requireAuth: true,
      channels: ["telegram"],
      handler: async () => ({ text: "PROROK", presentation: mainPresentation() }),
    });

    api.registerInteractiveHandler({
      channel: "telegram",
      namespace: CALLBACK_NAMESPACE,
      handler: async (ctx) => {
        if (!ctx?.auth?.isAuthorizedSender) {
          await ctx.respond.reply({ text: "PROROK: доступ заборонено." });
          return { handled: true };
        }

        try {
          const presentation = await renderPayload(ctx.callback.payload, ctx);
          const text = presentation.title || "PROROK";
          const buttons = presentation.blocks
            .filter((block) => block.type === "buttons")
            .map((block) =>
              block.buttons
                .filter((item) => item.value)
                .map((item) => ({
                  text: item.label,
                  callback_data: item.value,
                  ...(item.style === "danger" || item.style === "success" || item.style === "primary"
                    ? { style: item.style }
                    : {}),
                })),
            );
          const body = presentation.blocks
            .filter((block) => block.type === "text")
            .map((block) => block.text)
            .join("\n\n");
          await ctx.respond.editMessage({ text: body ? `${text}\n\n${body}` : text, buttons });
        } catch (error) {
          const errorText = String(error);
          if (!errorText.includes("message is not modified")) {
            await ctx.respond.reply({ text: `PROROK error: ${errorText.slice(0, 400)}` });
          }
        }
        return { handled: true };
      },
    });
  },
});