import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PreviewProvider } from "../src/context";
import { useOwnerTelemetry } from "../src/useOwnerTelemetry";
import { mapSystem } from "../src/adapters/live";
import HealthStatus, { healthPresentation } from "../src/components/HealthStatus";
import type { OwnerAdapter } from "../src/adapters/contracts";
const at = "2026-10-06T00:00:00Z";
const system = (status = "RUNNING", health = "unknown", freshness = "fresh") => ({
  generated_at: at, architecture_source: "declared source map", edges: [], nodes: [{
    id: "kernel", label: "Kernel", source_file: "kernel.py", configured_enabled: false,
    telemetry: { observed_at: at, age_s: 0, stale_after_s: 240, freshness, health,
      status_label: status, source: "heartbeat and process evidence", summary: "observed report" },
  }],
});
afterEach(() => vi.useRealTimers());
it.each(["RUNNING", "STOPPED", "WAITING", "FAILED", "DISABLED", "UNKNOWN"])(
  "renders %s separately from configured permission", (state) => {
    vi.useFakeTimers(); vi.setSystemTime(new Date(at));
    const node = mapSystem(system(state)).nodes[0];
    render(<HealthStatus node={node} />);
    expect(screen.getByText(state)).toBeVisible();
    expect(healthPresentation(node).tone).not.toBe("active");
    expect(node.evidence.summary).toContain("Configured: intentionally disabled");
  });
it("cached observed activity expires on the browser clock without a poll", () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(at));
  const value = system("", "active");
  value.nodes[0].id = "dashboard";
  const node = mapSystem(value).nodes[0];
  expect(healthPresentation(node).primary).toBe("ACTIVE");
  vi.advanceTimersByTime(240001);
  expect(healthPresentation(node)).toEqual({ primary: "STALE", tone: "stale", previous: "ACTIVE" });
});
it("missing/future time and UNKNOWN health never become ACTIVE", () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(at));
  const value = system("UNKNOWN");
  value.nodes[0].telemetry.observed_at = "bad";
  expect(healthPresentation(mapSystem(value).nodes[0]).primary).toBe("STALE");
  const unavailable = mapSystem(system("UNKNOWN", "active", "invalid")).nodes[0];
  expect(healthPresentation(unavailable).primary).toBe("UNAVAILABLE");
  const noTelemetry = { ...unavailable, hasTelemetry: false };
  expect(healthPresentation(noTelemetry).primary).toBe("NO TELEMETRY");
  const future = system("", "active");
  future.nodes[0].telemetry.observed_at = "2026-10-07T00:00:00Z";
  expect(healthPresentation(mapSystem(future).nodes[0]).primary).toBe("STALE");
  const unknown = mapSystem(system("UNKNOWN", "active")).nodes[0];
  expect(healthPresentation(unknown).tone).toBe("unknown");
  expect(mapSystem(system("", "active")).nodes[0].health).toBe("unknown");
});
it("IPC availability expires while polling fails to return, then fails closed on error", async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date(at));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let reject!: (e: Error) => void;
  const adapter = { mode: "LIVE", ownerInterface: vi.fn(() => new Promise((_resolve, r) => { reject = r; })) } as unknown as OwnerAdapter;
  client.setQueryData(["owner-interface"], { availability: "AVAILABLE", observedAt: at,
    controlState: "ACTIVE", recoveryInProgress: false, reasons: [] });
  const graph = mapSystem(system());
  graph.nodes[0].id = "owner-interface";
  function Probe() {
    const d = useOwnerTelemetry(graph, true);
    return <HealthStatus node={d.nodes[0]} />;
  }
  render(<QueryClientProvider client={client}><PreviewProvider adapter={adapter}><Probe /></PreviewProvider></QueryClientProvider>);
  expect(screen.getByText("AVAILABLE")).toBeVisible();
  await act(async () => { vi.advanceTimersByTime(61000); });
  expect(screen.getByText("STALE")).toBeVisible();
  expect(screen.getByText("Last reported: AVAILABLE")).toBeVisible();
  await act(async () => { reject(new Error("disconnected")); await Promise.resolve(); });
  await act(async () => { vi.advanceTimersByTime(1000); });
  expect(screen.getByText("UNAVAILABLE")).toBeVisible();
  expect(screen.queryByText("AVAILABLE")).toBeNull();
  client.clear();
});
it("cached Overview account, work and owner-attention evidence expire independently of permission", async () => {
  const { expireOverviewTelemetry } = await import("../src/telemetryExpiry");
  const { mapOverview } = await import("../src/adapters/live");
  vi.useFakeTimers(); vi.setSystemTime(new Date(at));
  const observation = { observed_at: at, age_s: 0, freshness: "fresh", stale_after_s: 1 };
  const value = mapOverview({ mode: "LIVE", generated_at: at, errors: {},
    control: { state: "ACTIVE", observed_at: at },
    account: { ...observation, equity: 100, currency: "USDT" },
    heartbeat: { ...observation, runtime_state: "RUNNING" },
    needs_you: { ...observation, needs_owner: false, reasons: [] },
  });
  const expired = expireOverviewTelemetry(value, Date.now() + 1001);
  expect(expired.control).toBe("ACTIVE");
  expect(expired.live?.kernelState).toBe("STALE");
  expect(expired.provenance.freshness).toBe("stale");
  expect(expired.live?.needsYou?.provenance.freshness).toBe("stale");
  const disconnected = expireOverviewTelemetry(value, Date.now(), true);
  expect(disconnected.live?.kernelState).toBe("UNKNOWN");
  expect(disconnected.live?.heartbeat?.freshness).toBe("unavailable");
  expect(disconnected.provenance.freshness).toBe("unavailable");
  expect(disconnected.equity).toBe(100); // retained figure is explicitly historical
});
