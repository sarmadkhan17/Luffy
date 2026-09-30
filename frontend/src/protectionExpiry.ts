import type { OverviewData } from "./adapters/contracts";

/** Re-evaluated on a local clock tick, independent of polling. Once the
 * snapshot's own expiry passes, every protection claim it backs is STALE —
 * even if every later poll failed and the query still holds the old data. */
export function applyProtectionExpiry(
  d: OverviewData,
  nowMs: number,
): OverviewData {
  const p = d.live?.protection;
  if (!p || p.status === "UNAVAILABLE" || p.status === "STALE") return d;
  if (p.expiresAt !== null && nowMs <= p.expiresAt) return d;
  const why = "verification_stale_locally";
  return {
    ...d,
    positions:
      d.positions?.map((pos) =>
        pos.protection === "UNAVAILABLE" || pos.protection === "STALE"
          ? pos
          : {
              ...pos,
              protection: "STALE",
              protectionDetail: pos.protectionDetail && {
                ...pos.protectionDetail,
                status: "STALE",
                lastReported: pos.protectionDetail.status,
                reasons: [...pos.protectionDetail.reasons, why],
              },
            },
      ) ?? null,
    live: {
      ...d.live!,
      protection: {
        ...p,
        status: "STALE",
        reasons: [...p.reasons, why],
        evidence: p.evidence && { ...p.evidence, freshness: "stale" },
      },
    },
  };
}
