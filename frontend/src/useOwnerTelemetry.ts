import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { usePreview } from "./context";
import type { GraphData } from "./adapters/contracts";
/** One shared health query. The architecture node never implies IPC health. */
export function useOwnerTelemetry(
  data: GraphData,
  enabled: boolean,
): GraphData {
  const { adapter } = usePreview();
  const health = useQuery({
    queryKey: ["owner-interface"],
    queryFn: ({ signal }) => adapter.ownerInterface!(signal),
    enabled: enabled && !!adapter.ownerInterface,
    staleTime: 0,
    refetchInterval: 30000,
    refetchIntervalInBackground: false,
  });
  return useMemo(() => {
    if (!enabled || adapter.mode !== "LIVE") return data;
    const h = health.error ? undefined : health.data;
    const age = h?.observedAt ? Date.now() - Date.parse(h.observedAt) : NaN;
    const stale = Number.isFinite(age) && (age < 0 || age > 60000);
    return {
      ...data,
      nodes: data.nodes.map((n) =>
        n.id !== "owner-interface"
          ? n
          : {
              ...n,
              hasTelemetry: true,
              statusLabel:
                h?.availability ??
                (health.isPending ? "CHECKING" : "UNAVAILABLE"),
              health: h?.availability === "AVAILABLE" ? "active" : "unknown",
              evidence: {
                id: "owner-interface:health",
                source: "kernel Owner Interface health read (IPC)",
                observedAt: h?.observedAt ?? null,
                freshness: stale
                  ? "stale"
                  : Number.isFinite(age)
                    ? "fresh"
                    : "unavailable",
                classification: "observed health response",
                summary: health.error
                  ? `Health read failed: ${health.error.message}`
                  : h
                    ? `${h.availability}; control ${h.controlState ?? "unknown"}; recovery ${h.recoveryInProgress === null ? "unknown" : h.recoveryInProgress ? "in progress" : "not in progress"}. ${h.reasons.join(", ")}. Polled every 30s; displayed stale after 60s.`
                    : "Health read pending; topology alone provides no availability evidence.",
              },
            },
      ),
    };
  }, [
    data,
    enabled,
    adapter.mode,
    health.data,
    health.error,
    health.isPending,
    health.dataUpdatedAt,
  ]);
}
