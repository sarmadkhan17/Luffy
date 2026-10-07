import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { mapOverview } from "../src/adapters/live";
import { applyProtectionExpiry } from "../src/protectionExpiry";
import { PreviewProvider } from "../src/context";
import Overview from "../src/views/Overview";
import type { OwnerAdapter } from "../src/adapters/contracts";

/** LUFFY-PROTECTION-SNAPSHOT-R2 read semantics: the adapter never claims
 * VERIFIED beyond a fresh, consistent, complete, all-checks-true kernel
 * snapshot, and protection evidence expires on the browser clock even when
 * every later poll fails. */
const iso = (ms: number) => new Date(ms).toISOString();
const snapshot = (at: number, over: Record<string, unknown> = {}) => ({
  observed_at: iso(at),
  age_s: 12,
  freshness: "fresh",
  stale_after_s: 120,
  status: "VERIFIED",
  venue_positions: true,
  observation_consistent: true,
  reconciliation: true,
  venue_protection: true,
  precision_known: true,
  complete_listing: true,
  cleanliness: { status: "CLEAN", items: [] },
  reasons: [],
  symbols: [],
  source: "journal protection_evidence",
  ...over,
});
const overview = (
  snap: unknown,
  status = "VERIFIED",
  posStatus = "VERIFIED",
  generated = Date.now(),
) => ({
  mode: "LIVE",
  generated_at: iso(generated),
  errors: {},
  control: { state: "ACTIVE", observed_at: iso(generated), last_event: null },
  positions: [
    {
      id: "t1",
      symbol: "BTC/USDT",
      side: "long",
      notional_usdt: 50,
      opened_at: iso(generated - 3600_000),
      journal_stop: { price: 1, order_ref_recorded: true },
      protection: {
        status: posStatus,
        observed_at: iso(generated),
        last_reported: null,
        reasons: [],
      },
    },
  ],
  protection: { status, snapshot: snap, supervisor: null },
});

afterEach(() => {
  vi.useRealTimers();
});

describe("protection snapshot read semantics", () => {
  it("fresh all-true snapshot reads VERIFIED", () => {
    const d = mapOverview(overview(snapshot(Date.now())));
    expect(d.live!.protection!.status).toBe("VERIFIED");
    expect(d.positions![0].protection).toBe("VERIFIED");
    expect(d.live!.protection!.evidence!.freshness).toBe("fresh");
    expect(d.live!.protection!.evidence!.summary).toContain("cleanliness CLEAN");
  });
  it("aged snapshot reads STALE even if the backend said VERIFIED", () => {
    const d = mapOverview(overview(snapshot(Date.now(), { freshness: "stale" })));
    expect(d.live!.protection!.status).toBe("STALE");
    expect(d.positions![0].protection).toBe("STALE");
  });
  it("invalid (future) snapshot time is never fresh", () => {
    const d = mapOverview(overview(snapshot(Date.now(), { freshness: "invalid" })));
    expect(d.live!.protection!.status).toBe("STALE");
  });
  it("no computable expiry is never VERIFIED", () => {
    const d = mapOverview(overview(snapshot(Date.now(), { stale_after_s: undefined })));
    expect(d.live!.protection!.status).toBe("STALE");
    expect(d.positions![0].protection).toBe("STALE");
  });
  it("missing check reads UNAVAILABLE / not verified", () => {
    const d = mapOverview(overview(null));
    expect(d.live!.protection!.status).toBe("UNAVAILABLE");
    expect(d.positions![0].protection).toBe("UNAVAILABLE");
    expect(d.live!.protection!.evidence).toBeNull();
    const none = mapOverview({ ...overview(null, "NO_POSITIONS"), positions: [] });
    expect(none.live!.protection!.status).toBe("UNAVAILABLE");
  });
  it("failed snapshot reads UNREADABLE with its reasons", () => {
    const d = mapOverview(
      overview(
        snapshot(Date.now(), {
          status: "UNREADABLE",
          venue_positions: false,
          reasons: ["venue_timeout"],
        }),
      ),
    );
    expect(d.live!.protection!.status).toBe("UNREADABLE");
    expect(d.live!.protection!.reasons).toEqual(["venue_timeout"]);
    expect(d.live!.protection!.evidence!.summary).toContain("venue_timeout");
  });
  it("partial snapshot reads PARTIAL; no single check alone gives VERIFIED", () => {
    for (const over of [
      { status: "PARTIAL", reconciliation: false, reasons: ["journal_size_drift:BTC/USDT"] },
      { venue_positions: false },
      { reconciliation: false },
      { observation_consistent: false },
      { observation_consistent: undefined },
      { precision_known: false },
      { complete_listing: false },
      { complete_listing: undefined },
    ]) {
      const d = mapOverview(overview(snapshot(Date.now(), over)));
      expect(d.live!.protection!.status).toBe("PARTIAL");
      expect(d.positions![0].protection).toBe("PARTIAL");
    }
  });
  it("backend UNREADABLE per-position status is preserved", () => {
    const d = mapOverview(
      overview(snapshot(Date.now(), { status: "UNREADABLE" }), "UNREADABLE", "UNREADABLE"),
    );
    expect(d.positions![0].protection).toBe("UNREADABLE");
  });
  it("server clock offset is applied to the local expiry", () => {
    const t = Date.now();
    // server clock 30 s ahead of the browser: its checked_at is 30 s "later"
    const d = mapOverview(overview(snapshot(t + 30_000), "VERIFIED", "VERIFIED", t + 30_000));
    expect(d.live!.protection!.expiresAt).toBeGreaterThan(t + 119_000);
    expect(d.live!.protection!.expiresAt).toBeLessThan(t + 121_000);
  });
});

describe("local expiry (independent of polling)", () => {
  it("VERIFIED becomes STALE once the snapshot's own expiry passes", () => {
    const t = Date.now();
    const d = mapOverview(overview(snapshot(t - 10_000)));
    expect(applyProtectionExpiry(d, t)).toBe(d); // still current: untouched
    const later = applyProtectionExpiry(d, t + 111_000);
    expect(later.live!.protection!.status).toBe("STALE");
    expect(later.live!.protection!.evidence!.freshness).toBe("stale");
    expect(later.positions![0].protection).toBe("STALE");
    expect(later.positions![0].protectionDetail!.lastReported).toBe("VERIFIED");
    expect(later.positions![0].protectionDetail!.reasons).toContain(
      "verification_stale_locally",
    );
  });
  it("PARTIAL and UNREADABLE evidence age the same way", () => {
    const t = Date.now();
    for (const over of [{ status: "PARTIAL", venue_protection: false }, { status: "UNREADABLE" }]) {
      const d = mapOverview(overview(snapshot(t, over), String(over.status), String(over.status)));
      expect(applyProtectionExpiry(d, t + 121_000).live!.protection!.status).toBe("STALE");
    }
  });
  it("transitions: VERIFIED→UNREADABLE, PARTIAL→VERIFIED, missing→first snapshot", () => {
    const t = Date.now();
    const seq = [
      [snapshot(t), "VERIFIED"],
      [snapshot(t + 1, { status: "UNREADABLE", venue_positions: false }), "UNREADABLE"],
      [snapshot(t + 2, { status: "PARTIAL", venue_protection: false }), "PARTIAL"],
      [snapshot(t + 3), "VERIFIED"],
    ] as const;
    for (const [snap, want] of seq) {
      const d = mapOverview(overview(snap, want, want));
      expect(applyProtectionExpiry(d, Date.now()).live!.protection!.status).toBe(want);
    }
    const missing = mapOverview(overview(null, "UNAVAILABLE", "UNAVAILABLE"));
    expect(applyProtectionExpiry(missing, t + 10 ** 9).live!.protection!.status).toBe(
      "UNAVAILABLE",
    );
    const first = mapOverview(overview(snapshot(t)));
    expect(first.live!.protection!.status).toBe("VERIFIED");
  });
});

function renderOverview(adapter: OwnerAdapter) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PreviewProvider adapter={adapter}>
        <Overview />
      </PreviewProvider>
    </QueryClientProvider>,
  );
}
const statusBadge = () =>
  screen.getByText("Protection").closest(".gl-tile")!.textContent ?? "";

describe("Overview: polling failure never keeps VERIFIED current", () => {
  it("VERIFIED → API failures → browser clock passes expiry → STALE", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    const t = Date.now();
    const overviewFn = vi
      .fn()
      .mockResolvedValueOnce(mapOverview(overview(snapshot(t - 5_000))))
      .mockRejectedValue(new Error("Backend unreachable. No data was substituted."));
    const adapter = {
      mode: "LIVE",
      overview: overviewFn,
      graph: vi.fn(),
      chat: vi.fn(),
    } as unknown as OwnerAdapter;
    renderOverview(adapter);
    await waitFor(() => expect(statusBadge()).toContain("VERIFIED"));
    // polls fail from here on (30 s interval); the old data stays in the query
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    await waitFor(() => expect(overviewFn.mock.calls.length).toBeGreaterThan(1));
    expect(statusBadge()).toContain("VERIFIED"); // 65 s old: still inside 120 s
    await act(async () => {
      vi.advanceTimersByTime(60_000); // 125 s after checked_at
    });
    await waitFor(() => expect(statusBadge()).toContain("STALE"));
    expect(statusBadge()).not.toContain("VERIFIED");
    // per-position protection now lives in the position detail panel, not the table
  });
  it("VERIFIED → UNREADABLE on the next successful poll", async () => {
    vi.useFakeTimers({ toFake: ["Date", "setInterval", "clearInterval"] });
    const t = Date.now();
    const overviewFn = vi
      .fn()
      .mockResolvedValueOnce(mapOverview(overview(snapshot(t))))
      .mockResolvedValue(
        mapOverview(
          overview(
            snapshot(t + 30_000, {
              status: "UNREADABLE",
              venue_positions: false,
              reasons: ["venue_timeout"],
            }),
            "UNREADABLE",
            "UNREADABLE",
            t + 30_000,
          ),
        ),
      );
    renderOverview({
      mode: "LIVE",
      overview: overviewFn,
      graph: vi.fn(),
      chat: vi.fn(),
    } as unknown as OwnerAdapter);
    await waitFor(() => expect(statusBadge()).toContain("VERIFIED"));
    await act(async () => {
      vi.advanceTimersByTime(31_000);
    });
    await waitFor(() => expect(statusBadge()).toContain("UNREADABLE"));
  });
});
