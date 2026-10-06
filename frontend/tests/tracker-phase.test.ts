import { describe, expect, it } from "vitest";
import { parseTracker, TRACKER_STATUSES } from "../src/adapters/tracker";
import { createLiveAdapter } from "../src/adapters/live";
function fixture(status: "CLOSED" | "BLOCKED") {
  const row = { id: "OBS-05", area: "Runtime", stage: "1", release_scope: "BASELINE", status,
    required_behavior: "Retained proof", why_needed: "Reason", closure_condition: "Exact condition",
    dependencies: [], parent_ids: [], related_items: [], latest_evidence: "Pinned evidence", next_proof: "None", owner_role: "Engineering" };
  return { generated_at: "2026-10-06T00:00:00Z", status: "AVAILABLE", read_only: true,
    selected: { id: row.id, status, mode: "ENGINEERING_ONLY", objective: "Terminal history" },
    source: { path: "tracker.yaml", sha256: "hash", next_path: "NEXT.yaml", next_sha256: "hash", repository_revision: null, observed_runtime_revision: "revision", version: "1" },
    items: [row], counts: Object.fromEntries(TRACKER_STATUSES.map(s => [s, s === status ? 1 : 0])),
    status_definitions: TRACKER_STATUSES.map(status => ({ status, meaning: "Meaning" })),
    readiness_gates: Array.from({ length: 7 }, (_, i) => ({ id: `M${i}`, title: "Gate", current: "Unknown", closure: "Proof", limit: "Scope", items: [row.id] })) };
}
describe("Dashboard phase contracts", () => {
  it.each(["CLOSED", "BLOCKED"] as const)("accepts backend terminal %s without creating active work", status => {
    const data = parseTracker(fixture(status));
    expect(data.selected.status).toBe(status);
    expect(data.counts.IN_PROGRESS).toBe(0);
  });
  it("rejects a terminal selection conflicting with row truth", () => {
    const data = fixture("CLOSED"); data.items[0].status = "BLOCKED";
    expect(() => parseTracker(data)).toThrow();
  });
  it("read-only adapter exposes no control capability", () => {
    expect(createLiveAdapter(() => {}, true).control).toBeUndefined();
    expect(createLiveAdapter(() => {}, false).control).toBeTypeOf("function");
  });
});
