/** Unresolved owner requests, one storage key per request id — the legacy
 * dashboard's protocol and prefix ("luffy.owner.req.<id>" → {action,id,t}), so
 * an unresolved request from either UI is found and re-sent with the SAME id.
 * No whole-map read-modify-write: creating writes only its own key, completing
 * removes only its own key. Ids are opaque and carry no private data. */
import type { PendingRequest } from "./contracts";

export const PREFIX = "luffy.owner.req.";
/** Statuses with a recorded final answer; anything else keeps the id. */
export const DEFINITIVE = [
  "ACCEPTED",
  "ACTIVATED",
  "CONTAINED",
  "ALREADY_SET",
  "REFUSED",
];
const memory = new Map<string, PendingRequest>();

export function entries(): PendingRequest[] {
  const out: PendingRequest[] = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (!k || !k.startsWith(PREFIX)) continue;
      try {
        const e = JSON.parse(localStorage.getItem(k) ?? "null");
        if (e && e.id && e.action && k === PREFIX + e.id) out.push(e);
      } catch {
        /* ignore a foreign or corrupt entry */
      }
    }
  } catch {
    /* storage unavailable: memory only */
  }
  for (const e of memory.values())
    if (!out.some((o) => o.id === e.id)) out.push(e);
  return out;
}

/** The oldest unresolved request for this action, from any tab or UI. */
export function find(action: string): PendingRequest | null {
  return (
    entries()
      .filter((e) => e.action === action)
      .sort(
        (a, b) => a.t - b.t || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0),
      )[0] ?? null
  );
}

function newId() {
  return typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
        b.toString(16).padStart(2, "0"),
      ).join("");
}

/** durable=false: storage failed; the entry survives only until reload. */
export function create(action: string): {
  entry: PendingRequest;
  durable: boolean;
} {
  const entry = { action, id: newId(), t: Date.now() };
  try {
    localStorage.setItem(PREFIX + entry.id, JSON.stringify(entry));
    return { entry, durable: true };
  } catch {
    memory.set(entry.id, entry);
    return { entry, durable: false };
  }
}

export function done(id: string) {
  memory.delete(id);
  try {
    localStorage.removeItem(PREFIX + id);
  } catch {
    /* nothing else to clear */
  }
}
