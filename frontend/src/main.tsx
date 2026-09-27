import React, { lazy, Suspense, useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ArrowUpRight, Anchor } from "lucide-react";
import { PreviewProvider, usePreview } from "./context";
import { Mark, Badge, VisualBoundary } from "./components/ui";
import Overview from "./views/Overview";
import Luffy from "./views/Luffy";
import "./styles.css";
const GraphView = lazy(() => import("./views/GraphView"));
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
function Shell() {
  const [route, setRoute] = useState(location.hash.slice(1) || "overview");
  const { scenario, setScenario } = usePreview();
  useEffect(() => {
    const update = () => setRoute(location.hash.slice(1) || "overview");
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
      <div className="demo-banner">
        DEMO — synthetic data, no trading connection
      </div>
      <header className="topbar">
        <a href="#overview" className="brand" aria-label="LUFFY home">
          <Mark />
          <span>
            LUFFY<small>OWNER WORKSPACE</small>
          </span>
        </a>
        <div className="topbar-right">
          <span className="quiet desktop-only">FRONTEND PREVIEW / V1</span>
          <Badge tone="amber">Isolated</Badge>
          <div className="owner-avatar" aria-label="Owner">
            O
          </div>
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
                    : "Your account and system, at a glance."}
            </p>
          </div>
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
        </div>
        <VisualBoundary key={route}>
          <Suspense
            fallback={
              <div className="empty" role="status">
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
        <span>LUFFY / OWNER-FRONTEND-V1</span>
        <span>Fixture mode · Text only · No control connection</span>
      </footer>
    </>
  );
}
const client = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      staleTime: Infinity,
      refetchOnWindowFocus: false,
      gcTime: 300000,
    },
  },
});
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <PreviewProvider>
        <Shell />
      </PreviewProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
