/** Display-only classifications of existing Supervisor/collector evidence. */
export type DiagnosticLevel = "ok" | "warn" | "bad" | "none";
type Evidence = Record<string, any> | null | undefined;
export function supervisorFreshness(s: Evidence, now = Date.now()): string {
  if (!s) return "unknown";
  if (s.freshness !== "fresh") return s.freshness === "stale" ? "stale" : "unknown";
  const at = Date.parse(s.observed_at);
  if (!Number.isFinite(at) || typeof s.stale_after_s !== "number" || !Number.isFinite(s.stale_after_s) || s.stale_after_s < 0) return "unknown";
  {
    if (now < at) return "unknown";
    if (now - at > s.stale_after_s * 1000) return "stale";
  }
  return "fresh";
}
export function recoveryTruth(s: Evidence, now = Date.now()) {
  const freshness = supervisorFreshness(s, now);
  const recovering = Array.isArray(s?.reasons) && s.reasons.some((r: string) => /recovery/i.test(r));
  return { freshness, status: freshness === "stale" ? "STALE" : freshness !== "fresh" ? "NOT REPORTED" : recovering ? "ACTIVE" : "INACTIVE",
    level: (freshness === "unknown" ? "none" : freshness === "stale" || recovering ? "warn" : "ok") as DiagnosticLevel };
}
export function reconciliationTruth(s: Evidence, now = Date.now()) {
  const freshness = supervisorFreshness(s, now);
  const proof = freshness === "fresh" && s?.checks_are_current === true ? s.reconciliation : null;
  return { status: freshness === "stale" ? "STALE" : proof === true ? "MATCHED" : proof === false ? "MISMATCH" : "UNKNOWN",
    level: (freshness === "stale" ? "warn" : proof === true ? "ok" : proof === false ? "bad" : "none") as DiagnosticLevel };
}
export function collectorTruth(c: Evidence) {
  const status = c?.status === "ok" ? "OK" : c?.status === "error" ? "ERROR" : "UNKNOWN";
  const freshness = c?.freshness === "fresh" ? "FRESH" : c?.freshness === "stale" ? "STALE" : "UNKNOWN";
  return { status, freshness, level: (status === "ERROR" ? "bad" : status === "UNKNOWN" || freshness === "UNKNOWN" ? "none" : freshness === "STALE" ? "warn" : "ok") as DiagnosticLevel };
}
export function needsYouCount(count: number, pending: boolean, unavailable: boolean): string {
  return unavailable ? "UNKNOWN" : pending ? "…" : String(count);
}
