import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PreviewProvider } from "../src/context";
import { fixtureAdapter } from "../src/adapters/fixture";
import type { OwnerAdapter } from "../src/adapters/contracts";
import type { Bootstrap } from "../src/adapters/live";
import Trades from "../src/views/Trades";
import Strategies from "../src/views/Strategies";
import Research from "../src/views/Research";
import Knowledge from "../src/views/Knowledge";
import Overview from "../src/views/Overview";
import Luffy from "../src/views/Luffy";
import { OwnerControls } from "../src/components/OwnerControls";
import Shell from "../src/Shell";
import { createLiveAdapter } from "../src/adapters/live";
import { NeedsYou } from "../src/components/OwnerEvidence";
vi.mock("../src/fonts", () => ({}));

const session = { dashboard_mode: "LIVE", backend: { commit: null, frontend_build: null } } as Bootstrap;
afterEach(() => { history.replaceState(null, "", "/"); vi.restoreAllMocks(); });
function setup(ui: React.ReactNode, partial: Partial<OwnerAdapter> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const adapter = { ...fixtureAdapter, mode: "LIVE", ownerRead: vi.fn(async () => ({})), ...partial } as OwnerAdapter;
  render(<QueryClientProvider client={client}><PreviewProvider adapter={adapter} session={session}>{ui}</PreviewProvider></QueryClientProvider>);
  return { client, adapter };
}
it.each([
  ["#trades?trade=trade%20one", Trades, "trades/trade%20one/lineage", "trade-story"],
  ["#strategies?id=strategy%20one", Strategies, "strategies/strategy%20one", "strategy-workspace"],
  ["#research?combo=combo%20one", Research, "research/combos/combo%20one", "research-item"],
  ["#knowledge?note=note%20one.md", Knowledge, "knowledge/note?id=note%20one.md", "linked-note"],
] as const)("opens recorded evidence at %s", async (hash, View, path, testid) => {
  history.replaceState(null, "", hash);
  const ownerRead = vi.fn(async () => ({}));
  setup(<View />, { ownerRead });
  await waitFor(() => expect(ownerRead).toHaveBeenCalledWith(path, expect.any(AbortSignal)));
  expect(screen.getByTestId(testid)).toBeVisible();
  expect(screen.getByTestId(testid).querySelector('a[href="' + hash.split("?")[0] + '"]')).toBeTruthy();
});
it("applies a recorded strategy family link to the registry", async () => {
  history.replaceState(null, "", "#strategies?family=spec");
  const ownerRead = vi.fn(async () => ({ strategies: [{ id: "spec-one", name: "Spec one", kind: "spec", closed_trades: 0 }, { id: "genome-one", name: "Genome one", kind: "genome", closed_trades: 0 }] }));
  setup(<Strategies />, { ownerRead });
  expect(await screen.findByRole("button", { name: /Spec one/ })).toBeVisible();
  expect(screen.queryByRole("button", { name: /Genome one/ })).toBeNull();
});
it("updates an evidence destination within the same route", async () => {
  history.replaceState(null, "", "#strategies?id=first");
  const ownerRead = vi.fn(async () => ({}));
  setup(<Strategies />, { ownerRead });
  await waitFor(() => expect(ownerRead).toHaveBeenCalledWith("strategies/first", expect.any(AbortSignal)));
  history.replaceState(null, "", "#strategies?id=second");
  fireEvent(window, new HashChangeEvent("hashchange"));
  await waitFor(() => expect(ownerRead).toHaveBeenCalledWith("strategies/second", expect.any(AbortSignal)));
  expect(screen.getByTestId("strategy-workspace")).toHaveTextContent("second");
});
it("links an Overview signal to its exact Operations decision", async () => {
  const ownerRead = vi.fn(async (path: string) => path === "desk" ? { scan: { observed_at: new Date().toISOString(), traded: 0, coins: [{ decision_id: "decision one", symbol: "SOL/USDT", action: "LONG", score: 0.2, threshold: 0.5, confidence: null, executed: false, skip_reason: "below threshold" }] } } : {});
  setup(<Overview />, { ownerRead });
  fireEvent.click(await screen.findByRole("button", { name: "SOL decision detail" }));
  expect(screen.getByRole("link", { name: "Open in Operations" })).toHaveAttribute("href", "#operations?decision=decision%20one");
});
it("disables owner controls after a cached AVAILABLE health read fails to refresh", async () => {
  const ownerInterface = vi.fn().mockResolvedValueOnce({ availability: "AVAILABLE", observedAt: new Date().toISOString(), reasons: [] }).mockRejectedValue(new Error("health unavailable"));
  const control = vi.fn();
  const { client } = setup(<OwnerControls control="ACTIVE" positions={[]} />, { ownerInterface, control });
  await waitFor(() => expect(screen.getByRole("button", { name: "Freeze" })).toBeEnabled());
  await client.refetchQueries({ queryKey: ["owner-interface"] });
  await waitFor(() => expect(screen.getByRole("button", { name: "Freeze" })).toBeDisabled());
  for (const name of ["Resume", "Freeze", "Halt", "PANIC"]) {
    const button = screen.getByRole("button", { name });
    expect(button).toBeDisabled(); fireEvent.click(button);
  }
  expect(control).not.toHaveBeenCalled();
});
it("does not infer a demo venue account from a LIVE dashboard", () => {
  history.replaceState(null, "", "#unavailable");
  setup(<Shell />);
  expect(screen.getByTestId("mode-banner")).toHaveTextContent("LIVE");
  expect(screen.getByTestId("mode-banner")).not.toHaveTextContent(/demo venue|BINANCE_DEMO/i);
});
it("exposes no trading or approval mutation in the read-only adapter", () => {
  const readOnly = createLiveAdapter(() => {}, true);
  expect(readOnly.control).toBeUndefined();
  expect(readOnly.approvalDecision).toBeUndefined();
  const live = createLiveAdapter(() => {});
  expect(live.control).toBeTypeOf("function");
  expect(live.approvalDecision).toBeTypeOf("function");
});
it("keeps a valid approval disabled in the read-only interface", async () => {
  const adapter = createLiveAdapter(() => {}, true);
  adapter.ownerRead = vi.fn(async () => ({ generated_at: new Date().toISOString(), items: [{ item_id: "approval-one", binding_hash: "binding-one", action_type: "paid_spend", validity: "VALID", receipt: null }], unavailable: [] }));
  setup(<NeedsYou />, adapter);
  expect(await screen.findByRole("button", { name: "Approve exact request" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Reject exact request" })).toBeDisabled();
});
it("directs a guarded recovery item to Trades where owner controls live", async () => {
  const ownerRead = vi.fn(async () => ({ generated_at: new Date().toISOString(), items: [{ item_id: "recovery-one", action_type: "resume", decision_operation: "resume", validity: "VALID", receipt: null }], unavailable: [] }));
  setup(<NeedsYou />, { ownerRead });
  expect(await screen.findByRole("link", { name: /Guarded recovery request/ })).toHaveAttribute("href", "#trades");
});
it("directs LUFFY recovery guidance to the current owner controls", () => {
  setup(<Luffy />);
  expect(screen.getByRole("link", { name: /Open owner controls in Trades/ })).toHaveAttribute("href", "#trades");
  expect(screen.getByText(/recovery uses Trades/)).toBeVisible();
});
