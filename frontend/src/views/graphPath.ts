import type { GraphData, GraphEdge } from "../adapters/contracts";
/** Directed BFS over returned records only. Never crosses a missing reference. */
export function evidencePath(
  data: GraphData,
  source: string,
  target: string,
): GraphEdge[] | null {
  const ids = new Set(data.nodes.map((n) => n.id));
  if (!ids.has(source) || !ids.has(target)) return null;
  if (source === target) return [];
  const adjacency = new Map<string, GraphEdge[]>();
  for (const e of data.edges)
    if (ids.has(e.source) && ids.has(e.target))
      adjacency.set(e.source, [...(adjacency.get(e.source) ?? []), e]);
  const seen = new Set([source]),
    queue = [source],
    prior = new Map<string, GraphEdge>();
  for (let i = 0; i < queue.length; i++)
    for (const e of adjacency.get(queue[i]) ?? []) {
      if (seen.has(e.target)) continue;
      seen.add(e.target);
      prior.set(e.target, e);
      queue.push(e.target);
      if (e.target === target) {
        const out: GraphEdge[] = [];
        let at = target;
        while (at !== source) {
          const edge = prior.get(at)!;
          out.push(edge);
          at = edge.source;
        }
        return out.reverse();
      }
    }
  return null;
}
