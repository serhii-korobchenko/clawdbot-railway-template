import assert from "node:assert/strict";
import {
  candidatePage,
  candidateRoute,
  parseCandidateRoute,
  pendingCandidates,
} from "../prorok_telegram/src/candidate_ui.js";

const items = [
  { candidate_id: 7, event_id: "event-a", validation_state: "accepted", decision_type: null },
  { candidate_id: 8, event_id: "event-b", validation_state: "accepted", decision_type: null },
  { candidate_id: 9, event_id: "event-a", validation_state: "accepted", decision_type: "reject" },
  { candidate_id: 10, event_id: "event-c", validation_state: "rejected_source_policy", decision_type: null },
  { candidate_id: 11, event_id: "event-d", validation_state: "accepted", decision_type: null, review_decision_type: "accept", promotion_action: "inserted", evidence_id: 21 },
  { candidate_id: 12, event_id: "event-e", validation_state: "accepted", decision_type: null, review_decision_type: "reject" },
  { candidate_id: 13, event_id: "event-f", validation_state: "accepted", decision_type: null, promotion_action: "reused", evidence_id: 22 },
];

assert.deepEqual(pendingCandidates(items).map((x) => x.candidate_id), [7, 8]);
assert.equal(pendingCandidates(items).some((x) => x.candidate_id === 11), false);
assert.equal(pendingCandidates(items).some((x) => x.candidate_id === 12), false);
assert.equal(pendingCandidates(items).some((x) => x.candidate_id === 13), false);
assert.deepEqual(candidatePage(items, 0, 1), { items: [items[0]], total: 2, page: 0, maxPage: 1 });
assert.equal(candidatePage(items, 1, 1).items[0].candidate_id, 8);
assert.equal(candidateRoute(8), "candidate:8");
assert.equal(parseCandidateRoute("candidate:7"), 7);
assert.throws(() => parseCandidateRoute("candidate:x"), /Invalid Candidate/);

console.log("PASS");
