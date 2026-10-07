/** Live System — approved "Final · 7" design (pipeline layout, glow icons).
 * The 30 parts and their connections are the DECLARED architecture; what is
 * observed comes from /system and /system/observed. Counts of records written
 * prove data moved, not rate or health, so nothing here animates from them:
 * only the explanatory walkthrough animates, and it says so. */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Activity, BookOpen, CheckCheck, ChartCandlestick, Dices, Globe, HeartPulse, Landmark, Layers, Lightbulb, LogOut, MessageSquare,
  Network, Newspaper, Radar, Scale, ShieldCheck, ScanEye, Sparkles, Vault, Waves, Zap, FlaskConical, ArrowDownUp, Users, Rows3, Layers2, Tag, Telescope, type LucideIcon,
} from "lucide-react";
import { usePreview } from "../context";
import { useRead, rows as asRows, rec } from "../components/records";
import { Card, DetailPanel, KV } from "../components/glass";
import { PageHeader } from "../components/PageHeader";
import { timestamp as utc } from "../time";

type Group = "Market" | "Perception" | "Analyst" | "Decision" | "Safety" | "Execution" | "Memory" | "Partner";
const GC: Record<Group, string> = { Market: "#4aa8ff", Perception: "#3fd5c8", Analyst: "rgb(86 204 242)", Decision: "rgb(86 204 242)", Safety: "#ff7b72", Execution: "#f2b44a", Memory: "#a78bfa", Partner: "#f472b6" };
const KC: Record<string, string> = { data: "#4aa8ff", sense: "#3fd5c8", vote: "rgb(86 204 242)", dec: "rgb(86 204 242)", order: "#f2b44a", mem: "#a78bfa", safe: "#ff7b72", guard: "#56ccf2" };
const OK = "#3ddc97", WARN = "#f2b44a", BAD = "#ff7b72", GREY = "#8d9fb2";

// id: [label, group, x, y, size, icon, code, description, telemetry component]
type Def = [string, Group, number, number, number, LucideIcon, string, string, string | null];
const AN = ["structure", "flow", "momentum", "value", "rotation", "positioning", "depth"];
const N: Record<string, Def> = {
  binIn: ["Binance · market", "Market", 85, 160, 64, Landmark, "exchange", "The exchange LUFFY trades on. Every cycle it supplies prices, order books, funding rates and open interest.", null],
  feed: ["Market feed", "Market", 85, 400, 62, ChartCandlestick, "trader/data/feed.py", "Collects candles, order books and derivatives data for the watched coins, plus Bitcoin context, and stores them so every other part reads the same numbers.", "market-data"],
  wm: ["World Model", "Perception", 265, 120, 56, Globe, "trader/world", "Keeps a running picture of the whole market — regime, volatility, liquidity and funding — that other agents can ask about.", "attention"],
  att: ["Attention", "Perception", 265, 270, 58, Radar, "trader/cognition/attention.py", "Scans every coin and flags the ones behaving unusually (volume, volatility, divergence), so the analysts spend their effort where something is happening.", "attention"],
  reg: ["Regime", "Perception", 265, 420, 56, Waves, "trader/agents/regime.py", "Decides what kind of market this is — trending, ranging or violent — and tells the orchestrator which analysts are worth listening to right now.", "attention"],
  grd: ["News & macro guards", "Perception", 265, 570, 56, Newspaper, "agents/news_guard.py · macro_guard.py", "Watches headlines and the economic calendar. Around big news they raise the bar to trade or block entries entirely.", "attention"],
  structure: ["Structure", "Analyst", 470, 90, 48, Rows3, "agents/structure.py", "Analyst: reads price structure — ranges, breakouts, support and resistance — and votes long, short or flat.", "orchestrator"],
  flow: ["Flow", "Analyst", 470, 178, 48, ArrowDownUp, "agents/flow.py", "Analyst: reads who is aggressive — buyers lifting offers or sellers hitting bids — and votes.", "orchestrator"],
  momentum: ["Momentum", "Analyst", 470, 266, 48, Activity, "agents/momentum.py", "Analyst: measures how strong and persistent the move is across timeframes, and votes.", "orchestrator"],
  value: ["Value", "Analyst", 470, 354, 48, Tag, "orchestrator weight \"value\"", "Analyst: asks whether price is stretched far from fair value, and votes.", "orchestrator"],
  rotation: ["Rotation", "Analyst", 470, 442, 48, Layers2, "orchestrator weight \"rotation\"", "Analyst: watches money moving between Bitcoin and altcoins, and votes.", "orchestrator"],
  positioning: ["Positioning", "Analyst", 470, 530, 48, Users, "agents/positioning.py", "Analyst: reads funding and open interest to see if traders are crowded on one side, and votes.", "orchestrator"],
  depth: ["Depth", "Analyst", 470, 618, 48, Telescope, "agents/orderbook_depth.py", "Analyst: reads the order book to see where real liquidity sits, and votes.", "orchestrator"],
  ewa: ["Weights & calibration", "Decision", 700, 150, 56, Scale, "agents/weights_online.py · calibration.py", "Learns how much to trust each analyst from past results, and corrects over- or under-confident votes.", "orchestrator"],
  strat: ["Strategy registry", "Decision", 700, 400, 58, Layers, "trader/strategy", "The list of strategies LUFFY is allowed to trade. Each live strategy adds its own buy or sell signal on top of the analyst votes.", "orchestrator"],
  meta: ["Meta-label", "Decision", 700, 560, 56, Dices, "brain/meta_label.py", "A second opinion: estimates the chance a trade wins and can shrink its size — never grow it.", "orchestrator"],
  orch: ["Orchestrator", "Decision", 865, 350, 84, Network, "trader/engine/orchestrator.py", "The decision maker. It combines every analyst vote and strategy signal into one score, compares it with a threshold, and decides BUY, SELL or HOLD — always with a written reason.", "orchestrator"],
  wd: ["Watchdog", "Safety", 1060, 110, 54, HeartPulse, "trader/engine/watchdog.py", "Listens for LUFFY's heartbeat. If it goes quiet, trading must not continue.", "kernel"],
  sup: ["Supervisor", "Safety", 1060, 250, 56, ScanEye, "trader/engine/supervisor.py", "Checks every few minutes whether it is safe to trade: positions readable, stops in place, heartbeat fresh. It can freeze LUFFY and lift freezes it set itself.", "supervisor"],
  risk: ["Risk gate", "Safety", 1060, 430, 64, ShieldCheck, "trader/engine/risk.py", "The only door to a real order. Checks the control state, position limits, exposure, margin and daily loss before anything is sent.", "risk"],
  exe: ["Executor", "Execution", 1240, 350, 60, Zap, "trader/engine/executor.py", "Sends approved orders to Binance together with a stop-loss that lives on the exchange, so protection holds even if LUFFY stops.", "execution"],
  binOut: ["Binance · orders", "Market", 1330, 170, 56, Landmark, "exchange", "Where orders and their stops actually live and get filled.", null],
  exits: ["Exits & reconcile", "Execution", 1240, 540, 58, LogOut, "engine/exits.py · reconcile.py", "Trails stops, notices stop-loss fills, and regularly checks that the exchange and the journal agree on every position.", "execution"],
  jr: ["Journal", "Memory", 1110, 750, 60, BookOpen, "trader/core/journal.py", "The single source of truth: every decision, vote, order, fill and control event is written here.", "journal"],
  out: ["Outcomes", "Memory", 1280, 750, 54, CheckCheck, "engine/outcomes.py", "Turns each closed trade into a result: profit, R multiple and how far it went for and against.", "journal"],
  learn: ["Learning", "Memory", 890, 750, 56, Sparkles, "trader/learning", "Feeds results back so analyst weights and calibration improve over time.", null],
  rs: ["Research", "Memory", 670, 750, 56, FlaskConical, "trader/research", "Background search for new trading rules, with strict tests on unseen coins and dates before anything can graduate.", "research"],
  brain: ["Strategist", "Memory", 450, 750, 54, Lightbulb, "trader/brain", "Writes, promotes and retires strategies based on research findings and post-mortems.", "research"],
  vault: ["Knowledge vault", "Memory", 230, 750, 54, Vault, "knowledge/", "Every lesson, risk rule and strategy note, linked together — LUFFY's long-term memory.", null],
  chat: ["LUFFY (partner)", "Partner", 1320, 650, 52, MessageSquare, "trader/chat", "The voice you talk to. Answers from the journal and the vault, and suggests actions — anything that changes trading still needs your approval.", "dashboard"],
};
type Edge = [string, string, keyof typeof KC, string];
const E: Edge[] = [
  ["binIn", "feed", "data", "prices, books, funding"], ["feed", "wm", "data", "market snapshot"], ["feed", "att", "data", "candles for every coin"], ["feed", "reg", "data", "Bitcoin trend, ADX"],
  ["wm", "att", "sense", "regime & volume context"],
  ...AN.map((a): Edge => ["att", a, "sense", "coins worth a look"]),
  ["reg", "orch", "sense", "which analysts fit this market"], ["grd", "orch", "guard", "raise the bar near news"],
  ...AN.map((a): Edge => [a, "orch", "vote", "vote + confidence + reason"]),
  ["strat", "orch", "vote", "strategy signals"], ["ewa", "orch", "dec", "learned trust per analyst"], ["meta", "orch", "dec", "chance to win, size cap"],
  ["orch", "jr", "mem", "every decision, traded or not"], ["orch", "risk", "dec", "BUY / SELL intent"],
  ["wd", "sup", "safe", "heartbeat"], ["sup", "risk", "safe", "ACTIVE / FROZEN"],
  ["risk", "exe", "order", "approved order"], ["exe", "binOut", "order", "order + exchange stop"],
  ["binOut", "exits", "order", "fills, stop hits"], ["exits", "jr", "mem", "exits & reconcile results"], ["exits", "sup", "safe", "position safety evidence"],
  ["jr", "out", "mem", "closed trades"], ["out", "learn", "mem", "results"], ["learn", "ewa", "mem", "updated weights"],
  ["out", "rs", "mem", "evidence"], ["rs", "brain", "mem", "findings"], ["brain", "strat", "mem", "new & retired strategies"], ["rs", "vault", "mem", "lessons"],
  ["vault", "chat", "mem", "knowledge"], ["jr", "chat", "mem", "records"],
];
const STEPS: [string, string, number[]][] = [
  ["Market data comes in", "Every cycle starts at the exchange. The market feed pulls candles, order books, funding and open interest for the watched coins, plus Bitcoin's trend as context.", [0, 1, 2, 3]],
  ["Attention picks what to look at", "The World Model adds context and Attention flags the coins behaving unusually — so the seven analysts focus where something is actually happening.", [4, 5, 6, 7, 8, 9, 10, 11]],
  ["Context sets the rules", "Regime decides which kinds of edge pay in this market. The news and macro guards raise the bar — or block entries — around big events.", [12, 13]],
  ["Seven analysts vote", "Structure, Flow, Momentum, Value, Rotation, Positioning and Depth each vote long, short or flat, with a confidence and a written reason.", [14, 15, 16, 17, 18, 19, 20]],
  ["Strategies add their signals", "Each strategy in the registry checks its own rule and adds a buy or sell signal. Strategies speak louder than any single analyst.", [21]],
  ["The orchestrator decides", "Votes are weighted by how much each analyst has earned trust, corrected for over-confidence, and checked by the meta-label. One score against one threshold: BUY, SELL or HOLD — written to the journal either way.", [22, 23, 24]],
  ["The risk gate", "An intent to trade must pass the risk gate: control state, limits, exposure, margin, daily loss. The Supervisor — fed by the Watchdog's heartbeat — decides whether trading is ACTIVE or FROZEN.", [25, 26, 27]],
  ["The order goes out", "Approved orders go to Binance with a stop-loss that lives on the exchange — so the position stays protected even if LUFFY stops.", [28, 29]],
  ["Exits and reconcile", "Exits trail stops and catch stop-loss fills. Reconcile checks that the exchange and the journal agree on every position and feeds that evidence to the Supervisor.", [30, 31, 32]],
  ["LUFFY learns", "Closed trades become outcomes. Learning updates how much each analyst is trusted; Research tests new rules; the Strategist promotes or retires strategies; lessons go into the vault.", [33, 34, 35, 36, 37, 38, 39]],
  ["You talk to LUFFY", "The partner reads the journal and the knowledge vault to answer you in plain words, and suggests actions — anything that changes trading still needs your approval.", [40, 41]],
];
const ZONES: [string, string, number, number, number, number][] = [
  ["MARKET", "#4aa8ff", 20, 40, 130, 610], ["PERCEPTION", "#3fd5c8", 200, 40, 130, 610], ["ANALYSTS", "#e2c88a", 405, 40, 130, 610], ["DECISION", "#e2c88a", 620, 40, 330, 610],
  ["SAFETY", "#ff7b72", 995, 40, 130, 610], ["EXECUTION", "#f2b44a", 1170, 40, 210, 610],
];
const geo = (a: string, b: string) => { const [x1, y1, x2, y2] = [N[a][2], N[a][3], N[b][2], N[b][3]]; const dx = (x2 - x1) * 0.5; return `M${x1} ${y1} C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`; };
const LEGEND: [string, string][] = [["Market data", "#4aa8ff"], ["Perception", "#3fd5c8"], ["Votes & decisions", "rgb(86 204 242)"], ["Orders", "#f2b44a"], ["Memory & learning", "#a78bfa"], ["Safety", "#ff7b72"]];

type Tele = { freshness?: string; health?: string | null; status_label?: string | null; observed_at?: string | null; summary?: string; source?: string };

export default function LiveSystem() {
  const { adapter } = usePreview();
  const live = adapter.mode === "LIVE";
  const sysQ = useRead(live ? "system" : null, 60000);
  const obsQ = useRead(live ? "system/observed" : null, 60000);
  const deskQ = useRead(live ? "desk" : null, 60000);
  const ov = useQuery({ queryKey: ["overview", "normal"], queryFn: ({ signal }) => adapter.overview("normal", signal), staleTime: 10000, refetchInterval: 30000, refetchIntervalInBackground: false, enabled: live });
  const [mode, setMode] = useState<"all" | "cycle">("all");
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [hover, setHover] = useState<string | null>(null);
  const [pop, setPop] = useState<string | null>(null);
  const reduced = typeof matchMedia !== "undefined" && matchMedia("(prefers-reduced-motion: reduce)").matches;

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
  // an edge is "observed" only if a flow between its components wrote records in its window
  const flows = asRows(obsQ.data?.flows) as Record<string, any>[];
  const observed = (e: Edge) => flows.some((f) => (f.count ?? 0) > 0 && f.source === compOf(e[0]) && f.target === compOf(e[1]));
  const obsEdges = new Set(E.map((e, i) => (observed(e) ? i : -1)).filter((i) => i >= 0));

  const cycle = mode === "cycle";
  const s = step % STEPS.length;
  const activeE = new Set<number>(cycle ? STEPS[s][2] : []);
  if (hover) { activeE.clear(); E.forEach((e, i) => { if (e[0] === hover || e[1] === hover) activeE.add(i); }); }
  const activeN = new Set<string>();
  activeE.forEach((i) => { activeN.add(E[i][0]); activeN.add(E[i][1]); });
  const focus = cycle || !!hover;

  const edgePaths: Record<string, { dim: string; on: string; obs: string }> = {};
  for (const k of Object.keys(KC)) edgePaths[k] = { dim: "", on: "", obs: "" };
  E.forEach((e, i) => {
    const slot = activeE.has(i) ? "on" : obsEdges.has(i) && !focus ? "obs" : "dim";
    edgePaths[e[2]][slot] += `${geo(e[0], e[1])} `;
  });
  const packets = (cycle ? [...activeE] : []).slice(0, 10);

  const control = ov.data?.control ?? "UNAVAILABLE";
  const L = ov.data?.live;
  const ev = (L?.observedEvidence ?? {}) as Record<string, any>;
  const sup = ev.protection?.supervisor as Record<string, any> | undefined;
  const scan = (deskQ.data as any)?.scan as { observed_at: string; coins: any[]; traded: number } | null | undefined;
  const tags: Record<string, [string, string, string]> = {};
  if (control !== "UNAVAILABLE") tags.risk = [control, control === "ACTIVE" ? "#3ddc9722" : "#56ccf233", control === "ACTIVE" ? OK : "#56ccf2"];
  if (L?.heartbeat && L.heartbeat.freshness !== "fresh") tags.wd = [String(L.heartbeat.freshness).toUpperCase(), "#ff7b7233", "#ff9d95"];
  if (sup?.outcome) tags.sup = [String(sup.outcome), sup.outcome === "SAFE" ? "#3ddc9722" : "#f2b44a33", sup.outcome === "SAFE" ? OK : WARN];
  if (ov.data?.positions) tags.exits = [`${ov.data.positions.length} POSITION${ov.data.positions.length === 1 ? "" : "S"}`, "#3ddc9722", OK];

  // "Right now" lines come only from records already read; steps without one say nothing
  const now: Record<number, string> = {};
  if (scan) {
    const top = [...scan.coins].sort((a, b) => Math.abs(b.score ?? 0) / (b.threshold || 1) - Math.abs(a.score ?? 0) / (a.threshold || 1))[0];
    now[1] = `Latest recorded scan (${utc(scan.observed_at)}) scored ${scan.coins.length} coins; ${scan.traded} traded.`;
    if (top) now[5] = `In that scan the strongest was ${String(top.symbol).split("/")[0]} at ${(top.score ?? 0) >= 0 ? "+" : "−"}${Math.abs(top.score ?? 0).toFixed(3)} against a bar of ±${(top.threshold ?? 0).toFixed(3)}.`;
  }
  now[6] = `Control state is ${control}${L?.heartbeat ? `; heartbeat is ${L.heartbeat.freshness}` : ""}${sup?.outcome ? `; the Supervisor's last pass reported ${String(sup.outcome).toLowerCase()}` : ""}.`;
  if (ov.data?.positions) now[8] = `${ov.data.positions.length} position${ov.data.positions.length === 1 ? "" : "s"} open in the journal; protection is ${(L?.protection?.status ?? "UNAVAILABLE").toLowerCase()}.`;
  const allNow = `Control state is ${control}${tele.kernel?.status_label ? `; the kernel is ${String(tele.kernel.status_label).toLowerCase()}` : ""}. Lit connections below have records written in their window; ${obsEdges.size === 0 ? "none do right now." : `${obsEdges.size} do.`}`;

  const popDef = pop ? N[pop] : null;
  const popTele = pop && compOf(pop) ? tele[compOf(pop)!] : undefined;
  const ins = pop ? E.filter((e) => e[1] === pop).map((e) => ({ n: N[e[0]][0], w: e[3], c: KC[e[2]] })) : [];
  const outs = pop ? E.filter((e) => e[0] === pop).map((e) => ({ n: N[e[1]][0], w: e[3], c: KC[e[2]] })) : [];

  return (
    <>
      <PageHeader title="Live System">
        <span style={{ fontSize: 13, color: "var(--txt2)" }}>how LUFFY thinks and trades · every agent and how they talk to each other</span>
      </PageHeader>
      {(sysQ.isError || obsQ.isError) && <div className="gl-err" role="alert">System telemetry unavailable: {((sysQ.error ?? obsQ.error) as Error).message} The diagram shows the declared architecture only.</div>}

      <Card className="pad" label="System diagram">
        <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
          <div role="group" aria-label="View" className="gl-seg">
            {([["cycle", "Follow a trading cycle"], ["all", "Show everything"]] as const).map(([k, l]) => (
              <button key={k} type="button" aria-pressed={mode === k} className="gl-segb" onClick={() => { setMode(k); setHover(null); }}>{l}</button>
            ))}
          </div>
          {cycle && (
            <>
              <span style={{ display: "inline-flex", gap: 6 }}>
                <button type="button" className="gl-tf" aria-label="Previous step" onClick={() => { setStep((s + STEPS.length - 1) % STEPS.length); setPlaying(false); }}>‹</button>
                {!reduced && <button type="button" className="gl-tf" aria-pressed={playing} onClick={() => setPlaying(!playing)}>{playing ? "❚❚ Pause" : "▶ Play"}</button>}
                <button type="button" className="gl-tf" aria-label="Next step" onClick={() => { setStep((s + 1) % STEPS.length); setPlaying(false); }}>›</button>
              </span>
              <span style={{ display: "inline-flex", gap: 5, alignItems: "center" }}>
                {STEPS.map((_, i) => (<button key={i} type="button" aria-label={`Step ${i + 1}`} aria-current={i === s} onClick={() => { setStep(i); setPlaying(false); }} style={{ width: i === s ? 26 : 10, height: 10, minHeight: 10, padding: 0, borderRadius: 5, border: 0, background: i === s ? "rgb(var(--a))" : i < s ? "rgb(var(--a) / .45)" : "#ffffff26", cursor: "pointer" }} />))}
              </span>
            </>
          )}
          <span style={{ flex: 1 }} />
          {LEGEND.map(([l, c]) => (<span key={l} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "#c9d4df" }}><span style={{ width: 10, height: 10, borderRadius: "50%", background: c, boxShadow: `0 0 8px ${c}` }} />{l}</span>))}
        </div>

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
                  <path d={p.dim || "M0 0"} fill="none" stroke={KC[k]} strokeWidth="1.1" strokeOpacity={focus ? 0.12 : 0.35} className="flowd" />
                  <path d={p.obs || "M0 0"} fill="none" stroke={KC[k]} strokeWidth="2.2" strokeOpacity=".9" />
                  <path d={p.on || "M0 0"} fill="none" stroke={KC[k]} strokeWidth="2.4" strokeOpacity=".95" className={cycle && !reduced ? "flowp" : ""} />
                </g>
              ))}
              {cycle && !reduced && packets.map((i, j) => (<circle key={`${s}-${i}`} r="6" className="pk" style={{ offsetPath: `path('${geo(E[i][0], E[i][1])}')`, fill: KC[E[i][2]], animationDelay: `${(j * 0.13).toFixed(2)}s`, filter: `drop-shadow(0 0 6px ${KC[E[i][2]]})` }} />))}
            </svg>
            {Object.entries(N).map(([id, d]) => {
              const [label, g, x, y, sz, Icon] = d;
              const on = activeN.has(id) && focus;
              const dim = focus && !on;
              const c = GC[g];
              const tag = tags[id];
              const t = d[8] ? tele[d[8]] : undefined;
              return (
                <button key={id} type="button" className={`gl-node ${on ? "on" : ""}`} aria-label={`${label}${t?.freshness ? ` — telemetry ${t.freshness}` : ""}`}
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
              <span style={{ fontSize: 14, color: "#b9c5d1", lineHeight: 1.5, maxWidth: 820 }}>{cycle ? STEPS[s][1] : "Hover or focus any part to light up who it listens to and who it talks to. Click it for the full story. Lit solid lines have records written in their window; dashed lines are declared only."}</span>
            </span>
          </div>
          {(cycle ? now[s] : allNow) && (
            <div className="gl-now"><span style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11, color: "rgb(var(--a2))", textTransform: "uppercase", letterSpacing: ".12em" }}><i className="gl-blinkdot" style={{ width: 8, height: 8, borderRadius: "50%", background: "rgb(var(--a))" }} />Right now</span><span style={{ fontSize: 13, color: "#c9d4df" }}>{cycle ? now[s] : allNow}</span></div>
          )}
        </div>
      </Card>

      <DetailPanel open={!!popDef} onClose={() => setPop(null)} title={popDef ? popDef[0] : ""} description={popDef ? `${popDef[1] === "Analyst" ? "Analyst · one of seven voters" : popDef[1]} · ${popDef[6]}` : undefined}>
        {popDef && (
          <>
            <p style={{ margin: 0, fontSize: 14, lineHeight: 1.55, color: "#c9d4df" }}>{popDef[7]}</p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: 14 }}>
              {[["Listens to", ins, "←"], ["Talks to", outs, "→"]].map(([title, list, arrow]) => (
                <div key={String(title)} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <span className="cz" style={{ fontSize: 11, color: "rgb(var(--mut))" }}>{String(title)}</span>
                  {(list as typeof ins).length === 0 ? <span style={{ fontSize: 13, color: GREY }}>{title === "Listens to" ? "Nothing — it starts the chain" : "You — it answers you"}</span>
                    : (list as typeof ins).map((x, i) => (<span key={i} style={{ display: "flex", gap: 8, fontSize: 13 }}><span style={{ color: x.c }}>{String(arrow)}</span><span><b>{x.n}</b> <span style={{ color: "var(--txt2)" }}>{x.w}</span></span></span>))}
                </div>
              ))}
            </div>
            <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>Observed now</h3>
            {compOf(pop!) ? (
              popTele && Object.keys(popTele).length ? (
                <KV items={[
                  ["Telemetry component", compOf(pop!)],
                  ["Freshness", popTele.freshness ?? null], ["Health", popTele.health ?? "unknown"], ["Status", popTele.status_label ?? "not reported"],
                  ["Observed at", popTele.observed_at ? utc(popTele.observed_at) : null], ["Source", popTele.source ?? null],
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
