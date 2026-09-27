import type {
  OwnerAdapter,
  Provenance,
  GraphData,
  GraphNode,
  GraphEdge,
  Scenario,
} from "./contracts";
export const FIXTURE_TIME = "2026-09-27T09:30:00Z";
export const evidence: Provenance = {
  id: "fixture:evidence-001",
  source: "Synthetic preview / research receipt 001",
  observedAt: FIXTURE_TIME,
  freshness: "fresh",
  summary:
    "Synthetic structural research receipt. Outcome INCONCLUSIVE. No registered falsifier predicates; this record establishes neither trading edge nor execution authority.",
  classification: "context_only · INCONCLUSIVE",
};
function delay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Cancelled", "AbortError"));
      return;
    }
    const abort = () => {
      clearTimeout(timer);
      reject(new DOMException("Cancelled", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, ms);
    signal.addEventListener("abort", abort, { once: true });
  });
}
function provenance(scenario: Scenario): Provenance {
  return {
    ...evidence,
    freshness:
      scenario === "missing"
        ? "unavailable"
        : scenario === "stale"
          ? "stale"
          : "fresh",
    observedAt: scenario === "missing" ? null : FIXTURE_TIME,
  };
}
async function gate(scenario: Scenario, signal: AbortSignal) {
  await delay(100, signal);
  if (scenario === "error")
    throw new Error(
      "Synthetic adapter failure. No substitute data was loaded.",
    );
}
const knowledgeLabels = [
  "Market observation",
  "Liquidity hypothesis",
  "Research question",
  "Frozen protocol",
  "Experiment receipt",
  "Evidence record",
  "Inconclusive result",
  "Strategy version",
  "Decision record",
  "Outcome record",
  "Learning note",
  "Code reference",
];
const kinds = [
  "Observation",
  "Hypothesis",
  "Question",
  "Protocol",
  "Experiment",
  "Evidence",
  "Conclusion",
  "Strategy",
  "Decision",
  "Outcome",
  "Learning",
  "Code",
];
export const knowledgeNodes: GraphNode[] = knowledgeLabels.map((label, i) => ({
  id: `k${i}`,
  label,
  kind: kinds[i],
  lenses:
    i === 11
      ? ["Code"]
      : i === 10
        ? ["Knowledge", "Timeline", "Code"]
        : i === 5 || i === 4 || i === 6
          ? ["Knowledge", "Evidence", "Timeline"]
          : ["Knowledge", "Timeline"],
  x: [0, 270, 540, 810, 810, 540, 270, 0, -270, -400, -270, -540][i],
  y: [0, -120, -120, 0, 200, 300, 300, 240, 240, 40, -140, -160][i],
  evidence: {
    ...evidence,
    id: `fixture:k${i}`,
    source: `Synthetic preview / ${kinds[i].toLowerCase()}`,
    classification: i === 6 ? "context_only · INCONCLUSIVE" : "context_only",
    summary: `${label} is a synthetic ${kinds[i].toLowerCase()} for interface testing. No production record or trading permission is represented.`,
  },
}));
const relations = [
  "motivates",
  "frames",
  "specifies",
  "evaluates",
  "records",
  "constrains",
  "informs",
  "context_for",
  "produces",
  "updates",
  "reference",
];
const knowledgeEdges: GraphEdge[] = knowledgeNodes.slice(0, -1).map((n, i) => ({
  id: `ke${i}`,
  source: n.id,
  target: `k${i + 1}`,
  kind: i === 10 ? "link" : "typed",
  relation: relations[i],
  evidenceId: n.evidence.id,
}));
const systemLabels = [
  "Market Data",
  "World Model",
  "Attention",
  "Analyst",
  "Strategy Registry",
  "Risk",
  "Execution",
  "Journal",
  "Research",
];
const health: GraphNode["health"][] = [
  "active",
  "idle",
  "idle",
  "unknown",
  "idle",
  "degraded",
  "unknown",
  "idle",
  "idle",
];
const systemNodes: GraphNode[] = systemLabels.map((label, i) => ({
  id: `s${i}`,
  label,
  kind: "Component",
  lenses: ["Knowledge"],
  x: (i % 3) * 340,
  y: Math.floor(i / 3) * 180,
  health: health[i],
  evidence: {
    ...evidence,
    id: `fixture:s${i}`,
    source: `Synthetic telemetry / ${label}`,
    classification: "synthetic telemetry · NOT_ASSESSED",
    freshness: i === 5 ? "stale" : i === 3 || i === 6 ? "unavailable" : "fresh",
    observedAt: i === 3 || i === 6 ? null : FIXTURE_TIME,
    summary: `${label}: illustrative component status only. Architecture describes possible connections; it is not observed throughput.`,
  },
}));
const systemEdges: GraphEdge[] = [
  [0, 1],
  [1, 2],
  [2, 3],
  [4, 3],
  [3, 5],
  [5, 6],
  [6, 7],
  [7, 8],
  [8, 4],
].map(([s, t], i) => ({
  id: `se${i}`,
  source: `s${s}`,
  target: `s${t}`,
  kind: "architecture",
  relation: "architectural connection",
}));
export const fixtureAdapter: OwnerAdapter = {
  mode: "DEMO",
  async overview(scenario, signal) {
    await gate(scenario, signal);
    return {
      provenance: provenance(scenario),
      equity: scenario === "missing" ? null : 102480.5,
      pnl: scenario === "missing" ? null : 1240.5,
      exposure: scenario === "missing" ? null : 18.6,
      control: scenario === "missing" ? "UNAVAILABLE" : "OBSERVATION PREVIEW",
      positions:
        scenario === "missing"
          ? null
          : [
              {
                symbol: "BTC / USDT",
                side: "Long",
                notional: 12000,
                pnl: 286.4,
                protection: "UNAVAILABLE",
              },
              {
                symbol: "ETH / USDT",
                side: "Long",
                notional: 7061.37,
                pnl: -42.8,
                protection: "UNAVAILABLE",
              },
            ],
      points:
        scenario === "missing"
          ? []
          : Array.from({ length: 30 }, (_, i) => ({
              time: new Date(Date.UTC(2026, 7, 29 + i))
                .toISOString()
                .slice(0, 10),
              value:
                101240 +
                (i / 29) * 1240.5 +
                Math.sin((i * Math.PI) / 29) * Math.sin(i * 0.8) * 180,
            })),
      needsYou: null,
    };
  },
  async graph(surface, scenario, signal): Promise<GraphData> {
    await gate(scenario, signal);
    const nodes = surface === "knowledge" ? knowledgeNodes : systemNodes;
    return {
      nodes:
        scenario === "missing"
          ? []
          : nodes.map((n) => ({
              ...n,
              evidence:
                scenario === "stale"
                  ? { ...n.evidence, freshness: "stale" }
                  : n.evidence,
            })),
      edges:
        scenario === "missing"
          ? []
          : surface === "knowledge"
            ? knowledgeEdges
            : systemEdges,
      events:
        surface === "system" && scenario === "normal"
          ? [
              {
                id: "fixture:event-001",
                edgeId: "se0",
                occurredAt: FIXTURE_TIME,
                description: "Synthetic Market Data → World Model receipt",
              },
            ]
          : [],
      provenance: provenance(scenario),
    };
  },
  async chat(text, scenario, signal, onChunk) {
    await gate(scenario, signal);
    await delay(500, signal);
    if (scenario === "missing")
      return {
        text: "DEMO: Supporting evidence is unavailable in this scenario. No account or system claim can be made.",
        evidence: [],
      };
    const reply = /trade|resume|panic|approve|buy|sell|stop/i.test(text)
      ? "DEMO: This preview cannot execute commands, approve decisions, or change trading state. No action was taken."
      : "DEMO fixture reply: The sample research receipt is INCONCLUSIVE. It records a structural evidence chain, not a validated trading edge. Open the attached evidence to inspect its source and limits.";
    const chunks = reply.match(/.{1,48}/g) || [];
    let output = "";
    for (const chunk of chunks) {
      await delay(80, signal);
      output += chunk;
      onChunk(output);
    }
    return {
      text: reply,
      evidence: [
        { ...evidence, freshness: scenario === "stale" ? "stale" : "fresh" },
      ],
    };
  },
};
