import { useEffect, useState } from "react";
import { usePreview } from "../context";
import { useRead, rec } from "./records";

export function accountLabel(account: Record<string, unknown> | null, now = Date.now()): string {
  const at = account?.observed_at_ms;
  if (account?.freshness !== "fresh" || typeof at !== "number" || now < at || now - at > 60000)
    return "account environment unavailable";
  const env = account.environment;
  const venue = typeof account.venue === "string" ? account.venue : "venue unavailable";
  return `${venue} · ${env === "production" ? "production" : env === "demo" || env === "testnet" || env === "live" ? env : "unknown environment"} account`;
}
export function dashboardLabel(mode: unknown): string {
  return mode === "READ_ONLY_GUI" ? "READ-ONLY · READ_ONLY_GUI" : mode === "LIVE" ? "LIVE GUI" : "dashboard mode UNKNOWN";
}
export function useRuntimeTruth() {
  const { adapter, session } = usePreview();
  const q = useRead(adapter.mode === "LIVE" ? "bootstrap" : null, 30000);
  // Session can seed the initial read only; an unsuccessful poll invalidates it.
  const data = q.isError ? null : q.data ?? session;
  return { data, truth: rec(data?.runtime_truth) };
}
export function RuntimeLabels({ kernel, control }: { kernel: string; control: string }) {
  const { adapter } = usePreview();
  const { data, truth } = useRuntimeTruth();
  const [now, setNow] = useState(Date.now);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  if (adapter.mode !== "LIVE") return <span>FIXTURE · synthetic account · Kernel {kernel} · Control {control}</span>;
  return <span data-testid="runtime-labels" title={`${rec(truth?.account)?.source ?? "Source UNAVAILABLE"} · snapshot ${rec(truth?.account)?.snapshot_id ?? "UNAVAILABLE"} · observed at ${rec(truth?.account)?.observed_at_ms ?? "UNAVAILABLE"} · freshness ${rec(truth?.account)?.freshness ?? "UNAVAILABLE"}`}>{dashboardLabel(data?.dashboard_mode)} · {accountLabel(rec(truth?.account), now)} · Kernel {kernel} · Control {control}</span>;
}
export function ProviderTruth() {
  const { truth } = useRuntimeTruth();
  const brain = rec(truth?.brain);
  return <p className="gl-note" data-testid="provider-state">Provider: {String(brain?.provider_state ?? "UNKNOWN")} · External LLM: {String(brain?.external_llm ?? "UNKNOWN")}. {brain?.response_mode === "EXTERNAL_CHAT_UNAVAILABLE" ? "Provider-disabled boundary enforced. Typed/local evidence reads remain available; external chat replies are unavailable." : "Response mode not established."} Conversation has no trading authority.</p>;
}
