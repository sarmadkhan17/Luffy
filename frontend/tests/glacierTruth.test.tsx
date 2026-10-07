import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PreviewProvider } from "../src/context";
import { fixtureAdapter } from "../src/adapters/fixture";
import type { Bootstrap } from "../src/adapters/live";
import { accountLabel, dashboardLabel, ProviderTruth, RuntimeLabels } from "../src/components/RuntimeTruth";
import { StorageTruth } from "../src/components/StorageTruth";
import { StrategyAuthority } from "../src/components/StrategyAuthority";
import { OwnerControls } from "../src/components/OwnerControls";
import Research, { refereeWording, verdictOf } from "../src/views/Research";
import Diagnostics from "../src/views/Diagnostics";
import OperationsLog from "../src/views/OperationsLog";
import LiveSystem from "../src/views/LiveSystem";
import { readContractIssue } from "../src/adapters/readContracts";

const session = { mode: "LIVE", backend: { commit: null, frontend_build: null, started_at: "2026-10-07T00:00:00Z", source_time: "2026-10-07T00:00:00Z" }, dashboard_mode: "READ_ONLY_GUI", runtime_truth: { account: { venue: null, environment: null, observed_at_ms: null, snapshot_id: null, freshness: "unavailable", source: "test" }, brain: { provider_state: "DISABLED", external_llm: "UNAVAILABLE", response_mode: "EXTERNAL_CHAT_UNAVAILABLE", source: "config.brain.enabled" } } } as Bootstrap;
function setup(ui: React.ReactNode, data: Record<string, any> = {}, mode = session) {
  const adapter = { ...fixtureAdapter, mode: "LIVE" as const, overview: data.overview ? vi.fn(async () => data.overview) : fixtureAdapter.overview, ownerRead: vi.fn(async (path: string) => data[path] ?? {}), ownerInterface: vi.fn(async () => ({ availability: "AVAILABLE" as const })), control: vi.fn() };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><PreviewProvider adapter={adapter as any} session={mode}>{ui}</PreviewProvider></QueryClientProvider>);
  return adapter;
}
describe("runtime truth", () => {
  it("never derives trading/account mode from adapter LIVE", () => {
    expect(dashboardLabel("READ_ONLY_GUI")).toContain("READ-ONLY");
    expect(dashboardLabel("LIVE")).toBe("LIVE GUI");
    expect(dashboardLabel(undefined)).toContain("UNKNOWN");
    expect(accountLabel(null, 1000)).toBe("account environment unavailable");
    expect(accountLabel({ venue: "venue-A", environment: "testnet", freshness: "fresh", observed_at_ms: 1000 }, 1000)).toBe("venue-A · testnet account");
    expect(accountLabel({ venue: "venue-B", environment: "production", freshness: "fresh", observed_at_ms: 1000 }, 1000)).toBe("venue-B · production account");
    expect(accountLabel({ venue: "binance", environment: "production", freshness: "fresh", observed_at_ms: 1000 }, 62000)).toContain("unavailable");
    expect(accountLabel({ venue: "binance", environment: "production", freshness: "stale", observed_at_ms: 1000 }, 1000)).toContain("unavailable");
  });
  it("renders provider-disabled local evidence without claiming external availability", async () => {
    setup(<ProviderTruth />, { bootstrap: session });
    expect(await screen.findByTestId("provider-state")).toHaveTextContent("Provider: DISABLED");
    expect(screen.getByTestId("provider-state")).toHaveTextContent("External LLM: UNAVAILABLE");
    expect(screen.getByTestId("provider-state")).toHaveTextContent("Typed/local evidence reads");
  });
  it("shows independent Kernel and control states", async () => {
    setup(<RuntimeLabels kernel="STOPPED" control="FROZEN" />, { bootstrap: session });
    expect(await screen.findByTestId("runtime-labels")).toHaveTextContent("READ_ONLY_GUI · account environment unavailable · Kernel STOPPED · Control FROZEN");
  });
  it("disables owner controls even if a health response claims AVAILABLE", async () => {
    const adapter = setup(<OwnerControls control="FROZEN" positions={[]} />);
    for (const name of ["Resume", "Freeze", "Halt", "PANIC"]) {
      const button = await screen.findByRole("button", { name });
      expect(button).toBeDisabled(); fireEvent.click(button);
    }
    expect(adapter.control).not.toHaveBeenCalled();
  });
});
describe("research truth", () => {
  it("distinguishes disabled, enabled without look, registered and unavailable", () => {
    expect(refereeWording(false, 0)).toContain("Referee disabled");
    expect(refereeWording(true, 0)).toContain("Referee enabled · no registered");
    expect(refereeWording(false, 2)).toContain("2 registered held-out look(s) exist");
    expect(refereeWording(undefined, null)).toContain("state unavailable");
    expect(verdictOf({})).toBe("UNAVAILABLE");
  });
  it("renders a nonempty bank without none-yet wording", async () => {
    setup(<Research />, { "research/board": { combos: [], total: 10, verdicts: [], candidates_by_state: { queued: 3 }, stores: { registrations: 2 }, held_out_looks: { n: 2 }, referee_enabled: false } });
    expect(await screen.findByText("queued: 3")).toBeVisible();
    expect(screen.queryByText(/none yet/)).toBeNull();
    expect(screen.getByTestId("referee-status")).toHaveTextContent("2 registered held-out look(s) exist");
  });
});
it("renders exact-version lifecycle and keeps unknown economics unavailable", () => {
  render(<StrategyAuthority errors={{}} versions={[{ strategy_id: "s1", version_id: "v1", version: { spec: { name: "Test Strategy" }, spec_hash: "spec-hash", lineage_sha256: "lineage-hash", parent_version_id: "v0" }, governor: { to_state: "PAUSED", transition_id: "transition1", reason_code: "test reason" }, approval: { status: "STALE", validity: "STALE", affected_object: "v1", item_id: "request1" } }]} />);
  expect(screen.getByRole("table", { name: "Strategy versions" })).toHaveTextContent("PAUSED");
  fireEvent.click(screen.getByRole("button", { name: /Test Strategy/ }));
  expect(screen.getByRole("dialog")).toHaveTextContent("spec-hash");
  expect(screen.getByRole("dialog")).toHaveTextContent("lineage-hash");
  expect(screen.getByRole("dialog")).toHaveTextContent("UNAVAILABLE — no current exact economics");
  expect(screen.getByRole("dialog")).toHaveTextContent("STALE");
});
it("shows root/data/recovery separately and cannot turn missing growth into zero", () => {
  render(<StorageTruth storage={{ filesystems: [{ label: "LUFFY data", path: "/data", total: 1000, used: 990, free: 10, percent_free: 1, mount: "/data", source: "device" }], files: [{ file: "data/luffy.db", bytes: null, modified_at: null }] }} />);
  const table = screen.getByRole("table", { name: "Filesystem capacity" });
  expect(table).toHaveTextContent("Root /"); expect(table).toHaveTextContent("Recovery");
  expect(table).toHaveTextContent("LOW FREE SPACE"); expect(table).toHaveTextContent("NOT REPORTED");
  expect(screen.getByTestId("growth-baseline")).toHaveTextContent("Growth baseline unavailable");
  expect(screen.getByRole("table", { name: "LUFFY database files" })).toHaveTextContent("NOT REPORTED");
});
it("discloses operation window, rollups, cooldown omission and truncation", async () => {
  setup(<OperationsLog />, { "operations/log": { events: [], window_days: 7, window_start: "2026-10-01T00:00:00Z", window_end: "2026-10-07T00:00:00Z", rolled_up_records: 5, signal_cooldowns_not_listed: 8, truncated: true } });
  await waitFor(() => expect(screen.getByTestId("operations-disclosure")).toHaveTextContent("Rolled-up occurrences (extra scan passes / safety repeats): 5"));
  expect(screen.getByTestId("operations-disclosure")).toHaveTextContent("counted, not listed: 8");
  expect(screen.getByTestId("operations-disclosure")).toHaveTextContent("Truncation: YES");
});
it("current architecture has explicit boundaries and does not observe edges from counts", async () => {
  setup(<LiveSystem />, { system: { nodes: [], edges: [] }, "system/observed": { flows: [{ source: "risk", target: "execution", count: 100, status: "observed" }] } });
  expect(await screen.findByRole("button", { name: "StrategyVersion — DECLARED ARCHITECTURE" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Opportunity Context — DECLARED ARCHITECTURE" }));
  expect(screen.getByRole("dialog")).toHaveTextContent("CASH");
  expect(screen.getByRole("dialog")).toHaveTextContent("supporting and opposing evidence");
  expect(screen.getByRole("dialog")).toHaveTextContent("opaque score are not trading authority");
  expect(document.querySelectorAll(".pk")).toHaveLength(0);
});
it("rejects malformed authority projections instead of presenting healthy empty state", () => {
  expect(readContractIssue("strategies/board", { strategies: [], versions: "healthy", authority_errors: {} })).toContain("versions");
  expect(readContractIssue("bootstrap", { dashboard_mode: "LIVE", runtime_truth: { account: {}, brain: {} } })).toContain("account.venue");
});

it("diagnostics keeps missing cycle work, unknown Kernel and unreported permission unverified", async () => {
  setup(<Diagnostics />, { overview: { control: "FROZEN", positions: [], live: { heartbeat: { freshness: "fresh" }, kernelState: "UNKNOWN", observedEvidence: { heartbeat: {}, kernel_process: { state: "UNKNOWN" } } } }, diagnostics: { storage: { files: [], filesystems: [] }, probes: [], incidents: [], collectors: [], watchdog: { disabled_flag: false } } });
  await waitFor(() => expect(screen.getByRole("region", { name: "Safety checklist" })).toHaveTextContent("Kernel process running — not reported"));
  expect(screen.getByRole("region", { name: "Safety checklist" })).toHaveTextContent("Safe to open new trades — not reported");
  const cycle = screen.getByRole("button", { name: /Cycle freshness/ });
  expect(cycle).toHaveTextContent("Not reported");
  expect(cycle).not.toHaveTextContent("Healthy");
});

it.each([
  ["history without proof", "fresh", null, "UNKNOWN"],
  ["stale proof", "stale", true, "STALE"],
  ["current match", "fresh", true, "MATCHED"],
  ["current mismatch", "fresh", false, "MISMATCH"],
] as const)("renders reconciliation %s independently of run count", async (_, freshness, reconciliation, status) => {
  const at = new Date(Date.now() - (freshness === "stale" ? 46 * 3600000 : 1000)).toISOString();
  setup(<Diagnostics />, {
    overview: { control: "FROZEN", positions: [], live: { observedEvidence: { protection: { supervisor: { observed_at: at, age_s: freshness === "stale" ? 46 * 3600 : 1, freshness, stale_after_s: 180, checks_are_current: freshness === "fresh", reconciliation, reasons: ["heartbeat_recovery"] } } } } },
    diagnostics: { incidents: [{event:"reconcile",ts:at,actor:"kernel"}], collectors: [] },
  });
  const tile = await screen.findByRole("button", {name:/^Reconcile/});
  await waitFor(() => expect(tile).toHaveTextContent(status));
  expect(tile).toHaveTextContent("Historical activity: 1 reconciliation run");
  if (freshness === "stale") {
    const recovery = screen.getByRole("button", {name:/^Recovery mode/});
    expect(recovery).toHaveTextContent("STALE"); expect(recovery).toHaveTextContent("46 h old"); expect(recovery).not.toHaveTextContent("ACTIVE");
  }
});
