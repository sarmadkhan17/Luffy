import type { GraphNode } from "../adapters/contracts";
export function healthPresentation(node: GraphNode) {
  if (node.hasTelemetry === false)
    return { primary: "NO TELEMETRY", tone: "unknown", previous: null };
  if (node.statusLabel === "CHECKING")
    return { primary: "CHECKING", tone: "unknown", previous: null };
  if (node.evidence.freshness !== "stale" &&
      (node.evidence.freshness === "unavailable" || !node.evidence.observedAt))
    return { primary: "UNAVAILABLE", tone: "unknown", previous: null };
  if (node.evidence.freshness === "stale" ||
      (node.evidence.expiresAt !== undefined &&
       (node.evidence.expiresAt === null || Date.now() > node.evidence.expiresAt)))
    return {
      primary: "STALE",
      tone: "stale",
      previous: node.statusLabel ?? node.health?.toUpperCase() ?? "UNKNOWN",
    };
  if (node.statusLabel === "STOPPED")
    return { primary: "STOPPED", tone: "unknown", previous: null };
  const health = node.health ?? "unknown";
  return {
    primary: node.statusLabel ?? health.toUpperCase(),
    tone: node.statusLabel === "UNKNOWN" ? "unknown" :
      node.statusLabel === "WAITING" || node.statusLabel === "DISABLED" ? "idle" :
      node.statusLabel === "FAILED" ? "failing" : health,
    previous: null,
  };
}
export default function HealthStatus({ node }: { node: GraphNode }) {
  const s = healthPresentation(node);
  return (
    <span className={`health-status health-${s.tone}`}>
      <strong>{s.primary}</strong>
      {s.previous && <span>Last reported: {s.previous}</span>}
    </span>
  );
}
