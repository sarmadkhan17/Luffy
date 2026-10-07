import { useEffect, useState, type ReactNode } from "react";
import { LogOut } from "lucide-react";
import { usePreview } from "../context";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const p2 = (n: number) => String(n).padStart(2, "0");
/** "Wed 7 Oct · 14:27 UTC" */
export const clockLabel = (d: Date) =>
  `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} · ${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())} UTC`;

/** Header row shared by every tab: title, tab-specific status/controls, clock, data-source chip. */
export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  const { adapter, session, signOut } = usePreview();
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 15000);
    return () => clearInterval(t);
  }, []);
  const live = adapter.mode === "LIVE";
  return (
    <header className="gl-head">
      <h1>{title}</h1>
      {children}
      <span style={{ flex: 1 }} />
      <span className="m" style={{ fontSize: 13, color: "var(--txt2)" }}>{clockLabel(now)}</span>
      <span className="gl-chip" title={live ? `Signed in as ${session?.principal ?? "unknown"}` : undefined}>
        {live ? (session?.dashboard_mode === "READ_ONLY_GUI" ? "LIVE DATA · READ-ONLY GUI" : "LIVE DATA · luffy.db") : "FIXTURE · NOT LIVE"}
      </span>
      {live && signOut && (
        <button type="button" className="gl-btn" onClick={signOut} aria-label="Sign out" style={{ minWidth: 36, padding: 0 }}>
          <LogOut size={15} aria-hidden="true" />
        </button>
      )}
    </header>
  );
}
