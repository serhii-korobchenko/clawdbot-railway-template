const REEL_TOPIC_ID = "1570";
const INSTAGRAM_URL_RE = /https:\/\/(?:www\.)?(?:instagram\.com|oginstagram\.com)\/(reel|p)\/([A-Za-z0-9_-]+)(?:\/[^\s]*)?/i;

export function extractInstagramUrl(text) {
  const match = String(text || "").match(INSTAGRAM_URL_RE);
  if (!match) return null;
  const [, kind, shortcode] = match;
  const rawQuery = match[0].includes("?") ? match[0].split("?")[1].split("#")[0] : "";
  const index = new URLSearchParams(rawQuery).get("img_index");
  const selected = kind.toLowerCase() === "p" && index && /^[1-9][0-9]*$/.test(index)
    ? `?img_index=${index}` : "";
  return `https://www.instagram.com/${kind.toLowerCase()}/${shortcode}/${selected}`;
}

export function extractReelUrl(text) {
  const url = extractInstagramUrl(text);
  return url && new URL(url).pathname.startsWith("/reel/") ? url : null;
}

export function shouldHandleReel({ channel, sessionKey, content }) {
  const topicSuffix = `:topic:${REEL_TOPIC_ID}`;
  return String(channel || "").toLowerCase() === "telegram"
    && String(sessionKey || "").endsWith(topicSuffix)
    && Boolean(extractInstagramUrl(content));
}

export function formatReelAnalysis(result) {
  const transcript = String(result?.transcript || "").trim();
  const visualFacts = String(result?.visual_facts || "").trim();
  const title = String(result?.title || "").trim();
  const isPost = result?.media_type === "post";
  return [
    isPost ? "🖼️ Аналіз публікації Instagram" : "🎬 Аналіз Reel",
    title ? `**${title}**` : null,
    "",
    "**1. Короткий зміст**",
    transcript || (isPost ? "Публікація без розпізнаного мовлення." : "Не вдалося надійно розпізнати мовлення."),
    "",
    "**2. Головні тези, ресурси та практичні деталі**",
    visualFacts || "Не вдалося надійно витягти візуальні деталі.",
    "",
    "**3. Невизначеність**",
    (!transcript || !visualFacts)
      ? "Частина аудіо або візуальної інформації не була надійно розпізнана."
      : isPost ? "Аналіз базується на доступних медіа публікації; частина каруселі може бути недоступна." : "Аналіз базується на транскрипті та репрезентативних кадрах Reel; дрібні або короткочасні елементи могли не потрапити у вибірку.",
  ].filter((line) => line !== null).join("\n");
}
