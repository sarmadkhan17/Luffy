/** Strategies — approved "Final · 4" design bound to the registry and the
 * journal's trades per strategy. Registry state is not admission. */
import { useMemo, useState } from "react";
import { Layers } from "lucide-react";
import { usePreview } from "../context";
import { useRead, rows as asRows } from "../components/records";
import { Card, CardHead, DataTable, DetailPanel, KV, type Col } from "../components/glass";
import { PageHeader } from "../components/PageHeader";
import { timestamp as utc } from "../time";
import { useQuery } from "@tanstack/react-query";

type S = Record<string, any>;
const OK = "#3ddc97", BAD = "#ff7b72", WARN = "#f2b44a", GREY = "#8d9fb2", BLUE = "#56ccf2";
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const money = (n: number | null, d?: number) => { if (n === null) return "—"; const k = d ?? (Math.abs(n) < 10 ? 2 : 0); return `${n >= 0 ? "+" : "−"}$${Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: k, maximumFractionDigits: k })}`; };
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const day = (iso?: string | null) => { const d = iso ? new Date(iso) : null; return d && !Number.isNaN(+d) ? `${d.getUTCDate()} ${MON[d.getUTCMonth()]}` : "—"; };

const STATE: Record<string, [string, string, string]> = {
  live: ["Live", OK, "#3ddc9722"], paper: ["Paper", "rgb(var(--a2))", "rgb(var(--a) / .16)"],
  demoted: ["Demoted", WARN, "#f2b44a1f"], retired: ["Retired", GREY, "#ffffff12"],
};
const stateLabel = (s: S) => (STATE[s.state] ?? [String(s.state), GREY, "#ffffff12"])[0];
/** Derived from journal counts only — a label, not an evaluation. */
export function healthOf(s: S): string {
  if (s.open_trades > 0) return `${s.open_trades} position${s.open_trades === 1 ? "" : "s"} open`;
  if (s.state === "retired") return "Retired";
  if (s.state === "demoted") return "Benched";
  if (s.closed_trades < 5) return "Collecting evidence";
  return (s.pnl_30d ?? 0) < 0 ? "Weakening" : "Healthy";
}
const HC: Record<string, string> = { Healthy: OK, Weakening: WARN, Benched: WARN, Retired: GREY, "Collecting evidence": "#a9b8c8" };
const healthColor = (h: string) => (h.endsWith("open") ? BLUE : (HC[h] ?? GREY));
const pfOf = (s: S) => (num(s.profit_factor) !== null ? num(s.profit_factor)!.toFixed(2) : s.wins > 0 ? "∞" : "—");

function Spark({ curve, w = 300, h = 70, stroke = 3 }: { curve: number[]; w?: number; h?: number; stroke?: number }) {
  if (curve.length < 2) return <span style={{ fontSize: 11, color: GREY }}>{curve.length ? "1 trade" : "no trades"}</span>;
  const pts = [0, ...curve];
  const lo = Math.min(0, ...pts), hi = Math.max(0.01, ...pts);
  const y = (p: number) => h - 4 - ((p - lo) / (hi - lo)) * (h - 8);
  const d = pts.map((p, k) => `${k ? "L" : "M"}${((k * w) / (pts.length - 1)).toFixed(1)} ${y(p).toFixed(1)}`).join(" ");
  const up = pts[pts.length - 1] >= 0;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: h > 50 ? 100 : 28 }} aria-hidden="true">
      <path d={`M0 ${y(0)}H${w}`} stroke="#ffffff1a" strokeDasharray="4 5" vectorEffect="non-scaling-stroke" />
      {h > 50 && <path d={`${d} L${w} ${y(0)} L0 ${y(0)} Z`} fill={up ? "#3ddc9718" : "#ff7b7218"} />}
      <path className="gl-curve" d={d} fill="none" stroke={up ? OK : BAD} strokeWidth={stroke} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export default function Strategies() {
  const { adapter } = usePreview();
  const q = useRead(adapter.mode === "LIVE" ? "strategies/board" : null, 60000);
  const ov = useQuery({ queryKey: ["overview", "normal"], queryFn: ({ signal }) => adapter.overview("normal", signal), staleTime: 10000, refetchInterval: 30000, refetchIntervalInBackground: false, enabled: adapter.mode === "LIVE" });
  const control = ov.data?.control ?? "UNAVAILABLE";
  const list = asRows(q.data?.strategies) as S[];
  const [open, setOpen] = useState<S | null>(null);
  const [shownN, setShownN] = useState<number | null>(null);
  const trading = list.filter((s) => s.state === "live").length;

  const cols: Col<S>[] = useMemo(() => [
    { key: "name", label: "Strategy", primary: true, filter: "text", value: (s) => String(s.name), cell: (s) => <span style={{ display: "flex", flexDirection: "column", textAlign: "left" }}><span style={{ fontWeight: 600, fontSize: 14 }}>{s.name}</span><span style={{ fontSize: 11, color: GREY, whiteSpace: "normal", overflowWrap: "anywhere" }}>{s.kind}{s.timeframe ? ` · ${s.timeframe}` : ""} · {s.origin ?? "origin unrecorded"}</span></span> },
    { key: "desc", label: "What it does", filter: "text", value: (s) => String(s.description), cell: (s) => <span style={{ fontSize: 13, color: "#b9c5d1", display: "block", maxWidth: 300, whiteSpace: "normal" }}>{s.description}</span> },
    { key: "state", label: "State", filter: "select", value: (s) => stateLabel(s), sortValue: (s) => ({ live: 4, paper: 3, demoted: 2, retired: 1 })[String(s.state)] ?? 0, cell: (s) => { const [l, c, bg] = STATE[s.state] ?? [s.state, GREY, "#ffffff12"]; return <span style={{ fontSize: 11, fontWeight: 700, padding: "3px 9px", borderRadius: 999, color: c, background: bg }}>{l}</span>; } },
    { key: "health", label: "Health", filter: "select", value: (s) => healthOf(s), cell: (s) => { const h = healthOf(s); return <span style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: healthColor(h), whiteSpace: "normal" }}><i style={{ width: 7, height: 7, flex: "0 0 7px", borderRadius: "50%", background: healthColor(h) }} />{h}</span>; } },
    { key: "pnl", label: "P&L 30d", align: "r", value: (s) => num(s.pnl_30d), cell: (s) => <span style={{ color: s.pnl_30d === null ? GREY : s.pnl_30d >= 0 ? OK : BAD }}>{s.closed_trades ? money(num(s.pnl_30d)) : "—"}</span> },
    { key: "win", label: "Win", align: "r", value: (s) => (s.closed_trades ? s.wins / s.closed_trades : null), cell: (s) => (s.closed_trades ? `${Math.round((s.wins / s.closed_trades) * 100)}%` : "—") },
    { key: "trades", label: "Trades", align: "r", value: (s) => s.closed_trades, cell: (s) => (s.closed_trades ? s.closed_trades : "—") },
    { key: "pf", label: "PF", align: "r", value: (s) => num(s.profit_factor), cell: (s) => <span style={{ color: num(s.profit_factor) === null ? "#c9d4df" : s.profit_factor >= 1.2 ? OK : s.profit_factor >= 1 ? "#c9d4df" : BAD }}>{pfOf(s)}</span> },
    { key: "trend", label: "Trend", value: () => null, cell: (s) => <span style={{ display: "block", width: 110 }}><Spark curve={s.curve ?? []} h={36} stroke={2} /></span> },
  ], []);

  return (
    <>
      <PageHeader title="Strategies">
        <span className={`gl-state ${trading ? "live" : "blink"}`} style={{ color: trading ? OK : BLUE }}><i />{trading} trading live · LUFFY is {control}</span>
      </PageHeader>
      {q.isError && <div className="gl-err" role="alert">{(q.error as Error).message} Nothing was substituted.</div>}
      <Card className="pad" label="Strategy registry">
        <CardHead icon={<Layers size={16} />} title="Strategy registry" sub={shownN !== null ? `${shownN} strategies · journal-booked trades per strategy id · P&L over the last 30 days` : undefined}><span style={{ fontSize: 12, color: GREY }}>Click a strategy for its details</span></CardHead>
        {q.isPending && adapter.mode === "LIVE" && <p className="gl-empty">Loading the registry…</p>}
        {adapter.mode !== "LIVE" && <p className="gl-empty">The strategy registry is unavailable in fixture mode.</p>}
        {q.data && (
          <>
            <DataTable label="Strategy registry" rows={list} cols={cols} rowId={(s) => String(s.id)} onOpen={setOpen} defaultSort={{ key: "state", dir: -1 }}
              onShown={(r) => setShownN(r.length)} empty="The registry holds no strategies." />
            <p className="gl-note">Registry state is not admission. Health is derived from journal counts: open positions, then state, then evidence (&lt; 5 closed trades) and 30-day P&amp;L.</p>
          </>
        )}
      </Card>
      <Story s={open} onClose={() => setOpen(null)} />
    </>
  );
}

function Story({ s, onClose }: { s: S | null; onClose: () => void }) {
  if (!s) return <DetailPanel open={false} onClose={onClose} title="" children={null} />;
  const h = healthOf(s);
  const [label, color, bg] = STATE[s.state] ?? [s.state, GREY, "#ffffff12"];
  const prov = s.provenance as Record<string, string> | null;
  const life = [
    { t: "Created", at: day(s.created_at), x: `${s.origin ?? "origin unrecorded"}${s.generation ? ` · generation ${s.generation}` : ""}${s.parent_id ? ` · from ${String(s.parent_id).slice(0, 14)}` : ""}`, tone: "done" },
    { t: `Now ${label}`, at: day(s.state_changed_at), x: s.retire_reason ? String(s.retire_reason) : `State last changed ${s.state_changed_at ? utc(s.state_changed_at) : "at an unrecorded time"}`, tone: s.state === "retired" || s.state === "demoted" ? "bad" : "now" },
    { t: "First trade", at: day(s.first_trade_at), x: s.first_trade_at ? utc(s.first_trade_at) : "no trade recorded", tone: s.first_trade_at ? "done" : "none" },
    { t: "Latest activity", at: day(s.last_trade_at), x: s.open_trades ? `${s.open_trades} position(s) still open` : s.last_trade_at ? utc(s.last_trade_at) : "none", tone: s.last_trade_at ? "done" : "none" },
  ];
  return (
    <DetailPanel open onClose={onClose} title={s.name} description={`${s.kind} · ${s.origin ?? "origin unrecorded"} · ${s.id}`}>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <span style={{ fontSize: 12, padding: "3px 9px", borderRadius: 6, color, background: bg }}>{label}</span>
        <span style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: healthColor(h) }}><i style={{ width: 7, height: 7, borderRadius: "50%", background: healthColor(h) }} />{h}</span>
      </div>
      <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>In plain words</h3>
      <p style={{ margin: 0, fontSize: 14, lineHeight: 1.5, color: "#c9d4df" }}>{s.description}</p>
      <KV items={[["Direction", s.direction ?? null], ["Timeframe", s.timeframe ?? null], ["Spec hash", s.spec_sha256 ? String(s.spec_sha256).slice(0, 12) : null],
        ["Idea source", prov ? `${prov.source_kind ?? "?"}${prov.idea_id ? ` · ${prov.idea_id}` : ""}` : null]]} />
      <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Its life so far</h3>
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
        {life.map((p, i) => (
          <li key={p.t} className="gl-st" style={{ display: "grid", gridTemplateColumns: "28px 1fr", gap: 10, animationDelay: `${(i * 0.15).toFixed(2)}s` }}>
            <span className="m" style={{ width: 26, height: 26, borderRadius: "50%", display: "grid", placeItems: "center", fontSize: 12, border: `1px solid ${p.tone === "bad" ? BAD : p.tone === "none" ? "#ffffff24" : "rgb(var(--a))"}`, background: p.tone === "bad" ? "#ff7b7222" : p.tone === "none" ? "transparent" : "rgb(var(--a) / .22)", color: p.tone === "bad" ? BAD : p.tone === "none" ? GREY : "rgb(var(--a2))" }}>{p.tone === "bad" ? "!" : p.tone === "now" ? "●" : p.tone === "none" ? "·" : "✓"}</span>
            <span style={{ display: "flex", flexDirection: "column" }}><span><b>{p.t}</b> <span className="m" style={{ fontSize: 11, color: GREY }}>{p.at}</span></span><span style={{ fontSize: 13, color: "#b9c5d1" }}>{p.x}</span></span>
          </li>
        ))}
      </ol>
      <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Cumulative P&amp;L</h3>
      <Spark curve={s.curve ?? []} />
      <KV items={[
        ["Realized P&L", s.closed_trades ? money(num(s.pnl_total), 2) : null],
        ["Last 30 days", s.closed_trades ? `${money(num(s.pnl_30d), 2)} · ${s.trades_30d} trades` : null],
        ["Win rate", s.closed_trades ? `${Math.round((s.wins / s.closed_trades) * 100)}%` : null],
        ["Closed trades", s.closed_trades],
        ["Profit factor", pfOf(s)],
        ["Open positions", s.open_trades],
      ]} />
      {s.unknown_pnl_trades > 0 && <p className="gl-note">{s.unknown_pnl_trades} closed trade(s) have unknown P&amp;L and are not counted.</p>}
      <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Recent trades</h3>
      {asRows(s.recent).length === 0 ? <p className="gl-note">No closed trades with a known P&amp;L.</p> : asRows(s.recent).map((r) => (
        <div key={String(r.trade_id)} className="gl-list" style={{ padding: "6px 10px", borderRadius: 8, background: "#ffffff08" }}>
          <span><span className="m">{day(String(r.at))}</span><span className="m" style={{ color: String(r.side).toLowerCase() === "long" ? OK : BAD }}>{String(r.side).toUpperCase()}</span>{String(r.symbol).split("/")[0]}-USDT</span>
          <span className="m" style={{ color: (r.pnl as number) >= 0 ? OK : BAD }}>{money(r.pnl as number, 2)}</span>
        </div>
      ))}
      <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Lessons about it</h3>
      <p className="gl-note">No lessons are linked to this strategy in the records. <a className="gl-link" href="#knowledge">Open Knowledge</a></p>
      <p className="gl-note">Journal-booked, not venue-verified. Registry state is not admission.</p>
    </DetailPanel>
  );
}
