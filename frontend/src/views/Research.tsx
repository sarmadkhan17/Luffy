/** Research — approved "Final · 3" design bound to the research ledger.
 * Everything here is DISCOVERY evidence: a ranking, never an admission. The
 * referee is OFF and no held-out look is shown or implied. */
import { useMemo, useState } from "react";
import { FlaskConical, Funnel, ListChecks } from "lucide-react";
import { usePreview } from "../context";
import { useRead, rows as asRows, rec } from "../components/records";
import { Card, CardHead, DataTable, DetailPanel, KV, type Col } from "../components/glass";
import { PageHeader } from "../components/PageHeader";
import { timestamp as utc } from "../time";

type Combo = Record<string, any>;
const OK = "#3ddc97", BAD = "#ff7b72", WARN = "#f2b44a", GREY = "#8d9fb2", BLUE = "#7fd3ff";
const STAGES = ["Looked at", "Scored", "Kept", "Ablation", "Candidate"];
const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const fmtP = (p: number | null) => (p === null ? "n/a" : p < 0.001 ? p.toExponential(1) : p.toFixed(3));

/** Plain-language verdict from the ledger's own status/verdict. */
export function verdictOf(c: Combo): string {
  if (c.status === "untestable") return "Untestable";
  if (c.status === "empty") return "No trades";
  return c.verdict === "survivor" ? "Survivor" : c.verdict === "grow" ? "Growing" : "Pruned";
}
const VC: Record<string, [string, string]> = {
  Survivor: ["rgb(var(--a2))", "rgb(var(--a) / .16)"], Growing: [BLUE, "#7fd3ff1f"], Pruned: [BAD, "#ff7b721f"], Untestable: [WARN, "#f2b44a1f"], "No trades": [GREY, "#ffffff12"],
};
/** How far it got: 0 looked at … 4 candidate bank (never, while the bank is empty). */
const stageOf = (c: Combo, banked: boolean) => (banked ? 4 : c.verdict === "survivor" ? 3 : c.verdict === "grow" ? 2 : c.status === "scored" ? 1 : 0);
const direction = (c: Combo) => {
  const l = c.has_long ?? !!c.entry_long, sh = c.has_short ?? !!c.entry_short;
  return l && sh ? "BOTH" : l ? "LONG" : sh ? "SHORT" : "—";
};
const rule = (c: Combo) => {
  let parts: string[] = [];
  try { parts = typeof c.parts === "string" ? JSON.parse(c.parts) : (c.parts ?? []); } catch { /* shown as hash */ }
  return parts.length ? parts.join("  AND  ") : String(c.hash);
};

export default function Research() {
  const { adapter } = usePreview();
  const q = useRead(adapter.mode === "LIVE" ? "research/board" : null, 120000);
  const b = q.data as Record<string, any> | undefined;
  const combos = (b?.combos ?? []) as Combo[];
  const [open, setOpen] = useState<Combo | null>(null);
  const [more, setMore] = useState(false);
  const [shownN, setShownN] = useState<number | null>(null);

  const verdictN = (st: string, v?: string) => asRows(b?.verdicts).filter((r) => r.status === st && (!v || r.verdict === v)).reduce((a, r) => a + (r.n as number), 0);
  const total = num(b?.total);
  const stages = useMemo(() => {
    if (!b) return [];
    const bank = Object.values((b.candidates_by_state ?? {}) as Record<string, number>).reduce((a, n) => a + n, 0);
    return [
      { l: "Looked at", n: total ?? 0, d: "every rule part and combination in the ledger" },
      { l: "Scored", n: verdictN("scored"), d: "had enough trades to measure" },
      { l: "Kept", n: verdictN("scored", "survivor") + verdictN("scored", "grow"), d: "survived pruning or are being grown" },
      { l: "Ablation survivors", n: verdictN("scored", "survivor"), d: "every part pulls its weight" },
      { l: "Candidate bank", n: bank, d: "carried toward the referee — none yet" },
    ];
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [b]);

  const cols: Col<Combo>[] = useMemo(() => [
    { key: "rule", label: "Rule", primary: true, filter: "text", value: (c) => rule(c), cell: (c) => <span className="m" style={{ fontSize: 12, textAlign: "left", display: "block", maxWidth: 300, whiteSpace: "normal", overflowWrap: "anywhere" }}>{rule(c)}</span> },
    { key: "tf", label: "TF", filter: "select", value: (c) => String(c.tf), cell: (c) => <span className="m">{c.tf}</span> },
    { key: "dir", label: "Direction", filter: "select", value: (c) => direction(c), cell: (c) => { const d = direction(c); return <span className="gl-pill" style={{ fontWeight: 400, background: d === "LONG" ? "#3ddc9724" : d === "SHORT" ? "#ff7b7224" : "#ffffff14", color: d === "LONG" ? OK : d === "SHORT" ? BAD : "#c9d4df" }}>{d}</span>; } },
    { key: "stage", label: "Got to", value: (c) => stageOf(c, false), cell: (c) => { const st = stageOf(c, false); return <span style={{ display: "flex", alignItems: "center", gap: 8 }}><span style={{ display: "flex", gap: 3 }} aria-hidden="true">{STAGES.map((_, k) => <i key={k} style={{ width: 14, height: 5, borderRadius: 3, background: k <= st ? "rgb(var(--a))" : "#ffffff1a" }} />)}</span><span style={{ fontSize: 12 }}>{STAGES[st]}</span></span>; } },
    { key: "verdict", label: "Verdict", filter: "select", value: (c) => verdictOf(c), cell: (c) => { const v = verdictOf(c); return <span style={{ fontSize: 12, padding: "3px 9px", borderRadius: 6, color: VC[v][0], background: VC[v][1] }}>{v}</span>; } },
    { key: "reason", label: "Reason", filter: "text", value: (c) => (c.reason ? String(c.reason) : null), cell: (c) => <span style={{ fontSize: 13, color: "#b9c5d1", display: "block", maxWidth: 260, whiteSpace: "normal" }}>{c.reason || "—"}</span> },
    { key: "pf", label: "PF", align: "r", value: (c) => num(c.median_pf), cell: (c) => { const p = num(c.median_pf); return <span style={{ color: p === null ? GREY : p >= 1.3 ? OK : p >= 1 ? "#c9d4df" : BAD }}>{p === null ? "n/a" : p.toFixed(2)}</span>; } },
    { key: "trades", label: "Trades", align: "r", value: (c) => num(c.trades), cell: (c) => <span>{c.trades ?? "n/a"}</span> },
  ], []);

  const last = b?.last_batch as Record<string, any> | null | undefined;
  const loops = b?.held_out_looks as Record<string, any> | null | undefined;
  const lastAt = last?.started ? +new Date(last.started) : 0;
  const active = !!last && !last.finished && Date.now() - lastAt < 30 * 60000; // a batch that started recently and has not finished
  const state = !b ? "Loading…" : active ? "Searching" : last?.error ? "Idle · last batch refused" : "Idle";
  const dots = useMemo(() => {
    let seed = 9; const rnd = () => (seed = (seed * 9301 + 49297) % 233280) / 233280;
    return Array.from({ length: 36 }, (_, i) => { const k = 1 + (i % 5); return { y: 40 + rnd() * 160, to: `${k * 20 - 2}%`, du: `${1.6 * k + 1}s`, dl: `${(rnd() * 6).toFixed(2)}s` }; });
  }, []);
  const H = stages.map((s) => 30 + 190 * (Math.log(s.n + 1) / Math.log((total ?? 1) + 1)));
  const NS = stages.length;
  const X = stages.map((_, i) => ((i + 0.5) * 1000) / NS);
  const top = H.length ? [[0, 130 - H[0] / 2], ...X.map((x, i) => [x, 130 - H[i] / 2]), [1000, 130 - H[NS - 1] / 2]] : [];
  const bot = top.map(([x, y]) => [x, 260 - y]).reverse();
  const funnelPath = top.length ? `M${top.map(([x, y]) => `${x.toFixed(0)} ${y.toFixed(1)}`).join(" L")} L${bot.map(([x, y]) => `${x.toFixed(0)} ${y.toFixed(1)}`).join(" L")} Z` : "";

  return (
    <>
      <PageHeader title="Research">
        <span className={`gl-state ${active ? "live" : "blink"}`} style={{ color: active ? OK : GREY }}><i />{state}</span>
      </PageHeader>
      {q.isError && <div className="gl-err" role="alert">{(q.error as Error).message} Nothing was substituted.</div>}
      {adapter.mode !== "LIVE" && <p className="gl-empty">The research ledger is unavailable in fixture mode.</p>}

      <Card className="pad" label="Discovery funnel">
        <CardHead icon={<Funnel size={16} />} title="Discovery funnel" sub="every rule LUFFY has looked at · discovery evidence only">
          <span style={{ fontSize: 12, color: "var(--txt2)" }}>
            referee {b?.referee_enabled ? "ON" : "OFF"} · handoff {b?.handoff_enabled ? "open" : "closed"} · held-out looks spent{" "}
            <b className="m">{loops?.n ?? "unavailable"}</b>
          </span>
        </CardHead>
        {stages.length > 0 && (
          <div>
            <div className="gl-funnel">
            {dots.map((d, i) => <span key={i} className="gl-fdot" aria-hidden="true" style={{ top: d.y * 200 / 260, ["--to" as string]: d.to, animationDuration: d.du, animationDelay: d.dl }} />)}
            <svg viewBox="0 0 1000 260" preserveAspectRatio="none" style={{ width: "100%", height: 200, display: "block" }} role="img" aria-label={`Funnel: ${stages.map((s) => `${s.l} ${s.n}`).join(", ")}`}>
              <defs><linearGradient id="gl-fg" x1="0" y1="0" x2="1" y2="0"><stop offset="0" style={{ stopColor: "rgb(var(--a))" }} stopOpacity=".5" /><stop offset="1" style={{ stopColor: "rgb(var(--a2))" }} stopOpacity=".12" /></linearGradient></defs>
              <path d={funnelPath} fill="url(#gl-fg)" style={{ stroke: "rgb(var(--a))" }} strokeWidth="1.2" vectorEffect="non-scaling-stroke" />
              {stages.slice(0, -1).map((_, i) => <path key={i} d={`M${((i + 1) * 1000) / NS} 20V240`} stroke="#ffffff1f" strokeDasharray="3 5" vectorEffect="non-scaling-stroke" />)}
            </svg>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: `repeat(${NS}, minmax(0, 1fr))`, gap: 8, marginTop: 8 }}>
              {stages.map((s, i) => (
                <div key={s.l} style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                  <span className="m" style={{ fontSize: 28 - i * 2, color: "rgb(var(--hi))" }}>{s.n.toLocaleString("en-US")}</span>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>{s.l}</span>
                  <span style={{ fontSize: 12, color: "var(--txt2)" }}>{s.d}</span>
                  <span className="m" style={{ fontSize: 11, color: i ? "rgb(var(--a2))" : GREY }}>{i === 0 ? "100%" : stages[i - 1].n ? `${Math.round((s.n / stages[i - 1].n) * 100)}% of previous` : "—"}</span>
                </div>
              ))}
            </div>
          </div>
        )}
        {b && (
          <div className="gl-list" style={{ padding: "10px 12px", borderRadius: 10, background: "#ffffff08", justifyContent: "flex-start", flexWrap: "wrap", gap: "6px 14px" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>{active && <span className="gl-scan" style={{ width: 20, height: 20, borderWidth: 3 }} aria-hidden="true" />}<b>{active ? "Now:" : "Last batch:"}</b></span>
            <span className="m" style={{ fontSize: 12 }}>{last ? `${last.round} · ${last.tf}/${last.geo} · ${utc(last.started)}` : "none recorded"}</span>
            {last?.error && <span style={{ color: WARN, fontSize: 12 }}>refused: {String(last.error)}</span>}
            <span style={{ flex: 1 }} />
            <span className="m" style={{ fontSize: 12, color: "var(--txt2)" }}>{b.batches?.n?.toLocaleString("en-US")} batches · {b.batches?.failed ?? 0} failed · controls {b.controls?.powered ?? "?"}/{b.controls?.total ?? "?"} powered</span>
          </div>
        )}
      </Card>

      <Card className="pad" label="Results">
        <CardHead icon={<ListChecks size={16} />} title="Results" sub={shownN !== null ? `${shownN} of ${total ?? combos.length} rules` : undefined}>
          <span style={{ fontSize: 12, color: GREY }}>Click a rule to open its story</span>
        </CardHead>
        {q.isPending && adapter.mode === "LIVE" && <p className="gl-empty">Loading the research ledger…</p>}
        {b && (
          <>
            <DataTable
              label="Research results"
              rows={combos}
              cols={cols}
              rowId={(c) => String(c.hash)}
              defaultSort={{ key: "pf", dir: -1 }}
              onOpen={setOpen}
              empty="The research ledger holds no combinations."
              onShown={(r) => setShownN(r.length)}
            />
            {b.truncated && <p className="gl-note">Showing the newest {combos.length} of {total} rules.</p>}
            <p className="gl-note">PF and trades are discovery-window figures. They rank rules; they do not admit one.</p>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
              <span style={{ fontSize: 12, color: GREY }}>More:</span>
              {["Questions", "Runs", "Research Bank", "Shadow reports", "Registrations", "Costs"].map((m) => (
                <button key={m} type="button" className="gl-tf" style={{ padding: "0 12px" }} onClick={() => setMore(true)}>{m}</button>
              ))}
            </div>
          </>
        )}
      </Card>

      <DetailPanel open={more} onClose={() => setMore(false)} title="Other research stores" description="Row counts as recorded in the ledger">
        <KV items={[
          ["Questions", b?.stores?.questions ?? null], ["Runs", b?.stores?.runs ?? null], ["Results (registered)", b?.stores?.results ?? null],
          ["Research bank objects", b?.stores?.bank_objects ?? null], ["Registrations", b?.stores?.registrations ?? null], ["Evidence records", b?.stores?.evidence ?? null],
          ["Shadow reports", "no store is recorded"], ["Costs", "recorded per strategy — see Strategies"],
        ].map(([k, v]) => [k as string, typeof v === "number" ? v.toLocaleString("en-US") : (v as string | null)])} />
        <p className="gl-note">Zero means the store exists and is empty; unavailable means the table is absent.</p>
      </DetailPanel>
      <Story combo={open} onClose={() => setOpen(null)} />
    </>
  );
}

function Story({ combo, onClose }: { combo: Combo | null; onClose: () => void }) {
  const q = useRead(combo ? `research/combos/${encodeURIComponent(String(combo.hash))}` : null);
  const d = rec(q.data) as Record<string, any> | null;
  const item = rec(d?.item) as Record<string, any> | null;
  if (!combo) return <DetailPanel open={false} onClose={onClose} title="" children={null} />;
  const c = { ...combo, ...(item ?? {}) };
  const verdict = verdictOf(c);
  const symbols = Object.entries((rec(c.result)?.symbols ?? {}) as Record<string, Record<string, any>>);
  const maxPf = Math.max(1, ...symbols.map(([, v]) => Math.min(num(v.pf) ?? 0, 20)));
  const ablation = Object.entries((c.ablation ?? {}) as Record<string, Record<string, any>>);
  const cand = rec(d?.candidate);
  const regs = asRows(d?.registrations);
  const steps = [
    { t: "Looked at", x: `Round ${c.round}${c.parent ? ` · grown from ${String(c.parent).slice(0, 8)}` : ""} · ${utc(c.created_at)}` },
    { t: "Scored", x: c.status === "scored" ? `${c.trades} trades across ${c.scored_symbols} coins · consistency p ${fmtP(num(c.consistency_p))}` : `Status: ${c.status}` },
    { t: "Pruning", x: `${verdict}${c.reason ? ` — ${c.reason}` : ""}` },
    { t: "Candidate bank", x: cand ? `State ${cand.state}` : "Not carried into the candidate bank." },
    { t: "Referee", x: regs.length ? `${regs.length} registered gate look(s)` : "No registered held-out look. The referee is off." },
  ];
  return (
    <DetailPanel open onClose={onClose} title={`${c.tf} · ${direction(c)} · ${c.geo}`} description={`${verdict} · discovery evidence only · hash ${c.hash}`}>
      <p className="m" style={{ margin: 0, fontSize: 13, color: "rgb(var(--hi))", overflowWrap: "anywhere" }}>{rule(c)}</p>
      {q.isError && <p className="gl-err">Detail unavailable: {(q.error as Error).message}</p>}
      <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Its journey</h3>
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 8 }}>
        {steps.map((s, i) => (
          <li key={s.t} className="gl-st" style={{ display: "grid", gridTemplateColumns: "28px 1fr", gap: 10, animationDelay: `${(i * 0.12).toFixed(2)}s` }}>
            <span className="m" style={{ width: 26, height: 26, borderRadius: "50%", display: "grid", placeItems: "center", background: "rgb(var(--a) / .25)", color: "rgb(var(--a2))", fontSize: 12 }}>{i + 1}</span>
            <span style={{ display: "flex", flexDirection: "column" }}><b>{s.t}</b><span style={{ fontSize: 13, color: "#b9c5d1" }}>{s.x}</span></span>
          </li>
        ))}
      </ol>
      <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Its numbers (discovery window)</h3>
      <KV items={[
        ["Median profit factor", num(c.median_pf)?.toFixed(2) ?? null],
        ["Total return", num(c.total_pct) === null ? null : `${num(c.total_pct)!.toFixed(1)}%`],
        ["Max drawdown", num(c.max_dd_pct) === null ? null : `${num(c.max_dd_pct)!.toFixed(1)}%`],
        ["Trades", c.trades ?? null],
        ["Coins scored", c.scored_symbols ?? null],
        ["Consistency p", fmtP(num(c.consistency_p))],
      ]} />
      {ablation.length > 0 && (
        <>
          <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Ablation — does each part pull its weight?</h3>
          <DataTable label="Ablation" rows={ablation.map(([part, v]) => ({ part, ...v }))} rowId={(r) => r.part} pageSizes={[6]} cols={[
            { key: "part", label: "Part kept alone", value: (r) => String(r.part), cell: (r) => <span className="m" style={{ fontSize: 12 }}>{r.part}</span> },
            { key: "ret", label: "Return alone", align: "r", value: (r) => num(r.total_pct), cell: (r) => (num(r.total_pct) === null ? "n/a" : `${num(r.total_pct)!.toFixed(1)}%`) },
            { key: "drop", label: "Lost by dropping", align: "r", value: (r) => num(r.drop_pct), cell: (r) => (num(r.drop_pct) === null ? "n/a" : `${num(r.drop_pct)!.toFixed(1)}%`) },
          ] as Col<Record<string, any>>[]} />
        </>
      )}
      {symbols.length > 0 && (
        <>
          <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Profit factor per coin (discovery window, not held-out)</h3>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {symbols.map(([s, v]) => {
              const pf = num(v.pf);
              return (
                <div key={s} style={{ display: "grid", gridTemplateColumns: "84px 1fr 56px", gap: 8, alignItems: "center", fontSize: 12 }}>
                  <span className="m">{s.split("/")[0]}</span>
                  <span style={{ height: 6, borderRadius: 3, background: "#ffffff12", overflow: "hidden" }}><i style={{ display: "block", height: "100%", width: `${Math.min(100, ((pf ?? 0) / maxPf) * 100)}%`, background: pf !== null && pf >= 1 ? OK : BAD }} /></span>
                  <span className="m" style={{ textAlign: "right" }}>{pf === null ? "n/a" : pf.toFixed(2)}</span>
                </div>
              );
            })}
          </div>
        </>
      )}
      {asRows(d?.unavailable).length > 0 && <p className="gl-note">Not recorded: {asRows(d?.unavailable).map((u) => String(u.field)).join(", ")}.</p>}
      <p className="gl-note">{(d?.notes as string[] | undefined)?.join(" ") ?? "A search result is a ranking, not admission."}</p>
    </DetailPanel>
  );
}
