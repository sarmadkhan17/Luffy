/** LUFFY-OWNER-CLOSE-TRADE-UI-R1 is DEFERRED / SAFETY_BLOCKED (owner decision
 * 2026-09-29, pending LUFFY-PER-TRADE-EXIT-ATTRIBUTION-R1). The React frontend
 * must not render or expose a close-one-trade control until then. */
import { describe, it, expect, vi } from "vitest";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createLiveAdapter } from "../src/adapters/live";
import { PreviewProvider } from "../src/context";
import Routes from "../src/views/Routes";
import type { OwnerAdapter, TradePage } from "../src/adapters/contracts";

const open = {
  id: "t-btc",
  symbol: "BTC/USDT",
  side: "long",
  amount: 0.01,
  status: "open",
  opened_at: "2026-09-29T00:00:00+00:00",
};
const page: TradePage = {
  generatedAt: "2026-09-29T00:00:00+00:00",
  source: "journal trades",
  status: "all",
  limit: 50,
  rows: [open],
  cursor: null,
  nextCursor: null,
  hasMore: false,
  preceding: 0,
  total: 1,
  first: null,
  last: null,
  history: { changed: false, reason: null },
};

describe("Close Trade is deferred (safety-blocked)", () => {
  it("an open trade's record offers no close control", async () => {
    const u = userEvent.setup();
    const adapter = {
      mode: "LIVE",
      overview: vi.fn(),
      graph: vi.fn(),
      chat: vi.fn(),
      tradePage: vi.fn(async () => page),
    } as unknown as OwnerAdapter;
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <PreviewProvider adapter={adapter}>
          <Routes route="trades" />
        </PreviewProvider>
      </QueryClientProvider>,
    );
    await u.click(
      await screen.findByRole("button", { name: "Inspect BTC/USDT" }),
    );
    const drawer = screen.getByRole("dialog", { name: "BTC/USDT" });
    expect(drawer).toHaveTextContent("open");
    expect(
      within(drawer).queryByRole("button", { name: /close trade|send close/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /close trade/i }),
    ).not.toBeInTheDocument();
  });

  it("the LIVE adapter exposes no close-trade action", () => {
    expect("closeTrade" in (createLiveAdapter(() => {}) as object)).toBe(false);
  });

  it("no React source sends the close_trade mutation", () => {
    const files: string[] = [];
    const walk = (d: string) => {
      for (const f of readdirSync(d)) {
        const p = join(d, f);
        if (statSync(p).isDirectory()) walk(p);
        else if (/\.(ts|tsx)$/.test(f)) files.push(p);
      }
    };
    walk(join(__dirname, "../src"));
    for (const f of files)
      expect(readFileSync(f, "utf8"), f).not.toMatch(/close_trade\s*\(/);
  });
});
