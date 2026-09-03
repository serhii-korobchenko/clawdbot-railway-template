import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const API_BASE = process.env.PROROK_API_BASE_URL || "http://127.0.0.1:18880";
const API_TOKEN = process.env.PROROK_API_TOKEN || "";
const CALLBACK_NAMESPACE = "prorok";

function callbackValue(payload) {
  return `${CALLBACK_NAMESPACE}:${payload}`;
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

function currentAssessmentLine(event) {
  const a = event.current_assessment;
  if (!a) return "Поточна оцінка: немає";
  const confidence = a.confidence ? ` · ${a.confidence}` : "";
  return `Поточна оцінка: ${a.probability_percent}%${confidence}`;
}

async function eventsPresentation() {
  const data = await apiGet("/api/v1/events?status=active");
  const items = Array.isArray(data.items) ? data.items : [];
  const blocks = [
    textBlock(`Активні прогнози: ${items.length}`),
  ];

  if (!items.length) {
    blocks.push(textBlock("Активних прогнозів немає."));
  } else {
    for (const event of items.slice(0, 12)) {
      const safeId = String(event.event_id || "");
      const payload = `event:${safeId}`;
      if (callbackValue(payload).length > 64) {
        blocks.push(textBlock(`• ${event.title}\n${currentAssessmentLine(event)}`));
        continue;
      }
      blocks.push(
        buttonsBlock([
          button(
            `${event.current_assessment?.probability_percent ?? "—"}% · ${event.title}`.slice(0, 80),
            payload,
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
        button("◀️ До прогнозів", "events"),
        button("🏠 Головне меню", "home"),
      ]),
    ],
  };
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
  if (payload.startsWith("event:")) return await eventPresentation(payload.slice("event:".length));
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
