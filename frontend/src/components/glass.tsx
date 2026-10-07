/** Glacier design primitives shared by every tab: glass card, centred detail
 * panel (opens on click only) and a table with header sort/filter + pagination. */
import { useEffect, useMemo, useState, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Filter, X } from "lucide-react";

export function Card({
  children,
  className = "",
  need,
  label,
}: {
  children: ReactNode;
  className?: string;
  need?: boolean;
  label?: string;
}) {
  return (
    <section className={`gl-card ${need ? "need" : ""} ${className}`} aria-label={label}>
      {children}
    </section>
  );
}

export function CardHead({
  icon,
  title,
  sub,
  tone,
  children,
}: {
  icon: ReactNode;
  title: string;
  sub?: ReactNode;
  tone?: "teal" | "ok" | "solid";
  children?: ReactNode;
}) {
  return (
    <div className="gl-hd">
      <span className={`gl-ic ${tone ?? ""}`} aria-hidden="true">
        {icon}
      </span>
      <h2>{title}</h2>
      {sub && <span className="sub">{sub}</span>}
      <span className="fill" />
      {children}
    </div>
  );
}

/** Centred modal. Portal is mounted inside .gl-app so the theme variables apply. */
export function DetailPanel({
  open,
  onClose,
  title,
  description,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
}) {
  const container = typeof document === "undefined" ? undefined : (document.querySelector(".gl-app") as HTMLElement | null) ?? undefined;
  return (
    <Dialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
      <Dialog.Portal container={container}>
        <Dialog.Overlay className="gl-overlay" />
        <Dialog.Content className="gl-panel" aria-describedby={description ? undefined : undefined}>
          <Dialog.Title asChild>
            <h2>{title}</h2>
          </Dialog.Title>
          {description ? (
            <Dialog.Description asChild>
              <p className="desc">{description}</p>
            </Dialog.Description>
          ) : (
            <Dialog.Description className="gl-sr" hidden />
          )}
          {children}
          <Dialog.Close asChild>
            <button type="button" className="gl-btn x" aria-label="Close panel" style={{ minWidth: 36, padding: 0 }}>
              <X size={16} aria-hidden="true" />
            </button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function KV({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="gl-kv">
      {items.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v ?? "UNAVAILABLE"}</dd>
        </div>
      ))}
    </dl>
  );
}

export interface Col<T> {
  key: string;
  label: string;
  align?: "r";
  /** comparable / filterable value; null sorts last and never matches a filter */
  value: (r: T) => string | number | null;
  /** ordering key when it differs from the filter value (e.g. a state rank) */
  sortValue?: (r: T) => string | number | null;
  cell?: (r: T) => ReactNode;
  filter?: "text" | "select";
  /** the cell that opens the row's detail panel */
  primary?: boolean;
}

const cmp = (a: string | number | null, b: string | number | null) =>
  a === null ? (b === null ? 0 : 1) : b === null ? -1 : typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));

export function DataTable<T>({
  rows,
  cols,
  rowId,
  onOpen,
  pageSizes = [10, 25, 50],
  pageSize: initialSize,
  label,
  empty = "No rows.",
  summary,
  rowTone,
  defaultSort,
  onShown,
}: {
  rows: T[];
  cols: Col<T>[];
  rowId: (r: T) => string;
  onOpen?: (r: T) => void;
  pageSizes?: number[];
  pageSize?: number;
  label: string;
  empty?: string;
  /** shown above the table, computed from the filtered rows */
  summary?: (shown: T[]) => ReactNode;
  rowTone?: (r: T) => string | undefined;
  defaultSort?: { key: string; dir: 1 | -1 };
  /** called with the filtered rows whenever they change (for an inline note in the card header) */
  onShown?: (rows: T[]) => void;
}) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(defaultSort ?? null);
  const [text, setText] = useState<Record<string, string>>({});
  const [picked, setPicked] = useState<Record<string, string[]>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [size, setSize] = useState(initialSize ?? pageSizes[0]);

  const shown = useMemo(() => {
    let out = rows.filter((r) =>
      cols.every((c) => {
        const v = c.value(r);
        const sel = picked[c.key];
        if (sel?.length && !(v !== null && sel.includes(String(v)))) return false;
        const f = (text[c.key] ?? "").trim().toLowerCase();
        return !f || (v !== null && String(v).toLowerCase().includes(f));
      }),
    );
    if (sort) {
      const c = cols.find((x) => x.key === sort.key);
      if (c) out = [...out].sort((a, b) => sort.dir * cmp((c.sortValue ?? c.value)(a), (c.sortValue ?? c.value)(b)));
    }
    return out;
  }, [rows, cols, text, picked, sort]);

  useEffect(() => { onShown?.(shown); }, [shown]); // eslint-disable-line react-hooks/exhaustive-deps
  const pages = Math.max(1, Math.ceil(shown.length / size));
  const at = Math.min(page, pages - 1);
  const slice = shown.slice(at * size, at * size + size);
  const first = shown.length ? at * size + 1 : 0;
  const nums: (number | "…")[] = [];
  for (let k = 0; k < pages; k++) {
    if (k === 0 || k === pages - 1 || Math.abs(k - at) <= 1) nums.push(k);
    else if (nums[nums.length - 1] !== "…") nums.push("…");
  }
  const chips = cols.flatMap((c) => [
    ...(picked[c.key] ?? []).map((v) => ({ id: `${c.key}:${v}`, label: v, clear: () => setPicked({ ...picked, [c.key]: picked[c.key].filter((x) => x !== v) }) })),
    ...(text[c.key]?.trim() ? [{ id: `${c.key}:text`, label: `${c.label}: ${text[c.key]}`, clear: () => setText({ ...text, [c.key]: "" }) }] : []),
  ]);

  return (
    <div>
      {(summary || chips.length > 0) && (
        <div className="gl-chips">
          {summary && <span className="m gl-sum">{summary(shown)}</span>}
          {chips.map((ch) => (
            <button key={ch.id} type="button" className="gl-tf on" aria-pressed="true" aria-label={`Clear filter ${ch.label}`} onClick={() => { ch.clear(); setPage(0); }}>
              {ch.label} ✕
            </button>
          ))}
        </div>
      )}
      <div className="gl-tablewrap">
        <table className="gl-table" aria-label={label}>
          <thead>
            <tr>
              {cols.map((c) => {
                const active = sort?.key === c.key;
                const n = (picked[c.key]?.length ?? 0) + (text[c.key]?.trim() ? 1 : 0);
                const counts = new Map<string, number>();
                if (c.filter === "select") for (const r of rows) { const v = c.value(r); if (v !== null) counts.set(String(v), (counts.get(String(v)) ?? 0) + 1); }
                return (
                  <th key={c.key} scope="col" className={c.align === "r" ? "r" : ""} aria-sort={active ? (sort!.dir === 1 ? "ascending" : "descending") : "none"}>
                    <div className={`gl-th ${c.align === "r" ? "r" : ""}`}>
                      <div className="gl-th-top">
                        <button
                          type="button"
                          aria-pressed={active}
                          aria-label={`Sort by ${c.label}`}
                          onClick={() => {
                            setSort(!active ? { key: c.key, dir: 1 } : sort!.dir === 1 ? { key: c.key, dir: -1 } : null);
                            setPage(0);
                          }}
                        >
                          {c.label}
                          {active ? (sort!.dir === 1 ? <ArrowUp size={12} aria-hidden="true" /> : <ArrowDown size={12} aria-hidden="true" />) : <ArrowUpDown size={11} aria-hidden="true" style={{ opacity: 0.5 }} />}
                        </button>
                        {c.filter && (
                          <button type="button" aria-pressed={n > 0 || editing === c.key} aria-label={`Filter ${c.label}`} aria-expanded={editing === c.key} onClick={() => setEditing(editing === c.key ? null : c.key)}>
                            <Filter size={12} aria-hidden="true" />
                          </button>
                        )}
                      </div>
                      {c.filter === "text" && editing === c.key && (
                        <input autoFocus aria-label={`${c.label} filter`} placeholder="contains…" value={text[c.key] ?? ""} onChange={(e) => { setText({ ...text, [c.key]: e.target.value }); setPage(0); }} />
                      )}
                      {c.filter === "select" && editing === c.key && (
                        <div className="gl-menu" role="group" aria-label={`${c.label} filter`}>
                          {[...counts.entries()].sort().map(([v, k]) => {
                            const on = picked[c.key]?.includes(v) ?? false;
                            return (
                              <label key={v} className={on ? "on" : ""}>
                                <input type="checkbox" checked={on} onChange={() => { setPicked({ ...picked, [c.key]: on ? picked[c.key].filter((x) => x !== v) : [...(picked[c.key] ?? []), v] }); setPage(0); }} />
                                <span>{v}</span>
                                <span className="m">{k}</span>
                              </label>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {slice.map((r) => (
              <tr key={rowId(r)} className={onOpen ? "click" : ""} style={rowTone?.(r) ? { background: rowTone(r) } : undefined} onClick={onOpen ? () => onOpen(r) : undefined}>
                {cols.map((c) => {
                  const content = c.cell ? c.cell(r) : (c.value(r) ?? "UNAVAILABLE");
                  return (
                    <td key={c.key} className={c.align === "r" ? "r m" : ""}>
                      {c.primary && onOpen ? (
                        <button type="button" className="gl-rowbtn" onClick={(e) => { e.stopPropagation(); onOpen(r); }}>
                          {content}
                        </button>
                      ) : (
                        content
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        {slice.length === 0 && <div className="gl-empty">{rows.length ? "No rows match the filters." : empty}</div>}
      </div>
      <div className="gl-pager">
        {pageSizes.length > 1 ? (
          <span>
            Rows per page{" "}
            {pageSizes.map((z) => (
              <button key={z} type="button" className="gl-tf" aria-pressed={size === z} onClick={() => { setSize(z); setPage(0); }} style={{ minWidth: 38, marginLeft: 4 }}>
                {z}
              </button>
            ))}
          </span>
        ) : <span />}
        <span>
          {first}–{Math.min(shown.length, at * size + size)} of {shown.length}
          {shown.length !== rows.length ? ` (filtered from ${rows.length})` : ""}
        </span>
        <div>
          <button type="button" className="gl-tf" disabled={at === 0} onClick={() => setPage(at - 1)} aria-label="Previous page"><ChevronLeft size={14} aria-hidden="true" /></button>
          {nums.map((k, i) => k === "…" ? <span key={`e${i}`}>…</span> : (
            <button key={k} type="button" className="gl-tf" aria-pressed={k === at} aria-label={`Page ${k + 1}`} onClick={() => setPage(k)} style={{ minWidth: 38 }}>{k + 1}</button>
          ))}
          <button type="button" className="gl-tf" disabled={at >= pages - 1} onClick={() => setPage(at + 1)} aria-label="Next page"><ChevronRight size={14} aria-hidden="true" /></button>
        </div>
      </div>
    </div>
  );
}
