/** Shape contracts for the LIVE owner read endpoints (/owner-api/v1/<path>).
 *
 * Checked at the adapter boundary so malformed evidence can never reach a view
 * as an empty list or a valid-looking state: a view only ever sees
 * VALID_WITH_DATA or VALID_EMPTY, and anything else is rejected as MALFORMED
 * with the offending field named. Nullable fields are declared as such; every
 * list is required to be a list of the declared record shape. */

type Check = (v: unknown, at: string) => string | null;

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str: Check = (v, at) =>
  typeof v === "string" ? null : `${at} is not a string`;
const num: Check = (v, at) =>
  typeof v === "number" && Number.isFinite(v) ? null : `${at} is not a number`;
const bool: Check = (v, at) =>
  typeof v === "boolean" ? null : `${at} is not a boolean`;
const any: Check = () => null;
const record: Check = (v, at) => (isObj(v) ? null : `${at} is not a record`);
const nullable =
  (c: Check): Check =>
  (v, at) =>
    v === null ? null : c(v, at);
const optional =
  (c: Check): Check =>
  (v, at) =>
    v === undefined ? null : c(v, at);
/** a stored id: a non-empty string (0 is not an id, "" is not an id) */
const id: Check = (v, at) =>
  typeof v === "string" && v !== "" ? null : `${at} is not a recorded id`;
const idOrNum: Check = (v, at) =>
  (typeof v === "string" && v !== "") ||
  (typeof v === "number" && Number.isInteger(v))
    ? null
    : `${at} is not a recorded id`;

function list(item: Check = any): Check {
  return (v, at) => {
    if (!Array.isArray(v)) return `${at} is not a list`;
    for (let i = 0; i < v.length; i++) {
      const bad = item(v[i], `${at}[${i}]`);
      if (bad) return bad;
    }
    return null;
  };
}
function shape(fields: Record<string, Check>): Check {
  return (v, at) => {
    if (!isObj(v)) return `${at} is not a record`;
    for (const [k, c] of Object.entries(fields)) {
      const bad = c(v[k], at ? `${at}.${k}` : k);
      if (bad) return bad;
    }
    return null;
  };
}

const ref = nullable(shape({ kind: str, id }));
const strategyRef = shape({ id, in_registry: bool });
const registryRow = shape({ id });
const hashRow = shape({ hash: id });

const lineage = shape({
  trade: shape({ id }),
  decision: nullable(shape({ id, signals: list(record) })),
  strategy: nullable(record),
  cycle: nullable(record),
  votes: list(shape({ agent: str })),
  votes_basis: nullable(record),
  chain: list(shape({ step: str, status: str, ref })),
  links: record,
  accounting: shape({ receipts: list(shape({ integrity: str })) }),
  outcome: nullable(record),
});

const decision = shape({
  decision: shape({ id, signals: list(record) }),
  cycle: nullable(record),
  votes: list(shape({ agent: str })),
  outcome: nullable(record),
  trades: list(registryRow),
  strategies: list(strategyRef),
});

const researchList: Check = (v, at) => {
  const base = shape({ available: bool })(v, at);
  if (base) return base;
  if ((v as Record<string, unknown>).available === false) return null;
  return shape({
    counts: record,
    results: list(
      shape({ hash: id, seed_strategy: nullable(strategyRef), parts: any }),
    ),
    results_page: shape({ offset: num, limit: num, has_more: bool }),
    runs: list(record),
    evidence: shape({
      controls: list(record),
      slices: list(record),
      gauges: list(record),
    }),
    candidates: list(hashRow),
    registrations: list(shape({ hash: nullable(str) })),
    ideas: list(
      shape({
        idea_id: nullable(str),
        spec: nullable(str),
        spec_in_registry: bool,
      }),
    ),
  })(v, at);
};

const researchItem = shape({
  item: shape({ hash: id, seed_strategy: nullable(strategyRef) }),
  parent: nullable(hashRow),
  children: list(hashRow),
  candidate: nullable(record),
  registrations: list(record),
});

const strategy = shape({
  id,
  lifecycle: list(shape({ event: str })),
  recent_trades: list(registryRow),
  journal_economics: nullable(record),
  brain_events: list(shape({ kind: str })),
  family: shape({
    parent: nullable(shape({ id, in_registry: bool })),
    children: list(registryRow),
    siblings: list(registryRow),
  }),
  postmortem: nullable(record),
  source_idea: nullable(record),
  research_seeded: list(hashRow),
});

const operations = shape({
  window: shape({ decisions: num }),
  timeline: list(
    shape({
      kind: str,
      title: str,
      ref,
      strategies: list(strategyRef),
    }),
  ),
  risk_blocks: list(shape({ reason: str, n: num })),
  decisions: list(registryRow),
  signals: list(record),
  scans: list(shape({ scan_id: str })),
  orders: list(registryRow),
  control_events: list(shape({ event: str })),
  signal_cooldowns: shape({ in_newest_500_control_events: num }),
});

const overview = shape({
  decisions: shape({
    window: num,
    executed: list(registryRow),
    skipped: list(registryRow),
    risk_blocks: list(shape({ reason: str, n: num })),
  }),
  strategies: list(registryRow),
  lifecycle: list(shape({ strategy_id: id, in_registry: bool })),
  research: nullable(
    shape({
      latest_run: nullable(record),
      newest_results: list(
        shape({ hash: id, seed_strategy: nullable(strategyRef) }),
      ),
      survivors: num,
      ideas: list(record),
    }),
  ),
  risk_state: nullable(record),
  rent_state: nullable(record),
  unresolved_owner_requests: list(record),
});

const edge = shape({ source: id, target: id });
const observed = shape({
  flows: list(
    shape({
      source: id,
      target: id,
      record: str,
      count: nullable(num),
      status: str,
      declared_edge: bool,
      saturated: bool,
    }),
  ),
  declared_edges: list(edge),
  unobserved_declared_edges: list(edge),
});

const note = shape({
  body: str,
  sources: list(shape({ path: str })),
  chronology: list(shape({ event: str })),
  records: list(
    shape({
      kind: str,
      id,
      label: str,
      basis: (v, at) =>
        v === "exact_id" ? null : `${at} is not an exact-id basis`,
    }),
  ),
  records_error: nullable(str),
  records_unresolved: nullable(list(shape({ token: str, kind_hint: str }))),
  records_resolved_count: nullable(num),
  records_truncated_count: nullable(num),
  records_unresolved_count: nullable(num),
  family: nullable(shape({ kind: str, strategies: list(registryRow) })),
});

/** A note's mention lists are display-capped; their counts must agree with
 * the complete lookup (shown + truncated = resolved). */
const noteRecords: Check = (v, at) => {
  const bad = note(v, at);
  if (bad) return bad;
  const d = v as Record<string, unknown>;
  if (d.records_error !== null) return null;
  const shown = (d.records as unknown[]).length;
  const resolved = d.records_resolved_count;
  const truncated = d.records_truncated_count;
  const unresolved = d.records_unresolved_count;
  if (
    typeof resolved !== "number" ||
    typeof truncated !== "number" ||
    typeof unresolved !== "number" ||
    !Array.isArray(d.records_unresolved) ||
    shown + truncated !== resolved ||
    d.records_unresolved.length > unresolved
  )
    return "records counts do not agree with the listed records";
  return null;
};

const diagnostics = shape({
  storage: shape({ files: list(shape({ file: str })) }),
  collectors: list(record),
  incidents: list(record),
  owner_audit: list(record),
  probes: optional(list(shape({ probe: str, ok: bool, ms: num }))),
});

const controlEvents = shape({
  events: list(shape({ id: idOrNum, event: str })),
  has_more: bool,
  next_before: nullable(num),
});

/** The first contract violation of an owner read, or null when valid. */
export function readContractIssue(path: string, d: unknown): string | null {
  const p = path.split("?")[0];
  const check: Check | null = /^trades\/[^/]+\/lineage$/.test(p)
    ? lineage
    : p.startsWith("decisions/")
      ? decision
      : p.startsWith("research/combos/")
        ? researchItem
        : p === "research"
          ? researchList
          : p.startsWith("strategies/")
            ? strategy
            : p === "operations/activity"
              ? operations
              : p === "overview/activity"
                ? overview
                : p === "system/observed"
                  ? observed
                  : p === "knowledge/note"
                    ? noteRecords
                    : p === "diagnostics/events"
                      ? controlEvents
                      : p === "diagnostics"
                        ? diagnostics
                        : null;
  return check ? check(d, "") : null;
}

/** Live System: an affirmative "every declared edge is observed" is allowed
 * only when every declared edge has an observed flow with records. Returns
 * the declared edges without such a flow (so [] means complete). */
export function unprovenEdges(d: {
  flows: {
    source: string;
    target: string;
    status: string;
    count: number | null;
  }[];
  declared_edges: { source: string; target: string }[];
}) {
  return d.declared_edges.filter(
    (e) =>
      !d.flows.some(
        (f) =>
          f.source === e.source &&
          f.target === e.target &&
          f.status === "observed" &&
          typeof f.count === "number" &&
          f.count > 0,
      ),
  );
}
