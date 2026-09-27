/** Frontend seams only. These are not claims about existing server contracts. */
export type Scenario = "normal" | "stale" | "missing" | "error";
export type Freshness = "fresh" | "stale" | "unavailable";
export type Lens = "Knowledge" | "Evidence" | "Timeline" | "Code";
export interface Provenance {
  id: string;
  source: string;
  observedAt: string | null;
  freshness: Freshness;
  summary: string;
  classification: string;
}
export interface Position {
  symbol: string;
  side: string;
  notional: number;
  pnl: number;
  protection: string;
}
export interface OverviewData {
  provenance: Provenance;
  equity: number | null;
  pnl: number | null;
  exposure: number | null;
  control: string;
  positions: Position[] | null;
  points: { time: string; value: number }[];
  needsYou: string | null;
}
export interface GraphNode {
  id: string;
  label: string;
  kind: string;
  lenses: Lens[];
  x: number;
  y: number;
  evidence: Provenance;
  health?: "active" | "idle" | "degraded" | "failing" | "unknown";
}
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  relation: string;
  kind: "typed" | "link" | "architecture";
  evidenceId?: string;
}
export interface ActivityEvent {
  id: string;
  edgeId: string;
  occurredAt: string;
  description: string;
}
export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
  events: ActivityEvent[];
  provenance: Provenance;
}
export interface Reply {
  text: string;
  evidence: Provenance[];
}
export interface OwnerAdapter {
  readonly mode: "DEMO";
  overview(scenario: Scenario, signal: AbortSignal): Promise<OverviewData>;
  graph(
    surface: "knowledge" | "system",
    scenario: Scenario,
    signal: AbortSignal,
  ): Promise<GraphData>;
  chat(
    text: string,
    scenario: Scenario,
    signal: AbortSignal,
    onChunk: (text: string) => void,
  ): Promise<Reply>;
}
