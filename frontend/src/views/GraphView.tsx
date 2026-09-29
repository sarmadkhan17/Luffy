import { timestamp } from "../time";
import { useOwnerTelemetry } from "../useOwnerTelemetry";
import PathTrace from "../components/PathTrace";
import { NoteDetail } from "./LiveReads";
import { useRouteParam } from "../links";
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  BaseEdge,
  useReactFlow,
  useNodesInitialized,
  type EdgeProps,
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
function ViewportTools({ scopeKey }: { scopeKey: string }) {
  const flow = useReactFlow();
  const initialized = useNodesInitialized();
  useEffect(() => {
    if (initialized)
      void flow.fitView({
        padding: 0.22,
        minZoom: 0.05,
        maxZoom: 1,
        duration: 0,
      });
  }, [scopeKey, initialized, flow]);
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
      <button
        onClick={() =>
          void flow.fitView({
            padding: 0.22,
            minZoom: 0.05,
            maxZoom: 1,
            duration: 0,
          })
        }
      >
        Fit View
      </button>
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
  const [list, setList] = useState(
    () => window.matchMedia("(max-width: 759px)").matches,
  );
  const [eventId, setEventId] = useState<string | null>(null);
  const reduced = useMotionPreference();
  const visible = useVisible();
  useEffect(() => {
    setEventId(null);
    setSelected(null);
    setNeighbours(false);
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
  const scope = useMemo(
    () => graphScope(d, lens, search, selected, neighbours, system),
    [d, lens, search, selected, neighbours, system],
  );
  const node = d.nodes.find((n) => n.id === selected);
  const event = d.events.find((e) => e.id === eventId);
  const positions = useMemo(
    () => (system ? systemPositions(d.nodes) : gridPositions(scope.nodes)),
    [d.nodes, scope.nodes, system],
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
      scope.nodes.map((n) => ({
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
              <span className="node-kind">{system ? "COMPONENT" : n.kind}</span>
              <strong>{n.label}</strong>
              {system ? (
                <HealthStatus node={n} />
              ) : (
                <span>
                  {n.evidence.timeBasis === "file_modified"
                    ? "Modified time · unassessed"
                    : n.evidence.freshness === "fresh"
                      ? live
                        ? n.kind
                        : "Fixture snapshot"
                      : n.evidence.freshness}
                </span>
              )}
            </div>
          ),
        },
        style: { width: NODE_WIDTH, height: NODE_HEIGHT },
        className: system
          ? `system-node status-${healthPresentation(n).tone}`
          : `knowledge-node kind-${n.kind.toLowerCase()}`,
      })),
    [scope.nodes, positions, selected, system],
  );
  const edges: Edge[] = useMemo(
    () =>
      scope.edges
        .filter((e) => !system || paths.get(e.id))
        .map((e) => ({
          id: e.id,
          source: e.source,
          target: e.target,
          label: system || e.kind === "link" ? undefined : e.relation,
          type: system ? "routed" : "default",
          data: { path: paths.get(e.id) },
          animated:
            visible &&
            !reduced &&
            d.provenance.freshness === "fresh" &&
            event?.edgeId === e.id,
          style: {
            stroke:
              e.kind === "typed"
                ? "#77cbbb"
                : e.kind === "link"
                  ? "#d9b779"
                  : "#9aafc1",
            strokeDasharray: e.kind === "typed" ? undefined : "6 5",
            strokeWidth: event?.edgeId === e.id ? 3 : 1.8,
          },
          labelStyle: { fill: "#c4d5e3", fontSize: 11 },
          labelBgStyle: { fill: "#101b27" },
          markerEnd:
            e.kind !== "link"
              ? {
                  type: MarkerType.ArrowClosed,
                  color: system ? "#c4d5e3" : "#77cbbb",
                  width: 18,
                  height: 18,
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
          <div className="graph-layout">
            <section
              className="panel graph-panel"
              aria-label={system ? "System graph" : "Knowledge graph"}
            >
              <div className="graph-legend">
                {system ? (
                  <>
                    <span className="legend-dash" /> Directed architectural
                    connection → · no throughput implied
                  </>
                ) : (
                  <>
                    <span className="legend-line" /> Typed relation →{" "}
                    <span className="legend-dash gold" /> Ordinary reference
                    link
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
                          <Background color="#29404e" gap={24} />
                        </ReactFlow>
                      </div>
                      <ViewportTools
                        scopeKey={scope.nodes.map((n) => n.id).join("|")}
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
                  <EvidenceDetails value={node.evidence} />
                  {!system && (
                    <PathTrace key={node.id} data={d} source={node.id} />
                  )}
                  {!system && adapter.ownerRead && (
                    <NoteDetail key={`note:${node.id}`} id={node.id} />
                  )}
                  <h3 className="space-top">Connections</h3>
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
