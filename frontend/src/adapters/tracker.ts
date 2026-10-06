/** Validated read-only contract; never turn malformed data into empty success. */
export const TRACKER_STATUSES = ["EVIDENCE_TO_MAP", "OPEN", "IN_PROGRESS", "AWAITING_EVIDENCE", "AWAITING_OWNER", "CLOSED", "DEFERRED", "BLOCKED"] as const;
export type TrackerStatus = typeof TRACKER_STATUSES[number];
export interface TrackerRow {
  id: string; area: string; stage: string; release_scope: string; status: TrackerStatus;
  required_behavior: string; why_needed: string; closure_condition: string;
  dependencies: string[]; parent_ids: string[]; related_items: string[];
  latest_evidence: string; next_proof: string; owner_role: string;
  [key: string]: unknown;
}
export interface TrackerData {
  generated_at: string; status: "AVAILABLE"; read_only: true;
  source: { path: string; sha256: string; next_path: string; next_sha256: string;
    repository_revision: string | null; observed_runtime_revision: string; version: string };
  selected: { id: string; status: "SELECTED_NOT_STARTED" | "IN_PROGRESS" | "CLOSED" | "BLOCKED"; mode: "DIAGNOSIS_ONLY" | "ENGINEERING_ONLY"; objective: string };
  counts: Record<string, number>;
  status_definitions: { status: TrackerStatus; meaning: string }[];
  readiness_gates: { id: string; title: string; current: string; closure: string; limit: string; items: string[] }[];
  items: TrackerRow[];
}
const obj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === "object" && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === "string" && v.length > 0;
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(text);
export function parseTracker(v: unknown): TrackerData {
  const fail = (): never => { throw new Error("Tracker contract invalid or degraded. No substitute rows were loaded."); };
  if (!obj(v) || v.status !== "AVAILABLE" || v.read_only !== true || !text(v.generated_at) || !Array.isArray(v.items) || v.items.length === 0) return fail();
  for (const row of v.items) {
    if (!obj(row) || !["id","area","stage","release_scope","required_behavior","why_needed","closure_condition","latest_evidence","next_proof","owner_role"].every(k => text(row[k])) || !TRACKER_STATUSES.includes(row.status as TrackerStatus) || !["dependencies","parent_ids","related_items"].every(k => strings(row[k]))) return fail();
  }
  const ids = v.items.map(r => (r as TrackerRow).id);
  if (new Set(ids).size !== ids.length || !obj(v.selected) || !ids.includes(v.selected.id as string) || !["SELECTED_NOT_STARTED","IN_PROGRESS","CLOSED","BLOCKED"].includes(v.selected.status as string) || !["DIAGNOSIS_ONLY","ENGINEERING_ONLY"].includes(v.selected.mode as string) || !text(v.selected.objective)) return fail();
  const active = v.items.filter(r => (r as TrackerRow).status === "IN_PROGRESS");
  const selected = v.items.find(r => (r as TrackerRow).id === (v.selected as Record<string, unknown>).id) as TrackerRow;
  if (active.length > 1 || active.some(r => (r as TrackerRow).id !== selected.id) || selected.status !== (v.selected.status === "SELECTED_NOT_STARTED" ? "OPEN" : v.selected.status)) return fail();
  if (!obj(v.source) || !["path","sha256","next_path","next_sha256","observed_runtime_revision","version"].every(k => text((v.source as Record<string,unknown>)[k])) || !(v.source.repository_revision === null || text(v.source.repository_revision))) return fail();
  const rows = v.items as TrackerRow[];
  if (!obj(v.counts) || !TRACKER_STATUSES.every(s => (v.counts as Record<string, unknown>)[s] === rows.filter((r: TrackerRow) => r.status === s).length)) return fail();
  if (!Array.isArray(v.status_definitions) || v.status_definitions.length !== TRACKER_STATUSES.length || new Set(v.status_definitions.map(d => obj(d) ? d.status : null)).size !== TRACKER_STATUSES.length || !v.status_definitions.every(d => obj(d) && TRACKER_STATUSES.includes(d.status as TrackerStatus) && text(d.meaning))) return fail();
  if (!Array.isArray(v.readiness_gates) || v.readiness_gates.length !== 7 || new Set(v.readiness_gates.map(g => obj(g) ? g.id : null)).size !== 7 || !v.readiness_gates.every(g => obj(g) && /^M[0-6]$/.test(String(g.id)) && ["title","current","closure","limit"].every(k => text(g[k])) && strings(g.items) && g.items.length > 0 && g.items.every(id => ids.includes(id)))) return fail();
  return v as unknown as TrackerData;
}
