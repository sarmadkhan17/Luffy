/** M2 connected evidence: the trade story chain, decision and research item
 * records, strategy workspace evidence, Operations timeline, Overview activity,
 * observed system flows, note record links and diagnostics probes. Every link
 * is a recorded id returned by the backend; what a store does not record is
 * shown as UNAVAILABLE with the backend's reason. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { usePreview } from "../context";
import { Badge, Panel, QueryState } from "../components/ui";
import { SourceStrip, timestamp, value } from "../components/workspace";
import {
  Fields,
  Loaded,
  RecordLink,
  RefLink,
  SmallTable,
  StrategyRef,
  UnavailableFields,
  rec,
  rows,
  short,
  useRead,
  type R,
} from "../components/records";
import { unprovenEdges } from "../adapters/readContracts";
import { LineageStrip, truthClass } from "../components/product";

const enc = encodeURIComponent;
const num = (v: unknown, digits = 2) =>
  typeof v === "number" && Number.isFinite(v) ? v.toFixed(digits) : value(v);
const usdt = (v: unknown) =>
  typeof v === "number" && Number.isFinite(v)
    ? `${v.toFixed(2)} USDT`
    : "Unavailable";

const STEP_LABEL: Record<string, string> = {
  opportunity: "Opportunity / scan",
  market_context: "Market context",
  decision: "Decision",
  signals: "Signals & votes",
  strategy: "Strategy",
  execution: "Execution (journal)",
  accounting: "Accounting receipts",
  outcome: "Outcome",
};
const stepTone = (s: unknown) =>
  s === "recorded" || s === "verified"
    ? "mint"
    : s === "open"
      ? "neutral"
      : "amber";

/** A record opened from a link: scrolled into view, with a way back. */
export function LinkedPanel({
  title,
  back,
  children,
  testid,
}: {
  title: string;
  back: string;
  children: ReactNode;
  testid: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView?.({ block: "start" });
  }, [title]);
  return (
    <div ref={ref} className="linked-wrap" data-testid={testid}>
      <Panel
        title={title}
        className="linked-record"
        aside={
          <a className="text-link" href={back}>
            Close
          </a>
        }
      >
        {children}
      </Panel>
    </div>
  );
}

// ── Trades ──────────────────────────────────────────────────────────────────
/** The ordered chain opportunity → … → outcome as a stage rail. Each stage
 * shows the status the backend recorded for it, never an inferred one. */
export const STAGE_ORDER = [
  "opportunity",
  "market_context",
  "decision",
  "signals",
  "strategy",
  "execution",
  "accounting",
  "outcome",
];
export const stageNum = (step: string) => {
  const i = STAGE_ORDER.indexOf(step);
  return i < 0 ? "··" : String(i + 1).padStart(2, "0");
};
export function TradeChain({ d }: { d: R }) {
  const chain = rows(d.chain);
  if (!chain.length) return null;
  return (
    <ol
      className="trade-chain stage-rail"
      data-testid="trade-chain"
      aria-label="Trade stages"
      style={{ ["--stages" as string]: chain.length }}
    >
      {chain.map((s) => (
        <li
          key={String(s.step)}
          data-step={String(s.step)}
          className={truthClass(s.status)}
        >
          <span className="stage-num">{stageNum(String(s.step))}</span>
          <strong>{STEP_LABEL[String(s.step)] ?? String(s.step)}</strong>
          <Badge tone={stepTone(s.status)}>
            {String(s.status).toUpperCase()}
          </Badge>
          {s.at ? <time>{timestamp(s.at)}</time> : null}
          <span className="stage-summary">
            {typeof s.summary === "string" ? s.summary : ""}
          </span>
          {s.ref ? <RefLink r={s.ref} /> : null}
        </li>
      ))}
    </ol>
  );
}
export function CycleFields({ d }: { d: R }) {
  const cycle = rec(d.cycle);
  if (!cycle) return null;
  return (
    <Fields
      row={cycle}
      keys={[
        ["id", "Cycle id"],
        ["ts", "Cycle at"],
        ["regime", "Regime"],
        ["adx", "ADX"],
        ["btc_trend", "BTC trend"],
        ["price", "Price"],
        ["mode", "Mode"],
      ]}
    />
  );
}
export function CycleVotes({ d }: { d: R }) {
  const basis = rec(d.votes_basis);
  if (!basis) return null;
  return (
    <SmallTable
      caption={`Analyst votes · votes · ${String(basis.join)} · ±${String(basis.window_s)} s window`}
      data={rows(d.votes)}
      empty={`No vote of this cycle within the read window (${String(basis.status)}).`}
      columns={[
        ["agent", "Analyst"],
        ["side", "Side"],
        ["conviction", "Conviction"],
        ["confidence", "Confidence"],
        ["rationale", "Rationale"],
      ]}
    />
  );
}
/** Chain, market context and the analyst votes of the decision's cycle. */
export function TradeContext({ d }: { d: R }) {
  return (
    <>
      <TradeChain d={d} />
      {rec(d.cycle) && (
        <>
          <h4>Market context (decision cycle)</h4>
          <CycleFields d={d} />
        </>
      )}
      {rec(d.votes_basis) && (
        <>
          <h4>Analyst votes (decision cycle)</h4>
          <CycleVotes d={d} />
        </>
      )}
    </>
  );
}

/** A record section numbered to its forensic stage. */
export function StageSection({
  step,
  title,
  children,
}: {
  step: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="stage-section" data-stage={step}>
      <h4>
        <span className="stage-num">{stageNum(step)}</span> {title}
      </h4>
      {children}
    </section>
  );
}

// ── Decisions ───────────────────────────────────────────────────────────────
export function DecisionDetail({ id }: { id: string }) {
  const q = useRead(`decisions/${enc(id)}`);
  return (
    <LinkedPanel
      title={`Decision ${short(id)}`}
      back="#operations"
      testid="decision-detail"
    >
      <Loaded q={q}>
        {(d) => {
          const dec = rec(d.decision);
          return (
            <>
              <SourceStrip source={String(d.source)} at={d.generated_at} />
              <Fields
                row={dec}
                keys={[
                  ["id", "Decision id"],
                  ["ts", "Recorded at"],
                  ["symbol", "Instrument"],
                  ["action", "Action"],
                  ["score", "Score"],
                  ["threshold", "Threshold"],
                  ["executed", "Executed", (v) => (v ? "yes" : "no")],
                  ["skip_reason", "Skip / risk reason"],
                  ["scan_id", "Attention scan id"],
                  ["meta_p", "Meta-label p"],
                ]}
              />
              <h4>Strategies named by the decision</h4>
              <ul className="connections">
                {rows(d.strategies).length ? (
                  rows(d.strategies).map((s) => (
                    <li key={String(s.id)}>
                      <StrategyRef s={s} />
                    </li>
                  ))
                ) : (
                  <li className="quiet">No strategy id recorded.</li>
                )}
              </ul>
              <SmallTable
                caption="Strategy signals recorded with the decision"
                data={rows(dec?.signals)}
                columns={[
                  ["strategy_id", "Strategy"],
                  ["action", "Action"],
                  ["confidence", "Confidence"],
                  ["rationale", "Rationale"],
                ]}
              />
              <TradeContext d={{ ...d, chain: [] }} />
              <h4>Trades opened from this decision</h4>
              <SmallTable
                caption="journal trades whose decision_id is this decision"
                data={rows(d.trades)}
                empty="No journal trade records this decision id."
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
                ]}
              />
              <h4>Forward outcome</h4>
              <Fields
                row={rec(d.outcome)}
                keys={[
                  ["fwd_ret_1h", "Forward return 1h"],
                  ["fwd_ret_4h", "Forward return 4h"],
                  ["fwd_ret_24h", "Forward return 24h"],
                  ["resolved_at", "Resolved at"],
                ]}
              />
              <UnavailableFields items={d.unavailable} />
            </>
          );
        }}
      </Loaded>
    </LinkedPanel>
  );
}

// ── Research ────────────────────────────────────────────────────────────────
export function ResearchItem({ hash }: { hash: string }) {
  const q = useRead(`research/combos/${enc(hash)}`);
  return (
    <LinkedPanel
      title={`Research result ${hash}`}
      back="#research"
      testid="research-item"
    >
      <Loaded q={q}>
        {(d) => {
          const item = rec(d.item);
          const seed = rec(item?.seed_strategy);
          return (
            <>
              <SourceStrip source={String(d.source)} at={d.generated_at} />
              <p className="quiet">
                {Array.isArray(d.notes) && (d.notes as string[]).join(". ")}
              </p>
              <Fields
                row={item}
                keys={[
                  ["hash", "Hash"],
                  ["label", "Combination"],
                  ["tf", "Timeframe"],
                  ["geo", "Exit geometry"],
                  ["round", "Round"],
                  ["trigger", "Trigger"],
                  ["window", "Window"],
                  ["status", "Status"],
                  ["verdict", "Verdict"],
                  ["reason", "Terminal reasoning"],
                  ["consistency_p", "Consistency p"],
                  ["median_pf", "Median PF"],
                  ["trades", "Trades"],
                  ["scored_symbols", "Scored symbols"],
                  ["created_at", "Recorded"],
                ]}
              />
              <h4>Parent and descendants</h4>
              <LineageStrip
                testid="research-lineage"
                parent={
                  rec(d.parent) ? (
                    <div className="lineage-node">
                      <RecordLink
                        kind="research"
                        id={String(rec(d.parent)!.hash)}
                      >
                        {String(rec(d.parent)!.hash)} ·{" "}
                        {value(rec(d.parent)!.verdict)}
                      </RecordLink>
                    </div>
                  ) : item?.parent ? (
                    <div className="lineage-node none">
                      {String(item.parent)} · not in the ledger
                    </div>
                  ) : null
                }
                current={
                  <>
                    <span className="mono">{hash}</span>
                    <small>
                      {value(item?.label)} · {value(item?.verdict)} ·{" "}
                      {value(item?.status)}
                    </small>
                  </>
                }
                childCaption="Descendants"
                items={rows(d.children).map((c) => (
                  <div className="lineage-node" key={String(c.hash)}>
                    <RecordLink kind="research" id={String(c.hash)}>
                      {String(c.hash)} · {value(c.label)} · {value(c.verdict)}
                    </RecordLink>
                  </div>
                ))}
              />
              <h4>Seeding strategy</h4>
              {seed ? (
                <StrategyRef s={seed} />
              ) : (
                <p className="quiet">No seeding strategy recorded.</p>
              )}
              <h4>Candidate bank and registrations</h4>
              <Fields
                row={rec(d.candidate)}
                keys={[
                  ["state", "Candidate state"],
                  ["rank", "Rank"],
                  ["gate1", "Gate 1"],
                  ["gate3", "Gate 3"],
                  ["reason", "Reason"],
                  ["updated_at", "Updated"],
                ]}
              />
              <SmallTable
                caption="research_tests for this hash"
                data={rows(d.registrations)}
                empty="No registered gate look for this hash."
                columns={[
                  ["seq", "Seq"],
                  ["gate", "Gate"],
                  ["p", "p"],
                  ["alpha_t", "Alpha"],
                  ["rejected", "Rejected"],
                  ["at", "At"],
                ]}
              />
              <details>
                <summary>
                  Parts, ablation and recorded evaluation (
                  {value(item?.result_bytes)} bytes)
                </summary>
                <pre className="note-body" tabIndex={0}>
                  {JSON.stringify(
                    {
                      parts: item?.parts,
                      entry_long: item?.entry_long,
                      entry_short: item?.entry_short,
                      ablation: item?.ablation,
                      result: item?.result,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
              <UnavailableFields items={d.unavailable} />
            </>
          );
        }}
      </Loaded>
    </LinkedPanel>
  );
}

// ── Strategies ──────────────────────────────────────────────────────────────
/** Strategy-detail sections the registry row alone does not show: family,
 * brain_events, the newest postmortem verdict, source idea and the research
 * the strategy seeded. `d` is the strategy detail response. */
export function StrategyEvidence({ d }: { d: R }) {
  const fam = rec(d.family);
  const parent = rec(fam?.parent);
  const pm = rec(d.postmortem);
  const idea = rec(d.source_idea);
  return (
    <>
      <h4>Family and generation</h4>
      <div data-testid="strategy-family">
        <LineageStrip
          parent={
            parent ? (
              <div className="lineage-node">
                <StrategyRef s={parent} />
              </div>
            ) : null
          }
          current={
            <>
              Family (registry kind):{" "}
              {fam?.kind ? (
                <RecordLink kind="family" id={String(fam.kind)}>
                  {String(fam.kind)}
                </RecordLink>
              ) : (
                "Unavailable"
              )}
              <small>generation {value(fam?.generation)}</small>
            </>
          }
          items={rows(fam?.children).map((c) => (
            <div className="lineage-node" key={String(c.id)}>
              <StrategyRef s={{ ...c, in_registry: true }} />
              <small>{value(c.state)}</small>
            </div>
          ))}
        />
        {rows(fam?.siblings).length > 0 && (
          <p className="quiet">
            Same family:{" "}
            {rows(fam?.siblings).map((s, i) => (
              <span key={String(s.id)}>
                {i ? ", " : ""}
                <RecordLink kind="strategy" id={String(s.id)}>
                  {String(s.name ?? s.id)}
                </RecordLink>{" "}
                ({value(s.state)})
              </span>
            ))}
          </p>
        )}
      </div>
      <div className="split even">
        <div>
          <h4>Source idea (provenance)</h4>
          {idea ? (
            <Fields
              row={{
                idea_id: idea.idea_id,
                consumed: rec(idea.consumed)
                  ? `${value(rec(idea.consumed)!.outcome)} at ${timestamp(rec(idea.consumed)!.at)}`
                  : null,
                harvested: rec(idea.harvested)
                  ? `${value(rec(idea.harvested)!.title)} · ${value(rec(idea.harvested)!.source)}`
                  : null,
                url: rec(idea.harvested)?.url,
                provenance: idea.provenance,
              }}
              keys={[
                ["idea_id", "Idea id"],
                ["consumed", "Writer outcome"],
                ["harvested", "Harvested as"],
                ["url", "Source URL (as recorded)"],
                ["provenance", "Spec provenance"],
              ]}
            />
          ) : (
            <p className="quiet">No source idea or provenance recorded.</p>
          )}
        </div>
        <div>
          <h4>Recorded postmortem verdict · historical</h4>
          {pm ? (
            <div className="postmortem" data-testid="strategy-postmortem">
              <Badge tone={pm.verdict === "CONSISTENT" ? "mint" : "amber"}>
                {value(pm.verdict)}
              </Badge>{" "}
              newest postmortem at {timestamp(pm.at)} (brain event{" "}
              {value(pm.event_id)}) — a verdict at that time, not current
              health.
              {rec(rec(pm.book_entry)?.health)?.summary
                ? ` ${String(rec(rec(pm.book_entry)?.health)?.summary)}`
                : ""}
            </div>
          ) : (
            <p className="quiet">
              The newest postmortem records no verdict for this strategy id.
            </p>
          )}
        </div>
      </div>
      <h4>Research seeded by this strategy</h4>
      <SmallTable
        caption="research_combos with trigger seed:<this id> (20 newest)"
        data={rows(d.research_seeded)}
        empty="No research combination records this strategy as its seed."
        columns={[
          [
            "hash",
            "Result",
            (v) => <RecordLink kind="research" id={String(v)} />,
          ],
          ["label", "Combination"],
          ["verdict", "Verdict"],
          ["status", "Status"],
          ["created_at", "Recorded"],
        ]}
      />
      <h4>Brain events for this strategy id</h4>
      <SmallTable
        caption="brain_events whose subject is this strategy id (100 newest)"
        data={rows(d.brain_events)}
        empty="No brain event records this strategy id."
        columns={[
          ["at", "At"],
          ["kind", "Event"],
          [
            "detail",
            "Detail",
            (v) => (
              <span className="clip">
                {typeof v === "string" ? v : JSON.stringify(v)}
              </span>
            ),
          ],
        ]}
      />
    </>
  );
}

// ── Operations ──────────────────────────────────────────────────────────────
const TIMELINE_LABEL: Record<string, string> = {
  decision: "Decision",
  trade_opened: "Trade opened",
  trade_closed: "Trade closed",
  control_event: "Control event",
  strategy_lifecycle: "Strategy lifecycle",
};
const dayOf = (at: unknown) =>
  typeof at === "string" && /^\d{4}-\d{2}-\d{2}/.test(at)
    ? at.slice(0, 10)
    : "Undated";
export function OperationsTimeline({ d }: { d: R }) {
  const [kind, setKind] = useState("all");
  const all = rows(d.timeline);
  const items = all.filter((i) => kind === "all" || i.kind === kind);
  const cool = rec(d.signal_cooldowns);
  const counts = new Map<string, number>();
  for (const i of all)
    counts.set(String(i.kind), (counts.get(String(i.kind)) ?? 0) + 1);
  return (
    <div data-testid="ops-timeline-panel">
      <Panel
        title="What Luffy recorded doing"
        className="tier-primary"
        aside={<Badge>Chronological · journal</Badge>}
      >
        <div className="workspace-toolbar">
          <label>
            Record type
            <select
              aria-label="Filter timeline"
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="all">All</option>
              {Object.entries(TIMELINE_LABEL).map(([k, l]) => (
                <option key={k} value={k}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <span className="ops-counts" aria-label="Records per type">
            {Object.entries(TIMELINE_LABEL).map(([k, l]) => (
              <span key={k} className={`ops-kind k-${k}`}>
                <i aria-hidden="true" /> {l} {counts.get(k) ?? 0}
              </span>
            ))}
          </span>
          <span className="quiet" role="status">
            {items.length} of {all.length} records · signal cooldowns counted
            separately: {value(cool?.in_newest_500_control_events)} in the
            newest 500 control events
          </span>
        </div>
        {items.length === 0 ? (
          <p className="empty">
            {all.length
              ? "No records of this type in the window."
              : "No directional decision, trade, control or lifecycle record in the window."}
          </p>
        ) : (
          <ol className="ops-timeline" data-testid="ops-timeline">
            {items.map((i, n) => {
              const day = dayOf(i.at);
              const newDay = n === 0 || dayOf(items[n - 1].at) !== day;
              return (
                <li
                  key={n}
                  data-kind={String(i.kind)}
                  className={`k-${String(i.kind)}${newDay ? " new-day" : ""}`}
                  data-day={newDay ? day : undefined}
                >
                  <time>{timestamp(i.at)}</time>
                  <span className="ops-mark" aria-hidden="true" />
                  <Badge
                    tone={
                      i.status === "executed" || i.kind === "trade_opened"
                        ? "mint"
                        : i.status === "skipped"
                          ? "amber"
                          : "neutral"
                    }
                  >
                    {TIMELINE_LABEL[String(i.kind)] ?? String(i.kind)}
                  </Badge>
                  <span className="ops-body">
                    {i.ref ? (
                      <RefLink r={i.ref} label={String(i.title)} />
                    ) : (
                      <span className="ops-title">{String(i.title)}</span>
                    )}
                    {i.detail ? (
                      <span className="quiet"> · {String(i.detail)}</span>
                    ) : null}
                    {rows(i.strategies).length > 0 &&
                      i.kind !== "strategy_lifecycle" && (
                        <span className="ops-strategies">
                          {rows(i.strategies).map((s) => (
                            <StrategyRef key={String(s.id)} s={s} />
                          ))}
                        </span>
                      )}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </Panel>
    </div>
  );
}

// ── Overview ────────────────────────────────────────────────────────────────
export function OverviewEvidence() {
  const q = useRead("overview/activity", 30000);
  return (
    <div className="workspace-stack" data-testid="overview-evidence">
      <Loaded q={q}>
        {(d) => {
          const dec = rec(d.decisions);
          const research = rec(d.research);
          const run = rec(research?.latest_run);
          const risk = rec(d.risk_state);
          const rent = rec(d.rent_state);
          const reqs = rows(d.unresolved_owner_requests);
          return (
            <>
              <div className="section-rule">
                <h2>Recent recorded actions</h2>
                <SourceStrip source={String(d.source)} at={d.generated_at} />
              </div>
              <div className="workspace-grid">
                <Panel
                  className="tier-primary"
                  title="Recent decisions"
                  aside={<a href="#operations">Operations ↗</a>}
                >
                  <p className="quiet">
                    Newest {value(dec?.window)} journal decisions.
                  </p>
                  <SmallTable
                    caption="Executed (newest 5)"
                    data={rows(dec?.executed)}
                    empty="No executed decision in the window."
                    columns={[
                      [
                        "id",
                        "Decision",
                        (v, r) => (
                          <RecordLink kind="decision" id={String(v)}>
                            {String(r.action)} {String(r.symbol)}
                          </RecordLink>
                        ),
                      ],
                      ["ts", "At"],
                    ]}
                  />
                  <SmallTable
                    caption="Directional but not executed (newest 5)"
                    data={rows(dec?.skipped)}
                    empty="No skipped directional decision in the window."
                    columns={[
                      [
                        "id",
                        "Decision",
                        (v, r) => (
                          <RecordLink kind="decision" id={String(v)}>
                            {String(r.action)} {String(r.symbol)}
                          </RecordLink>
                        ),
                      ],
                      ["skip_reason", "Reason"],
                      ["ts", "At"],
                    ]}
                  />
                  <SmallTable
                    caption="Risk / rejection reason classes"
                    data={rows(dec?.risk_blocks)}
                    empty="No skip reason recorded in the window."
                    columns={[
                      ["reason", "Reason class"],
                      ["n", "Decisions"],
                    ]}
                  />
                </Panel>
                <Panel
                  title="Strategy activity"
                  aside={<a href="#strategies">Strategies ↗</a>}
                >
                  <SmallTable
                    caption="Active and paper strategies · journal-booked economics"
                    data={rows(d.strategies)}
                    empty="No active or paper strategy in the registry."
                    columns={[
                      [
                        "id",
                        "Strategy",
                        (v, r) => (
                          <RecordLink kind="strategy" id={String(v)}>
                            {String(r.name)}
                          </RecordLink>
                        ),
                      ],
                      ["state", "State"],
                      [
                        "journal_economics",
                        "Trades (open)",
                        (v) =>
                          rec(v)
                            ? `${value(rec(v)!.trades)} (${value(rec(v)!.open)})`
                            : "none booked",
                      ],
                      [
                        "journal_economics",
                        "Realized",
                        (v) => usdt(rec(v)?.realized_pnl),
                      ],
                    ]}
                  />
                  <SmallTable
                    caption="Newest lifecycle events"
                    data={rows(d.lifecycle)}
                    empty="No strategy lifecycle event recorded."
                    columns={[
                      ["at", "At"],
                      ["kind", "Event"],
                      [
                        "strategy_id",
                        "Strategy",
                        (v, r) => (
                          <StrategyRef
                            s={{ id: v, in_registry: r.in_registry }}
                          />
                        ),
                      ],
                    ]}
                  />
                </Panel>
              </div>
              <div className="workspace-grid">
                <Panel
                  title="Research activity"
                  aside={<a href="#research">Research ↗</a>}
                >
                  {research ? (
                    <>
                      <p>
                        Latest run {value(run?.id)} · {timestamp(run?.started)}{" "}
                        · {value(run?.tf)} {value(run?.geo)} {value(run?.round)}{" "}
                        · {run?.ok ? "completed" : "failed or incomplete"} ·
                        survivors recorded: {value(research.survivors)}
                      </p>
                      <SmallTable
                        caption="Newest results (rankings, not admission)"
                        data={rows(research.newest_results)}
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
                          [
                            "seed_strategy",
                            "Seeded by",
                            (v) => (rec(v) ? <StrategyRef s={v} /> : "—"),
                          ],
                        ]}
                      />
                    </>
                  ) : (
                    <p className="quiet">Research ledger unavailable.</p>
                  )}
                </Panel>
                <Panel title="Risk and operating cost">
                  <Fields
                    row={risk}
                    keys={[
                      ["peak_equity", "Peak equity (Risk record)", usdt],
                      ["day_start_equity", "Day-start equity", usdt],
                      ["day_key", "Risk day"],
                    ]}
                  />
                  <h4>Operating-cost week (rent)</h4>
                  <Fields
                    row={rent}
                    keys={[
                      ["week_start", "Week start"],
                      ["net", "Net (USDT)", (v) => num(v)],
                      ["bar", "Bar (USDT)", (v) => num(v)],
                      ["status", "Status"],
                      ["updated_at", "Updated"],
                    ]}
                  />
                  <p className="quiet">
                    Recorded state_kv values; not a live risk check.
                  </p>
                </Panel>
              </div>
              <Panel title="Owner attention records">
                <SmallTable
                  caption="Unresolved owner control requests"
                  data={reqs}
                  empty="No unresolved owner request is recorded."
                  columns={[
                    ["request_id", "Request", (v) => short(v)],
                    ["operation", "Operation"],
                    ["state", "State"],
                    ["created_at", "Created"],
                  ]}
                />
                <UnavailableFields items={d.unavailable} />
              </Panel>
            </>
          );
        }}
      </Loaded>
    </div>
  );
}

// ── Live System ─────────────────────────────────────────────────────────────
const windowLabel = (v: unknown) =>
  typeof v === "number"
    ? v >= 3600
      ? `${v / 3600} h`
      : `${v / 60} min`
    : "Unavailable";
/** Movement lanes: one per recorded flow. The line style is the evidence
 * class — observed records, none in the window, or an unavailable read. */
export function SystemObserved() {
  const q = useRead("system/observed", 30000);
  return (
    <Panel
      title="Observed data movement"
      className="tier-primary"
      aside={<Badge>OBSERVED · records written</Badge>}
    >
      <Loaded q={q}>
        {(d) => (
          <div data-testid="system-observed">
            <SourceStrip
              source={String(d.source)}
              at={d.generated_at}
              note={String(d.note)}
            />
            <div className="truth-legend" aria-label="Evidence classes">
              <span>
                <i className="swatch" /> records observed in window
              </span>
              <span>
                <i className="swatch none" /> no record in window
              </span>
              <span>
                <i className="swatch unavailable" /> read unavailable
              </span>
              <span>
                <i className="swatch declared" /> declared, no proving record
              </span>
              <span className="quiet">
                Static: nothing here is animated or implies throughput.
              </span>
            </div>
            <h4>
              Records one component wrote for another, within the stated window
            </h4>
            {rows(d.flows).length === 0 ? (
              <p className="quiet">No flow records returned.</p>
            ) : (
              <ol className="flow-lanes" aria-label="Observed flows">
                {rows(d.flows).map((r, i) => {
                  const cls =
                    r.count === null
                      ? "unavailable"
                      : r.status === "observed"
                        ? "observed"
                        : "none";
                  return (
                    <li key={i} className={`flow-lane lane-${cls}`}>
                      <span className="lane-end">{String(r.source)}</span>
                      <span className="lane-track">
                        <span className="lane-record">{value(r.record)}</span>
                        <span className="lane-line" aria-hidden="true" />
                      </span>
                      <span className="lane-end">{String(r.target)}</span>
                      <span className="lane-count">
                        {r.count === null
                          ? `Unavailable (${value(r.error)})`
                          : `${r.saturated ? "≥" : ""}${String(r.count)}`}
                      </span>
                      <span className="lane-window">
                        {windowLabel(r.window_s)}
                      </span>
                      <span className="lane-newest">
                        {timestamp(r.newest_at)}
                      </span>
                      <span>
                        <Badge
                          tone={r.status === "observed" ? "mint" : "amber"}
                        >
                          {String(r.status).toUpperCase()}
                        </Badge>
                      </span>
                      <span className="lane-declared">
                        {r.declared_edge ? "DECLARED" : "not in the declared map"}
                      </span>
                    </li>
                  );
                })}
              </ol>
            )}
            <h4>Declared connections without observed records</h4>
            <DeclaredEdges d={d} />
          </div>
        )}
      </Loaded>
    </Panel>
  );
}

/** The completeness claim is made only when every declared edge has an
 * observed flow with records AND the backend's unobserved list agrees. */
function DeclaredEdges({ d }: { d: R }) {
  const declared = rows(d.declared_edges);
  const reported = rows(d.unobserved_declared_edges);
  const unproven = unprovenEdges({
    flows: rows(d.flows) as never,
    declared_edges: declared as never,
  });
  const consistent =
    unproven.length === reported.length &&
    unproven.every((e) =>
      reported.some((r) => r.source === e.source && r.target === e.target),
    );
  if (!consistent)
    return (
      <p data-testid="edges-inconsistent">
        <Badge tone="amber">UNAVAILABLE</Badge> The observed-edge evidence is
        inconsistent ({unproven.length} declared edge(s) lack an observed flow;
        the backend lists {reported.length}). No completeness claim is made.
      </p>
    );
  if (!declared.length)
    return (
      <p className="quiet" data-testid="edges-none-declared">
        No declared edges were returned; no completeness claim is made.
      </p>
    );
  if (!unproven.length)
    return (
      <p data-testid="edges-complete">
        Every declared edge has an observed record in its window.
      </p>
    );
  return (
    <ol
      className="flow-lanes"
      aria-label="DECLARED architecture edges no record proves"
    >
      {reported.map((e, i) => (
        <li key={i} className="flow-lane lane-declared-only">
          <span className="lane-end">{String(e.source)}</span>
          <span className="lane-track">
            <span className="lane-record">no proving record</span>
            <span className="lane-line" aria-hidden="true" />
          </span>
          <span className="lane-end">{String(e.target)}</span>
          <span className="lane-reason">{value(e.reason)}</span>
        </li>
      ))}
    </ol>
  );
}

// ── Knowledge ───────────────────────────────────────────────────────────────
export function NoteRecords({ d }: { d: R }) {
  const fam = rec(d.family);
  const records = rows(d.records);
  return (
    <div data-testid="note-records">
      <h4>Linked records</h4>
      {d.records_error ? (
        <p className="quiet">
          Record lookup unavailable ({String(d.records_error)}).
        </p>
      ) : records.length ? (
        <ul className="connections">
          {records.map((r) => (
            <li key={`${r.kind}:${r.id}`}>
              <RefLink r={r} label={`${String(r.kind)} · ${String(r.label)}`} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="quiet">No stored record id appears in this note.</p>
      )}
      {typeof d.records_truncated_count === "number" &&
        d.records_truncated_count > 0 && (
          <p className="quiet" data-testid="note-records-truncated">
            {records.length} of {String(d.records_resolved_count)} matched
            stored records shown.
          </p>
        )}
      {rows(d.records_unresolved).length > 0 && (
        <p className="quiet" data-testid="note-unresolved">
          Not found in the stores (unresolved ids):{" "}
          {rows(d.records_unresolved)
            .map((u) => String(u.token))
            .join(", ")}
        </p>
      )}
      {fam && (
        <p>
          Family <RecordLink kind="family" id={String(fam.kind)} /> (
          {String(fam.basis)}):{" "}
          {rows(fam.strategies).map((s, i) => (
            <span key={String(s.id)}>
              {i ? ", " : ""}
              <RecordLink kind="strategy" id={String(s.id)}>
                {String(s.name ?? s.id)}
              </RecordLink>
            </span>
          ))}
          {rows(fam.strategies).length === 0 && "no registry strategy"}
        </p>
      )}
      <p className="quiet">{value(d.records_basis)}</p>
    </div>
  );
}

// ── Diagnostics ─────────────────────────────────────────────────────────────
export function DiagnosticsProbes({ d }: { d: R }) {
  return (
    <Panel title="Backend and store probes" aside={<Badge>Measured now</Badge>}>
      <SmallTable
        caption="Availability measured by this request"
        data={rows(d.probes)}
        empty="No probe results returned."
        columns={[
          ["probe", "Probe"],
          [
            "ok",
            "Result",
            (v) => (
              <Badge tone={v ? "mint" : "rose"}>{v ? "OK" : "FAILED"}</Badge>
            ),
          ],
          ["ms", "ms"],
          ["detail", "Detail"],
        ]}
      />
      <p className="quiet">
        One measurement per read; no latency history is kept.
      </p>
    </Panel>
  );
}

export function ControlEventsPager() {
  const { adapter } = usePreview();
  const [cursors, setCursors] = useState<(number | null)[]>([null]);
  const [cooldown, setCooldown] = useState(false);
  const before = cursors[cursors.length - 1];
  const path = `diagnostics/events?limit=50&include_cooldown=${cooldown}${before !== null ? `&before=${before}` : ""}`;
  const q = useQuery({
    queryKey: ["owner-read", path],
    queryFn: ({ signal }) => adapter.ownerRead!(path, signal),
    enabled: !!adapter.ownerRead,
  });
  const d = q.data;
  return (
    <Panel title="Control event history" aside={<Badge>Keyset pages</Badge>}>
      <div className="workspace-toolbar">
        <label>
          <input
            type="checkbox"
            checked={cooldown}
            onChange={(e) => {
              setCooldown(e.target.checked);
              setCursors([null]);
            }}
          />{" "}
          Include signal cooldowns
        </label>
        <button
          type="button"
          disabled={cursors.length === 1}
          onClick={() => setCursors(cursors.slice(0, -1))}
        >
          Newer
        </button>
        <button
          type="button"
          disabled={!d?.has_more || q.isFetching}
          onClick={() =>
            typeof d?.next_before === "number" &&
            setCursors([...cursors, d.next_before])
          }
        >
          Older
        </button>
        <span className="quiet" role="status">
          Page {cursors.length} · 50 per page · newest first
        </span>
      </div>
      <QueryState
        error={q.error}
        loading={q.isPending}
        retry={() => void q.refetch()}
      />
      {d && !q.error && (
        <SmallTable
          caption="journal control_events"
          data={rows(d.events)}
          empty="No control events on this page."
          columns={[
            ["id", "Id"],
            ["ts", "At"],
            ["event", "Event"],
            ["from_state", "From"],
            ["to_state", "To"],
            ["actor", "Actor"],
            [
              "detail",
              "Detail",
              (v) => (
                <span className="clip">
                  {typeof v === "string" ? v : JSON.stringify(v)}
                </span>
              ),
            ],
          ]}
        />
      )}
    </Panel>
  );
}
