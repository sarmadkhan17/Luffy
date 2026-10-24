import {render, screen} from "@testing-library/react";
import {QueryClient, QueryClientProvider} from "@tanstack/react-query";
import {describe, it, expect, vi} from "vitest";
import {PreviewProvider} from "../src/context";
import {NeedsYou} from "../src/components/OwnerEvidence";
import type {OwnerAdapter} from "../src/adapters/contracts";

const bindings = {study_id: "s".repeat(64), candidate_id: "c".repeat(64), question_id: "q1", question: "does ctx_total lead",
  requirement: "ref:tv_total", reference_key: "tv_total", provider: "TradingView", series: "CRYPTOCAP:TOTAL", tf: "1d",
  span_start_ms: 1600000000000, span_end_ms: 1614000000000, span_utc: "2020-09-13 to 2021-02-22 (UTC)", missing_reason: "stale: latest valid observation 2020-10-01", development_mode: "RETROSPECTIVE", references_store: "/data/refs.db"};
const item = {item_id: "d".repeat(64), binding_hash: "b".repeat(64), action_type: "research_data_access", affected_object: "study s / candidate c",
  reason: "Research is waiting for TradingView CRYPTOCAP:TOTAL (1d). Only this candidate waits.", validity: "VALID", receipt: null, status: "PENDING",
  decision_operation: "provide_data", required_owner_action: "Open TradingView and sign in, then provide the official chart export.",
  bindings, context_link: "#research"};
const feed = (items: unknown[]) => ({generated_at: new Date().toISOString(), schema: "owner-needs-you.v1", items, pending_total: items.length, unavailable: []});
function mount(items: unknown[], compact = false) {
  const client = new QueryClient({defaultOptions: {queries: {retry: false}}});
  const approvalDecision = vi.fn();
  render(<QueryClientProvider client={client}><PreviewProvider adapter={{mode: "LIVE", ownerRead: vi.fn().mockResolvedValue(feed(items)), approvalDecision} as unknown as OwnerAdapter}><NeedsYou compact={compact} /></PreviewProvider></QueryClientProvider>);
  return approvalDecision;
}

describe("research data-access alert in Needs You", () => {
  it("shows exact requested data and the owner action, with no approval controls", async () => {
    const decision = mount([item]);
    expect(await screen.findByText(/What to do:/)).toBeVisible();
    expect(screen.getByText(/Open TradingView and sign in/)).toBeVisible();
    expect(screen.getByText("CRYPTOCAP:TOTAL · 1d")).toBeVisible();
    expect(screen.getByTestId("data-request-span")).toHaveTextContent("2020-09-13 to 2021-02-22 (UTC)");
    expect(screen.getByText(/stale: latest valid observation/)).toBeVisible();
    expect(screen.getByText(/not an approval/)).toBeVisible();
    expect(screen.getByRole("link", {name: /research context/i})).toHaveAttribute("href", "#research");
    expect(screen.queryByRole("button", {name: /Approve exact request|Reject exact request/})).toBeNull();
    expect(decision).not.toHaveBeenCalled();
  });
  it("a resolved request is history, not a pending compact item", async () => {
    const decision = mount([{...item, status: "RESOLVED", reason: "Resolved: re-evaluated"}], true);
    await screen.findByText(/Validity last checked/);          // the query has loaded, not just the wrapper
    expect(screen.queryByTestId(`data-request-${item.item_id}`)).toBeNull();
    expect(screen.queryByText(/What to do:/)).toBeNull();
    expect(screen.queryByText(/DATA NEEDED/)).toBeNull();
    expect(screen.queryByRole("button", {name: /Approve exact request|Reject exact request/})).toBeNull();
    expect(decision).not.toHaveBeenCalled();
  });
});
