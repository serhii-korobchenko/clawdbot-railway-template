import assert from "node:assert/strict";
import fs from "node:fs";

const src = fs.readFileSync("prorok_telegram/src/index.js", "utf8");

assert.match(
  src,
  /const \{ data, event, item \} = await loadEvidenceForDeletion\(eventId, evidenceId\);\s+const currentAssessment = data\.current_assessment;/
);
assert.match(
  src,
  /currentAssessment\?\.assessment_id \? \[button\(`🤖 Отримати рекомендацію`, `evidence-rec:\$\{token\}:\$\{item\.evidence_id\}:\$\{currentAssessment\.assessment_id\}`/
);
assert.match(
  src,
  /button\(`📊 #\$\{item\.evidence_id\} · Переоцінити`, `evidence-assess:\$\{token\}:\$\{item\.evidence_id\}:\$\{currentAssessment\.assessment_id\}`\)/
);
assert.doesNotMatch(
  src,
  /event\.current_assessment\?\.assessment_id/
);

console.log("PASS");
