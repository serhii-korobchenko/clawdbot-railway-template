const REEL_TOPIC_ID = "1570";
const REEL_URL_RE = /https:\/\/(?:www\.)?(?:instagram\.com|oginstagram\.com)\/reel\/([A-Za-z0-9_-]+)(?:\/[^\s]*)?/i;

export function extractReelUrl(text) {
  const match = String(text || "").match(REEL_URL_RE);
  if (!match) return null;
  return `https://www.instagram.com/reel/${match[1]}/`;
}

export function shouldHandleReel({ channel, sessionKey, content }) {
  const topicSuffix = `:topic:${REEL_TOPIC_ID}`;
  return String(channel || "").toLowerCase() === "telegram"
    && String(sessionKey || "").endsWith(topicSuffix)
    && Boolean(extractReelUrl(content));
}

export function formatReelAnalysis(result) {
  const transcript = String(result?.transcript || "").trim();
  const visualFacts = String(result?.visual_facts || "").trim();
  const title = String(result?.title || "").trim();
  return [
    "🎬 Аналіз Reel",
    title ? `**${title}**` : null,
    "",
    "**1. Короткий зміст**",
    transcript || "Не вдалося надійно розпізнати мовлення.",
    "",
    "**2. Головні тези, ресурси та практичні деталі**",
    visualFacts || "Не вдалося надійно витягти візуальні деталі.",
    "",
    "**3. Невизначеність**",
    (!transcript || !visualFacts)
      ? "Частина аудіо або візуальної інформації не була надійно розпізнана."
      : "Аналіз базується на транскрипті та репрезентативних кадрах Reel; дрібні або короткочасні елементи могли не потрапити у вибірку.",
  ].filter((line) => line !== null).join("\n");
}
