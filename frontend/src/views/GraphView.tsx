import { timestamp } from "../time";
import { useOwnerTelemetry } from "../useOwnerTelemetry";
import PathTrace from "../components/PathTrace";
import { NoteDetail } from "./LiveReads";
import { useRouteParam } from "../links";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BaseEdge,
  Handle,
  ViewportPortal,
  useReactFlow,
  useNodesInitialized,
  type EdgeProps,
  type NodeProps,
  Position,
  MarkerType,
  type Node,
  type Edge,
} from "@xyflow/react";

import { Search, Network, List, Play, ArrowUpRight } from "lucide-react";
import "@xyflow/react/dist/style.css";
import { usePreview } from "../context";
import {
  Badge,
  EvidenceDetails,
  Freshness,
  QueryState,
  useVisible,
  useMotionPreference,
  VisualBoundary,
} from "../components/ui";
import type { Lens, GraphData } from "../adapters/contracts";
import HealthStatus, { healthPresentation } from "../components/HealthStatus";
import {
  systemPositions,
  gridPositions,
  routeEdges,
  clusterLayout,
  KNODE_W,
  KNODE_H,
  NODE_WIDTH,
  NODE_HEIGHT,
} from "./graphGeometry";
import { graphScope, MAX_GRAPH_NODES } from "./graphScope";
function RoutedEdge(props: EdgeProps) {
  const path = props.data?.path;
  return typeof path === "string" ? (
    <BaseEdge
      id={props.id}
      path={path}
      markerEnd={props.markerEnd}
      style={props.style}
    />
  ) : null;
}
const edgeTypes = { routed: RoutedEdge };
/** A knowledge note in the constellation: kind mark, title and one line of
 * recorded metadata. Handles sit at the centre so relations meet the mark. */
function KnowledgeNode({ data }: NodeProps) {
  const d = data as { label: string; kind: string; meta: string };
  return (
    <div className="kn">
      <Handle
        type="target"
        position={Position.Left}
        className="kn-handle"
        isConnectable={false}
      />
      <span className="kn-dot" aria-hidden="true" />
      <span className="kn-text">
        <strong>{d.label}</strong>
        <small>
          {d.kind} · {d.meta}
        </small>
      </span>
      <Handle
        type="source"
        position={Position.Right}
        className="kn-handle"
        isConnectable={false}
      />
    </div>
  );
}
const nodeTypes = { kn: KnowledgeNode };
function ViewportTools({
  scopeKey,
  padding,
  bounds,
}: {
  scopeKey: string;
  padding: number;
  /** Fit these flow-space bounds (the constellation's cluster rings). */
  bounds?: { x: number; y: number; width: number; height: number } | null;
}) {
  const flow = useReactFlow();
  const initialized = useNodesInitialized();
  const fit = useCallback(() => {
    if (bounds) void flow.fitBounds(bounds, { padding, duration: 0 });
    else
      void flow.fitView({
        padding,
        minZoom: 0.05,
        maxZoom: 1,
        duration: 0,
      });
  }, [flow, padding, bounds]);
  useEffect(() => {
    if (!initialized) return;
    fit();
    // the renderer's own initial fit and pane measurement settle a frame
    // later; fit the constellation bounds again once they have
    const id = requestAnimationFrame(() => fit());
    return () => cancelAnimationFrame(id);
  }, [scopeKey, initialized, fit]);
  return (
    <div className="graph-controls" role="group" aria-label="Graph viewport">
      <button
        aria-label="Zoom In"
        onClick={() => void flow.zoomIn({ duration: 0 })}
      >
        +
      </button>
      <button
        aria-label="Zoom Out"
        onClick={() => void flow.zoomOut({ duration: 0 })}
      >
        −
      </button>
      <button onClick={fit}>Fit View</button>
      <span>Fit shows the scope; zoom or use the list to read details.</span>
    </div>
  );
}
const empty: GraphData = {
  nodes: [],
  edges: [],
  events: [],
  provenance: {
    id: "unavailable",
    source: "Unavailable",
    observedAt: null,
    freshness: "unavailable",
    summary: "",
    classification: "UNAVAILABLE",
  },
};
export default function GraphView({
  surface,
}: {
  surface: "knowledge" | "system";
}) {
  const system = surface === "system";
  const { adapter, scenario } = usePreview();
  const live = import.meta.env.MODE === "production" || adapter.mode === "LIVE";
  const q = useQuery({
    queryKey: ["graph", surface, scenario],
    queryFn: ({ signal }) => adapter.graph(surface, scenario, signal),
  });
  const d = useOwnerTelemetry(q.data ?? empty, system);
  const [lens, setLens] = useState<Lens>("Knowledge");
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [neighbours, setNeighbours] = useState(false);
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(new Set());
  const [list, setList] = useState(
    () => window.matchMedia("(max-width: 759px)").matches,
  );
  const [eventId, setEventId] = useState<string | null>(null);
  const [pathEdges, setPathEdges] = useState<string[]>([]);
  const reduced = useMotionPreference();
  const visible = useVisible();
  useEffect(() => {
    setEventId(null);
    setSelected(null);
    setNeighbours(false);
    setHiddenKinds(new Set());
  }, [scenario, surface]);
  useEffect(() => {
    if (!visible) setEventId(null);
  }, [visible]);
  // #knowledge?note=<id>: select the linked note once the graph holds it
  const linkedNote = useRouteParam("note");
  const linkedInGraph =
    !system && !!linkedNote && d.nodes.some((n) => n.id === linkedNote);
  useEffect(() => {
    if (linkedInGraph) {
      setNeighbours(false);
      setSelected(linkedNote);
    }
  }, [linkedInGraph, linkedNote]);
  useEffect(() => {
    if (!eventId) return;
    const timer = setTimeout(() => setEventId(null), 2000);
    return () => clearTimeout(timer);
  }, [eventId]);
  // Kind filter: exact recorded kinds only; a hidden kind is removed from
  // the scope, never relabelled.
  const kindCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const n of d.nodes)
      if (system || n.lenses.includes(lens))
        m.set(n.kind, (m.get(n.kind) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [d.nodes, lens, system]);
  const filtered = useMemo(
    () =>
      system || !hiddenKinds.size
        ? d
        : { ...d, nodes: d.nodes.filter((n) => !hiddenKinds.has(n.kind)) },
    [d, hiddenKinds, system],
  );
  const scope = useMemo(
    () => graphScope(filtered, lens, search, selected, neighbours, system),
    [filtered, lens, search, selected, neighbours, system],
  );
  // Selected-node focus: the node, its direct neighbours and their relations.
  const focus = useMemo(() => {
    if (system || !selected) return null;
    const ids = new Set([selected]);
    for (const e of d.edges) {
      if (e.source === selected) ids.add(e.target);
      if (e.target === selected) ids.add(e.source);
    }
    return ids;
  }, [d.edges, selected, system]);
  const node = d.nodes.find((n) => n.id === selected);
  const event = d.events.find((e) => e.id === eventId);
  const constellation = useMemo(
    () => (system ? null : clusterLayout(scope.nodes, scope.edges)),
    [scope.nodes, scope.edges, system],
  );
  const clusterBounds = useMemo(() => {
    const cs = constellation?.clusters;
    if (!cs?.length) return null;
    const x0 = Math.min(...cs.map((c) => c.x - c.rx));
    const y0 = Math.min(...cs.map((c) => c.y - c.ry - 30));
    const x1 = Math.max(...cs.map((c) => c.x + c.rx));
    const y1 = Math.max(...cs.map((c) => c.y + c.ry));
    // never zoom a small constellation past readable scale
    const w = Math.max(x1 - x0, 760),
      h = Math.max(y1 - y0, 480);
    return {
      x: (x0 + x1) / 2 - w / 2,
      y: (y0 + y1) / 2 - h / 2,
      width: w,
      height: h,
    };
  }, [constellation]);
  const positions = useMemo(
    () =>
      system
        ? systemPositions(d.nodes)
        : (constellation?.positions ?? gridPositions(scope.nodes)),
    [d.nodes, scope.nodes, system, constellation],
  );
  const paths = useMemo(
    () =>
      system
        ? routeEdges(scope.nodes, scope.edges, positions)
        : new Map<string, string | null>(),
    [scope.nodes, scope.edges, positions, system],
  );
  const nodes: Node[] = useMemo(
    () =>
      scope.nodes.map((n) => {
        const meta =
          n.evidence.timeBasis === "file_modified"
            ? "Modified time · unassessed"
            : n.evidence.timeBasis === "event"
              ? "Event chronology · historical"
            : n.evidence.freshness === "fresh"
              ? live
                ? "recorded"
                : "Fixture snapshot"
              : n.evidence.freshness;
        return system
          ? {
              id: n.id,
              position: positions.get(n.id)!,
              sourcePosition: Position.Right,
              targetPosition: Position.Left,
              selected: n.id === selected,
              ariaLabel: `Inspect ${n.label}`,
              ariaRole: "button",
              data: {
                label: (
                  <div className="graph-node-content">
                    <span className="node-kind">COMPONENT</span>
                    <strong>{n.label}</strong>
                    <HealthStatus node={n} />
                  </div>
                ),
              },
              style: { width: NODE_WIDTH, height: NODE_HEIGHT },
              className: `system-node status-${healthPresentation(n).tone}`,
            }
          : {
              id: n.id,
              type: "kn",
              position: positions.get(n.id)!,
              selected: n.id === selected,
              ariaLabel: `Inspect ${n.label}`,
              ariaRole: "button",
              data: { label: n.label, kind: n.kind, meta },
              style: { width: KNODE_W, height: KNODE_H },
              className: `knowledge-node kind-${n.kind.toLowerCase()}${
                focus ? (focus.has(n.id) ? " in-focus" : " out-focus") : ""
              }`,
            };
      }),
    [scope.nodes, positions, selected, system, focus, live],
  );
  const edges: Edge[] = useMemo(
    () =>
      scope.edges
        .filter((e) => !system || paths.get(e.id))
        .map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          label:
            system ||
            e.kind === "link" ||
            (focus && !(e.source === selected || e.target === selected))
              ? undefined
              : e.relation,
          type: system ? "routed" : "straight",
          className: pathEdges.includes(e.id)
            ? "path-selected"
            : focus
              ? e.source === selected || e.target === selected
                ? "in-focus"
                : "out-focus"
              : undefined,
          data: { path: paths.get(e.id) },
          animated:
            visible &&
            !reduced &&
            d.provenance.freshness === "fresh" &&
            event?.edgeId === e.id,
          style: {
            stroke:
              pathEdges.includes(e.id)
                ? "#e6c27e"
                : e.kind === "typed"
                ? "#77cbbb"
                : e.kind === "link"
                  ? "#d9b779"
                  : "#9aafc1",
            strokeDasharray: e.kind === "typed" ? undefined : "6 5",
            strokeWidth:
              pathEdges.includes(e.id)
                ? 3.5
                : event?.edgeId === e.id
                ? 3
                : !system && focus
                  ? e.source === selected || e.target === selected
                    ? 2.2
                    : 1
                  : system
                    ? 1.8
                    : 1.3,
          },
          labelStyle: { fill: "#c4d5e3", fontSize: 11 },
          labelBgStyle: { fill: "#101b27" },
          markerEnd:
            e.kind !== "link"
              ? {
                  type: MarkerType.ArrowClosed,
                  color: system ? "#c4d5e3" : "#77cbbb",
                  width: system ? 18 : 14,
                  height: system ? 18 : 14,
                }
              : undefined,
        })),
    [
      scope.edges,
      system,
      paths,
      visible,
      reduced,
      event,
      d.provenance.freshness,
      focus,
      selected,
      pathEdges,
    ],
  );
  /** Every connection touching the visible scope that is not drawn, with why.
   * Nothing is dropped silently. */
  const undrawn = useMemo(() => {
    if (list) return [];
    const inScope = new Set(scope.nodes.map((n) => n.id));
    const known = new Set(d.nodes.map((n) => n.id));
    const drawn = new Set(edges.map((e) => e.id));
    return d.edges
      .filter(
        (e) =>
          (inScope.has(e.source) || inScope.has(e.target)) && !drawn.has(e.id),
      )
      .map((e) => ({
        edge: e,
        reason:
          !known.has(e.source) || !known.has(e.target)
            ? "endpoint record not supplied"
            : !inScope.has(e.source) || !inScope.has(e.target)
              ? "endpoint outside the current scope"
              : "no clear route in this layout",
      }));
  }, [list, scope.nodes, d.nodes, d.edges, edges]);
  const label = (id: string) =>
    d.nodes.find((n) => n.id === id)?.label ?? id.replace(/^unresolved:/, "");
  function select(id: string) {
    setSelected(id);
  }
  function changeLens(value: Lens) {
    setLens(value);
    setList(
      value === "Timeline" || window.matchMedia("(max-width: 759px)").matches,
    );
    setSelected(null);
    setNeighbours(false);
    setSearch("");
  }
  return (
    <>
      <QueryState
        error={q.error}
        loading={q.isPending}
        retry={() => void q.refetch()}
      />
      {live && !system && linkedNote && q.data && !linkedInGraph && (
        <section
          className="panel linked-record"
          data-testid="linked-note"
          aria-label="Linked note"
        >
          <div className="panel-heading">
            <h2>Linked note · {linkedNote}</h2>
            <a className="text-link" href="#knowledge">
              Close
            </a>
          </div>
          <p className="quiet">
            Not among the notes loaded in this graph view; shown from the note
            read directly.
          </p>
          <NoteDetail key={linkedNote} id={linkedNote} />
        </section>
      )}
      {q.data && (
        <>
          <div className="graph-toolbar">
            {system ? (
              <div className="toolbar-label">
                <Network size={17} /> Component topology
              </div>
            ) : (
              <div className="lenses" role="group" aria-label="Knowledge lens">
                {(["Knowledge", "Evidence", "Timeline", "Code"] as Lens[]).map(
                  (l) => {
                    const info = d.lenses?.[l];
                    const off = info?.available === false;
                    return (
                      <button
                        key={l}
                        aria-pressed={lens === l}
                        disabled={off}
                        title={info?.note}
                        aria-describedby={off ? `lens-note-${l}` : undefined}
                        onClick={() => changeLens(l)}
                      >
                        {l}
                        {off && (
                          <span className="sr-only" id={`lens-note-${l}`}>
                            {" "}
                            unavailable: {info?.note}
                          </span>
                        )}
                      </button>
                    );
                  },
                )}
              </div>
            )}
            <div className="graph-tools">
              <label className="search">
                <Search size={16} />
                <span className="sr-only">Search nodes</span>
                <input
                  value={search}
                  onChange={(e) => {
                    setSearch(e.target.value);
                    setNeighbours(false);
                  }}
                  placeholder="Search nodes…"
                />
              </label>
              <button aria-pressed={list} onClick={() => setList(!list)}>
                <List size={16} /> {list ? "Show graph" : "List view"}
              </button>
            </div>
          </div>
          <div className="snapshot-line">
            <Freshness value={d.provenance} />
            <span>
              {scope.nodes.length} of {scope.total} matching nodes · scope cap{" "}
              {MAX_GRAPH_NODES}
              {d.truncated && d.total !== undefined
                ? ` · backend returned ${d.nodes.length} of ${d.total}`
                : ""}
            </span>
          </div>
          {d.lenses &&
            Object.entries(d.lenses).some(([, v]) => v && !v.available) && (
              <p className="quiet lens-limits" data-testid="lens-limits">
                Limited:{" "}
                {Object.entries(d.lenses)
                  .filter(([, v]) => v && !v.available)
                  .map(([k, v]) => `${k} lens unavailable (${v!.note})`)
                  .join(" · ")}
                {d.lenses[lens]?.note
                  ? ` · ${lens}: ${d.lenses[lens]!.note}`
                  : ""}
              </p>
            )}
          {!system && kindCounts.length > 1 && (
            <div
              className="kind-filter"
              role="group"
              aria-label="Filter by recorded kind"
            >
              <span className="quiet">Clusters · recorded kind</span>
              {kindCounts.map(([k, n]) => (
                <button
                  key={k}
                  type="button"
                  aria-pressed={!hiddenKinds.has(k)}
                  className={`kind-chip kind-${k.toLowerCase()}`}
                  onClick={() => {
                    const next = new Set(hiddenKinds);
                    if (next.has(k)) next.delete(k);
                    else next.add(k);
                    setHiddenKinds(next);
                  }}
                >
                  <i aria-hidden="true" /> {k} · {n}
                </button>
              ))}
              {hiddenKinds.size > 0 && (
                <button
                  type="button"
                  className="kind-reset"
                  onClick={() => setHiddenKinds(new Set())}
                >
                  Show all kinds
                </button>
              )}
            </div>
          )}
          <div className="graph-layout">
            <section
              className="panel graph-panel"
              aria-label={system ? "System graph" : "Knowledge graph"}
            >
              <div className="graph-legend">
                {system ? (
                  <>
                    <span className="legend-dash" /> DECLARED architectural
                    connection → (repository map) · no throughput implied ·
                    node status is each component's own telemetry; observed
                    records are listed below
                  </>
                ) : (
                  <>
                    <span className="legend-line" /> Typed relation →{" "}
                    <span className="legend-dash gold" /> Ordinary reference
                    link <span className="legend-ring" /> Cluster = the note's
                    recorded kind · layout position carries no meaning
                  </>
                )}
              </div>
              {!scope.nodes.length ? (
                <div className="empty graph-canvas">
                  {d.nodes.length
                    ? "No matching nodes. Change search or lens."
                    : "Graph data unavailable. No nodes were substituted."}
                </div>
              ) : list ? (
                <div className="graph-list graph-canvas">
                  {scope.nodes.map((n) => (
                    <button
                      aria-pressed={selected === n.id}
                      key={n.id}
                      onClick={() => select(n.id)}
                    >
                      <strong>{n.label}</strong>
                      <span>
                        {system ? (
                          <HealthStatus node={n} />
                        ) : (
                          <>
                            {n.kind} ·{" "}
                            {n.evidence.timeBasis === "file_modified"
                              ? "Modified time"
                              : n.evidence.freshness}
                          </>
                        )}
                        {lens === "Timeline" && (
                          <time dateTime={n.evidence.observedAt ?? undefined}>
                            {" "}
                            · {n.evidence.timeBasis === "event" ? "Event chronology" : n.evidence.timeBasis === "file_modified" ? "File modification time — not event chronology" : "Event time NOT_RECORDED"}
                            · {timestamp(n.evidence.observedAt)}
                          </time>
                        )}
                      </span>
                    </button>
                  ))}
                </div>
              ) : (
                <VisualBoundary
                  fallback={
                    <div className="empty">
                      Graph renderer unavailable. Use List view to inspect
                      nodes.
                    </div>
                  }
                >
                  <div className="graph-canvas" data-testid="graph-canvas">
                    <ReactFlowProvider>
                      <div className="graph-renderer">
                        <ReactFlow
                          edgeTypes={edgeTypes}
                          nodeTypes={nodeTypes}
                          nodes={nodes}
                          edges={edges}
                          fitView
                          fitViewOptions={{
                            padding: 0.18,
                            minZoom: 0.05,
                            maxZoom: 1,
                          }}
                          minZoom={0.05}
                          maxZoom={2}
                          nodesDraggable={false}
                          nodesConnectable={false}
                          edgesReconnectable={false}
                          deleteKeyCode={null}
                          onNodeClick={(_, n) => select(n.id)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              const id = (e.target as HTMLElement)
                                .closest(".react-flow__node")
                                ?.getAttribute("data-id");
                              if (id) {
                                e.preventDefault();
                                select(id);
                              }
                            }
                          }}
                          colorMode="dark"
                        >
                          <Background
                            color={system ? "#29404e" : "#1a2733"}
                            gap={system ? 24 : 32}
                          />
                          {constellation && (
                            <ViewportPortal>
                              {constellation.clusters.map((c) => (
                                <div
                                  key={c.kind}
                                  className={`k-cluster kind-${c.kind.toLowerCase()}`}
                                  aria-hidden="true"
                                  style={{
                                    transform: `translate(${c.x - c.rx}px, ${c.y - c.ry}px)`,
                                    width: c.rx * 2,
                                    height: c.ry * 2,
                                  }}
                                >
                                  <span>
                                    {c.kind} · {c.n}
                                  </span>
                                </div>
                              ))}
                            </ViewportPortal>
                          )}
                        </ReactFlow>
                      </div>
                      <ViewportTools
                        scopeKey={scope.nodes.map((n) => n.id).join("|")}
                        padding={system ? 0.22 : 0.1}
                        bounds={clusterBounds}
                      />
                    </ReactFlowProvider>
                  </div>
                </VisualBoundary>
              )}
              {undrawn.length > 0 && (
                <details className="undrawn" data-testid="undrawn">
                  <summary>
                    {undrawn.length} connection{undrawn.length === 1 ? "" : "s"}{" "}
                    not drawn — see list
                  </summary>
                  <ul>
                    {undrawn.map(({ edge, reason }) => (
                      <li key={edge.id}>
                        {label(edge.source)} → {edge.relation} →{" "}
                        {label(edge.target)}{" "}
                        <span className="quiet">· {reason}</span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              <div className="graph-bottom">
                <span>
                  {list
                    ? "Select a record to inspect its source."
                    : "Drag to pan · Scroll to zoom · Enter to inspect"}
                </span>
                <span>Read only</span>
              </div>
              {!system && node && adapter.ownerRead && (
                <div className="note-record-panel">
                  <div className="note-record-head">
                    <span className="eyebrow">Stored record</span>
                    <strong>{node.label}</strong>
                    <span className="quiet mono">{node.id}</span>
                  </div>
                  <NoteDetail key={`note:${node.id}`} id={node.id} />
                </div>
              )}
            </section>
            <aside className="panel inspector" aria-label="Node inspector">
              <div className="panel-heading">
                <h2>{node ? "Selected record" : "Evidence inspector"}</h2>
                <Badge>{system ? "Component" : "Provenance"}</Badge>
              </div>
              {node ? (
                <>
                  <div className="selected-title">
                    <span className="eyebrow">{node.kind}</span>
                    <h2>{node.label}</h2>
                    {system && <HealthStatus node={node} />}
                  </div>

                  <button
                    className="wide"
                    aria-pressed={neighbours}
                    onClick={() => {
                      setSearch("");
                      setNeighbours(!neighbours);
                    }}
                  >
                    {neighbours ? "Show full lens" : "Explore neighbours"}
                  </button>
                  <h3 className="space-top">
                    Connections ·{" "}
                    {
                      d.edges.filter(
                        (e) => e.source === node.id || e.target === node.id,
                      ).length
                    }
                  </h3>
                  <ul className="connections">
                    {d.edges
                      .filter(
                        (e) => e.source === node.id || e.target === node.id,
                      )
                      .map((e) => {
                        const other = d.nodes.find(
                          (n) =>
                            n.id ===
                            (e.source === node.id ? e.target : e.source),
                        );
                        return (
                          <li key={e.id}>
                            <span>
                              {e.kind} · {e.relation}
                              <br />
                              {e.source === node.id
                                ? "Outgoing →"
                                : "Incoming ←"}
                            </span>
                            <button
                              disabled={!other}
                              onClick={() => {
                                if (!other) return;
                                setNeighbours(false);
                                setSearch("");
                                if (other && !other.lenses.includes(lens))
                                  setLens(other.lenses[0]);
                                select(other.id);
                              }}
                            >
                              {other?.label ??
                                `Unavailable reference: ${e.source === node.id ? e.target : e.source}`}{" "}
                              <ArrowUpRight size={13} />
                            </button>
                          </li>
                        );
                      })}
                  </ul>
                  <EvidenceDetails value={node.evidence} />
                  {!system && (
                    <PathTrace key={node.id} data={d} source={node.id} onPathChange={setPathEdges} />
                  )}
                </>
              ) : (
                <div className="inspector-empty">
                  <Network size={36} />
                  <h3>Select a node</h3>
                  <p>
                    Inspect source records, relationship types and evidence
                    limits.
                  </p>
                  <p>
                    Use the graph or the keyboard-accessible node list below.
                  </p>
                </div>
              )}
            </aside>
          </div>
          {system ? (
            <section className="panel event-panel">
              <div className="panel-heading">
                <h2>Supplied events</h2>
                <Badge>
                  {live ? "No event stream" : "Fixture replay only"}
                </Badge>
              </div>
              {d.events.length ? (
                d.events.map((e) => (
                  <div className="event-row" key={e.id}>
                    <div>
                      <strong>{e.description}</strong>
                      <p>
                        {e.id} · {e.occurredAt}
                      </p>
                    </div>
                    <button
                      disabled={!!eventId || !visible}
                      onClick={() => setEventId(e.id)}
                    >
                      <Play size={14} /> Replay fixture event
                    </button>
                  </div>
                ))
              ) : (
                <p>
                  {live
                    ? `${d.eventsNote ?? "No activity events supplied"}. Node status comes only from the telemetry each component actually reports.`
                    : "No fresh events supplied. Activity animation is unavailable."}
                </p>
              )}
              <p role="status">
                {event
                  ? `Replaying ${event.id}${reduced ? " · reduced motion: static highlight" : ""}`
                  : "No event replay running. Connections represent architecture only."}
              </p>
            </section>
          ) : (
            <div className="knowledge-footnote">
              <Badge tone="amber">context_only</Badge>
              <p>
                {live
                  ? "Relations are as written in vault notes (frontmatter relations and wikilinks). A connection is not causality, verified evidence or permission to trade."
                  : "Relations describe the supplied fixture records. A connection is not causality, verified evidence or permission to trade."}
              </p>
              {live && d.limits && d.limits.length > 0 && (
                <ul className="quiet">
                  {d.limits.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <details className="node-directory">
            <summary>
              Keyboard-accessible node directory ({scope.nodes.length})
            </summary>
            <div className="directory-items">
              {scope.nodes.map((n) => (
                <button key={n.id} onClick={() => select(n.id)}>
                  {n.label}
                </button>
              ))}
            </div>
          </details>
        </>
      )}
    </>
  );
}
