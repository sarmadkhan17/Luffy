/** LIVE root: bootstrap the signed-in session, then render the shell bound to
 * the live adapter. Every failure is shown as a failure: this module has no
 * route to fixtures, and a session that ends clears everything it loaded. */
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PreviewProvider } from "./context";
import Shell from "./Shell";
import { CHUNK_ERROR } from "./lazyChunk";
import {
  BackendUnavailable,
  SessionExpired,
  createLiveAdapter,
  fetchBootstrap,
  logout,
  type Bootstrap,
} from "./adapters/live";
import "./styles.css";
import "./live.css";
import "./product.css";
import "./m4.css";

type State =
  | { kind: "loading" }
  | { kind: "ready"; boot: Bootstrap }
  | { kind: "signed-out"; reason: string }
  | { kind: "unreachable"; message: string; retryIn: number }
  | { kind: "invalid"; message: string };

export const liveClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: false,
      staleTime: 15000,
      refetchOnWindowFocus: false,
      refetchOnReconnect: true,
      gcTime: 300000,
    },
  },
});

function Gate({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <main id="content" className="gate" tabIndex={-1}>
      <section className="panel empty" role="alert">
        <h1>{title}</h1>
        {children}
      </section>
    </main>
  );
}

export function LiveRoot({ client = liveClient }: { client?: QueryClient }) {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [generation, setGeneration] = useState(0);
  const attempts = useRef(0);
  const expire = useCallback(
    (reason: string) => {
      client.cancelQueries();
      client.clear();
      setState({ kind: "signed-out", reason });
    },
    [client],
  );
  const adapter = useMemo(
    () =>
      createLiveAdapter(() => expire("Your session ended or was signed out.")),
    [expire],
  );
  const load = useCallback(async () => {
    try {
      const boot = await fetchBootstrap(() => undefined);
      attempts.current = 0;
      setState({ kind: "ready", boot });
    } catch (e) {
      if (e instanceof SessionExpired) expire("Sign in to continue.");
      else if (e instanceof BackendUnavailable) {
        const retryIn = Math.min(30, 2 ** attempts.current * 2);
        attempts.current += 1;
        setState({ kind: "unreachable", message: e.message, retryIn });
      } else
        setState({
          kind: "invalid",
          message: e instanceof Error ? e.message : "Bootstrap failed",
        });
    }
  }, [expire]);
  useEffect(() => {
    void load();
  }, [load, generation]);
  // Reconnect: back off while unreachable; retry immediately when online.
  useEffect(() => {
    if (state.kind !== "unreachable") return;
    const timer = setTimeout(
      () => setGeneration((g) => g + 1),
      state.retryIn * 1000,
    );
    const online = () => setGeneration((g) => g + 1);
    window.addEventListener("online", online);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("online", online);
    };
  }, [state]);
  // Session expiry: clear locally at the cookie's expiry, before any 401.
  useEffect(() => {
    if (state.kind !== "ready" || !state.boot.session.expires_at) return;
    const ms = new Date(state.boot.session.expires_at).getTime() - Date.now();
    if (!Number.isFinite(ms)) return;
    const timer = setTimeout(
      () => expire("Your session expired."),
      Math.max(0, Math.min(ms, 2 ** 31 - 1)),
    );
    return () => clearTimeout(timer);
  }, [state, expire]);
  // A lazy chunk failed to load: if the session ended, sign out cleanly.
  useEffect(() => {
    const check = () =>
      void fetchBootstrap(() => undefined).catch((e) => {
        if (e instanceof SessionExpired)
          expire("Your session ended or was signed out.");
      });
    window.addEventListener(CHUNK_ERROR, check);
    return () => window.removeEventListener(CHUNK_ERROR, check);
  }, [expire]);
  const signOut = useCallback(async () => {
    await logout();
    expire("You signed out.");
  }, [expire]);
  if (state.kind === "loading")
    return (
      <div className="empty" role="status">
        Connecting to the Luffy backend…
      </div>
    );
  if (state.kind === "signed-out")
    return (
      <Gate title="Signed out">
        <p data-testid="signed-out">
          {state.reason} Nothing from the previous session is kept on this page.
        </p>
        <button
          className="primary"
          onClick={() => {
            history.replaceState(null, "", location.pathname + location.hash);
            location.reload();
          }}
        >
          Sign in
        </button>
      </Gate>
    );
  if (state.kind === "unreachable")
    return (
      <Gate title="Backend unreachable">
        <p data-testid="unreachable">
          {state.message} No data is shown. Retrying in {state.retryIn}s.
        </p>
        <button onClick={() => setGeneration((g) => g + 1)}>Retry now</button>
      </Gate>
    );
  if (state.kind === "invalid")
    return (
      <Gate title="Cannot start">
        <p data-testid="invalid">{state.message}</p>
        <button onClick={() => setGeneration((g) => g + 1)}>Retry</button>
      </Gate>
    );
  return (
    <PreviewProvider adapter={adapter} session={state.boot} signOut={signOut}>
      <Shell />
    </PreviewProvider>
  );
}

export function mountLive() {
  document.title = "LUFFY · Owner workspace · LIVE";
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <QueryClientProvider client={liveClient}>
        <LiveRoot />
      </QueryClientProvider>
    </React.StrictMode>,
  );
}
