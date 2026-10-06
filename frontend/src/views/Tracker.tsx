import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { usePreview } from "../context";
import { Badge, Panel, QueryState } from "../components/ui";
import { parseTracker, TRACKER_STATUSES, type TrackerRow } from "../adapters/tracker";
import "./tracker.css";

const tone = (status: string) => status === "BLOCKED" ? "red" : status === "CLOSED" ? "mint" :
  status === "IN_PROGRESS" ? "blue" : status === "EVIDENCE_TO_MAP" || status === "DEFERRED" ? "neutral" : "amber";
const show = (v: unknown) => Array.isArray(v) ? v.join(", ") || "None" :
  typeof v === "object" && v !== null ? JSON.stringify(v, null, 2) : String(v ?? "Not recorded");

export default function Tracker() {
  const { adapter } = usePreview();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [area, setArea] = useState("all");
  const [gui, setGui] = useState(false);
  const [stage, setStage] = useState("all");
  const [gate, setGate] = useState("all");
  const [sort, setSort] = useState("id");
  const [detail, setDetail] = useState<string | null>(null);
  const q = useQuery({
    queryKey: ["tracker"],
    queryFn: async ({ signal }) => parseTracker(await adapter.ownerRead!("tracker", signal)),
    enabled: !!adapter.ownerRead, retry: false, staleTime: 0,
  });
  // Do not present cached rows as successful when a refresh failed.
  const data = q.error ? undefined : q.data;
  const filtered = useMemo(() => {
    const term = search.trim().toLowerCase();
    const members = data?.readiness_gates.find(g => g.id === gate)?.items;
    return (data?.items ?? []).filter(r =>
      (status === "all" || (status === "SELECTED" ? r.id === data?.selected.id : r.status === status)) &&
      (area === "all" || r.area === area) && (stage === "all" || r.stage === stage) &&
      (!gui || /^(GUI|VIS|GUX)-/.test(r.id)) && (!members || members.includes(r.id)) &&
      (!term || JSON.stringify(r).toLowerCase().includes(term))
    ).sort((a, b) => String(a[sort as keyof TrackerRow]).localeCompare(String(b[sort as keyof TrackerRow])) || a.id.localeCompare(b.id));
  }, [data, search, status, area, gui, stage, gate, sort]);
  if (!adapter.ownerRead) return <Panel title="Tracker unavailable"><p role="status">The canonical Tracker read API is unavailable in this preview. No sample rows were substituted.</p></Panel>;
  const selected = data?.items.find(r => r.id === data.selected.id);
  const focused = data?.items.find(r => r.id === detail);
  return <div className="workspace-stack tracker-view">
    <Panel title="Engineering tracker" aside={<Badge>Read-only</Badge>}>
      <p>SDD defines the target. YAML is the canonical detailed ledger; STATE summarizes evidence and NEXT selects one package. Tracker status grants no runtime, provider or trading authority.</p>
      <p><strong>EVIDENCE_TO_MAP</strong> means exact evidence still needs linking. It does not mean missing or broken implementation. <strong>CLOSED</strong> means only the exact narrow row contract, not product completion.</p>
      <p className="quiet">XLSX is deprecated, non-authoritative history. Changes remain engineering/control-plane actions; there are no Tracker mutation controls.</p>
      <button type="button" disabled={q.isFetching} onClick={() => void q.refetch()}>Refresh tracker</button>
      <QueryState error={q.error} loading={!q.error && !data} retry={() => void q.refetch()} />
      {data && <div className="tracker-source">
        <p>Source: <code>{data.source.path}</code> · {data.source.version}</p>
        <p>Repository revision: <code>{data.source.repository_revision ?? "Unavailable"}</code></p>
        <p>Exact source SHA-256: <code>{data.source.sha256}</code></p>
        <p>Observed runtime revision (separate): <code>{data.source.observed_runtime_revision}</code></p>
        <p>NEXT: <code>{data.source.next_path}</code> · SHA-256 <code>{data.source.next_sha256}</code></p>
      </div>}
    </Panel>
    {data && <>
      <Panel title={data.selected.status === "CLOSED" ? "No active package · terminal history" : "Single selected NEXT"} aside={<Badge tone="blue">{data.selected.status === "CLOSED" ? "HISTORY" : "SELECTED"} · {data.selected.status}</Badge>}>
        <h3>{data.selected.id} · {selected?.required_behavior}</h3>
        <p>{data.selected.mode} · Tracker row remains {selected?.status}</p>
        <p>{data.selected.objective}</p>
        <p>No implementation choice, runtime execution, new observation, threshold change, workload reduction, provider enablement or trading activation follows from this selection.</p>
      </Panel>
      <Panel title="Workflow counts — not completion or readiness">
        <div className="tracker-counts">{[["BLOCKED","Blocked"],["OPEN","Open"],["AWAITING_EVIDENCE","Awaiting evidence"],["AWAITING_OWNER","Awaiting owner"],["CLOSED","Closed — narrow"]].map(([s, label]) => <div key={s}><span>{label}</span><strong>{data.counts[s]}</strong></div>)}</div>
        <details><summary>Status definitions</summary><dl>{data.status_definitions.map(d => <div key={d.status}><dt><Badge tone={tone(d.status)}>{d.status}</Badge></dt><dd>{d.meaning}</dd></div>)}</dl></details>
      </Panel>
      <Panel title="Readiness gates M0–M6">
        <div className="tracker-gates">{data.readiness_gates.map(g => <details key={g.id}><summary>{g.id} · {g.title}</summary><p>{g.current}</p><p><strong>Close when:</strong> {g.closure}</p><p>{g.limit}</p><p>Items: {g.items.join(", ")}</p></details>)}</div>
      </Panel>
      <Panel title="Requirements and evidence">
        <div className="workspace-toolbar tracker-filters">
          <label>Search ID or text<input aria-label="Search tracker" value={search} onChange={e => setSearch(e.target.value)} /></label>
          <label>Status<select aria-label="Tracker status" value={status} onChange={e => setStatus(e.target.value)}><option value="all">All statuses</option><option value="SELECTED">SELECTED (NEXT)</option>{TRACKER_STATUSES.map(s => <option key={s}>{s}</option>)}</select></label>
          <label>Area<select aria-label="Tracker area" value={area} onChange={e => setArea(e.target.value)}><option value="all">All areas</option>{[...new Set(data.items.map(r => r.area))].sort().map(s => <option key={s}>{s}</option>)}</select></label>
          <label>Stage<select aria-label="Tracker stage" value={stage} onChange={e => setStage(e.target.value)}><option value="all">All stages</option>{[...new Set(data.items.map(r => r.stage))].sort().map(s => <option key={s}>{s}</option>)}</select></label>
          <label>Readiness gate<select aria-label="Tracker gate" value={gate} onChange={e => setGate(e.target.value)}><option value="all">All gates</option>{data.readiness_gates.map(g => <option key={g.id} value={g.id}>{g.id} · {g.title}</option>)}</select></label>
          <label>Sort<select aria-label="Sort tracker" value={sort} onChange={e => setSort(e.target.value)}><option value="id">ID</option><option value="status">Status</option><option value="area">Area</option></select></label>
          <label><input type="checkbox" checked={gui} onChange={e => setGui(e.target.checked)} />GUI rows only</label>
        </div>
        <p role="status">{filtered.length} matching rows of {data.items.length} recorded rows</p>
        {filtered.length === 0 ? <p>No rows match these filters. The source loaded successfully.</p> : <div className="tracker-table-scroll"><table aria-label="Tracker requirements"><thead><tr>{["ID","Area","Scope / stage","Status","Required behaviour","Why this exists","Close when","Dependencies","Current evidence / limitations","Next proof / action","Owner role"].map(c => <th key={c} scope="col">{c}</th>)}</tr></thead><tbody>{filtered.map(r => <tr key={r.id}>
          <td><button type="button" onClick={() => setDetail(r.id)} aria-label={`Inspect ${r.id}`}>{r.id}</button>{r.id === data.selected.id && <Badge tone="blue">SELECTED</Badge>}</td>
          <td>{r.area}</td><td>{r.release_scope} / {r.stage}</td><td><Badge tone={tone(r.status)}>{r.status}</Badge></td>
          <td>{r.required_behavior}</td><td>{r.why_needed}</td><td>{r.closure_condition}</td><td>{show(r.dependencies)}{r.parent_ids.length > 0 && <p>Parents: {r.parent_ids.join(", ")}</p>}</td><td>{r.latest_evidence}</td><td>{r.next_proof}</td><td>{r.owner_role}</td>
        </tr>)}</tbody></table></div>}
      </Panel>
      {focused && <Panel title={`Full detail · ${focused.id}`}><button type="button" onClick={() => setDetail(null)}>Dismiss detail</button><dl className="tracker-detail">{Object.entries(focused).map(([key, v]) => <div key={key}><dt>{key}</dt><dd>{show(v)}</dd></div>)}</dl></Panel>}
    </>}
  </div>;
}
