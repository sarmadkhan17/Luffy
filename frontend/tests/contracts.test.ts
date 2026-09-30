import { describe, it, expect } from "vitest";
import { fixtureAdapter, knowledgeNodes } from "../src/adapters/fixture";
import { graphScope, MAX_GRAPH_NODES } from "../src/views/graphScope";
describe("preview boundaries", () => {
  it("rejects errors without substituting data", async () => {
    await expect(
      fixtureAdapter.overview("error", new AbortController().signal),
    ).rejects.toThrow("No substitute");
  });
  it("keeps missing financial values null and protection unavailable", async () => {
    const d = await fixtureAdapter.overview(
      "missing",
      new AbortController().signal,
    );
    expect(d.equity).toBeNull();
    expect(d.positions).toBeNull();
    expect(d.points).toEqual([]);
    expect(d.provenance.freshness).toBe("unavailable");
  });
  it("does not supply activity for stale or missing topology", async () => {
    for (const s of ["stale", "missing"] as const) {
      const d = await fixtureAdapter.graph(
        "system",
        s,
        new AbortController().signal,
      );
      expect(d.events).toEqual([]);
    }
  });
  it("bounds large scopes and excludes edges outside the scope", () => {
    const nodes = Array.from({ length: 500 }, (_, i) => ({
      ...knowledgeNodes[0],
      id: `n${i}`,
    }));
    const d = {
      nodes,
      edges: [
        {
          id: "off",
          source: "n0",
          target: "n499",
          kind: "typed" as const,
          relation: "test",
        },
      ],
      events: [],
      provenance: knowledgeNodes[0].evidence,
    };
    const scoped = graphScope(d, "Knowledge", "", null, false, false);
    expect(scoped.nodes).toHaveLength(MAX_GRAPH_NODES);
    expect(scoped.total).toBe(500);
    expect(scoped.edges).toEqual([]);
  });
  it("filters lenses and finds only immediate neighbours", async () => {
    const d = await fixtureAdapter.graph(
      "knowledge",
      "normal",
      new AbortController().signal,
    );
    expect(
      graphScope(d, "Code", "", null, false, false).nodes.map((n) => n.label),
    ).toEqual(["Learning note", "Code reference"]);
    expect(
      graphScope(d, "Knowledge", "", "k1", true, false).nodes.map((n) => n.id),
    ).toEqual(["k0", "k1", "k2"]);
  });
  it("aborts already cancelled requests", async () => {
    const a = new AbortController();
    a.abort();
    await expect(
      fixtureAdapter.overview("normal", a.signal),
    ).rejects.toHaveProperty("name", "AbortError");
  });
});
