/** LIVE performance/memory against the live-contract test server (not
 * production). Writes evidence/live-binding/performance-live.json.
 * Timings include Playwright observation; heap is JS heap after forced GC;
 * server RSS is the test server's own process (fixture journal, fake kernel). */
import { test, expect, type Page, type CDPSession } from "@playwright/test";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { LIVE, state, calls, login } from "./liveHelpers";

const evidence = process.env.OWNER_EVIDENCE_DIR ?? "evidence/live-binding";
const report: Record<string, unknown> = {};
const rss = (pid: number) => {
  const m = readFileSync(`/proc/${pid}/status`, "utf8").match(
    /VmRSS:\s+(\d+) kB/,
  );
  return m ? Math.round((Number(m[1]) / 1024) * 10) / 10 : null;
};
async function metrics(cdp: CDPSession) {
  await cdp.send("HeapProfiler.collectGarbage");
  const { metrics } = await cdp.send("Performance.getMetrics");
  const get = (n: string) => metrics.find((m) => m.name === n)?.value ?? null;
  return {
    heapMiB: Math.round(((get("JSHeapUsedSize") ?? 0) / 1048576) * 100) / 100,
    nodes: get("Nodes"),
    listeners: get("JSEventListeners"),
  };
}
async function timeTo(
  page: Page,
  action: () => Promise<unknown>,
  until: () => Promise<unknown>,
) {
  const t0 = Date.now();
  await action();
  await until();
  return Date.now() - t0;
}

test.describe.configure({ mode: "serial" });
test.setTimeout(180000);

test("cold load, cached routes and lazy surfaces", async ({
  browser,
  request,
}) => {
  await state(request, {
    scenario: "normal",
    gateway: "up",
    chat: "ok",
    fail: [],
    ttl: 43200,
  });
  const cold: Record<string, number>[] = [];
  for (let i = 0; i < 3; i++) {
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await login(page); // establishes the cookie; then measure a fresh cold load
    await page.goto("about:blank");
    const cdp = await ctx.newCDPSession(page);
    await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
    const t0 = Date.now();
    await page.goto(LIVE + "/owner-preview/#overview");
    await expect(
      page.getByRole("navigation", { name: "Main navigation" }),
    ).toBeVisible();
    const nav = Date.now() - t0;
    await expect(page.getByText("5,164.50 USDT").first()).toBeVisible();
    const useful = Date.now() - t0;
    await expect(page.locator(".chart canvas").first()).toBeVisible();
    const chart = Date.now() - t0;
    cold.push({
      usableNavigationMs: nav,
      usefulOverviewMs: useful,
      chartMs: chart,
    });
    await ctx.close();
  }
  report.cold = cold;

  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await login(page);
  await expect(page.getByText("5,164.50 USDT").first()).toBeVisible();
  const first: Record<string, number> = {};
  first.knowledge = await timeTo(
    page,
    () => page.evaluate(() => (location.hash = "#knowledge")),
    () => expect(page.locator(".react-flow__node").first()).toBeVisible(),
  );
  first.liveSystem = await timeTo(
    page,
    () => page.evaluate(() => (location.hash = "#live-system")),
    () => expect(page.locator(".react-flow__node").first()).toBeVisible(),
  );
  first.operations = await timeTo(
    page,
    () => page.evaluate(() => (location.hash = "#operations")),
    () =>
      expect(page.getByTestId("owner-interface-status")).toContainText(
        "control state",
      ),
  );
  first.luffy = await timeTo(
    page,
    () => page.evaluate(() => (location.hash = "#luffy")),
    () => expect(page.getByLabel("Message LUFFY")).toBeVisible(),
  );
  report.firstVisitMs = first;
  const cached: number[] = [];
  for (let i = 0; i < 5; i++)
    for (const [route, ready] of [
      [
        "overview",
        () => expect(page.getByText("5,164.50 USDT").first()).toBeVisible(),
      ],
      [
        "knowledge",
        () => expect(page.locator(".react-flow__node").first()).toBeVisible(),
      ],
      ["luffy", () => expect(page.getByLabel("Message LUFFY")).toBeVisible()],
    ] as const)
      cached.push(
        await timeTo(
          page,
          () => page.evaluate((r) => (location.hash = "#" + r), route),
          ready,
        ),
      );
  report.cachedRouteMs = {
    min: Math.min(...cached),
    max: Math.max(...cached),
    samples: cached,
  };
  await ctx.close();
});

test("chat and navigation stay responsive while a heavy graph loads", async ({
  page,
}) => {
  await login(page);
  await page.route("**/owner-api/v1/knowledge", async (r) => {
    await new Promise((res) => setTimeout(res, 2500));
    await r.continue();
  });
  await page.evaluate(() => (location.hash = "#knowledge"));
  await expect(page.getByText("Loading from the Luffy backend…")).toBeVisible();
  const navMs = await timeTo(
    page,
    () => page.evaluate(() => (location.hash = "#luffy")),
    () => expect(page.getByLabel("Message LUFFY")).toBeVisible(),
  );
  const box = page.getByLabel("Message LUFFY");
  const typeMs = await timeTo(
    page,
    () => box.pressSequentially("hello", { delay: 0 }),
    () => expect(box).toHaveValue("hello"),
  );
  const replyMs = await timeTo(
    page,
    () => page.getByRole("button", { name: "Send" }).click(),
    () =>
      expect(page.getByText("Fixture backend reply to: hello")).toBeVisible(),
  );
  report.whileGraphLoading = {
    navigateAwayMs: navMs,
    typeFiveCharsMs: typeMs,
    chatReplyMs: replyMs,
  };
});

test("route churn, graph pages, websocket use and server RSS", async ({
  page,
  request,
}) => {
  const sockets: string[] = [];
  page.on("websocket", (w) => sockets.push(w.url()));
  const pid = (await calls(request)).pid as number;
  const serverBefore = rss(pid);
  await login(page);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Performance.enable");
  await expect(page.getByText("5,164.50 USDT").first()).toBeVisible();
  const before = await metrics(cdp);
  const routes = [
    "overview",
    "trades",
    "research",
    "strategies",
    "luffy",
    "operations",
    "live-system",
    "knowledge",
    "diagnostics",
  ];
  let changes = 0;
  for (let round = 0; round < 12; round++)
    for (const r of routes) {
      await page.evaluate((x) => (location.hash = "#" + x), r);
      await page.waitForTimeout(40);
      changes++;
    }
  await page.evaluate(() => (location.hash = "#overview"));
  await expect(page.getByText("5,164.50 USDT").first()).toBeVisible();
  await page.waitForTimeout(500);
  const after = await metrics(cdp);
  await page.evaluate(() => (location.hash = "#knowledge"));
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  const knowledge = await metrics(cdp);
  await page.evaluate(() => (location.hash = "#live-system"));
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  const system = await metrics(cdp);
  // server under repeated owner API reads (the Overview poll path and graphs)
  for (let i = 0; i < 100; i++)
    for (const p of ["overview", "knowledge", "system", "bootstrap"])
      await request.get(`${LIVE}/owner-api/v1/${p}`, {
        headers: { "x-luffy-token": "fixture-token" },
      });
  const serverAfter = rss(pid);
  report.memory = {
    browser: {
      overviewBefore: before,
      overviewAfterChurn: after,
      knowledge,
      liveSystem: system,
      routeChanges: changes,
    },
    serverRssMiB: {
      afterBoot: serverBefore,
      after400ApiReadsAndChurn: serverAfter,
    },
    websockets: sockets,
  };
  expect(sockets).toEqual([]); // the owner frontend opens no websocket
  mkdirSync(evidence, { recursive: true });
  writeFileSync(
    `${evidence}/performance-live.json`,
    JSON.stringify(
      {
        environment: {
          note: "Live-contract test server (tests/owner_frontend_server.py): real FastAPI app, fixture journal, fake Owner Interface/chat. Not production.",
          browser: page.context().browser()?.version(),
          viewport: page.viewportSize(),
        },
        ...report,
      },
      null,
      2,
    ),
  );
});
