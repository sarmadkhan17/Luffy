/** M2: connected evidence — deep links, record links only from backend ids,
 * unavailable/malformed truth and chat record mentions. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { PreviewProvider } from "../src/context";
import {
  mapMentions,
  createLiveAdapter,
  ContractViolation,
} from "../src/adapters/live";
import { OperationsActivity, NoteDetail } from "../src/views/LiveReads";
import { ReplyEvidence } from "../src/views/Luffy";
import type { OwnerAdapter } from "../src/adapters/contracts";
import { parseHash, recordHref } from "../src/links";
import {
  DecisionDetail,
  NoteRecords,
  OperationsTimeline,
  OverviewEvidence,
  ResearchItem,
  StrategyEvidence,
  SystemObserved,
  TradeContext,
} from "../src/views/Evidence";

afterEach(() => {
  vi.unstubAllGlobals();
  history.replaceState(null, "", "/");
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
const read = (map: Record<string, unknown>) =>
  vi.fn(async (path: string) => {
    const key = Object.keys(map).find((k) => path === k || path.startsWith(k));
    if (!key) throw new Error(`Backend error 404 for ${path}`);
    const v = map[key];
    if (v instanceof Error) throw v;
    return v as Record<string, unknown>;
  });
const links = () =>
  [...document.querySelectorAll<HTMLAnchorElement>("a[data-record]")].map(
    (a) => a.dataset.record,
  );

describe("deep links", () => {
  it("builds and parses record routes", () => {
    expect(recordHref("trade", "pos_1")).toBe("#trades?trade=pos_1");
    expect(recordHref("research", "a b")).toBe("#research?combo=a%20b");
    expect(parseHash("#strategies?id=s%201")).toEqual({
      route: "strategies",
      params: new URLSearchParams("id=s 1"),
    });
    expect(parseHash("").route).toBe("overview");
    expect(parseHash("#live-system").route).toBe("live-system");
  });
});

describe("trade story", () => {
  it("links only recorded refs and states unavailable steps", () => {
    withAdapter(
      {},
      <TradeContext
        d={{
          chain: [
            {
              step: "opportunity",
              status: "recorded",
              ref: { kind: "scan", id: "scan_1" },
              summary: "scan",
            },
            {
              step: "decision",
              status: "recorded",
              ref: { kind: "decision", id: "dec_1" },
            },
            { step: "strategy", status: "unavailable", ref: null },
            {
              step: "execution",
              status: "recorded",
              ref: { kind: "trade", id: "pos_1" },
            },
          ],
          votes: [],
          votes_basis: {
            join: "votes.cycle_id = decisions.cycle_id (exact)",
            window_s: 300,
            status: "none_in_window",
          },
        }}
      />,
    );
    expect(links()).toEqual(["decision:dec_1", "trade:pos_1"]);
    // a scan id is recorded but has no by-id record: shown, not linked
    expect(screen.getByTestId("ref-not-openable")).toHaveTextContent(
      "scan scan_1 · recorded id; not openable here",
    );
    expect(screen.getByText("UNAVAILABLE")).toBeInTheDocument();
    expect(
      screen.getByText(/No vote of this cycle within the read window/),
    ).toBeInTheDocument();
  });
});

describe("decision detail", () => {
  it("links a registered strategy and marks an unregistered id", async () => {
    const ownerRead = read({
      "decisions/dec_2": {
        generated_at: "2026-09-29T00:00:00Z",
        source: "journal",
        decision: { id: "dec_2", signals: [], executed: 0 },
        strategies: [
          { id: "spec_a", in_registry: true, name: "A" },
          { id: "spec_ghost", in_registry: false },
        ],
        trades: [{ id: "pos_9", symbol: "SOL/USDT" }],
        votes: [],
        unavailable: [{ field: "outcome", reason: "no outcome recorded" }],
      },
    });
    withAdapter({ ownerRead }, <DecisionDetail id="dec_2" />);
    expect(
      await screen.findByText("spec_ghost · not in registry"),
    ).toBeTruthy();
    expect(links()).toEqual(
      expect.arrayContaining(["strategy:spec_a", "trade:pos_9"]),
    );
    expect(links()).not.toContain("strategy:spec_ghost");
    expect(screen.getByTestId("unavailable-fields")).toHaveTextContent(
      "no outcome recorded",
    );
  });

  it("shows a missing decision as an error, not an empty record", async () => {
    const ownerRead = read({ "decisions/": new Error("Backend error 404") });
    withAdapter({ ownerRead }, <DecisionDetail id="nope" />);
    await waitFor(() =>
      expect(screen.getAllByRole("alert")[0]).toHaveTextContent(
        "Backend error 404",
      ),
    );
    expect(links()).toEqual([]);
  });
});

describe("research item", () => {
  it("navigates parent, children and seed by recorded hash/id", async () => {
    const ownerRead = read({
      "research/combos/h2": {
        generated_at: "t",
        source: "ledger",
        item: {
          hash: "h2",
          parent: "h1",
          seed_strategy: { id: "spec_a", in_registry: true, name: "A" },
        },
        parent: { hash: "h1", verdict: "grow" },
        children: [{ hash: "h3", label: "x", verdict: "prune" }],
        candidate: null,
        registrations: [],
        unavailable: [{ field: "candidate", reason: "not in the bank" }],
      },
    });
    withAdapter({ ownerRead }, <ResearchItem hash="h2" />);
    await screen.findByTestId("unavailable-fields");
    expect(links()).toEqual(
      expect.arrayContaining(["strategy:spec_a", "research:h1", "research:h3"]),
    );
  });
});

describe("strategy workspace", () => {
  it("shows the postmortem as a timed verdict and links family", () => {
    withAdapter(
      {},
      <StrategyEvidence
        d={{
          family: {
            kind: "ema_trend",
            generation: 1,
            parent: { id: "p1", in_registry: false },
            children: [],
            siblings: [],
          },
          postmortem: {
            verdict: "CONSISTENT",
            at: "2026-09-29T15:02:58Z",
            event_id: 7,
          },
          source_idea: null,
          research_seeded: [{ hash: "h1", label: "x" }],
          brain_events: [
            { at: "t", kind: "keep_verdict", detail: { _unparseable: true } },
          ],
        }}
      />,
    );
    expect(screen.getByTestId("strategy-postmortem")).toHaveTextContent(
      "not current health",
    );
    expect(screen.getByText("p1 · not in registry")).toBeTruthy();
    expect(links()).toEqual(
      expect.arrayContaining(["family:ema_trend", "research:h1"]),
    );
    expect(screen.getByText('{"_unparseable":true}')).toBeTruthy();
  });
});

describe("operations timeline", () => {
  it("renders an empty window truthfully", () => {
    withAdapter(
      {},
      <OperationsTimeline
        d={{
          timeline: [],
          signal_cooldowns: { in_newest_500_control_events: 0 },
        }}
      />,
    );
    expect(
      screen.getByText(/No directional decision, trade, control/),
    ).toBeTruthy();
  });

  it("links a record only when it carries a ref", () => {
    withAdapter(
      {},
      <OperationsTimeline
        d={{
          timeline: [
            {
              at: "2026-09-29T10:00:00Z",
              kind: "decision",
              title: "BUY SOL",
              status: "executed",
              ref: { kind: "decision", id: "dec_1" },
              strategies: [{ id: "spec_a", in_registry: true }],
            },
            {
              at: "2026-09-29T09:00:00Z",
              kind: "strategy_lifecycle",
              title: "spec_rejected · spec_ghost",
              ref: null,
              strategies: [{ id: "spec_ghost", in_registry: false }],
            },
          ],
          signal_cooldowns: { in_newest_500_control_events: 12 },
        }}
      />,
    );
    expect(links()).toEqual(["decision:dec_1", "strategy:spec_a"]);
    expect(screen.getByText("spec_rejected · spec_ghost")).toBeTruthy();
  });
});

describe("overview activity", () => {
  it("shows recorded items with links and declared gaps", async () => {
    const ownerRead = read({
      "overview/activity": {
        generated_at: "t",
        source: "journal",
        decisions: {
          window: 500,
          executed: [
            { id: "dec_1", action: "BUY", symbol: "SOL/USDT", ts: "t" },
          ],
          skipped: [],
          risk_blocks: [],
        },
        strategies: [{ id: "spec_a", name: "A", state: "paper" }],
        lifecycle: [],
        research: null,
        risk_state: null,
        rent_state: null,
        unresolved_owner_requests: [],
        unavailable: [
          { field: "approvals", reason: "no approval-object store exists" },
          { field: "rent_state", reason: "not a readable record" },
        ],
      },
    });
    withAdapter({ ownerRead }, <OverviewEvidence />);
    await screen.findByTestId("unavailable-fields");
    expect(links()).toEqual(
      expect.arrayContaining(["decision:dec_1", "strategy:spec_a"]),
    );
    expect(screen.getByTestId("unavailable-fields")).toHaveTextContent(
      "no approval-object store exists",
    );
    expect(screen.getByText("Research ledger unavailable.")).toBeTruthy();
  });

  it("surfaces a failed summary read as an error", async () => {
    const ownerRead = read({
      "overview/activity": new Error("Backend error 500"),
    });
    withAdapter({ ownerRead }, <OverviewEvidence />);
    await waitFor(() =>
      expect(screen.getAllByRole("alert")[0]).toHaveTextContent(
        "Backend error 500",
      ),
    );
  });
});

describe("live system observed flows", () => {
  it("labels observed vs declared and saturated counts", async () => {
    const ownerRead = read({
      "system/observed": {
        generated_at: "t",
        source: "journal",
        note: "Counts prove records were written, not traffic.",
        flows: [
          {
            source: "orchestrator",
            target: "journal",
            record: "journal decisions",
            count: 500,
            saturated: true,
            window_s: 900,
            status: "observed",
            declared_edge: false,
          },
          {
            source: "risk",
            target: "execution",
            record: "trades",
            count: 0,
            window_s: 86400,
            status: "none_in_window",
            declared_edge: true,
          },
        ],
        unobserved_declared_edges: [
          { source: "attention", target: "orchestrator", reason: "no record" },
        ],
      },
    });
    withAdapter({ ownerRead }, <SystemObserved />);
    expect(await screen.findByText("≥500")).toBeTruthy();
    expect(screen.getByText("NONE_IN_WINDOW")).toBeTruthy();
    expect(screen.getByText("DECLARED")).toBeTruthy();
    expect(screen.getByText("not in the declared map")).toBeTruthy();
  });
});

describe("knowledge note records", () => {
  it("links exact ids and reports a failed lookup", () => {
    const { rerender } = withAdapter(
      {},
      <NoteRecords
        d={{
          records: [
            { kind: "strategy", id: "spec_a", label: "A", basis: "exact_id" },
          ],
          family: { kind: "spec", basis: "exact", strategies: [] },
          records_basis: "exact ids only",
        }}
      />,
    );
    expect(links()).toEqual(["strategy:spec_a", "family:spec"]);
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <PreviewProvider adapter={{ mode: "LIVE" } as OwnerAdapter}>
          <NoteRecords d={{ records: [], records_error: "OperationalError" }} />
        </PreviewProvider>
      </QueryClientProvider>,
    );
    expect(
      screen.getByText("Record lookup unavailable (OperationalError)."),
    ).toBeTruthy();
  });
});

describe("chat record mentions (exact ids only)", () => {
  const ok = {
    links: [
      { kind: "trade", id: "pos_1", label: "SOL long", basis: "exact_id" },
    ],
    links_basis: "exact",
    links_error: null,
    unresolved: [{ token: "spec_missing", kind_hint: "strategy" }],
    resolved_count: 1,
    truncated_count: 0,
    unresolved_count: 1,
    consulted: [{ tool: "get_trades", arguments: "{}", rows: 2 }],
  };
  const EMPTY = {
    links: [],
    links_error: null,
    unresolved: [],
    resolved_count: 0,
    truncated_count: 0,
    unresolved_count: 0,
  };

  it("accepts exact-id mentions, unresolved ids and consulted reads", () => {
    const m = mapMentions(ok);
    expect(m.links).toHaveLength(1);
    expect(m.unresolved).toEqual(["spec_missing"]);
    expect(m.consulted).toEqual([
      { tool: "get_trades", arguments: "{}", rows: 2, error: null },
    ]);
  });

  it("rejects any non-exact-id basis, including a unique name", () => {
    const m = mapMentions({
      ...ok,
      links: [
        {
          kind: "strategy",
          id: "s1",
          label: "Donchian",
          basis: "exact_unique_name",
        },
      ],
    });
    expect(m.links).toBeNull();
    expect(m.linksNote).toBe(
      "Stored-record lookup unavailable (malformed response).",
    );
  });

  it("keeps a lookup error apart from an empty success", () => {
    const failed = mapMentions({
      links: [],
      links_error: "lookup_failed:OperationalError",
    });
    expect(failed.links).toBeNull();
    expect(failed.linksNote).toBe(
      "Stored-record lookup unavailable (lookup_failed:OperationalError).",
    );
    const timeout = mapMentions({ links: null, links_error: "lookup_timeout" });
    expect(timeout.links).toBeNull();
    expect(timeout.linksNote).toContain("lookup_timeout");
    const empty = mapMentions(EMPTY);
    expect(empty.links).toEqual([]);
    expect(mapMentions({}).links).toBeNull();
  });

  it("renders the four states distinctly", () => {
    const view = (r: Record<string, unknown>) => {
      const { container, unmount } = render(
        <ReplyEvidence m={mapMentions(r)} />,
      );
      const text = container.textContent ?? "";
      const recs = [...container.querySelectorAll("a[data-record]")].map(
        (a) => (a as HTMLElement).dataset.record,
      );
      unmount();
      return { text, recs };
    };
    const found = view(ok);
    expect(found.recs).toEqual(["trade:pos_1"]);
    expect(found.text).toContain("unresolved ids): spec_missing");
    const none = view(EMPTY);
    expect(none.text).toContain("No stored record was mentioned by id.");
    const failed = view({ links: [], links_error: "lookup_timeout" });
    expect(failed.text).toContain("Stored-record lookup unavailable");
    expect(failed.text).not.toContain("No stored record");
    expect(failed.recs).toEqual([]);
    const bad = view({ links: "x" });
    expect(bad.text).toContain("Stored-record lookup unavailable");
    expect(bad.text).not.toContain("No stored record");
  });
});

// ── malformed evidence at the adapter boundary ──────────────────────────────
function stubRead(body: unknown) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => body,
    }),
  );
  return createLiveAdapter(() => {});
}
const G = { generated_at: "2026-09-29T00:00:00Z", source: "journal" };

const VALID: Record<string, Record<string, unknown>> = {
  "overview/activity": {
    ...G,
    decisions: { window: 0, executed: [], skipped: [], risk_blocks: [] },
    strategies: [],
    lifecycle: [],
    research: null,
    risk_state: null,
    rent_state: null,
    unresolved_owner_requests: [],
    unavailable: [],
  },
  "trades/t1/lineage": {
    ...G,
    trade: { id: "t1" },
    decision: null,
    strategy: null,
    cycle: null,
    votes: [],
    votes_basis: null,
    chain: [{ step: "decision", status: "unavailable", ref: null }],
    links: {},
    accounting: { receipts: [] },
    outcome: null,
    unavailable: [],
  },
  research: {
    ...G,
    available: true,
    counts: {},
    results: [],
    results_page: { offset: 0, limit: 50, has_more: false },
    runs: [],
    evidence: { controls: [], slices: [], gauges: [] },
    candidates: [],
    registrations: [],
    ideas: [],
    unavailable: [],
  },
  "strategies/s1": {
    ...G,
    id: "s1",
    lifecycle: [],
    recent_trades: [],
    journal_economics: null,
    brain_events: [],
    family: { parent: null, children: [], siblings: [] },
    postmortem: null,
    source_idea: null,
    research_seeded: [],
    unavailable: [],
  },
  "operations/activity": {
    ...G,
    window: { decisions: 0 },
    timeline: [],
    risk_blocks: [],
    decisions: [],
    signals: [],
    scans: [],
    orders: [],
    control_events: [],
    signal_cooldowns: { in_newest_500_control_events: 0 },
    unavailable: [],
  },
  "system/observed": {
    ...G,
    note: "records written",
    flows: [
      {
        source: "a",
        target: "b",
        record: "r",
        count: 3,
        status: "observed",
        declared_edge: true,
        saturated: false,
      },
    ],
    declared_edges: [{ source: "a", target: "b" }],
    unobserved_declared_edges: [],
  },
  "knowledge/note?id=x.md": {
    ...G,
    body: "",
    sources: [],
    chronology: [],
    records: [],
    records_error: null,
    records_unresolved: [],
    records_resolved_count: 0,
    records_truncated_count: 0,
    records_unresolved_count: 0,
    family: null,
  },
  diagnostics: {
    ...G,
    storage: { files: [] },
    collectors: [],
    incidents: [],
    owner_audit: [],
    probes: [],
  },
};
const CORRUPT: [string, (v: Record<string, unknown>) => void, string][] = [
  [
    "overview/activity",
    (v) => ((v.decisions as R2).executed = "x"),
    "decisions.executed",
  ],
  [
    "trades/t1/lineage",
    (v) => ((v.chain as R2[])[0].ref = { kind: "trade" }),
    "chain[0].ref.id",
  ],
  ["trades/t1/lineage", (v) => delete v.chain, "chain"],
  ["research", (v) => (v.results = {}), "results"],
  ["research", (v) => (v.results = [{ hash: "" }]), "results[0].hash"],
  ["strategies/s1", (v) => (v.recent_trades = [17]), "recent_trades[0]"],
  ["strategies/s1", (v) => (v.lifecycle = "nope"), "lifecycle"],
  ["operations/activity", (v) => (v.timeline = null), "timeline"],
  [
    "operations/activity",
    (v) => (v.timeline = [{ kind: "decision" }]),
    "timeline[0].title",
  ],
  [
    "system/observed",
    (v) => (v.unobserved_declared_edges = "garbage"),
    "unobserved_declared_edges",
  ],
  ["system/observed", (v) => delete v.declared_edges, "declared_edges"],
  [
    "system/observed",
    (v) => ((v.flows as R2[])[0].count = "3"),
    "flows[0].count",
  ],
  [
    "knowledge/note?id=x.md",
    (v) =>
      (v.records = [
        { kind: "strategy", id: "s1", label: "S", basis: "exact_unique_name" },
      ]),
    "records[0].basis",
  ],
  ["knowledge/note?id=x.md", (v) => (v.records = "none"), "records"],
  [
    "diagnostics",
    (v) => (v.probes = [{ probe: "journal_read", ok: "yes", ms: 1 }]),
    "probes[0].ok",
  ],
];
type R2 = Record<string, unknown>;

describe("owner read contracts reject malformed evidence", () => {
  for (const [path, body] of Object.entries(VALID))
    it(`accepts a valid empty ${path}`, async () => {
      const a = stubRead(structuredClone(body));
      await expect(
        a.ownerRead!(path, new AbortController().signal),
      ).resolves.toBeTruthy();
    });
  for (const [path, corrupt, field] of CORRUPT)
    it(`rejects ${path} with malformed ${field}`, async () => {
      const body = structuredClone(VALID[path]);
      corrupt(body);
      const a = stubRead(body);
      const e = await a.ownerRead!(path, new AbortController().signal).catch(
        (x) => x,
      );
      expect(e).toBeInstanceOf(ContractViolation);
      expect(e.message).toContain("MALFORMED evidence");
      expect(e.message).toContain(field);
    });
});

describe("malformed evidence renders as unavailable, never as empty", () => {
  const renderRead = async (
    path: string,
    corrupt: (v: R2) => void,
    ui: React.ReactNode,
  ) => {
    const body = structuredClone(VALID[path]);
    corrupt(body);
    const live = stubRead(body);
    withAdapter({ ownerRead: live.ownerRead }, ui);
    await waitFor(() =>
      expect(screen.getAllByRole("alert")[0]).toHaveTextContent(
        "MALFORMED evidence",
      ),
    );
  };

  it("operations: a corrupt timeline is not 'no activity'", async () => {
    await renderRead(
      "operations/activity",
      (v) => (v.timeline = "garbage"),
      <OperationsActivity />,
    );
    expect(screen.queryByText(/No directional decision/)).toBeNull();
    expect(screen.queryByTestId("ops-timeline")).toBeNull();
  });

  it("live system: corrupt edges make no completeness claim", async () => {
    await renderRead(
      "system/observed",
      (v) => (v.unobserved_declared_edges = "garbage"),
      <SystemObserved />,
    );
    expect(screen.queryByText(/Every declared edge/)).toBeNull();
    expect(screen.queryByTestId("edges-complete")).toBeNull();
  });

  it("overview: corrupt decisions are not 'no executed decision'", async () => {
    await renderRead(
      "overview/activity",
      (v) => ((v.decisions as R2).executed = { x: 1 }),
      <OverviewEvidence />,
    );
    expect(screen.queryByText(/No executed decision/)).toBeNull();
  });

  it("knowledge: corrupt references are not 'no stored record'", async () => {
    await renderRead(
      "knowledge/note?id=x.md",
      (v) => (v.records = { a: 1 }),
      <NoteDetail id="x.md" />,
    );
    expect(screen.queryByText(/No stored record id appears/)).toBeNull();
  });
});

describe("live system completeness claim", () => {
  const observedWith = (over: R2) => {
    const live = stubRead({
      ...structuredClone(VALID["system/observed"]),
      ...over,
    });
    withAdapter({ ownerRead: live.ownerRead }, <SystemObserved />);
  };

  it("claims completeness only when every declared edge is observed", async () => {
    observedWith({});
    expect(await screen.findByTestId("edges-complete")).toBeTruthy();
  });

  it("a zero-count flow with an empty unobserved list is inconsistent", async () => {
    observedWith({
      flows: [
        {
          source: "a",
          target: "b",
          record: "r",
          count: 0,
          status: "none_in_window",
          declared_edge: true,
          saturated: false,
        },
      ],
    });
    expect(await screen.findByTestId("edges-inconsistent")).toHaveTextContent(
      "No completeness claim is made",
    );
    expect(screen.queryByTestId("edges-complete")).toBeNull();
  });

  it("no declared edges is not completeness", async () => {
    observedWith({ declared_edges: [], flows: [] });
    expect(await screen.findByTestId("edges-none-declared")).toBeTruthy();
    expect(screen.queryByTestId("edges-complete")).toBeNull();
  });
});

describe("display cap never becomes not-found", () => {
  const mention = (i: number) => ({
    kind: "strategy",
    id: `spec_review_${String(i).padStart(3, "0")}`,
    label: `Review ${i}`,
    basis: "exact_id",
  });
  const capped = (valid: number, missing: string[]) => ({
    links: Array.from({ length: Math.min(valid, 50) }, (_, i) => mention(i)),
    links_error: null,
    unresolved: missing.map((token) => ({ token, kind_hint: "strategy" })),
    resolved_count: valid,
    truncated_count: Math.max(0, valid - 50),
    unresolved_count: missing.length,
  });

  it("51 valid ids: 50 shown, 1 truncated, none unresolved", () => {
    const m = mapMentions(capped(51, []));
    expect(m.links).toHaveLength(50);
    expect(m.counts).toEqual({ resolved: 51, truncated: 1, unresolved: 0 });
    expect(m.unresolved).toEqual([]);
    const { container } = render(<ReplyEvidence m={m} />);
    expect(container.textContent).toContain(
      "50 of 51 matched stored records shown.",
    );
    expect(container.textContent).not.toContain("Not found");
    expect(container.querySelectorAll("a[data-record]")).toHaveLength(50);
  });

  it("60 valid + 2 missing: 10 truncated, only the 2 unresolved", () => {
    const m = mapMentions(capped(60, ["spec_review_900", "spec_review_901"]));
    expect(m.counts).toEqual({ resolved: 60, truncated: 10, unresolved: 2 });
    const { container } = render(<ReplyEvidence m={m} />);
    expect(container.textContent).toContain(
      "50 of 60 matched stored records shown.",
    );
    expect(container.textContent).toContain(
      "unresolved ids): spec_review_900, spec_review_901",
    );
  });

  it("exactly 50 valid: nothing truncated, no truncation note", () => {
    const m = mapMentions(capped(50, []));
    expect(m.counts).toEqual({ resolved: 50, truncated: 0, unresolved: 0 });
    const { container } = render(<ReplyEvidence m={m} />);
    expect(container.textContent).not.toContain("matched stored records shown");
  });

  it("counts that disagree with the lists are malformed, not evidence", () => {
    for (const bad of [
      { ...capped(51, []), truncated_count: 0 },
      { ...capped(51, []), resolved_count: 50 },
      { ...capped(10, ["x_missing"]), unresolved_count: 0 },
      { ...capped(10, []), resolved_count: undefined },
    ]) {
      const m = mapMentions(bad);
      expect(m.links).toBeNull();
      expect(m.linksNote).toBe(
        "Stored-record lookup unavailable (malformed response).",
      );
    }
  });

  it("a note whose counts disagree with its records is rejected", async () => {
    const body = structuredClone(VALID["knowledge/note?id=x.md"]);
    body.records_truncated_count = 3;
    const a = stubRead(body);
    const e = await a.ownerRead!(
      "knowledge/note?id=x.md",
      new AbortController().signal,
    ).catch((x) => x);
    expect(e).toBeInstanceOf(ContractViolation);
    expect(e.message).toContain("records counts do not agree");
  });

  it("a truncated note says how many matches are shown", () => {
    render(
      <NoteRecords
        d={{
          records: capped(51, []).links,
          records_resolved_count: 51,
          records_truncated_count: 1,
          records_unresolved: [],
          records_basis: "exact ids only",
        }}
      />,
    );
    expect(screen.getByTestId("note-records-truncated")).toHaveTextContent(
      "50 of 51 matched stored records shown.",
    );
    expect(screen.queryByTestId("note-unresolved")).toBeNull();
  });
});
