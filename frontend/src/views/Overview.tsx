import { timestamp as utc } from "../time";
import { lazyChunk } from "../lazyChunk";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { applyProtectionExpiry } from "../protectionExpiry";
import { expireOverviewTelemetry } from "../telemetryExpiry";
import { ArrowUpRight, Shield, Radio, Wallet } from "lucide-react";
import { usePreview } from "../context";
import { recordHref } from "../links";
import { NeedsYou } from "../components/OwnerEvidence";
import {
  Badge,
  Panel,
  Freshness,
  QueryState,
  EvidenceButton,
  VisualBoundary,
} from "../components/ui";
import type { Position, ProtectionStatus } from "../adapters/contracts";
const RecentActivity = lazyChunk(() => import("./Activity"));
const EquityChart = lazyChunk(() => import("../components/EquityChart"));
const OverviewEvidence = lazyChunk(() =>
  import("./Evidence").then((m) => ({ default: m.OverviewEvidence })),
);
const money = (n: number | null) =>
  n === null
    ? "UNAVAILABLE"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
      }).format(n);
const usdt = (n: number | null | undefined) =>
  n === null || n === undefined
    ? "UNAVAILABLE"
    : `${new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)} USDT`;

const pointLabel = (t: string | number) =>
  typeof t === "number" ? utc(new Date(t * 1000).toISOString()) : t;
/** Only a fresh VERIFIED reads as healthy; everything else is a warning. */
export function protectionTone(status: ProtectionStatus | string) {
  return status === "VERIFIED"
    ? "mint"
    : status === "UNPROTECTED"
      ? "rose"
      : "amber";
}
function ProtectionCell({ p }: { p: Position }) {
  const d = p.protectionDetail;
  if (!d) return <Badge tone="amber">{p.protection}</Badge>;
  return (
    <span className="protection-cell">
      <Badge tone={protectionTone(d.status)}>{d.status}</Badge>
      {d.lastReported && <span>Last reported: {d.lastReported}</span>}
      <span className="quiet">
        {d.observedAt
          ? `Checked ${utc(d.observedAt)}`
          : "Not verified: no protection check"}
      </span>
    </span>
  );
}
export default function Overview() {
  const { adapter, scenario } = usePreview();
  const live = import.meta.env.MODE === "production" || adapter.mode === "LIVE";
  const q = useQuery({
    queryKey: ["overview", scenario],
    queryFn: ({ signal }) => adapter.overview(scenario, signal),
    ...(live
      ? {
          staleTime: 10000,
          refetchInterval: 30000,
          refetchIntervalInBackground: false,
        }
      : {}),
  });
  // Protection evidence expires on the browser clock, not on the next poll:
  // a failed refetch keeps q.data, but never keeps it current.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [live]);
  const d = useMemo(
    () => (q.data && live ? expireOverviewTelemetry(applyProtectionExpiry(q.data, now), now, !!q.error) : q.data),
    [q.data, q.error, now, live],
  );
  // Optional venue enrichment: never blocks, never replaces local values.
  const marks = useQuery({
    queryKey: ["marks"],
    queryFn: ({ signal }) => adapter.marks!(signal),
    enabled: live && !!adapter.marks && !!d?.positions?.length,
    staleTime: 20000,
    refetchInterval: 60000,
    refetchIntervalInBackground: false,
  });
  const L = d?.live;
  const pnlOf = (p: Position) =>
    live ? (marks.data?.marks[p.symbol]?.upnl ?? null) : p.pnl;
  return (
    <>
      <QueryState
        error={q.error}
        loading={q.isPending}
        retry={() => void q.refetch()}
      />
      {d && (
        <>
          <div className="snapshot-line">
            <Freshness value={d.provenance} />
            <span>
              {live
                ? "Local journal read · demo venue account (BINANCE_DEMO) · USDT"
                : "All amounts are synthetic USD equivalents"}
            </span>
            {q.isFetching && live && <span className="quiet">Refreshing…</span>}
          </div>
          {live && L && Object.keys(L.errors).length > 0 && (
            <div
              className="inline-error"
              role="status"
              data-testid="missing-sections"
            >
              Missing:{" "}
              {Object.entries(L.errors)
                .map(([k, v]) => `${k} (${v})`)
                .join(" · ")}
              . Nothing was substituted.
            </div>
          )}
          <div className="command-deck">
            <div className="command-atmosphere" aria-hidden="true" />
            <section
              className="panel tier-primary attention-band"
              aria-label="Owner attention"
            >
              <div className="attention-needs">
                <div className="eyebrow">
                  <Shield size={12} aria-hidden="true" /> NEEDS YOU ·{" "}
                  {live ? "SUPERVISOR" : "PREVIEW"}
                </div>
                <h2>
                  {d.needsYou ??
                    (live
                      ? "Owner-attention evidence unavailable"
                      : "Approval feed not connected")}
                </h2>
                <p>
                  {live
                    ? L?.needsYou
                      ? L.needsYou.provenance.freshness === "fresh"
                        ? `From the Supervisor pass at ${utc(L.needsYou.provenance.observedAt)}.`
                        : `STALE — last reported ${utc(L.needsYou.provenance.observedAt)}; current need is unknown.`
                      : "No Supervisor record is readable. This does not establish that no owner action is needed."
                    : "No approval records are supplied. This does not establish that no owner action is needed."}
                </p>
                <NeedsYou compact />
                <a className="text-link" href="#luffy">
                  Open LUFFY{" "}
                  <ArrowUpRight size={14} />
                </a>
              </div>
              <div className="attention-control">
                <span className="figure-label">Control state</span>
                <div className="stat-control" data-testid="control-state">
                  {d.control}
                </div>
                {live && <p>Stored control permission · not evidence of running work.</p>}
                <p>
                  {live ? (
                    <a className="text-link" href="#operations">
                      Owner controls on Operations <ArrowUpRight size={13} />
                    </a>
                  ) : (
                    "Trading controls are unavailable"
                  )}
                </p>
              </div>
              <div className="attention-status">
                <div className="status-row">
                  <span>Protection verification</span>
                  <Badge
                    tone={
                      live && L?.protection
                        ? protectionTone(L.protection.status)
                        : "amber"
                    }
                  >
                    {live
                      ? (L?.protection?.status ?? "UNAVAILABLE")
                      : "Unavailable"}
                  </Badge>
                </div>
                {live && (
                  <div className="status-row">
                    <span>Kernel process</span>
                    <Badge tone="amber">{q.error ? "UNKNOWN" :
                      L?.kernelState === "RUNNING" && (L.heartbeat?.expiresAt == null || now > L.heartbeat.expiresAt)
                        ? "STALE" : L?.kernelState ?? "UNKNOWN"}</Badge>
                  </div>
                )}
                {live && (
                  <div className="status-row">
                    <span>Kernel heartbeat / work</span>
                    <Badge
                      tone={
                        !q.error && L?.heartbeat?.freshness === "fresh" && L.heartbeat.expiresAt != null && now <= L.heartbeat.expiresAt ? "mint" : "amber"
                      }
                    >
                      {q.error ? "UNAVAILABLE" : L?.heartbeat
                        ? L.heartbeat.expiresAt == null || now > L.heartbeat.expiresAt ? "STALE" : L.heartbeat.freshness.toUpperCase()
                        : "UNAVAILABLE"}
                    </Badge>
                  </div>
                )}
                <div className="status-row">
                  <span>Data source</span>
                  <Badge>{live ? "Luffy backend · LIVE" : "Synthetic"}</Badge>
                </div>
                {live ? (
                  <>
                    <div className="status-row">
                      <span>News Guard</span>
                      <Badge tone="amber">UNAVAILABLE</Badge>
                    </div>
                    <div className="status-row">
                      <span>Approval objects</span>
                      <Badge tone="amber">UNAVAILABLE</Badge>
                    </div>
                  </>
                ) : (
                  <div className="status-row">
                    <span>Production health</span>
                    <Badge>Unknown</Badge>
                  </div>
                )}
                <a className="text-link" href="#live-system">
                  Inspect topology <ArrowUpRight size={14} />
                </a>
              </div>
            </section>
            <div
              className="panel capital-panel command-capital"
              role="group"
              aria-label="Capital"
            >
              <div className="panel-heading">
                <h2>Capital</h2>
                <Badge>
                  {live
                    ? `${d.points.length} hourly points`
                    : "30 fixture points"}
                </Badge>
              </div>
              <div className="capital-body">
                <div className="capital-figures">
                  <section className="capital-figure">
                    <h3 className="figure-label">
                      <Wallet size={12} aria-hidden="true" /> Account equity
                    </h3>
                    <div className="stat-value">
                      {live ? usdt(d.equity) : money(d.equity)}
                    </div>
                    <p>
                      {live
                        ? L?.account
                          ? `Journal record ${utc(L.account.observedAt)} · ${L.account.freshness.toUpperCase()}`
                          : "No equity record"
                        : "Fixture account · No venue connection"}
                    </p>
                  </section>
                  <section className="capital-figure">
                    <h3 className="figure-label">
                      <ArrowUpRight size={12} aria-hidden="true" />{" "}
                      {live ? "Realized today" : "Period P&L"}
                    </h3>
                    <div
                      data-testid="realized-today"
                      className={`stat-value ${d.pnl === null ? "quiet" : d.pnl > 0 ? "mint" : d.pnl < 0 ? "rose" : "neutral"}`}
                    >
                      {live ? usdt(d.pnl) : money(d.pnl)}
                    </div>
                    <p>
                      {live
                        ? L?.realizedClosedTrades !== null &&
                          L?.realizedClosedTrades !== undefined
                          ? `${L.realizedClosedTrades} closed trade(s) today (UTC) · journal-booked`
                          : "Realized P&L unavailable"
                        : "Sample period · 29 Aug–27 Sep 2026"}
                    </p>
                  </section>
                  <section className="capital-figure">
                    <h3 className="figure-label">
                      <Radio size={12} aria-hidden="true" /> Gross exposure
                    </h3>
                    <div className="stat-value">
                      {d.exposure === null ? "UNAVAILABLE" : `${d.exposure}%`}
                    </div>
                    <p>
                      {live
                        ? "Entry notional ÷ equity · not a risk limit"
                        : "Illustrative allocation · Not a risk limit"}
                      {" · "}
                      <Freshness value={d.provenance} />
                    </p>
                  </section>
                </div>
                <div className="capital-chart">
                  <div className="chart-summary">
                    <span className="figure-label">Account trajectory</span>
                    <span className="quiet">
                      {live
                        ? (L?.seriesNote ?? "Equity history unavailable")
                        : "Synthetic series · USD equivalent"}
                    </span>
                  </div>
                  {d.points.length ? (
                    <VisualBoundary>
                      <Suspense
                        fallback={
                          <div className="chart loading">Loading chart…</div>
                        }
                      >
                        <EquityChart
                          points={d.points}
                          label={
                            live
                              ? "Journal account equity chart; values available in accessible chart data"
                              : undefined
                          }
                        />
                      </Suspense>
                    </VisualBoundary>
                  ) : (
                    <div className="empty chart">
                      Equity history unavailable
                    </div>
                  )}
                  <details className="chart-data">
                    <summary>Accessible chart data</summary>
                    <div
                      className="table-scroll"
                      tabIndex={0}
                      role="region"
                      aria-label="Scrollable account table"
                    >
                      <table>
                        <caption>
                          {live
                            ? "Journal equity (USDT), last record per hour"
                            : "Synthetic daily equity"}
                        </caption>
                        <thead>
                          <tr>
                            <th>{live ? "Time" : "Date"}</th>
                            <th>{live ? "USDT" : "USD equivalent"}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {d.points.map((p) => (
                            <tr key={String(p.time)}>
                              <td>{pointLabel(p.time)}</td>
                              <td>{live ? usdt(p.value) : money(p.value)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </details>
                </div>
              </div>
            </div>
          </div>
          {live && (
            <p className="owner-status-strip">
              <span>
                Risk state: <strong>recorded values below</strong> (no live risk
                check)
              </span>
              <span>
                News guard: unavailable · Approval objects:{" "}
                <strong>Unavailable</strong> (no approval store)
              </span>
            </p>
          )}
          <div className="bottom-grid">
            <Panel
              title="Positions"
              className="positions-panel"
              aside={
                <Badge>
                  {live ? "Journal-open trades" : "Fixture snapshot"}
                </Badge>
              }
            >
              {d.positions === null ? (
                <div className="empty">Position data unavailable</div>
              ) : live && d.positions.length === 0 ? (
                <div className="empty">No journal-open trades.</div>
              ) : (
                <div
                  className="table-scroll"
                  tabIndex={0}
                  role="region"
                  aria-label="Scrollable account table"
                >
                  <table>
                    <thead>
                      <tr>
                        <th>Instrument</th>
                        <th>Side</th>
                        <th>{live ? "Entry notional" : "Notional"}</th>
                        <th>
                          {live ? "Unrealized (estimate)" : "Unrealized P&L"}
                        </th>
                        <th>Protection</th>
                        {live && <th>Journal stop</th>}
                      </tr>
                    </thead>
                    <tbody>
                      {d.positions.map((p) => {
                        const pnl = pnlOf(p);
                        return (
                          <tr key={p.id ?? p.symbol}>
                            <td data-label="Instrument" className="strong">
                              {live && p.id ? (
                                <a
                                  className="record-link"
                                  href={recordHref("trade", p.id)}
                                  data-record={`trade:${p.id}`}
                                >
                                  {p.symbol}
                                </a>
                              ) : (
                                p.symbol
                              )}
                            </td>
                            <td data-label="Side">{p.side}</td>
                            <td data-label="Entry notional">
                              {live ? usdt(p.notional) : money(p.notional)}
                            </td>
                            <td
                              data-label="Unrealized estimate"
                              className={
                                pnl === null
                                  ? "quiet"
                                  : pnl >= 0
                                    ? "mint"
                                    : "rose"
                              }
                            >
                              {pnl === null
                                ? live
                                  ? marks.isPending &&
                                    marks.fetchStatus !== "idle"
                                    ? "Loading mark…"
                                    : "Mark unavailable"
                                  : "UNAVAILABLE"
                                : live
                                  ? usdt(pnl)
                                  : money(pnl)}
                            </td>
                            <td data-label="Protection">
                              <ProtectionCell p={p} />
                            </td>
                            {live && (
                              <td data-label="Journal stop" className="quiet">
                                {p.journalStop?.price
                                  ? `${p.journalStop.price}${p.journalStop.orderRefRecorded ? " · order ref recorded" : " · no order ref"}`
                                  : "None recorded"}
                              </td>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              {live && (
                <p className="quiet">
                  Protection is the kernel's last read-only venue protection
                  check; a journal stop is not venue verification. Unrealized
                  values are
                  {marks.data?.observedAt
                    ? ` venue-price estimates at ${utc(marks.data.observedAt)}.`
                    : " unavailable until a venue price is read."}
                </p>
              )}
            </Panel>
            <Panel title={live ? "Protection evidence" : "Evidence note"}>
              {live ? (
                <>
                  <p>
                    Verification is shown as healthy only when a fresh kernel
                    protection check read every venue position, the complete
                    stop listing, and found nothing unprotected or unreconciled.
                    The browser never asks the venue.
                  </p>
                  {L?.protection?.evidence ? (
                    <div className="space-top">
                      <EvidenceButton value={L.protection.evidence} />
                    </div>
                  ) : (
                    <Badge tone="amber">
                      NOT VERIFIED · no protection check
                    </Badge>
                  )}
                </>
              ) : (
                <>
                  <p>
                    Research context stays separate from permission to trade.
                  </p>
                  <Badge tone="amber">INCONCLUSIVE</Badge>
                  <div className="space-top">
                    <EvidenceButton value={d.provenance} />
                  </div>
                </>
              )}
            </Panel>
          </div>
          {live && (
            <VisualBoundary
              fallback={(error, reset) => (
                <div role="alert" className="empty">
                  Owner activity summary unavailable: {error.message}{" "}
                  <button type="button" onClick={reset}>
                    Retry
                  </button>
                </div>
              )}
            >
              <Suspense
                fallback={<div role="status">Loading owner activity…</div>}
              >
                <OverviewEvidence />
              </Suspense>
            </VisualBoundary>
          )}
          {live && (
            <VisualBoundary
              fallback={(error, reset) => (
                <div role="alert" className="empty">
                  Recent activity unavailable: {error.message}{" "}
                  <button type="button" onClick={reset}>
                    Retry
                  </button>
                </div>
              )}
            >
              <Suspense
                fallback={<div role="status">Loading recent activity…</div>}
              >
                <RecentActivity compact />
              </Suspense>
            </VisualBoundary>
          )}
        </>
      )}
    </>
  );
}
