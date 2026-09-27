// Built only with --mode test-fixtures, never in the distributable preview.
import { useState, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PreviewProvider, usePreview } from "../../src/context";
import { fixtureAdapter, knowledgeNodes } from "../../src/adapters/fixture";
import type {
  OwnerAdapter,
  GraphData,
  GraphNode,
} from "../../src/adapters/contracts";
import GraphView from "../../src/views/GraphView";
import Luffy from "../../src/views/Luffy";
import "../../src/styles.css";
const params = new URLSearchParams(location.search),
  scale = params.has("scale"),
  dangling = params.has("dangling");
const adapter: OwnerAdapter = {
  ...fixtureAdapter,
  async graph(surface, scenario, signal) {
    const base = await fixtureAdapter.graph(surface, scenario, signal);
    if (dangling)
      base.edges.push({
        id: "missing",
        source: base.nodes[0].id,
        target: "unavailable-record",
        kind: "link",
        relation: "reference",
      });
    if (!scale) return base;
    const nodes: GraphNode[] = Array.from(
      { length: surface === "knowledge" ? 500 : 40 },
      (_, i) => ({
        ...knowledgeNodes[0],
        id: `scale-${i}`,
        label: `Sample ${String(i).padStart(3, "0")}`,
        kind: surface === "system" ? "Component" : "Observation",
        health: "idle",
        x: (i % 8) * 300,
        y: Math.floor(i / 8) * 180,
      }),
    );
    const edges = nodes.slice(0, -1).map((n, i) => ({
      id: `scale-edge-${i}`,
      source: n.id,
      target: nodes[i + 1].id,
      kind: "architecture" as const,
      relation: "fixture connection",
    }));
    if (surface === "system" && params.has("branched"))
      for (const [source, target] of [
        [0, 2],
        [3, 0],
        [5, 2],
        [8, 4],
        [12, 6],
      ])
        edges.push({
          id: `branch-${source}-${target}`,
          source: `scale-${source}`,
          target: `scale-${target}`,
          kind: "architecture",
          relation: "adversarial fan-in / back-edge",
        });
    return { ...base, nodes, edges, events: [] };
  },
  async chat(text, scenario, signal, chunk) {
    if (!params.has("long-chat"))
      return fixtureAdapter.chat(text, scenario, signal, chunk);
    let result = "";
    for (let i = 0; i < 25; i++) {
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => {
          signal.removeEventListener("abort", cancel);
          resolve();
        }, 90);
        const cancel = () => {
          clearTimeout(timer);
          reject(new DOMException("Cancelled", "AbortError"));
        };
        signal.addEventListener("abort", cancel, { once: true });
      });
      result += "Fixture streaming text. ";
      chunk(result);
    }
    return { text: result, evidence: [] };
  },
};
function Harness() {
  const [surface, setSurface] = useState<"knowledge" | "system" | "chat">(
    "system",
  );
  const { setMessages } = usePreview();
  useEffect(() => {
    if (params.has("long-chat"))
      setMessages(
        Array.from({ length: 24 }, (_, i) => ({
          id: i,
          role: "luffy",
          text: `Historical fixture message ${i}. `.repeat(10),
          evidence: [],
        })),
      );
  }, [setMessages]);
  return (
    <>
      <div className="demo-banner">
        DEMO — synthetic data, no trading connection · regression harness
      </div>
      <nav>
        {(["system", "knowledge", "chat"] as const).map((v) => (
          <button key={v} onClick={() => setSurface(v)}>
            {v}
          </button>
        ))}
      </nav>
      <main>
        {surface === "chat" ? (
          <Luffy />
        ) : (
          <GraphView key={surface} surface={surface} />
        )}
      </main>
    </>
  );
}
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider
    client={
      new QueryClient({
        defaultOptions: { queries: { retry: false, staleTime: Infinity } },
      })
    }
  >
    <PreviewProvider adapter={adapter}>
      <Harness />
    </PreviewProvider>
  </QueryClientProvider>,
);
