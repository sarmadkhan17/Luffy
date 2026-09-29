/** Shared record primitives for the LIVE read contracts: one bounded query per
 * owner-api path, recorded-field lists and tables, UNAVAILABLE reasons, and
 * links that navigate only to ids a backend record returned. */
import { type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { usePreview } from "../context";
import { Badge, QueryState } from "./ui";
import { timestamp, value } from "./workspace";
import type { OwnerRecord } from "../adapters/contracts";
import { recordHref, refKind, type RecordKind } from "../links";

export type R = Record<string, unknown>;
export const rec = (v: unknown): R | null =>
  typeof v === "object" && v !== null && !Array.isArray(v) ? (v as R) : null;
export const rows = (v: unknown): R[] =>
  Array.isArray(v) ? (v.filter((x) => rec(x)) as R[]) : [];
export const short = (v: unknown) =>
  typeof v === "string" && v.length > 16 ? `${v.slice(0, 12)}…` : value(v);
export const bytes = (v: unknown) =>
  typeof v === "number"
    ? v >= 1e9
      ? `${(v / 1e9).toFixed(2)} GB`
      : v >= 1e6
        ? `${(v / 1e6).toFixed(1)} MB`
        : `${(v / 1e3).toFixed(1)} kB`
    : "Unavailable";

export function useRead(path: string | null, poll?: number) {
  const { adapter } = usePreview();
  return useQuery({
    queryKey: ["owner-read", path],
    queryFn: ({ signal }) => adapter.ownerRead!(path!, signal),
    enabled: !!adapter.ownerRead && !!path,
    refetchInterval: poll,
  });
}

export function Loaded({
  q,
  children,
}: {
  q: ReturnType<typeof useRead>;
  children: (d: OwnerRecord) => ReactNode;
}) {
  return (
    <>
      <QueryState
        error={q.error}
        loading={q.isPending}
        retry={() => void q.refetch()}
      />
      {q.data && !q.error && children(q.data)}
    </>
  );
}

const label = (field: string) => {
  const t = field.replaceAll("_", " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
};
export function UnavailableFields({ items }: { items: unknown }) {
  const list = rows(items);
  if (!list.length) return null;
  return (
    <ul className="unavailable-list" data-testid="unavailable-fields">
      {list.map((u) => (
        <li key={String(u.field)}>
          <Badge tone="amber">UNAVAILABLE</Badge>{" "}
          <strong>{label(String(u.field))}</strong> — {String(u.reason)}
        </li>
      ))}
    </ul>
  );
}

const TIME_KEY = /(_at$|^ts$|^at$|^started$|^finished$)/;

export function Fields({
  row,
  keys,
}: {
  row: R | null;
  keys: [string, string, ((v: unknown, r: R) => ReactNode)?][];
}) {
  if (!row) return <p className="quiet">Not recorded.</p>;
  return (
    <dl className="record-fields">
      {keys.map(([k, label, fmt]) => (
        <div key={k + label}>
          <dt>{label}</dt>
          <dd>
            {fmt
              ? fmt(row[k], row)
              : TIME_KEY.test(k)
                ? timestamp(row[k])
                : value(row[k])}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function SmallTable({
  data,
  columns,
  caption,
  empty = "No records returned.",
}: {
  data: R[];
  columns: [string, string, ((v: unknown, r: R) => ReactNode)?][];
  caption: string;
  empty?: string;
}) {
  if (!data.length) return <p className="quiet">{empty}</p>;
  return (
    <div
      className="table-scroll"
      tabIndex={0}
      role="region"
      aria-label={caption}
    >
      <table>
        <caption>{caption}</caption>
        <thead>
          <tr>
            {columns.map(([, h], c) => (
              <th key={c}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((r, i) => (
            <tr key={i}>
              {columns.map(([k, h, fmt], c) => (
                <td key={c} data-label={h}>
                  {fmt
                    ? fmt(r[k], r)
                    : /(_at$|^ts$|^at$)/.test(k)
                      ? timestamp(r[k])
                      : value(r[k])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** A link to a stored record by its recorded id. */
export function RecordLink({
  kind,
  id,
  children,
}: {
  kind: RecordKind;
  id: string;
  children?: ReactNode;
}) {
  return (
    <a
      className="record-link"
      href={recordHref(kind, id)}
      data-record={`${kind}:${id}`}
    >
      {children ?? id}
    </a>
  );
}

/** A backend `{kind, id}` reference: a link when the record is navigable,
 * otherwise the recorded id and why it cannot be opened. */
export function RefLink({ r, label }: { r: unknown; label?: ReactNode }) {
  const x = rec(r);
  if (!x || typeof x.id !== "string")
    return <span className="quiet">Not recorded</span>;
  const kind = refKind(x.kind);
  if (!kind)
    return (
      <span className="quiet" data-testid="ref-not-openable">
        {String(x.kind)} {short(x.id)} · recorded id; not openable here
      </span>
    );
  return (
    <RecordLink kind={kind} id={x.id}>
      {label ?? x.id}
    </RecordLink>
  );
}

/** A strategy id as recorded: a link only when the backend confirmed it is in
 * the registry. */
export function StrategyRef({ s }: { s: unknown }) {
  const x = rec(s);
  if (!x || typeof x.id !== "string") return <span>Unavailable</span>;
  return x.in_registry ? (
    <RecordLink kind="strategy" id={x.id}>
      {typeof x.name === "string" ? `${x.name} (${x.id})` : x.id}
    </RecordLink>
  ) : (
    <span title="Recorded id not present in the strategy registry">
      {x.id} · not in registry
    </span>
  );
}
