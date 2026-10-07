/** Knowledge — approved "Final · 8" design: LUFFY's long-term memory (the vault).
 * "Map" stands on one note and shows everything it touches; "List" is every note
 * as a table. Relations are as written in the notes: not verified evidence or
 * causality. Unresolved links are counted, never drawn as notes. */
import { useMemo, useState } from "react";
import { Network } from "lucide-react";
import { usePreview } from "../context";
import { useRead, rows as asRows, rec } from "../components/records";
import { Card, DataTable, DetailPanel, KV, type Col } from "../components/glass";
import { PageHeader } from "../components/PageHeader";
import { timestamp as utc } from "../time";

type Node = { id: string; label: string; kind: string; folder: string; status: string | null; deg: number; observed_at: string | null };
const TC: Record<string, string> = {
  strategy: "rgb(86 204 242)", lesson: "#f2b44a", "risk-rule": "#ff7b72", "market-mechanism": "#4aa8ff", theory: "#7fd3ff", "research-finding": "#a78bfa",
  role: "#3fd5c8", "org-chart": "#3fd5c8", moc: "#ffffff", experiment: "#e2c88a", decision: "#f472b6", "daily-review": "#8d9fb2", postmortem: "#fb855c", autopsy: "#ff9d95",
  "market-regime": "#5eead4", "regime-playbook": "#86efdc", ledger: "#c9d4df",
};
const TL: Record<string, string> = {
  strategy: "Strategy", lesson: "Lesson", "risk-rule": "Risk rule", "market-mechanism": "Mechanism", theory: "Theory", "research-finding": "Research finding", role: "Team role",
  "org-chart": "Org chart", moc: "Map of content", experiment: "Experiment", decision: "Decision", "daily-review": "Daily review", postmortem: "Post-mortem", autopsy: "Autopsy",
  "market-regime": "Market regime", "regime-playbook": "Regime playbook", ledger: "Ledger",
};
const GLAB: Record<string, string> = { strategy: "Strategies", lesson: "Lessons", "risk-rule": "Risk rules", "market-mechanism": "Mechanisms", theory: "Theories", "research-finding": "Findings", role: "Team", moc: "Maps" };
const KL: Record<string, string> = { wikilink: "linked in the note", derived_from: "derived from", supports: "supports", contradicts: "contradicts", fails_in: "fails in", works_in: "works in", precedes: "precedes", causes: "causes" };
const RC = (rel: string) => (rel === "supports" || rel === "works_in" ? "sup" : rel === "derived_from" ? "der" : rel === "contradicts" || rel === "fails_in" ? "bad" : rel === "precedes" || rel === "causes" ? "flow" : "link");
const RCOL = { link: "#9fb3c8", der: "#a78bfa", sup: "#3ddc97", bad: "#ff7b72", flow: "#f2b44a" } as const;
const STC: Record<string, [string, string]> = { active: ["#3ddc97", "#3ddc9724"], "core-belief": ["#7fd3ff", "#7fd3ff24"], candidate: ["#f2b44a", "#f2b44a22"], "—": ["#a9b8c8", "#ffffff10"] };
const col = (k: string) => TC[k] ?? "#8d9fb2";
const label = (k: string) => TL[k] ?? k;
const cut = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const grp = (k: string) => (k === "org-chart" || k === "role" ? "role" : k);

/** The note's own one-line claim: its first blockquote, else its first prose paragraph. */
export function claimOf(body: unknown): string | null {
  if (typeof body !== "string") return null;
  const lines = body.split("\n").map((l) => l.trim());
  const quote = lines.find((l) => l.startsWith(">"));
  const text = quote ? quote.replace(/^>+\s*/, "") : lines.find((l) => l && !l.startsWith("#") && !l.startsWith("-") && !l.startsWith("|") && !l.startsWith("```") && l !== "---");
  return text ? text.replace(/\[\[([^\]|]+)(\|[^\]]+)?\]\]/g, "$1").replace(/[*_`]/g, "") : null;
}

export default function Knowledge() {
  const { adapter } = usePreview();
  const q = useRead(adapter.mode === "LIVE" ? "knowledge" : null, 300000);
  const [view, setView] = useState<"map" | "list">("map");
  const [text, setText] = useState("");
  const [types, setTypes] = useState<string[]>([]);
  const [focus, setFocus] = useState<string | null>(null);
  const [trail, setTrail] = useState<string[]>([]);
  const [sel, setSel] = useState<string | null>(null);

  const g = useMemo(() => {
    const nodes: Node[] = asRows(q.data?.nodes).map((n) => ({ id: String(n.id), label: String(n.label), kind: String(n.kind), folder: String(n.folder ?? ""), status: (n.status as string) ?? null, deg: 0, observed_at: (rec(n.provenance)?.observed_at as string) ?? null }));
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const adj = new Map<string, [string, string][]>();
    let resolved = 0;
    for (const e of asRows(q.data?.edges)) {
      const a = String(e.source), b = String(e.target), rel = String(e.relation);
      if (!byId.has(a) || !byId.has(b) || a === b) continue;
      resolved++;
      (adj.get(a) ?? adj.set(a, []).get(a)!).push([b, rel]);
      (adj.get(b) ?? adj.set(b, []).get(b)!).push([a, rel]);
    }
    for (const n of nodes) n.deg = new Set((adj.get(n.id) ?? []).map(([j]) => j)).size;
    return { nodes, byId, adj, resolved };
  }, [q.data]);

  const qq = text.trim().toLowerCase();
  const typeOk = (n: Node) => !types.length || types.includes(n.kind);
  const match = (n: Node) => !qq || `${n.label} ${n.folder} ${n.kind}`.toLowerCase().includes(qq);
  const kinds = useMemo(() => [...new Set(g.nodes.map((n) => n.kind))].sort(), [g.nodes]);
  const def = g.nodes.find((n) => n.label === "Strategist") ?? [...g.nodes].sort((a, b) => b.deg - a.deg)[0];
  const f0 = focus && g.byId.has(focus) ? focus : def?.id;
  const fn = f0 ? g.byId.get(f0)! : null;
  const go = (id: string) => { setFocus(id); setTrail((t) => [...t.slice(-5), id]); setText(""); };
  const crumbs = trail.length ? trail : f0 ? [f0] : [];

  // neighbours of the focus, unique, filtered by type chips
  const nbs = useMemo(() => {
    if (!f0) return [];
    const seen = new Set<string>();
    return (g.adj.get(f0) ?? []).filter(([j]) => j !== f0 && !seen.has(j) && seen.add(j)).filter(([j]) => typeOk(g.byId.get(j)!));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [f0, g, types]);
  const nbo = useMemo(() => [...nbs].sort((a, b) => (a[1] > b[1] ? 1 : a[1] < b[1] ? -1 : 0) || (g.byId.get(a[0])!.kind > g.byId.get(b[0])!.kind ? 1 : -1)), [nbs, g]);
  const two = nbo.length > 18;
  const CX = 700, CY = 380, FX = 1.55, R1 = two ? 190 : 230, R2 = 300;
  const ring = useMemo(() => nbo.map(([j, rel], idx) => {
    const a = -Math.PI / 2 + (idx / nbo.length) * Math.PI * 2, r = two ? (idx % 2 ? R2 : R1) : R1;
    return { j, rel, a, x: CX + Math.cos(a) * r * FX, y: CY + Math.sin(a) * r };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [nbo, two]);
  const far = useMemo(() => {
    const placed = new Map(ring.map((r) => [r.j, r]));
    const out = new Map<string, string>();
    for (const r of ring) for (const [z] of g.adj.get(r.j) ?? []) if (z !== f0 && !placed.has(z) && !out.has(z) && out.size < 60 && typeOk(g.byId.get(z)!)) out.set(z, r.j);
    return [...out.entries()].map(([z, j], k) => {
      const p = placed.get(j)!, a2 = p.a + ((k % 5) - 2) * 0.05;
      return { z, from: p, x: Math.min(1385, Math.max(15, CX + Math.cos(a2) * 360 * FX)), y: Math.min(748, Math.max(12, CY + Math.sin(a2) * 360)) };
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ring, f0, types]);
  const paths: Record<string, string> = { link: "", der: "", sup: "", bad: "", flow: "", far: "" };
  for (const r of ring) paths[RC(r.rel)] += `M${CX} ${CY}L${r.x.toFixed(1)} ${r.y.toFixed(1)}`;
  for (const f of far) paths.far += `M${f.from.x.toFixed(1)} ${f.from.y.toFixed(1)}L${f.x.toFixed(1)} ${f.y.toFixed(1)}`;
  const groups = useMemo(() => Object.entries(nbs.reduce<Record<string, number>>((acc, [j]) => { const k = grp(g.byId.get(j)!.kind); acc[k] = (acc[k] ?? 0) + 1; return acc; }, {})).sort((a, b) => b[1] - a[1]), [nbs, g]);
  const hits = qq && view === "map" ? g.nodes.filter((n) => typeOk(n) && match(n)).slice(0, 6) : [];
  const starts = [...g.nodes].sort((a, b) => b.deg - a.deg).slice(0, 6);

  const count = (f: (n: Node) => boolean) => g.nodes.filter(f).length;
  const stats: [number, string, string][] = [
    [g.nodes.length, "notes", "rgb(var(--hi))"], [g.resolved, "links", "rgb(var(--a2))"],
    [count((n) => n.status === "active" || n.status === "core-belief"), "active", "#3ddc97"], [count((n) => n.status === "candidate"), "candidates", "#f2b44a"],
    [q.data ? Number(q.data.unresolved_edges ?? 0) : 0, "unresolved links", "#8d9fb2"],
  ];

  const cols: Col<Node>[] = useMemo(() => [
    { key: "t", label: "Note", primary: true, filter: "text", value: (n) => n.label, cell: (n) => <span style={{ display: "flex", alignItems: "center", gap: 8 }}><i style={{ width: 9, height: 9, borderRadius: "50%", background: col(n.kind), boxShadow: `0 0 8px ${col(n.kind)}` }} /><span style={{ fontWeight: 600, fontSize: 13 }}>{n.label}</span></span> },
    { key: "type", label: "Type", filter: "select", value: (n) => label(n.kind), cell: (n) => <span style={{ fontSize: 13, color: col(n.kind) }}>{label(n.kind)}</span> },
    { key: "st", label: "State", filter: "select", value: (n) => n.status ?? "—", cell: (n) => { const [c, bg] = STC[n.status ?? "—"] ?? STC["—"]; return <span className="gl-pill" style={{ color: c, background: bg, fontWeight: 700, textTransform: "uppercase", fontSize: 10 }}>{n.status ?? "—"}</span>; } },
    { key: "deg", label: "Links", align: "r", value: (n) => n.deg, cell: (n) => n.deg },
    { key: "folder", label: "Folder", filter: "select", value: (n) => n.folder || "(root)", cell: (n) => <span className="m" style={{ fontSize: 12, color: "var(--txt2)" }}>{n.folder || "(root)"}</span> },
  ], []);
  const listRows = g.nodes.filter((n) => typeOk(n) && match(n));

  return (
    <>
      <PageHeader title="Knowledge">
        <span style={{ fontSize: 13, color: "var(--txt2)" }}>LUFFY's long-term memory · what it has learned and how it connects</span>
      </PageHeader>
      {q.isError && <div className="gl-err" role="alert">{(q.error as Error).message} Nothing was substituted.</div>}
      {adapter.mode !== "LIVE" && <p className="gl-empty">The knowledge vault is unavailable in fixture mode.</p>}
      {q.isPending && adapter.mode === "LIVE" && <p className="gl-empty">Reading the vault…</p>}
      {q.data && (
        <>
          <div className="gl-kbar">
            <span className="cz" style={{ fontSize: 11, color: "rgb(var(--mut))" }}>View</span>
            <div role="group" aria-label="View" className="gl-seg">
              {([["map", "Map"], ["list", "List"]] as const).map(([k, l]) => (<button key={k} type="button" className="gl-segb" aria-pressed={view === k} onClick={() => { setView(k); setSel(null); }}>{l}</button>))}
            </div>
            <span style={{ fontSize: 13, color: "var(--txt2)" }}>{view === "map" ? "Stand on one note and see everything it touches. Click any note to walk there." : "Every note as a table — sort, filter and page through."}</span>
            <span style={{ flex: 1 }} />
            {stats.map(([v, l, c]) => (<span key={l} style={{ fontSize: 12, color: "var(--txt2)", display: "inline-flex", gap: 6, alignItems: "baseline" }}><b className="m" style={{ fontSize: 16, color: c }}>{v}</b>{l}</span>))}
          </div>
          <div className="gl-kbar">
            <label className="gl-search" style={{ flex: "0 1 420px" }}><input aria-label="Search notes" placeholder="Search notes… e.g. Donchian, funding, stop" value={text} onChange={(e) => setText(e.target.value)} style={{ width: "100%" }} /></label>
            <span style={{ flex: 1 }} />
            {kinds.filter((k) => k !== "moc" && k !== "org-chart").map((k) => (
              <button key={k} type="button" className="gl-tf" aria-pressed={types.includes(k)} style={{ padding: "0 12px", display: "inline-flex", gap: 6, alignItems: "center" }} onClick={() => setTypes(types.includes(k) ? types.filter((x) => x !== k) : [...types, k])}>
                <i style={{ width: 8, height: 8, borderRadius: "50%", background: col(k) }} />{label(k)} <span className="m">{count((n) => n.kind === k)}</span>
              </button>
            ))}
          </div>

          {view === "map" && fn && (
            <Card className="pad" label="Focus map">
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
                <span className="cz" style={{ fontSize: 11, color: "rgb(var(--mut))" }}>Path</span>
                {crumbs.map((id, k) => (<span key={`${id}${k}`} style={{ display: "inline-flex", gap: 8, alignItems: "center" }}><span style={{ color: GREY }}>›</span><button type="button" className="gl-tf" aria-pressed={k === crumbs.length - 1} style={{ padding: "0 10px", minHeight: 30 }} onClick={() => { setFocus(id); setTrail(crumbs.slice(0, k + 1)); }}>{cut(g.byId.get(id)?.label ?? id, 26)}</button></span>))}
                <span style={{ flex: 1 }} />
                <span style={{ fontSize: 12, color: GREY }}>{hits.length ? "Jump to:" : "Start from:"}</span>
                {(hits.length ? hits : starts).map((n) => (<button key={n.id} type="button" className="gl-tf" style={{ padding: "0 10px", minHeight: 30, borderColor: `${col(n.kind)}66` }} onClick={() => go(n.id)}>{cut(n.label, 24)}</button>))}
              </div>
              <div style={{ overflowX: "auto" }}>
                <div className="gl-orbit" key={f0}>
                  <svg viewBox="0 0 1400 760" style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }} aria-hidden="true">
                    <ellipse className="orbit" cx={CX} cy={CY} rx={R1 * FX} ry={R1} fill="none" stroke="rgb(86 204 242)" strokeOpacity=".25" />
                    <ellipse className="orbit r" cx={CX} cy={CY} rx={R2 * FX} ry={R2} fill="none" stroke="#a78bfa" strokeOpacity=".2" />
                    <path d={paths.far || "M0 0"} fill="none" stroke="#9fb3c8" strokeOpacity=".12" strokeWidth=".8" />
                    {(["link", "bad", "flow"] as const).map((k) => <path key={k} d={paths[k] || "M0 0"} fill="none" stroke={RCOL[k]} strokeOpacity=".55" strokeWidth="1.3" className="flowd" />)}
                    {(["der", "sup"] as const).map((k) => <path key={k} d={paths[k] || "M0 0"} fill="none" stroke={RCOL[k]} strokeOpacity=".85" strokeWidth="1.5" />)}
                  </svg>
                  {far.map((f) => { const n = g.byId.get(f.z)!; return <button key={f.z} type="button" className="gl-gd" title={n.label} aria-label={n.label} style={{ left: `${(f.x / 1400) * 100}%`, top: `${(f.y / 760) * 100}%`, background: col(n.kind) }} onClick={() => go(f.z)} />; })}
                  <div className="gl-fctr"><span className="o" style={{ background: `radial-gradient(circle, ${col(fn.kind)}55, transparent 70%)`, borderColor: col(fn.kind) }}><i style={{ background: col(fn.kind), boxShadow: `0 0 24px ${col(fn.kind)}` }} /></span><span className="cg" style={{ fontSize: 20, color: "rgb(var(--hi))", textAlign: "center", maxWidth: 220 }}>{cut(fn.label, 40)}</span></div>
                  {ring.map((r) => { const n = g.byId.get(r.j)!; const s = Math.round(12 + Math.sqrt(n.deg) * 2.4); return (
                    <button key={r.j} type="button" className="gl-fn" aria-label={`${n.label} · ${label(n.kind)} · ${KL[r.rel] ?? r.rel}`} title={`${n.label} · ${label(n.kind)} · ${KL[r.rel] ?? r.rel}`} style={{ left: `${(r.x / 1400) * 100}%`, top: `${(r.y / 760) * 100}%` }} onClick={() => go(r.j)}>
                      <span className="d" style={{ width: s, height: s, background: col(n.kind), boxShadow: `0 0 ${s}px ${col(n.kind)}99` }} /><span className="t">{cut(n.label, nbo.length > 30 ? 17 : 24)}</span>
                    </button>); })}
                  {ring.length === 0 && <p className="gl-empty" style={{ position: "absolute", left: "50%", top: "62%", transform: "translateX(-50%)" }}>No connected notes{types.length ? " of the selected types" : ""}.</p>}
                </div>
              </div>
              <FocusStrip id={fn.id} node={fn} groups={groups} total={nbs.length} onOpen={() => setSel(fn.id)} />
              <p className="gl-note">Relations are as written in the notes — not verified evidence or causality. {g.resolved} of {g.resolved + Number(q.data.unresolved_edges ?? 0)} links resolve to a note{q.data.truncated ? " (the vault read is truncated)" : ""}.</p>
            </Card>
          )}

          {view === "list" && (
            <Card className="pad" label="Notes">
              <DataTable label="Knowledge notes" rows={listRows} cols={cols} rowId={(n) => n.id} onOpen={(n) => setSel(n.id)} defaultSort={{ key: "deg", dir: -1 }} empty="The vault holds no notes." />
            </Card>
          )}
        </>
      )}
      <NotePanel id={sel} g={g} onClose={() => setSel(null)} onWalk={(id) => setSel(id)} onExplore={(id) => { setView("map"); go(id); setSel(null); }} />
    </>
  );
}
const GREY = "#8d9fb2";

function FocusStrip({ id, node, groups, total, onOpen }: { id: string; node: Node; groups: [string, number][]; total: number; onOpen: () => void }) {
  const nq = useRead(`knowledge/note?id=${encodeURIComponent(id)}`);
  const claim = claimOf(nq.data?.body);
  const fm = (rec(nq.data?.frontmatter) ?? {}) as Record<string, unknown>;
  const tags = [node.status && `state: ${node.status}`, fm.confidence && `confidence: ${fm.confidence}`].filter(Boolean) as string[];
  return (
    <div className="gl-cap">
      <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0, flex: "2 1 420px" }}>
        <span style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
          <span style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".1em", color: col(node.kind) }}>{label(node.kind)}</span>
          <span className="cg" style={{ fontSize: 22, color: "rgb(var(--hi))" }}>{node.label}</span>
          {tags.map((t) => (<span key={t} className="gl-tag">{t}</span>))}
        </span>
        <span style={{ fontSize: 14, color: "#b9c5d1", lineHeight: 1.5 }}>{nq.isPending ? "Reading the note…" : (claim ?? "No summary text in this note.")}</span>
        <span className="m" style={{ fontSize: 12, color: GREY }}>knowledge/{node.id}</span>
        <button type="button" className="gl-btn" style={{ alignSelf: "flex-start" }} onClick={onOpen}>Open note details</button>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, flex: "1 1 260px" }}>
        <span className="cz" style={{ fontSize: 11, color: "rgb(var(--mut))" }}>Connections · {total}</span>
        {groups.map(([k, n]) => (<span key={k} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13 }}><i style={{ width: 8, height: 8, borderRadius: "50%", background: col(k) }} />{GLAB[k] ?? label(k)}<span style={{ flex: 1 }} /><b className="m">{n}</b></span>))}
        <span style={{ fontSize: 12, color: GREY }}>Click any connected note to walk there.</span>
      </div>
    </div>
  );
}

function NotePanel({ id, g, onClose, onWalk, onExplore }: { id: string | null; g: { byId: Map<string, Node>; adj: Map<string, [string, string][]> }; onClose: () => void; onWalk: (id: string) => void; onExplore: (id: string) => void }) {
  const nq = useRead(id ? `knowledge/note?id=${encodeURIComponent(id)}` : null);
  if (!id || !g.byId.has(id)) return <DetailPanel open={false} onClose={onClose} title="" children={null} />;
  const n = g.byId.get(id)!;
  const seen = new Set<string>();
  const nb = (g.adj.get(id) ?? []).filter(([j]) => (seen.has(j) ? false : seen.add(j)));
  const claim = claimOf(nq.data?.body);
  const fm = (rec(nq.data?.frontmatter) ?? {}) as Record<string, unknown>;
  return (
    <DetailPanel open onClose={onClose} title={n.label} description={`${label(n.kind)} · knowledge/${n.id}`}>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        {[n.status && `state: ${n.status}`, fm.confidence && `confidence: ${fm.confidence}`, fm.family && `family: ${fm.family}`, `${n.deg} links`].filter(Boolean).map((t) => (<span key={String(t)} className="gl-tag">{String(t)}</span>))}
      </div>
      <p style={{ margin: 0, fontSize: 14, lineHeight: 1.55, color: "#c9d4df" }}>{nq.isPending ? "Reading the note…" : nq.isError ? `Note unavailable: ${(nq.error as Error).message}` : (claim ?? "No summary text in this note.")}</p>
      <KV items={[["Last modified", n.observed_at ? utc(n.observed_at) : null], ["Folder", n.folder || "(root)"], ["Bytes", nq.data?.bytes != null ? String(nq.data.bytes) : null]]} />
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span className="cz" style={{ fontSize: 11, color: "rgb(var(--mut))" }}>Connected notes · {nb.length}</span><span style={{ flex: 1 }} />
        <button type="button" className="gl-btn primary" onClick={() => onExplore(id)}>Explore in Map →</button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: 8 }}>
        {nb.map(([j, rel]) => { const m = g.byId.get(j)!; return (
          <button key={j} type="button" className="rcard" onClick={() => onWalk(j)}>
            <i style={{ width: 10, height: 10, flex: "0 0 10px", borderRadius: "50%", background: col(m.kind), boxShadow: `0 0 8px ${col(m.kind)}` }} />
            <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}><span style={{ fontSize: 14, fontWeight: 600, overflowWrap: "anywhere" }}>{m.label}</span><span style={{ fontSize: 12, color: "var(--txt2)" }}>{label(m.kind)} · {KL[rel] ?? rel}</span></span>
          </button>); })}
      </div>
      {nb.length === 0 && <p className="gl-note">No resolved links to other notes.</p>}
    </DetailPanel>
  );
}
