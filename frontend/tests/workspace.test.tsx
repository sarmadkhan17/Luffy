import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RecordTable, timestamp, value } from "../src/components/workspace";
import { createLiveAdapter, ContractViolation } from "../src/adapters/live";
import { vi } from "vitest";
describe("Owner V2 record workspaces", () => {
  it("keeps missing values distinct from numeric zero and invalid source times", () => {
    expect(value(null)).toBe("Unavailable");
    expect(value(0)).toBe("0");
    expect(timestamp("invalid")).toBe("Source time unavailable");
  });
  it("filters records and exposes original fields through a keyboard accessible drawer", async () => {
    const u = userEvent.setup();
    render(
      <RecordTable
        title="Trades"
        rows={[
          { id: "one", symbol: "BTC", status: "open", pnl: null },
          { id: "two", symbol: "SOL", status: "closed", pnl: 0 },
        ]}
        filterKey="status"
        columns={[
          ["symbol", "Instrument"],
          ["pnl", "P&L"],
        ]}
      />,
    );
    await u.selectOptions(screen.getByLabelText("Filter Trades"), "closed");
    expect(screen.queryByText("BTC")).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Inspect SOL" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("two");
    await u.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("rejects GraphQL partial/error results instead of treating them as empty decisions", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue({
          ok: true,
          status: 200,
          json: async () => ({
            data: { decisions: [] },
            errors: [{ message: "failure" }],
          }),
        }),
    );
    await expect(
      createLiveAdapter(() => {}).decisions!(new AbortController().signal),
    ).rejects.toBeInstanceOf(ContractViolation);
    vi.unstubAllGlobals();
  });
  it("rejects malformed investigation contracts", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue({
          ok: true,
          status: 200,
          json: async () => ({ status: "ok", health: null, cases: [] }),
        }),
    );
    await expect(
      createLiveAdapter(() => {}).investigations!(new AbortController().signal),
    ).rejects.toBeInstanceOf(ContractViolation);
    vi.unstubAllGlobals();
  });
});
