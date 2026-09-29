import { timestamp } from "../time";
import { useEffect, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Search } from "lucide-react";
import { Badge, Panel } from "./ui";

export const value = (v: unknown): string =>
  v === null || v === undefined || v === ""
    ? "Unavailable"
    : typeof v === "object"
      ? JSON.stringify(v)
      : String(v);
export { timestamp } from "../time";
const fieldValue = (key: string, v: unknown) =>
  /(_at$|^ts$)/.test(key) ? timestamp(v) : value(v);
export function Unavailable({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <Panel title={title} aside={<Badge tone="amber">Unavailable</Badge>}>
      <p className="quiet">{children}</p>
    </Panel>
  );
}
export function SourceStrip({
  source,
  at,
  note,
}: {
  source: string;
  at?: string | null;
  note?: string;
}) {
  return (
    <div className="source-strip">
      <Badge>Recorded source</Badge>
      <span>{source}</span>
      <span>Read {timestamp(at)}</span>
      {note && <span>{note}</span>}
    </div>
  );
}
export function Timeline({
  events,
}: {
  events: { label: string; at: unknown; detail?: string }[];
}) {
  return (
    <ol className="record-timeline">
      {events.map((e, i) => (
        <li key={i}>
          <strong>{e.label}</strong>
          <time>{timestamp(e.at)}</time>
          {e.detail && <p>{e.detail}</p>}
        </li>
      ))}
    </ol>
  );
}
export function RecordDrawer({
  row,
  title,
  children,
}: {
  row: Record<string, unknown>;
  title: string;
  children?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  // a record link inside the drawer navigates: close so the target is visible
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("hashchange", close);
    return () => window.removeEventListener("hashchange", close);
  }, [open]);
  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger className="button secondary">
        Inspect {title}
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="evidence-drawer">
          <div className="panel-heading">
            <Dialog.Title>{title}</Dialog.Title>
            <Dialog.Close className="icon-button" aria-label="Close record">
              <X size={20} />
            </Dialog.Close>
          </div>
          <Dialog.Description>
            Recorded fields only. Missing evidence does not establish execution,
            health or approval.
          </Dialog.Description>
          {children}
          <dl className="record-fields">
            {Object.entries(row).map(([k, v]) => (
              <div
                key={k}
                className={
                  fieldValue(k, v).length > 40 ? "wide-field" : undefined
                }
              >
                <dt>
                  {k === "realized_pnl" && row.status === "open"
                    ? "Realized so far (USDT)"
                    : k.replaceAll("_", " ")}
                </dt>
                <dd>{fieldValue(k, v)}</dd>
              </div>
            ))}
          </dl>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function RecordTable({
  rows,
  columns,
  title,
  filterKey,
  detail,
  search: controlledSearch,
  onSearch,
  searchNote,
  open,
}: {
  rows: Record<string, unknown>[];
  columns: [string, string][];
  title: string;
  filterKey?: string;
  detail?: (row: Record<string, unknown>) => ReactNode;
  /** Controlled search (kept by the parent, e.g. across pages). */
  search?: string;
  onSearch?: (search: string) => void;
  /** Placeholder describing what the search covers. */
  searchNote?: string;
  /** A direct link to the row's own record (its recorded id), if any. */
  open?: (row: Record<string, unknown>) => ReactNode;
}) {
  const [localSearch, setLocalSearch] = useState(""),
    [filter, setFilter] = useState("all");
  const search = controlledSearch ?? localSearch;
  const setSearch = onSearch ?? setLocalSearch;
  const options = filterKey
    ? [...new Set(rows.map((r) => value(r[filterKey])))].sort()
    : [];
  const filtered = rows.filter(
    (r) =>
      (!filterKey || filter === "all" || value(r[filterKey]) === filter) &&
      Object.values(r).some((v) =>
        value(v).toLowerCase().includes(search.toLowerCase()),
      ),
  );
  return (
    <>
      <div className="workspace-toolbar">
        <label>
          <Search size={15} />
          <span className="sr-only">Search {title}</span>
          <input
            aria-label={`Search ${title}`}
            placeholder={searchNote ?? `Search ${title.toLowerCase()}…`}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        {filterKey && (
          <label>
            Filter {filterKey.replaceAll("_", " ")}
            <select
              aria-label={`Filter ${title}`}
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            >
              <option value="all">All</option>
              {options.map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          </label>
        )}
        <span className="quiet" role="status">
          {filtered.length} of {rows.length} loaded records
        </span>
      </div>
      {!filtered.length ? (
        <div className="empty">
          {rows.length
            ? "No records match these filters."
            : "No records returned by this source."}
        </div>
      ) : (
        <div className="table-scroll">
          <table>
            <caption>{title} · bounded source window</caption>
            <thead>
              <tr>
                {columns.map(([k, h]) => (
                  <th key={k}>{h}</th>
                ))}
                <th>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((r, i) => (
                <tr
                  key={
                    r.id === null || r.id === undefined
                      ? `row:${i}`
                      : `id:${String(r.id)}`
                  }
                >
                  {columns.map(([k, h]) => (
                    <td
                      key={k}
                      data-label={
                        k === "realized_pnl" && r.status === "open"
                          ? "Realized so far"
                          : h
                      }
                    >
                      {k === "realized_pnl" && r.status === "open" && (
                        <small className="realized-label">
                          Realized so far ·{" "}
                        </small>
                      )}
                      {fieldValue(k, r[k])}
                    </td>
                  ))}
                  <td className="evidence-cell">
                    {open?.(r)}
                    <RecordDrawer
                      row={r}
                      title={value(r.symbol ?? r.name ?? r.id)}
                    >
                      {detail?.(r)}
                    </RecordDrawer>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

/** Passive shell when the active endpoint has no paging contract. */
export function PagingBar({
  count,
  limit,
  offset = 0,
  hasMore,
  onPage,
  busy = false,
}: {
  count: number;
  limit: number;
  offset?: number;
  hasMore?: boolean;
  onPage?: (offset: number) => void;
  busy?: boolean;
}) {
  return (
    <div className="paging-bar" aria-label="Result paging">
      <span>
        {offset === 0
          ? `Showing latest ${count}`
          : count
            ? `Showing records ${offset + 1}–${offset + count}`
            : `No records at offset ${offset}`}{" "}
        · up to {limit} per request.{" "}
        {onPage
          ? "Offset pages are live journal reads, not a frozen history snapshot."
          : "Paging unavailable from this endpoint; this is not complete history."}
      </span>
      <div>
        <button
          type="button"
          disabled={!onPage || offset === 0 || busy}
          onClick={() => onPage?.(Math.max(0, offset - limit))}
        >
          Previous page
        </button>
        <button
          type="button"
          disabled={!onPage || !hasMore || busy}
          onClick={() => onPage?.(offset + limit)}
        >
          Next page
        </button>
      </div>
    </div>
  );
}
