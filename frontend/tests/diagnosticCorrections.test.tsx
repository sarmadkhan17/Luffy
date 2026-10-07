import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { recoveryTruth, reconciliationTruth, collectorTruth, needsYouCount } from "../src/diagnosticTruth";
import { StorageTruth } from "../src/components/StorageTruth";
const now = Date.parse("2026-10-08T00:00:00Z");
const proof = { observed_at: new Date(now).toISOString(), stale_after_s: 240, freshness: "fresh", checks_are_current: true, reconciliation: true, reasons: ["heartbeat_recovery"] };
describe("Supervisor evidence controls display, never historical counts", () => {
  it("shows a 46-hour-old recovery report as STALE, with no active recovery claim", () => {
    expect(recoveryTruth({ ...proof, freshness: "stale", age_s: 46 * 3600, observed_at: new Date(now - 46 * 3600000).toISOString() }, now)).toMatchObject({ status: "STALE", level: "warn" });
    expect(recoveryTruth(proof, now).status).toBe("ACTIVE");
    expect(recoveryTruth(null, now).status).toBe("NOT REPORTED");
    expect(recoveryTruth(proof, now + 241000).status).toBe("STALE");
  });
  it("does not make recent activity without proof healthy", () => {
    expect(reconciliationTruth({ ...proof, reconciliation: null, historical_runs: 3 }, now)).toMatchObject({ status: "UNKNOWN", level: "none" });
    expect(reconciliationTruth({ ...proof, freshness: "stale" }, now)).toMatchObject({ status: "STALE", level: "warn" });
    expect(reconciliationTruth(proof, now)).toMatchObject({ status: "MATCHED", level: "ok" });
    expect(reconciliationTruth({ ...proof, reconciliation: false }, now)).toMatchObject({ status: "MISMATCH", level: "bad" });
  });
});
it("distinguishes pending, failed/unavailable, empty and populated owner-action counts", () => {
  expect(needsYouCount(0, false, false)).toBe("0");
  expect(needsYouCount(0, false, true)).toBe("UNKNOWN");
  expect(needsYouCount(0, true, false)).toBe("…");
  expect(needsYouCount(3, false, false)).toBe("3");
  expect(needsYouCount(3, false, true)).toBe("UNKNOWN");
});
it("separates last recorded collector error from report freshness", () => {
  expect(collectorTruth({ status: "ok", freshness: "stale" })).toMatchObject({ status: "OK", freshness: "STALE", level: "warn" });
  expect(collectorTruth({ status: "error", freshness: "stale" })).toMatchObject({ status: "ERROR", freshness: "STALE", level: "bad" });
  expect(collectorTruth({ status: "ok", freshness: "fresh" }).level).toBe("ok");
  expect(collectorTruth(null).level).toBe("none");
});
it("keeps all filesystem free-space summaries and missing baseline explicit", () => {
  render(<StorageTruth storage={{ filesystems: [{ label: "Root /", total: 49e9, free: 13e9, percent_free: 27 }] }} />);
  const summary = screen.getByLabelText("Filesystem free-space summary");
  expect(summary).toHaveTextContent("Free: 13.0 GB / 49.0 GB");
  expect(summary).toHaveTextContent("27.0% free");
  expect(summary).toHaveTextContent("LUFFY data");
  expect(summary).toHaveTextContent("Recovery");
  expect(summary).toHaveTextContent("UNKNOWN");
  expect(screen.getByTestId("growth-baseline")).toHaveTextContent("Growth baseline unavailable");
});
