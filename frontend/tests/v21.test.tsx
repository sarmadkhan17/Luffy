import { describe, it, expect, vi, afterEach } from "vitest";
import {
  mapOverview,
  mapKnowledge,
  createLiveAdapter,
} from "../src/adapters/live";
import { healthPresentation } from "../src/components/HealthStatus";
import { timestamp, modifiedAge } from "../src/time";
import type { GraphNode } from "../src/adapters/contracts";

afterEach(() => vi.unstubAllGlobals());
describe("V2.1 truthful presentation and contracts", () => {
  it("does not equate missing telemetry with unavailable runtime", () => {
    const node = {
      hasTelemetry: false,
      health: undefined,
      evidence: { freshness: "unavailable", observedAt: null },
    } as GraphNode;
    expect(healthPresentation(node).primary).toBe("NO TELEMETRY");
    node.hasTelemetry = true;
    expect(healthPresentation(node).primary).toBe("UNAVAILABLE");
    node.evidence.freshness = "stale";
    node.health = "active";
    expect(healthPresentation(node).primary).toBe("STALE");
  });
  it("file modification is age, never a freshness contract", () => {
    const d = mapKnowledge({
      nodes: [
        {
          id: "a",
          label: "A",
          kind: "note",
          provenance: {
            observed_at: "2000-01-01T00:00:00Z",
            time_basis: "file_modified",
          },
        },
      ],
      edges: [],
      generated_at: "2026-09-29T00:00:00Z",
    });
    expect(d.nodes[0].evidence.freshness).toBe("not_assessed");
    expect(d.nodes[0].evidence.timeBasis).toBe("file_modified");
    expect(modifiedAge("2000-01-01T00:00:00Z")).toMatch(/d ago/);
    expect(modifiedAge(null)).toBe("age unavailable");
    expect(timestamp("2026-09-29T03:00:00+03:00")).toBe(
      "2026-09-29 00:00:00 UTC",
    );
    expect(timestamp(null)).toBe("Source time unavailable");
  });
  it.each([
    "venue_positions",
    "reconciliation",
    "venue_protection",
    "observation_consistent",
    "precision_known",
  ])(
    "VERIFIED requires %s evidence",
    (field) => {
      const now = new Date().toISOString();
      const snapshot = {
        observed_at: now,
        freshness: "fresh",
        stale_after_s: 120,
        status: "VERIFIED",
        complete_listing: true,
        venue_positions: true,
        observation_consistent: true,
        reconciliation: true,
        venue_protection: true,
        precision_known: true,
        [field]: false,
      };
      const d = mapOverview({
        mode: "LIVE",
        generated_at: now,
        errors: {},
        protection: { status: "VERIFIED", snapshot },
        positions: [
          {
            id: "a",
            symbol: "BTC",
            protection: { status: "VERIFIED", observed_at: now },
          },
        ],
      });
      expect(d.positions![0].protection).toBe("PARTIAL");
      expect(d.live!.protection!.status).toBe("PARTIAL");
    },
  );
  it("binds offset and lookahead without including lookahead row", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        new Response(
          JSON.stringify({
            data: {
              decisions: Array.from({ length: 101 }, (_, i) => ({
                id: String(i),
              })),
            },
          }),
        ),
      );
    vi.stubGlobal("fetch", fetcher);
    const d = await createLiveAdapter(() => {}).decisions!(
      new AbortController().signal,
      100,
    );
    expect(d.rows).toHaveLength(100);
    expect(d.paging).toEqual({ offset: 100, limit: 100, hasMore: true });
    const body = JSON.parse(fetcher.mock.calls[0][1].body);
    expect(body.variables.offset).toBe(100);
    expect(body.query).toContain("limit:101,offset:$offset");
  });
});
