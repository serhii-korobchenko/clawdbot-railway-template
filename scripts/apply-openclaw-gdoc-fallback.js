```js
import fs from "node:fs";
import path from "node:path";

const OPENCLAW_DIST = process.env.OPENCLAW_DIST?.trim() || "/openclaw/dist";

function log(message) {
  console.log("[gdoc-fallback-patch] " + message);
}

function fail(message) {
  console.error("[gdoc-fallback-patch] ERROR: " + message);
  process.exit(1);
}

function findGetReplyFile() {
  let entries;
  try {
    entries = fs.readdirSync(OPENCLAW_DIST);
  } catch (err) {
    fail(`Cannot read OpenClaw dist directory ${OPENCLAW_DIST}: ${err}`);
  }

  const candidates = entries
    .filter((name) => /^get-reply-.*\.js$/.test(name))
    .map((name) => path.join(OPENCLAW_DIST, name));

  for (const candidate of candidates) {
    const text = fs.readFileSync(candidate, "utf8");
    if (text.includes("Tool not available") && text.includes("resolveSkillDispatchTools")) {
      return candidate;
    }
  }

  fail(`Could not find get-reply runtime file in ${OPENCLAW_DIST}`);
}

function buildFallbackBlock(indent) {
  const line = (spaces, text) => `${indent}${" ".repeat(spaces)}${text}`;

  return [
    line(0, "if (!tool) {"),
    line(8, 'if (dispatch.toolName === "gdoc_report") {'),
    line(16, "// gdoc_report fallback: deterministic local Google Docs router call."),
    line(16, "try {"),
    line(24, 'const port = process.env.PORT || "8080";'),
    line(24, "const response = await fetch(`http://127.0.0.1:${port}/gdoc`, {"),
    line(32, 'method: "POST",'),
    line(32, "headers: {"),
    line(40, '"Content-Type": "application/json"'),
    line(32, "},"),
    line(32, "body: JSON.stringify({ task: rawArgs }),"),
    line(32, "signal: opts?.abortSignal"),
    line(24, "});"),
    line(24, "const bodyText = await response.text();"),
    line(24, "let data;"),
    line(24, "try {"),
    line(32, "data = JSON.parse(bodyText);"),
    line(24, "} catch {"),
    line(32, "data = {"),
    line(40, "ok: false,"),
    line(40, "error: `Non-JSON response from /gdoc: ${bodyText.slice(0, 800)}`"),
    line(32, "};"),
    line(24, "}"),
    line(24, "typing.cleanup();"),
    line(24, "if (!response.ok || !data?.ok) {"),
    line(32, "const error = data?.error ? String(data.error) : `HTTP ${response.status}`;"),
    line(32, "return {"),
    line(40, 'kind: "reply",'),
    line(40, "reply: { text: `Status: failed\\nError: ${error.slice(0, 1800)}` }"),
    line(32, "};"),
    line(24, "}"),
    line(24, 'const title = data.title || "Google Docs report";'),
    line(24, 'const url = data.url || "";'),
    line(24, 'const documentId = data.document_id || data.documentId || "";'),
    line(24, "return {"),
    line(32, 'kind: "reply",'),
    line(32, "reply: {"),
    line(40, "text: ["),
    line(48, "`Title: ${title}`,"),
    line(48, "`URL: ${url}`,"),
    line(48, 'documentId ? `Document ID: ${documentId}` : "",'),
    line(48, '"Summary: Google Docs operation completed through deterministic gdoc_report fallback."'),
    line(40, '].filter(Boolean).join("\\n")'),
    line(32, "}"),
    line(24, "};"),
    line(16, "} catch (err) {"),
    line(24, "const message = formatErrorMessage(err);"),
    line(24, "typing.cleanup();"),
    line(24, "return {"),
    line(32, 'kind: "reply",'),
    line(32, "reply: { text: `Status: failed\\nError: ${message}` }"),
    line(24, "};"),
    line(16, "}"),
    line(8, "}"),
    line(8, "typing.cleanup();"),
    line(8, "return {"),
    line(16, 'kind: "reply",'),
    line(16, "reply: { text: `❌ Tool not available: ${dispatch.toolName}` }"),
    line(8, "};"),
    line(0, "}"),
  ].join("\n");
}

function patchGetReplyFile(file) {
  let text = fs.readFileSync(file, "utf8");

  if (text.includes("gdoc_report fallback: deterministic local Google Docs router call")) {
    log(`Already patched: ${file}`);
    return;
  }

  const pattern = /(\s*)if \(!tool\) \{\s*typing\.cleanup\(\);\s*return \{\s*kind: "reply",\s*reply: \{ text: `❌ Tool not available: \$\{dispatch\.toolName\}` \}\s*\};\s*\}/s;
  const match = text.match(pattern);

  if (!match) {
    const idx = text.indexOf("Tool not available");
    const context = idx >= 0 ? text.slice(Math.max(0, idx - 500), idx + 500) : "not found";
    fail(`Could not locate Tool not available block in ${file}. Context:\n${context}`);
  }

  const replacement = buildFallbackBlock(match[1]);
  text = text.replace(pattern, replacement);

  fs.writeFileSync(file, text, "utf8");
  log(`Patched ${file}`);
}

const file = findGetReplyFile();
patchGetReplyFile(file);
log("OpenClaw gdoc fallback patch complete.");
```
