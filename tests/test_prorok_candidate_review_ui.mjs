import assert from "node:assert/strict";
import fs from "node:fs";

const src = fs.readFileSync("prorok_telegram/src/index.js", "utf8");

assert.match(src, /CANDIDATE_REVIEW_CLI\s*=\s*"prorok\/prorok_candidate_review_cli\.py"/);

assert.match(
  src,
  /candidate-review:\$\{candidateId\}:\$\{recommendationId\}:accept/
);
assert.match(
  src,
  /candidate-review:\$\{candidateId\}:\$\{recommendationId\}:reject/
);

assert.match(src, /async function candidateReviewConfirmPresentation/);
assert.match(src, /candidate-review-apply:\$\{candidateId\}:\$\{recommendationId\}:\$\{decision\}/);

assert.match(src, /"--recommendation-id", String\(recommendationId\)/);
assert.match(src, /"--source", "telegram"/);
assert.match(src, /telegramActorSnapshot\(ctx\)/);
assert.match(src, /args\.push\("--actor", String\(actor\)\)/);

const applyPos = src.indexOf('payload.startsWith("candidate-review-apply:")');
const confirmPos = src.indexOf('payload.startsWith("candidate-review:")');
const recPos = src.indexOf('payload.startsWith("candidate-rec:")');
const candidatePos = src.indexOf('payload.startsWith("candidate:")');

assert.ok(applyPos >= 0);
assert.ok(confirmPos > applyPos);
assert.ok(recPos > confirmPos);
assert.ok(candidatePos > recPos);

console.log("PASS");
