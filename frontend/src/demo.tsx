/** DEMO root: fixtures for isolated development and tests only. Reached only in
 * a `--mode demo` build; the production (LIVE) build removes this import. */
import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PreviewProvider } from "./context";
import { fixtureAdapter } from "./adapters/fixture";
import Shell from "./Shell";
import "./styles.css";
import "./live.css";
import "./product.css";
import "./m4.css";
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
export function mountDemo() {
  document.title = "LUFFY · Owner workspace · DEMO";
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <QueryClientProvider client={client}>
        <PreviewProvider adapter={fixtureAdapter}>
          <Shell />
        </PreviewProvider>
      </QueryClientProvider>
    </React.StrictMode>,
  );
}
