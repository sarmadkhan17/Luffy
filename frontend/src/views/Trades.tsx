/** Trades — approved "Final · 2" design. Journal trades, newest first, with
 * sort/filter in the headers, pagination, a centred trade story (opens on
 * click) and the owner controls in the header. P&L is journal-booked. */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftRight } from "lucide-react";
import { usePreview } from "../context";
import { useRead, rows as asRows, rec } from "../components/records";
import { Card, CardHead, DataTable, DetailPanel, KV, type Col } from "../components/glass";
import { OwnerControls } from "../components/OwnerControls";
import { PageHeader } from "../components/PageHeader";
import { timestamp as utc } from "../time";
import type { Position } from "../adapters/contracts";

type Trade = Record<string, any>;
const OK = "#3ddc97", BAD = "#ff7b72", WARN = "#f2b44a", OPEN = "#56ccf2", GREY = "#8d9fb2";
const PALETTE = ["#9be3c6", "#c9d4df", "#ffb86b", "#b6509e", "#7fd3ff", "#ff6b6b", "#a78bfa", "#e2b45c"];
const hue = (s: string) => PALETTE[[...s].reduce((a, c) => a + c.charCodeAt(0), 0) % PALETTE.length];
const base = (s: string) => String(s).split("/")[0].split("-")[0];
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const money = (n: number | null) => (n === null ? "UNAVAILABLE" : `${n >= 0 ? "+" : "−"}$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
const price = (n: number | null) => (n === null ? "—" : n >= 100 ? n.toFixed(2) : n >= 1 ? n.toFixed(3) : n.toPrecision(4));
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const opened = (iso: string) => { const d = new Date(iso); return Number.isNaN(+d) ? "UNAVAILABLE" : `${String(d.getUTCDate()).padStart(2, "0")} ${MON[d.getUTCMonth()]} ${d.toISOString().slice(11, 16)}`; };
const dur = (ms: number) => { const m = Math.max(0, Math.floor(ms / 60000)); const d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60); return d ? `${d}d ${h}h` : h ? `${h}h ${m % 60}m` : `${m}m`; };

/** Risk in USDT at entry: price distance to the initial stop × size. */
export const riskUsd = (t: Trade) => { const r = num(t.initial_risk), a = num(t.amount); return r && a ? r * a : null; };
export const rMultiple = (t: Trade) => { const p = num(t.realized_pnl), r = riskUsd(t); return t.status === "closed" && p !== null && r ? p / r : null; };
export const statusOf = (t: Trade) => {
  if (t.status !== "closed") return "Open";
  const c = String(t.close_reason ?? "");
  if (c === "sl_fill") return "Stopped";
  if (c) return c.split("=")[0].replaceAll("_", " ").replace(/^./, (x) => x.toUpperCase());
  const p = num(t.realized_pnl);
  return p === null ? "Closed" : p > 0 ? "Won" : "Lost";
};
const statusColor = (s: string) => (s === "Open" ? OPEN : s === "Won" || /flip/i.test(s) ? OK : s === "Stopped" || s === "Lost" ? BAD : WARN);

async function loadAll(adapter: ReturnType<typeof usePreview>["adapter"], signal: AbortSignal) {
  const out: Trade[] = [];
  let cursor: string | null = null, total = 0, pages = 0;
  do {
    const p = await adapter.tradePage!({ status: "all", cursor, limit: 200 }, signal);
    out.push(...(p.rows as Trade[]));
    total = p.total;
    cursor = p.hasMore ? p.nextCursor : null;
  } while (cursor && ++pages < 10);
  return { rows: out, total, truncated: out.length < total };
}

export default function Trades() {
  const { adapter } = usePreview();
  const live = adapter.mode === "LIVE";
  const q = useQuery({
    queryKey: ["trades-all"],
    queryFn: ({ signal }) => loadAll(adapter, signal),
    enabled: !!adapter.tradePage,
    refetchInterval: 60000,
    refetchIntervalInBackground: false,
  });
  const ov = useQuery({
    queryKey: ["overview", "normal"],
    queryFn: ({ signal }) => adapter.overview("normal", signal),
    staleTime: 10000,
    refetchInterval: 30000,
    refetchIntervalInBackground: false,
  });
  const control = ov.data?.control ?? "UNAVAILABLE";
  const positions: Position[] | null = ov.data?.positions ?? null;
  const sup = (ov.data?.live?.observedEvidence as Record<string, any> | undefined)?.protection?.supervisor;
  const supNote = sup?.outcome && sup.outcome !== "SAFE" ? `Supervisor last reported ${sup.outcome} — resume may be refused until its checks pass.` : undefined;
  const [open, setOpen] = useState<Trade | null>(null);
  const [shownRows, setShownRows] = useState<Trade[] | null>(null);
  const now = Date.now();

  const cols: Col<Trade>[] = useMemo(() => [
    { key: "coin", label: "Instrument", primary: true, filter: "select", value: (t) => `${base(t.symbol)}-USDT`, cell: (t) => (<><span className="gl-coin" style={{ background: hue(base(t.symbol)), fontSize: 13 }}>{base(t.symbol)[0]}</span><span style={{ fontWeight: 600 }}>{base(t.symbol)}-USDT</span></>) },
    { key: "side", label: "Side", filter: "select", value: (t) => String(t.side).toUpperCase(), cell: (t) => (<span className="gl-pill" style={{ fontWeight: 400, background: String(t.side).toLowerCase() === "long" ? "#3ddc9724" : "#ff7b7224", color: String(t.side).toLowerCase() === "long" ? OK : BAD }}>{String(t.side).toUpperCase()}</span>) },
    { key: "strat", label: "Strategy", filter: "select", value: (t) => t.strategy_name || t.strategy_id || null, cell: (t) => <span style={{ fontSize: 13 }}>{t.strategy_name || t.strategy_id || "—"}</span> },
    { key: "opened", label: "Opened", value: (t) => String(t.opened_at), cell: (t) => <span className="m" style={{ fontSize: 13 }}>{opened(t.opened_at)}</span> },
    { key: "held", label: "Held", value: (t) => (t.closed_at ? +new Date(t.closed_at) - +new Date(t.opened_at) : now - +new Date(t.opened_at)), cell: (t) => <span className="m" style={{ fontSize: 13 }}>{t.closed_at ? dur(+new Date(t.closed_at) - +new Date(t.opened_at)) : `${dur(now - +new Date(t.opened_at))}+ (open)`}</span> },
    { key: "px", label: "Entry → Exit", value: (t) => num(t.entry_price), cell: (t) => <span className="m" style={{ fontSize: 13 }}>{price(num(t.entry_price))} → {t.status === "closed" ? price(num(t.exit_price)) : "open"}</span> },
    { key: "r", label: "R", align: "r", value: (t) => rMultiple(t), cell: (t) => { const r = rMultiple(t); return <span style={{ color: r === null ? GREY : r >= 0 ? OK : BAD }}>{t.status !== "closed" ? "—" : r === null ? "n/a" : `${r >= 0 ? "+" : "−"}${Math.abs(r).toFixed(1)}R`}</span>; } },
    { key: "pnl", label: "P&L", align: "r", value: (t) => (t.status === "closed" ? num(t.realized_pnl) : null), cell: (t) => { const p = num(t.realized_pnl); return t.status !== "closed" ? <span style={{ color: OPEN }}>open</span> : <span style={{ color: p === null ? GREY : p >= 0 ? OK : BAD }}>{money(p)}</span>; } },
    { key: "status", label: "Status", filter: "select", value: (t) => statusOf(t), cell: (t) => { const s = statusOf(t); return <span style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: statusColor(s) }}><i style={{ width: 7, height: 7, borderRadius: "50%", background: statusColor(s) }} />{s}</span>; } },
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ], []);

  return (
    <>
      <PageHeader title="Trades">
        <span className="gl-state live" style={{ color: OK }}><i />{live ? "Live · Binance demo" : "Fixture"}</span>
        {live && <OwnerControls control={control} positions={positions} supervisorNote={supNote} />}
      </PageHeader>

      <Card className="pad" label="Trade log">
        <CardHead icon={<ArrowLeftRight size={16} />} title="Trade log" sub={shownRows && q.data ? (() => { const net = shownRows.reduce((a, t) => a + (t.status === "closed" ? (num(t.realized_pnl) ?? 0) : 0), 0); const unknown = shownRows.filter((t) => t.status === "closed" && num(t.realized_pnl) === null).length; return `${shownRows.length} of ${q.data.total} trades · net ${money(net)} journal-booked${unknown ? ` · ${unknown} unknown P&L not counted` : ""}`; })() : undefined}>
          <span style={{ fontSize: 12, color: GREY }}>Click a trade to open its story</span>
        </CardHead>
        {q.error && <div className="gl-err" role="alert">{(q.error as Error).message} Nothing was substituted. <button type="button" className="gl-btn" onClick={() => void q.refetch()}>Retry</button></div>}
        {q.isPending && adapter.tradePage && <p className="gl-empty">Loading trades…</p>}
        {!adapter.tradePage && <p className="gl-empty">Trade history is unavailable in this mode.</p>}
        {q.data && (
          <>
            <DataTable
              label="Trade log"
              rows={q.data.rows}
              cols={cols}
              rowId={(t) => String(t.id)}
              onOpen={setOpen}
              empty="No trades recorded."
              onShown={setShownRows}
            />
            {q.data.truncated && <p className="gl-note">Showing the newest {q.data.rows.length} of {q.data.total} trades.</p>}
          </>
        )}
      </Card>
      <TradeStory trade={open} onClose={() => setOpen(null)} />
    </>
  );
}

function TradeStory({ trade, onClose }: { trade: Trade | null; onClose: () => void }) {
  const lq = useRead(trade ? `trades/${encodeURIComponent(String(trade.id))}/lineage` : null);
  const L = rec(lq.data) as Record<string, any> | null;
  const chain = Object.fromEntries(asRows(L?.chain).map((c) => [String(c.step), c]));
  const votes = asRows(L?.votes);
  const decision = rec(L?.decision) as Record<string, any> | null;
  const outcome = rec(L?.outcome) as Record<string, any> | null;
  const unavailable = asRows(L?.unavailable).map((u) => String(u.field));
  if (!trade) return <DetailPanel open={false} onClose={onClose} title="" children={null} />;
  const t = trade;
  const closed = t.status === "closed";
  const entry = num(t.entry_price), exit = num(t.exit_price), stop = num(t.stop_loss);
  const pnl = num(t.realized_pnl), r = rMultiple(t);
  const s = statusOf(t);
  const heldMs = (closed ? +new Date(t.closed_at) : Date.now()) - +new Date(t.opened_at);
  const signal = asRows(decision?.signals)[0];
  const fwd = outcome && num(outcome.fwd_ret_4h);
  const steps: { n: string; at: string; x: string }[] = [
    { n: "Spotted", at: "before entry", x: chain.opportunity ? String(chain.opportunity.summary) : lq.isPending ? "Loading…" : "No attention-scan record is linked." },
    { n: "Analysed", at: "before entry", x: `${votes.length ? `${votes.length} analyst votes` : "No analyst votes recorded"}${chain.market_context ? ` · ${chain.market_context.summary}` : ""}` },
    { n: "Decided", at: decision?.ts ? opened(String(decision.ts)) + " UTC" : "before entry", x: `${chain.decision ? String(chain.decision.summary) : "No decision linked"}${signal?.rationale ? ` · ${signal.rationale}` : ""}` },
    { n: "Entered", at: `${opened(t.opened_at)} UTC`, x: `${String(t.side).toUpperCase()} at ${price(entry)}${t.leverage ? ` · ${t.leverage}x` : ""} · ${num(t.notional_usdt) === null ? "notional unavailable" : `$${num(t.notional_usdt)!.toFixed(2)}`} · journal stop ${price(stop)}${unavailable.includes("venue_fills") ? " · venue fills not verified" : ""}` },
    { n: "Managed", at: closed ? "while open" : "now", x: `${closed ? `Held ${dur(heldMs)}` : `Open for ${dur(heldMs)}`}${num(t.mfe_r) !== null ? ` · best ${num(t.mfe_r)!.toFixed(1)}R` : ""}${num(t.mae_r) !== null ? ` · worst ${num(t.mae_r)!.toFixed(1)}R` : ""}${num(t.mfe_r) === null && num(t.mae_r) === null ? " · excursions not recorded" : ""}` },
    { n: "Closed", at: closed ? `${opened(t.closed_at)} UTC` : "—", x: closed ? `${s} at ${price(exit)} · ${money(pnl)} journal-booked${r !== null ? ` · ${r >= 0 ? "+" : "−"}${Math.abs(r).toFixed(1)}R` : ""}` : "Still open" },
    { n: "Lesson", at: closed ? "review" : "pending", x: closed ? `No lesson is linked to this trade.${fwd !== null && fwd !== undefined ? ` Recorded 4h forward return after the decision: ${(fwd * 100).toFixed(2)}%.` : ""}` : "Written when the trade closes" },
  ];
  // levels diagram: only prices the journal recorded, on one scale
  const lv = [stop !== null && { k: "Stop", v: stop, c: BAD }, entry !== null && { k: "Entry", v: entry, c: OK }, closed && exit !== null && { k: "Exit", v: exit, c: pnl !== null && pnl >= 0 ? OK : BAD }].filter(Boolean) as { k: string; v: number; c: string }[];
  const lo = Math.min(...lv.map((x) => x.v)), hi = Math.max(...lv.map((x) => x.v));
  const Y = (v: number) => (hi === lo ? 100 : 180 - ((v - lo) / (hi - lo)) * 160);
  return (
    <DetailPanel open onClose={onClose} title={`${base(t.symbol)}-USDT · ${String(t.side).toUpperCase()} · ${s}`} description={`${t.strategy_name || t.strategy_id || "no strategy recorded"} · journal trade ${t.id}`}>
      <div style={{ display: "flex", gap: 14, alignItems: "baseline", flexWrap: "wrap" }}>
        <span className="m" style={{ fontSize: 26, color: !closed ? OPEN : pnl === null ? GREY : pnl >= 0 ? OK : BAD }}>{closed ? money(pnl) : "open"}</span>
        <span className="m" style={{ color: "var(--txt2)" }}>{closed && r !== null ? `${r >= 0 ? "+" : "−"}${Math.abs(r).toFixed(1)}R` : ""}</span>
      </div>
      {lv.length > 0 && (
        <div>
          <svg viewBox="0 0 800 200" preserveAspectRatio="none" role="img" aria-label={`Recorded price levels: ${lv.map((x) => `${x.k} ${price(x.v)}`).join(", ")}`} style={{ width: "100%", height: 140 }}>
            {lv.map((x) => (<g key={x.k}><path d={`M0 ${Y(x.v)}H800`} stroke={x.c} strokeWidth="1.6" strokeDasharray={x.k === "Stop" ? "8 6" : undefined} vectorEffect="non-scaling-stroke" /></g>))}
          </svg>
          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 12, color: "var(--txt2)" }}>
            {lv.map((x) => (<span key={x.k}><i style={{ display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: x.c, marginRight: 6 }} />{x.k} <span className="m">{price(x.v)}</span></span>))}
          </div>
          <p className="gl-note">Levels recorded in the journal on one scale — not a price chart.</p>
        </div>
      )}
      {lq.isError && <p className="gl-err">Lineage unavailable: {(lq.error as Error).message}</p>}
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
        {steps.map((p, i) => (
          <li key={p.n} className="gl-st" style={{ display: "grid", gridTemplateColumns: "28px 1fr", gap: 10, animationDelay: `${(i * 0.12).toFixed(2)}s` }}>
            <span className="m" style={{ width: 26, height: 26, borderRadius: "50%", display: "grid", placeItems: "center", background: "rgb(var(--a) / .25)", color: "rgb(var(--a2))", fontSize: 12 }}>{i + 1}</span>
            <span style={{ display: "flex", flexDirection: "column" }}>
              <span><b>{p.n}</b> <span className="m" style={{ fontSize: 11, color: GREY }}>{p.at}</span></span>
              <span style={{ fontSize: 13, color: "#b9c5d1" }}>{p.x}</span>
            </span>
          </li>
        ))}
      </ol>
      <KV items={[
        ["Opened", `${utc(t.opened_at)}`],
        ["Closed", closed ? utc(t.closed_at) : "—"],
        ["Size", num(t.amount) === null ? null : `${t.amount} ${base(t.symbol)}`],
        ["Risk at entry", riskUsd(t) === null ? "not recorded" : `$${riskUsd(t)!.toFixed(2)}`],
        ["Execution", String(t.exec_mode ?? "UNAVAILABLE")],
        ["Close reason", closed ? String(t.close_reason ?? "not recorded") : "—"],
      ]} />
      {unavailable.length > 0 && <p className="gl-note">Not recorded or not verified: {unavailable.join(", ").replaceAll("_", " ")}.</p>}
      {decision?.id && <div className="gl-actions"><a className="gl-btn" href={`#operations?decision=${encodeURIComponent(String(decision.id))}`}>Open the decision record</a></div>}
    </DetailPanel>
  );
}
