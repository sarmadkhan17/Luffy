/** M3 product primitives: key figures, lineage strips, truth-state marks and
 * proportional distributions. Presentation only: every value shown is passed
 * in by a view that read it from a backend record. */
import type { ReactNode } from "react";
import { Badge } from "./ui";

/** One key figure with its label and provenance note. */
export function Figure({
  label,
  value,
  note,
  tone,
  small = false,
  testid,
}: {
  label: string;
  value: ReactNode;
  note?: ReactNode;
  tone?: "mint" | "rose" | "amber" | "brass";
  small?: boolean;
  testid?: string;
}) {
  const missing = value === "Unavailable" || value === "UNAVAILABLE";
  return (
    <div className={`figure ${missing ? "is-unavailable" : (tone ?? "")}`}>
      <span className="figure-label">{label}</span>
      <span
        className={`figure-value${small ? " small" : ""}`}
        data-testid={testid}
      >
        {value}
      </span>
      {note && <span className="figure-note">{note}</span>}
    </div>
  );
}

/** Truth states shared by every route. The tone follows the state; the label
 * is always the recorded state itself, never a softened synonym. */
const TRUTH_TONE: Record<string, string> = {
  VERIFIED: "mint",
  RECORDED: "mint",
  OBSERVED: "mint",
  OK: "mint",
  OPEN: "steel",
  DECLARED: "steel",
  NONE_IN_WINDOW: "neutral",
  VALID_EMPTY: "neutral",
  UNAVAILABLE: "amber",
  NOT_RECORDED: "amber",
  STALE: "amber",
  MALFORMED: "rose",
  FAILED: "rose",
};
export function Truth({ state }: { state: string }) {
  const s = state.toUpperCase();
  return <Badge tone={TRUTH_TONE[s] ?? "neutral"}>{s}</Badge>;
}
export const truthClass = (s: unknown) =>
  `st-${String(s ?? "unavailable").toLowerCase()}`;

/** parent → this → children. Relationship lineage, not a process flow. */
export function LineageStrip({
  parentCaption = "Parent",
  parent,
  current,
  childCaption = "Children",
  items,
  testid,
}: {
  parentCaption?: string;
  parent: ReactNode | null;
  current: ReactNode;
  childCaption?: string;
  items: ReactNode[];
  testid?: string;
}) {
  return (
    <div className="lineage-strip" data-testid={testid}>
      <div className="lineage-col">
        <span className="lineage-caption">{parentCaption}</span>
        {parent ?? <div className="lineage-node none">none recorded</div>}
      </div>
      <span className="lineage-link" aria-hidden="true" />
      <div className="lineage-col">
        <span className="lineage-caption">This record</span>
        <div className="lineage-node current">{current}</div>
      </div>
      <span className="lineage-link" aria-hidden="true" />
      <div className="lineage-col">
        <span className="lineage-caption">
          {childCaption} ({items.length})
        </span>
        {items.length ? (
          items
        ) : (
          <div className="lineage-node none">none recorded</div>
        )}
      </div>
    </div>
  );
}

/** Counts as proportional bars, each labelled with its exact number. */
export function Distribution({
  rows,
  label,
}: {
  rows: { key: string; label: string; n: number; tone?: string }[];
  label: string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.n));
  return (
    <div className="dist" role="list" aria-label={label}>
      {rows.map((r) => (
        <div className="dist-row" role="listitem" key={r.key}>
          <span title={r.label}>{r.label}</span>
          <span className="dist-track" aria-hidden="true">
            <span
              className={`dist-fill ${r.tone ?? ""}`}
              style={{ width: `${(r.n / max) * 100}%`, display: "block" }}
            />
          </span>
          <span>{r.n}</span>
        </div>
      ))}
    </div>
  );
}
