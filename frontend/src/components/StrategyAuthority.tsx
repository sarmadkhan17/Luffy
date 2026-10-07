import { useState } from "react";
import { Layers } from "lucide-react";
import { Card, CardHead, DataTable, DetailPanel, KV, type Col } from "./glass";
import { rec, rows, type R } from "./records";

const value = (v: unknown): string => v == null ? "UNAVAILABLE" : typeof v === "object" ? JSON.stringify(v) : String(v);
const cols: Col<R>[] = [
  { key: "strategy", label: "Strategy", primary: true, filter: "text", value: r => value(r.strategy_id), cell: r => <span>{value(rec(rec(r.version)?.spec)?.name)}<br /><small>{value(r.strategy_id)}</small></span> },
  { key: "version", label: "Exact version", filter: "text", value: r => value(r.version_id), cell: r => <span style={{ overflowWrap: "anywhere", whiteSpace: "normal" }}>{value(r.version_id)}</span> },
  { key: "validation", label: "Validation", value: r => r.validation ? "Verified" : "UNAVAILABLE" },
  { key: "install", label: "Install", value: r => rec(r.install)?.mode ? `Verified ${rec(r.install)?.mode} install` : "UNAVAILABLE" },
  { key: "probation", label: "Probation", value: r => value(rec(r.probation)?.status) },
  { key: "approval", label: "Owner approval", value: r => r.approval ? `${value(rec(r.approval)?.status)} · ${value(rec(r.approval)?.validity)}` : "NOT REPORTED" },
  { key: "governor", label: "Governor lifecycle", value: r => value(rec(r.governor)?.to_state) },
];
export function StrategyAuthority({ versions, errors }: { versions: unknown; errors: unknown }) {
  const [selected, setSelected] = useState<R | null>(null);
  const v = rec(selected?.version), spec = rec(v?.spec), approval = rec(selected?.approval), governor = rec(selected?.governor);
  return <>
    <Card className="pad" label="Versioned strategy authority">
      <CardHead icon={<Layers size={16} />} title="Versioned strategy authority" sub="Strategy → StrategyVersion → validation → install → probation → owner approval → Strategy Governor" />
      {Array.isArray(versions) ? <DataTable label="Strategy versions" rows={rows(versions)} cols={cols} rowId={r => String(r.version_id)} onOpen={setSelected} empty="No StrategyVersions recorded." /> : <p className="gl-empty">Versioned authority UNAVAILABLE.</p>}
      {!!errors && Object.keys(errors).length > 0 && <p className="gl-note">Authority read: {value(errors)}</p>}
      <p className="gl-note">Exact-version authority is separate from journal performance. Registry labels and ACTIVE lifecycle alone never authorize an order. Risk has final entry authority; execution permission fails closed.</p>
    </Card>
    <DetailPanel open={!!selected} onClose={() => setSelected(null)} title="Exact StrategyVersion" description={value(selected?.version_id)}>
      {selected && <>
        <section aria-label="Authority and lifecycle" className="gl-authority">
          <h3>Authority / lifecycle · exact version</h3>
          <ol className="gl-lifecycle">
            {[
              ["StrategyVersion", value(selected.version_id), "Immutable identity; bound spec and lineage hashes"],
              ["Validation", selected.validation ? "Verified against current research evidence" : "UNAVAILABLE", value(rec(selected.validation)?.receipt_id)],
              ["Install", selected.install ? "Verified exact current install" : "UNAVAILABLE", value(rec(selected.install)?.install_id)],
              ["Probation", value(rec(selected.probation)?.status), value(rec(selected.probation)?.receipt_id)],
              ["Owner approval", approval ? `${value(approval.status)} · ${value(approval.validity)}` : "NOT REPORTED", value(approval?.affected_object)],
              ["Governor", value(governor?.to_state), value(governor?.reason_code)],
              ["Execution eligibility", "NOT ESTABLISHED BY THIS VIEW", "Risk retains final entry authority; permission fails closed"],
            ].map(([label, status, evidence]) => <li key={label} data-stale={status.includes("STALE") || undefined}>
              <span className="gl-lifecycle-mark" aria-hidden="true" />
              <div><b>{label}</b><span>{status}</span><small>{evidence}</small></div>
            </li>)}
          </ol>
        </section>
        <h3 className="gl-section-title">Identity / source evidence</h3>
        <KV items={[
          ["Strategy id", value(selected.strategy_id)], ["Strategy name / type", `${value(spec?.name)} / ${value(spec?.type ?? spec?.kind)}`],
          ["Version id", value(selected.version_id)], ["Spec hash", value(v?.spec_hash)], ["Lineage hash", value(v?.lineage_sha256)],
          ["Parent version", v?.parent_version_id === null ? "No parent recorded (root version)" : value(v?.parent_version_id)],
          ["Created", typeof selected.created_at_ms === "number" ? new Date(selected.created_at_ms).toISOString() : "UNAVAILABLE"], ["Origin", value(v?.source)],
          ["Validation status", selected.validation ? "Verified against current research evidence" : "UNAVAILABLE"],
          ["Validation receipt", value(rec(selected.validation)?.receipt_id)], ["QNT / research lineage", value(rec(v?.lineage)?.quantitative ?? v?.evidence_ids)],
          ["Install status", selected.install ? "Verified exact current install" : "UNAVAILABLE"], ["Install identity / mode", `${value(rec(selected.install)?.install_id)} / ${value(rec(selected.install)?.mode)}`],
          ["Probation result", value(rec(selected.probation)?.status)], ["Probation receipt", value(rec(selected.probation)?.receipt_id)],
          ["Approval request", value(approval?.item_id)], ["Owner decision", value(rec(approval?.receipt)?.decision)],
          ["Approval freshness / validity", value(approval?.validity)], ["Exact-version scope", value(approval?.affected_object)], ["Approval stale reasons", value(approval?.invalid_reasons)],
          ["Version lifecycle projection", value(selected.lifecycle)], ["Governor lifecycle", value(governor?.to_state)],
          ["Latest transition", value(governor?.transition_id)], ["Transition reason", value(governor?.reason_code)],
          ["Transition time", value(governor?.at_ms)], ["Transition evidence", value(governor?.evidence ?? governor?.binding)],

        ]} />
        <section className="gl-economics" aria-label="Performance and economics">
          <h3>Performance / economics · separate from authority</h3>
          <KV items={[
            ["Economics", selected.economics ? value(rec(selected.economics)?.current) : "UNAVAILABLE — no current exact economics projection reported"],
            ["Recorded economics / capacity evidence (historical)", value(selected.economics)],
          ]} />
        </section>
        <p className="gl-note">Recorded Governor transition evidence: {value(governor)}</p>
        <p className="gl-note">Unavailable reads: {value(selected.errors)}. These reads grant no activation or order permission.</p>
      </>}
    </DetailPanel>
  </>;
}
