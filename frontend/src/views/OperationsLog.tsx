/** Operations — approved "Final · 6" design: everything LUFFY recorded doing,
 * in order, with its evidence. Read-only; owner controls live in the Trades
 * header. Scans are rolled up per UTC hour; nothing is estimated. */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BarChart3, BookOpen, ChevronLeft, ChevronRight, Layers, ListFilter, Radar, Search, ShieldCheck, ArrowLeftRight } from "lucide-react";
import { usePreview } from "../context";
import { useRouteParam } from "../links";
import { useRead, rows as asRows, rec } from "../components/records";
import { Card, CardHead, DataTable, DetailPanel, KV, type Col } from "../components/glass";
import { PageHeader } from "../components/PageHeader";
import { DecisionDetail } from "./Evidence";
import { timestamp as utc } from "../time";

type Ev = Record<string, any>;
const OK = "#3ddc97", BAD = "#ff7b72", WARN = "#f2b44a", GREY = "#8d9fb2", BLUE = "#56ccf2";
const TYPES = ["Scan", "Order", "Safety", "Strategy", "Learning"] as const;
const TYPE_COLOR: Record<string, string> = { Scan: "#3fd5c8", Order: "#56ccf2", Safety: "#f2b44a", Strategy: "#a78bfa", Learning: "#7fd3ff" };
const TYPE_ICON = { Scan: Radar, Order: ArrowLeftRight, Safety: ShieldCheck, Strategy: Layers, Learning: BookOpen } as const;
const TONE: Record<string, string> = { ok: OK, warn: WARN, bad: BAD, grey: GREY };
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const when = (iso: string) => { const d = new Date(iso); return Number.isNaN(+d) ? "UNAVAILABLE" : `${d.getUTCDate()} ${MON[d.getUTCMonth()]} ${d.toISOString().slice(11, 16)}`; };
const dayKey = (iso: string) => iso.slice(0, 10);
const dayLabel = (k: string) => { const d = new Date(`${k}T00:00:00Z`); return `${d.getUTCDate()} ${MON[d.getUTCMonth()]}`; };
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);

export default function OperationsLog() {
  const { adapter } = usePreview();
  const decision = useRouteParam("decision");
  const q = useRead(adapter.mode === "LIVE" ? "operations/log" : null, 60000);
  const ov = useQuery({ queryKey: ["overview", "normal"], queryFn: ({ signal }) => adapter.overview("normal", signal), staleTime: 10000, refetchInterval: 30000, refetchIntervalInBackground: false, enabled: adapter.mode === "LIVE" });
  const events = (asRows(q.data?.events) as Ev[]);
  const [types, setTypes] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [dayIdx, setDayIdx] = useState(0);
  const [open, setOpen] = useState<Ev | null>(null);
  const [shownN, setShownN] = useState<number | null>(null);

  const days = useMemo(() => [...new Set(events.map((e) => dayKey(String(e.at))))].sort().reverse(), [events]);
  const day = days[Math.min(dayIdx, Math.max(0, days.length - 1))];
  const counts = useMemo(() => Object.fromEntries(TYPES.map((t) => [t, events.filter((e) => e.type === t).length])), [events]);
  const visible = useMemo(() => {
    const t = text.trim().toLowerCase();
    return events.filter((e) => (!types.length || types.includes(e.type)) && (!t || `${e.what} ${e.who} ${e.type} ${e.result}`.toLowerCase().includes(t)));
  }, [events, types, text]);

  // 24 hourly stacked bars for the selected UTC day; segment height ~ sqrt(count) so rare types stay visible
  const hours = useMemo(() => Array.from({ length: 24 }, (_, h) => {
    const key = `${day}T${String(h).padStart(2, "0")}`;
    const here = events.filter((e) => String(e.at).slice(0, 13) === key && (!types.length || types.includes(e.type)));
    const segs = TYPES.map((t) => ({ t, n: here.filter((e) => e.type === t).reduce((a, e) => a + (num(e.n) ?? 1), 0) })).filter((s) => s.n > 0);
    return { h, segs, total: segs.reduce((a, s) => a + s.n, 0) };
  }), [events, day, types]);
  const maxH = Math.max(1, ...hours.map((x) => x.segs.reduce((a, s) => a + Math.sqrt(s.n), 0)));

  const control = ov.data?.control ?? "UNAVAILABLE";
  const kernel = String(ov.data?.live?.kernelState ?? "UNKNOWN");
  const sup = (ov.data?.live?.observedEvidence as Record<string, any> | undefined)?.protection?.supervisor;
  const cc = control === "ACTIVE" ? OK : control === "FROZEN" ? BLUE : control === "HALTED" ? WARN : GREY;

  const cols: Col<Ev>[] = useMemo(() => [
    { key: "at", label: "Time", value: (e) => String(e.at), cell: (e) => <span className="m" style={{ fontSize: 13 }}>{when(String(e.at))}</span> },
    { key: "type", label: "Type", primary: true, filter: "select", value: (e) => String(e.type), cell: (e) => { const I = TYPE_ICON[e.type as keyof typeof TYPE_ICON] ?? Radar; return <><span className="gl-ic" style={{ width: 26, height: 26, flexBasis: 26, color: TYPE_COLOR[e.type], borderColor: `${TYPE_COLOR[e.type]}55`, background: `${TYPE_COLOR[e.type]}18` }}><I size={14} aria-hidden="true" /></span><span style={{ fontSize: 13 }}>{e.type}</span></>; } },
    { key: "what", label: "What happened", filter: "text", value: (e) => String(e.what), cell: (e) => <span style={{ fontSize: 13, color: "#c9d4df", display: "block", maxWidth: 440, whiteSpace: "normal", overflowWrap: "anywhere" }}>{e.what}</span> },
    { key: "who", label: "Who", filter: "select", value: (e) => String(e.who), cell: (e) => <span style={{ fontSize: 13, color: "var(--txt2)" }}>{e.who}</span> },
    { key: "result", label: "Result", filter: "select", value: (e) => String(e.result), cell: (e) => <span style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: TONE[e.tone] ?? GREY }}><i style={{ width: 7, height: 7, borderRadius: "50%", background: TONE[e.tone] ?? GREY }} />{e.result}</span> },
  ], []);

  return (
    <>
      <PageHeader title="Operations">
        <span style={{ fontSize: 13, color: "var(--txt2)" }}>everything LUFFY recorded doing, in order, with its evidence</span>
        <span className="gl-state blink" style={{ color: cc }}><i />{control} · kernel {kernel.toLowerCase()}{sup?.outcome ? ` · supervisor ${String(sup.outcome).toLowerCase()}` : ""}</span>
      </PageHeader>
      {q.isError && <div className="gl-err" role="alert">{(q.error as Error).message} Nothing was substituted.</div>}
      {decision && <div className="gl-legacy"><DecisionDetail key={decision} id={decision} /></div>}

      <Card className="pad" label="Activity chart">
        <CardHead icon={<BarChart3 size={16} />} title={`Activity · ${day ? dayLabel(day) : "—"} (UTC)`}>
          <button type="button" className="gl-tf" disabled={dayIdx >= days.length - 1} onClick={() => setDayIdx(dayIdx + 1)} aria-label="Previous day"><ChevronLeft size={14} aria-hidden="true" /></button>
          <button type="button" className="gl-tf" disabled={dayIdx === 0} onClick={() => setDayIdx(dayIdx - 1)} aria-label="Next day"><ChevronRight size={14} aria-hidden="true" /></button>
          {TYPES.map((t) => (
            <button key={t} type="button" className="gl-tf" aria-pressed={types.includes(t)} style={{ padding: "0 12px", display: "inline-flex", alignItems: "center", gap: 6 }}
              onClick={() => setTypes(types.includes(t) ? types.filter((x) => x !== t) : [...types, t])}>
              <i style={{ width: 8, height: 8, borderRadius: 2, background: TYPE_COLOR[t] }} />{t} <span className="m">{counts[t]}</span>
            </button>
          ))}
        </CardHead>
        {q.isPending && adapter.mode === "LIVE" ? <p className="gl-empty">Loading the activity record…</p> : (
          <>
            <div className="gl-hours" role="img" aria-label={`Events per hour on ${day ? dayLabel(day) : "the selected day"}`}>
              {hours.map((x) => (
                <div key={x.h} className="gl-hour" title={x.total ? `${String(x.h).padStart(2, "0")}:00 UTC — ${x.segs.map((s) => `${s.t} ${s.n}`).join(", ")}` : `${String(x.h).padStart(2, "0")}:00 UTC — nothing recorded`}>
                  {x.segs.map((s) => (<span key={s.t} className="seg" style={{ height: `${(Math.sqrt(s.n) / maxH) * 100}%`, background: TYPE_COLOR[s.t] }} />))}
                </div>
              ))}
            </div>
            <div className="gl-hourlabels m">{hours.map((x) => <span key={x.h}>{x.h % 3 === 0 ? String(x.h).padStart(2, "0") : ""}</span>)}</div>
            <p className="gl-note">Bar height is the square root of the count so rare events stay visible. Scans are counted per pass; click a type to filter the chart and the log.</p>
          </>
        )}
      </Card>

      <Card className="pad" label="Activity log">
        <CardHead icon={<ListFilter size={16} />} title="Activity log" sub={shownN !== null && q.data ? `${shownN} of ${events.length} events · last ${q.data.window_days} days · ${q.data.signal_cooldowns_not_listed} signal-cooldown records counted, not listed` : undefined}>
          <label className="gl-search"><Search size={15} aria-hidden="true" /><input aria-label="Search the log" placeholder="Search coin, strategy, word…" value={text} onChange={(e) => setText(e.target.value)} /></label>
          <span style={{ fontSize: 12, color: GREY }}>Click an event for its evidence</span>
        </CardHead>
        {q.data && (
          <>
            <DataTable label="Activity log" rows={visible} cols={cols} rowId={(e) => String(e.id)} onOpen={setOpen} defaultSort={{ key: "at", dir: -1 }}
onShown={(r) => setShownN(r.length)} empty="Nothing recorded in this window." />
            <p className="gl-note">{String(q.data.source)}</p>
          </>
        )}
      </Card>
      <EventPanel e={open} onClose={() => setOpen(null)} />
    </>
  );
}

function EventPanel({ e, onClose }: { e: Ev | null; onClose: () => void }) {
  if (!e) return <DetailPanel open={false} onClose={onClose} title="" children={null} />;
  const facts = (rec(e.facts) ?? {}) as Record<string, unknown>;
  const detail = rec(e.detail) as Record<string, any> | null;
  const coins = (e.coins ?? []) as [string, number | null, number | null, string, boolean, string | null][];
  const reasons = Array.isArray(detail?.reasons) ? (detail!.reasons as string[]) : [];
  const checks = rec(detail?.checks) as Record<string, unknown> | null;
  const sc = (z: number, th: number) => 50 + Math.max(-1, Math.min(1, z / (th * 1.25))) * 50;
  const flat = Object.entries(facts).filter(([, v]) => v !== null && v !== undefined && typeof v !== "object").map(([k, v]) => [k.replaceAll("_", " "), String(v)] as [string, string]);
  const extra = detail ? Object.entries(detail).filter(([k, v]) => !["reasons", "checks"].includes(k) && v !== null && typeof v !== "object").slice(0, 10).map(([k, v]) => [k.replaceAll("_", " "), String(v)] as [string, string]) : [];
  return (
    <DetailPanel open onClose={onClose} title={String(e.what)} description={`${e.type} · ${utc(String(e.at))} · ${e.who}`}>
      <p style={{ margin: 0, display: "flex", alignItems: "center", gap: 8, fontSize: 14, color: TONE[e.tone] ?? GREY }}><i style={{ width: 8, height: 8, borderRadius: "50%", background: TONE[e.tone] ?? GREY }} />{e.result}</p>
      {reasons.length > 0 && (
        <>
          <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Reasons recorded</h3>
          {reasons.map((r) => <span key={r} style={{ fontSize: 13, color: "#b9c5d1" }}><span style={{ color: WARN }}>● </span>{r}</span>)}
        </>
      )}
      {checks && (
        <>
          <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Safety checks</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))", gap: 6 }}>
            {Object.entries(checks).map(([k, v]) => <span key={k} style={{ fontSize: 13, color: v === true ? OK : v === false ? BAD : GREY }}>{v === true ? "✓" : v === false ? "✕" : "·"} {k.replaceAll("_", " ")}</span>)}
          </div>
        </>
      )}
      {coins.length > 0 && (
        <>
          <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Every coin's score vs the bar it had to clear (latest pass, {e.last_scan_at ? utc(String(e.last_scan_at)) : ""})</h3>
          <div className="gl-allc" style={{ border: 0, padding: 0, background: "none" }}>
            <div className="grid">
              {coins.map(([sym, score, th, action, executed, skip]) => {
                const t = th ?? 0.3, v = score ?? 0, p = sc(v, t), crossed = Math.abs(v) >= t;
                const col = crossed ? OK : v >= 0 ? "#7fd3ff" : "#ff9d95";
                return (
                  <div key={sym} title={`${action}${skip ? ` · ${skip}` : ""}${executed ? " · traded" : ""}`} style={{ display: "grid", gridTemplateColumns: "46px minmax(0,1fr) 58px", gap: 8, alignItems: "center", fontSize: 12 }}>
                    <span className="m" style={{ color: "#c9d4df" }}>{sym.split("/")[0]}</span>
                    <span className="gl-track"><u style={{ left: `${sc(-t, t)}%` }} /><u style={{ left: `${sc(t, t)}%` }} /><u className="mid" /><b style={{ left: `${Math.min(p, 50)}%`, width: `${Math.max(1, Math.abs(p - 50))}%`, background: col }} /></span>
                    <span className="m" style={{ textAlign: "right", color: col }}>{score === null ? "—" : `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(3)}`}</span>
                  </div>
                );
              })}
            </div>
          </div>
          <p className="gl-note">Amber lines = the threshold a score had to cross to trade.</p>
        </>
      )}
      <KV items={[...flat, ...extra]} />
      {String(e.id).startsWith("open:") || String(e.id).startsWith("close:") ? <div className="gl-actions"><a className="gl-btn primary" href="#trades">Open in Trades</a></div> : null}
      {e.type === "Strategy" && facts.subject ? <div className="gl-actions"><a className="gl-btn" href="#strategies">Open Strategies</a></div> : null}
      <p className="gl-note">Journal-recorded; read-only.</p>
    </DetailPanel>
  );
}
