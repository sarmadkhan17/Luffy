/** LIVE read contracts rendered as recorded: Trades lineage, Strategy detail,
 * Research ledger, Operations activity, Knowledge note and Diagnostics. Every
 * field comes from the backend response; what the backend lists under
 * `unavailable` is shown as UNAVAILABLE with its reason, never filled in. */
import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { usePreview } from "../context";
import { Badge, Panel, QueryState } from "../components/ui";
import {
  RecordTable,
  SourceStrip,
  Timeline,
  timestamp,
  value,
} from "../components/workspace";
import type { OwnerRecord } from "../adapters/contracts";
import { Investigations } from "./Activity";

type R = Record<string, unknown>;
const rec = (v: unknown): R | null =>
  typeof v === "object" && v !== null && !Array.isArray(v) ? (v as R) : null;
const rows = (v: unknown): R[] =>
  Array.isArray(v) ? (v.filter((x) => rec(x)) as R[]) : [];
const short = (v: unknown) =>
  typeof v === "string" && v.length > 16 ? `${v.slice(0, 12)}…` : value(v);
const bytes = (v: unknown) =>
  typeof v === "number"
    ? v >= 1e9
      ? `${(v / 1e9).toFixed(2)} GB`
      : v >= 1e6
        ? `${(v / 1e6).toFixed(1)} MB`
        : `${(v / 1e3).toFixed(1)} kB`
    : "Unavailable";

function useRead(path: string | null, poll?: number) {
  const { adapter } = usePreview();
  return useQuery({
    queryKey: ["owner-read", path],
    queryFn: ({ signal }) => adapter.ownerRead!(path!, signal),
    enabled: !!adapter.ownerRead && !!path,
    refetchInterval: poll,
  });
}

function Loaded({
  q,
  children,
}: {
  q: ReturnType<typeof useRead>;
  children: (d: OwnerRecord) => ReactNode;
}) {
  return (
    <>
      <QueryState
        error={q.error}
        loading={q.isPending}
        retry={() => void q.refetch()}
      />
      {q.data && !q.error && children(q.data)}
    </>
  );
}

const label = (field: string) => {
  const t = field.replaceAll("_", " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
};
export function UnavailableFields({ items }: { items: unknown }) {
  const list = rows(items);
  if (!list.length) return null;
  return (
    <ul className="unavailable-list" data-testid="unavailable-fields">
      {list.map((u) => (
        <li key={String(u.field)}>
          <Badge tone="amber">UNAVAILABLE</Badge>{" "}
          <strong>{label(String(u.field))}</strong> — {String(u.reason)}
        </li>
      ))}
    </ul>
  );
}

function Fields({ row, keys }: { row: R | null; keys: [string, string][] }) {
  if (!row) return <p className="quiet">Not recorded.</p>;
  return (
    <dl className="record-fields">
      {keys.map(([k, label]) => (
        <div key={k}>
          <dt>{label}</dt>
          <dd>
            {/(_at$|^ts$|^at$|^started$|^finished$)/.test(k)
              ? timestamp(row[k])
              : value(row[k])}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function SmallTable({
  data,
  columns,
  caption,
}: {
  data: R[];
  columns: [string, string, ((v: unknown, r: R) => ReactNode)?][];
  caption: string;
}) {
  if (!data.length) return <p className="quiet">No records returned.</p>;
  return (
    <div
      className="table-scroll"
      tabIndex={0}
      role="region"
      aria-label={caption}
    >
      <table>
        <caption>{caption}</caption>
        <thead>
          <tr>
            {columns.map(([, h], c) => (
              <th key={c}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((r, i) => (
            <tr key={i}>
              {columns.map(([k, h, fmt], c) => (
                <td key={c} data-label={h}>
                  {fmt
                    ? fmt(r[k], r)
                    : /(_at$|^ts$|^at$)/.test(k)
                      ? timestamp(r[k])
                      : value(r[k])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Trades ──────────────────────────────────────────────────────────────────
export function TradeLineage({ id }: { id: string }) {
  const q = useRead(`trades/${encodeURIComponent(id)}/lineage`);
  return (
    <section className="lineage" aria-label="Trade lineage">
      <h3>Lineage and accounting</h3>
      <Loaded q={q}>
        {(d) => {
          const decision = rec(d.decision);
          const strategy = rec(d.strategy);
          const acct = rec(d.accounting);
          const trade = rec(d.trade);
          const excursion = rec(trade?.excursion);
          return (
            <>
              <SourceStrip source={String(d.source)} at={d.generated_at} />
              <h4>Decision</h4>
              <Fields
                row={decision}
                keys={[
                  ["id", "Decision id"],
                  ["ts", "Recorded at"],
                  ["action", "Action"],
                  ["score", "Score"],
                  ["threshold", "Threshold"],
                  ["scan_id", "Attention scan (opportunity ref)"],
                  ["strategy_ids", "Strategy ids"],
                  ["meta_p", "Meta-label p"],
                ]}
              />
              {rows(decision?.signals).length > 0 && (
                <SmallTable
                  caption="Strategy signals recorded with the decision"
                  data={rows(decision?.signals)}
                  columns={[
                    ["strategy_id", "Strategy"],
                    ["action", "Action"],
                    ["confidence", "Confidence"],
                    ["rationale", "Rationale"],
                  ]}
                />
              )}
              <h4>Strategy (current registry row)</h4>
              <Fields
                row={strategy}
                keys={[
                  ["id", "Strategy id"],
                  ["name", "Name"],
                  ["state", "State now"],
                  ["generation", "Generation"],
                  ["parent_id", "Parent"],
                  ["spec_sha256", "Spec sha256"],
                  ["spec_sha256_basis", "Hash basis"],
                ]}
              />
              <h4>Accounting receipts</h4>
              <SmallTable
                caption="trade_accounting_bookings · integrity is the receipt's own sha256 replay"
                data={rows(acct?.receipts)}
                columns={[
                  ["receipt_id", "Receipt"],
                  ["kind", "Kind"],
                  [
                    "observed_ms",
                    "Booked at",
                    (v) =>
                      typeof v === "number"
                        ? timestamp(new Date(v).toISOString())
                        : "Unavailable",
                  ],
                  ["integrity", "Receipt integrity"],
                  [
                    "assessment",
                    "Fill assessment",
                    (v) => value(rec(v)?.status),
                  ],
                  [
                    "assessment",
                    "Reasons",
                    (v) =>
                      Array.isArray(rec(v)?.reasons)
                        ? (rec(v)!.reasons as unknown[]).join(", ")
                        : "Unavailable",
                  ],
                ]}
              />
              {typeof acct?.replay === "string" && (
                <p className="quiet">Replay: {acct.replay}</p>
              )}
              <h4>Outcome and excursion</h4>
              <Fields
                row={rec(d.outcome)}
                keys={[
                  ["fwd_ret_1h", "Forward return 1h"],
                  ["fwd_ret_4h", "Forward return 4h"],
                  ["fwd_ret_24h", "Forward return 24h"],
                  ["resolved_at", "Resolved at"],
                ]}
              />
              <Fields
                row={trade}
                keys={[
                  ["mfe_r", "MFE (R)"],
                  ["mae_r", "MAE (R)"],
                  ["initial_risk", "Initial risk"],
                ]}
              />
              {excursion && (
                <p className="quiet">
                  Excursion provenance: {value(excursion)}
                </p>
              )}
              <UnavailableFields items={d.unavailable} />
            </>
          );
        }}
      </Loaded>
    </section>
  );
}

// ── Strategies ──────────────────────────────────────────────────────────────
export function StrategyDetail({ id }: { id: string }) {
  const q = useRead(`strategies/${encodeURIComponent(id)}`);
  return (
    <section className="lineage" aria-label="Strategy evidence">
      <h3>Identity, evidence and lifecycle</h3>
      <Loaded q={q}>
        {(d) => {
          const spec = rec(d.spec);
          return (
            <>
              <SourceStrip source={String(d.source)} at={d.generated_at} />
              <Fields
                row={d}
                keys={[
                  ["id", "Registry id"],
                  ["kind", "Family / kind"],
                  ["generation", "Generation"],
                  ["parent_id", "Parent"],
                  ["spec_sha256", "Spec sha256 (current row)"],
                  ["params_sha256", "Params sha256"],
                ]}
              />
              <h4>Declared specification</h4>
              <Fields
                row={spec}
                keys={[
                  ["timeframe", "Timeframe"],
                  ["direction", "Direction"],
                  ["universe", "Universe"],
                  ["regime_filter", "Regime filter"],
                  ["exit", "Exit geometry"],
                ]}
              />
              <h4>Economics</h4>
              <Fields
                row={rec(d.journal_economics)}
                keys={[
                  ["trades", "Journal trades"],
                  ["open", "Open"],
                  ["closed", "Closed"],
                  ["wins", "Winning closes"],
                  ["realized_pnl", "Realized P&L (USDT, journal-booked)"],
                ]}
              />
              <p className="quiet">
                Registry stats (strategy row): {value(d.registry_stats)}
              </p>
              <h4>Lifecycle</h4>
              <Timeline
                events={rows(d.lifecycle).map((e) => ({
                  label: String(e.event),
                  at: e.at,
                  detail: `${e.source}${e.detail ? ` · ${value(e.detail)}` : ""}`,
                }))}
              />
              <h4>Recent trades</h4>
              <SmallTable
                caption="Journal trades for this strategy id (20 newest)"
                data={rows(d.recent_trades)}
                columns={[
                  ["symbol", "Instrument"],
                  ["side", "Side"],
                  ["status", "Status"],
                  ["realized_pnl", "Realized (USDT)"],
                  ["opened_at", "Opened"],
                ]}
              />
              <UnavailableFields items={d.unavailable} />
            </>
          );
        }}
      </Loaded>
    </section>
  );
}

// ── Research ────────────────────────────────────────────────────────────────
type Section = {
  title: string;
  note: string;
  count?: number;
  unavailable?: string;
  render?: () => ReactNode;
};
export function ResearchLive() {
  const q = useRead("research", 60000);
  const [pick, setPick] = useState(0);
  const d = q.data;
  const missing = new Map(
    rows(d?.unavailable).map((u) => [String(u.field), String(u.reason)]),
  );
  const ev = rec(d?.evidence);
  const gap = (field: string) =>
    missing.get(field) ?? "Not listed by the backend.";
  // the locked Research workspace sections, in their design order
  const sections: Section[] = [
    {
      title: "Questions",
      note: "Versioned research questions and their source context.",
      unavailable: gap("questions"),
    },
    {
      title: "Plans",
      note: "Frozen protocols, hypotheses and cutoffs.",
      unavailable: gap("plans"),
    },
    {
      title: "Evidence",
      note: "Point-in-time observations and assessment classifications: window power controls and measured gauges. Underpowered is not evidence of no edge.",
      count: rows(ev?.controls).length,
      render: () => (
        <>
          <SmallTable
            caption="research_controls"
            data={rows(ev?.controls)}
            columns={[
              ["tf", "TF"],
              ["window", "Window"],
              ["status", "Status"],
              ["powered", "Powered"],
              ["consistency_p", "Consistency p"],
              ["measured_at", "Measured"],
            ]}
          />
          <SmallTable
            caption="research_gauges per timeframe"
            data={rows(ev?.gauges)}
            columns={[
              ["tf", "TF"],
              ["n", "Gauges"],
              ["usable", "Usable"],
              ["measured_at", "Measured"],
            ]}
          />
        </>
      ),
    },
    {
      title: "Results",
      note: "Every combination the search scored, newest first. A ranking, not admission.",
      count: rows(d?.results).length,
      render: () => (
        <RecordTable
          title="Research results"
          rows={rows(d?.results).map((r) => ({ id: r.hash, ...r }))}
          filterKey="verdict"
          columns={[
            ["label", "Combination"],
            ["verdict", "Verdict"],
            ["status", "Status"],
            ["reason", "Reason"],
            ["median_pf", "Median PF"],
            ["trades", "Trades"],
            ["created_at", "Recorded"],
          ]}
        />
      ),
    },
    {
      title: "Runs",
      note: "Execution records: search batches as run, including failures.",
      count: rows(d?.runs).length,
      render: () => (
        <SmallTable
          caption="research_batches (newest first)"
          data={rows(d?.runs)}
          columns={[
            ["id", "Run"],
            ["started", "Started", (v) => timestamp(v)],
            ["tf", "TF"],
            ["geo", "Geometry"],
            ["round", "Round"],
            ["n", "Looked at"],
            ["ok", "Completed", (v) => (v ? "yes" : "no")],
            ["error", "Error"],
          ]}
        />
      ),
    },
    {
      title: "Research Bank",
      note: "Filed records and receipt cutoffs: candidates carried between gates and the shared calendar cut per horizon.",
      count: rows(d?.candidates).length,
      render: () => (
        <>
          <SmallTable
            caption="research_slices (calendar cut per horizon)"
            data={rows(ev?.slices)}
            columns={[
              ["tf", "TF"],
              [
                "cut_ms",
                "Cut",
                (v) =>
                  typeof v === "number"
                    ? timestamp(new Date(v).toISOString())
                    : "Unavailable",
              ],
              ["measured_at", "Measured"],
            ]}
          />
          <SmallTable
            caption="research_candidates"
            data={rows(d?.candidates)}
            columns={[
              ["hash", "Hash", short],
              ["state", "State"],
              ["gate1", "Gate 1"],
              ["gate3", "Gate 3"],
              ["reason", "Reason"],
              ["updated_at", "Updated"],
            ]}
          />
        </>
      ),
    },
    {
      title: "Prior recall",
      note: "Prior evidence retrieved as context_only.",
      unavailable: gap("prior_recall"),
    },
    {
      title: "Registrations",
      note: "Prespecified registrations: registered gate looks, each spending the registered error budget.",
      count: rows(d?.registrations).length,
      render: () => (
        <SmallTable
          caption="research_tests"
          data={rows(d?.registrations)}
          columns={[
            ["seq", "Seq"],
            ["hash", "Hash", short],
            ["gate", "Gate"],
            ["p", "p"],
            ["alpha_t", "Alpha"],
            ["rejected", "Rejected"],
            ["at", "At"],
          ]}
        />
      ),
    },
    {
      title: "Costs",
      note: "Attributed cost records; missing costs are not zero.",
      unavailable: gap("costs"),
    },
    {
      title: "Shadow reports",
      note: "Shadow observations; TESTED does not establish deployed maturity.",
      unavailable: gap("shadow_reports"),
    },
  ];
  const s = sections[pick];
  const counts = rec(d?.counts);
  return (
    <div className="workspace-stack">
      <Panel
        title="Research ledger"
        aside={
          <Badge tone={d?.available ? "mint" : "amber"}>
            {q.error
              ? "UNAVAILABLE"
              : d
                ? d.available
                  ? "Recorded"
                  : "UNAVAILABLE"
                : "Loading"}
          </Badge>
        }
      >
        <Loaded q={q}>
          {(d) => (
            <>
              <SourceStrip source={String(d.source)} at={d.generated_at} />
              {d.available === false ? (
                <p>{value(d.reason)}</p>
              ) : (
                <SmallTable
                  caption="Recorded results by verdict"
                  data={rows(counts?.results_by_verdict)}
                  columns={[
                    ["verdict", "Verdict"],
                    ["status", "Status"],
                    ["n", "Combinations"],
                  ]}
                />
              )}
              <p className="quiet">
                Runs recorded: {value(rec(counts?.runs)?.n)} (failed:{" "}
                {value(rec(counts?.runs)?.failed)}) · registered tests:{" "}
                {value(counts?.registered_tests)}.{" "}
                {Array.isArray(d.notes) && (d.notes as string[]).join(". ")}
              </p>
            </>
          )}
        </Loaded>
      </Panel>
      <div className="research-workspace">
        <div
          className="research-directory"
          role="group"
          aria-label="Research sections"
        >
          {sections.map((x, i) => (
            <button
              key={x.title}
              aria-pressed={pick === i}
              onClick={() => setPick(i)}
            >
              {x.title}
              <span>
                {!d
                  ? q.error
                    ? "Read failed"
                    : "Loading"
                  : x.unavailable
                    ? "Unavailable"
                    : `${x.count} loaded`}
              </span>
            </button>
          ))}
        </div>
        <Panel
          title={s.title}
          aside={
            !d ? undefined : s.unavailable ? (
              <Badge tone="amber">UNAVAILABLE</Badge>
            ) : (
              <Badge>Recorded</Badge>
            )
          }
        >
          {s.note && <p className="quiet">{s.note}</p>}
          {/* nothing about a section is claimed before the ledger read returns */}
          {!d ? null : s.unavailable ? (
            <div className="empty" data-testid="research-unavailable">
              <h3>No record source</h3>
              <p>{s.unavailable}</p>
            </div>
          ) : (
            s.render?.()
          )}
        </Panel>
      </div>
      <Investigations />
    </div>
  );
}

// ── Operations ──────────────────────────────────────────────────────────────
function AttentionScan() {
  const { adapter } = usePreview();
  const q = useQuery({
    queryKey: ["attention"],
    queryFn: ({ signal }) => adapter.attention!(signal),
    enabled: !!adapter.attention,
    refetchInterval: 30000,
  });
  if (!adapter.attention) return null;
  return (
    <Panel
      title="Attention scan"
      aside={<Badge tone="amber">{value(q.data?.status)}</Badge>}
    >
      <QueryState
        error={q.error}
        loading={q.isPending}
        retry={() => void q.refetch()}
      />
      {q.data && !q.error && (
        <>
          <Fields
            row={{
              ...(rec(q.data.scan) ?? {}),
              causes: Array.isArray(q.data.causes)
                ? q.data.causes.length
                : null,
              age_seconds: q.data.age_seconds,
              collector_status: q.data.collector_status,
            }}
            keys={[
              ["scan_id", "Scan id"],
              ["as_of_ms", "As of (ms)"],
              ["age_seconds", "Age (s)"],
              ["causes", "Candidate causes"],
              ["collector_status", "Collector"],
            ]}
          />
          <p className="quiet">
            Source: /api/attention/latest. A status of disabled or waiting means
            no current scan is recorded.
          </p>
        </>
      )}
    </Panel>
  );
}

export function OperationsActivity() {
  const q = useRead("operations/activity", 30000);
  return (
    <div className="workspace-stack" data-testid="operations-activity">
      <Panel title="Current activity" aside={<Badge>Journal records</Badge>}>
        <Loaded q={q}>
          {(d) => {
            const w = rec(d.window);
            return (
              <>
                <SourceStrip source={String(d.source)} at={d.generated_at} />
                <p>
                  Newest {value(w?.decisions)} decisions ({timestamp(w?.oldest)}{" "}
                  → {timestamp(w?.newest)}): {value(w?.directional)}{" "}
                  directional, {value(w?.executed)} executed.
                </p>
                <SmallTable
                  caption="Risk / skip reasons in this window"
                  data={rows(d.risk_blocks)}
                  columns={[
                    ["reason", "Reason class"],
                    ["n", "Decisions"],
                  ]}
                />
                <p className="quiet">
                  Execution recovery: {value(d.execution_recovery)}
                </p>
                <UnavailableFields items={d.unavailable} />
              </>
            );
          }}
        </Loaded>
      </Panel>
      <div className="workspace-grid">
        <AttentionScan />
        <Panel title="Recent scans referenced by decisions">
          {q.data && (
            <SmallTable
              caption="scan ids in the decision window"
              data={rows(q.data.scans)}
              columns={[
                ["scan_id", "Scan", short],
                ["latest_decision_at", "Latest decision"],
              ]}
            />
          )}
        </Panel>
      </div>
      {q.data && (
        <>
          <Panel title="Strategy signals">
            {rows(q.data.signals).length === 0 ? (
              <p className="quiet">
                No strategy signal is recorded in the newest{" "}
                {value(rec(q.data.window)?.decisions)} decisions.
              </p>
            ) : (
              <RecordTable
                title="Signals"
                rows={rows(q.data.signals)}
                filterKey="strategy_id"
                columns={[
                  ["symbol", "Instrument"],
                  ["strategy_id", "Strategy"],
                  ["action", "Proposed"],
                  ["signal_bar_age_min", "Bar age (min)"],
                  ["skip_reason", "Skip / block"],
                  ["ts", "Recorded at"],
                ]}
              />
            )}
          </Panel>
          <Panel title="Orders (journal trades)">
            <SmallTable
              caption="20 newest journal trades; venue orders are not read here"
              data={rows(q.data.orders)}
              columns={[
                ["symbol", "Instrument"],
                ["side", "Side"],
                ["status", "Status"],
                ["exec_mode", "Exec mode"],
                [
                  "stop_ref_recorded",
                  "Stop ref (journal)",
                  (v) => (v ? "recorded" : "none"),
                ],
                ["opened_at", "Opened"],
              ]}
            />
          </Panel>
          <Panel title="Control events">
            <SmallTable
              caption="30 newest control events"
              data={rows(q.data.control_events)}
              columns={[
                ["ts", "At"],
                ["event", "Event"],
                ["actor", "Actor"],
                ["detail", "Detail"],
              ]}
            />
          </Panel>
        </>
      )}
    </div>
  );
}

// ── Knowledge ───────────────────────────────────────────────────────────────
export function NoteDetail({ id }: { id: string }) {
  const q = useRead(`knowledge/note?id=${encodeURIComponent(id)}`);
  return (
    <section className="note-detail" aria-label="Note body and evidence">
      <h3 className="space-top">Note</h3>
      <Loaded q={q}>
        {(d) => (
          <>
            <details open>
              <summary>
                Body{d.truncated ? " (truncated at 64 kB)" : ""}
              </summary>
              <pre className="note-body" tabIndex={0}>
                {String(d.body ?? "")}
              </pre>
            </details>
            <h4>Evidence and code links</h4>
            {rows(d.sources).length ? (
              <ul className="connections">
                {rows(d.sources).map((s) => (
                  <li key={String(s.path)}>
                    <span>
                      <Badge>{String(s.kind)}</Badge> {String(s.path)}
                    </span>
                    <span className="quiet">
                      {s.exists ? "present in repository" : "not found"}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="quiet">No source paths recorded in the note.</p>
            )}
            <h4>Chronology</h4>
            <Timeline
              events={rows(d.chronology).map((e) => ({
                label: String(e.event),
                at: e.at,
                detail: `${e.source}${e.detail ? ` · ${e.detail}` : ""}`,
              }))}
            />
            {d.git_history !== "available" && (
              <p className="quiet">Git history unavailable.</p>
            )}
          </>
        )}
      </Loaded>
    </section>
  );
}

// ── Diagnostics ─────────────────────────────────────────────────────────────
export function DiagnosticsDetail() {
  const q = useRead("diagnostics", 30000);
  return (
    <Loaded q={q}>
      {(d) => {
        const storage = rec(d.storage);
        const disk = rec(storage?.disk);
        const res = rec(d.resources);
        const host = rec(res?.host);
        const proc = rec(res?.dashboard_process);
        const wd = rec(d.watchdog);
        return (
          <div className="workspace-stack" data-testid="diagnostics-detail">
            <SourceStrip source={String(d.source)} at={d.generated_at} />
            <div className="workspace-grid">
              <Panel title="Storage">
                <SmallTable
                  caption="Data stores (size, last modified)"
                  data={rows(storage?.files)}
                  columns={[
                    ["file", "File"],
                    ["bytes", "Size", bytes],
                    ["modified_at", "Modified"],
                  ]}
                />
                <p>
                  Disk: {bytes(disk?.free)} free of {bytes(disk?.total)}
                </p>
              </Panel>
              <Panel title="Resources">
                <dl className="record-fields">
                  <div>
                    <dt>Host load (1/5/15 min)</dt>
                    <dd>{value(host?.loadavg)}</dd>
                  </div>
                  <div>
                    <dt>CPUs</dt>
                    <dd>{value(host?.cpus)}</dd>
                  </div>
                  <div>
                    <dt>Memory available</dt>
                    <dd>
                      {bytes(host?.mem_available)} of {bytes(host?.mem_total)}
                    </dd>
                  </div>
                  <div>
                    <dt>Dashboard process</dt>
                    <dd>
                      RSS {bytes(proc?.rss)} · {value(proc?.threads)} threads
                    </dd>
                  </div>
                  <div>
                    <dt>This read</dt>
                    <dd>{value(rec(d.api)?.diagnostics_read_ms)} ms</dd>
                  </div>
                </dl>
              </Panel>
            </div>
            <div className="workspace-grid">
              <Panel
                title="Watchdog"
                aside={
                  <Badge tone={wd?.disabled_flag ? "amber" : "neutral"}>
                    {wd?.disabled_flag
                      ? "Disabled flag set"
                      : "No disable flag"}
                  </Badge>
                }
              >
                <p className="quiet">{value(wd?.note)}</p>
                <p>Log modified: {timestamp(wd?.log_modified_at)}</p>
                {Array.isArray(wd?.log_tail) ? (
                  <details>
                    <summary>
                      Inspect {(wd!.log_tail as string[]).length} watchdog log
                      lines
                    </summary>
                    <pre className="log-tail" tabIndex={0}>
                      {(wd!.log_tail as string[]).join("\n")}
                    </pre>
                  </details>
                ) : (
                  <p>Watchdog log unavailable.</p>
                )}
              </Panel>
              <Panel title="Collectors">
                <SmallTable
                  caption="Collector health files"
                  data={rows(d.collectors)}
                  columns={[
                    ["file", "Health file"],
                    ["status", "Status"],
                    ["freshness", "Freshness"],
                    ["updated_at", "Updated"],
                    ["last_error", "Last error"],
                  ]}
                />
              </Panel>
            </div>
            <Panel
              title="Incidents and recovery"
              aside={<Badge>Recorded</Badge>}
            >
              <p className="quiet">
                Execution recovery ledger: {value(d.execution_recovery)}
              </p>
              <RecordTable
                title="Control events"
                rows={rows(d.incidents)}
                filterKey="event"
                columns={[
                  ["ts", "At"],
                  ["event", "Event"],
                  ["from_state", "From"],
                  ["to_state", "To"],
                  ["actor", "Actor"],
                ]}
              />
              <SmallTable
                caption="Owner audit (20 newest)"
                data={rows(d.owner_audit)}
                columns={[
                  ["ts", "At"],
                  ["operation", "Operation"],
                  ["channel", "Channel"],
                  ["status", "Status"],
                  ["state_before", "Before"],
                  ["state_after", "After"],
                ]}
              />
            </Panel>
            <Panel title="Known visibility limitations">
              <UnavailableFields items={d.unavailable} />
            </Panel>
          </div>
        );
      }}
    </Loaded>
  );
}
