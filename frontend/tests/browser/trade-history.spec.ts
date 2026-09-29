import { test, expect, type Page, type Route } from "@playwright/test";
import { LIVE, H, login, state } from "./liveHelpers";

// LIVE build over the real FastAPI app and a temporary fixture journal:
// 3 scenario trades (BTC, ETH open; SOL closed) + N history rows H0000…
const HISTORY = 180;
const TOTAL = HISTORY + 3;

const book = (page: Page) =>
  page.locator("section").filter({
    has: page.getByRole("heading", { name: "Trade book", exact: true }),
  });
const paging = (page: Page) =>
  page.getByRole("group", { name: "Trade history paging" });
const range = (page: Page) => page.getByTestId("trade-range");
const next = (page: Page) =>
  paging(page).getByRole("button", { name: "Next page" });
const previous = (page: Page) =>
  paging(page).getByRole("button", { name: "Previous page" });
async function symbols(page: Page): Promise<string[]> {
  return book(page).locator("table tbody tr td:first-child").allTextContents();
}
/** The fixture's own timestamp format, `hoursBack` before H0000 (now − 3 days). */
function historyTime(hoursBack: number): string {
  const t = Date.now() - 3 * 86400_000 - hoursBack * 3600_000;
  return new Date(t).toISOString().replace(/\.\d{3}Z$/, "+00:00");
}
const WARNING =
  "Trade history changed while you were paging. Restart from Newest for a complete current view.";
async function openTrades(page: Page) {
  await login(page);
  await page.getByRole("link", { name: "Trades", exact: true }).click();
  await expect(range(page)).toContainText("Records 1–");
}
/** Hold matching requests until released; late ones are delivered afterwards. */
function gate(page: Page, match: (url: URL) => boolean) {
  let release!: () => void;
  const opened = new Promise<void>((r) => (release = r));
  const held: string[] = [];
  void page.route("**/owner-api/v1/trades*", async (route: Route) => {
    const url = new URL(route.request().url());
    if (!match(url)) return route.continue();
    held.push(url.search);
    await opened;
    await route.continue().catch(() => undefined); // aborted by the client
  });
  return { release, held };
}

test.beforeEach(async ({ request }) => {
  await state(request, { scenario: "normal", fail: [] });
  await state(request, { history: HISTORY });
});
test.afterAll(async ({ request }) => {
  await state(request, { scenario: "normal", fail: [] });
});

test("pages through the full history with exact ranges and legacy parity", async ({
  page,
  request,
}) => {
  await openTrades(page);
  const seen: string[] = [];
  for (let p = 1; ; p++) {
    const from = (p - 1) * 50 + 1;
    const to = Math.min(p * 50, TOTAL);
    await expect(range(page)).toContainText(
      `Records ${from}–${to} of ${TOTAL} trades · page ${p}`,
    );
    seen.push(...(await symbols(page)));
    if (to === TOTAL) break;
    await next(page).click();
  }
  await expect(next(page)).toBeDisabled();
  expect(seen).toHaveLength(TOTAL);
  expect(new Set(seen).size).toBe(TOTAL); // no duplicate
  expect(seen[0]).toBe("ETH/USDT"); // newest first
  expect(seen[TOTAL - 1]).toBe(`H${String(HISTORY - 1).padStart(4, "0")}/USDT`);
  // the legacy offset view reaches the same records — nothing is only there
  const legacy: string[] = [];
  for (let offset = 0; ; offset += 200) {
    const r = await request.post(LIVE + "/graphql", {
      headers: H,
      data: {
        query: `{ trades(limit: 200, offset: ${offset}) { symbol } }`,
      },
    });
    const rows = (await r.json()).data.trades as { symbol: string }[];
    legacy.push(...rows.map((t) => t.symbol));
    if (rows.length < 200) break;
  }
  expect([...seen].sort()).toEqual([...legacy].sort());
  // Previous walks back through the same pages
  await previous(page).click();
  await expect(range(page)).toContainText(`Records 101–150 of ${TOTAL}`);
});

test("a trade inserted after page 1 is neither duplicated nor silently absorbed", async ({
  page,
  request,
}) => {
  await openTrades(page);
  const first = await symbols(page);
  await state(request, {
    insert_trade: {
      id: "t-late-new",
      symbol: "NEW/USDT",
      opened_at: new Date().toISOString(),
    },
  });
  await next(page).click();
  await expect(range(page)).toContainText(`Records 52–101 of ${TOTAL + 1}`);
  // newer than the traversal's first row: new, not a change to earlier pages
  await expect(page.getByTestId("history-newer")).toContainText(
    "1 newer matching trade(s) were booked after this traversal began",
  );
  await expect(page.getByTestId("history-changed")).toHaveCount(0);
  const second = await symbols(page);
  expect(second.filter((s) => first.includes(s))).toEqual([]);
  expect(second).not.toContain("NEW/USDT");
  await paging(page).getByRole("button", { name: "Newest" }).click();
  await expect(book(page).locator("table")).toContainText("NEW/USDT");
  await expect(page.getByTestId("history-changed")).toHaveCount(0);
});

test("a backdated trade behind the cursor warns on every later page until Newest", async ({
  page,
  request,
}) => {
  await openTrades(page);
  await next(page).click();
  await expect(range(page)).toContainText("Records 51–100");
  await expect(page.getByTestId("history-changed")).toHaveCount(0);
  // entry recovery books a trade with its original (page-1) opening time
  await state(request, {
    insert_trade: {
      id: "t-recovered",
      symbol: "RECOVERED/USDT",
      opened_at: historyTime(10.5),
      status: "closed",
    },
  });
  await next(page).click();
  await expect(range(page)).toContainText("page 3");
  await expect(page.getByTestId("history-changed")).toHaveText(WARNING);
  await next(page).click();
  await expect(range(page)).toContainText("page 4");
  await expect(page.getByTestId("history-changed")).toHaveText(WARNING);
  await previous(page).click();
  await previous(page).click();
  await previous(page).click();
  await expect(range(page)).toContainText("page 1");
  // the revisited page 1 is re-read (it now holds the late row, reported
  // unchanged on its own) and the traversal's warning still stands
  await expect(book(page).locator("table")).toContainText("RECOVERED/USDT");
  await expect(page.getByTestId("history-changed")).toHaveText(WARNING);
  await paging(page).getByRole("button", { name: "Newest" }).click();
  await expect(page.getByTestId("history-changed")).toHaveCount(0);
  await expect(book(page).locator("table")).toContainText("RECOVERED/USDT");
  await expect(range(page)).toContainText(`of ${TOTAL + 1} trades · page 1`);
});

test("Astra cancellation: a late insert plus a departure still warns with equal counts", async ({
  page,
  request,
}) => {
  await openTrades(page);
  await book(page).getByLabel("Filter Trades").selectOption("closed");
  const closed = HISTORY + 1;
  await expect(range(page)).toContainText(`Records 1–50 of ${closed} closed trades`);
  await next(page).click();
  await expect(range(page)).toContainText(`Records 51–100 of ${closed} closed trades`);
  await state(request, {
    insert_trade: {
      id: "t-recovered",
      symbol: "RECOVERED/USDT",
      opened_at: historyTime(20.5),
      status: "closed",
    },
  });
  await state(request, { reopen_trade: "h-0005" }); // shown on page 1; leaves "closed"
  await next(page).click();
  // counts alone look untouched …
  await expect(range(page)).toContainText(
    `Records 101–150 of ${closed} closed trades · page 3`,
  );
  // … but the change is reported
  await expect(page.getByTestId("history-changed")).toHaveText(WARNING);
  await expect(page.getByTestId("history-newer")).toHaveCount(0);
});

test("an unchanged traversal never warns", async ({ page }) => {
  await openTrades(page);
  for (let p = 2; p <= 4; p++) {
    await next(page).click();
    await expect(range(page)).toContainText(`page ${p}`);
    await expect(page.getByTestId("history-changed")).toHaveCount(0);
    await expect(page.getByTestId("history-newer")).toHaveCount(0);
  }
});

test("a close during paging updates the row once and keeps positions", async ({
  page,
  request,
}) => {
  await openTrades(page);
  await next(page).click();
  await expect(range(page)).toContainText("Records 51–100");
  // H0100 sits on page 3 (3 scenario rows precede the history)
  await state(request, { close_trade: "h-0100" });
  await next(page).click();
  await expect(range(page)).toContainText(`Records 101–150 of ${TOTAL}`);
  await expect(page.getByTestId("history-changed")).toHaveCount(0);
  const row = book(page).locator("tbody tr", { hasText: "H0100/USDT" });
  await expect(row).toHaveCount(1);
  await expect(row).toContainText("-3.25");
});

test("a late page response never overwrites the page on screen", async ({
  page,
}) => {
  await openTrades(page);
  const held = gate(page, (u) => u.searchParams.has("cursor"));
  await next(page).click();
  await expect.poll(() => held.held.length).toBe(1);
  await previous(page).click();
  await expect(range(page)).toContainText("Records 1–50");
  held.release();
  await page.waitForTimeout(600);
  await expect(range(page)).toContainText("Records 1–50");
  await expect(book(page).locator("table")).toContainText("ETH/USDT");
});

test("changing the filter while a request is pending shows only the latest filter", async ({
  page,
}) => {
  await openTrades(page);
  await next(page).click();
  await expect(range(page)).toContainText("page 2");
  const held = gate(page, (u) => u.searchParams.get("status") === "open");
  await book(page).getByLabel("Filter Trades").selectOption("open");
  await expect.poll(() => held.held.length).toBe(1);
  await book(page).getByLabel("Filter Trades").selectOption("closed");
  await expect(range(page)).toContainText(
    `Records 1–50 of ${HISTORY + 1} closed trades · page 1`,
  );
  held.release();
  await page.waitForTimeout(600);
  await expect(range(page)).toContainText("closed trades · page 1");
  await expect(book(page).locator("table")).not.toContainText("BTC/USDT");
});

test("search is labelled as covering the loaded page and survives paging", async ({
  page,
}) => {
  await openTrades(page);
  const search = book(page).getByLabel("Search Trades");
  await expect(search).toHaveAttribute(
    "placeholder",
    "Search this loaded page…",
  );
  await expect(book(page)).toContainText("search covers only the loaded page");
  await search.fill("H0060");
  await expect(
    book(page).getByText("No records match these filters."),
  ).toBeVisible();
  await next(page).click();
  await expect(search).toHaveValue("H0060");
  await expect(book(page).locator("tbody tr")).toHaveCount(1);
  await expect(book(page).locator("tbody tr")).toContainText("H0060/USDT");
});

test("backend failure and an invalid cursor fail clearly with no substituted rows", async ({
  page,
  request,
}) => {
  await openTrades(page);
  // invalid cursor: the server refuses it; the page offers the newest page
  await page.route("**/owner-api/v1/trades*", (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.has("cursor"))
      url.searchParams.set("cursor", "not-a-cursor");
    return route.continue({ url: url.toString() });
  });
  await next(page).click();
  await expect(book(page).getByRole("alert")).toContainText("invalid_cursor");
  await expect(book(page).locator("table")).toHaveCount(0);
  await book(page)
    .getByRole("button", { name: "Return to newest trades" })
    .click();
  await expect(range(page)).toContainText("Records 1–50");
  await page.unroute("**/owner-api/v1/trades*");
  // backend failure: explicit error, retry recovers
  await state(request, { fail: ["/owner-api/v1/trades"] });
  await book(page).getByLabel("Filter Trades").selectOption("open");
  await expect(book(page).getByRole("alert")).toContainText(
    "Backend error 500",
  );
  await expect(book(page).locator("table")).toHaveCount(0);
  await state(request, { fail: [] });
  await book(page).getByRole("button", { name: "Retry request" }).click();
  await expect(range(page)).toContainText("Records 1–2 of 2 open trades");
});

test("session expiry while paging signs out and shows no trade data", async ({
  page,
  context,
}) => {
  await openTrades(page);
  await context.clearCookies();
  await next(page).click();
  await expect(page.getByTestId("signed-out")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("H0050/USDT");
});

test("empty history is reported as empty, not as an error", async ({
  page,
  request,
}) => {
  await state(request, { scenario: "missing" });
  await login(page);
  await page.getByRole("link", { name: "Trades", exact: true }).click();
  await expect(range(page)).toContainText("No trades on page 1 (0 in total)");
  await expect(book(page)).toContainText("No records returned by this source.");
});
