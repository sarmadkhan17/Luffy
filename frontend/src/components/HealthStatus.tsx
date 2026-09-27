import type { GraphNode } from "../adapters/contracts";
export function healthPresentation(node: GraphNode) {
  if (node.evidence.freshness === "stale")
    return {
      primary: "STALE",
      tone: "stale",
      previous: node.health?.toUpperCase() ?? "UNKNOWN",
    };
  if (node.evidence.freshness === "unavailable" || !node.evidence.observedAt)
    return { primary: "UNAVAILABLE", tone: "unknown", previous: null };
  const health = node.health ?? "unknown";
  return { primary: health.toUpperCase(), tone: health, previous: null };
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
