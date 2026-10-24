import { useEffect, useState } from "react";
import { usePreview } from "../context";
import { Loaded, rec, rows, useRead } from "./records";
import { value } from "./workspace";

const text = (v: unknown) => value(v).replaceAll("_", " ");
function Evidence({ title, data }: { title: string; data: unknown }) {
  if (data == null) return <p>{title}: not recorded</p>;
  return <details><summary>{title}</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(data, null, 2)}</pre></details>;
}

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
  return <details onToggle={e => setOpen((e.target as HTMLDetailsElement).open)} data-testid={`candidate-detail-${id}`}>
    <summary>Open the exact stored formula, fitted models, thresholds, hedge and lineage</summary>
    <Loaded q={catalog}>{d => {
      if (listing === null) return <p role="status">Result catalogue unavailable. {JSON.stringify(d.unavailable)}</p>;
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
    <label>Section <select value={section} onChange={e => { setSection(e.target.value); setPart(0); }}>{SECTIONS.map(x => <option key={x}>{x}</option>)}</select></label>
    {runId !== null && <Loaded q={q}>{d => {
      const r = rec(rows(d.records)[0]?.value);
      if (!r || r.status !== "AVAILABLE") return <p role="status">Detail unavailable: {text(r?.status)}. {JSON.stringify(d.unavailable)}</p>;
      const parts = Number(r.parts ?? 1);
      return <>
        <p className="quiet">{text(r.section)} · part {part + 1} of {parts} · {value(r.chars ?? r.bytes)} characters · sha256 {String(r.sha256).slice(0, 12)}</p>
        <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{String(r.text ?? "")}</pre>
        <button disabled={part === 0} onClick={() => setPart(part - 1)}>Previous part</button>{" "}
        <button disabled={part + 1 >= parts} onClick={() => setPart(part + 1)}>Next part</button>
      </>;
    }}</Loaded>}
  </details>;
}

/** Read-only study progress. A development result never implies qualification or activation. */
export function ResearchStudies() {
  const { adapter } = usePreview();
  const q = useRead(adapter.mode === "LIVE" ? "query/research?identity=closure-studies" : null, 120000);
  if (adapter.mode !== "LIVE") return null;
  return <section className="gl-card pad" aria-label="Research studies" data-testid="research-studies">
    <h2>Research studies</h2>
    <p className="quiet">Questions, experiments and their measured results. Development support, independent qualification and trading activation are separate stages.</p>
    <Loaded q={q}>{d => {
      const record = rows(d.records).find(r => r.kind === "closure_research");
      const body = rec(record?.value);
      const studies = rows(body?.studies);
      if (!body || body.status === "UNAVAILABLE") return <p role="status">Research study evidence unavailable. {JSON.stringify(d.unavailable)}</p>;
      return <>
        {studies.length === 0 && <p>No study has been recorded in the configured research store.</p>}
        {studies.map((s, i) => <details key={String(s.study_id)} open={i === 0}>
          <summary>{text(s.status)} · {text(s.stop_reason)} · study {String(s.study_id).slice(0, 12)}</summary>
          <p>Data mode: {text(s.development_mode)}. Next work: {text(s.next_work)}.</p>
          <Evidence title="Costs and sizing assumptions" data={s.cost_assumptions} />
          <table><caption>Stage timings</caption><thead><tr><th>Stage</th><th>State</th><th>Seconds</th><th>Reason</th></tr></thead>
            <tbody>{rows(s.units).map(u => <tr key={String(u.unit)}><td>{text(u.kind)}</td><td>{text(u.status)}</td><td>{u.elapsed_s == null ? "not measured" : value(u.elapsed_s)}</td><td>{text(u.reason)}</td></tr>)}</tbody>
          </table>
          {rows(s.candidates).map(c => <article key={String(c.result_id)}>
            <h3>{text(c.question)}</h3><p><strong>{text(c.outcome)}</strong> · {text(c.reason)}</p>
            <p>Position structure: {text(c.structure)}. Required inputs: {text(c.requires)}.</p>
            <Evidence title="Measured results" data={c.metrics} />
            <Evidence title="Formula and factor roles" data={c.graph} />
            <Evidence title="Measured factor roles and stress conditions" data={c.factor_roles} />
            <Evidence title="Fitted models" data={c.models} />
            <Evidence title="Chronological tests" data={c.walk_forward} />
            <Evidence title="Alternatives and component comparisons" data={c.alternatives} />
            <Evidence title="Regimes and transitions" data={c.regimes} />
            <Evidence title="Open-position risk" data={c.mtm} />
            <Evidence title="Portfolio contribution" data={c.contribution} />
            <Evidence title="Data provenance" data={c.input_lineage} />
            <Evidence title="Missing inputs" data={c.context_gaps} />
            <Evidence title="Execution compatibility" data={c.deployment} />
            <Evidence title="Evaluated formula, fit window and thresholds" data={c.evaluated} />
            <Evidence title="Revision lineage" data={c.lineage} />
            <Evidence title="Hedge development result and alignment" data={c.hedge} />
            {c.candidate_id != null && <CandidateDetail id={String(c.candidate_id)} />}
          </article>)}
          {rows(s.qualifications).map(k => <p key={String(k.qualification_id)} data-testid="qualification-packet">
            {k.kind === "hedge_protective"
              ? <><strong>Protective-hedge risk assessment: {text(k.status)}</strong> · statistical admission {text(k.statistical_admission)} (no admission method is registered; this is not statistical qualification)</>
              : <><strong>Independent alpha qualification: {text(k.status)}</strong> · class {text(k.authority_class)}</>}
            {" "}· {text(k.reason)}{Array.isArray(k.gaps) && k.gaps.length ? ` · gaps: ${k.gaps.join(", ")}` : ""}. Research evidence only; trading authority is separate.</p>)}
          <Evidence title="Work used, including retries" data={s.resources} />
        </details>)}
        <p className="quiet">Recent studies are shown within the recorded catalogue limits. Shortened evidence is marked with its retained hash.</p>
        <Evidence title="Recent scheduled work" data={body.schedule_events} />
      </>;
    }}</Loaded>
  </section>;
}
