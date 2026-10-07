/** Overview — approved "Final · 1" design, bound to live owner-api reads.
 * Every figure is a recorded value with its source; a missing value reads
 * UNAVAILABLE and is never replaced by a plausible one. */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowLeftRight, Bell, Eye, LineChart, Search, Shield, TrendingUp, Wallet } from "lucide-react";
import { usePreview } from "../context";
import { applyProtectionExpiry } from "../protectionExpiry";
import { expireOverviewTelemetry } from "../telemetryExpiry";
import { useRead, rows as asRows, rec } from "../components/records";
import { Card, CardHead, DataTable, DetailPanel, KV, type Col } from "../components/glass";
import { PageHeader } from "../components/PageHeader";
import { timestamp as utc } from "../time";
import type { Marks, Position, ProtectionStatus } from "../adapters/contracts";

/** Only a fresh VERIFIED reads as healthy; everything else is a warning. */
export function protectionTone(status: ProtectionStatus | string) {
  return status === "VERIFIED" ? "mint" : status === "UNPROTECTED" ? "rose" : "amber";
}
/** A cached quote cannot stay current through a failed poll or beyond its own age limit. */
export function currentPositionPnl(p: Position, marks: Marks | undefined, receivedAt: number, now: number, failed: boolean): number | null {
  if (!marks || failed || marks.error) return null;
  const byTrade = marks.evidence?.by_trade as Record<string, Record<string, unknown>> | undefined;
  const quote = (p.id && byTrade?.[p.id]) || (marks.marks[p.symbol] as Record<string, unknown> | undefined);
  const age = quote?.quote_age_s;
  const value = quote?.upnl;
  return typeof age === "number" && age >= 0 && age + Math.max(0, now - receivedAt) / 1000 <= 60 &&
    typeof value === "number" && Number.isFinite(value)
    ? value
    : null;
}

const OK = "#3ddc97", WARN = "#f2b44a", BAD = "#ff7b72", FROZEN = "#56ccf2", GREY = "#8d9fb2";
const PALETTE = ["#9be3c6", "#c9d4df", "#ffb86b", "#b6509e", "#7fd3ff", "#ff6b6b", "#a78bfa", "#e2b45c"];
const hue = (s: string) => PALETTE[[...s].reduce((a, c) => a + c.charCodeAt(0), 0) % PALETTE.length];
const base = (symbol: string) => symbol.split("/")[0].split("-")[0];
const usd = (n: number | null | undefined, digits = 2) =>
  n === null || n === undefined ? "UNAVAILABLE" : `${n < 0 ? "−" : ""}$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
const signed = (n: number, digits = 2) => `${n >= 0 ? "+" : "−"}$${Math.abs(n).toFixed(digits)}`;
const px = (n: number | null | undefined) =>
  n === null || n === undefined ? "UNAVAILABLE" : n >= 100 ? n.toFixed(2) : n >= 1 ? n.toFixed(3) : n.toPrecision(4);
const day = (iso: string | null | undefined) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(+d) ? d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", timeZone: "UTC" }) : "UNAVAILABLE";
};
const hhmm = (iso: string | null | undefined) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(+d) ? `${d.toISOString().slice(11, 16)} UTC` : "UNAVAILABLE";
};
const age = (s: unknown) =>
  typeof s !== "number" ? "age unknown" : s < 120 ? `${Math.round(s)} s` : s < 7200 ? `${Math.round(s / 60)} min` : s < 172800 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} d`;

interface DeskPoint { trade_id: string; closed_at: string; symbol: string; pnl: number; cumulative: number; status: string; close_reason: string | null; strategy_name: string | null }
interface DeskCoin { decision_id: string; symbol: string; action: string; score: number | null; threshold: number | null; confidence: number | null; executed: boolean; skip_reason: string | null }
interface Desk {
  realized: { points: DeskPoint[]; last_closes: DeskPoint[]; closed_trades: number; unknown_pnl_trades: number; estimated_pnl_trades: number; total: number | null; source: string } | null;
  scan: { scan_id: string; observed_at: string; coins: DeskCoin[]; traded: number; source: string } | null;
  research: { counts: Record<string, number | null>; referee_enabled: boolean | null; handoff_enabled: boolean | null } | null;
  errors: Record<string, string>;
}

type Tone = "ok" | "warn" | "bad" | "frozen" | "grey";
const COLOR: Record<Tone, string> = { ok: OK, warn: WARN, bad: BAD, frozen: FROZEN, grey: GREY };

export default function Overview() {
  const { adapter, scenario } = usePreview();
  const live = adapter.mode === "LIVE";
  const q = useQuery({
    queryKey: ["overview", scenario],
    queryFn: ({ signal }) => adapter.overview(scenario, signal),
    ...(live ? { staleTime: 10000, refetchInterval: 30000, refetchIntervalInBackground: false } : {}),
  });
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [live]);
  // Evidence expires on the browser clock, not on the next poll.
  const d = useMemo(
    () => (q.data && live ? expireOverviewTelemetry(applyProtectionExpiry(q.data, now), now, !!q.error) : q.data),
    [q.data, q.error, now, live],
  );
  const marks = useQuery({
    queryKey: ["marks"],
    queryFn: ({ signal }) => adapter.marks!(signal),
    enabled: live && !!adapter.marks && !!d?.positions?.length,
    staleTime: 20000,
    refetchInterval: 60000,
    refetchIntervalInBackground: false,
  });
  const deskQ = useRead(live ? "desk" : null, 60000);
  const sysQ = useRead(live ? "system" : null, 60000);
  const needsQ = useRead(live ? "needs-you" : null, 30000);
  const desk = deskQ.data as unknown as Desk | undefined;
  const L = d?.live;
  const positions = d?.positions ?? null;
  const control = d?.control ?? "UNAVAILABLE";

  const pnlOf = (p: Position) => (live ? currentPositionPnl(p, marks.data, marks.dataUpdatedAt, now, !!marks.error) : p.pnl);
  const upnl = useMemo(() => {
    if (!positions) return { value: null as number | null, why: "positions unavailable" };
    if (positions.length === 0) return { value: 0, why: "no open positions" };
    const vals = positions.map((p) => pnlOf(p));
    if (vals.some((v) => v === null)) return { value: null, why: marks.isPending ? "loading mark prices…" : "no fresh mark price (≤ 60 s) for every position" };
    return { value: (vals as number[]).reduce((a, b) => a + b, 0), why: `${positions.length} open position${positions.length === 1 ? "" : "s"} · estimate from venue marks` };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [positions, marks.data, marks.dataUpdatedAt, marks.error, marks.isPending, now]);

  const ev = (L?.observedEvidence ?? {}) as Record<string, any>;
  const sup = ev.protection?.supervisor as Record<string, any> | undefined;
  const snapshot = ev.protection?.snapshot as Record<string, any> | null | undefined;
  const hb = ev.heartbeat as Record<string, any> | null | undefined;
  const kernelState = String(L?.kernelState ?? "UNKNOWN");
  const lastEvent = ev.control?.last_event as Record<string, any> | null | undefined;

  // ── safety tiles ──
  const tiles = useMemo(() => {
    const t: { l: string; v: string; tone: Tone; ok: boolean }[] = [];
    const ctlTone: Tone = control === "ACTIVE" ? "ok" : control === "FROZEN" ? "frozen" : control === "HALTED" ? "bad" : "warn";
    t.push({ l: "Trading", v: `${control}${lastEvent?.ts ? ` · last control event ${day(lastEvent.ts)}` : ""}`, tone: ctlTone, ok: control === "ACTIVE" || control === "FROZEN" });
    const ps = L?.protection?.status ?? "UNAVAILABLE";
    const syms = asRows(snapshot?.symbols);
    const withStop = syms.filter((r) => r.stop_present === true).length;
    t.push({
      l: "Protection",
      v: ps === "VERIFIED" ? `VERIFIED · ${withStop}/${syms.length} stops on exchange` : ps === "NO_POSITIONS" ? "NO_POSITIONS · nothing to protect" : `${ps} · not verified now`,
      tone: ps === "VERIFIED" || ps === "NO_POSITIONS" ? "ok" : ps === "UNPROTECTED" ? "bad" : "warn",
      ok: ps === "VERIFIED" || ps === "NO_POSITIONS",
    });
    const hf = L?.heartbeat?.freshness;
    t.push({
      l: "Heartbeat",
      v: !L?.heartbeat ? "Unavailable" : hf === "fresh" ? `Fresh · ${age(hb?.age_s)}` : `Stale · ${age(hb?.age_s)} old`,
      tone: !L?.heartbeat ? "warn" : hf === "fresh" ? "ok" : "bad",
      ok: hf === "fresh",
    });
    const outcome = sup?.outcome as string | undefined;
    const fresh = sup?.freshness === "fresh";
    t.push({
      l: "Supervisor",
      v: !sup ? "Unavailable" : `${outcome ? outcome[0] + outcome.slice(1).toLowerCase() : "Unknown"}${fresh ? "" : ` · last pass ${age(sup.age_s)} ago`}`,
      tone: !sup ? "warn" : outcome === "NEEDS_OWNER" ? "bad" : outcome === "SAFE" && fresh ? "ok" : "warn",
      ok: outcome === "SAFE" && fresh,
    });
    return t;
  }, [control, lastEvent, L, snapshot, hb, sup]);

  // ── exposure donut ──
  const exposure = useMemo(() => {
    const ps = (positions ?? []).filter((p) => p.notional !== null);
    const total = ps.reduce((a, p) => a + (p.notional ?? 0), 0);
    return { ps, total };
  }, [positions]);

  // ── needs you ──
  const [later, setLater] = useState<string[]>([]);
  const needs = useMemo(() => {
    const out: { id: string; t: string; w: string; dsc: string; cta: string; href: string }[] = [];
    const stopped = kernelState === "STOPPED" || L?.heartbeat?.freshness === "stale";
    if ((control === "FROZEN" || control === "HALTED") && stopped)
      out.push({
        id: "frozen-stopped",
        t: `Trading is ${control.toLowerCase()} and the kernel is ${kernelState === "UNKNOWN" ? "not reporting" : kernelState.toLowerCase()}`,
        w: lastEvent?.ts ? `last control event ${day(lastEvent.ts)}` : "",
        dsc: `Heartbeat ${age(hb?.age_s)} old.${asRows(sup ? [sup] : []).length && Array.isArray(sup?.reasons) && sup.reasons.length ? ` Supervisor reasons: ${sup.reasons.slice(0, 3).join(", ")}.` : ""} Resuming is an owner decision.`,
        cta: "Open Live System",
        href: "#live-system",
      });
    else if (sup?.needs_owner === true)
      out.push({ id: "sup-owner", t: "Supervisor needs the owner", w: sup.observed_at ? utc(sup.observed_at) : "", dsc: `Reasons: ${(sup.reasons ?? []).join(", ") || "not recorded"}.`, cta: "Open Operations", href: "#operations" });
    if (positions && positions.length) {
      const opened = positions.map((p) => p.openedAt).filter(Boolean).sort()[0] ?? null;
      const ds = opened ? Math.floor((now - +new Date(opened)) / 86400000) : null;
      if ((control !== "ACTIVE" || (ds ?? 0) >= 7))
        out.push({
          id: "open-positions",
          t: `${positions.length} position${positions.length === 1 ? "" : "s"} open${ds !== null ? ` for ${ds} days` : ""}`,
          w: opened ? day(opened) : "",
          dsc: `${positions.map((p) => base(p.symbol)).join(" and ")} ${positions.length === 1 ? "is" : "are"} held while trading is ${control.toLowerCase()}. Protection is ${(L?.protection?.status ?? "UNAVAILABLE").toLowerCase()} — journal stops are records, not venue proof.`,
          cta: "Review",
          href: "#trades",
        });
    }
    for (const i of asRows(needsQ.data?.items).filter((x) => x.validity === "VALID" && !x.receipt))
      out.push({ id: `approval:${String(i.item_id)}`, t: String(i.action_type ?? "Owner decision"), w: "approval", dsc: String(i.reason ?? ""), cta: "Decide in LUFFY", href: "#luffy" });
    return out;
  }, [control, kernelState, L, hb, sup, lastEvent, positions, now, needsQ.data]);
  const visibleNeeds = needs.filter((n) => !later.includes(n.id));

  const okCount = tiles.filter((x) => x.ok).length;

  return (
    <>
      <PageHeader title="Overview">
        <span className={`gl-state ${control === "ACTIVE" ? "live" : "blink"}`} style={{ color: COLOR[control === "ACTIVE" ? "ok" : control === "FROZEN" ? "frozen" : control === "HALTED" ? "bad" : "warn"] }} data-testid="control-state">
          <i />
          {control} · Binance {live ? "demo" : "fixture"} · data to {L?.account?.observedAt ? `${day(L.account.observedAt)} ${hhmm(L.account.observedAt)}` : "UNAVAILABLE"}
        </span>
      </PageHeader>

      {q.error && <div className="gl-err" role="alert">{(q.error as Error).message} Nothing was substituted. <button className="gl-btn" onClick={() => void q.refetch()}>Retry</button></div>}
      {live && L && Object.keys(L.errors).length > 0 && (
        <div className="gl-err" role="status" data-testid="missing-sections">
          Missing: {Object.entries(L.errors).map(([k, v]) => `${k} (${v})`).join(" · ")}. Nothing was substituted.
        </div>
      )}

      <section className="gl-row" aria-label="Key figures">
        <Card className="gl-kpis" label="Key figures">
          <span className="gl-sheen"><i /></span>
          <div className="gl-kpi">
            <span className="gl-hd"><span className="gl-ic" aria-hidden="true"><Wallet size={16} /></span><span className="lab">Unrealized P&amp;L</span></span>
            <span className="big" style={{ color: upnl.value === null ? GREY : upnl.value >= 0 ? OK : BAD }} data-testid="kpi-upnl">{upnl.value === null ? "UNAVAILABLE" : signed(upnl.value)}</span>
            <span className="sub">{upnl.why}</span>
          </div>
          <LastCloses desk={desk} />
          <div className="gl-kpi">
            <Donut ps={exposure.ps} total={exposure.total} />
            <span style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
              <span className="lab">Open exposure</span>
              {exposure.ps.length === 0 ? (
                <span style={{ fontSize: 13, color: "var(--txt2)" }}>{positions ? "No open positions" : "UNAVAILABLE"}</span>
              ) : (
                <>
                  <span style={{ fontSize: 13, color: "#c9d4df" }}>
                    {exposure.ps.map((p, i) => (<span key={p.id ?? p.symbol}>{i ? " · " : ""}<span style={{ color: hue(p.symbol) }}>●</span> {base(p.symbol)} {usd(p.notional, 0)}</span>))}
                  </span>
                  <span style={{ fontSize: 13, color: "var(--txt2)" }}>
                    {exposure.ps.map((p) => `${p.side.toLowerCase()}${p.leverage ? ` ${p.leverage}x` : ""}`).join(", ")}
                    <br />{lockedSummary(positions ?? [])}
                  </span>
                </>
              )}
            </span>
          </div>
        </Card>

        <Card className="gl-safety" label="Safety">
          <div className="gl-hd">
            <span className="gl-ic" aria-hidden="true"><Shield size={16} /></span><h2>Safety</h2><span className="fill" />
            <span style={{ fontSize: 12, color: okCount === 4 ? OK : WARN }}>{okCount} of 4 OK</span>
          </div>
          <div className="grid">
            {tiles.map((t) => (
              <div className="gl-tile" key={t.l} style={{ borderColor: t.tone === "ok" ? undefined : `${COLOR[t.tone]}55` }}>
                <i className={t.tone === "ok" ? "" : "blink"} style={{ background: COLOR[t.tone], boxShadow: `0 0 8px ${COLOR[t.tone]}` }} />
                <span><small>{t.l}</small><b style={{ color: COLOR[t.tone] }}>{t.v}</b></span>
              </div>
            ))}
          </div>
        </Card>
      </section>

      <div className="gl-row" style={{ flex: 1 }}>
        <div className="gl-col">
          <RealizedChart desk={desk} loading={deskQ.isPending && live} error={desk?.errors?.realized} />
          <SignalsCard desk={desk} loading={deskQ.isPending && live} />
          <PositionsCard positions={positions} pnlOf={pnlOf} />
        </div>

        <aside className="gl-side">
          <Card need className="pad" label="Needs you">
            <div className="gl-hd">
              <span className="gl-ic solid" aria-hidden="true"><Bell size={16} /></span><h2 style={{ color: "rgb(var(--a2))" }}>Needs you</h2><span className="fill" />
              <span className="gl-count" aria-label={`${visibleNeeds.length} items`}>{visibleNeeds.length}</span>
            </div>
            {visibleNeeds.map((n) => (
              <div className="gl-need-item" key={n.id}>
                <div className="top"><b>{n.t}</b><span style={{ fontSize: 12, color: GREY, whiteSpace: "nowrap" }}>{n.w}</span></div>
                <p>{n.dsc}</p>
                <div style={{ display: "flex", gap: 8, marginTop: 2 }}>
                  <a className="gl-btn primary" href={n.href}>{n.cta}</a>
                  <button type="button" className="gl-btn" onClick={() => setLater([...later, n.id])}>Later</button>
                </div>
              </div>
            ))}
            {visibleNeeds.length === 0 && (
              <p className="gl-note">
                {needs.length ? `${needs.length} item${needs.length === 1 ? "" : "s"} snoozed until reload.` : L?.needsYou ? "The Supervisor records no owner action and nothing else is pending." : "No Supervisor record is readable. This does not establish that nothing needs the owner."}
              </p>
            )}
            {needsQ.isError && <p className="gl-note">Approval ledger unavailable — pending approvals may exist.</p>}
          </Card>

          <ResearchCard desk={desk} sys={sysQ.data} loading={deskQ.isPending && live} />
          <SystemCard sys={sysQ.data} control={control} hbFresh={L?.heartbeat?.freshness} naked={ev.protection?.naked_exposure} loading={sysQ.isPending && live} />
        </aside>
      </div>
    </>
  );
}

function lockedSummary(ps: Position[]) {
  const withStop = ps.filter((p) => p.journalStop?.price && p.entryPrice);
  if (!withStop.length) return "journal stops not recorded";
  const locked = withStop.filter((p) => (p.side.toLowerCase() === "long" ? p.journalStop!.price! > p.entryPrice! : p.journalStop!.price! < p.entryPrice!)).length;
  return locked === withStop.length ? "journal stops all beyond entry" : `${locked}/${withStop.length} journal stops beyond entry`;
}

function LastCloses({ desk }: { desk?: Desk }) {
  const closes = desk?.realized?.last_closes ?? [];
  const wins = closes.filter((c) => c.pnl > 0).length;
  const net = closes.reduce((a, c) => a + c.pnl, 0);
  const mx = Math.max(1e-9, ...closes.map((c) => Math.abs(c.pnl)));
  return (
    <div className="gl-kpi">
      <span className="gl-hd"><span className="gl-ic ok" aria-hidden="true"><TrendingUp size={16} /></span><span className="lab">Last {closes.length || 10} closes</span></span>
      <span className="big" style={{ color: closes.length ? "rgb(var(--hi))" : GREY }}>{closes.length ? `${wins} won · ${closes.length - wins} lost` : "UNAVAILABLE"}</span>
      <span className="sub">{closes.length ? `net ${signed(net)}` : desk ? "no closed trades with known P&L" : "loading…"}</span>
      <div className="gl-bars" aria-hidden="true">
        {closes.map((c) => (<span key={c.trade_id} title={`${base(c.symbol)} ${signed(c.pnl)}`} style={{ height: `${Math.max(8, Math.round((Math.abs(c.pnl) / mx) * 100))}%`, background: c.pnl > 0 ? OK : BAD }} />))}
      </div>
    </div>
  );
}

function Donut({ ps, total }: { ps: Position[]; total: number }) {
  const C = 2 * Math.PI * 42;
  let acc = 0;
  return (
    <svg width="104" height="104" viewBox="0 0 104 104" role="img" aria-label={`Deployed notional ${usd(total, 0)} split between ${ps.length} open positions`} style={{ flex: "0 0 104px" }}>
      <circle cx="52" cy="52" r="42" fill="none" stroke="#1d3042" strokeWidth="9" />
      {ps.map((p) => {
        const len = total ? ((p.notional ?? 0) / total) * C : 0;
        const el = <circle className="gl-gauge" key={p.id ?? p.symbol} cx="52" cy="52" r="42" fill="none" stroke={hue(p.symbol)} strokeWidth="9" strokeDasharray={`${Math.max(0, len - (ps.length > 1 ? 2 : 0))} ${C}`} strokeDashoffset={-acc} transform="rotate(-90 52 52)" />;
        acc += len;
        return el;
      })}
      <text x="52" y="50" textAnchor="middle" fontFamily="JetBrains Mono" fontSize="15" fill="rgb(228 244 252)">{ps.length ? usd(total, 0) : "—"}</text>
      <text x="52" y="66" textAnchor="middle" fontFamily="Manrope" fontSize="10" fill="#a9b8c8">deployed</text>
    </svg>
  );
}

type TF = "7D" | "30D" | "ALL";
function RealizedChart({ desk, loading, error }: { desk?: Desk; loading: boolean; error?: string }) {
  const [tf, setTf] = useState<TF>("ALL");
  const all = desk?.realized?.points ?? [];
  const view = useMemo(() => {
    if (!all.length) return null;
    const end = +new Date(all[all.length - 1].closed_at);
    const span = { "7D": 7, "30D": 30, ALL: 99999 }[tf] * 86400000;
    let pts = all.filter((p) => +new Date(p.closed_at) >= end - span);
    if (pts.length < 2) pts = all.slice(-2);
    const baseline = tf === "ALL" ? 0 : (all[all.indexOf(pts[0]) - 1]?.cumulative ?? 0);
    const series = pts.map((p) => ({ t: +new Date(p.closed_at), v: p.cumulative - baseline, p }));
    if (tf === "ALL") series.unshift({ t: series[0].t - 3600000, v: 0, p: null as unknown as DeskPoint });
    const lo = Math.min(0, ...series.map((s) => s.v)), hi = Math.max(0, ...series.map((s) => s.v));
    const t0 = series[0].t, t1 = series[series.length - 1].t;
    const X = (t: number) => (((t - t0) / Math.max(1, t1 - t0)) * 800).toFixed(1);
    const Y = (v: number) => (200 - ((v - lo) / Math.max(1e-9, hi - lo)) * 180).toFixed(1);
    const curve = series.map((s, i) => `${i ? "L" : "M"}${X(s.t)} ${Y(s.v)}`).join(" ");
    const labels = [0, 0.25, 0.5, 0.75, 1].map((f) => day(new Date(t0 + f * (t1 - t0)).toISOString()));
    return { curve, area: `${curve} L800 220 L0 220 Z`, zero: Y(0), labels, change: series[series.length - 1].v, n: pts.length, lo, hi };
  }, [all, tf]);
  return (
    <Card className="pad" label="Realized P&L">
      <CardHead icon={<LineChart size={16} />} title="Realized P&L" sub={view ? <span style={{ color: view.change >= 0 ? OK : BAD }}>{signed(view.change)} {tf === "ALL" ? "since first close" : "in this window"}</span> : undefined}>
        <div style={{ display: "flex", gap: 6 }}>
          {(["7D", "30D", "ALL"] as TF[]).map((t) => (<button key={t} type="button" className="gl-tf" aria-pressed={tf === t} onClick={() => setTf(t)}>{t}</button>))}
        </div>
      </CardHead>
      {view ? (
        <>
          <svg className="gl-chart" viewBox="0 0 800 220" preserveAspectRatio="none" role="img" aria-label={`Cumulative journal-booked realized profit and loss across ${view.n} closed trades, ${signed(view.lo)} to ${signed(view.hi)}`}>
            <defs><linearGradient id="gl-g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style={{ stopColor: "rgb(var(--a))" }} stopOpacity=".32" /><stop offset="1" style={{ stopColor: "rgb(var(--a))" }} stopOpacity="0" /></linearGradient></defs>
            <path d="M0 55H800M0 110H800M0 165H800" stroke="#1d3042" strokeWidth="1" strokeDasharray="3 5" vectorEffect="non-scaling-stroke" />
            <path d={`M0 ${view.zero}H800`} stroke="#8d9fb2" strokeWidth="1" strokeDasharray="2 4" vectorEffect="non-scaling-stroke" />
            <path className="gl-area" d={view.area} fill="url(#gl-g)" />
            <path className="gl-curve" d={view.curve} fill="none" style={{ stroke: "rgb(var(--a))" }} strokeWidth="2.5" vectorEffect="non-scaling-stroke" />
          </svg>
          <div className="gl-xl">{view.labels.map((x, i) => (<span key={i}>{x}</span>))}</div>
          <p className="gl-note">
            {desk!.realized!.closed_trades} closed trades · journal-booked, not venue net
            {desk!.realized!.unknown_pnl_trades ? ` · ${desk!.realized!.unknown_pnl_trades} with unknown P&L left out` : ""}
            {desk!.realized!.estimated_pnl_trades ? ` · ${desk!.realized!.estimated_pnl_trades} estimated` : ""}. Dashed line = zero; windows end at the last close.
          </p>
        </>
      ) : (
        <p className="gl-empty">{loading ? "Loading closed trades…" : error ? `Unavailable: ${error}` : "No closed trades with a known P&L."}</p>
      )}
    </Card>
  );
}

function SignalsCard({ desk, loading }: { desk?: Desk; loading: boolean }) {
  const scan = desk?.scan;
  const coins = scan?.coins ?? [];
  const ratio = (c: DeskCoin) => (c.score !== null && c.threshold ? Math.abs(c.score) / c.threshold : 0);
  const top = [...coins].sort((a, b) => ratio(b) - ratio(a)).slice(0, 5);
  const [open, setOpen] = useState<DeskCoin | null>(null);
  const sc = (z: number, th: number) => 50 + Math.max(-1, Math.min(1, z / (th * 1.25))) * 50;
  return (
    <Card className="pad" label="Latest cycle signals">
      <CardHead icon={<Eye size={16} />} title="Latest cycle · signals" tone="teal" sub={scan ? `${day(scan.observed_at)} ${hhmm(scan.observed_at)} · ${coins.length} coins scored · ${scan.traded} traded` : undefined}>
        <span style={{ fontSize: 12, color: GREY }}>strongest 5 shown</span>
      </CardHead>
      {!scan ? (
        <p className="gl-empty">{loading ? "Loading the latest scan…" : desk?.errors?.scan ?? "No scan decisions are recorded."}</p>
      ) : (
        <>
          <div className="gl-sigs">
            {top.map((c) => (
              <button key={c.decision_id} type="button" className="gl-sig" onClick={() => setOpen(c)} aria-label={`${base(c.symbol)} decision detail`}>
                <span style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span className="gl-coin" style={{ background: hue(c.symbol) }}>{base(c.symbol)[0]}</span>
                  <span style={{ display: "flex", flexDirection: "column" }}><b style={{ fontWeight: 600 }}>{base(c.symbol)}-USDT</b><span className="m" style={{ fontSize: 11, color: GREY }}>cycle {hhmm(scan.observed_at)}</span></span>
                </span>
                <span style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <span style={{ fontSize: 12, color: "#c9d4df" }}>{c.score === null ? "score UNAVAILABLE" : `score ${c.score >= 0 ? "+" : "−"}${Math.abs(c.score).toFixed(3)} · needed ±${(c.threshold ?? NaN).toFixed(3)}`}</span>
                  <span className="gl-meter"><i style={{ width: `${Math.min(100, Math.round(ratio(c) * 100))}%` }} /></span>
                </span>
                <span style={{ display: "flex", flexDirection: "column", gap: 3, alignItems: "flex-start" }}>
                  <span className="gl-pill" style={{ background: c.action === "LONG" ? "#3ddc9722" : c.action === "SHORT" ? "#ff7b7222" : "#ffffff14", color: c.action === "LONG" ? OK : c.action === "SHORT" ? BAD : "#c9d4df" }}>{c.action}</span>
                  <span className="m" style={{ fontSize: 11, color: GREY }}>{c.confidence === null ? "—" : `${Math.round(c.confidence * 100)}%`} conf.</span>
                </span>
                <span style={{ fontSize: 13, color: "#b9c5d1", lineHeight: 1.4 }}>
                  {c.skip_reason ?? (c.score !== null && c.threshold ? `Leaning ${c.score >= 0 ? "long" : "short"} but only ${Math.round(ratio(c) * 100)}% of the way to the threshold.` : "No score recorded.")}
                </span>
                <span style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: c.executed ? OK : GREY }}><i style={{ width: 7, height: 7, borderRadius: "50%", background: c.executed ? OK : GREY }} />{c.executed ? "Order sent" : "No trade"}</span>
              </button>
            ))}
          </div>
          <div className="gl-allc">
            <div style={{ display: "flex", flexWrap: "wrap", gap: "8px 18px", alignItems: "baseline" }}>
              <span className="cz" style={{ fontSize: 12, color: "rgb(var(--hi))" }}>All {coins.length} coins · score vs the bar to trade</span>
              <span style={{ fontSize: 12, color: "var(--txt2)" }}>amber lines = threshold · {scan.traded === 0 ? "nothing crossed it, so no coin was traded" : `${scan.traded} traded`}</span>
            </div>
            <div className="grid">
              {coins.map((c) => {
                const th = c.threshold ?? 0.3, v = c.score ?? 0, p = sc(v, th);
                const crossed = Math.abs(v) >= th;
                const col = crossed ? OK : v >= 0 ? "#7fd3ff" : "#ff9d95";
                return (
                  <button key={c.decision_id} type="button" onClick={() => setOpen(c)} aria-label={`${base(c.symbol)} score ${v.toFixed(3)}, open detail`}>
                    <span className="m" style={{ color: "#c9d4df" }}>{base(c.symbol)}</span>
                    <span className="gl-track"><u style={{ left: `${sc(-th, th)}%` }} /><u style={{ left: `${sc(th, th)}%` }} /><u className="mid" /><b style={{ left: `${Math.min(p, 50)}%`, width: `${Math.max(1, Math.abs(p - 50))}%`, background: col }} /></span>
                    <span className="m" style={{ textAlign: "right", color: col }}>{c.score === null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(3)}`}</span>
                  </button>
                );
              })}
            </div>
            <span style={{ fontSize: 12, color: GREY }}>Select a coin for its analyst votes and the recorded reasoning.</span>
          </div>
        </>
      )}
      <DecisionPanel coin={open} scanAt={scan?.observed_at} onClose={() => setOpen(null)} />
    </Card>
  );
}

function DecisionPanel({ coin, scanAt, onClose }: { coin: DeskCoin | null; scanAt?: string; onClose: () => void }) {
  const q = useRead(coin ? `query/decision?identity=${encodeURIComponent(coin.decision_id)}` : null);
  const value = rec(asRows(q.data?.records)[0]?.value) as Record<string, any> | null;
  const votes = asRows(value?.votes);
  const cycle = rec(value?.cycle);
  return (
    <DetailPanel open={!!coin} onClose={onClose} title={coin ? `${base(coin.symbol)}-USDT · ${coin.action}` : ""} description={coin ? `Decision ${coin.decision_id} · scan ${hhmm(scanAt)}` : undefined}>
      {coin && (
        <>
          <KV items={[
            ["Score", coin.score === null ? null : coin.score.toFixed(4)],
            ["Needed to trade", coin.threshold === null ? null : `±${coin.threshold.toFixed(4)}`],
            ["Confidence", coin.confidence === null ? null : `${Math.round(coin.confidence * 100)}%`],
            ["Outcome", coin.executed ? "Order sent" : coin.skip_reason ?? "No trade"],
            ["Regime", cycle?.regime ? String(cycle.regime) : null],
            ["Price at cycle", typeof cycle?.price === "number" ? px(cycle.price) : null],
          ]} />
          <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Analyst votes</h3>
          {q.isPending && <p className="gl-note">Loading recorded votes…</p>}
          {q.isError && <p className="gl-err">Decision evidence unavailable: {(q.error as Error).message}</p>}
          {q.data && votes.length === 0 && <p className="gl-note">No analyst votes are recorded for this decision{value?.votes_basis?.reason ? ` (${value.votes_basis.reason})` : ""}.</p>}
          {votes.length > 0 && (
            <DataTable
              label="Analyst votes"
              rows={votes}
              rowId={(v) => `${v.agent}`}
              pageSizes={[6]}
              cols={[
                { key: "agent", label: "Analyst", value: (v) => String(v.agent), filter: "select" },
                { key: "side", label: "Side", value: (v) => String(v.side), filter: "select" },
                { key: "conviction", label: "Conviction", align: "r", value: (v) => (typeof v.conviction === "number" ? v.conviction : null), cell: (v) => (typeof v.conviction === "number" ? v.conviction.toFixed(3) : "—") },
                { key: "why", label: "Rationale", value: (v) => String(v.rationale ?? ""), filter: "text", cell: (v) => <span style={{ fontFamily: "var(--sans)", fontSize: 13 }}>{String(v.rationale ?? "—")}</span> },
              ] as Col<Record<string, unknown>>[]}
            />
          )}
          <p className="gl-note">Journal-recorded decision; read-only. <a className="gl-link" href={`#operations?id=${encodeURIComponent(coin.decision_id)}`}>Open in Operations</a></p>
        </>
      )}
    </DetailPanel>
  );
}

function PositionsCard({ positions, pnlOf }: { positions: Position[] | null; pnlOf: (p: Position) => number | null }) {
  const [open, setOpen] = useState<Position | null>(null);
  const stopInfo = (p: Position) => {
    const stop = p.journalStop?.price ?? null;
    if (!stop || !p.entryPrice) return null;
    const dir = p.side.toLowerCase() === "long" ? 1 : -1;
    return { pct: ((stop - p.entryPrice) / p.entryPrice) * 100 * dir, stop };
  };
  const cols: Col<Position>[] = [
    { key: "sym", label: "Instrument", primary: true, filter: "text", value: (p) => p.symbol, cell: (p) => (<><span className="gl-coin" style={{ background: hue(p.symbol), fontSize: 13 }}>{base(p.symbol)[0]}</span><span style={{ fontWeight: 600 }}>{base(p.symbol)}-USDT</span></>) },
    { key: "side", label: "Side", filter: "select", value: (p) => `${p.side.toUpperCase()}${p.leverage ? ` ${p.leverage}x` : ""}`, cell: (p) => (<span className="m gl-pill" style={{ background: p.side.toLowerCase() === "long" ? "#3ddc971f" : "#ff7b7222", color: p.side.toLowerCase() === "long" ? OK : BAD, fontWeight: 400 }}>{p.side.toUpperCase()}{p.leverage ? ` ${p.leverage}x` : ""}</span>) },
    { key: "entry", label: "Entry", align: "r", value: (p) => p.entryPrice ?? null, cell: (p) => px(p.entryPrice) },
    { key: "size", label: "Size", align: "r", value: (p) => p.amount ?? null, cell: (p) => (p.amount == null ? "UNAVAILABLE" : `${p.amount} ${base(p.symbol)}`) },
    {
      key: "stop", label: "Stop (profit locked)", value: (p) => stopInfo(p)?.pct ?? null,
      cell: (p) => {
        const s = stopInfo(p);
        if (!s) return <span style={{ color: GREY }}>not recorded</span>;
        return (
          <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span role="img" aria-label={`stop ${s.pct >= 0 ? "beyond" : "short of"} entry by ${Math.abs(s.pct).toFixed(1)}%`} style={{ flex: 1, maxWidth: 110, height: 6, borderRadius: 3, background: "#1d3042", overflow: "hidden" }}>
              <span style={{ display: "block", height: "100%", width: `${Math.min(100, (Math.abs(s.pct) / 25) * 100)}%`, background: s.pct >= 0 ? OK : WARN }} />
            </span>
            <span className="m" style={{ fontSize: 12, color: "var(--txt2)" }}>{px(s.stop)} · {s.pct >= 0 ? "+" : "−"}{Math.abs(s.pct).toFixed(1)}%</span>
          </span>
        );
      },
    },
    { key: "notional", label: "Notional", align: "r", value: (p) => p.notional, cell: (p) => <span style={{ fontSize: 15 }}>{usd(p.notional)}</span> },
  ];
  const strategies = [...new Set((positions ?? []).map((p) => p.strategyName).filter(Boolean))];
  const since = (positions ?? []).map((p) => p.openedAt).filter(Boolean).sort()[0];
  return (
    <Card className="pad" label="Open positions">
      <CardHead icon={<ArrowLeftRight size={16} />} title="Open positions" sub={positions ? `${positions.length} open${since ? ` since ${day(since)}` : ""}${strategies.length ? ` · ${strategies.join(", ")}` : ""}` : undefined}>
        <a className="gl-link" href="#trades">All trades →</a>
      </CardHead>
      {positions === null ? (
        <p className="gl-empty">UNAVAILABLE — open positions could not be read.</p>
      ) : (
        <DataTable rows={positions} cols={cols} rowId={(p) => p.id ?? p.symbol} onOpen={setOpen} pageSizes={[5]} label="Open positions" empty="No open positions." />
      )}
      <DetailPanel open={!!open} onClose={() => setOpen(null)} title={open ? `${base(open.symbol)}-USDT · ${open.side.toUpperCase()}${open.leverage ? ` ${open.leverage}x` : ""}` : ""} description={open ? `Journal trade ${open.id ?? "—"} · opened ${open.openedAt ? utc(open.openedAt) : "UNAVAILABLE"}` : undefined}>
        {open && (
          <>
            <KV items={[
              ["Entry price", open.entryPrice == null ? null : px(open.entryPrice)],
              ["Size", open.amount == null ? null : `${open.amount} ${base(open.symbol)}`],
              ["Notional (entry)", open.notional == null ? null : usd(open.notional)],
              ["Unrealized P&L", pnlOf(open) === null ? "UNAVAILABLE — no fresh mark" : signed(pnlOf(open)!)],
              ["Strategy", open.strategyName ?? null],
              ["Journal stop", open.journalStop?.price == null ? "not recorded" : `${px(open.journalStop.price)} (${open.journalStop.orderRefRecorded ? "order id recorded" : "no order id"})`],
              ["Protection", open.protectionDetail?.status ?? open.protection],
              ["Protection checked", open.protectionDetail?.observedAt ? utc(open.protectionDetail.observedAt) : "never verified"],
            ]} />
            {open.protectionDetail?.reasons?.length ? <p className="gl-note">Reasons: {open.protectionDetail.reasons.join(", ")}</p> : null}
            <p className="gl-note">The journal stop is a record, not venue verification. Unrealized P&amp;L is an estimate from venue marks.</p>
            <div className="gl-actions"><a className="gl-btn primary" href="#trades">Open in Trades</a></div>
          </>
        )}
      </DetailPanel>
    </Card>
  );
}

function ResearchCard({ desk, sys, loading }: { desk?: Desk; sys?: Record<string, unknown>; loading: boolean }) {
  const r = desk?.research;
  const node = asRows(sys?.nodes).find((n) => n.id === "research");
  const tele = rec(node?.telemetry) as Record<string, any> | null;
  const active = tele?.freshness === "fresh" && tele?.health === "active";
  const total = r ? Object.values(r.counts).reduce<number>((a, b) => a + (b ?? 0), 0) : null;
  const line = (n: string, v: number | null | undefined) => ({ n, v: v === null || v === undefined ? "unavailable" : String(v), c: v === null || v === undefined ? GREY : v > 0 ? OK : GREY });
  return (
    <Card className="pad" label="Research">
      <CardHead icon={<Search size={16} />} title="Research" tone="teal" />
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <span className={active ? "gl-scan" : ""} style={active ? undefined : { width: 30, height: 30, borderRadius: "50%", border: "3px solid #1d3042" }} aria-hidden="true" />
        <span style={{ display: "flex", flexDirection: "column" }}>
          <span style={{ color: active ? "#3fd5c8" : "var(--txt2)", fontWeight: 600 }}>{active ? "Research active" : loading ? "Loading…" : "Research idle"}</span>
          <span style={{ fontSize: 12, color: "var(--txt2)" }}>
            {r ? `${total} ledger record${total === 1 ? "" : "s"} · discovery only · referee ${r.referee_enabled ? "ON" : "OFF"} · handoff ${r.handoff_enabled ? "open" : "closed"}` : "ledger unavailable"}
            {!active && tele ? ` · telemetry ${tele.freshness}` : ""}
          </span>
        </span>
      </div>
      {r && [line("Rules looked at", r.counts.combos), line("Candidates", r.counts.candidates), line("Held-out looks", r.counts.tests), line("Questions", r.counts.questions)].map((x) => (
        <div className="gl-list" key={x.n}><span><i style={{ background: x.c }} />{x.n}</span><span style={{ color: x.c }}>{x.v}</span></div>
      ))}
      <a className="gl-link" href="#research">Open Research →</a>
    </Card>
  );
}

function SystemCard({ sys, control, hbFresh, naked, loading }: { sys?: Record<string, unknown>; control: string; hbFresh?: string; naked?: Record<string, any>; loading: boolean }) {
  const nodes = asRows(sys?.nodes);
  const tone = (n: Record<string, any>): Tone => {
    const t = rec(n.telemetry) as Record<string, any> | null;
    if (!t || t.freshness === "unavailable") return "grey";
    if (t.health === "failing") return "bad";
    if (t.health === "degraded" || t.freshness === "stale") return "warn";
    return t.health === "active" || t.health === "idle" ? "ok" : "grey";
  };
  const tones = nodes.map(tone);
  const verdict = !nodes.length ? "UNAVAILABLE" : tones.includes("bad") ? "Failing" : tones.includes("warn") ? "Degraded" : tones.every((t) => t === "grey") ? "No telemetry" : "Healthy";
  const vc = verdict === "Failing" ? BAD : verdict === "Healthy" ? OK : WARN;
  return (
    <a href="#live-system" className="gl-card pad" style={{ textDecoration: "none", color: "inherit" }} aria-label="Live system — open">
      <CardHead icon={<Activity size={16} />} title="Live system"><span style={{ fontSize: 12, color: vc }}>{loading ? "Loading…" : verdict}</span></CardHead>
      <div className="gl-comps" style={{ gridTemplateColumns: `repeat(${Math.max(1, nodes.length)}, minmax(0, 1fr))` }}>
        {nodes.map((n, i) => (<span key={String(n.id)} title={`${n.label}: ${(rec(n.telemetry) as any)?.freshness ?? "no telemetry"}`} style={{ background: `${COLOR[tones[i]]}26`, borderColor: COLOR[tones[i]] }}><i style={{ background: COLOR[tones[i]] }} /></span>))}
      </div>
      <div className="gl-triple">
        <span><b style={{ color: control === "FROZEN" ? FROZEN : control === "ACTIVE" ? OK : WARN }}>{control}</b><small>trading</small></span>
        <span><b style={{ color: hbFresh === "fresh" ? OK : BAD }}>{hbFresh ?? "unknown"}</b><small>heartbeat</small></span>
        <span><b style={{ color: naked?.value === 0 ? OK : naked?.value > 0 ? BAD : GREY }}>{naked?.value ?? "unknown"}</b><small>unprotected</small></span>
      </div>
    </a>
  );
}
