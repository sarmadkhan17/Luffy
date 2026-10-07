import { timestamp as utc } from "./time";
import { lazyChunk, ChunkLoadError, retryFailedChunks } from "./lazyChunk";
import { Suspense, useEffect, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { usePreview } from "./context";
import { parseHash } from "./links";
import { VisualBoundary } from "./components/ui";
import { PageHeader } from "./components/PageHeader";
import "./fonts";
import "./glacier.css";
import Overview from "./views/Overview";
const Trades = lazyChunk(() => import("./views/Trades"));
const Research = lazyChunk(() => import("./views/Research"));
const Strategies = lazyChunk(() => import("./views/Strategies"));
const Luffy = lazyChunk(() => import("./views/Luffy"));
const GraphView = lazyChunk(() => import("./views/GraphView"));
const OperationsLog = lazyChunk(() => import("./views/OperationsLog"));
const LiveSystem = lazyChunk(() => import("./views/LiveSystem"));
const Knowledge = lazyChunk(() => import("./views/Knowledge"));
const Diagnostics = lazyChunk(() => import("./views/Diagnostics"));
const Tracker = lazyChunk(() => import("./views/Tracker"));
const Routes = lazyChunk(() => import("./views/Routes"));
const SystemObserved = lazyChunk(() =>
  import("./views/Evidence").then((m) => ({ default: m.SystemObserved })),
);
export const routes = [
  "Overview",
  "Trades",
  "Research",
  "Strategies",
  "LUFFY",
  "Operations",
  "Live System",
  "Knowledge",
  "Diagnostics",
  "Tracker",
];
/** Rail grouping: what the owner acts on, who speaks, how the machine runs. */
const ICON_PATH: Record<string, string> = {
  Overview: "M3 11 12 4l9 7v9H5v-9",
  Trades: "M4 7h13l-3-3M20 17H7l3 3",
  Research: "M10 4a6 6 0 1 0 0 12 6 6 0 0 0 0-12zM20 20l-5.5-5.5",
  Strategies: "M4 6h16M4 12h10M4 18h6",
  LUFFY: "M12 2v4M12 18v4M2 12h4M18 12h4M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z",
  Operations: "M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0",
  "Live System": "M3 12h4l3-7 4 14 3-7h4",
  Knowledge: "M12 4a2 2 0 1 0 0 .1M5 18a2 2 0 1 0 0 .1M19 18a2 2 0 1 0 0 .1M12 6v4M7 17l4-6M17 17l-4-6",
  Diagnostics: "M4 4v6a6 6 0 0 0 12 0V4M10 16v2a4 4 0 0 0 8 0v-3M18 13a2 2 0 1 0 0-.1",
  Tracker: "M9 6h11M9 12h11M9 18h11M4 6l1 1 2-2M4 12l1 1 2-2M4 18l1 1 2-2",
};
/** Rail grouping from the approved design: what the owner acts on, who speaks, how the machine runs. */
const RAIL: { group: string; items: string[] }[] = [
  { group: "Desk", items: ["Overview", "Trades", "Research", "Strategies"] },
  { group: "Partner", items: ["LUFFY"] },
  { group: "Machine", items: ["Operations", "Live System", "Knowledge", "Diagnostics", "Tracker"] },
];
const SUBTITLE: Record<string, string> = {
  overview: "Attention, capital and risk first; recorded activity below.",
  trades: "Each trade as a forensic chain, from opportunity to outcome.",
  research: "Hypotheses, results and their lineage in the research ledger.",
  strategies: "Identity, lifecycle, provenance and economics per strategy.",
  luffy: "LUFFY speaks; the dashboard proves.",
  operations: "What LUFFY recorded doing, in order, with its evidence.",
  "live-system": "Declared architecture beside what the records observe.",
  knowledge: "The vault as a clustered graph; relations as written.",
  diagnostics: "Stores, probes, collectors, watchdog and recovery evidence.",
  tracker: "Requirements, narrow closure evidence and the single selected engineering package.",
};
const slug = (s: string) => s.toLowerCase().replaceAll(" ", "-");

/** Tabs rebuilt to the approved design: they draw their own header. */
const REDESIGNED = new Set(["overview", "trades", "research", "strategies", "luffy", "operations", "live-system", "knowledge", "diagnostics"]);
const LIVE_ROUTES = new Set([
  "diagnostics",
]);
export default function Shell() {
  const [route, setRoute] = useState(() => parseHash(location.hash).route);
  const { adapter, scenario, setScenario, session } = usePreview();
  const live = import.meta.env.MODE === "production" || adapter.mode === "LIVE";
  useEffect(() => {
    const update = () => {
      // after a chunk failed, navigating reloads the new URL so the next
      // route is not left on a poisoned module import
      if (retryFailedChunks()) return;
      setRoute(parseHash(location.hash).route);
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const title = routes.find((r) => slug(r) === route) ?? "Page unavailable";
  const group = RAIL.find((g) => g.items.some((r) => r === title))?.group;
  return (
    <div className={`gl-app route-${route}`}>
      {live ? (
        <div className="live-banner" data-testid="mode-banner">
          {session?.dashboard_mode === "READ_ONLY_GUI" ? `READ-ONLY GUI — canonical evidence · control and provider actions disabled · kernel ${String(session.kernel_process?.state ?? "UNAVAILABLE").toLowerCase()} · runtime telemetry may be unknown or stale` : "LIVE — Luffy backend · demo venue account (BINANCE_DEMO) · owner controls only through the Owner Interface"}
        </div>
      ) : (
        <div className="demo-banner" data-testid="mode-banner">
          DEMO — synthetic data, no trading connection
        </div>
      )}
      <a
        className="skip"
        href="#content"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("content")?.focus();
        }}
      >
        Skip to content
      </a>
      <aside className="gl-rail">
        <a href="#overview" className="gl-brand" aria-label="LUFFY home">
          <LuffyHat />
          <span className="name">
            <b>LUFFY</b>
            <small>Autonomous Trading OS</small>
          </span>
        </a>
        <nav className="gl-nav" aria-label="Main navigation">
          {RAIL.map((g) => (
            <div className="grp" key={g.group}>
              <span aria-hidden="true">{g.group}</span>
              {g.items.map((r) => (
                <a key={r} href={`#${slug(r)}`} aria-current={title === r ? "page" : undefined}>
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d={ICON_PATH[r]} /></svg>
                  {r}
                </a>
              ))}
            </div>
          ))}
        </nav>
        <p className="gl-quote">“Discipline compounds freedom.”</p>
      </aside>
      <main id="content" tabIndex={-1} className="gl-main">
        {!REDESIGNED.has(route) && (
          <>
            <PageHeader title={title} />
            <div className="page-heading">
              <div>
                <div className="eyebrow">
                  {group ? group.toUpperCase() : "OWNER"} / {title.toUpperCase()}
                </div>
                <p>{SUBTITLE[route] ?? SUBTITLE.overview}</p>
              </div>
              {!live && (
                <label className="scenario">
                  Fixture scenario
                  <select value={scenario} onChange={(e) => setScenario(e.target.value as typeof scenario)}>
                    <option value="normal">Normal snapshot</option>
                    <option value="stale">Stale data</option>
                    <option value="missing">Missing data</option>
                    <option value="error">Adapter error</option>
                  </select>
                </label>
              )}
            </div>
          </>
        )}
        <VisualBoundary
          key={route}
          fallback={(error, reset) => (
            <section
              className="panel empty"
              role="alert"
              data-testid="route-failed"
            >
              <h2>
                {error instanceof ChunkLoadError
                  ? "This view could not be loaded."
                  : "This view failed while rendering."}
              </h2>
              <p>
                {error.message} No data was substituted. Navigation remains
                available.
              </p>
              <button type="button" onClick={reset}>
                Retry
              </button>
            </section>
          )}
        >
          <Suspense
            fallback={
              <div className="empty" role="status" data-testid="route-loading">
                Loading view… Navigation remains available.
              </div>
            }
          >
            {route === "tracker" ? (
              <Tracker />
            ) : route === "overview" ? (
              <Overview />
            ) : route === "trades" ? (
              <Trades />
            ) : route === "research" ? (
              <Research />
            ) : route === "strategies" ? (
              <Strategies />
            ) : route === "luffy" ? (
              <Luffy />
            ) : live && route === "live-system" ? (
              <LiveSystem />
            ) : live && route === "knowledge" ? (
              <Knowledge />
            ) : live && route === "diagnostics" ? (
              <Diagnostics />
            ) : route === "knowledge" || route === "live-system" ? (
              <>
                <GraphView
                  key={route}
                  surface={route === "knowledge" ? "knowledge" : "system"}
                />
                {live && route === "live-system" && <SystemObserved />}
              </>
            ) : live && route === "operations" ? (
              <OperationsLog />
            ) : live && LIVE_ROUTES.has(route) ? (
              <Routes route={route} />
            ) : (
              <section className="panel empty">
                <h2>Not implemented in this preview.</h2>
                <p>
                  The shared navigation is ready. This route awaits its own
                  implementation.
                </p>
                <a href="#overview">
                  Return to Overview <ArrowUpRight size={16} />
                </a>
              </section>
            )}
          </Suspense>
        </VisualBoundary>
      </main>
      <footer className="gl-foot">
        <span>
          {live
            ? `LUFFY · OWNER WORKSPACE · backend commit ${session?.backend.commit?.slice(0, 7) ?? "unknown"}`
            : "LUFFY · FRONTEND PREVIEW"}
        </span>
        <span>
          {live
            ? `${session?.dashboard_mode === "READ_ONLY_GUI" ? "READ-ONLY · chat disabled" : "LIVE · text chat"} · backend time ${utc(session?.backend.source_time)} · voice unavailable`
            : "Fixture mode · Text only · No control connection"}
        </span>
      </footer>
    </div>
  );
}

/** The brand mark from the approved design. */
function LuffyHat() {
  return (
    <svg width="48" height="44" viewBox="0 0 48 44" aria-hidden="true">
      <ellipse cx="24" cy="30" rx="22" ry="8" style={{ fill: "rgb(var(--a) / .18)", stroke: "rgb(var(--a))", strokeWidth: 1.2 }} />
      <ellipse cx="24" cy="30" rx="17" ry="5.5" style={{ fill: "none", stroke: "rgb(var(--a) / .45)", strokeWidth: 0.8, strokeDasharray: "1.5 2.5" }} />
      <path d="M11.5 29 C11 15, 37 15, 36.5 29 C31 31.5, 17 31.5, 11.5 29 Z" style={{ fill: "rgb(var(--a) / .28)", stroke: "rgb(var(--a))", strokeWidth: 1.2 }} />
      <path d="M14 21 C20 18.5, 28 18.5, 34 21 M13 25 C20 22.5, 28 22.5, 35 25" style={{ fill: "none", stroke: "rgb(var(--a) / .5)", strokeWidth: 0.8, strokeDasharray: "1.5 2" }} />
      <path d="M11.8 26.2 C17 28.6, 31 28.6, 36.2 26.2 L36.5 29 C31 31.5, 17 31.5, 11.5 29 Z" style={{ fill: "rgb(var(--a2))" }} />
      <path d="M20 15.8 C22.5 14.6, 25.5 14.6, 28 15.8" style={{ fill: "none", stroke: "rgb(var(--hi) / .7)", strokeWidth: 1, strokeLinecap: "round" }} />
    </svg>
  );
}
