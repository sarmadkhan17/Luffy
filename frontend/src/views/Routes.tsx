import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { usePreview } from "../context";
import { Badge, Panel, QueryState } from "../components/ui";
import {
  RecordTable,
  value,
  timestamp,
  SourceStrip,
  Timeline,
  Unavailable,
} from "../components/workspace";
import type { TradeStatusFilter } from "../adapters/contracts";
import { Decisions, RuntimeStatus, Investigations } from "./Activity";
import {
  TradeLineage,
  StrategyDetail,
  ResearchLive,
  DiagnosticsDetail,
} from "./LiveReads";

export const TRADE_PAGE_SIZE = 50;
/** A position in one newest-first traversal: the page's cursor and how many
 * rows earlier pages of this traversal showed. */
type TradeStep = { cursor: string | null; shown: number };
const FIRST: TradeStep[] = [{ cursor: null, shown: 0 }];

function TradeBook() {
  const { adapter } = usePreview();
  const [status, setStatus] = useState<TradeStatusFilter>("all");
  const [steps, setSteps] = useState<TradeStep[]>(FIRST);
  // One traversal = one start from Newest. Its pages are cached apart from any
  // other traversal's, and once a page reports that history changed (or cannot
  // be verified) the notice stays until the owner restarts from Newest.
  const [traversal, setTraversal] = useState(0);
  const [notice, setNotice] = useState<"changed" | "unverifiable" | null>(null);
  const [search, setSearch] = useState("");
  const step = steps[steps.length - 1];
  const q = useQuery({
    queryKey: ["trades", status, step.cursor, traversal],
    queryFn: ({ signal }) =>
      adapter.tradePage!(
        { status, cursor: step.cursor, limit: TRADE_PAGE_SIZE },
        signal,
      ),
    enabled: !!adapter.tradePage,
    // a revisited page (Previous / Newest) is re-read at once; its cached
    // read stays on screen, labelled with its read time, until then
    staleTime: 0,
    refetchInterval: 30000,
  });
  // Only a response for the page on screen is ever rendered.
  const d =
    q.data && q.data.status === status && q.data.cursor === step.cursor
      ? q.data
      : undefined;
  const reported = d?.history.changed;
  useEffect(() => {
    if (reported === true) setNotice("changed");
    else if (reported === null) setNotice((n) => n ?? "unverifiable");
  }, [reported, d]);
  if (!adapter.tradePage)
    return (
      <Unavailable title="Trade book">
        Trade history is served by the LIVE journal endpoint only.
      </Unavailable>
    );
  const newest = () => {
    setSteps(FIRST);
    setTraversal((t) => t + 1);
    setNotice(null);
  };
  // Unchanged history: rows beyond those shown are trades newer than page 1.
  const warning =
    notice ??
    (reported === true ? "changed" : reported === null ? "unverifiable" : null);
  const newer = d && !warning ? d.preceding - step.shown : 0;
  const scope = status === "all" ? "trades" : `${status} trades`;
  return (
    <Panel
      title="Trade book"
      aside={<Badge tone="amber">Journal records · partial</Badge>}
    >
      <div className="workspace-toolbar">
        <label>
          Status (server-side, full history)
          <select
            aria-label="Filter Trades"
            value={status}
            onChange={(e) => {
              setStatus(e.target.value as TradeStatusFilter);
              newest();
            }}
          >
            <option value="all">All</option>
            <option value="open">open</option>
            <option value="closed">closed</option>
          </select>
        </label>
      </div>
      <div
        className="paging-bar"
        role="group"
        aria-label="Trade history paging"
      >
        <span data-testid="trade-range">
          {!d
            ? `Page ${steps.length} · newest first · up to ${TRADE_PAGE_SIZE} per page.`
            : d.rows.length
              ? `Records ${d.preceding + 1}–${d.preceding + d.rows.length} of ${d.total} ${scope} · page ${steps.length} · newest first` +
                (d.first && d.last
                  ? ` · opened ${timestamp(d.first.opened_at)} → ${timestamp(d.last.opened_at)}`
                  : "") +
                ` · up to ${d.limit} per page. Positions as of the read time.`
              : `No ${scope} on page ${steps.length} (${d.total} in total).`}
        </span>
        <div>
          <button
            type="button"
            disabled={steps.length === 1 && !warning}
            onClick={newest}
          >
            Newest
          </button>
          <button
            type="button"
            disabled={steps.length === 1}
            onClick={() => setSteps(steps.slice(0, -1))}
          >
            Previous page
          </button>
          <button
            type="button"
            disabled={!d?.hasMore || !d.nextCursor || q.isFetching}
            onClick={() =>
              d &&
              setSteps([
                ...steps,
                { cursor: d.nextCursor, shown: step.shown + d.rows.length },
              ])
            }
          >
            Next page
          </button>
        </div>
      </div>
      <QueryState
        error={q.error}
        loading={!q.error && !d}
        retry={() => void q.refetch()}
      />
      {q.error && steps.length > 1 && (
        <button type="button" onClick={newest}>
          Return to newest trades
        </button>
      )}
      {warning && (
        <p
          className="history-warning"
          role="status"
          data-testid="history-changed"
        >
          {warning === "changed"
            ? "Trade history changed while you were paging. Restart from Newest for a complete current view."
            : "This traversal began before history-change checking, so changes to earlier pages cannot be ruled out. Restart from Newest for a complete current view."}
        </p>
      )}
      {d && !q.error && (
        <>
          <SourceStrip
            source={d.source}
            at={d.generatedAt}
            note="Keyset pages over the full journal history, newest first by opening time. The status filter is applied by the server; search covers only the loaded page."
          />
          {newer > 0 && (
            <p className="quiet" role="status" data-testid="history-newer">
              {newer} newer matching trade(s) were booked after this traversal
              began; earlier pages are unchanged. Newest shows them.
            </p>
          )}
          <RecordTable
            title="Trades"
            rows={d.rows}
            search={search}
            onSearch={setSearch}
            searchNote="Search this loaded page…"
            columns={[
              ["symbol", "Instrument"],
              ["side", "Side"],
              ["status", "Status"],
              ["realized_pnl", "Realized P&L (USDT)"],
              ["strategy_name", "Strategy"],
              ["opened_at", "Opened"],
            ]}
            detail={(r) => (
              <>
                <Timeline
                  events={[
                    { label: "Journal entry", at: r.opened_at },
                    {
                      label: "Journal exit",
                      at: r.closed_at,
                      detail:
                        typeof r.close_reason === "string"
                          ? r.close_reason
                          : undefined,
                    },
                  ]}
                />
                <p className="quiet">
                  Entry and exit are journal records. The journal stop id is a
                  journal record, not venue verification.
                </p>
                <SourceStrip source={d.source} at={d.generatedAt} />
                {typeof r.id === "string" && <TradeLineage id={r.id} />}
              </>
            )}
          />
        </>
      )}
    </Panel>
  );
}

function Trades() {
  return (
    <div className="workspace-stack">
      <TradeBook />
      <Decisions />
      <div className="workspace-grid">
        <Panel title="Execution lineage">
          <p>
            Inspect a trade for its recorded decision, strategy, accounting
            receipts and outcome. Links are the ids the journal stored; nothing
            is joined by inference, and what was not recorded is marked
            UNAVAILABLE.
          </p>
        </Panel>
        <Panel title="Open position protection">
          <p>
            Overview binds journal-open positions to the Supervisor’s
            timestamped venue reconciliation and optional price estimates.
          </p>
          <a href="#overview">Inspect positions and protection ↗</a>
        </Panel>
      </div>
    </div>
  );
}

function Strategies() {
  const { adapter } = usePreview();
  const q = useQuery({
    queryKey: ["table", "strategies"],
    queryFn: ({ signal }) => adapter.table!("strategies", signal),
    enabled: !!adapter.table,
    refetchInterval: 30000,
  });
  return (
    <div className="workspace-stack">
      <Panel
        title="Strategy registry"
        aside={<Badge tone="amber">Journal records · partial</Badge>}
      >
        <QueryState
          error={q.error}
          loading={q.isPending}
          retry={() => void q.refetch()}
        />
        {q.data && !q.error && (
          <>
            <SourceStrip
              source={q.data.source}
              at={q.data.generatedAt}
              note="Up to 200 strategies; registry state does not establish admission."
            />
            <RecordTable
              title="Strategies"
              rows={q.data.rows}
              filterKey="state"
              columns={[
                ["name", "Identity"],
                ["state", "Lifecycle"],
                ["kind", "Kind"],
                ["origin", "Origin"],
                ["generation", "Generation"],
                ["retire_reason", "Retirement evidence"],
              ]}
              detail={(r) => (
                <>
                  <div className="strategy-detail">
                    <h3>{value(r.name)}</h3>
                    <Badge>{value(r.state)}</Badge>
                    <dl>
                      <dt>Registry ID</dt>
                      <dd>{value(r.id)}</dd>
                      <dt>Kind / origin</dt>
                      <dd>
                        {value(r.kind)} / {value(r.origin)}
                      </dd>
                      <dt>Retirement reason</dt>
                      <dd>{value(r.retire_reason)}</dd>
                    </dl>
                    <h3>Recorded lifecycle</h3>
                  </div>
                  <Timeline
                    events={[{ label: "Registry creation", at: r.created_at }]}
                  />
                  <p className="quiet">
                    Registry identity is not a versioned approval.
                  </p>
                  <SourceStrip source={q.data.source} at={q.data.generatedAt} />
                  {typeof r.id === "string" && <StrategyDetail id={r.id} />}
                </>
              )}
            />
          </>
        )}
      </Panel>
      <div className="workspace-grid">
        <Unavailable title="Health, capacity & allocation">
          No rolling health, capacity or per-strategy allocation record is
          stored. A registry state of active does not establish current health.
          Journal economics per strategy are in each strategy's record.
        </Unavailable>
        <Unavailable title="Research & authority">
          Approvals, parent/child relationships and research linkage are not
          exposed. TESTED does not mean deployed; this surface grants no
          strategy authority.
        </Unavailable>
      </div>
    </div>
  );
}
function Research() {
  return (
    <div className="workspace-stack">
      <ResearchLive />
      <Panel title="Interpretation boundaries">
        <p>
          <Badge>INCONCLUSIVE</Badge> <Badge>NOT_ASSESSED</Badge>{" "}
          <Badge>NOT_ESTABLISHED</Badge> <Badge>context_only</Badge>
        </p>
        <p>
          These are distinct evidence states, not failed or successful
          deployment gates. Discovery ranks candidates; it does not grant
          admission. No results, run progress, cost totals or approval actions
          are fabricated.
        </p>
      </Panel>
    </div>
  );
}
function Diagnostics() {
  const { adapter, session } = usePreview();
  const q = useQuery({
    queryKey: ["logs"],
    queryFn: ({ signal }) => adapter.logs!(signal),
    enabled: !!adapter.logs,
  });
  const oi = useQuery({
    queryKey: ["owner-interface"],
    queryFn: ({ signal }) => adapter.ownerInterface!(signal),
    enabled: !!adapter.ownerInterface,
    staleTime: 0,
    refetchInterval: 30000,
  });
  return (
    <div className="workspace-stack">
      <RuntimeStatus />
      <Investigations />
      <div className="workspace-grid">
        <Panel title="Dashboard & session">
          <Badge>Authenticated browser session</Badge>
          <dl className="record-fields">
            <div>
              <dt>Backend commit</dt>
              <dd>{session?.backend.commit ?? "Unavailable"}</dd>
            </div>
            <div>
              <dt>Frontend build</dt>
              <dd>{session?.backend.frontend_build ?? "Unavailable"}</dd>
            </div>
            <div>
              <dt>Session expiry</dt>
              <dd>{timestamp(session?.session.expires_at)}</dd>
            </div>
          </dl>
          <p className="quiet">
            Bootstrap identity is not a current health probe. Runtime evidence
            above is polled separately.
          </p>
        </Panel>
        <Panel title="Owner Interface">
          <QueryState
            error={oi.error}
            loading={oi.isPending}
            retry={() => void oi.refetch()}
          />
          {oi.data && !oi.error && (
            <>
              <Badge
                tone={oi.data.availability === "AVAILABLE" ? "mint" : "amber"}
              >
                {oi.data.availability}
              </Badge>
              <SourceStrip
                source="Owner Interface health response"
                at={oi.data.observedAt}
              />
              <p>
                Recovery:{" "}
                {oi.data.recoveryInProgress === null
                  ? "Unavailable"
                  : oi.data.recoveryInProgress
                    ? "IN_PROGRESS"
                    : "Not reported in progress"}
              </p>
              <p>{oi.data.reasons.join(", ")}</p>
            </>
          )}
        </Panel>
      </div>
      <Panel
        title="Historical log evidence"
        aside={<Badge>Not current health</Badge>}
      >
        <p className="quiet">
          Bounded log tail; warnings may be historical. A quiet tail does not
          prove a healthy system.
        </p>
        <QueryState
          error={q.error}
          loading={q.isPending}
          retry={() => void q.refetch()}
        />
        {q.data && !q.error && (
          <>
            <SourceStrip
              source={q.data.source}
              note={`File modification: ${timestamp(q.data.observedAt)}`}
            />
            {q.data.lines === null ? (
              <p>Log unavailable ({q.data.error}).</p>
            ) : (
              <details>
                <summary>Inspect {q.data.lines.length} log lines</summary>
                <pre className="log-tail" tabIndex={0} aria-label="Log lines">
                  {q.data.lines.join("\n")}
                </pre>
              </details>
            )}
          </>
        )}
        <button onClick={() => void q.refetch()} disabled={q.isFetching}>
          Refresh logs
        </button>
      </Panel>
      <DiagnosticsDetail />
    </div>
  );
}
export default function Routes({ route }: { route: string }) {
  if (route === "trades") return <Trades />;
  if (route === "strategies") return <Strategies />;
  if (route === "diagnostics") return <Diagnostics />;
  return <Research />;
}
