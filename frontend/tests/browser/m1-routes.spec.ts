/** M1: all nine LIVE routes render under concurrent slow journal reads, lazy
 * chunks stay deliverable, and a failed chunk reports a failure instead of an
 * indefinite "Loading view…". Real FastAPI app (tests/owner_frontend_server.py)
 * with every journal decisions read slowed server-side. */
import { test, expect, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { LIVE, H, state, login } from "./liveHelpers";
import { ev } from "./evidencePath";

const SLOW_S = 2;
const ROUTES: [string, (p: Page) => Promise<void>][] = [
  [
    "overview",
    async (p) => {
      await expect(
        p.getByRole("heading", { name: "Overview", level: 1 }),
      ).toBeVisible();
      await expect(p.getByText("Recent decisions / rejections")).toBeVisible({
        timeout: 10000,
      });
    },
  ],
  [
    "trades",
    async (p) => {
      await expect(p.locator("table").first()).toContainText("SOL/USDT");
    },
  ],
  [
    "research",
    async (p) => {
      await expect(
        p.getByRole("heading", { name: "Research ledger" }),
      ).toBeVisible();
      await expect(p.getByTestId("research-unavailable")).toContainText(
        "no persisted research-question store",
      );
      await p.getByRole("button", { name: /^Results/ }).click();
      await expect(p.getByText("donchian_hi(100)")).toBeVisible();
    },
  ],
  [
    "strategies",
    async (p) => {
      await expect(p.locator("table").first()).toContainText("Donchian");
    },
  ],
  [
    "luffy",
    async (p) => {
      await expect(p.getByRole("textbox").first()).toBeVisible();
    },
  ],
  [
    "operations",
    async (p) => {
      await expect(p.getByTestId("operations-activity")).toBeVisible();
      await expect(
        p.getByRole("heading", { name: "Strategy signals" }),
      ).toBeVisible({
        timeout: 10000,
      });
    },
  ],
  [
    "live-system",
    async (p) => {
      await expect(p.getByText("Kernel cycle").first()).toBeVisible();
    },
  ],
  [
    "knowledge",
    async (p) => {
      await expect(p.getByText("Aggressor Flow").first()).toBeVisible();
    },
  ],
  [
    "diagnostics",
    async (p) => {
      await expect(p.getByTestId("diagnostics-detail")).toBeVisible({
        timeout: 10000,
      });
      await expect(p.getByRole("heading", { name: "Storage" })).toBeVisible();
    },
  ],
];

test.beforeEach(async ({ request }) => {
  await state(request, {
    scenario: "normal",
    gateway: "up",
    chat: "ok",
    fail: [],
    ttl: 43200,
    reset_calls: true,
    slow_decisions: 0,
  });
});
test.afterEach(async ({ request }) => {
  await state(request, { slow_decisions: 0 });
});

test("all nine routes render while decisions reads are slow", async ({
  page,
  request,
}) => {
  await state(request, { history: 120, slow_decisions: SLOW_S });
  await login(page);
  const report: Record<string, unknown> = { slow_decisions_s: SLOW_S };
  const FIXTURE =
    /Synthetic|fixture scenario|DEMO —|Not implemented in this preview/i;
  for (const [route, ready] of ROUTES) {
    const t = Date.now();
    await page.goto(LIVE + `/#${route}`);
    await ready(page);
    await expect(page.getByTestId("route-loading")).toHaveCount(0);
    await expect(page.getByTestId("route-failed")).toHaveCount(0);
    await expect(page.locator("main")).not.toContainText(FIXTURE);
    report[route] = { ready_ms: Date.now() - t };
  }
  writeFileSync(ev("evidence/m1-routes.json"), JSON.stringify(report, null, 2));
});

test("chunks and other reads stay responsive during slow decisions and paging", async ({
  page,
  request,
}) => {
  await state(request, { history: 120, slow_decisions: SLOW_S });
  await login(page);
  // Overview starts the slow decisions GraphQL read (Recent activity)
  await page.goto(LIVE + "/#overview");
  await expect(page.getByText("Loading from the Luffy backend…").first())
    .toBeVisible({ timeout: 5000 })
    .catch(() => undefined);
  const html = await (await request.get(LIVE + "/", { headers: H })).text();
  const assets = [...html.matchAll(/\/assets\/[^"']+\.(?:js|css)/g)].map(
    (m) => m[0],
  );
  expect(assets.length).toBeGreaterThan(0);
  const timings: Record<string, number> = {};
  const started = Date.now();
  const inflight = [
    // concurrent slow reads: decisions GraphQL x3, operations, research, trades pages
    ...[0, 100, 200].map((offset) =>
      request.post(LIVE + "/graphql", {
        headers: H,
        data: {
          query: "query($o:Int!){decisions(limit:101,offset:$o){id}}",
          variables: { o: offset },
        },
      }),
    ),
    request.get(LIVE + "/owner-api/v1/operations/activity", { headers: H }),
  ];
  const fast = async (name: string, url: string) => {
    const t = Date.now();
    const r = await request.get(url, { headers: H });
    timings[name] = Date.now() - t;
    return r;
  };
  for (const a of assets) expect((await fast(a, LIVE + a)).ok()).toBeTruthy();
  expect(
    (await fast("research", LIVE + "/owner-api/v1/research")).ok(),
  ).toBeTruthy();
  expect(
    (await fast("trades page", LIVE + "/owner-api/v1/trades?limit=50")).ok(),
  ).toBeTruthy();
  expect(
    (await fast("bootstrap", LIVE + "/owner-api/v1/bootstrap")).ok(),
  ).toBeTruthy();
  const fastDone = Date.now() - started;
  const slow = await Promise.all(inflight);
  const slowDone = Date.now() - started;
  for (const r of slow) expect(r.ok()).toBeTruthy();
  // every fast request finished while the slow reads were still running
  expect(fastDone).toBeLessThan(SLOW_S * 1000);
  expect(slowDone).toBeGreaterThanOrEqual(SLOW_S * 1000);
  for (const [k, ms] of Object.entries(timings))
    expect(ms, k).toBeLessThan(1000);
  // lazy route chunks load and render while those reads are outstanding
  const again = request.post(LIVE + "/graphql", {
    headers: H,
    data: { query: "{decisions(limit:101){id}}" },
  });
  for (const route of ["trades", "research", "knowledge", "luffy"]) {
    await page.goto(LIVE + `/#${route}`);
    await expect(page.getByTestId("route-loading")).toHaveCount(0, {
      timeout: 3000,
    });
  }
  // Trade History pages while decisions reads continue
  await page.goto(LIVE + "/#trades");
  await page
    .getByRole("group", { name: "Trade history paging" })
    .getByRole("button", { name: "Next page" })
    .click();
  await expect(page.getByTestId("trade-range")).toContainText("page 2", {
    timeout: 3000,
  });
  expect((await again).ok()).toBeTruthy();
  writeFileSync(
    ev("evidence/m1-concurrency.json"),
    JSON.stringify(
      { slow_decisions_s: SLOW_S, fast_ms: timings, fastDone, slowDone },
      null,
      2,
    ),
  );
});

const ROUTES_CHUNK = "**/assets/Routes-*.js";
/** Count real network requests for the Routes chunk that reached the server. */
function chunkRequests(page: Page) {
  const seen = { ok: 0 };
  page.on("response", (r) => {
    if (/\/assets\/Routes-[^/]+\.js$/.test(r.url()) && r.ok()) seen.ok += 1;
  });
  return seen;
}

test("failed chunk: error state, then Retry makes a real new request and renders", async ({
  page,
}) => {
  await login(page);
  const seen = chunkRequests(page);
  await page.route(ROUTES_CHUNK, (route) => route.abort());
  await page.goto(LIVE + "/?keep=1#research");
  await expect(page.getByTestId("route-failed")).toContainText(
    "This view could not be loaded.",
  );
  await expect(page.getByTestId("route-loading")).toHaveCount(0);
  expect(seen.ok).toBe(0);
  // restore the network, then Retry
  await page.unroute(ROUTES_CHUNK);
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(
    page.getByRole("heading", { name: "Research ledger" }),
  ).toBeVisible();
  expect(seen.ok).toBeGreaterThanOrEqual(1);
  await expect(page.getByTestId("route-failed")).toHaveCount(0);
  // the route and query state survive the recovery
  expect(new URL(page.url()).hash).toBe("#research");
  expect(new URL(page.url()).search).toBe("?keep=1");
});

test("failed chunk: navigating away and back recovers without a manual reload", async ({
  page,
}) => {
  await login(page);
  const seen = chunkRequests(page);
  await page.route(ROUTES_CHUNK, (route) => route.abort());
  await page.goto(LIVE + "/#research");
  await expect(page.getByTestId("route-failed")).toBeVisible();
  await page.unroute(ROUTES_CHUNK);
  // navigation stays available; the next route is not left poisoned
  await page.getByRole("link", { name: "Overview", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Overview", level: 1 }),
  ).toBeVisible();
  await expect(page.getByTestId("route-failed")).toHaveCount(0);
  await page.getByRole("link", { name: "Research", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Research ledger" }),
  ).toBeVisible();
  expect(seen.ok).toBeGreaterThanOrEqual(1);
  await page.getByRole("link", { name: "Trades", exact: true }).click();
  await expect(page.locator("table").first()).toContainText("SOL/USDT");
  await expect(page.getByTestId("route-failed")).toHaveCount(0);
});

test("trade and strategy drawers show recorded lineage and explicit gaps", async ({
  page,
}) => {
  await login(page);
  await page.goto(LIVE + "/#trades");
  await page
    .locator("section.panel", { hasText: "Trade book" })
    .getByRole("button", { name: "Inspect BTC/USDT" })
    .click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("heading", { name: "Lineage and accounting" }),
  ).toBeVisible();
  await expect(dialog.getByText("verified", { exact: true })).toBeVisible();
  await expect(dialog.getByTestId("unavailable-fields")).toContainText(
    "Venue fills",
  );
  await page.keyboard.press("Escape");
  await page.goto(LIVE + "/#strategies");
  await page.getByRole("button", { name: "Inspect Donchian" }).click();
  await expect(
    page.getByRole("dialog").getByTestId("unavailable-fields"),
  ).toContainText("allocation");
});
