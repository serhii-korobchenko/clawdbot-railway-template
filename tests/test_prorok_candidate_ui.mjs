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
];

assert.deepEqual(pendingCandidates(items).map((x) => x.candidate_id), [7, 8]);
assert.deepEqual(candidatePage(items, 0, 1), { items: [items[0]], total: 2, page: 0, maxPage: 1 });
assert.equal(candidatePage(items, 1, 1).items[0].candidate_id, 8);
assert.equal(candidateRoute(8), "candidate:8");
assert.equal(parseCandidateRoute("candidate:7"), 7);
assert.throws(() => parseCandidateRoute("candidate:x"), /Invalid Candidate/);

console.log("PASS");
