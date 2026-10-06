import { render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import { EconomicsEvidence } from "../src/views/LiveReads";
import { readContractIssue } from "../src/adapters/readContracts";

it("renders journal, venue and derived statuses and source coverage separately", () => {
  const field = (name: string, value: unknown, status: string) => ({
    field: name, value, status, source: "retained evidence", version: "snapshot-123",
    calculation_version: null, coverage: { complete: false, bars: 8, expected: 10 },
  });
  render(<EconomicsEvidence economics={{ schema_version: "trade-economics-read.v1",
    venue_monetary: [field("funding", null, "UNAVAILABLE")],
    journal_booked: [field("realized_pnl", 999, "JOURNAL_BOOKED")],
    derived: [field("MFE", 0, "DERIVED_RECORDED")],
  }} />);
  const venue = screen.getByRole("table", { name: "Venue monetary fields" });
  expect(within(venue).getByText("UNAVAILABLE")).toBeInTheDocument();
  expect(within(venue).queryByText("999")).toBeNull();
  expect(screen.getByRole("table", { name: "Journal bookings" })).toHaveTextContent("JOURNAL_BOOKED");
  const derived = screen.getByRole("table", { name: "Derived measurements" });
  expect(derived).toHaveTextContent("DERIVED_RECORDED");
  expect(derived).toHaveTextContent("snapshot-123");
  expect(derived).toHaveTextContent('"complete":false');
  expect(screen.getByText(/Whole-trade cashflow accounting is unavailable/)).toBeInTheDocument();
});

it("rejects economics without coverage at the adapter boundary", () => {
  const d = { trade: { id: "t" }, decision: null, strategy: null, cycle: null,
    votes: [], votes_basis: null, chain: [], links: {}, accounting: { receipts: [] },
    outcome: null, economics: { schema_version: "trade-economics-read.v1",
      whole_trade_complete: false, journal_booked: [], derived: [],
      venue_monetary: [{ field: "commission", value: 1, status: "RECORDED_VENUE_FIELD",
        source: "trade_fills", version: "snapshot", calculation_version: null }],
    },
  };
  expect(readContractIssue("trades/t/lineage", d)).toContain("coverage");
});
