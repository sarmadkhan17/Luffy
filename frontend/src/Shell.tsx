import { timestamp as utc } from "./time";
import { lazyChunk, ChunkLoadError, retryFailedChunks } from "./lazyChunk";
import { Suspense, useEffect, useState } from "react";
import {
  ArrowUpRight,
  LogOut,
  LayoutDashboard,
  ArrowLeftRight,
  FlaskConical,
  Layers,
  Orbit,
  Activity,
  Network,
  Waypoints,
  Stethoscope,
  ListChecks,
  type LucideIcon,
} from "lucide-react";
import { usePreview } from "./context";
import { parseHash } from "./links";
import { Mark, Badge, VisualBoundary } from "./components/ui";
import Overview from "./views/Overview";
const Luffy = lazyChunk(() => import("./views/Luffy"));
const GraphView = lazyChunk(() => import("./views/GraphView"));
const Operations = lazyChunk(() => import("./views/Operations"));
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
const RAIL: { group: string; items: [string, LucideIcon][] }[] = [
  {
    group: "Desk",
    items: [
      ["Overview", LayoutDashboard],
      ["Trades", ArrowLeftRight],
      ["Research", FlaskConical],
      ["Strategies", Layers],
    ],
  },
  { group: "Partner", items: [["LUFFY", Orbit]] },
  {
    group: "Machine",
    items: [
      ["Operations", Activity],
      ["Live System", Network],
      ["Knowledge", Waypoints],
      ["Diagnostics", Stethoscope],
      ["Tracker", ListChecks],
    ],
  },
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

const LIVE_ROUTES = new Set([
  "trades",
  "research",
  "strategies",
  "diagnostics",
]);
export default function Shell() {
  const [route, setRoute] = useState(() => parseHash(location.hash).route);
  const { adapter, scenario, setScenario, session, signOut } = usePreview();
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
  const group = RAIL.find((g) => g.items.some(([r]) => r === title))?.group;
  return (
    <div className={`app-shell route-${route}`}>
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
      {live ? (
        <div className="live-banner" data-testid="mode-banner">
          LIVE — Luffy backend · demo venue account (BINANCE_DEMO) · owner
          controls only through the Owner Interface
        </div>
      ) : (
        <div className="demo-banner" data-testid="mode-banner">
          DEMO — synthetic data, no trading connection
        </div>
      )}
      <aside className="rail">
        <a href="#overview" className="brand" aria-label="LUFFY home">
          <Mark />
          <span>
            LUFFY<small>OWNER WORKSPACE</small>
          </span>
        </a>
        <nav aria-label="Main navigation">
          {RAIL.map((g) => (
            <div className="rail-group" key={g.group}>
              <span className="rail-label" aria-hidden="true">
                {g.group}
              </span>
              {g.items.map(([r, Icon]) => (
                <a
                  key={r}
                  href={`#${slug(r)}`}
                  aria-current={title === r ? "page" : undefined}
                >
                  <Icon size={16} strokeWidth={1.6} aria-hidden="true" />
                  <span className="rail-text">{r}</span>
                  {r === "Operations" && (
                    <small aria-hidden="true" className="mobile-nav-hint">
                      Controls · Panic
                    </small>
                  )}
                </a>
              ))}
            </div>
          ))}
        </nav>
        <div className="rail-foot" aria-hidden="true">
          <span className="rail-bearing" />
          <span>{live ? "LIVE · DEMO VENUE" : "FIXTURE PREVIEW"}</span>
        </div>
      </aside>
      <div className="stage">
        <header className="topbar">
          <div className="topbar-context">
            <span className="quiet">{group ?? "Owner"}</span>
            <span className="topbar-sep" aria-hidden="true">
              /
            </span>
            <span>{title}</span>
          </div>
          <div className="topbar-right">
            <span className="quiet desktop-only">
              {live ? "OWNER WORKSPACE" : "FRONTEND PREVIEW"}
            </span>
            <Badge tone={live ? "mint" : "amber"}>
              {live ? "Live" : "Isolated"}
            </Badge>
            <div
              className="owner-avatar"
              aria-label={
                live
                  ? `Signed in as ${session?.principal ?? "unknown"}`
                  : "Owner"
              }
              title={
                live
                  ? `Session ${session?.session.method} · expires ${utc(session?.session.expires_at)}`
                  : undefined
              }
            >
              O
            </div>
            {live && signOut && (
              <button
                className="icon-button"
                onClick={signOut}
                aria-label="Sign out"
              >
                <LogOut size={17} />
              </button>
            )}
          </div>
        </header>
        <main id="content" tabIndex={-1}>
          <div className="page-heading">
            <div>
              <div className="eyebrow">
                OWNER / {title.toUpperCase()}
              </div>
              <h1>{title}</h1>
              <p>
                {route === "operations" && !live
                  ? SUBTITLE.overview
                  : (SUBTITLE[route] ?? SUBTITLE.overview)}
              </p>
            </div>
            {!live && (
              <label className="scenario">
                Fixture scenario
                <select
                  value={scenario}
                  onChange={(e) =>
                    setScenario(e.target.value as typeof scenario)
                  }
                >
                  <option value="normal">Normal snapshot</option>
                  <option value="stale">Stale data</option>
                  <option value="missing">Missing data</option>
                  <option value="error">Adapter error</option>
                </select>
              </label>
            )}
          </div>
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
              ) : route === "luffy" ? (
                <Luffy />
              ) : route === "knowledge" || route === "live-system" ? (
                <>
                  <GraphView
                    key={route}
                    surface={route === "knowledge" ? "knowledge" : "system"}
                  />
                  {live && route === "live-system" && <SystemObserved />}
                </>
              ) : live && route === "operations" ? (
                <Operations />
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
        <footer>
          <span>
            {live
              ? `LUFFY · OWNER WORKSPACE · backend commit ${session?.backend.commit?.slice(0, 7) ?? "unknown"}`
              : "LUFFY · FRONTEND PREVIEW"}
          </span>
          <span>
            {live
              ? `LIVE · text chat · backend time ${utc(session?.backend.source_time)} · voice unavailable`
              : "Fixture mode · Text only · No control connection"}
          </span>
        </footer>
      </div>
    </div>
  );
}
