import type { GraphData, Lens } from "../adapters/contracts";
export const MAX_GRAPH_NODES = 40;
export function graphScope(
  data: GraphData,
  lens: Lens,
  search: string,
  selected: string | null,
  neighbours: boolean,
  system: boolean,
) {
  const adjacent = new Set(selected ? [selected] : []);
  if (neighbours && selected)
    for (const e of data.edges) {
      if (e.source === selected) adjacent.add(e.target);
      if (e.target === selected) adjacent.add(e.source);
    }
  const matches = data.nodes.filter(
    (n) =>
      (system || n.lenses.includes(lens)) &&
      `${n.label} ${n.kind}`.toLowerCase().includes(search.toLowerCase()) &&
      (!neighbours || adjacent.has(n.id)),
  );
  const nodes = matches.slice(0, MAX_GRAPH_NODES);
  const ids = new Set(nodes.map((n) => n.id));
  return {
    nodes,
    edges: data.edges.filter((e) => ids.has(e.source) && ids.has(e.target)),
    total: matches.length,
  };
}
