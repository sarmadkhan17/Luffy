import { useEffect, useState } from "react";
import { usePreview } from "../context";
import { useRead, Loaded, rows, rec, UnavailableFields } from "./records";
import { value } from "./workspace";
import * as pending from "../adapters/pending";

/** One owner-action surface: concise Overview, exact discussion in LUFFY. */
export function NeedsYou({compact = false}: {compact?: boolean}) {
  const {adapter} = usePreview();
  const [offset,setOffset] = useState(0);
  const q = useRead(adapter.mode === "LIVE" ? `needs-you${offset ? `?offset=${offset}` : ""}` : null, 30000);
  const [result, setResult] = useState("");
  const [busy, setBusy] = useState(false);
  const [now,setNow] = useState(Date.now);
  useEffect(() => {const timer=setInterval(()=>setNow(Date.now()),1000); return ()=>clearInterval(timer);},[]);
  if (adapter.mode !== "LIVE") return null;
  async function decide(item: Record<string, unknown>, decision: "APPROVED" | "REJECTED") {
    if (!adapter.approvalDecision || busy) return;
    const action = `approval:${item.item_id}:${item.binding_hash}:${decision}`;
    const req = pending.find(action) ?? pending.create(action).entry;
    setBusy(true);
    try {
      const r = await adapter.approvalDecision({item_id: String(item.item_id), binding_hash: String(item.binding_hash), decision}, req);
      if (pending.DEFINITIVE.includes(String(r.status))) pending.done(req.id);
      setResult(`${r.status}: ${value(r.reasons)} · request ${req.id}`);
      await q.refetch();
    } catch (e) {
      setResult(`${e instanceof Error ? e.message : "UNAVAILABLE"}. Retry retains request ${req.id}.`);
    } finally {setBusy(false);}
  }
  return <section aria-label="Needs You" data-testid="needs-you-items">
    <Loaded q={q}>{d => <>
      <p className="quiet">{compact ? "Pending exact owner decisions" : "Needs You · owner decisions; approval does not execute, activate or spend."}</p>
      <p className="quiet">Validity last checked {value(d.generated_at)}. Submission revalidates exact context.</p>
      <UnavailableFields items={d.unavailable} />
      {typeof d.pending_total === "number" && <p>{d.pending_total} pending or stale owner items across available ledgers.</p>}
      {rows(d.items).filter(i => !compact || !i.receipt || i.validity !== "VALID").map(i => {
        const expired=typeof i.valid_until_ms === "number" && now >= i.valid_until_ms;
        return <article key={String(i.item_id)}>
        <strong>{value(i.action_type)} · {expired ? "STALE — expired" : value(i.validity)}</strong><p>{value(i.reason)}</p>
        {compact ? <a href={`#luffy?approval=${encodeURIComponent(String(i.item_id))}`}>Discuss evidence in LUFFY ↗</a> : <>
          <details><summary>Exact object, evidence and validity · {value(i.affected_object)}</summary><pre style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{JSON.stringify(i,null,2)}</pre></details>
          {i.decision_operation === "resume" ? <a href="#operations">Guarded recovery in Operations ↗</a> : <>
            <button disabled={busy || expired || i.validity !== "VALID" || !!i.receipt || !adapter.approvalDecision || q.isError} onClick={() => decide(i,"APPROVED")}>Approve exact request</button>{" "}
            <button disabled={busy || expired || i.validity !== "VALID" || !!i.receipt || !adapter.approvalDecision || q.isError} onClick={() => decide(i,"REJECTED")}>Reject exact request</button>
          </>}
        </>}
      </article>;})}
      {rows(d.items).length === 0 && <p>No items returned from available ledgers. Unavailable ledgers do not establish no pending action.</p>}
      {compact && d.truncated === true && <a href="#luffy">Browse all items in LUFFY ↗</a>}
      {!compact && <div aria-label="Needs You paging">
        <button disabled={offset === 0 || q.isFetching} onClick={()=>setOffset(Math.max(0,offset-100))}>Previous owner items</button>{" "}
        <button disabled={d.truncated !== true || q.isFetching} onClick={()=>setOffset(offset+100)}>More owner items</button>
      </div>}
    </>}</Loaded>
    {result && <p role="status">{result}</p>}
  </section>;
}

const kinds = ["decision","trade","research","portfolio","world","strategies","cost","approvals","learning","capability"];
export function OwnerEvidence() {
  const {adapter} = usePreview();
  const initial = new URLSearchParams(location.hash.split("?")[1]);
  const [kind,setKind] = useState(initial.get("query") ?? "decision");
  const [identity,setIdentity] = useState(initial.get("id") ?? "");
  const [path,setPath] = useState(`query/${kinds.includes(kind) ? kind : "decision"}${identity ? `?identity=${encodeURIComponent(identity)}` : ""}`);
  const q = useRead(adapter.mode === "LIVE" ? path : null);
  if (adapter.mode !== "LIVE") return null;
  return <details className="side-note" data-testid="owner-evidence-query"><summary>Interrogate exact internal evidence</summary>
    <form onSubmit={e => {e.preventDefault(); setPath(`query/${kind}${identity ? `?identity=${encodeURIComponent(identity)}` : ""}`);}}>
      <label>Evidence class <select value={kind} onChange={e => setKind(e.target.value)}>{kinds.map(k => <option key={k}>{k}</option>)}</select></label>{" "}
      <label>Exact stored identity <input value={identity} maxLength={128} onChange={e => setIdentity(e.target.value)} placeholder="Blank for bounded catalog" /></label>
      <button>Read evidence</button>
    </form>
    <Loaded q={q}>{d => <>
      <p>{value(d.status)} · historical records; journal is not venue. Query config is not historical decision config.</p>
      <UnavailableFields items={d.unavailable} />
      {rows(d.records).map((r,i) => <details key={`${r.record_id}:${i}`}><summary>{value(r.record_id)} · {value(r.verification)}</summary>
        <pre style={{whiteSpace:"pre-wrap",overflowWrap:"anywhere"}}>{JSON.stringify(r,null,2)}</pre></details>)}
      {d.questions_page && typeof d.questions_page === "object" && !Array.isArray(d.questions_page) && !identity && <div aria-label="Research question paging">
        <button disabled={q.isFetching || rec(d.questions_page)?.offset === 0} onClick={()=>setPath(`query/${kind}?offset=${Math.max(0,Number(rec(d.questions_page)?.offset)-50)}`)}>Previous questions</button>{" "}
        <button disabled={q.isFetching || rec(d.questions_page)?.has_more !== true} onClick={()=>setPath(`query/${kind}?offset=${Number(rec(d.questions_page)?.next_offset)}`)}>More questions</button>
      </div>}
      {d.truncated === true && <p>PARTIAL: bounded catalog. Request an exact stored identity for detail.</p>}
    </>}</Loaded>
  </details>;
}
