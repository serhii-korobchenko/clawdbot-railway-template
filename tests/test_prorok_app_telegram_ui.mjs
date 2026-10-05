import assert from "node:assert/strict";
import fs from "node:fs";

const src = fs.readFileSync("prorok_telegram/src/index.js", "utf8");

assert.match(src, /PROROK_APP_CLI/);
assert.match(src, /async function runProrokAppShow/);
assert.match(src, /async function runProrokAppSet/);

assert.match(src, /"show", String\(evidenceId\)/);
assert.match(src, /"--state", state/);
assert.match(src, /"--source", "telegram"/);
assert.match(src, /telegramActorSnapshot\(ctx\)/);

assert.match(src, /prorokApp\.state === "marked"/);
assert.match(src, /📤 Внести до PROROK_APP/);
assert.match(src, /↩️ Зняти позначку PROROK_APP/);
assert.match(src, /PROROK_APP: \$\{prorokAppState/);

assert.match(src, /function parseProrokAppRoute/);
assert.match(src, /async function appliedProrokAppPresentation/);
assert.match(src, /prorok-app:\$\{token\}:\$\{item\.evidence_id\}/);

const appPos = src.indexOf('payload.startsWith("prorok-app:")');
const detailPos = src.indexOf('payload.startsWith("evidence-detail:")');
const evidencePos = src.indexOf('payload.startsWith("evidence:")');

assert.ok(appPos >= 0);
assert.ok(detailPos > appPos);
assert.ok(evidencePos > detailPos);

console.log("PASS");


assert.match(src, /button\("📋 Логи", "logs"/);
assert.match(src, /TRAJECTORY_EXPORT_CLI/);
assert.match(src, /async function sendLatestLogs/);
assert.match(src, /loadAdapter\("telegram"\)/);
assert.match(src, /adapter\.sendMedia/);
assert.match(src, /mediaLocalRoots: \[path\.dirname\(archivePath\)\]/);
assert.match(src, /ctx\.callback\.payload === "logs"/);
