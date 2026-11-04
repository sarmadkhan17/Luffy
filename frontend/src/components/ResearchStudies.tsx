import { useEffect, useState } from "react";
import { usePreview } from "../context";
import { Loaded, rec, rows, useRead } from "./records";
import { value } from "./workspace";
import { Evidence, ResearchData, fieldLabel, researchReason } from "./ResearchEvidence";
import "./researchStudies.css";

const text = (v: unknown) => value(v).replaceAll("_", " ");
const SECTIONS = ["evaluated", "graph", "models", "diagnostics", "hedge", "lineage"] as const;

/** Exact stored content of one candidate section, fetched on request through the bounded typed owner query and paged. Read-only. */
function CandidateDetail({ id }: { id: string }) {
  const { adapter } = usePreview();
  const [open, setOpen] = useState(false);
  const [section, setSection] = useState<string>("evaluated");
  const [part, setPart] = useState(0);
  const [page, setPage] = useState(0);
  const [runId, setRunId] = useState<string | null>(null);
  const catalog = useRead(adapter.mode === "LIVE" && open ? `query/research?identity=${encodeURIComponent(`closure-candidate:${id}:results:${page}`)}` : null, 0);
  const catalogValue = rec(rows(catalog.data?.records)[0]?.value);
  let listing: Record<string, unknown> | null = null;
  if (catalogValue?.status === "AVAILABLE" && typeof catalogValue.text === "string") {
    try { listing = rec(JSON.parse(catalogValue.text)); } catch { /* invalid evidence stays unavailable */ }
  }
  const versions = rows(listing?.items);
  const firstRun = versions[0]?.run_id;
  const newest = typeof firstRun === "string" ? firstRun : null;
  useEffect(() => { if (runId === null && newest !== null) setRunId(newest); }, [newest, runId]);
  const q = useRead(adapter.mode === "LIVE" && open && runId !== null ? `query/research?identity=${encodeURIComponent(`closure-run:${runId}:${section}:${part}`)}` : null, 0);
  const detail = rec(rows(q.data?.records)[0]?.value);
  const [chunks, setChunks] = useState<Record<string, Record<number, string>>>({});
  const chunkKey = `${runId}:${section}:${detail?.sha256}:${detail?.parts}`;
  useEffect(() => {
    if (detail?.status !== "AVAILABLE" || typeof detail.text !== "string" || typeof detail.part !== "number") return;
    const partNumber = detail.part, body = detail.text;
    setChunks(old => old[chunkKey]?.[partNumber] === body ? old : ({ ...old, [chunkKey]: { ...old[chunkKey], [partNumber]: body } }));
  }, [detail, chunkKey]);
  return <details className="rs-evidence" onToggle={e => setOpen(e.currentTarget.open)} data-testid={`candidate-detail-${id}`}>
    <summary>Full result evidence</summary>
    <Loaded q={catalog}>{d => {
      if (listing === null) return <div role="status"><p>Result catalogue unavailable.</p><ResearchData data={d.unavailable} /></div>;
      return <>
        <label>Research result <select value={runId ?? ""} onChange={e => { setRunId(e.target.value); setPart(0); }}>
          {runId !== null && !versions.some(v => v.run_id === runId) && <option value={runId}>Selected result from another page</option>}
          {versions.map((v, i) => <option key={String(v.run_id)} value={String(v.run_id)}>Result {Number(listing?.total ?? 0) - Number(listing?.offset ?? 0) - i} · {text(v.outcome ?? v.status ?? v.summary_status)}</option>)}
        </select></label>
        <button disabled={page === 0} onClick={() => setPage(page - 1)}>Newer results</button>{" "}
        <button disabled={!listing.more_results} onClick={() => setPage(page + 1)}>Earlier results</button>
        {listing.results_may_be_incomplete === true && <p className="quiet">This older catalogue is limited; some past results may be missing.</p>}
        {versions.length === 0 && <p>No developed result is recorded in this catalogue.</p>}
      </>;
    }}</Loaded>
    <label>Evidence section <select value={section} onChange={e => { setSection(e.target.value); setPart(0); }}>{SECTIONS.map(x => <option key={x} value={x}>{fieldLabel(x)}</option>)}</select></label>
    {runId !== null && <Loaded q={q}>{d => {
      const r = rec(rows(d.records)[0]?.value);
      if (!r || r.status !== "AVAILABLE") return <div role="status"><p>Detail unavailable: {text(r?.status)}.</p><ResearchData data={d.unavailable} /></div>;
      const parts = Number(r.parts ?? 1);
      return <>
        <p className="quiet">{text(r.section)} · part {part + 1} of {parts} · {value(r.chars ?? r.bytes)} characters · sha256 {String(r.sha256).slice(0, 12)}</p>
        {(() => {
          const partsLoaded = chunks[chunkKey] ?? {};
          if (parts > 10000 || Object.keys(partsLoaded).length !== parts) return <p className="quiet">Load the remaining parts for a readable view ({Object.keys(partsLoaded).length} of {parts} loaded).</p>;
          const joined = Array.from({length: parts}, (_, i) => partsLoaded[i] ?? "").join("");
          try { return <ResearchData data={JSON.parse(joined)} />; }
          catch { return <p role="status">The stored section could not be decoded. Its parts may be incomplete.</p>; }
        })()}
        <button disabled={part === 0} onClick={() => setPart(part - 1)}>Previous part</button>{" "}
        <button disabled={part + 1 >= parts} onClick={() => setPart(part + 1)}>Next part</button>
      </>;
    }}</Loaded>}
  </details>;
}

const outcomeLabels: Record<string, string> = {
  BLOCKED_DATA: "Needs data", INCONCLUSIVE_POWER: "Insufficient evidence",
  DEVELOPMENT_POSITIVE: "Development support", HEDGE_SUPPORTED: "Hedge support",
  NEGATIVE_ECONOMICS: "Negative after costs", UNTESTED: "Not tested",
};
const outcomeLabel = (v: unknown) => outcomeLabels[String(v)] ?? fieldLabel(String(v ?? "Outcome not recorded"));
const date = (v: unknown) => typeof v === "number" && Number.isFinite(v) ? new Date(v).toLocaleString("en-GB", {timeZone:"UTC",dateStyle:"medium",timeStyle:"short"}) + " UTC" : "Time not recorded";

function Candidate({ candidate: c }: { candidate: Record<string, unknown> }) {
  const groups: [string, unknown][] = [
    ["Measured results",c.metrics],["Formula and factor roles",c.graph],
    ["Measured factor roles and stress conditions",c.factor_roles],["Fitted models",c.models],
    ["Chronological tests",c.walk_forward],["Alternatives and component comparisons",c.alternatives],
    ["Regimes and transitions",c.regimes],["Open-position risk",c.mtm],["Portfolio contribution",c.contribution],
    ["Data provenance",c.input_lineage],["Missing inputs",c.context_gaps],["Execution compatibility",c.deployment],
    ["Evaluated formula, fit window and thresholds",c.evaluated],["Revision lineage",c.lineage],
    ["Hedge development result and alignment",c.hedge],
  ];
  return <article className="rs-result" data-testid="research-result">
    <div className="rs-result-top"><span className={`rs-outcome rs-outcome-${String(c.outcome).toLowerCase()}`}>{outcomeLabel(c.outcome)}</span><span className="quiet">{c.structure ? text(c.structure) : "Structure not recorded"}</span></div>
    <h3>{typeof c.question === "string" ? c.question : "Research question not recorded"}</h3>
    <p className="rs-reason">{researchReason(c.reason)}</p>
    <p className="quiet">Required inputs: {Array.isArray(c.requires) ? c.requires.map(text).join(", ") : "Not recorded"}</p>
    {c.metrics == null && <p className="quiet">No numeric metrics were retained for this result.</p>}
    <div className="rs-evidence-grid">{groups.map(([title,data]) => <Evidence key={title} title={title} data={data} />)}</div>
    {typeof c.candidate_id === "string" && <CandidateDetail id={c.candidate_id} />}
  </article>;
}

/** Stored development results rendered as research, without trading authority. */
export function ResearchStudies() {
  const { adapter } = usePreview();
  const q = useRead(adapter.mode === "LIVE" ? "query/research?identity=closure-studies" : null, 120000);
  const [study, setStudy] = useState("all");
  const [outcome, setOutcome] = useState("all");
  const [activity, setActivity] = useState(false);
  if (adapter.mode !== "LIVE") return null;
  return <section className="gl-card pad rs-workspace" aria-label="Research studies" data-testid="research-studies">
    <div className="rs-heading"><div><h2>Research results</h2><p className="quiet">Questions, experiments and their measured results. Development support, independent qualification and trading activation are separate stages.</p></div></div>
    <Loaded q={q}>{d => {
      const body=rec(rows(d.records).find(r=>r.kind==="closure_research")?.value);
      if (!body || body.status==="UNAVAILABLE") return <div role="status"><p>Research study evidence unavailable.</p><ResearchData data={d.unavailable} /></div>;
      const studies=rows(body.studies);
      const all=studies.flatMap(s=>rows(s.candidates).map(c=>({study:s.study_id,candidate:c})));
      const selected=study==="all"?studies:studies.filter(s=>s.study_id===study);
      const visible=all.filter(x=>(study==="all"||x.study===study)&&(outcome==="all"||x.candidate.outcome===outcome));
      const outcomes=[...new Set(all.map(x=>String(x.candidate.outcome)))];
      const failed=studies.flatMap(s=>rows(s.units).filter(u=>u.status==="FAILED").map(u=>({s,u})));
      return <>
        {failed.length>0 && <aside className="rs-notice"><strong>Recorded research interruption</strong><p>{researchReason(failed[0].u.reason || failed[0].u.outcome)} Previous results remain available.</p><span className="quiet">Recorded {date(failed[0].s.last_progress_ms)} · {text(failed[0].u.kind)}</span></aside>}
        <div className="rs-summary"><div><strong>{all.length}</strong><span>Retained development results</span></div><div><strong>{all.filter(x=>x.candidate.outcome==="BLOCKED_DATA").length}</strong><span>Need historical data</span></div><div><strong>{all.filter(x=>x.candidate.outcome==="INCONCLUSIVE_POWER").length}</strong><span>Insufficient evidence</span></div></div>
        <div className="rs-filters"><label>Study <select value={study} onChange={e=>setStudy(e.target.value)}><option value="all">All retained studies</option>{studies.map((s,i)=><option key={String(s.study_id)} value={String(s.study_id)}>Study {studies.length-i} · {date(s.last_progress_ms)} · {rows(s.candidates).length} results</option>)}</select></label><label>Outcome <select value={outcome} onChange={e=>setOutcome(e.target.value)}><option value="all">All outcomes</option>{outcomes.map(o=><option key={o} value={o}>{outcomeLabel(o)}</option>)}</select></label><span className="quiet">{visible.length} of {all.length} results</span></div>
        {studies.length===0 && <p>No study has been recorded in the configured research store.</p>}
        {visible.length===0 && studies.length>0 && <p className="rs-empty">{all.length===0 ? "No development result has been recorded yet. Study activity below shows the completed and interrupted work." : "No recorded results match these filters."}</p>}
        <div className="rs-results">{visible.map(({candidate:c,study:s},i)=><Candidate key={String(s)+":"+String(c.result_id??c.candidate_id??i)} candidate={c} />)}</div>
        {selected.flatMap(s=>rows(s.qualifications)).map(k=><p key={String(k.qualification_id)} data-testid="qualification-packet">
          {k.kind==="hedge_protective" ? <><strong>Protective-hedge risk assessment: {text(k.status)}</strong> · statistical admission {text(k.statistical_admission)} (no admission method is registered; this is not statistical qualification)</> : <><strong>Independent alpha qualification: {text(k.status)}</strong> · class {text(k.authority_class)}</>}
          {" "}· {researchReason(k.reason)}{Array.isArray(k.gaps)&&k.gaps.length?` · gaps: ${k.gaps.join(", ")}`:""}. Research evidence only; trading authority is separate.
        </p>)}
        <details className="rs-activity" onToggle={e=>setActivity(e.currentTarget.open)}><summary>Study activity · stage timings and recorded stops</summary>{activity && selected.map((s,i)=><section className="rs-study" key={String(s.study_id)}><h3>Study {studies.length-studies.indexOf(s)} · {date(s.last_progress_ms)}</h3><p>{text(s.status)} · {text(s.stop_reason)} · {text(s.development_mode)}</p>{s.next_work!=null&&<p>Next work: {text(s.next_work)}</p>}
          <div className="rs-table-wrap"><table className="rs-table"><caption>Stage timings</caption><thead><tr><th>Stage</th><th>State</th><th>Seconds</th><th>Reason</th></tr></thead><tbody>{rows(s.units).map(u=><tr key={String(u.unit)}><td>{text(u.kind)}</td><td>{text(u.status)}</td><td>{u.elapsed_s==null?"Not measured":value(u.elapsed_s)}</td><td>{u.reason?researchReason(u.reason):u.outcome?text(u.outcome):"No further reason recorded"}</td></tr>)}</tbody></table></div>
          <Evidence title="Costs and sizing assumptions" data={s.cost_assumptions} /><Evidence title="Work used, including retries" data={s.resources} />
        </section>)}</details>
        <Evidence title="Recent scheduled work" data={body.schedule_events} />
        <p className="quiet rs-footnote">{rec(body.catalog_limits) ? `Showing up to ${String(rec(body.catalog_limits)?.studies)} recent studies and ${String(rec(body.catalog_limits)?.candidates_per_study)} results per study. ` : "Recent retained studies are shown within the catalogue limits. "}Missing metrics remain unrecorded; development results do not authorize trading.</p>
      </>;
    }}</Loaded>
  </section>;
}
