/** Hash deep links between recorded evidence: `#<route>?<param>=<id>`.
 * Only ids a backend record returned are ever linked; nothing is matched. */
import { useEffect, useState } from "react";

export type RecordKind =
  "trade" | "decision" | "strategy" | "research" | "note" | "family";

const TARGET: Record<RecordKind, [route: string, param: string]> = {
  trade: ["trades", "trade"],
  decision: ["operations", "decision"],
  strategy: ["strategies", "id"],
  research: ["research", "combo"],
  note: ["knowledge", "note"],
  family: ["strategies", "family"],
};

export function recordHref(kind: RecordKind, id: string) {
  const [route, param] = TARGET[kind];
  return `#${route}?${param}=${encodeURIComponent(id)}`;
}

/** The route slug and its query, from a location hash. */
export function parseHash(hash: string) {
  const raw = hash.replace(/^#\/?/, "");
  const i = raw.indexOf("?");
  const route = (i < 0 ? raw : raw.slice(0, i)) || "overview";
  return {
    route,
    params: new URLSearchParams(i < 0 ? "" : raw.slice(i + 1)),
  };
}

/** A query parameter of the current hash route, kept in sync on navigation. */
export function useRouteParam(name: string): string | null {
  const read = () => parseHash(location.hash).params.get(name);
  const [v, setV] = useState(read);
  useEffect(() => {
    const update = () => setV(read());
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);
  return v;
}

/** Map a backend `{kind, id}` reference onto a link kind, when navigable. */
export function refKind(kind: unknown): RecordKind | null {
  return kind === "trade" ||
    kind === "decision" ||
    kind === "strategy" ||
    kind === "research"
    ? kind
    : null;
}
