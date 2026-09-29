import { timestamp as utc } from "./time";
import { lazyChunk, ChunkLoadError, retryFailedChunks } from "./lazyChunk";
import { Suspense, useEffect, useState } from "react";
import { ArrowUpRight, Anchor, LogOut } from "lucide-react";
import { usePreview } from "./context";
import { Mark, Badge, VisualBoundary } from "./components/ui";
import Overview from "./views/Overview";
const Luffy = lazyChunk(() => import("./views/Luffy"));
const GraphView = lazyChunk(() => import("./views/GraphView"));
const Operations = lazyChunk(() => import("./views/Operations"));
const Routes = lazyChunk(() => import("./views/Routes"));
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
];
const slug = (s: string) => s.toLowerCase().replaceAll(" ", "-");

const LIVE_ROUTES = new Set([
  "trades",
  "research",
  "strategies",
  "diagnostics",
]);
export default function Shell() {
  const [route, setRoute] = useState(
    location.hash.replace(/^#\/?/, "") || "overview",
  );
  const { adapter, scenario, setScenario, session, signOut } = usePreview();
  const live = import.meta.env.MODE === "production" || adapter.mode === "LIVE";
  useEffect(() => {
    const update = () => {
      // after a chunk failed, navigating reloads the new URL so the next
      // route is not left on a poisoned module import
      if (retryFailedChunks()) return;
      setRoute(location.hash.replace(/^#\/?/, "") || "overview");
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  const title = routes.find((r) => slug(r) === route) ?? "Page unavailable";
  return (
    <>
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
      <header className="topbar">
        <a href="#overview" className="brand" aria-label="LUFFY home">
          <Mark />
          <span>
            LUFFY<small>OWNER WORKSPACE</small>
          </span>
        </a>
        <div className="topbar-right">
          <span className="quiet desktop-only">
            {live ? "OWNER WORKSPACE / V2.1" : "FRONTEND PREVIEW / V1"}
          </span>
          <Badge tone={live ? "mint" : "amber"}>
            {live ? "Live" : "Isolated"}
          </Badge>
          <div
            className="owner-avatar"
            aria-label={
              live ? `Signed in as ${session?.principal ?? "unknown"}` : "Owner"
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
      <nav aria-label="Main navigation">
        {routes.map((r) => (
          <a
            key={r}
            href={`#${slug(r)}`}
            aria-current={title === r ? "page" : undefined}
          >
            {r}
            {r === "Operations" && (
              <small aria-hidden="true" className="mobile-nav-hint">
                Controls · Panic
              </small>
            )}
          </a>
        ))}
      </nav>
      <main id="content" tabIndex={-1}>
        <div className="page-heading">
          <div>
            <div className="eyebrow">
              <Anchor size={13} aria-hidden="true" /> OWNER /{" "}
              {title.toUpperCase()}
            </div>
            <h1>{title}</h1>
            <p>
              {route === "luffy"
                ? "A clear conversation. Evidence within reach."
                : route === "knowledge"
                  ? "Follow the evidence. Understand the connections."
                  : route === "live-system"
                    ? "Architecture, observed state and the space between."
                    : route === "operations" && live
                      ? "Owner controls, through the kernel's Owner Interface."
                      : route === "trades"
                        ? "Follow positions, recorded decisions and trade outcomes."
                        : route === "research"
                          ? "Questions to evidence. Evidence to conclusions."
                          : route === "strategies"
                            ? "Identity, lifecycle and the evidence behind each strategy."
                            : route === "diagnostics"
                              ? "Current health, historical evidence and unresolved visibility."
                              : "Your account and system, at a glance."}
            </p>
          </div>
          {!live && (
            <label className="scenario">
              Fixture scenario
              <select
                value={scenario}
                onChange={(e) => setScenario(e.target.value as typeof scenario)}
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
            {route === "overview" ? (
              <Overview />
            ) : route === "luffy" ? (
              <Luffy />
            ) : route === "knowledge" || route === "live-system" ? (
              <GraphView
                key={route}
                surface={route === "knowledge" ? "knowledge" : "system"}
              />
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
            ? `LUFFY / OWNER-FRONTEND-V2.1 · backend ${session?.backend.commit?.slice(0, 7) ?? "unknown"}`
            : "LUFFY / OWNER-FRONTEND-V1"}
        </span>
        <span>
          {live
            ? `LIVE · text chat · backend time ${utc(session?.backend.source_time)} · voice unavailable`
            : "Fixture mode · Text only · No control connection"}
        </span>
      </footer>
    </>
  );
}
