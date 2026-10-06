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
import { Investigations } from "./Activity";
import {
  Fields,
  Loaded,
  RecordLink,
  SmallTable,
  StrategyRef,
  UnavailableFields,
  bytes,
  rec,
  rows,
  short,
  useRead,
  type R,
} from "../components/records";
import { Distribution, Figure } from "../components/product";
import {
  ControlEventsPager,
  DiagnosticsProbes,
  NoteRecords,
  OperationsTimeline,
  StrategyEvidence,
  TradeChain,
  CycleFields,
  CycleVotes,
  StageSection,
} from "./Evidence";
import { PagingBar } from "../components/workspace";
export { UnavailableFields };

// ── Trades ──────────────────────────────────────────────────────────────────
export function TradeLineage({
  id,
  story = false,
}: {
  id: string;
  /** Rendered as the route's trade story (not inside a drawer). */
  story?: boolean;
}) {
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
              {!story && (
                <p>
                  <RecordLink kind="trade" id={id}>
                    Open the full trade story ↗
                  </RecordLink>
                </p>
              )}
              <TradeChain d={d} />
              <div className="stage-columns">
                <StageSection step="market_context" title="Market context">
                  {rec(d.cycle) ? (
                    <CycleFields d={d} />
                  ) : (
                    <p className="quiet">No decision cycle recorded.</p>
                  )}
                </StageSection>
                <StageSection step="decision" title="Decision">
                  <Fields
                    row={decision}
                    keys={[
                      [
                        "id",
                        "Decision id",
                        (v) =>
                          typeof v === "string" ? (
                            <RecordLink kind="decision" id={v} />
                          ) : (
                            "Unavailable"
                          ),
                      ],
                      ["ts", "Recorded at"],
                      ["action", "Action"],
                      ["score", "Score"],
                      ["threshold", "Threshold"],
                      ["scan_id", "Attention scan (opportunity ref)"],
                      ["strategy_ids", "Strategy ids"],
                      ["meta_p", "Meta-label p"],
                    ]}
                  />
                </StageSection>
              </div>
              <StageSection step="signals" title="Signals & votes">
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
                <CycleVotes d={d} />
              </StageSection>
              <div className="stage-columns">
                <StageSection
                  step="strategy"
                  title="Strategy (current registry row)"
                >
                  <Fields
                    row={strategy}
                    keys={[
                      [
                        "id",
                        "Strategy id",
                        (v) =>
                          typeof v === "string" ? (
                            <RecordLink kind="strategy" id={v} />
                          ) : (
                            "Unavailable"
                          ),
                      ],
                      ["name", "Name"],
                      ["state", "State now"],
                      ["generation", "Generation"],
                      ["parent_id", "Parent"],
                      ["spec_sha256", "Spec sha256"],
                      ["spec_sha256_basis", "Hash basis"],
                    ]}
                  />
                </StageSection>
                <StageSection step="execution" title="Trade record (journal)">
                  <Fields
                    row={trade}
                    keys={[
                      ["id", "Trade id"],
                      ["symbol", "Instrument"],
                      ["side", "Side"],
                      ["amount", "Quantity"],
                      ["entry_price", "Entry price"],
                      ["exit_price", "Exit price"],
                      ["notional_usdt", "Entry notional (USDT)"],
                      ["leverage", "Leverage"],
                      ["opened_at", "Opened"],
                      ["closed_at", "Closed"],
                      ["close_reason", "Close reason"],
                      ["realized_pnl", "Realized P&L (USDT, journal-booked)"],
                      [
                        "exec_mode",
                        "Exec mode (live = orders sent to the demo venue)",
                      ],
                      ["stop_loss", "Journal stop"],
                    ]}
                  />
                </StageSection>
              </div>
              <StageSection step="accounting" title="Accounting receipts">
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
              </StageSection>
              <StageSection step="outcome" title="Outcome and excursion">
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
              </StageSection>
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
          const econ = rec(d.journal_economics);
          return (
            <>
              {typeof d.name === "string" && (
                <div className="identity-head">
                  <strong>{d.name}</strong>
                  {typeof d.state === "string" && (
                    <Badge tone="steel">registry state: {d.state}</Badge>
                  )}
                  <span className="quiet mono">{id}</span>
                </div>
              )}
              <SourceStrip source={String(d.source)} at={d.generated_at} />
              <div className="figure-row" aria-label="Journal economics">
                {econ ? (
                  <>
                    <Figure label="Journal trades" value={value(econ.trades)} />
                    <Figure label="Open" value={value(econ.open)} />
                    <Figure label="Closed" value={value(econ.closed)} />
                    <Figure
                      label="Winning closes"
                      value={value(econ.wins)}
                    />
                    <Figure
                      label="Realized P&L"
                      value={
                        typeof econ.realized_pnl === "number"
                          ? `${econ.realized_pnl.toFixed(2)} USDT`
                          : "Unavailable"
                      }
                      note="journal-booked, not venue-verified"
                    />
                  </>
                ) : (
                  <p className="quiet">No journal economics recorded.</p>
                )}
              </div>
              <p className="quiet">
                Registry stats (strategy row): {value(d.registry_stats)}
              </p>
              <div className="split">
                <div>
                  <h4>Identity</h4>
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
                  {!!(d.hypothesis || d.invalidation) && (
                    <Fields
                      row={d}
                      keys={[
                        ["hypothesis", "Hypothesis (as recorded)"],
                        ["invalidation", "Invalidation (as recorded)"],
                      ]}
                    />
                  )}
                  {rec(d.spec_full) && (
                    <details>
                      <summary>Full declared spec (current registry row)</summary>
                      <pre className="note-body" tabIndex={0}>
                        {JSON.stringify(d.spec_full, null, 2)}
                      </pre>
                    </details>
                  )}
                </div>
                <div>
                  <h4>Lifecycle</h4>
                  <Timeline
                    events={rows(d.lifecycle).map((e) => {
                      const detail = e.detail ? value(e.detail) : "";
                      return {
                        label: String(e.event),
                        at: e.at,
                        detail: `${e.source}${detail ? ` · ${detail.length > 240 ? `${detail.slice(0, 240)}…` : detail}` : ""}`,
                      };
                    })}
                  />
                </div>
              </div>
              <StrategyEvidence d={d} />
              <h4>Recent trades</h4>
              <SmallTable
                caption="Journal trades for this strategy id (20 newest)"
                data={rows(d.recent_trades)}
                empty="No journal trade records this strategy id."
                columns={[
                  [
                    "id",
                    "Trade",
                    (v) => <RecordLink kind="trade" id={String(v)} />,
                  ],
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
const hashLink = (v: unknown) =>
  typeof v === "string" && v ? (
    <RecordLink kind="research" id={v}>
      {short(v)}
    </RecordLink>
  ) : (
    "Unavailable"
  );

/** How the lab's records relate, with what this read loaded for each. A
 * station without a record store is shown UNAVAILABLE, not empty. */
function LabMap({ d, missing }: { d: R; missing: Map<string, string> }) {
  const results = rows(d.results);
  const seeded = results.filter((r) => rec(r.seed_strategy)).length;
  const station = (
    key: string,
    title: string,
    n: number | null,
    note: string,
  ) => (
    <li
      key={key}
      className={`lab-station ${n === null ? "unavailable" : ""}`}
      data-station={key}
    >
      <span className="lab-count">{n === null ? "—" : n}</span>
      <strong>{title}</strong>
      <small>
        {n === null ? `UNAVAILABLE · ${missing.get(key) ?? note}` : note}
      </small>
    </li>
  );
  return (
    <div className="lab-map">
      <h4>Lab relationships · loaded in this read</h4>
      <ol aria-label="Research record relationships">
        {station(
          "questions",
          "Questions & plans",
          null,
          "no question or plan store is listed by the backend",
        )}
        {station(
          "ideas",
          "Assessed ideas",
          rows(d.ideas).length,
          "harvested ideas the writer consumed",
        )}
        {station(
          "results",
          "Results",
          results.length,
          "scored combinations on this page (parent → children)",
        )}
        {station(
          "candidates",
          "Candidate bank",
          rows(d.candidates).length,
          "carried between gates",
        )}
        {station(
          "registrations",
          "Registrations",
          rows(d.registrations).length,
          "registered gate looks",
        )}
        {station(
          "seeded",
          "Seeded by a strategy",
          seeded,
          "results on this page with trigger seed:<id>",
        )}
      </ol>
      <p className="quiet">
        Counts are rows returned by this read, not totals or admission.
      </p>
    </div>
  );
}

type Section = {
  title: string;
  note: string;
  count?: number;
  unavailable?: string;
  render?: () => ReactNode;
};
export function ResearchLive() {
  const [offset, setOffset] = useState(0);
  const q = useRead(
    offset ? `research?limit=50&offset=${offset}` : "research",
    60000,
  );
  const [pick, setPick] = useState(0);
  const page = rec(q.data?.results_page);
  const questionPage = rec(q.data?.questions_page);
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
      count: rows(d?.questions).length,
      unavailable: missing.has("questions") ? gap("questions") : undefined,
      render: () => <>
        <PagingBar count={rows(d?.questions).length} limit={typeof questionPage?.limit === "number" ? questionPage.limit : 50}
          offset={offset} hasMore={questionPage?.has_more === true} onPage={questionPage ? setOffset : undefined} busy={q.isFetching} />
        {rows(d?.questions).map(r => <details key={String(r.record_id)}><summary>{value(r.record_id)} · recorded question</summary>
          <a href={`#luffy?query=research&id=${encodeURIComponent(String(r.identity))}`}>Discuss exact evidence in LUFFY ↗</a>
          <pre style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{JSON.stringify(r,null,2)}</pre>
        </details>)}
        {rows(d?.questions).length === 0 && !missing.has("questions") && <p>No questions on this page of the available store.</p>}
      </>,
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
        <>
          <PagingBar
            count={rows(d?.results).length}
            limit={typeof page?.limit === "number" ? page.limit : 50}
            offset={offset}
            hasMore={page?.has_more === true}
            onPage={page ? setOffset : undefined}
            busy={q.isFetching}
          />
          <SmallTable
            caption="research_combos · newest first · open a result for its evidence"
            data={rows(d?.results)}
            columns={[
              [
                "hash",
                "Result",
                (v, r) => (
                  <RecordLink kind="research" id={String(v)}>
                    {String(r.label ?? v)}
                  </RecordLink>
                ),
              ],
              ["verdict", "Verdict"],
              ["status", "Status"],
              ["reason", "Reason"],
              ["median_pf", "Median PF"],
              ["trades", "Trades"],
              [
                "seed_strategy",
                "Seeded by",
                (v) => (rec(v) ? <StrategyRef s={v} /> : "—"),
              ],
              ["created_at", "Recorded"],
            ]}
          />
        </>
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
              ["hash", "Hash", hashLink],
              ["state", "State"],
              ["gate1", "Gate 1"],
              ["gate3", "Gate 3"],
              ["reason", "Reason"],
              ["updated_at", "Updated"],
            ]}
          />
          <SmallTable
            caption="Assessed ideas: harvested ideas the strategy writer consumed, with its recorded outcome"
            data={rows(d?.ideas)}
            empty="No idea_consumed record in the brain event window."
            columns={[
              ["ts", "At"],
              ["idea_id", "Idea"],
              ["name", "Name"],
              ["outcome", "Outcome"],
              ["stream", "Stream"],
              [
                "spec",
                "Spec",
                (v, r) =>
                  typeof v !== "string" ? (
                    "none recorded"
                  ) : (
                    <StrategyRef
                      s={{ id: v, in_registry: r.spec_in_registry }}
                    />
                  ),
              ],
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
            ["hash", "Hash", hashLink],
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
        className="tier-primary"
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
            <div data-testid="research-ledger">
              <SourceStrip source={String(d.source)} at={d.generated_at} />
              {d.available === false ? (
                <p>{value(d.reason)}</p>
              ) : (
                <div className="split">
                  <div>
                    <h4>Recorded results by verdict · status</h4>
                    {rows(counts?.results_by_verdict).length ? (
                      <Distribution
                        label="Recorded results by verdict"
                        rows={rows(counts?.results_by_verdict).map((r, i) => ({
                          key: String(i),
                          label: `${value(r.verdict)} · ${value(r.status)}`,
                          n: typeof r.n === "number" ? r.n : 0,
                        }))}
                      />
                    ) : (
                      <p className="quiet">No result is recorded.</p>
                    )}
                  </div>
                  <LabMap d={d} missing={missing} />
                </div>
              )}
              <p className="quiet space-top">
                Runs recorded: {value(rec(counts?.runs)?.n)} (failed:{" "}
                {value(rec(counts?.runs)?.failed)}) · registered tests:{" "}
                {value(counts?.registered_tests)}.{" "}
                {Array.isArray(d.notes) && (d.notes as string[]).join(". ")}
              </p>
            </div>
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
      title="Attention admission and investigation observations"
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
            row={rec(q.data.admission) ?? {}}
            keys={[
              ["status", "Admission receipt"],
              ["broad_observed", "Broad crypto observed"],
              ["peer_cohort", "Strategy peer cohort"],
              ["deep_admitted", "Discretionary deep admitted"],
              ["event_admitted", "Event admitted"],
              ["exploration_admitted", "Exploration admitted"],
              ["exposure_required", "Exposure required (outside budget)"],
              ["unused_capacity", "Unused deep capacity"],
              ["degraded_state", "Admission data state"],
            ]}
          />
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
              ["scan_id", "Investigation observation id"],
              ["as_of_ms", "As of (ms)"],
              ["age_seconds", "Age (s)"],
              ["causes", "Captured decision causes"],
              ["collector_status", "Collector"],
            ]}
          />
          <p className="quiet">
            Source: /api/attention/latest. A status of disabled or waiting means
            no current investigation capture is recorded. Admission has its own retained receipt; holdings and protection are independent of deep slots.
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
      {q.data && !q.error && <OperationsTimeline d={q.data} />}
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
                detail={(r) =>
                  typeof r.decision_id === "string" ? (
                    <p>
                      <RecordLink kind="decision" id={r.decision_id}>
                        Open decision {r.decision_id} ↗
                      </RecordLink>
                    </p>
                  ) : null
                }
              />
            )}
          </Panel>
          <Panel title="Orders (journal trades)">
            <SmallTable
              caption="20 newest journal trades; venue orders are not read here"
              data={rows(q.data.orders)}
              columns={[
                [
                  "id",
                  "Trade",
                  (v) => <RecordLink kind="trade" id={String(v)} />,
                ],
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
              caption="30 newest control events (signal cooldowns counted, not listed)"
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
            <NoteRecords d={d} />
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
/** Owner summary of the diagnostics read: each cell restates a returned
 * value (probe results, collector statuses, watchdog flag, disk, memory,
 * recovery ledger). Nothing is scored or inferred. */
function DiagnosticsBoard({ d }: { d: R }) {
  const probes = rows(d.probes);
  const failed = probes.filter((p) => !p.ok).length;
  const collectors = new Map<string, number>();
  for (const c of rows(d.collectors)) {
    const k = `${value(c.freshness)} · last reported ${value(c.status)}`;
    collectors.set(k, (collectors.get(k) ?? 0) + 1);
  }
  const disk = rec(rec(d.storage)?.disk);
  const host = rec(rec(d.resources)?.host);
  const wd = rec(d.watchdog);
  return (
    <section
      className="panel tier-primary diag-board"
      aria-label="Diagnostics summary"
      data-testid="diagnostics-board"
    >
      <Figure
        label="Store probes · this read"
        value={
          probes.length
            ? `${probes.length - failed} of ${probes.length} OK`
            : "Unavailable"
        }
        tone={probes.length ? (failed ? "rose" : "mint") : undefined}
        note={failed ? `${failed} FAILED` : "measured now, no history"}
      />
      <Figure
        label="Collectors"
        small
        value={
          collectors.size
            ? [...collectors.entries()].map(([k, n]) => `${n} ${k}`).join(" · ")
            : "Unavailable"
        }
        note="Historical health-file reports; process activity is not established"
      />
      <Figure
        label="Watchdog"
        small
        value={
          wd ? (wd.disabled_flag ? "Disable flag set" : "No disable flag") : "Unavailable"
        }
        tone={wd?.disabled_flag ? "amber" : undefined}
        note={`log modified ${timestamp(wd?.log_modified_at)}`}
      />
      <Figure
        label="Disk free"
        small
        value={disk ? `${bytes(disk.free)} of ${bytes(disk.total)}` : "Unavailable"}
      />
      <Figure
        label="Host memory available"
        small
        value={
          host
            ? `${bytes(host.mem_available)} of ${bytes(host.mem_total)}`
            : "Unavailable"
        }
        note={`load ${value(host?.loadavg)}`}
      />
      <Figure
        label="Execution recovery ledger"
        small
        value={value(d.execution_recovery)}
      />
    </section>
  );
}

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
            <DiagnosticsBoard d={d} />
            <SourceStrip source={String(d.source)} at={d.generated_at} />
            <DiagnosticsProbes d={d} />
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
                    ["status", "Last reported status"],
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
            <ControlEventsPager />
            <Panel title="Known visibility limitations">
              <UnavailableFields items={d.unavailable} />
            </Panel>
          </div>
        );
      }}
    </Loaded>
  );
}
