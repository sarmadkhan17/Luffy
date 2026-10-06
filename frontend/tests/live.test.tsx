import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  BackendUnavailable,
  SessionExpired,
  createLiveAdapter,
  createTransport,
  mapOverview,
  mapKnowledge,
} from "../src/adapters/live";
import * as pending from "../src/adapters/pending";
import { PreviewProvider } from "../src/context";
import Operations from "../src/views/Operations";
import Luffy from "../src/views/Luffy";
import { protectionTone, currentPositionPnl } from "../src/views/Overview";
import type { OwnerAdapter } from "../src/adapters/contracts";

const now = new Date().toISOString();
const overview = (over: Record<string, unknown> = {}) => ({
  mode: "LIVE",
  generated_at: now,
  errors: {},
  control: { state: "FROZEN", observed_at: now, last_event: null },
  heartbeat: { observed_at: now, age_s: 5, freshness: "fresh", stale_after_s: 240 },
  account: { equity: 100, currency: "USDT", observed_at: now, freshness: "fresh" },
  equity_series: { points: [{ time: 1, value: 100 }], window_hours: 168, bucket: "b" },
  realized_today: { value: 0, closed_trades: 0 },
  exposure: null,
  positions: [
    {
      id: "t1",
      symbol: "BTC/USDT",
      side: "long",
      notional_usdt: 50,
      opened_at: now,
      journal_stop: { price: 1, order_ref_recorded: true },
      protection: { status: "STALE", observed_at: now, last_reported: "VERIFIED", reasons: [] },
    },
  ],
  protection: { status: "STALE", snapshot: null, supervisor: null },
  needs_you: null,
  ...over,
});
const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status }));

describe("live adapter", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("unrealized estimates use exact trade quotes and expire through failed polling", () => {
    const p = {id:"t1",symbol:"BTC/USDT"} as import("../src/adapters/contracts").Position;
    const m = {observedAt:now, marks:{}, evidence:{by_trade:{t1:{upnl:5,quote_age_s:10}}}};
    expect(currentPositionPnl(p,m,1000,1000,false)).toBe(5);
    expect(currentPositionPnl(p,m,1000,52000,false)).toBeNull();
    expect(currentPositionPnl(p,m,1000,1000,true)).toBeNull();
    expect(currentPositionPnl(p,{observedAt:now,marks:{"BTC/USDT":{upnl:5,mark:10}}},1000,1000,false)).toBeNull();
  });
  it("keeps STALE protection stale and never marks it healthy", () => {
    const d = mapOverview(overview());
    expect(d.positions![0].protection).toBe("STALE");
    expect(d.positions![0].protectionDetail!.lastReported).toBe("VERIFIED");
    expect(protectionTone("STALE")).not.toBe("mint");
    expect(protectionTone("UNVERIFIED")).not.toBe("mint");
    expect(protectionTone("VERIFIED")).toBe("mint");
    expect(d.positions![0].pnl).toBeNull(); // local overview never invents marks
  });
  it("maps missing sections to null, not zero", () => {
    const d = mapOverview(
      overview({ account: null, control: null, exposure: null, errors: { account: "x" } }),
    );
    expect(d.equity).toBeNull();
    expect(d.exposure).toBeNull();
    expect(d.control).toBe("UNAVAILABLE");
    expect(d.provenance.freshness).toBe("unavailable");
  });
  it("retains per-figure coverage and global signal evidence without filling unknowns", () => {
    const realized = { value: null, quality: "PARTIAL_UNKNOWN", version: "overview-journal-realized.v1", coverage: { known: 1, total: 2, complete: false } };
    const d = mapOverview(overview({ realized_today: realized, news_guard: null, risk: { configured: { limit: 2 }, current: { status: "UNAVAILABLE" } } }));
    expect(d.pnl).toBeNull();
    expect(d.live?.observedEvidence?.realized_today).toEqual(realized);
    expect(d.live?.observedEvidence?.news_guard).toBeNull();
    expect(d.live?.observedEvidence?.risk).toEqual({ configured: { limit: 2 }, current: { status: "UNAVAILABLE" } });
  });
  it("refuses a response that is not the LIVE contract", () => {
    expect(() => mapOverview({ mode: "DEMO", errors: {} })).toThrow("contract");
    expect(() => mapKnowledge({ nodes: "x" })).toThrow("contract");
  });
  it("401 ends the session; network failure is BackendUnavailable", async () => {
    const onUnauthorized = vi.fn();
    const api = createTransport(onUnauthorized);
    vi.stubGlobal("fetch", vi.fn(() => json({ error: "authentication required" }, 401)));
    await expect(api("/x")).rejects.toBeInstanceOf(SessionExpired);
    expect(onUnauthorized).toHaveBeenCalledOnce();
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    await expect(api("/x")).rejects.toBeInstanceOf(BackendUnavailable);
    vi.stubGlobal("fetch", vi.fn(() => json({ error: "boom" }, 500)));
    await expect(api("/x")).rejects.toThrow("No substitute data");
  });
  it("control: lost answer resolves null (keep the id), never throws a fallback", async () => {
    const adapter = createLiveAdapter(() => undefined);
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("offline"))));
    expect(
      await adapter.control!("freeze", { action: "freeze", id: "a".repeat(32), t: 1 }),
    ).toBeNull();
    const fetchMock = vi.fn(() =>
      json({ data: { res: { status: "ACCEPTED", disposition: "COMPLETED", reasons: [] } } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const r = await adapter.control!("resume", { action: "resume", id: "b".repeat(32), t: 7 });
    expect(r!.status).toBe("ACCEPTED");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/graphql");
    const body = JSON.parse(init.body as string);
    expect(body.query).toContain("owner_control");
    expect(body.variables).toEqual({ o: "resume", r: "b".repeat(32), t: 7 });
  });
  it("chat posts only to the chat endpoint", async () => {
    const adapter = createLiveAdapter(() => undefined);
    const fetchMock = vi.fn(() =>
      json({ reply: "ok", operational: false, request_id: "r1", evidence: [] }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const reply = await adapter.chat("freeze now", "normal", new AbortController().signal, () => {});
    expect(reply).toMatchObject({ text: "ok", evidence: [], requestId: "r1" });
    // a backend without mention fields reports no lookup: unavailable, not "none"
    expect(reply.links).toBeNull();
    expect(reply.linksNote).toBe(
      "Stored-record lookup unavailable (this backend reports no lookup).",
    );
    expect(fetchMock.mock.calls.map((c) => (c as unknown as [string])[0])).toEqual([
      "/owner-api/v1/chat",
    ]);
  });
});

describe("pending owner requests", () => {
  beforeEach(() => localStorage.clear());
  it("one key per id; oldest reused; completion removes only its own key", () => {
    const put = (id: string, t: number) =>
      localStorage.setItem(pending.PREFIX + id, JSON.stringify({ action: "halt", id, t }));
    put("zz-older-request-0001", 1);
    put("aa-newer-request-0002", 2);
    const a = { id: "zz-older-request-0001" };
    const b = { id: "aa-newer-request-0002" };
    expect(pending.find("halt")!.id).toBe(a.id); // oldest by issue time
    expect(pending.create("halt").durable).toBe(true);
    pending.done(a.id);
    expect(pending.find("halt")!.id).toBe(b.id);
    expect(localStorage.getItem(pending.PREFIX + b.id)).not.toBeNull();
  });
});

function withProvider(adapter: OwnerAdapter, ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PreviewProvider adapter={adapter}>{ui}</PreviewProvider>
    </QueryClientProvider>,
  );
}
const base = {
  mode: "LIVE" as const,
  overview: vi.fn(),
  graph: vi.fn(),
  chat: vi.fn(),
};

describe("owner controls", () => {
  beforeEach(() => localStorage.clear());
  it("fail closed when the Owner Interface is unavailable", async () => {
    const control = vi.fn();
    withProvider(
      {
        ...base,
        control,
        ownerInterface: vi.fn(async () => ({
          availability: "UNAVAILABLE" as const,
          status: "UNAVAILABLE",
          reasons: ["kernel_unavailable"],
          observedAt: null,
          controlState: null,
          recoveryInProgress: null,
        })),
      },
      <Operations />,
    );
    expect(await screen.findByTestId("controls-closed")).toBeVisible();
    for (const b of screen.getAllByRole("button").filter((b) => b.dataset.op))
      expect(b).toBeDisabled();
    expect(control).not.toHaveBeenCalled();
  });
  it("fail closed when the health read itself fails", async () => {
    withProvider(
      {
        ...base,
        control: vi.fn(),
        ownerInterface: vi.fn(async () => {
          throw new BackendUnavailable("Backend unreachable.");
        }),
      },
      <Operations />,
    );
    expect(await screen.findByText(/Health read failed/)).toBeVisible();
    for (const b of screen.getAllByRole("button").filter((b) => b.dataset.op))
      expect(b).toBeDisabled();
  });
  it("Resume is sent to the Owner Interface with a persisted request id", async () => {
    const control = vi.fn(async () => ({
      requestId: "dashboard-x",
      operation: "resume",
      status: "CONTAINED",
      disposition: "COMPLETED",
      controlStateBefore: "FROZEN",
      controlStateAfter: "FROZEN",
      reasons: ["owner_resume_required"],
      supervisorOutcome: null,
      auditEventIds: [],
      replayed: false,
      message: "contained",
    }));
    const user = userEvent.setup();
    withProvider(
      {
        ...base,
        control,
        ownerInterface: vi.fn(async () => ({
          availability: "AVAILABLE" as const,
          status: "ACCEPTED",
          reasons: [],
          observedAt: now,
          controlState: "FROZEN",
          recoveryInProgress: false,
        })),
      },
      <Operations />,
    );
    const resume = await screen.findByRole("button", { name: "Resume (guarded)" });
    await waitFor(() => expect(resume).toBeEnabled());
    await user.click(resume);
    await user.click(screen.getByRole("button", { name: /Send Resume/ }));
    expect(await screen.findByTestId("control-outcome")).toHaveTextContent("CONTAINED");
    expect(control).toHaveBeenCalledOnce();
    const [op, req] = control.mock.calls[0] as unknown as [string, { id: string; t: number }];
    expect(op).toBe("resume");
    expect(req.id).toMatch(/^[A-Za-z0-9-]{16,64}$/);
    expect(pending.find("resume")).toBeNull(); // definitive → released
  });
});

describe("LUFFY chat in LIVE mode", () => {
  it("cannot reach controls", async () => {
    const control = vi.fn();
    const chat = vi.fn(async () => ({ text: "noted", evidence: [] }));
    const user = userEvent.setup();
    withProvider({ ...base, chat, control }, <Luffy />);
    await user.type(screen.getByLabelText("Message LUFFY"), "freeze everything");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByText("noted")).toBeVisible();
    expect(chat).toHaveBeenCalledOnce();
    expect(control).not.toHaveBeenCalled();
  });
});
