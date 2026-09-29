/** M1: bounded lazy-route loading and the LIVE read contracts. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { Suspense } from "react";
import { render, screen, act, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  lazyChunk,
  ChunkLoadError,
  CHUNK_ERROR,
  page as chunkPage,
} from "../src/lazyChunk";
import { VisualBoundary } from "../src/components/ui";
import { PreviewProvider } from "../src/context";
import { createLiveAdapter, ContractViolation } from "../src/adapters/live";
import type { OwnerAdapter } from "../src/adapters/contracts";
import {
  ResearchLive,
  TradeLineage,
  UnavailableFields,
} from "../src/views/LiveReads";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function RouteFrame({ children }: { children: React.ReactNode }) {
  return (
    <VisualBoundary
      fallback={(error, reset) => (
        <div role="alert">
          {error instanceof ChunkLoadError ? "chunk failed" : "render failed"}:{" "}
          {error.message}
          <button onClick={reset}>Retry</button>
        </div>
      )}
    >
      <Suspense fallback={<div role="status">Loading view…</div>}>
        {children}
      </Suspense>
    </VisualBoundary>
  );
}

describe("bounded lazy route loading", () => {
  it("reports a chunk that never arrives instead of loading forever", async () => {
    vi.useFakeTimers();
    const announced = vi.fn();
    window.addEventListener(CHUNK_ERROR, announced);
    const View = lazyChunk(() => new Promise<never>(() => {}), 1000);
    render(
      <RouteFrame>
        <View />
      </RouteFrame>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Loading view");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("chunk failed");
    expect(screen.getByRole("alert")).toHaveTextContent("did not arrive");
    expect(announced).toHaveBeenCalledTimes(1);
    window.removeEventListener(CHUNK_ERROR, announced);
  });

  it("Retry after a failed chunk reloads the page; it does not re-render the poisoned import", async () => {
    const reload = vi.spyOn(chunkPage, "reload").mockImplementation(() => {});
    const load = vi.fn(() => Promise.reject(new Error("network")));
    const View = lazyChunk(load);
    render(
      <RouteFrame>
        <View />
      </RouteFrame>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "could not be loaded",
    );
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(reload).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledTimes(1);
    reload.mockRestore();
  });

  it("Retry of a render error with no failed chunk resets in place, no reload", async () => {
    vi.resetModules();
    const lc = await import("../src/lazyChunk");
    const ui = await import("../src/components/ui");
    const reload = vi.spyOn(lc.page, "reload").mockImplementation(() => {});
    let fail = true;
    const Flaky = () => {
      if (fail) throw new Error("boom");
      return <p>Recovered</p>;
    };
    render(
      <ui.VisualBoundary
        fallback={(error, reset) => (
          <div role="alert">
            {error.message}
            <button onClick={reset}>Retry</button>
          </div>
        )}
      >
        <Flaky />
      </ui.VisualBoundary>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(screen.getByText("Recovered")).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
  });
});

function withAdapter(adapter: Partial<OwnerAdapter>, ui: React.ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <PreviewProvider adapter={{ mode: "LIVE", ...adapter } as OwnerAdapter}>
        {ui}
      </PreviewProvider>
    </QueryClientProvider>,
  );
}

describe("LIVE read contracts", () => {
  it("shows backend-declared gaps as UNAVAILABLE with their reasons", () => {
    render(
      <UnavailableFields
        items={[{ field: "venue_fills", reason: "no receipt verifies fills" }]}
      />,
    );
    expect(screen.getByTestId("unavailable-fields")).toHaveTextContent(
      "UNAVAILABLE Venue fills — no receipt verifies fills",
    );
  });

  it("renders a trade's recorded lineage and never fills gaps", async () => {
    const ownerRead = vi.fn().mockResolvedValue({
      generated_at: "2026-09-29T00:00:00Z",
      source: "journal",
      trade: { id: "t1", mfe_r: null },
      decision: { id: "d1", action: "BUY", scan_id: "scan_1", signals: [] },
      strategy: null,
      accounting: {
        receipts: [
          {
            receipt_id: 7,
            kind: "entry",
            integrity: "verified",
            assessment: {
              status: "unverified",
              reasons: ["funding_unattributed"],
            },
          },
        ],
      },
      outcome: null,
      unavailable: [
        { field: "strategy", reason: "trade row records no strategy_id" },
      ],
    });
    withAdapter({ ownerRead }, <TradeLineage id="t 1" />);
    expect(await screen.findByText("scan_1")).toBeInTheDocument();
    expect(ownerRead).toHaveBeenCalledWith(
      "trades/t%201/lineage",
      expect.anything(),
    );
    expect(screen.getByText("unverified")).toBeInTheDocument();
    expect(screen.getByText("funding_unattributed")).toBeInTheDocument();
    expect(screen.getByTestId("unavailable-fields")).toHaveTextContent(
      "trade row records no strategy_id",
    );
  });

  it("Research lists recorded results and backend-declared missing sections", async () => {
    const ownerRead = vi.fn().mockResolvedValue({
      generated_at: "2026-09-29T00:00:00Z",
      source: "journal research ledger",
      available: true,
      counts: {
        results_by_verdict: [{ verdict: "prune", status: "scored", n: 3 }],
        runs: { n: 1, failed: 0 },
        registered_tests: 0,
      },
      results: [{ hash: "h1", label: "donchian_hi(100)", verdict: "prune" }],
      runs: [],
      evidence: { controls: [], gauges: [] },
      candidates: [],
      registrations: [],
      unavailable: [
        { field: "questions", reason: "no persisted research-question store" },
      ],
    });
    withAdapter({ ownerRead }, <ResearchLive />);
    // the design's first section, Questions, has no record store
    expect(await screen.findByTestId("research-unavailable")).toHaveTextContent(
      "no persisted research-question store",
    );
    expect(
      screen.getAllByRole("button", {
        name: /^(Questions|Plans|Evidence|Results|Runs|Research Bank|Prior recall|Registrations|Costs|Shadow reports)/,
      }),
    ).toHaveLength(10);
    await userEvent.click(screen.getByRole("button", { name: /^Results/ }));
    expect(await screen.findByText("donchian_hi(100)")).toBeInTheDocument();
  });

  it("rejects a read without generated_at or with a malformed unavailable list", async () => {
    const adapter = createLiveAdapter(() => {});
    for (const body of [
      { x: 1 },
      { generated_at: "t", unavailable: [{ field: 1 }] },
    ]) {
      vi.stubGlobal(
        "fetch",
        vi
          .fn()
          .mockResolvedValue({ ok: true, status: 200, json: async () => body }),
      );
      await expect(
        adapter.ownerRead!("research", new AbortController().signal),
      ).rejects.toBeInstanceOf(ContractViolation);
    }
  });

  it("surfaces a failed read as an error, not an empty ledger", async () => {
    const ownerRead = vi.fn().mockRejectedValue(new Error("Backend error 500"));
    withAdapter({ ownerRead }, <ResearchLive />);
    await waitFor(() =>
      expect(screen.getAllByRole("alert")[0]).toHaveTextContent(
        "Backend error 500",
      ),
    );
  });
});
