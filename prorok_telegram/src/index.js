import { createHash } from "node:crypto";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const API_BASE = process.env.PROROK_API_BASE_URL || "http://127.0.0.1:18880";
const API_TOKEN = process.env.PROROK_API_TOKEN || "";
const CALLBACK_NAMESPACE = "prorok";
const EVENT_TOKEN_LENGTH = 12;

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
        button("🧾 Evidence", "evidence"),
        button("🔄 Останнє оновлення", "refresh"),
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

async function activeEvents() {
  const data = await apiGet("/api/v1/events?status=active");
  return Array.isArray(data.items) ? data.items : [];
}

async function resolveActiveEventId(token) {
  const items = await activeEvents();
  const matches = items.filter((event) => eventToken(event.event_id) === token);
  if (matches.length === 1) {
    return String(matches[0].event_id);
  }
  if (matches.length > 1) {
    throw new Error("Event callback token collision");
  }
  throw new Error("Event is no longer active or callback is stale");
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

  return {
    title: event.title,
    tone: "neutral",
    blocks: [
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
      buttonsBlock([
        button("🧾 Evidence", `event-evidence:${token}`),
        button("📈 Історія", `event-history:${token}`),
      ]),
      buttonsBlock([
        button("◀️ До прогнозів", "events"),
        button("🏠 Головне меню", "home"),
      ]),
    ],
  };
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
    }
    if (evidence.length > 10) {
      blocks.push(textBlock(`Показано 10 з ${evidence.length} evidence.`));
    }
  }

  blocks.push(
    buttonsBlock([
      button("◀️ До події", `event:${token}`),
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
    if (assessments.length > 12) {
      blocks.push(textBlock(`Показано 12 з ${assessments.length} assessment.`));
    }
  }

  blocks.push(
    buttonsBlock([
      button("◀️ До події", `event:${token}`),
      button("📊 До прогнозів", "events"),
    ]),
  );
  return { title: `📈 ${event.title}`, tone: "neutral", blocks };
}

function placeholderPresentation(title) {
  return {
    title,
    tone: "neutral",
    blocks: [
      textBlock("Цей розділ буде реалізований у наступних кроках M2."),
      buttonsBlock([button("◀️ Назад", "home")]),
    ],
  };
}

async function renderPayload(payload) {
  if (!payload || payload === "home") return mainPresentation();
  if (payload === "events") return await eventsPresentation();
  if (payload.startsWith("event-evidence:")) {
    const eventId = await resolveActiveEventId(payload.slice("event-evidence:".length));
    return await eventEvidencePresentation(eventId);
  }
  if (payload.startsWith("event-history:")) {
    const eventId = await resolveActiveEventId(payload.slice("event-history:".length));
    return await eventHistoryPresentation(eventId);
  }
  if (payload.startsWith("event:")) {
    const eventId = await resolveActiveEventId(payload.slice("event:".length));
    return await eventPresentation(eventId);
  }
  if (payload === "evidence") return placeholderPresentation("🧾 Evidence");
  if (payload === "refresh") return placeholderPresentation("🔄 Останнє оновлення");
  if (payload === "archive") return placeholderPresentation("🗂 Архів");
  if (payload === "manage") return placeholderPresentation("⚙️ Керування");
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
      handler: async () => ({
        text: "PROROK",
        presentation: mainPresentation(),
      }),
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
          const presentation = await renderPayload(ctx.callback.payload);
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
          await ctx.respond.reply({ text: `PROROK error: ${String(error).slice(0, 400)}` });
        }
        return { handled: true };
      },
    });
  },
});
