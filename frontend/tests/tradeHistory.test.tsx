import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createLiveAdapter,
  ContractViolation,
  SessionExpired,
} from "../src/adapters/live";
import { PreviewProvider } from "../src/context";
import Routes from "../src/views/Routes";
import type {
  OwnerAdapter,
  TradePage,
  TradeStatusFilter,
} from "../src/adapters/contracts";

afterEach(() => vi.unstubAllGlobals());

const wire = (over: Record<string, unknown> = {}, page = {}) => ({
  generated_at: "2026-09-29T00:00:00+00:00",
  source: "journal trades",
  status: "all",
  limit: 50,
  trades: [
    { id: "t1", symbol: "SOL/USDT", opened_at: "2026-09-28T00:00:00+00:00" },
  ],
  page: {
    cursor: null,
    next_cursor: "c1",
    has_more: true,
    preceding: 0,
    total: 120,
    first: { opened_at: "2026-09-28T00:00:00+00:00", id: "t1" },
    last: { opened_at: "2026-09-28T00:00:00+00:00", id: "t1" },
    history: { changed: false, reason: null },
    ...page,
  },
  ...over,
});
const respond = (body: unknown, status = 200) =>
  vi
    .fn()
    .mockResolvedValue({ ok: status < 400, status, json: async () => body });
const signal = () => new AbortController().signal;
const q = (
  cursor: string | null = null,
  status: TradeStatusFilter = "all",
) => ({
  status,
  cursor,
  limit: 50,
});

describe("trade history adapter contract", () => {
  it("requests keyset pages and maps the page contract", async () => {
    const fetch = respond(wire({}, { cursor: "c0", preceding: 50 }));
    vi.stubGlobal("fetch", fetch);
    const p = await createLiveAdapter(() => {}).tradePage!(q("c0"), signal());
    const url = new URL(fetch.mock.calls[0][0], "http://x");
    expect(url.pathname).toBe("/owner-api/v1/trades");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      status: "all",
      limit: "50",
      cursor: "c0",
    });
    expect(p).toMatchObject({
      nextCursor: "c1",
      hasMore: true,
      preceding: 50,
      total: 120,
      history: { changed: false, reason: null },
    });
  });
  it.each([
    ["missing preceding", wire({}, { preceding: undefined })],
    ["negative total", wire({}, { total: -1 })],
    ["has_more without cursor", wire({}, { next_cursor: null })],
    ["cursor on the last page", wire({}, { has_more: false })],
    ["another page's response", wire({}, { cursor: "other" })],
    ["another filter's response", wire({ status: "open" })],
    ["more rows than the limit", wire({ limit: 0 })],
    ["no trades array", wire({ trades: null })],
    ["malformed edge", wire({}, { first: { id: "t1" } })],
    ["missing history", wire({}, { history: undefined })],
    [
      "history changed as a string",
      wire({}, { history: { changed: "true", reason: null } }),
    ],
    [
      "a first page claiming a change",
      wire({}, { history: { changed: true, reason: "x" } }),
    ],
  ])("rejects %s instead of rendering it", async (_, body) => {
    vi.stubGlobal("fetch", respond(body));
    await expect(
      createLiveAdapter(() => {}).tradePage!(q(), signal()),
    ).rejects.toBeInstanceOf(ContractViolation);
  });
  it("surfaces an invalid cursor as an explicit error code", async () => {
    vi.stubGlobal("fetch", respond({ error: "invalid_cursor" }, 400));
    await expect(
      createLiveAdapter(() => {}).tradePage!(q("bad"), signal()),
    ).rejects.toMatchObject({ code: "invalid_cursor" });
  });
  it("signals session expiry", async () => {
    vi.stubGlobal("fetch", respond({ error: "authentication required" }, 401));
    const expired = vi.fn();
    await expect(
      createLiveAdapter(expired).tradePage!(q(), signal()),
    ).rejects.toBeInstanceOf(SessionExpired);
    expect(expired).toHaveBeenCalled();
  });
});

// ── the Trades page over a controllable adapter ────────────────────────────
type Call = {
  query: { status: TradeStatusFilter; cursor: string | null };
  signal: AbortSignal;
  resolve: (p: TradePage) => void;
  reject: (e: Error) => void;
};
function rows(from: number, n: number, status = "closed") {
  return Array.from({ length: n }, (_, i) => ({
    id: `t${from + i}`,
    symbol: `PAIR${from + i}/USDT`,
    side: "long",
    status,
    realized_pnl: 1,
    opened_at: "2026-09-28T00:00:00+00:00",
  }));
}
function page(
  query: Call["query"],
  over: Partial<TradePage> & { rows: Record<string, unknown>[] },
): TradePage {
  return {
    generatedAt: "2026-09-29T00:00:00+00:00",
    source: "journal trades",
    status: query.status,
    cursor: query.cursor,
    limit: 50,
    nextCursor: null,
    hasMore: false,
    preceding: 0,
    total: over.rows.length,
    first: { opened_at: "2026-09-28T00:00:00+00:00", id: "a" },
    last: { opened_at: "2026-09-28T00:00:00+00:00", id: "b" },
    history: { changed: false, reason: null },
    ...over,
  };
}
function harness() {
  const calls: Call[] = [];
  const adapter = {
    mode: "LIVE",
    overview: vi.fn(),
    graph: vi.fn(),
    chat: vi.fn(),
    tradePage: (query: Call["query"], s: AbortSignal) =>
      new Promise<TradePage>((resolve, reject) =>
        calls.push({ query, signal: s, resolve, reject }),
      ),
  } as unknown as OwnerAdapter;
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <PreviewProvider adapter={adapter}>
        <Routes route="trades" />
      </PreviewProvider>
    </QueryClientProvider>,
  );
  const last = () => calls[calls.length - 1];
  return { calls, client, last };
}
/** Resolve a pending request and let React Query deliver it. */
const settle = (fn: () => void) =>
  act(async () => {
    fn();
    await new Promise((r) => setTimeout(r, 20));
  });
const range = () => screen.getByTestId("trade-range").textContent ?? "";
const bookTable = () => screen.getAllByRole("table")[0];

describe("Trades page paging", () => {
  it("pages forward and back with exact positions and keeps the search", async () => {
    const u = userEvent.setup();
    const h = harness();
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await settle(() =>
      h
        .last()
        .resolve(
          page(h.last().query, {
            rows: rows(0, 50),
            hasMore: true,
            nextCursor: "c1",
            total: 120,
          }),
        ),
    );
    expect(range()).toContain("Records 1–50 of 120 trades");
    expect(
      screen.getByPlaceholderText("Search this loaded page…"),
    ).toBeInTheDocument();
    await u.type(screen.getByLabelText("Search Trades"), "PAIR5");
    await u.click(screen.getByRole("button", { name: "Next page" }));
    expect(h.last().query).toEqual({ status: "all", cursor: "c1", limit: 50 });
    await settle(() =>
      h
        .last()
        .resolve(
          page(h.last().query, {
            rows: rows(50, 50),
            preceding: 50,
            hasMore: true,
            nextCursor: "c2",
            total: 120,
          }),
        ),
    );
    expect(range()).toContain("Records 51–100 of 120 trades · page 2");
    expect(screen.getByLabelText("Search Trades")).toHaveValue("PAIR5");
    expect(screen.queryByTestId("history-changed")).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Previous page" }));
    expect(range()).toContain("Records 1–50"); // cached page 1, no new request
  });

  it("a late response for a page no longer on screen never overwrites it", async () => {
    const u = userEvent.setup();
    const h = harness();
    await waitFor(() => expect(h.calls).toHaveLength(1));
    const first = h.last();
    await settle(() =>
      first.resolve(
        page(first.query, {
          rows: rows(0, 50),
          hasMore: true,
          nextCursor: "c1",
        }),
      ),
    );
    await u.click(screen.getByRole("button", { name: "Next page" }));
    const second = h.last();
    expect(second.query.cursor).toBe("c1");
    await u.click(screen.getByRole("button", { name: "Previous page" }));
    await settle(() =>
      second.resolve(page(second.query, { rows: rows(50, 50), preceding: 50 })),
    );
    expect(range()).toContain("Records 1–50");
    expect(bookTable()).toHaveTextContent("PAIR0/USDT");
    expect(bookTable()).not.toHaveTextContent("PAIR50/USDT");
  });

  it("a filter change while a request is pending shows only the new filter and restarts paging", async () => {
    const u = userEvent.setup();
    const h = harness();
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await settle(() =>
      h
        .last()
        .resolve(
          page(h.last().query, {
            rows: rows(0, 50),
            hasMore: true,
            nextCursor: "c1",
          }),
        ),
    );
    await u.click(screen.getByRole("button", { name: "Next page" }));
    await u.selectOptions(screen.getByLabelText("Filter Trades"), "open");
    const openCall = h.last();
    expect(openCall.query).toEqual({ status: "open", cursor: null, limit: 50 });
    await u.selectOptions(screen.getByLabelText("Filter Trades"), "closed");
    const closedCall = h.last();
    await settle(() =>
      closedCall.resolve(
        page(closedCall.query, { rows: rows(200, 3, "closed") }),
      ),
    );
    await settle(() =>
      openCall.resolve(page(openCall.query, { rows: rows(300, 2, "open") })),
    );
    expect(range()).toContain("Records 1–3 of 3 closed trades · page 1");
    expect(bookTable()).toHaveTextContent("PAIR200/USDT");
    expect(bookTable()).not.toHaveTextContent("PAIR300/USDT");
  });

  it("a newer trade is reported as newer, not as a history change", async () => {
    const u = userEvent.setup();
    const h = harness();
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await settle(() =>
      h.last().resolve(
        page(h.last().query, {
          rows: rows(0, 50),
          hasMore: true,
          nextCursor: "c1",
        }),
      ),
    );
    await u.click(screen.getByRole("button", { name: "Next page" }));
    await settle(() =>
      h.last().resolve(
        page(h.last().query, { rows: rows(50, 50), preceding: 51, total: 121 }),
      ),
    );
    expect(range()).toContain("Records 52–101 of 121");
    expect(screen.getByTestId("history-newer")).toHaveTextContent(
      "1 newer matching trade(s) were booked after this traversal began",
    );
    expect(screen.queryByTestId("history-changed")).not.toBeInTheDocument();
  });

  it("a history change warns even when counts match, persists, and clears only at Newest", async () => {
    const u = userEvent.setup();
    const h = harness();
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await settle(() =>
      h.last().resolve(
        page(h.last().query, {
          rows: rows(0, 50),
          hasMore: true,
          nextCursor: "c1",
          total: 150,
        }),
      ),
    );
    await u.click(screen.getByRole("button", { name: "Next page" }));
    // counts are exactly as expected; only the server's history check differs
    await settle(() =>
      h.last().resolve(
        page(h.last().query, {
          rows: rows(50, 50),
          preceding: 50,
          total: 150,
          hasMore: true,
          nextCursor: "c2",
          history: { changed: true, reason: "membership_changed" },
        }),
      ),
    );
    const warning =
      "Trade history changed while you were paging. Restart from Newest for a complete current view.";
    expect(screen.getByTestId("history-changed")).toHaveTextContent(warning);
    expect(screen.queryByTestId("history-newer")).not.toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "Next page" }));
    // still shown while the next page loads
    expect(screen.getByTestId("history-changed")).toHaveTextContent(warning);
    await settle(() =>
      h.last().resolve(
        page(h.last().query, {
          rows: rows(100, 50),
          preceding: 100,
          total: 150,
          history: { changed: true, reason: "earlier_page" },
        }),
      ),
    );
    expect(screen.getByTestId("history-changed")).toHaveTextContent(warning);
    // walking back to a page read before the change keeps the warning
    await u.click(screen.getByRole("button", { name: "Previous page" }));
    await u.click(screen.getByRole("button", { name: "Previous page" }));
    expect(range()).toContain("Records 1–50");
    expect(screen.getByTestId("history-changed")).toHaveTextContent(warning);
    // restarting from Newest (enabled on page 1 while warned) starts a fresh read
    const before = h.calls.length;
    await u.click(screen.getByRole("button", { name: "Newest" }));
    expect(screen.queryByTestId("history-changed")).not.toBeInTheDocument();
    await waitFor(() => expect(h.calls.length).toBeGreaterThan(before));
    expect(h.last().query.cursor).toBeNull();
    await settle(() =>
      h.last().resolve(page(h.last().query, { rows: rows(0, 50), total: 151 })),
    );
    expect(screen.queryByTestId("history-changed")).not.toBeInTheDocument();
  });

  it("an unverifiable cursor warns that completeness cannot be confirmed", async () => {
    const u = userEvent.setup();
    const h = harness();
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await settle(() =>
      h.last().resolve(
        page(h.last().query, {
          rows: rows(0, 50),
          hasMore: true,
          nextCursor: "c1",
        }),
      ),
    );
    await u.click(screen.getByRole("button", { name: "Next page" }));
    await settle(() =>
      h.last().resolve(
        page(h.last().query, {
          rows: rows(50, 50),
          preceding: 50,
          history: { changed: null, reason: "unverifiable_cursor" },
        }),
      ),
    );
    expect(screen.getByTestId("history-changed")).toHaveTextContent(
      "changes to earlier pages cannot be ruled out. Restart from Newest",
    );
  });

  it("changing the filter restarts the traversal and clears the warning", async () => {
    const u = userEvent.setup();
    const h = harness();
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await settle(() =>
      h.last().resolve(
        page(h.last().query, {
          rows: rows(0, 50),
          hasMore: true,
          nextCursor: "c1",
        }),
      ),
    );
    await u.click(screen.getByRole("button", { name: "Next page" }));
    await settle(() =>
      h.last().resolve(
        page(h.last().query, {
          rows: rows(50, 50),
          preceding: 50,
          history: { changed: true, reason: "membership_changed" },
        }),
      ),
    );
    expect(screen.getByTestId("history-changed")).toBeInTheDocument();
    await u.selectOptions(screen.getByLabelText("Filter Trades"), "open");
    expect(screen.queryByTestId("history-changed")).not.toBeInTheDocument();
    expect(h.last().query).toEqual({ status: "open", cursor: null, limit: 50 });
  });

  it("keeps the inspected trade open when a refresh adds a newer row", async () => {
    const u = userEvent.setup();
    const h = harness();
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await settle(() =>
      h.last().resolve(page(h.last().query, { rows: rows(0, 3) })),
    );
    await u.click(screen.getByRole("button", { name: "Inspect PAIR1/USDT" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("t1");
    void h.client.refetchQueries({ queryKey: ["trades"] });
    await waitFor(() => expect(h.calls).toHaveLength(2));
    await settle(() =>
      h
        .last()
        .resolve(
          page(h.last().query, { rows: [...rows(99, 1), ...rows(0, 3)] }),
        ),
    );
    expect(range()).toContain("Records 1–4 of 4");
    expect(screen.getByRole("dialog")).toHaveTextContent("t1");
  });

  it("an invalid cursor shows the error and offers the newest page", async () => {
    const u = userEvent.setup();
    const h = harness();
    await waitFor(() => expect(h.calls).toHaveLength(1));
    await settle(() =>
      h
        .last()
        .resolve(
          page(h.last().query, {
            rows: rows(0, 50),
            hasMore: true,
            nextCursor: "c1",
          }),
        ),
    );
    await u.click(screen.getByRole("button", { name: "Next page" }));
    await settle(() =>
      h
        .last()
        .reject(
          new Error(
            "Backend error 400 (invalid_cursor). No substitute data was loaded.",
          ),
        ),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("invalid_cursor");
    expect(range()).not.toContain("Records");      // no rows are claimed for the page
    await u.click(
      screen.getByRole("button", { name: "Return to newest trades" }),
    );
    // a restart is a fresh traversal: page 1 is read again, not reused
    expect(h.last().query.cursor).toBeNull();
    await settle(() =>
      h.last().resolve(page(h.last().query, { rows: rows(0, 50) })),
    );
    expect(range()).toContain("Records 1–50");
  });

  it("without the LIVE capability the book is explicitly unavailable", () => {
    const client = new QueryClient();
    render(
      <QueryClientProvider client={client}>
        <PreviewProvider
          adapter={
            {
              mode: "DEMO",
              overview: vi.fn(),
              graph: vi.fn(),
              chat: vi.fn(),
            } as unknown as OwnerAdapter
          }
        >
          <Routes route="trades" />
        </PreviewProvider>
      </QueryClientProvider>,
    );
    expect(
      screen.getByText(/served by the LIVE journal endpoint only/),
    ).toBeInTheDocument();
  });
});
