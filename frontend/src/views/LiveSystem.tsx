/** Live System — approved "Final · 7" design (pipeline layout, glow icons).
 * The parts and their connections are the DECLARED architecture; what is
 * component telemetry comes from /system. Aggregate counts do not prove
 * an architecture edge or current authority, so nothing animates from them:
 * only the explanatory walkthrough animates, and it says so. */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BookOpen, CheckCheck, ChartCandlestick, Globe, Layers, MessageSquare,
  Network, Radar, Scale, ShieldCheck, ScanEye, Sparkles, Vault, Zap, FlaskConical, Layers2, type LucideIcon,
} from "lucide-react";
import { usePreview } from "../context";
import { useRead, rows as asRows, rec } from "../components/records";
import { Card, DetailPanel, KV } from "../components/glass";
import { useMotionPreference } from "../components/ui";
import { PageHeader } from "../components/PageHeader";

type Group = "Market" | "Perception" | "Analyst" | "Decision" | "Safety" | "Execution" | "Memory" | "Partner";
const GC: Record<Group, string> = { Market: "#4aa8ff", Perception: "#3fd5c8", Analyst: "rgb(86 204 242)", Decision: "rgb(86 204 242)", Safety: "#ff7b72", Execution: "#f2b44a", Memory: "#a78bfa", Partner: "#f472b6" };
const KC: Record<string, string> = { data: "#4aa8ff", sense: "#3fd5c8", vote: "rgb(86 204 242)", dec: "rgb(86 204 242)", order: "#f2b44a", mem: "#a78bfa", safe: "#ff7b72", guard: "#56ccf2" };
const OK = "#3ddc97", WARN = "#f2b44a", BAD = "#ff7b72", GREY = "#8d9fb2";

// id: [label, group, x, y, size, icon, code, description, telemetry component]
type Def = [string, Group, number, number, number, LucideIcon, string, string, string | null];
const N: Record<string, Def> = {
  market: ["Acquisition", "Market", 85, 160, 64, ChartCandlestick, "Market / Data", "Market acquisition retains point-in-time source and availability provenance. Missing inputs remain unavailable.", null],
  registry: ["Registry / universe", "Market", 85, 420, 62, Layers, "Registry", "Registry and universe eligibility bind instruments and point-in-time market provenance. Membership is not trading permission.", null],
  att: ["Attention", "Perception", 265, 150, 58, Radar, "Attention", "Cheap broad observation feeds a deterministic deep-selection budget. Held, protected and execution-required work bypasses discretionary selection. Investigation candidates are not admitted strategies.", null],
  wm: ["World Model", "Perception", 265, 400, 58, Globe, "World Model", "Typed world state retains point-in-time relationships and context; a written relation is not verified causality.", null],
  research: ["Research", "Analyst", 470, 130, 58, FlaskConical, "Research", "Typed research questions use governed sources, search, filter, deduplication and extraction. Research Bank retains competing hypotheses and a governed experiment bridge. Discovery evidence is not admission.", null],
  quant: ["Quant / Referee", "Analyst", 470, 370, 62, Scale, "Quant", "Reproducible evaluation requires nulls and controls, dependence-aware evidence, purged holdout and persistent error budget. Insufficient evidence remains UNTESTED; Referee admission is an explicit evidence boundary.", null],
  strat: ["StrategyVersion", "Decision", 680, 145, 62, Layers, "Strategy Factory", "Immutable StrategySpec and StrategyVersion bind spec and lineage hashes, compiler parity, validation, exact install, probation and exact owner approval. A registry active/live label grants no first-live or order permission.", null],
  governor: ["Strategy Governor", "Decision", 680, 390, 62, ShieldCheck, "Strategy Governor", "Governor records versioned lifecycle transitions with reasons and evidence. ACTIVE lifecycle alone does not authorize an order. Owner approval has exact-version scope and freshness checks.", null],
  orch: ["Opportunity Context", "Decision", 865, 220, 80, Network, "Decision", "Coherent Opportunity Context binds required and optional analyst evidence, supporting and opposing evidence, exact cuts and economics. Deterministic candidate comparison includes CASH. Analyst majority and an opaque score are not trading authority.", null],
  portfolio: ["Portfolio", "Decision", 865, 480, 62, Layers2, "Portfolio", "Whole-book comparison applies economics, capacity and concentration constraints where authority exists. Unavailable economics never becomes zero.", null],
  risk: ["Risk", "Safety", 1060, 300, 64, ShieldCheck, "Risk", "Risk has final entry authority. Permission fails closed on missing or invalid prerequisites. Control state and strategy lifecycle alone do not establish entry permission.", "risk"],
  sup: ["Safety / control", "Safety", 1060, 115, 56, ScanEye, "Supervisor", "Supervisor and owner control boundaries constrain work. Fresh heartbeat, protection and reconciliation evidence retain separate stale, missing and failing states.", "supervisor"],
  exe: ["Execution", "Execution", 1240, 220, 62, Zap, "Execution", "Idempotent entry and recovery preserve exact execution evidence and partial-exit identity. Execution follows final Risk permission.", null],
  accounting: ["Accounting", "Execution", 1240, 470, 62, BookOpen, "Accounting", "Accounting distinguishes authoritative, unknown and estimated economics. Journal-booked performance and current venue economics are separate evidence.", null],
  out: ["Outcome capture", "Memory", 1280, 750, 54, CheckCheck, "Memory / Learning", "Outcome capture retains trade and non-trade results with exact evidence identities and limitations.", null],
  memory: ["Assessed memory", "Memory", 960, 750, 56, Vault, "Memory", "Assessed memory retains evidence-linked observations; written relations are not verified causality. Unresolved links stay explicit.", null],
  learning: ["Governed learning", "Memory", 640, 750, 56, Sparkles, "Learning / replay", "Governed learning and replay assess outcomes. Discovery or conversational claims cannot mutate Strategy, Risk or Execution authority.", null],
  chat: ["Owner / LUFFY", "Partner", 300, 750, 60, MessageSquare, "Owner", "Owner interaction uses exact evidence links and explicit approval boundaries. Typed/local evidence queries remain available with providers disabled; external chat availability is separate. Conversation has no trading authority.", null],
};
type Edge = [string, string, keyof typeof KC, string];
const E: Edge[] = [
  ["market", "registry", "data", "point-in-time provenance and eligible universe"],
  ["registry", "att", "data", "cheap broad observation"], ["att", "wm", "sense", "deep-selection budget; required-work bypass"],
  ["wm", "research", "sense", "typed context and investigation candidates"], ["research", "quant", "sense", "governed experiment bridge"],
  ["quant", "strat", "dec", "Referee evidence; UNTESTED is not admitted"], ["strat", "governor", "dec", "validation / install / probation / exact owner approval"],
  ["wm", "orch", "sense", "coherent point-in-time context"], ["governor", "orch", "dec", "versioned lifecycle; no standalone order permission"],
  ["orch", "portfolio", "dec", "exact cuts; economics; candidate comparison against CASH"], ["portfolio", "risk", "safe", "whole-book constraints where authoritative"],
  ["sup", "risk", "safe", "control and safety prerequisites"], ["risk", "exe", "order", "final fail-closed entry permission"],
  ["exe", "accounting", "order", "idempotent execution / recovery / partial-exit evidence"], ["accounting", "out", "mem", "authoritative / unknown / estimated outcome economics"],
  ["out", "memory", "mem", "evidence-linked assessed outcomes"], ["memory", "learning", "mem", "governed learning and replay"],
  ["learning", "research", "mem", "governed investigation questions"], ["memory", "chat", "mem", "exact evidence queries"],
  ["chat", "governor", "guard", "explicit exact-version approval boundary; no conversational authority"],
];
const STEPS: [string, string, number[]][] = [
  ["Market / Data", N.market[7] + " " + N.registry[7], [0,1]],
  ["Attention / World Model", N.att[7] + " " + N.wm[7], [2,3]],
  ["Research / Quant", N.research[7] + " " + N.quant[7], [4,5]],
  ["Strategy / Governor", N.strat[7] + " " + N.governor[7], [6,8]],
  ["Decision / Portfolio", N.orch[7] + " " + N.portfolio[7], [7,9,10]],
  ["Risk / Execution", N.risk[7] + " " + N.exe[7], [11,12,13]],
  ["Accounting / Memory / Learning", N.accounting[7] + " " + N.learning[7], [14,15,16,17]],
  ["Owner / LUFFY", N.chat[7], [18,19]],
];
const ZONES: [string, string, number, number, number, number][] = [
  ["MARKET / DATA", "#4aa8ff", 20, 40, 130, 610], ["ATTENTION / WORLD", "#3fd5c8", 190, 40, 150, 610],
  ["RESEARCH / QUANT", "#e2c88a", 390, 40, 160, 610], ["STRATEGY / DECISION", "#e2c88a", 610, 40, 340, 610],
  ["RISK", "#ff7b72", 995, 40, 130, 610], ["EXECUTION", "#f2b44a", 1170, 40, 210, 610],
];
const geo = (a: string, b: string) => { const [x1, y1, x2, y2] = [N[a][2], N[a][3], N[b][2], N[b][3]]; const dx = (x2 - x1) * 0.5; return `M${x1} ${y1} C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`; };
const LEGEND: [string, string][] = [["Market data", "#4aa8ff"], ["Perception", "#3fd5c8"], ["Evidence & decisions", "rgb(86 204 242)"], ["Orders", "#f2b44a"], ["Memory & learning", "#a78bfa"], ["Safety", "#ff7b72"]];

type Tele = { freshness?: string; health?: string | null; status_label?: string | null; observed_at?: string | null; summary?: string; source?: string };

export default function LiveSystem() {
  const { adapter } = usePreview();
  const live = adapter.mode === "LIVE";
  const sysQ = useRead(live ? "system" : null, 60000);
  const ov = useQuery({ queryKey: ["overview", "normal"], queryFn: ({ signal }) => adapter.overview("normal", signal), staleTime: 10000, refetchInterval: 30000, refetchIntervalInBackground: false, enabled: live });
  const [mode, setMode] = useState<"all" | "cycle">("all");
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const [pop, setPop] = useState<string | null>(null);
  const reduced = useMotionPreference();
  const [subsystem, setSubsystem] = useState<Group | null>(null);
  useEffect(() => { if (reduced) setPlaying(false); }, [reduced]);

  useEffect(() => {
    if (!(playing && mode === "cycle" && pop === null && !reduced)) return;
    const t = setInterval(() => setStep((s) => (s + 1) % STEPS.length), 4200);
    return () => clearInterval(t);
  }, [playing, mode, pop, reduced]);

  const tele = useMemo(() => {
    const m: Record<string, Tele> = {};
    for (const n of asRows(sysQ.data?.nodes)) m[String(n.id)] = (rec(n.telemetry) ?? {}) as Tele;
    return m;
  }, [sysQ.data]);
  const compOf = (id: string) => N[id][8];
  // Aggregate component counts do not establish a specific architecture
  // edge or exact version/cut. Keep every edge DECLARED until exact causal
  // evidence can be bound to that edge.
  const obsEdges = new Set<number>();

  const cycle = mode === "cycle";
  const s = step % STEPS.length;
  const activeE = new Set<number>(cycle ? STEPS[s][2] : []);
  if (hover) { activeE.clear(); E.forEach((e, i) => { if (e[0] === hover || e[1] === hover) activeE.add(i); }); }
  if (subsystem && !hover && !cycle) E.forEach((e, i) => { if (N[e[0]][1] === subsystem || N[e[1]][1] === subsystem) activeE.add(i); });
  const activeN = new Set<string>();
  activeE.forEach((i) => { activeN.add(E[i][0]); activeN.add(E[i][1]); });
  if (subsystem) Object.entries(N).forEach(([id, d]) => { if (d[1] === subsystem) activeN.add(id); });
  const focus = cycle || !!hover || !!subsystem;

  const edgePaths: Record<string, { dim: string; on: string; obs: string }> = {};
  for (const k of Object.keys(KC)) edgePaths[k] = { dim: "", on: "", obs: "" };
  E.forEach((e, i) => {
    const slot = activeE.has(i) ? "on" : obsEdges.has(i) && !focus ? "obs" : "dim";
    edgePaths[e[2]][slot] += `${geo(e[0], e[1])} `;
  });

  const control = ov.data?.control ?? "UNAVAILABLE";
  const L = ov.data?.live;
  const ev = (L?.observedEvidence ?? {}) as Record<string, any>;
  const sup = ev.protection?.supervisor as Record<string, any> | undefined;
  const tags: Record<string, [string, string, string]> = {};
  if (control !== "UNAVAILABLE") tags.risk = [`Control ${control}`, control === "ACTIVE" ? "#3ddc9722" : "#56ccf233", control === "ACTIVE" ? OK : "#56ccf2"];
  if (L?.heartbeat && L.heartbeat.freshness !== "fresh") tags.wd = [String(L.heartbeat.freshness).toUpperCase(), "#ff7b7233", "#ff9d95"];
  if (sup?.outcome) tags.sup = [`Last ${sup.outcome} · ${sup.freshness ?? "freshness unknown"}`, sup.outcome === "SAFE" && sup.freshness === "fresh" ? "#3ddc9722" : "#f2b44a33", sup.outcome === "SAFE" && sup.freshness === "fresh" ? OK : WARN];
  const now: Record<number, string> = {};
  const allNow = `OBSERVED control: ${control}; Kernel: ${L?.kernelState ?? "UNKNOWN"}. All diagram nodes and edges are DECLARED ARCHITECTURE. Exact flow evidence is UNAVAILABLE; aggregate record counts do not prove a path.`;

  const popDef = pop ? N[pop] : null;
  const popTele = pop && compOf(pop) ? tele[compOf(pop)!] : undefined;
  const ins = pop ? E.filter((e) => e[1] === pop).map((e) => ({ n: N[e[0]][0], w: e[3], c: KC[e[2]] })) : [];
  const outs = pop ? E.filter((e) => e[0] === pop).map((e) => ({ n: N[e[1]][0], w: e[3], c: KC[e[2]] })) : [];

  return (
    <>
      <PageHeader title="Live System">
        <span style={{ fontSize: 13, color: "var(--txt2)" }}>current architecture and authority boundaries · declared architecture with separate observed evidence</span>
      </PageHeader>
      {sysQ.isError && <div className="gl-err" role="alert">System telemetry unavailable: {(sysQ.error as Error).message} The diagram shows the declared architecture only.</div>}

      <Card className="pad" label="System diagram">
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
          <div role="group" aria-label="View" className="gl-seg">
            {([["cycle", "Follow a trading cycle"], ["all", "Show everything"]] as const).map(([k, l]) => (
              <button key={k} type="button" aria-pressed={mode === k} className="gl-segb" onClick={() => { setMode(k); setHover(null); setSubsystem(null); setPlaying(false); }}>{l}</button>
            ))}
          </div>
          {cycle && (
            <>
              <span style={{ display: "inline-flex", gap: 6 }}>
                <button type="button" className="gl-tf" aria-label="Previous step" onClick={() => { setStep((s + STEPS.length - 1) % STEPS.length); setPlaying(false); }}>‹</button>
                {!reduced && <button type="button" className="gl-tf" aria-pressed={playing} onClick={() => setPlaying(!playing)}>{playing ? "❚❚ Pause" : "▶ Play"}</button>}
                <button type="button" className="gl-tf" aria-label="Next step" onClick={() => { setStep((s + 1) % STEPS.length); setPlaying(false); }}>›</button>
              </span>
              <span className="gl-step-list" style={{ display: "inline-flex", gap: 5, alignItems: "center" }}>
                {STEPS.map((_, i) => (<button key={i} type="button" className="gl-step-dot" aria-label={`Step ${i + 1}`} aria-current={i === s} onClick={() => { setStep(i); setPlaying(false); }} style={{ width: i === s ? 26 : 10, height: 10, minHeight: 10, padding: 0, borderRadius: 5, border: 0, ["--step-tone" as string]: i === s ? "rgb(var(--a))" : i < s ? "rgb(var(--a) / .45)" : "#ffffff26", cursor: "pointer" }} />))}
              </span>
            </>
          )}
          <span style={{ flex: 1 }} />
          {LEGEND.map(([l, c]) => (<span key={l} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#c9d4df" }}><span style={{ width: 10, height: 10, borderRadius: "50%", background: c, boxShadow: `0 0 8px ${c}` }} />{l}</span>))}
        </div>

        <div className="gl-kbar" role="group" aria-label="Isolate subsystem">
          <button type="button" className="gl-tf" aria-pressed={!subsystem} onClick={() => { setSubsystem(null); setMode("all"); setPlaying(false); }}>All subsystems</button>
          {(Object.keys(GC) as Group[]).map(g => <button key={g} type="button" className="gl-tf" aria-pressed={subsystem === g} onClick={() => { setSubsystem(subsystem === g ? null : g); setMode("all"); setPlaying(false); }}>{g}</button>)}
        </div>
        <p className="gl-note" data-testid="path-legend">DECLARED — static architecture. Highlight — selected connections only. OBSERVED — exact edge evidence unavailable. STALE / UNKNOWN telemetry never animates a path.</p>
        <div style={{ overflowX: "auto" }}>
          <div className="gl-diagram" onMouseLeave={() => setHover(null)}>
            <svg viewBox="0 0 1400 820" style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }} aria-hidden="true">
              <defs>
                <pattern id="gl-pcb" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="#ffffff" strokeOpacity=".035" strokeWidth="1" /></pattern>
              </defs>
              <rect width="1400" height="820" fill="url(#gl-pcb)" />
              {ZONES.map(([l, c, x, y, w, h]) => (<g key={l}><rect x={x} y={y} width={w} height={h} rx="14" fill={c} fillOpacity=".05" /><text x={x + w / 2} y="30" textAnchor="middle" fontFamily="Cinzel, serif" fontSize="12" letterSpacing="2" fill={c}>{l}</text></g>))}
              <rect x="20" y="680" width="1360" height="130" rx="14" fill="#a78bfa" fillOpacity=".05" />
              <text x="40" y="700" fontFamily="Cinzel, serif" fontSize="12" letterSpacing="2" fill="#a78bfa">MEMORY &amp; LEARNING  ←  feedback lane</text>
              {Object.entries(edgePaths).map(([k, p]) => (
                <g key={k}>
                  <path d={p.dim || "M0 0"} fill="none" stroke={KC[k]} strokeWidth="1.1" strokeOpacity={focus ? 0.12 : 0.35} data-path-kind="declared" className="flowd" />
                  <path d={p.obs || "M0 0"} fill="none" stroke={KC[k]} strokeWidth="2.2" strokeOpacity=".9" />
                  <path d={p.on || "M0 0"} fill="none" stroke={KC[k]} strokeWidth="2.4" strokeOpacity=".95" data-path-kind="declared" className="gl-declared-highlight" />
                </g>
              ))}

            </svg>
            {Object.entries(N).map(([id, d]) => {
              const [label, g, x, y, sz, Icon] = d;
              const on = activeN.has(id) && focus;
              const dim = focus && !on;
              const c = GC[g];
              const tag = tags[id];
              const t = d[8] ? tele[d[8]] : undefined;
              return (
                <button key={id} type="button" className={`gl-node ${on ? "on" : ""}`} aria-pressed={pop === id} data-telemetry={t?.freshness ?? "unknown"} aria-label={`${label} — DECLARED ARCHITECTURE${t?.freshness ? ` — telemetry ${t.freshness}` : ""}`}
                  style={{ left: `${(x / 1400) * 100}%`, top: `${(y / 820) * 100}%`, width: sz, height: sz, ["--nc" as string]: c, opacity: dim ? 0.32 : 1 }}
                  onClick={() => { setPop(id); setPlaying(false); }} onMouseEnter={() => setHover(id)} onFocus={() => setHover(id)} onBlur={() => setHover(null)}>
                  <span className="shape" />
                  <Icon className="ico" size={Math.round(sz * 0.42)} color={c} strokeWidth={2} aria-hidden="true" />
                  <span className="nl" style={{ color: dim ? GREY : "#e6edf3" }}>{label}</span>
                  {tag && <span className="ntag" style={{ background: tag[1], color: tag[2] }}>{tag[0]}</span>}
                </button>
              );
            })}
          </div>
        </div>

        <div className="gl-cap">
          <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
            <span className="m" style={{ fontSize: 30, color: "rgb(var(--a2))", minWidth: 38 }}>{cycle ? s + 1 : "∞"}</span>
            <span style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="cz" style={{ fontSize: 11, color: "rgb(var(--mut))" }}>{cycle ? `Step ${s + 1} of ${STEPS.length} · walkthrough of the declared architecture, not live traffic` : "Everything at once"}</span>
              <span className="cg" style={{ fontSize: 22, color: "rgb(var(--hi))" }}>{cycle ? STEPS[s][0] : `All ${Object.keys(N).length} parts and ${E.length} connections`}</span>
              <span style={{ fontSize: 14, color: "#b9c5d1", lineHeight: 1.5, maxWidth: 820 }}>{cycle ? STEPS[s][1] : "Hover or focus any part to light up who it listens to and who it talks to. Click it for the full story. Every node and edge is DECLARED ARCHITECTURE. Highlighting is explanatory; exact observed flow evidence is UNAVAILABLE."}</span>
            </span>
          </div>
          {(cycle ? now[s] : allNow) && (
            <div className="gl-now"><span style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "rgb(var(--a2))", textTransform: "uppercase", letterSpacing: ".12em" }}><i className="gl-blinkdot" style={{ width: 8, height: 8, borderRadius: "50%", background: "rgb(var(--a))" }} />Right now</span><span style={{ fontSize: 13, color: "#c9d4df" }}>{cycle ? now[s] : allNow}</span></div>
          )}
        </div>
      </Card>

      <DetailPanel open={!!popDef} onClose={() => setPop(null)} title={popDef ? popDef[0] : ""} description={popDef ? `DECLARED ARCHITECTURE · ${popDef[6]}` : undefined}>
        {popDef && (
          <>
            <p style={{ margin: 0, fontSize: 14, lineHeight: 1.55, color: "#c9d4df" }}>{popDef[7]}</p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: 14 }}>
              {[["Listens to", ins, "←"], ["Talks to", outs, "→"]].map(([title, list, arrow]) => (
                <div key={String(title)} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <span className="cz" style={{ fontSize: 11, color: "rgb(var(--mut))" }}>{String(title)}</span>
                  {(list as typeof ins).length === 0 ? <span style={{ fontSize: 13, color: GREY }}>{title === "Listens to" ? "Nothing — it starts the chain" : "You — it answers you"}</span>
                    : (list as typeof ins).map((x, i) => (<span key={i} style={{ display: "flex", gap: 8, fontSize: 13 }}><span style={{ color: x.c }}>{String(arrow)}</span><span><b>{x.n}</b> <span style={{ color: "var(--txt2)" }}>{x.w} · DECLARED ARCHITECTURE</span></span></span>))}
                </div>
              ))}
            </div>
            <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>OBSERVED component telemetry / flow UNAVAILABLE</h3>
            {compOf(pop!) ? (
              popTele && Object.keys(popTele).length ? (
                <KV items={[
                  ["Telemetry component", compOf(pop!)],
                  ["Freshness", popTele.freshness ?? null], ["Health", popTele.health ?? "unknown"], ["Status", popTele.status_label ?? "not reported"],
                  ["Observed at", popTele.observed_at ?? null], ["Source", popTele.source ?? null],
                ]} />
              ) : <p className="gl-note">No telemetry was returned for “{compOf(pop!)}”. Nothing is inferred.</p>
            ) : <p className="gl-note">This part has no telemetry component of its own, so nothing is observed for it here.</p>}
            {popTele?.summary && <p className="gl-note">{popTele.summary}</p>}
            <p className="gl-note">The description is the declared architecture. Telemetry freshness is not activity or health proof.</p>
          </>
        )}
      </DetailPanel>
    </>
  );
}
