import { expect, it } from "vitest";
import type { GraphData } from "../src/adapters/contracts";
import { evidencePath } from "../src/views/graphPath";
const d = {
  nodes: ["a", "b", "c", "d"].map((id) => ({ id })),
  edges: [
    ["a", "b"],
    ["b", "c"],
    ["c", "a"],
    ["a", "missing"],
    ["missing", "d"],
  ].map(([source, target], i) => ({
    id: String(i),
    source,
    target,
    relation: "rel",
    kind: "typed",
  })),
} as GraphData;
it("follows directed edges and terminates on cycles", () => {
  expect(evidencePath(d, "a", "c")?.map((e) => e.id)).toEqual(["0", "1"]);
  expect(evidencePath(d, "c", "b")?.map((e) => e.id)).toEqual(["2", "0"]);
});
it("does not invent missing-reference bridges or a reversed connection", () => {
  expect(evidencePath(d, "a", "d")).toBeNull();
  expect(evidencePath(d, "d", "a")).toBeNull();
  expect(evidencePath(d, "missing", "a")).toBeNull();
});
it("handles an identity path without claiming an edge", () => {
  expect(evidencePath(d, "a", "a")).toEqual([]);
});
