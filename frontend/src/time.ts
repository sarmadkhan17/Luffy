/** One owner-facing convention: UTC, second precision. Never format invalid input as now. */
export const timestamp = (v: unknown): string =>
  typeof v === "string" && Number.isFinite(Date.parse(v))
    ? new Date(v).toISOString().slice(0, 19).replace("T", " ") + " UTC"
    : "Source time unavailable";
export function modifiedAge(v: string | null) {
  if (!v || !Number.isFinite(Date.parse(v))) return "age unavailable";
  const seconds = Math.floor((Date.now() - Date.parse(v)) / 1000);
  if (seconds < 0) return "source clock ahead";
  return seconds < 60
    ? `${seconds}s ago`
    : seconds < 3600
      ? `${Math.floor(seconds / 60)}m ago`
      : seconds < 86400
        ? `${Math.floor(seconds / 3600)}h ago`
        : `${Math.floor(seconds / 86400)}d ago`;
}
