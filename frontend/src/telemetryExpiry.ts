import type { OverviewData, Provenance } from "./adapters/contracts";

/** Observation deadlines continue to age when cached queries stop updating. */
export function expireOverviewTelemetry(d: OverviewData, now: number, disconnected = false): OverviewData {
  const expire = (p: Provenance | null): Provenance | null => !p ? null : {
    ...p, freshness: disconnected ? "unavailable" : p.freshness === "fresh" &&
      (p.expiresAt == null || now > p.expiresAt) ? "stale" : p.freshness,
  };
  if (!d.live) return d;
  const heartbeat = expire(d.live.heartbeat);
  return { ...d, provenance: expire(d.provenance)!, live: {
    ...d.live, heartbeat, account: expire(d.live.account),
    kernelState: disconnected ? "UNKNOWN" : d.live.kernelState === "RUNNING" && heartbeat?.freshness !== "fresh"
      ? "STALE" : d.live.kernelState,
    needsYou: d.live.needsYou && { ...d.live.needsYou, provenance: expire(d.live.needsYou.provenance)! },
  } };
}
