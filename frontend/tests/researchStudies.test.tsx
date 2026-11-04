import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi } from "vitest";
import { PreviewProvider } from "../src/context";
import { ResearchStudies } from "../src/components/ResearchStudies";
import type { OwnerAdapter } from "../src/adapters/contracts";

function mount(records: unknown[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ownerRead = vi.fn().mockResolvedValue({ records, unavailable: [{ reason: "store absent" }] });
  const approvalDecision = vi.fn();
  render(<QueryClientProvider client={client}><PreviewProvider adapter={{ mode: "LIVE", ownerRead, approvalDecision } as unknown as OwnerAdapter}><ResearchStudies /></PreviewProvider></QueryClientProvider>);
  return { ownerRead, approvalDecision };
}
describe("compositional research visibility", () => {
  it("shows the attempted question, actual result and timings without activation controls", async () => {
    const spies = mount([{ kind: "closure_research", value: { studies: [{ study_id: "s1", status: "IN_PROGRESS", stop_reason: "WAITING_FOR_DATA",
      development_mode: "RETROSPECTIVE", units: [{ unit: "develop:a", kind: "develop", status: "WAITING_DATA", elapsed_s: 18.5, reason: "TradingView history missing" }],
      candidates: [{ result_id: "r1", question: "Does funding and liquidity improve the hedge?", outcome: "BLOCKED_DATA", reason: "No historical basis", structure: "hedged" }] }] } }]);
    expect(await screen.findByText("Does funding and liquidity improve the hedge?")).toBeVisible();
    const activity = screen.getByText(/Study activity · stage timings/).closest("details")!;
    activity.open = true; fireEvent(activity, new Event("toggle"));
    expect(screen.getByText("18.5")).toBeVisible();
    expect(screen.getByText(/TradingView history missing/)).toBeVisible();
    expect(screen.getByText(/No historical basis/)).toBeVisible();
    expect(screen.getByText(/independent qualification and trading activation are separate/)).toBeVisible();
    expect(spies.ownerRead.mock.calls[0][0]).toBe("query/research?identity=closure-studies");
    expect(spies.approvalDecision).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: /approve|activate|resume/i })).toBeNull();
  });
  it("does not turn an unavailable store into no studies", async () => {
    mount([]);
    expect(await screen.findByText(/Research study evidence unavailable/)).toBeVisible();
    expect(screen.queryByText(/No study has been recorded/)).toBeNull();
  });
  it("opens the exact stored section on request, pages it, and shows evaluated/lineage/hedge states without authority controls", async () => {
    const detail = (part: number) => ({ records: [{ kind: "closure_candidate", value: { status: "AVAILABLE", section: "evaluated", part, parts: 2,
      run_id: "b".repeat(64), chars: 70000, sha256: "abcdef0123456789", text: part === 0 ? '{"combo":{"long":"close > 1"' : ',"short":null}}' } }], unavailable: [] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const ownerRead = vi.fn().mockImplementation(async (raw: string) => {
      const identity = decodeURIComponent(raw);
      if (identity.includes("closure-candidate:")) return { records: [{ kind: "closure_candidate", value: { status: "AVAILABLE", text: JSON.stringify({ total: 2, offset: 0, more_results: false, items: [{ run_id: "b".repeat(64), outcome: "HEDGE_SUPPORTED" }, { run_id: "c".repeat(64), outcome: "NEGATIVE_ECONOMICS" }] }) } }], unavailable: [] };
      if (identity.includes("closure-run:")) return detail(identity.endsWith(":1") ? 1 : 0);
      return { records: [{ kind: "closure_research", value: { studies: [{ study_id: "s1", status: "IN_PROGRESS", stop_reason: "WAITING_FOR_DATA", units: [],
          qualifications: [{ qualification_id: "q1", kind: "hedge_protective", status: "ASSESSED_SUPPORTED", statistical_admission: "NONE_REGISTERED", reason: "risk cut", gaps: [] },
            { qualification_id: "q2", kind: "alpha", status: "QUALIFIED", authority_class: "CONFIGURED", reason: "referee passed" }],
          candidates: [{ result_id: "r1", candidate_id: "c1", question: "Does the hedge cut drawdown?", outcome: "HEDGE_SUPPORTED", reason: "ok", structure: "hedged",
            evaluated: { combo: { long: "close > 1" } }, lineage: [{ kind: "revision", parent: "p0" }], hedge: { per_symbol: { A: { alignment: { bars: 10 } } } } }] }] } }], unavailable: [] };
    });
    render(<QueryClientProvider client={client}><PreviewProvider adapter={{ mode: "LIVE", ownerRead, approvalDecision: vi.fn() } as unknown as OwnerAdapter}><ResearchStudies /></PreviewProvider></QueryClientProvider>);
    expect(await screen.findByText("Does the hedge cut drawdown?")).toBeVisible();
    expect(screen.getByText("Evaluated formula, fit window and thresholds")).toBeVisible();
    expect(screen.getByText("Revision lineage")).toBeVisible();
    expect(screen.getByText("Hedge development result and alignment")).toBeVisible();
    expect(screen.getByText(/Protective-hedge risk assessment: ASSESSED SUPPORTED/)).toBeVisible();
    expect(screen.getByText(/Independent alpha qualification: QUALIFIED/)).toBeVisible();          // an alpha packet is NOT labelled "assessment only"
    expect(screen.getAllByTestId("qualification-packet")).toHaveLength(2);
    const details = screen.getByTestId("candidate-detail-c1") as HTMLDetailsElement;
    details.open = true; fireEvent(details, new Event("toggle"));
    expect(await screen.findByText(/part 1 of 2/)).toBeVisible();
    expect(ownerRead).toHaveBeenCalledWith(`query/research?identity=closure-run%3A${"b".repeat(64)}%3Aevaluated%3A0`, expect.anything());
    fireEvent.click(screen.getByRole("button", { name: "Next part" }));
    await waitFor(() => expect(screen.getByText(/part 2 of 2/)).toBeVisible());
    expect(ownerRead).toHaveBeenCalledWith(`query/research?identity=closure-run%3A${"b".repeat(64)}%3Aevaluated%3A1`, expect.anything());
    expect(await screen.findByText("close > 1")).toBeVisible();
    expect(document.querySelector("pre")).toBeNull();
    fireEvent.change(screen.getByRole("combobox", { name: "Research result" }), { target: { value: "c".repeat(64) } });
    await waitFor(() => expect(ownerRead).toHaveBeenCalledWith(`query/research?identity=closure-run%3A${"c".repeat(64)}%3Aevaluated%3A0`, expect.anything()));
    expect(screen.queryByRole("button", { name: /approve|activate|resume|qualify/i })).toBeNull();
  });
});
