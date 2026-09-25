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
      if (!shouldHandleReel({ channel, sessionKey, content })) return;

      try {
        const result = await analyzeReel(extractReelUrl(content));
        return { handled: true, text: formatReelAnalysis(result) };
      } catch (error) {
        const detail = String(error?.stderr || error?.message || error).trim();
        api.logger?.warn?.(`[reel-telegram] analyzer failed: ${detail.slice(0, 500)}`);
        return {
          handled: true,
          text: "Не вдалося проаналізувати цей Reel. Він може бути приватним, видаленим, обмеженим Instagram або тимчасово недоступним. Спробуйте ще раз пізніше.",
        };
      }
    });
  },
});
