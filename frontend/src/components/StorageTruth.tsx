import { HardDrive } from "lucide-react";
import { Card, CardHead } from "./glass";
import { rec, rows } from "./records";
import { timestamp } from "../time";
export const storageSize = (n: unknown) => typeof n === "number" && Number.isFinite(n) && n >= 0 ? `${n.toLocaleString("en-US")} bytes (${(n / 1e9).toFixed(3)} GB)` : "NOT REPORTED";
export function StorageTruth({ storage, unavailable = false }: { storage: unknown; unavailable?: boolean }) {
  const s = unavailable ? null : rec(storage);
  const fs = rows(s?.filesystems), files = rows(s?.files);
  const expected = ["Root /", "LUFFY data", "Recovery"];
  return <Card className="pad" label="Storage capacity and growth">
    <CardHead icon={<HardDrive size={16} />} title="Is LUFFY filling the disk again?" sub="Current filesystem capacity and database sizes · growth requires a recorded previous sample" />
    <div style={{ overflowX: "auto" }}><table className="gl-table" aria-label="Filesystem capacity"><thead><tr>{["Filesystem", "Mount / source", "Total", "Used", "Free", "% free", "State"].map(x => <th key={x}>{x}</th>)}</tr></thead><tbody>
      {expected.map(label => { const f = fs.find(x => x.label === label); const pct = typeof f?.percent_free === "number" && Number.isFinite(f.percent_free) ? f.percent_free : null; return <tr key={label}>
        <td>{label}<br /><small>{String(f?.path ?? "path NOT REPORTED")}</small></td><td>{String(f?.mount ?? "NOT REPORTED")} / {String(f?.source ?? "NOT REPORTED")}</td>
        <td>{storageSize(f?.total)}</td><td>{storageSize(f?.used)}</td><td>{storageSize(f?.free)}</td><td>{pct === null ? "NOT REPORTED" : `${pct.toFixed(1)}%`}</td><td>{pct === null ? "NOT REPORTED" : pct < 10 ? "LOW FREE SPACE — critical" : pct < 20 ? "LOW FREE SPACE" : "Capacity observed"}</td>
      </tr>; })}
    </tbody></table></div>
    <p className="gl-note" data-testid="growth-baseline">{s?.growth_baseline ? "Recorded growth baseline available" : "Growth baseline unavailable"}. Abnormal database growth, WAL growth and sudden change: {s?.growth_baseline ? "inspect measured changes" : "UNKNOWN — no recorded comparison"}. File existence does not establish storage health.</p>
    <div style={{ overflowX: "auto" }}><table className="gl-table" aria-label="LUFFY database files"><thead><tr><th>Store</th><th>Current size</th><th>Last modified</th><th>Growth since recorded sample</th></tr></thead><tbody>
      {files.map(f => <tr key={String(f.file)}><td>{String(f.file)}{String(f.file).endsWith("-wal") ? " · WAL" : ""}</td><td>{storageSize(f.bytes)}</td><td>{typeof f.modified_at === "string" ? timestamp(f.modified_at) : "NOT REPORTED"}</td><td>{typeof f.growth_bytes === "number" ? storageSize(f.growth_bytes) : "Growth baseline unavailable"}</td></tr>)}
      {!files.length && <tr><td colSpan={4}>Database sizes NOT REPORTED</td></tr>}
    </tbody></table></div>
  </Card>;
}
