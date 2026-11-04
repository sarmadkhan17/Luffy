import { useState } from "react";
import { rec } from "./records";

const labels: Record<string,string> = {
  median_pf:"Median profit factor", total_pct:"Net return (%)", max_dd_pct:"Maximum drawdown (%)",
  pnl:"Profit / loss", mtm:"Marked-to-market risk", tf:"Timeframe", slippage_atr_frac:"Slippage (ATR fraction)",
  taker_fee_pct:"Taker fee (%)", risk_per_trade_pct:"Risk per trade (%)", funding_rate_8h:"Eight-hour funding rate",
};
export const fieldLabel = (s: string) => labels[s] ?? s.replaceAll("_", " ").replace(/([a-z])([A-Z])/g, "$1 $2").replace(/^./, c => c.toUpperCase());
function FieldValue({name,data,depth}:{name:string;data:unknown;depth:number}) {
  if (typeof data==="string" && ["reason","error","note","stop_reason"].includes(name)) return <span className="rs-value">{researchReason(data)}</span>;
  if (typeof data==="number" && data>100000000000 && /(^|_)(at|start|end|observed|received|cut|updated|available|event_time)_ms$/.test(name)) {
    const date=new Date(data);
    if (Number.isFinite(date.getTime())) return <span className="rs-value" title={String(data)}>{date.toISOString().replace("T"," ").replace("Z"," UTC")}</span>;
  }
  return <ResearchData data={data} depth={depth} />;
}
export function researchReason(input: unknown): string {
  if (typeof input !== "string" || !input.trim()) return "No further reason was recorded.";
  if (input.includes("checkpoint_conflict")) return "Research intake could not verify its journal source binding.";
  if (input.includes("intake_worker_failed")) return "The research intake stage failed. Its recorded results remain available below.";
  if (input.includes("missing trustworthy retained source")) {
    const match = input.match(/derivative:([^:\s"\\]+):([a-z_]+)/);
    return match ? `Trusted historical ${match[2].replaceAll("_", " ")} data is missing for ${match[1]}.` : "Required historical inputs could not be verified.";
  }
  if (input.startsWith("required context absent:")) return "Required historical inputs could not be verified.";
  if (input.includes("NO_FINALIST")) return "No candidate met the development requirements for further evaluation.";
  const scored = input.match(/verdict=(\w+)\s+testable=(\w+)\s+trades=(\d+)/);
  if (scored) return `${fieldLabel(scored[1])} · testable: ${scored[2] === "True" ? "yes" : "no"} · ${scored[3]} recorded trades.`;
  return input.replaceAll("_", " ");
}

function More({ count, shown, onMore }: { count: number; shown: number; onMore: () => void }) {
  return count > shown ? <button className="rs-button" onClick={onMore}>Show more ({count - shown} remaining)</button> : null;
}

/** Bounded readable fields, lists and tables. Nested evidence mounts on demand. */
export function ResearchData({ data, depth = 0 }: { data: unknown; depth?: number }) {
  const [shown, setShown] = useState(20);
  const [expanded, setExpanded] = useState(false);
  if (data == null) return <span className="quiet">Not recorded</span>;
  if (typeof data === "string") {
    if (/^\s*[\[{]/.test(data)) {
      try { return <ResearchData data={JSON.parse(data)} depth={depth} />; }
      catch { return <span className="quiet">This record is incomplete. Load its remaining parts to read the full evidence.</span>; }
    }
    return <span className="rs-value">{data || "Empty recorded value"}</span>;
  }
  if (typeof data === "boolean") return <span>{data ? "Yes" : "No"}</span>;
  if (typeof data === "number") return <span className="rs-number">{Number.isFinite(data) ? String(data) : "Unavailable"}</span>;
  if (depth >= 4) return <div><button className="rs-button" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? "Hide nested fields" : "Open nested fields"}</button>{expanded && <ResearchData data={data} />}</div>;
  if (Array.isArray(data)) {
    if (!data.length) return <span className="quiet">No entries recorded</span>;
    const objects = data.every(x => rec(x));
    const columns = objects ? [...new Set(data.flatMap(x => Object.keys(x).filter(k => x[k] == null || typeof x[k] !== "object")))].slice(0, 6) : [];
    return <div>{objects && columns.length ? <div className="rs-table-wrap"><table className="rs-table"><thead><tr>{columns.map(k => <th key={k}>{fieldLabel(k)}</th>)}<th>Details</th></tr></thead><tbody>{data.slice(0, shown).map((r, i) => {
      const extra = Object.fromEntries(Object.entries(r).filter(([k]) => !columns.includes(k)));
      return <tr key={i}>{columns.map(k => <td key={k}><FieldValue name={k} data={r[k]} depth={depth + 1} /></td>)}<td>{Object.keys(extra).length ? <Evidence title="More fields" data={extra} /> : "—"}</td></tr>;
    })}</tbody></table></div> : <ol className="rs-list">{data.slice(0, shown).map((x, i) => <li key={i}><ResearchData data={x} depth={depth + 1} /></li>)}</ol>}
      <More count={data.length} shown={shown} onMore={() => setShown(shown + 20)} /></div>;
  }
  const object = rec(data);
  if (!object) return <span className="quiet">Unsupported recorded value</span>;
  if (object.truncated === true) return <p className="quiet">The full evidence is larger than this summary. Open “Full result evidence” below to inspect it.</p>;
  const fields = Object.entries(object);
  if (!fields.length) return <span className="quiet">No fields recorded</span>;
  return <div><dl className="rs-fields">{fields.slice(0, shown).map(([k, v]) => <div key={k}><dt>{fieldLabel(k)}</dt><dd><FieldValue name={k} data={v} depth={depth + 1} /></dd></div>)}</dl><More count={fields.length} shown={shown} onMore={() => setShown(shown + 20)} /></div>;
}

export function Evidence({ title, data }: { title: string; data: unknown }) {
  const [open, setOpen] = useState(false);
  if (data == null || (Array.isArray(data) && !data.length) || (rec(data) && !Object.keys(data as object).length)) return null;
  return <details className="rs-evidence" onToggle={e => setOpen(e.currentTarget.open)}><summary>{title}</summary>{open && <div className="rs-evidence-body"><ResearchData data={data} /></div>}</details>;
}
