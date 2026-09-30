import type { GraphNode, GraphEdge } from "../adapters/contracts";
export const NODE_WIDTH = 210,
  NODE_HEIGHT = 112;
export type Point = { x: number; y: number };
export function systemPositions(nodes: GraphNode[]) {
  const fixed: Record<string, Point> = {
    s0: { x: 0, y: 0 },
    s1: { x: 320, y: 0 },
    s2: { x: 640, y: 0 },
    s3: { x: 960, y: 0 },
    s5: { x: 0, y: 250 },
    s6: { x: 320, y: 250 },
    s7: { x: 640, y: 250 },
    s8: { x: 640, y: 500 },
    s4: { x: 0, y: 500 },
  };
  return new Map(
    nodes.map((n, i) => [
      n.id,
      fixed[n.id] ??
        n.layout ?? { x: (i % 4) * 320, y: (3 + Math.floor(i / 4)) * 250 },
    ]),
  );
}
/** Orthogonal routing with reserved parallel tracks and unrelated-port exclusion
 * zones. Crossings are permitted, shared/near-parallel unrelated runs are not.
 * Distances are flow units (zoom scales the entire diagram). */
export const EDGE_CLEARANCE = 36;
export const PORT_CLEARANCE = 48;
export function routeEdges(
  nodes: GraphNode[],
  edges: GraphEdge[],
  positions: Map<string, Point>,
) {
  const reserved: { edge: GraphEdge; a: Point; b: Point }[] = [];
  const boxes = nodes.map((n) => ({ ...positions.get(n.id)!, id: n.id }));
  const intersects = (a: Point, b: Point) =>
    boxes.some((r) =>
      a.x === b.x
        ? a.x > r.x - 16 &&
          a.x < r.x + NODE_WIDTH + 16 &&
          Math.max(a.y, b.y) > r.y - 16 &&
          Math.min(a.y, b.y) < r.y + NODE_HEIGHT + 16
        : a.y > r.y - 16 &&
          a.y < r.y + NODE_HEIGHT + 16 &&
          Math.max(a.x, b.x) > r.x - 16 &&
          Math.min(a.x, b.x) < r.x + NODE_WIDTH + 16,
    );
  return new Map(
    edges.map((edge) => {
      const blocked = (a: Point, b: Point) => {
        // Guard every unrelated input/output port, including the final source
        // and target stubs. A route may not visually terminate near another port.
        const nearPort = boxes.some((r) => {
          if (r.id === edge.source || r.id === edge.target) return false;
          return [r.x, r.x + NODE_WIDTH].some((x) => {
            const y = r.y + NODE_HEIGHT / 2;
            return (
              Math.max(a.x, b.x) > x - PORT_CLEARANCE &&
              Math.min(a.x, b.x) < x + PORT_CLEARANCE &&
              Math.max(a.y, b.y) > y - PORT_CLEARANCE &&
              Math.min(a.y, b.y) < y + PORT_CLEARANCE
            );
          });
        });
        return (
          nearPort ||
          reserved.some((r) => {
            // Only fan-in/fan-out immediately at the SAME port may converge.
            // Sharing a component elsewhere does not license shared tracks.
            const commonPorts: Point[] = [];
            if (edge.source === r.edge.source) {
              const p = positions.get(edge.source)!;
              commonPorts.push({
                x: p.x + NODE_WIDTH,
                y: p.y + NODE_HEIGHT / 2,
              });
            }
            if (edge.target === r.edge.target) {
              const p = positions.get(edge.target)!;
              commonPorts.push({ x: p.x, y: p.y + NODE_HEIGHT / 2 });
            }
            for (const axis of ["x", "y"] as const) {
              const other = axis === "x" ? "y" : "x";
              if (
                a[axis] !== b[axis] ||
                r.a[axis] !== r.b[axis] ||
                Math.abs(a[axis] - r.a[axis]) >= EDGE_CLEARANCE
              )
                continue;
              const lo = Math.max(
                Math.min(a[other], b[other]),
                Math.min(r.a[other], r.b[other]),
              );
              const hi = Math.min(
                Math.max(a[other], b[other]),
                Math.max(r.a[other], r.b[other]),
              );
              if (hi <= lo) continue;
              const localConvergence = commonPorts.some(
                (p) =>
                  Math.abs(a[axis] - p[axis]) <= 72 &&
                  Math.abs(r.a[axis] - p[axis]) <= 72 &&
                  lo >= p[other] - 72 &&
                  hi <= p[other] + 72,
              );
              if (!localConvergence) return true;
            }
            return false;
          })
        );
      };
      const save = (points: Point[]) => {
        for (let i = 1; i < points.length; i++)
          reserved.push({ edge, a: points[i - 1], b: points[i] });
        return [
          edge.id,
          points.map((p, i) => `${i ? "L" : "M"} ${p.x} ${p.y}`).join(" "),
        ] as const;
      };
      const s = positions.get(edge.source)!,
        t = positions.get(edge.target)!;
      if (!s || !t) return [edge.id, null] as const;
      const portS = { x: s.x + NODE_WIDTH, y: s.y + NODE_HEIGHT / 2 },
        portT = { x: t.x, y: t.y + NODE_HEIGHT / 2 };
      if (
        portS.y === portT.y &&
        portT.x > portS.x &&
        !blocked(portS, portT) &&
        !boxes.some(
          (r) =>
            r.id !== edge.source &&
            r.id !== edge.target &&
            portS.y > r.y &&
            portS.y < r.y + NODE_HEIGHT &&
            r.x < portT.x &&
            r.x + NODE_WIDTH > portS.x,
        )
      )
        return save([portS, portT]);
      const gap = EDGE_CLEARANCE;
      const start = { x: portS.x + gap, y: portS.y },
        end = { x: portT.x - gap, y: portT.y };
      if (blocked(portS, start) || blocked(end, portT))
        return [edge.id, null] as const;
      const xs = [
        ...new Set([
          start.x,
          end.x,
          ...boxes.flatMap((b) =>
            [1, 2].flatMap((lane) => [
              b.x - gap * lane,
              b.x + NODE_WIDTH + gap * lane,
            ]),
          ),
        ]),
      ].sort((a, b) => a - b);
      const ys = [
        ...new Set([
          start.y,
          end.y,
          ...boxes.flatMap((b) =>
            [1, 2].flatMap((lane) => [
              b.y - gap * lane,
              b.y + NODE_HEIGHT + gap * lane,
            ]),
          ),
        ]),
      ].sort((a, b) => a - b);
      const width = xs.length,
        key = (x: number, y: number) => y * width + x;
      const source = key(xs.indexOf(start.x), ys.indexOf(start.y)),
        target = key(xs.indexOf(end.x), ys.indexOf(end.y));
      const points = (k: number) => ({
        x: xs[k % width],
        y: ys[Math.floor(k / width)],
      });
      const cost = new Map<number, number>([[source, 0]]),
        previous = new Map<number, number>();
      const open = [source];
      const done = new Set<number>();
      while (open.length) {
        let best = 0;
        for (let i = 1; i < open.length; i++) {
          const a = points(open[i]),
            b = points(open[best]);
          if (
            cost.get(open[i])! + Math.abs(a.x - end.x) + Math.abs(a.y - end.y) <
            cost.get(open[best])! +
              Math.abs(b.x - end.x) +
              Math.abs(b.y - end.y)
          )
            best = i;
        }
        const current = open.splice(best, 1)[0];
        if (current === target) break;
        if (done.has(current)) continue;
        done.add(current);
        const x = current % width,
          y = Math.floor(current / width),
          a = points(current);
        for (const [nx, ny] of [
          [x - 1, y],
          [x + 1, y],
          [x, y - 1],
          [x, y + 1],
        ]) {
          if (nx < 0 || ny < 0 || nx >= xs.length || ny >= ys.length) continue;
          const next = key(nx, ny),
            b = points(next);
          if (done.has(next) || intersects(a, b) || blocked(a, b)) continue;
          const score =
            cost.get(current)! + Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
          if (score < (cost.get(next) ?? Infinity)) {
            cost.set(next, score);
            previous.set(next, current);
            open.push(next);
          }
        }
      }
      if (!cost.has(target)) return [edge.id, null] as const;
      const path = [end];
      let cursor = target;
      while (cursor !== source) {
        cursor = previous.get(cursor)!;
        path.unshift(points(cursor));
      }
      const all = [portS, ...path, portT];
      const compact = all.filter(
        (p, i) =>
          !i ||
          i === all.length - 1 ||
          !(
            (all[i - 1].x === p.x && p.x === all[i + 1].x) ||
            (all[i - 1].y === p.y && p.y === all[i + 1].y)
          ),
      );
      return save(compact);
    }),
  );
}

/** Layout for knowledge records the backend supplies without coordinates:
 * a deterministic grid over the current scope (order = backend order). */
export function gridPositions(nodes: GraphNode[], columns = 6) {
  return new Map(
    nodes.map((n, i) => [
      n.id,
      n.x !== undefined && n.y !== undefined
        ? { x: n.x, y: n.y }
        : { x: (i % columns) * 300, y: Math.floor(i / columns) * 190 },
    ]),
  );
}

/** Knowledge constellation: one cluster per recorded node kind (the kind is
 * the note's own field, never inferred). Within a cluster the best-connected
 * node sits at the hub and the rest fill concentric rings; clusters sit on a
 * ring around the centre. Positions are node top-left corners. */
export const KNODE_W = 168,
  KNODE_H = 44;
export type Cluster = {
  kind: string;
  x: number;
  y: number;
  rx: number;
  ry: number;
  n: number;
};
export function clusterLayout(nodes: GraphNode[], edges: GraphEdge[]) {
  const degree = new Map<string, number>();
  for (const e of edges) {
    degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
    degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
  }
  const groups = new Map<string, GraphNode[]>();
  for (const n of nodes) {
    const g = groups.get(n.kind) ?? [];
    g.push(n);
    groups.set(n.kind, g);
  }
  const RING = 96;
  const local = [...groups.entries()]
    .sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]))
    .map(([kind, members]) => {
      const sorted = [...members].sort(
        (a, b) =>
          (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0) ||
          a.label.localeCompare(b.label),
      );
      const pts: Point[] = [{ x: 0, y: 0 }];
      let ring = 1,
        placed = 1;
      while (placed < sorted.length) {
        const r = ring * RING;
        const cap = Math.max(4, Math.floor((2 * Math.PI * r) / (KNODE_W + 14)));
        const take = Math.min(cap, sorted.length - placed);
        for (let i = 0; i < take; i++) {
          const a = -Math.PI / 2 + (i / take) * 2 * Math.PI + ring * 0.35;
          pts.push({ x: r * Math.cos(a) * 1.25, y: r * Math.sin(a) });
        }
        placed += take;
        ring += 1;
      }
      const radius = (ring - 1) * RING * 1.25 + KNODE_W * 0.62;
      const ry = (ring - 1) * RING + KNODE_H * 1.4;
      return { kind, members: sorted, pts, radius, ry };
    });
  const clusters: Cluster[] = [];
  const positions = new Map<string, Point>();
  if (!local.length) return { positions, clusters };
  // clusters around a centre, arc length proportional to their diameter
  const total = local.reduce((s, c) => s + 2 * c.radius + 50, 0);
  const R =
    local.length === 1 ? 0 : Math.max(total / (2 * Math.PI), local[0].radius + 80);
  let acc = 0;
  for (const c of local) {
    const share = (2 * c.radius + 50) / total;
    const a = -Math.PI / 2 + (acc + share / 2) * 2 * Math.PI;
    acc += share;
    const cx = R * Math.cos(a) * 1.3,
      cy = R * Math.sin(a);
    clusters.push({
      kind: c.kind,
      x: cx,
      y: cy,
      rx: c.radius,
      ry: c.ry,
      n: c.members.length,
    });
    c.members.forEach((m, i) =>
      positions.set(m.id, {
        x: cx + c.pts[i].x - KNODE_W / 2,
        y: cy + c.pts[i].y - KNODE_H / 2,
      }),
    );
  }
  return { positions, clusters };
}
