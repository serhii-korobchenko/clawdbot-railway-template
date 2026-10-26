import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { extractInstagramUrl, formatReelAnalysis, shouldHandleReel } from "./routing.js";

const execFileAsync = promisify(execFile);
const PYTHON_BIN = process.env.REEL_ANALYZER_PYTHON || "python3";

async function analyzeReel(url) {
  const { stdout } = await execFileAsync(
    PYTHON_BIN,
    ["-m", "reel_analyzer.reel_analyzer", url],
    { cwd: "/app", env: process.env, maxBuffer: 1024 * 1024, timeout: url.includes("/p/") ? 900000 : 180000 },
  );
  return JSON.parse(stdout);
}

const REEL_CHAT_ID = "-1003804919781";
const REEL_THREAD_ID = 1570;
const REEL_SESSION_KEY = `agent:main:telegram:group:${REEL_CHAT_ID}:topic:${REEL_THREAD_ID}`;

async function deliverReel(api, text, sessionKey, accountId) {
  if (sessionKey !== REEL_SESSION_KEY) throw new Error("unexpected_reel_session");
  const adapter = await api.runtime.channel.outbound.loadAdapter("telegram");
  if (typeof adapter?.sendText !== "function") throw new Error("telegram_sendText_unavailable");
  const receipt = await adapter.sendText({
    cfg: api.config,
    to: REEL_CHAT_ID,
    text,
    threadId: REEL_THREAD_ID,
    ...(accountId ? { accountId } : {}),
  });
  if (!receipt?.messageId) throw new Error("telegram_delivery_receipt_missing");
  api.logger?.info?.("[reel-telegram] delivery completed receipt_present=true");
}

export default definePluginEntry({
  id: "reel-telegram",
  name: "Reel Analyzer Telegram Router",
  description: "Deterministically analyzes Instagram Reels posted in Telegram topic 1570.",
  register(api) {
    api.on("before_dispatch", async (event, ctx) => {
      const channel = event?.channel || ctx?.channelId;
      const sessionKey = event?.sessionKey || ctx?.sessionKey;
      const content = event?.content ?? event?.body ?? "";
      const inTargetTopic = String(channel || "").toLowerCase() === "telegram"
        && String(sessionKey || "").endsWith(":topic:1570");
      if (!inTargetTopic) return;

      const reelUrl = extractInstagramUrl(content);
      const matched = shouldHandleReel({ channel, sessionKey, content });
      api.logger?.info?.(`[reel-telegram] before_dispatch target_topic=true instagram_url_present=${Boolean(reelUrl)} matched=${matched}`);
      if (!matched) return;

      api.logger?.info?.("[reel-telegram] analyzer started");
      let text;
      try {
        const result = await analyzeReel(reelUrl);
        api.logger?.info?.("[reel-telegram] analyzer completed");
        text = formatReelAnalysis(result);
      } catch (error) {
        const errorCode = typeof error?.code === "string" || typeof error?.code === "number"
          ? String(error.code).replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 40)
          : "unknown";
        api.logger?.warn?.(`[reel-telegram] analyzer failed code=${errorCode}`);
        text = "Не вдалося проаналізувати цю публікацію Instagram. Він може бути приватним, видаленим, обмеженим Instagram або тимчасово недоступним. Спробуйте ще раз пізніше.";
      }

      try {
        await deliverReel(api, text, sessionKey, ctx?.accountId);
      } catch (error) {
        const code = String(error?.code || error?.message || "unknown").replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 50);
        api.logger?.warn?.(`[reel-telegram] delivery failed code=${code}`);
      }
      api.logger?.info?.("[reel-telegram] returning handled=true delivery=adapter");
      return { handled: true };
    });
  },
});
