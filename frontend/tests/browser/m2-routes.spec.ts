/** M2: connected evidence workflows on the LIVE build against the real
 * FastAPI app (tests/owner_frontend_server.py, fixture ROOT + seed_m2).
 * Every hop follows an id a backend record returned; nothing is matched. */
import { test, expect, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { LIVE, H, state, login } from "./liveHelpers";
import { ev } from "./evidencePath";

const rec = (p: Page, r: string) => p.locator(`a[data-record="${r}"]`).first();
const FIXTURE =
  /Synthetic|fixture scenario|DEMO —|Not implemented in this preview/i;

test.beforeEach(async ({ request }) => {
  await state(request, {
    scenario: "normal",
    m2: true,
    gateway: "up",
    chat: "ok",
    fail: [],
    ttl: 43200,
    reset_calls: true,
    slow_decisions: 0,
  });
});
test.afterEach(async ({ request }) => {
  await state(request, { slow_decisions: 0, fail: [], chat: "ok" });
});

const ROUTES: [string, (p: Page) => Promise<void>][] = [
  [
    "overview",
    async (p) => {
      await expect(p.getByTestId("overview-evidence")).toBeVisible({
        timeout: 10000,
      });
      await expect(rec(p, "decision:dec_fixture01")).toBeVisible();
      await expect(rec(p, "strategy:spec_fixture_trend")).toBeVisible();
      await expect(rec(p, "research:a1b2c3d4e5f60718")).toBeVisible();
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
      await p.getByRole("button", { name: /^Results/ }).click();
      await expect(rec(p, "research:a1b2c3d4e5f60718")).toBeVisible();
      await expect(rec(p, "strategy:spec_fixture_trend")).toBeVisible();
    },
  ],
  [
    "strategies",
    async (p) => {
      await expect(
        p.getByRole("navigation", { name: "Strategy families" }),
      ).toContainText("ema_trend");
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
      await expect(p.getByTestId("ops-timeline")).toBeVisible({
        timeout: 10000,
      });
      await expect(rec(p, "decision:dec_fixture01")).toBeVisible();
      await expect(rec(p, "trade:pos_fixture01")).toBeVisible();
    },
  ],
  [
    "live-system",
    async (p) => {
      await expect(p.getByTestId("system-observed")).toBeVisible({
        timeout: 10000,
      });
      await expect(p.getByTestId("system-observed")).toContainText(
        "journal trades closed",
      );
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
      await expect(
        p.getByRole("heading", { name: "Backend and store probes" }),
      ).toBeVisible({ timeout: 10000 });
      await expect(
        p.getByRole("heading", { name: "Control event history" }),
      ).toBeVisible();
    },
  ],
];

test("all nine routes render their M2 evidence", async ({ page }) => {
  await login(page);
  const report: Record<string, unknown> = {};
  for (const [route, ready] of ROUTES) {
    const t = Date.now();
    await page.goto(LIVE + `/#${route}`);
    await ready(page);
    await expect(page.getByTestId("route-loading")).toHaveCount(0);
    await expect(page.getByTestId("route-failed")).toHaveCount(0);
    await expect(page.locator("main")).not.toContainText(FIXTURE);
    report[route] = { ready_ms: Date.now() - t };
    await page.screenshot({ path: ev(`evidence/m2/${route}.png`), fullPage: true });
  }
  for (const [name, url, testid] of [
    ["trade-story", "/#trades?trade=pos_fixture01", "trade-story"],
    ["decision", "/#operations?decision=dec_fixture01", "decision-detail"],
    [
      "strategy-workspace",
      "/#strategies?id=spec_fixture_trend",
      "strategy-workspace",
    ],
    ["research-item", "/#research?combo=a1b2c3d4e5f60718", "research-item"],
  ]) {
    await page.goto(LIVE + url);
    await expect(page.getByTestId(testid).locator("dl").first()).toBeVisible({
      timeout: 10000,
    });
    await page
      .getByTestId(testid)
      .screenshot({ path: ev(`evidence/m2/${name}.png`) });
  }
  writeFileSync(ev("evidence/m2-routes.json"), JSON.stringify(report, null, 2));
});

test("connected workflow: overview → decision → trade → strategy → research → knowledge", async ({
  page,
}) => {
  await login(page);
  const hops: string[] = [];
  const hop = async (r: string, arrive: string) => {
    await rec(page, r).click();
    await expect(page).toHaveURL(new RegExp(arrive.replace("?", "\\?")));
    hops.push(`${r} → ${arrive}`);
  };
  await page.goto(LIVE + "/#overview");
  await expect(page.getByTestId("overview-evidence")).toBeVisible({
    timeout: 10000,
  });
  await hop("decision:dec_fixture01", "#operations?decision=dec_fixture01");
  const dec = page.getByTestId("decision-detail");
  await expect(dec).toContainText("scan_fixture01");
  await expect(dec).toContainText("TRENDING_UP");
  // the other cycle's vote (short) is inside the window but not linked by id
  await expect(dec).toContainText("trend");
  await expect(dec).not.toContainText("short");
  await dec.locator('a[data-record="trade:pos_fixture01"]').click();
  await expect(page).toHaveURL(/#trades\?trade=pos_fixture01/);
  hops.push("trade:pos_fixture01 → #trades?trade=pos_fixture01");
  const story = page.getByTestId("trade-story");
  const chain = story.getByTestId("trade-chain");
  await expect(chain.locator("li")).toHaveCount(8);
  await expect(chain.locator('[data-step="accounting"]')).toContainText(
    "UNAVAILABLE",
  );
  await expect(chain.locator('[data-step="outcome"]')).toContainText("12.5");
  await expect(story).toContainText("1.8");
  await expect(story.getByTestId("unavailable-fields")).toContainText(
    "Strategy version at entry",
  );
  await story
    .locator('a[data-record="strategy:spec_fixture_trend"]')
    .first()
    .click();
  await expect(page).toHaveURL(/#strategies\?id=spec_fixture_trend/);
  hops.push("strategy:spec_fixture_trend → #strategies?id=spec_fixture_trend");
  const ws = page.getByTestId("strategy-workspace");
  await expect(ws.getByTestId("strategy-postmortem")).toContainText(
    "CONSISTENT",
  );
  await expect(ws).toContainText("Fixture pullback idea");
  await ws.locator('a[data-record="research:a1b2c3d4e5f60718"]').click();
  await expect(page).toHaveURL(/#research\?combo=a1b2c3d4e5f60718/);
  hops.push("research:a1b2c3d4e5f60718 → #research?combo=a1b2c3d4e5f60718");
  const item = page.getByTestId("research-item");
  await expect(item).toContainText("queued");
  await item.locator('a[data-record="research:b1b2c3d4e5f60718"]').click();
  await expect(page.getByTestId("research-item")).toContainText(
    "Research result b1b2c3d4e5f60718",
  );
  await expect(
    page.getByTestId("research-item").getByTestId("unavailable-fields"),
  ).toContainText("not carried into the candidate bank");
  // Knowledge → referenced record
  await page.goto(
    LIVE +
      "/#knowledge?note=" +
      encodeURIComponent("30 Postmortems/Fixture Autopsy.md"),
  );
  const records = page.getByTestId("note-records").first();
  await expect(records).toBeVisible({ timeout: 10000 });
  await expect(records.locator("a[data-record]")).toHaveCount(4);
  // an id-shaped token with no record is listed as unresolved, never linked
  await expect(records.getByTestId("note-unresolved")).toContainText(
    "spec_nonexistent",
  );
  await expect(
    records.locator('a[data-record*="spec_nonexistent"]'),
  ).toHaveCount(0);
  await records.locator('a[data-record="trade:pos_fixture01"]').click();
  await expect(page.getByTestId("trade-story")).toBeVisible();
  hops.push("note → trade:pos_fixture01");
  // Strategy family filter and family → parent/child by recorded ids
  await page.goto(LIVE + "/#strategies?id=strat_child_fx");
  await expect(page.getByTestId("strategy-family")).toContainText(
    "EMA Fixture Parent",
  );
  await page
    .getByTestId("strategy-family")
    .locator('a[data-record="family:ema_trend"]')
    .click();
  await expect(page.locator("table").first()).toContainText(
    "EMA Fixture Child",
  );
  await expect(page.locator("table").first()).not.toContainText(
    "Fixture Trend Pullback",
  );
  writeFileSync(ev("evidence/m2-workflow.json"), JSON.stringify({ hops }, null, 2));
});

test("drawer links close the drawer and open the record", async ({ page }) => {
  await login(page);
  await page.goto(LIVE + "/#trades");
  await page
    .locator("tr", { hasText: "spec:spec_fixture_trend" })
    .getByRole("button", { name: /Inspect/ })
    .click();
  const drawer = page.getByRole("dialog");
  await expect(drawer).toBeVisible();
  const open = drawer.getByText("Open the full trade story ↗");
  await expect(open).toBeVisible({ timeout: 10000 });
  await open.click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByTestId("trade-story")).toBeVisible();
});

test("LUFFY links only exact record mentions and stays non-operational", async ({
  page,
  request,
}) => {
  await state(request, { chat: "mentions" });
  await login(page);
  await page.goto(LIVE + "/#luffy");
  await page.getByRole("textbox").first().fill("What opened recently?");
  await page.getByRole("button", { name: /Send/ }).click();
  const ev = page.getByTestId("reply-evidence").last();
  await expect(ev).toBeVisible({ timeout: 10000 });
  // the unique registry name in the reply is NOT linked; only the exact id
  await expect(ev.locator("a[data-record]")).toHaveCount(1);
  await expect(ev.getByTestId("unresolved-ids")).toContainText(
    "spec_nonexistent",
  );
  await expect(ev).not.toContainText("unique registry name");
  await expect(ev).toContainText("get_trades");
  await expect(ev).not.toContainText("tv_fixture_1 ·");
  const calls = await (
    await request.get(LIVE + "/__test__/calls", { headers: H })
  ).json();
  expect(calls.gateway).toEqual([]); // chat issued no owner request
  await ev.locator('a[data-record="trade:pos_fixture01"]').click();
  await expect(page.getByTestId("trade-story")).toBeVisible();
});

test("a failed record read is shown as a failure; navigation remains", async ({
  page,
  request,
}) => {
  await state(request, {
    fail: ["/owner-api/v1/decisions", "/owner-api/v1/overview/activity"],
  });
  await login(page);
  await page.goto(LIVE + "/#overview");
  const summary = page.getByTestId("overview-evidence");
  await expect(summary.getByRole("alert")).toBeVisible({ timeout: 10000 });
  await expect(page.getByTestId("control-state")).toBeVisible();
  await page.goto(LIVE + "/#operations?decision=dec_fixture01");
  const dec = page.getByTestId("decision-detail");
  await expect(dec.getByRole("alert")).toBeVisible({ timeout: 10000 });
  await expect(dec.locator("a[data-record]")).toHaveCount(0);
  await expect(page.getByTestId("ops-timeline")).toBeVisible();
  await page.getByRole("link", { name: "Strategies" }).click();
  await expect(page.getByTestId("route-failed")).toHaveCount(0);
});

test("unknown ids are reported as not found, not substituted", async ({
  page,
}) => {
  await login(page);
  for (const [url, testid] of [
    ["/#trades?trade=pos_missing", "trade-story"],
    ["/#strategies?id=spec_missing", "strategy-workspace"],
    ["/#research?combo=0000000000000000", "research-item"],
    ["/#operations?decision=dec_missing", "decision-detail"],
  ]) {
    await page.goto(LIVE + url);
    const panel = page.getByTestId(testid);
    await expect(panel.getByRole("alert")).toBeVisible({ timeout: 10000 });
    await expect(panel.locator("a[data-record]")).toHaveCount(0);
  }
});

test("a session that ends while on a deep link signs out cleanly", async ({
  page,
  context,
}) => {
  await login(page);
  await page.goto(LIVE + "/#strategies");
  await expect(page.locator("table").first()).toContainText("Donchian");
  await context.clearCookies(); // server-side: session gone → 401
  await page.evaluate(
    () => (location.hash = "#strategies?id=spec_fixture_trend"),
  );
  await expect(page.getByTestId("signed-out")).toBeVisible({ timeout: 10000 });
  await expect(page.locator("body")).not.toContainText(
    "Fixture Trend Pullback",
  );
  await expect(page.locator("body")).not.toContainText("Donchian");
});

test("record reads stay responsive while decisions reads are slow", async ({
  page,
  request,
}) => {
  // the harness slows EVERY journal read of the decisions table (even a
  // primary-key lookup) to SLOW_S: reads that touch decisions are slow here,
  // and must not queue behind one another or block reads that do not
  const SLOW_S = 2;
  await state(request, { slow_decisions: SLOW_S });
  await login(page);
  const t0 = Date.now();
  const timed = async (path: string) => {
    const t = Date.now();
    const r = await request.get(LIVE + "/owner-api/v1/" + path, { headers: H });
    return { path, ok: r.ok(), ms: Date.now() - t };
  };
  const touching = Promise.all(
    [
      "decisions/dec_fixture01",
      "trades/pos_fixture01/lineage",
      "overview/activity",
      "operations/activity",
      "system/observed",
      "knowledge/note?id=MOC.md",
    ].map(timed),
  );
  const fast: Awaited<ReturnType<typeof timed>>[] = [];
  for (const path of [
    "strategies/spec_fixture_trend",
    "research/combos/a1b2c3d4e5f60718",
    "diagnostics/events?limit=50",
    "trades?limit=50",
    "bootstrap",
  ])
    fast.push(await timed(path));
  await page.goto(LIVE + "/#strategies?id=spec_fixture_trend");
  await expect(page.getByTestId("strategy-postmortem")).toBeVisible({
    timeout: SLOW_S * 1000 - 100,
  });
  const renderMs = Date.now() - t0;
  const slow = await touching;
  const allMs = Date.now() - t0;
  for (const f of [...fast, ...slow]) expect(f.ok, f.path).toBeTruthy();
  for (const f of fast) expect(f.ms, f.path).toBeLessThan(1000);
  // six decisions-touching reads ran side by side, not one after another
  expect(allMs).toBeLessThan(SLOW_S * 1000 * 3);
  writeFileSync(
    ev("evidence/m2-concurrency.json"),
    JSON.stringify(
      {
        slow_decisions_s: SLOW_S,
        fast,
        decisions_touching: slow,
        strategy_workspace_render_ms: renderMs,
        all_done_ms: allMs,
      },
      null,
      2,
    ),
  );
});

test("trade story fits a phone viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.goto(LIVE + "/#trades?trade=pos_fixture01");
  await expect(page.getByTestId("trade-chain")).toBeVisible({ timeout: 10000 });
  const overflow = await page.evaluate(() => ({
    px: document.documentElement.scrollWidth - window.innerWidth,
    offenders: [...document.querySelectorAll("main *")]
      .filter((e) => e.getBoundingClientRect().right > window.innerWidth + 1)
      .slice(0, 8)
      .map(
        (e) =>
          `${e.tagName}.${e.className} ${Math.round(e.getBoundingClientRect().right)}`,
      ),
  }));
  expect(overflow.px, overflow.offenders.join(" | ")).toBeLessThanOrEqual(1);
  await page.screenshot({ path: ev("evidence/m2/mobile-trade-story.png") });
});
