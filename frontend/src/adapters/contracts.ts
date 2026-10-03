/** Frontend seams. LIVE adapters map reviewed backend contracts
 * (/owner-api/v1, GraphQL owner mutations); the DEMO adapter is fixtures only. */
export type Mode = "LIVE" | "DEMO";
export type Scenario = "normal" | "stale" | "missing" | "error";
export type Freshness = "fresh" | "stale" | "unavailable" | "not_assessed";
export type Lens = "Knowledge" | "Evidence" | "Timeline" | "Code";
export interface Provenance {
  id: string;
  source: string;
  observedAt: string | null;
  freshness: Freshness;
  summary: string;
  classification: string;
  /** What observedAt means when it is not a live observation (e.g. file_modified). */
  timeBasis?: string;
}
export type ProtectionStatus =
  | "PARTIAL"
  | "VERIFIED"
  | "STALE"
  | "UNVERIFIED"
  | "UNPROTECTED"
  | "UNREADABLE"
  | "UNAVAILABLE"
  | "NO_POSITIONS";
export interface Protection {
  status: ProtectionStatus;
  observedAt: string | null;
  lastReported: string | null;
  reasons: string[];
}
export interface Position {
  id?: string;
  symbol: string;
  side: string;
  notional: number | null;
  pnl: number | null;
  protection: string;
  protectionDetail?: Protection;
  journalStop?: { price: number | null; orderRefRecorded: boolean };
  openedAt?: string | null;
}
export interface LiveOverview {
  generatedAt: string;
  control: {
    state: string;
    observedAt: string;
    lastEventAt: string | null;
  } | null;
  heartbeat: Provenance | null;
  account: Provenance | null;
  protection: {
    status: ProtectionStatus;
    /** The kernel's read-only protection snapshot (never a browser venue read). */
    evidence: Provenance | null;
    reasons: string[];
    /** Browser-clock ms after which this evidence is STALE, from the snapshot's
     * own checked_at + stale_after_s (server clock offset applied). Enforced
     * locally, so a failed poll can never keep old evidence current. */
    expiresAt: number | null;
  } | null;
  needsYou: {
    needsOwner: boolean;
    reasons: string[];
    provenance: Provenance;
  } | null;
  realizedClosedTrades: number | null;
  exposureSource: string | null;
  seriesNote: string | null;
  errors: Record<string, string>;
}
export interface OverviewData {
  provenance: Provenance;
  equity: number | null;
  pnl: number | null;
  exposure: number | null;
  control: string;
  positions: Position[] | null;
  points: { time: string | number; value: number }[];
  needsYou: string | null;
  live?: LiveOverview;
}
export interface GraphNode {
  hasTelemetry?: boolean;
  statusLabel?: string;
  id: string;
  label: string;
  kind: string;
  lenses: Lens[];
  /** Supplied layout; absent for backends that provide none (layout is computed). */
  x?: number;
  y?: number;
  /** Backend-declared system topology position (system surface only). */
  layout?: { x: number; y: number };
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
  lenses?: Partial<Record<Lens, { available: boolean; note: string }>>;
  limits?: string[];
  eventsNote?: string;
  total?: number;
  truncated?: boolean;
}
/** A stored record whose exact id the reply text mentions. Mentioned, not
 * the source the reply was derived from. Names are never linked. */
export interface RecordMention {
  kind: "strategy" | "trade" | "decision" | "research";
  id: string;
  label: string;
  basis: "exact_id";
}
/** Existence is decided on the complete exact-id lookup; the lists shown are
 * capped for display. `truncated` valid matches are omitted from display, not
 * missing from the stores. */
export interface MentionCounts {
  resolved: number;
  truncated: number;
  unresolved: number;
}
/** A read-only tool the chat agent consulted for this reply. */
export interface Consulted {
  tool: string;
  arguments: string;
  rows: number | null;
  error: string | null;
}
export interface Reply {
  text: string;
  evidence: Provenance[];
  requestId?: string;
  /** null: the backend supplied none or a malformed list (see note) */
  links?: RecordMention[] | null;
  linksNote?: string;
  /** id-shaped tokens in the reply that match no stored record */
  unresolved?: string[] | null;
  /** complete-lookup counts behind the display-capped lists */
  counts?: MentionCounts | null;
  consulted?: Consulted[] | null;
  consultedNote?: string;
}
export interface ChatTurn {
  who: "owner" | "Luffy";
  text: string;
}
export type ControlOperation =
  "freeze" | "halt" | "resume" | "unhalt" | "panic";
/** One intended action: id and issue time are created once and reused on retry. */
export interface PendingRequest {
  action: string;
  id: string;
  t: number;
}
export interface ControlResult {
  requestId: string | null;
  operation: string | null;
  status: string;
  disposition: string;
  controlStateBefore: string | null;
  controlStateAfter: string | null;
  reasons: string[];
  supervisorOutcome: string | null;
  auditEventIds: number[];
  replayed: boolean;
  message: string;
}
export interface OwnerInterfaceStatus {
  availability: "AVAILABLE" | "UNAVAILABLE";
  status: string | null;
  reasons: string[];
  observedAt: string | null;
  controlState: string | null;
  recoveryInProgress: boolean | null;
}
export interface Marks {
  observedAt: string | null;
  marks: Record<string, { mark: number | null; upnl: number | null }>;
  error?: string;
  source?: string;
}
export interface TableData {
  paging?: { offset: number; limit: number; hasMore: boolean };
  generatedAt: string;
  source: string;
  rows: Record<string, unknown>[];
}
export type TradeStatusFilter = "all" | "open" | "closed";
/** One keyset page of journal trades, newest first (opened_at DESC, id DESC). */
export interface TradePage {
  generatedAt: string;
  source: string;
  status: TradeStatusFilter;
  limit: number;
  rows: Record<string, unknown>[];
  cursor: string | null;
  nextCursor: string | null;
  hasMore: boolean;
  /** Matching trades that sort before this page, read with the rows. */
  preceding: number;
  total: number;
  first: { opened_at: string; id: string } | null;
  last: { opened_at: string; id: string } | null;
  /** Whether the matching trades behind this page's cursor changed since the
   * traversal's first page (sticky for the traversal); null: unverifiable. */
  history: { changed: boolean | null; reason: string | null };
}
export interface LogTail {
  lines: string[] | null;
  observedAt: string | null;
  source: string;
  error?: string;
}
export interface OwnerAdapter {
  readonly mode: Mode;
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
    history?: ChatTurn[],
  ): Promise<Reply>;
  /** LIVE only. Absent capabilities render as explicitly unavailable. */
  marks?(signal: AbortSignal): Promise<Marks>;
  ownerInterface?(signal: AbortSignal): Promise<OwnerInterfaceStatus>;
  control?(
    op: ControlOperation,
    pending: PendingRequest,
  ): Promise<ControlResult | null>;
  table?(
    kind: "trades" | "strategies",
    signal: AbortSignal,
  ): Promise<TableData>;
  investigations?(signal: AbortSignal): Promise<{
    status: string;
    health: Record<string, unknown>;
    cases: Record<string, unknown>[];
  }>;
  decisions?(signal: AbortSignal, offset?: number): Promise<TableData>;
  tradePage?(
    query: { status: TradeStatusFilter; cursor: string | null; limit: number },
    signal: AbortSignal,
  ): Promise<TradePage>;
  logs?(signal: AbortSignal): Promise<LogTail>;
  /** LIVE read contracts (/owner-api/v1/<path>): research, operations/activity,
   * diagnostics, trades/<id>/lineage, strategies/<id>, knowledge/note?id=…
   * Fields a store does not record arrive listed under `unavailable`. */
  ownerRead?(path: string, signal: AbortSignal): Promise<OwnerRecord>;
  approvalDecision?(body: {item_id: string; binding_hash: string; decision: "APPROVED" | "REJECTED"}, pending: PendingRequest): Promise<OwnerRecord>;
  /** The attention collector's latest scan (/api/attention/latest). */
  attention?(signal: AbortSignal): Promise<OwnerRecord>;
}
export type OwnerRecord = Record<string, unknown> & { generated_at?: string };
