export const CANDIDATE_PAGE_SIZE = 8;

export function pendingCandidates(items) {
  return (Array.isArray(items) ? items : []).filter(
    (item) =>
      item &&
      item.event_id &&
      item.candidate_id !== null &&
      item.candidate_id !== undefined &&
      !item.decision_type &&
      !item.review_decision_type &&
      !item.promotion_action &&
      !item.evidence_id &&
      String(item.validation_state || "accepted") === "accepted",
  );
}

export function candidatePage(items, page = 0, pageSize = CANDIDATE_PAGE_SIZE) {
  const pending = pendingCandidates(items);
  const maxPage = Math.max(0, Math.ceil(pending.length / pageSize) - 1);
  const safePage = Math.min(Math.max(Number(page) || 0, 0), maxPage);
  const start = safePage * pageSize;
  return {
    items: pending.slice(start, start + pageSize),
    total: pending.length,
    page: safePage,
    maxPage,
  };
}

export function candidateRoute(candidateId) {
  const id = Number(candidateId);
  if (!Number.isInteger(id) || id <= 0) throw new Error("Invalid Candidate id");
  return `candidate:${id}`;
}

export function parseCandidateRoute(payload, prefix = "candidate:") {
  const raw = String(payload || "");
  if (!raw.startsWith(prefix)) throw new Error("Invalid Candidate callback payload");
  const id = raw.slice(prefix.length);
  if (!/^\d+$/.test(id) || Number(id) <= 0) throw new Error("Invalid Candidate callback payload");
  return Number(id);
}
