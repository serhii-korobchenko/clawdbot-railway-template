import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";
import { extractReelUrl, formatReelAnalysis, shouldHandleReel } from "./routing.js";

const execFileAsync = promisify(execFile);
const PYTHON_BIN = process.env.REEL_ANALYZER_PYTHON || "python3";

async function analyzeReel(url) {
  const { stdout } = await execFileAsync(
    PYTHON_BIN,
    ["-m", "reel_analyzer.reel_analyzer", url],
    { cwd: "/app", env: process.env, maxBuffer: 1024 * 1024, timeout: 180000 },
  );
  return JSON.parse(stdout);
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

      const reelUrl = extractReelUrl(content);
      const matched = shouldHandleReel({ channel, sessionKey, content });
      api.logger?.info?.(`[reel-telegram] before_dispatch target_topic=true reel_url_present=${Boolean(reelUrl)} matched=${matched}`);
      if (!matched) return;

      api.logger?.info?.("[reel-telegram] analyzer started");
      try {
        const result = await analyzeReel(reelUrl);
        api.logger?.info?.("[reel-telegram] analyzer completed");
        const text = formatReelAnalysis(result);
        api.logger?.info?.("[reel-telegram] returning handled=true result=success");
        return { handled: true, text };
      } catch (error) {
        const errorCode = typeof error?.code === "string" || typeof error?.code === "number"
          ? String(error.code).replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 40)
          : "unknown";
        api.logger?.warn?.(`[reel-telegram] analyzer failed code=${errorCode}`);
        api.logger?.info?.("[reel-telegram] returning handled=true result=fallback");
        return {
          handled: true,
          text: "Не вдалося проаналізувати цей Reel. Він може бути приватним, видаленим, обмеженим Instagram або тимчасово недоступним. Спробуйте ще раз пізніше.",
        };
      }
    });
  },
});
