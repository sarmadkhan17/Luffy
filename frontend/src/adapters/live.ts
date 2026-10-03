/** LIVE adapter: reviewed backend contracts only. This module must never import
 * fixtures; a failed request rejects and nothing is substituted. */
import type {
  ControlOperation,
  ControlResult,
  GraphData,
  GraphNode,
  Lens,
  LogTail,
  Marks,
  OverviewData,
  OwnerAdapter,
  OwnerInterfaceStatus,
  PendingRequest,
  Position,
  Protection,
  ProtectionStatus,
  Provenance,
  MentionCounts,
  RecordMention,
  Reply,
  TableData,
  TradePage,
  Freshness,
} from "./contracts";
import { readContractIssue } from "./readContracts";

export class SessionExpired extends Error {}
export class BackendUnavailable extends Error {}
export class ContractViolation extends Error {}

export interface Bootstrap {
  mode: "LIVE";
  principal: string | null;
  session: {
    authenticated: boolean;
    method: string;
    expires_at: string | null;
  };
  backend: {
    commit: string | null;
    started_at: string;
    source_time: string;
    frontend_build: string | null;
  };
  owner_interface: { configured: boolean; check: string; note: string };
  providers: Record<string, string>;
  capabilities: { voice: boolean; chat: string; controls: string[] };
}

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const API = "/owner-api/v1";
const obj = (v: unknown): v is Json =>
  typeof v === "object" && v !== null && !Array.isArray(v);
function need(cond: unknown, what: string): asserts cond {
  if (!cond)
    throw new ContractViolation(
      `Backend response did not match the ${what} contract. No substitute data was loaded.`,
    );
}
const fresh = (v: unknown): Freshness =>
  v === "fresh" || v === "stale" ? v : "unavailable";

export function createTransport(onUnauthorized: () => void) {
  return async function api<T = Json>(
    path: string,
    init: RequestInit = {},
  ): Promise<T> {
    let r: Response;
    try {
      r = await fetch(path, {
        credentials: "same-origin",
        cache: "no-store",
        ...init,
        headers: { Accept: "application/json", ...(init.headers ?? {}) },
      });
    } catch (e) {
      if (init.signal?.aborted) throw e;
      throw new BackendUnavailable(
        "Backend unreachable. No data was substituted; retry when the connection returns.",
      );
    }
    if (r.status === 401) {
      onUnauthorized();
      throw new SessionExpired("Session ended. Sign in again to continue.");
    }
    let body: unknown = null;
    try {
      body = await r.json();
    } catch {
      body = null;
    }
    if (!r.ok) {
      const reason =
        obj(body) && typeof body.error === "string" ? body.error : "";
      const err = new Error(
        `Backend error ${r.status}${reason ? ` (${reason})` : ""}. No substitute data was loaded.`,
      );
      (err as Error & { code?: string }).code = reason;
      throw err;
    }
    return body as T;
  };
}

function provenance(
  id: string,
  source: string,
  s: Json | null | undefined,
  summary: string,
  classification: string,
): Provenance | null {
  if (!s) return null;
  return {
    id,
    source,
    observedAt: typeof s.observed_at === "string" ? s.observed_at : null,
    freshness: fresh(s.freshness),
    summary,
    classification,
  };
}

function protection(p: Json | undefined): Protection {
  const status = p?.status;
  const known = [
    "VERIFIED",
    "PARTIAL",
    "STALE",
    "UNVERIFIED",
    "UNPROTECTED",
    "UNREADABLE",
    "UNAVAILABLE",
  ];
  return {
    status: known.includes(status) ? status : "UNAVAILABLE",
    observedAt: typeof p?.observed_at === "string" ? p.observed_at : null,
    lastReported: typeof p?.last_reported === "string" ? p.last_reported : null,
    reasons: Array.isArray(p?.reasons) ? p!.reasons.map(String) : [],
  };
}

export function mapOverview(o: Json): OverviewData {
  need(obj(o) && o.mode === "LIVE" && obj(o.errors), "overview");
  const errors = o.errors as Record<string, string>;
  const acc = obj(o.account) ? o.account : null;
  const account = provenance(
    "journal:equity@" + (acc?.observed_at ?? "missing"),
    acc?.source ?? "journal equity table",
    acc,
    acc
      ? `Latest kernel-recorded equity ${acc.equity} ${acc.currency}; stale after ${acc.stale_after_s}s.`
      : `Unavailable: ${errors.account ?? "no record"}.`,
    "journal record · demo venue account",
  );
  // Protection truth is the kernel's read-only protection snapshot. The adapter
  // re-checks the backend: VERIFIED needs a fresh VERIFIED snapshot with every
  // check true and a complete stop listing, never venue_protection alone.
  const snap = obj(o.protection?.snapshot) ? o.protection.snapshot : null;
  // Local expiry from the snapshot's own checked_at, in browser-clock ms: the
  // server's generated_at gives the clock offset. No computable expiry → the
  // evidence is treated as already expired, never as current.
  const receivedAt = Date.now();
  const serverNow = Date.parse(String(o.generated_at));
  const offset = Number.isFinite(serverNow) ? serverNow - receivedAt : 0;
  const checkedAt = Date.parse(String(snap?.observed_at));
  const expiresAt =
    snap && Number.isFinite(checkedAt) && typeof snap.stale_after_s === "number"
      ? checkedAt + snap.stale_after_s * 1000 - offset
      : null;
  const snapshotVerified =
    !!snap &&
    snap.freshness === "fresh" &&
    expiresAt !== null &&
    receivedAt <= expiresAt &&
    snap.status === "VERIFIED" &&
    snap.venue_positions === true &&
    snap.observation_consistent === true &&
    snap.reconciliation === true &&
    snap.venue_protection === true &&
    snap.precision_known === true &&
    snap.complete_listing === true;
  const notVerified: ProtectionStatus = !snap
    ? "UNAVAILABLE"
    : snap.freshness !== "fresh" || expiresAt === null || receivedAt > expiresAt
      ? "STALE"
      : snap.status === "UNREADABLE"
        ? "UNREADABLE"
        : "PARTIAL";
  const evidence = provenance(
    "journal:protection_snapshot@" + (snap?.observed_at ?? "missing"),
    snap?.source ?? "kernel protection snapshot",
    snap,
    snap
      ? `Kernel protection check ${snap.status}: venue positions ${snap.venue_positions ? "read" : "not read"}, stop listing ${snap.complete_listing ? "complete" : "incomplete"}, observation ${snap.observation_consistent ? "consistent" : "not proven consistent"}, journal agreement ${snap.reconciliation ? "verified" : "not verified"}, venue protection ${snap.venue_protection ? "verified" : "not verified"}, price precision ${snap.precision_known ? "known" : "unknown"} at that time${snap.reasons?.length ? ` (${snap.reasons.slice(0, 4).join(", ")})` : ""}. Reconciliation cleanliness ${snap.cleanliness?.status ?? "UNKNOWN"} (reported separately; does not change protection).`
      : "No kernel protection check is recorded: not verified.",
    "read-only venue protection evidence",
  );
  const positions: Position[] | null = Array.isArray(o.positions)
    ? o.positions.map((p: Json) => {
        const detail = protection(p.protection);
        if (detail.status === "VERIFIED" && !snapshotVerified) {
          detail.status = notVerified;
          detail.reasons.push("complete_verification_evidence_missing");
        }
        return {
          id: String(p.id),
          symbol: String(p.symbol),
          side: String(p.side),
          notional:
            typeof p.notional_usdt === "number" ? p.notional_usdt : null,
          pnl: null,
          protection: detail.status,
          protectionDetail: detail,
          journalStop: {
            price:
              typeof p.journal_stop?.price === "number"
                ? p.journal_stop.price
                : null,
            orderRefRecorded: p.journal_stop?.order_ref_recorded === true,
          },
          openedAt: typeof p.opened_at === "string" ? p.opened_at : null,
        };
      })
    : null;
  const points = Array.isArray(o.equity_series?.points)
    ? o.equity_series.points.filter(
        (p: Json) => typeof p.time === "number" && typeof p.value === "number",
      )
    : [];
  const needs = obj(o.needs_you) ? o.needs_you : null;
  const heartbeat = obj(o.heartbeat) ? o.heartbeat : null;
  return {
    provenance: account ?? {
      id: "journal:equity@missing",
      source: "journal equity table",
      observedAt: null,
      freshness: "unavailable",
      summary: `Unavailable: ${errors.account ?? "no record"}.`,
      classification: "journal record · demo venue account",
    },
    equity: typeof acc?.equity === "number" ? acc.equity : null,
    pnl:
      typeof o.realized_today?.value === "number"
        ? o.realized_today.value
        : null,
    exposure:
      typeof o.exposure?.pct_of_equity === "number"
        ? o.exposure.pct_of_equity
        : null,
    control:
      typeof o.control?.state === "string" ? o.control.state : "UNAVAILABLE",
    positions,
    points,
    needsYou: needs
      ? needs.needs_owner
        ? `Owner action required: ${(needs.reasons as string[]).join(", ") || "reason not recorded"}`
        : "Supervisor records no owner action"
      : null,
    live: {
      generatedAt: String(o.generated_at),
      control: obj(o.control)
        ? {
            state: String(o.control.state),
            observedAt: String(o.control.observed_at),
            lastEventAt: o.control.last_event?.ts ?? null,
          }
        : null,
      heartbeat: provenance(
        "heartbeat_luffy@" + (heartbeat?.observed_at ?? "missing"),
        heartbeat?.source ?? "kernel heartbeat",
        heartbeat,
        heartbeat
          ? `Kernel heartbeat ${heartbeat.age_s}s old (stale after ${heartbeat.stale_after_s}s).`
          : "No readable kernel heartbeat.",
        "kernel liveness",
      ),
      account,
      protection: obj(o.protection)
        ? {
            status:
              (o.protection.status === "VERIFIED" ||
                o.protection.status === "NO_POSITIONS") &&
              !snapshotVerified
                ? notVerified
                : o.protection.status,
            evidence,
            reasons: Array.isArray(snap?.reasons)
              ? snap!.reasons.map(String)
              : [],
            expiresAt,
          }
        : null,
      needsYou: needs
        ? {
            needsOwner: needs.needs_owner === true,
            reasons: Array.isArray(needs.reasons)
              ? needs.reasons.map(String)
              : [],
            provenance: provenance(
              "journal:supervisor_status@" + (needs.observed_at ?? "missing"),
              needs.source,
              needs,
              "Supervisor owner-attention flag and reasons.",
              "venue reconciliation evidence",
            )!,
          }
        : null,
      realizedClosedTrades:
        typeof o.realized_today?.closed_trades === "number"
          ? o.realized_today.closed_trades
          : null,
      exposureSource: o.exposure?.source ?? null,
      seriesNote: o.equity_series
        ? `${o.equity_series.bucket} · last ${o.equity_series.window_hours} h`
        : null,
      errors,
    },
  };
}

const LENSES: Lens[] = ["Knowledge", "Evidence", "Timeline", "Code"];
export function mapKnowledge(k: Json): GraphData {
  need(obj(k) && Array.isArray(k.nodes) && Array.isArray(k.edges), "knowledge");
  const lensInfo = obj(k.lenses) ? k.lenses : {};
  const available = LENSES.filter((l) => lensInfo[l]?.available === true);
  const nodes: GraphNode[] = k.nodes.map((n: Json) => ({
    id: String(n.id),
    label: String(n.label),
    kind: String(n.kind),
    lenses: Array.isArray(n.lenses) ? n.lenses.filter((l: Lens) => available.includes(l)) : available,
    evidence: {
      id: String(n.provenance?.id ?? n.id),
      source: String(n.provenance?.source ?? n.id),
      observedAt: n.provenance?.observed_at ?? null,
      freshness: "not_assessed",
      summary: n.provenance?.summary ?? "No claim recorded in this note.",
      classification: String(n.provenance?.classification ?? "context_only"),
      timeBasis: n.provenance?.time_basis,
    },
  }));
  return {
    nodes,
    edges: k.edges.map((e: Json) => ({
      id: String(e.id),
      source: String(e.source),
      target: String(e.target),
      relation: String(e.relation),
      kind: e.kind === "typed" ? "typed" : "link",
      evidenceId: e.evidence,
    })),
    events: [],
    provenance: {
      id: "owner-api:knowledge@" + k.generated_at,
      source: String(k.source),
      observedAt: k.generated_at ?? null,
      freshness: "not_assessed",
      timeBasis: "read_time",
      summary: `${k.returned_nodes} of ${k.total_nodes} notes returned; ${k.unresolved_edges} relations point at notes that do not exist.`,
      classification: "context_only",
    },
    lenses: lensInfo,
    limits: Array.isArray(k.limits) ? k.limits.map(String) : [],
    total: k.total_nodes,
    truncated: k.truncated === true,
  };
}

export function mapSystem(s: Json): GraphData {
  need(obj(s) && Array.isArray(s.nodes) && Array.isArray(s.edges), "system");
  return {
    nodes: s.nodes.map((n: Json) => {
      const t = obj(n.telemetry) ? n.telemetry : {};
      const health = [
        "active",
        "idle",
        "degraded",
        "failing",
        "unknown",
      ].includes(t.health)
        ? t.health
        : undefined;
      return {
        id: String(n.id),
        label: String(n.label),
        kind: "Component",
        hasTelemetry: typeof t.source === "string" && t.source.length > 0,
        lenses: ["Knowledge"] as Lens[],
        layout:
          typeof n.x === "number" && typeof n.y === "number"
            ? { x: n.x, y: n.y }
            : undefined,
        health,
        evidence: {
          id: `telemetry:${n.id}`,
          source: t.source ?? `No telemetry source · ${n.source_file}`,
          observedAt: t.observed_at ?? null,
          freshness: fresh(t.freshness),
          summary: `${t.summary ?? "No telemetry"}. Declared in ${n.source_file}.`,
          classification: t.source ? "observed telemetry" : "architecture only",
        },
      };
    }),
    edges: s.edges.map((e: Json) => ({
      id: String(e.id),
      source: String(e.source),
      target: String(e.target),
      relation: String(e.relation),
      kind: "architecture" as const,
    })),
    events: [],
    eventsNote: typeof s.events_note === "string" ? s.events_note : undefined,
    provenance: {
      id: "owner-api:system@" + s.generated_at,
      source: String(s.architecture_source),
      observedAt: s.generated_at ?? null,
      freshness: "not_assessed",
      timeBasis: "read_time",
      summary: String(s.architecture_source),
      classification: "declared architecture + observed telemetry",
    },
  };
}

function mapControl(r: Json | null | undefined): ControlResult | null {
  if (!obj(r) || typeof r.status !== "string") return null;
  return {
    requestId: r.request_id ?? null,
    operation: r.operation ?? null,
    status: r.status,
    disposition:
      typeof r.disposition === "string" ? r.disposition : "OUTCOME_UNKNOWN",
    controlStateBefore: r.control_state_before ?? null,
    controlStateAfter: r.control_state_after ?? null,
    reasons: Array.isArray(r.reasons) ? r.reasons.map(String) : [],
    supervisorOutcome: r.supervisor_outcome ?? null,
    auditEventIds: Array.isArray(r.audit_event_ids) ? r.audit_event_ids : [],
    replayed: r.replayed === true,
    message: typeof r.message === "string" ? r.message : r.status,
  };
}

const FIELDS =
  "request_id operation status disposition control_state_before control_state_after reasons supervisor_outcome audit_event_ids replayed message";
export const CONTROL_MUTATION = `mutation($o:String!,$r:String!,$t:Float!){res:owner_control(operation:$o,request_id:$r,issued_at_ms:$t){${FIELDS}}}`;
export const PANIC_MUTATION = `mutation($r:String!,$t:Float!){res:panic(request_id:$r,issued_at_ms:$t){${FIELDS}}}`;

const MENTION_KINDS = ["strategy", "trade", "decision", "research"];
const count = (v: unknown): v is number =>
  typeof v === "number" && Number.isInteger(v) && v >= 0;
/** Display-capped mention lists checked against their complete-lookup counts:
 * shown + truncated = resolved, and shown unresolved <= unresolved. */
export function mentionCounts(
  shown: number | null,
  resolved: unknown,
  truncated: unknown,
  shownUnresolved: number | null,
  unresolvedTotal: unknown,
): MentionCounts | null {
  if (
    shown === null ||
    shownUnresolved === null ||
    !count(resolved) ||
    !count(truncated) ||
    !count(unresolvedTotal) ||
    shown + truncated !== resolved ||
    shownUnresolved > unresolvedTotal
  )
    return null;
  return { resolved, truncated, unresolved: unresolvedTotal };
}
/** Record mentions and consulted tools from a chat reply. Three states are
 * kept apart: a lookup that found ids, a lookup that found none, and a lookup
 * that failed or returned a malformed list (links null, with the reason). A
 * failure is never shown as "no records mentioned". */
export function mapMentions(
  r: Json,
): Pick<
  Reply,
  | "links"
  | "linksNote"
  | "unresolved"
  | "counts"
  | "consulted"
  | "consultedNote"
> {
  const lookupError =
    typeof r.links_error === "string" && r.links_error !== ""
      ? r.links_error
      : r.links_error === undefined || r.links_error === null
        ? null
        : "malformed error field";
  const wellFormed =
    Array.isArray(r.links) &&
    r.links.every(
      (x: unknown) =>
        obj(x) &&
        MENTION_KINDS.includes(x.kind) &&
        typeof x.id === "string" &&
        x.id !== "" &&
        typeof x.label === "string" &&
        x.basis === "exact_id",
    );
  const unresolvedOk =
    Array.isArray(r.unresolved) &&
    r.unresolved.every(
      (x: unknown) => obj(x) && typeof x.token === "string" && x.token !== "",
    );
  // existence counts come from the complete lookup; the lists are display-
  // capped. They must agree, or none of it is shown as evidence.
  const counts = mentionCounts(
    wellFormed ? r.links.length : null,
    r.resolved_count,
    r.truncated_count,
    unresolvedOk ? r.unresolved.length : null,
    r.unresolved_count,
  );
  const links =
    lookupError === null && wellFormed && unresolvedOk && counts
      ? (r.links as RecordMention[])
      : null;
  const unresolved =
    links !== null
      ? (r.unresolved as Json[]).map((x) => String(x.token))
      : null;
  const consulted = Array.isArray(r.consulted)
    ? r.consulted.every((x: unknown) => obj(x) && typeof x.tool === "string")
      ? (r.consulted as Json[]).map((x) => ({
          tool: String(x.tool),
          arguments: typeof x.arguments === "string" ? x.arguments : "",
          rows: typeof x.rows === "number" ? x.rows : null,
          error: typeof x.error === "string" ? x.error : null,
        }))
      : null
    : null;
  return {
    links,
    linksNote:
      links !== null
        ? String(r.links_basis ?? "")
        : lookupError !== null
          ? `Stored-record lookup unavailable (${lookupError}).`
          : r.links === undefined
            ? "Stored-record lookup unavailable (this backend reports no lookup)."
            : "Stored-record lookup unavailable (malformed response).",
    unresolved,
    counts: links !== null ? counts : null,
    consulted,
    consultedNote:
      consulted !== null
        ? undefined
        : String(
            r.consulted_note ??
              "Consulted reads unavailable (not reported or malformed).",
          ),
  };
}

export function createLiveAdapter(onUnauthorized: () => void): OwnerAdapter {
  const api = createTransport(onUnauthorized);
  return {
    mode: "LIVE",
    async overview(_s, signal) {
      return mapOverview(await api(`${API}/overview`, { signal }));
    },
    async graph(surface, _s, signal) {
      return surface === "knowledge"
        ? mapKnowledge(await api(`${API}/knowledge`, { signal }))
        : mapSystem(await api(`${API}/system`, { signal }));
    },
    async chat(text, _s, signal, onChunk, history = []) {
      const r = await api<Json>(`${API}/chat`, {
        method: "POST",
        signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: history.slice(-20) }),
      });
      need(typeof r.reply === "string" && r.operational === false, "chat");
      onChunk(r.reply);
      return {
        text: r.reply,
        evidence: Array.isArray(r.evidence) ? r.evidence.map((e: Json) => ({
          id: String(e.record_id), source: typeof e.source === "string" ? e.source : JSON.stringify(e.source),
          observedAt: typeof e.timestamp === "string" ? e.timestamp : typeof e.timestamp === "number" ? new Date(e.timestamp).toISOString() : null,
          freshness: "not_assessed" as const, summary: JSON.stringify(e.value),
          classification: `${e.verification} · historical · SHA256 ${e.sha256}`,
        })) : [],
        requestId: r.request_id,
        ...mapMentions(r),
      };
    },
    async marks(signal): Promise<Marks> {
      const m = await api<Json>(`${API}/enrichment/marks`, { signal });
      need(obj(m.marks), "marks");
      return {
        observedAt: m.observed_at ?? null,
        marks: m.marks,
        error: m.error,
        source: m.source,
      };
    },
    async ownerInterface(signal): Promise<OwnerInterfaceStatus> {
      const h = await api<Json>(`${API}/owner-interface`, { signal });
      need(
        h.availability === "AVAILABLE" || h.availability === "UNAVAILABLE",
        "owner-interface",
      );
      return {
        availability: h.availability,
        status: h.status ?? null,
        reasons: Array.isArray(h.reasons) ? h.reasons.map(String) : [],
        observedAt: h.observed_at ?? null,
        controlState: h.control_state ?? null,
        recoveryInProgress: h.recovery_in_progress ?? null,
      };
    },
    /** Resolves null when no definitive answer arrived (keep the same id). */
    async control(op: ControlOperation, pending: PendingRequest) {
      const query = op === "panic" ? PANIC_MUTATION : CONTROL_MUTATION;
      const variables =
        op === "panic"
          ? { r: pending.id, t: pending.t }
          : { o: op, r: pending.id, t: pending.t };
      try {
        const d = await api<Json>("/graphql", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ query, variables }),
        });
        return mapControl(d?.data?.res);
      } catch (e) {
        if (e instanceof SessionExpired) throw e;
        return null;
      }
    },
    async table(kind, signal): Promise<TableData> {
      const t = await api<Json>(`${API}/${kind}`, { signal });
      const rows = t[kind];
      need(Array.isArray(rows), kind);
      return { generatedAt: t.generated_at, source: t.source, rows };
    },
    async tradePage({ status, cursor, limit }, signal): Promise<TradePage> {
      const qs = new URLSearchParams({ status, limit: String(limit) });
      if (cursor !== null) qs.set("cursor", cursor);
      const t = await api<Json>(`${API}/trades?${qs}`, { signal });
      const p = t.page;
      const edge = (e: unknown) =>
        e === null ||
        (obj(e) && typeof e.opened_at === "string" && typeof e.id === "string");
      need(
        obj(t) &&
          Array.isArray(t.trades) &&
          t.trades.every(obj) &&
          t.status === status &&
          typeof t.limit === "number" &&
          t.trades.length <= t.limit &&
          typeof t.source === "string" &&
          obj(p) &&
          p.cursor === cursor &&
          typeof p.has_more === "boolean" &&
          (p.has_more
            ? typeof p.next_cursor === "string" && p.next_cursor !== ""
            : p.next_cursor === null) &&
          Number.isInteger(p.preceding) &&
          p.preceding >= 0 &&
          Number.isInteger(p.total) &&
          p.total >= 0 &&
          edge(p.first) &&
          edge(p.last) &&
          obj(p.history) &&
          (typeof p.history.changed === "boolean" ||
            p.history.changed === null) &&
          (typeof p.history.reason === "string" || p.history.reason === null) &&
          // a first page starts a traversal: nothing to have changed yet
          (cursor !== null || p.history.changed === false),
        "trade history page",
      );
      return {
        generatedAt: t.generated_at,
        source: t.source,
        status,
        limit: t.limit,
        rows: t.trades,
        cursor,
        nextCursor: p.next_cursor,
        hasMore: p.has_more,
        preceding: p.preceding,
        total: p.total,
        first: p.first,
        last: p.last,
        history: { changed: p.history.changed, reason: p.history.reason },
      };
    },
    async investigations(signal) {
      const d = await api<Json>("/api/investigations/latest", { signal });
      need(
        typeof d.status === "string" && obj(d.health) && Array.isArray(d.cases),
        "investigations",
      );
      return { status: d.status, health: d.health, cases: d.cases };
    },
    async decisions(signal, offset = 0): Promise<TableData> {
      const d = await api<Json>("/graphql", {
        method: "POST",
        signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query:
            "query($offset:Int!){decisions(limit:101,offset:$offset){id ts symbol action score threshold confidence executed skip_reason}}",
          variables: { offset },
        }),
      });
      need(!d.errors && Array.isArray(d.data?.decisions), "decisions");
      return {
        rows: d.data.decisions.slice(0, 100),
        paging: { offset, limit: 100, hasMore: d.data.decisions.length > 100 },
        source:
          "journal decisions via GraphQL · offset window · not trade lineage",
        generatedAt: new Date().toISOString(),
      };
    },
    async ownerRead(path, signal) {
      const d = await api<Json>(`${API}/${path}`, { signal });
      const what = path.split("?")[0];
      need(obj(d) && typeof d.generated_at === "string", what);
      need(
        d.unavailable === undefined ||
          (Array.isArray(d.unavailable) &&
            d.unavailable.every(
              (u: unknown) =>
                obj(u) &&
                typeof u.field === "string" &&
                typeof u.reason === "string",
            )),
        `${what} unavailable-list`,
      );
      // malformed evidence is rejected, never shown as empty or complete
      const issue = readContractIssue(path, d);
      if (issue)
        throw new ContractViolation(
          `MALFORMED evidence: the ${what} response ${issue}. No substitute data was loaded.`,
        );
      return d;
    },
    async approvalDecision(body, pending) {
      const result = await api<Json>(`${API}/needs-you/decision`, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({...body, request_id: pending.id, issued_at_ms: pending.t}),
      });
      need(typeof result.status === "string" && Array.isArray(result.reasons), "approval result");
      return result;
    },
    async attention(signal) {
      const d = await api<Json>("/api/attention/latest", { signal });
      need(obj(d) && typeof d.status === "string", "attention");
      return d;
    },
    async logs(signal): Promise<LogTail> {
      const l = await api<Json>(`${API}/logs?lines=120`, { signal });
      return {
        lines: Array.isArray(l.tail) ? l.tail.map(String) : null,
        observedAt: l.observed_at ?? null,
        source: l.source ?? "logs/luffy.log",
        error: l.error,
      };
    },
  };
}

export async function fetchBootstrap(
  onUnauthorized: () => void,
): Promise<Bootstrap> {
  const b = await createTransport(onUnauthorized)<Json>(`${API}/bootstrap`);
  need(
    obj(b) && b.mode === "LIVE" && obj(b.session) && obj(b.backend),
    "bootstrap",
  );
  return b as Bootstrap;
}

export async function logout() {
  try {
    await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
  } catch {
    /* the local session state is cleared regardless */
  }
}
