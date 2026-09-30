/** M3 final product evidence. LIVE production build served by the real
 * FastAPI app over a temporary, synthetic seeded fixture journal
 * (tests/owner_frontend_server.py). Every screenshot is SYNTHETIC SEEDED
 * EVIDENCE, not production data. Output: evidence/m3 (or EVIDENCE_SINK). */
import { test, expect, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { LIVE, state, calls, login } from "./liveHelpers";
import { ev } from "./evidencePath";
import AxeBuilder from "@axe-core/playwright";

const out = (name: string) => ev(`evidence/m3/${name}`);
const rec = (p: Page, r: string) => p.locator(`a[data-record="${r}"]`).first();
const FIXTURE =
  /Synthetic|fixture scenario|DEMO —|Not implemented in this preview/i;
/** Scroll to the top (a linked record scrolls itself into view; a full-page
 * capture of a scrolled page misplaces sticky/fixed chrome), then let two
 * frames paint. */
const settle = (p: Page) =>
  p.evaluate(
    () =>
      new Promise<void>((r) => {
        window.scrollTo(0, 0);
        requestAnimationFrame(() => requestAnimationFrame(() => r()));
      }),
  );

/** A tall record panel: clip a full-page capture to the element's box. */
async function shootElement(page: Page, testid: string, name: string) {
  const box = (await page.getByTestId(testid).first().boundingBox())!;
  const y = await page.evaluate(() => window.scrollY);
  await page.screenshot({
    path: out(name),
    fullPage: true,
    clip: { x: box.x, y: box.y + y, width: box.width, height: box.height },
  });
}

test.setTimeout(240000);
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

const READY: Record<string, (p: Page) => Promise<void>> = {
  overview: async (p) => {
    await expect(p.getByTestId("overview-evidence")).toBeVisible({
      timeout: 10000,
    });
    await expect(p.locator(".chart canvas").first()).toBeVisible();
    await expect(rec(p, "decision:dec_fixture01")).toBeVisible();
  },
  trades: async (p) => {
    await expect(p.locator("table").first()).toContainText("SOL/USDT");
  },
  research: async (p) => {
    await expect(p.getByTestId("research-ledger")).toBeVisible({
      timeout: 10000,
    });
  },
  strategies: async (p) => {
    await expect(
      p.getByRole("navigation", { name: "Strategy families" }),
    ).toContainText("ema_trend");
  },
  luffy: async (p) => {
    await expect(p.getByRole("img", { name: /mechanical core/ })).toBeVisible();
  },
  operations: async (p) => {
    await expect(p.getByTestId("ops-timeline")).toBeVisible({
      timeout: 10000,
    });
  },
  "live-system": async (p) => {
    await expect(p.getByTestId("system-observed")).toBeVisible({
      timeout: 10000,
    });
    await expect(p.locator(".react-flow__node").first()).toBeVisible();
  },
  knowledge: async (p) => {
    await expect(p.locator(".react-flow__node").first()).toBeVisible({
      timeout: 10000,
    });
  },
  diagnostics: async (p) => {
    await expect(
      p.getByRole("heading", { name: "Backend and store probes" }),
    ).toBeVisible({ timeout: 10000 });
  },
};

test("M3 desktop: all nine routes and connected detail workflows", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  const report: Record<string, unknown> = {};
  for (const [route, ready] of Object.entries(READY)) {
    const t = Date.now();
    await page.goto(LIVE + `/#${route}`);
    await ready(page);
    await expect(page.getByTestId("route-loading")).toHaveCount(0);
    await expect(page.getByTestId("route-failed")).toHaveCount(0);
    await expect(page.locator("main")).not.toContainText(FIXTURE);
    report[route] = { ready_ms: Date.now() - t };
    await settle(page);
    await page.screenshot({ path: out(`desktop-${route}.png`), fullPage: true });
  }
  const details: [string, string, string][] = [
    ["trade-story", "/#trades?trade=pos_fixture01", "trade-story"],
    ["decision", "/#operations?decision=dec_fixture01", "decision-detail"],
    [
      "strategy-workspace",
      "/#strategies?id=spec_fixture_trend",
      "strategy-workspace",
    ],
    ["strategy-child", "/#strategies?id=strat_child_fx", "strategy-workspace"],
    ["research-item", "/#research?combo=a1b2c3d4e5f60718", "research-item"],
  ];
  for (const [name, url, testid] of details) {
    const t = Date.now();
    await page.goto(LIVE + url);
    await expect(page.getByTestId(testid).locator("dl").first()).toBeVisible({
      timeout: 10000,
    });
    report[name] = { ready_ms: Date.now() - t };
    await settle(page);
    await shootElement(page, testid, `desktop-detail-${name}.png`);
  }
  // Knowledge: a selected note focuses its neighbourhood and opens the inspector
  await page.goto(LIVE + "/#knowledge");
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  await page
    .getByRole("button", { name: "Inspect Absorption Test", exact: true })
    .click();
  await expect(
    page.getByRole("complementary", { name: "Node inspector" }),
  ).toContainText("Absorption Test");
  await expect(page.getByTestId("note-records").first()).toBeVisible({
    timeout: 10000,
  });
  await settle(page);
  await page.screenshot({
    path: out("desktop-detail-knowledge-selected.png"),
    fullPage: true,
  });
  // LUFFY: one reply with exact-id mentions and consulted reads; no gateway call
  await state(request, { chat: "mentions", reset_calls: true });
  await page.goto(LIVE + "/#luffy");
  await page.getByLabel("Message LUFFY").fill("What did you open recently?");
  await page.getByLabel("Message LUFFY").press("Enter");
  await expect(page.getByTestId("reply-evidence")).toBeVisible({
    timeout: 10000,
  });
  await expect(rec(page, "trade:pos_fixture01")).toBeVisible();
  expect((await calls(request)).gateway).toEqual([]);
  await settle(page);
  await page.screenshot({
    path: out("desktop-detail-luffy-reply.png"),
    fullPage: true,
  });
  // Operations timeline filtered to decisions
  await page.goto(LIVE + "/#operations");
  await expect(page.getByTestId("ops-timeline")).toBeVisible();
  await page.getByLabel("Filter timeline").selectOption("decision");
  await settle(page);
  await shootElement(
    page,
    "ops-timeline-panel",
    "desktop-detail-operations-decisions.png",
  );
  writeFileSync(out("routes.json"), JSON.stringify(report, null, 2));
});

test("M3 mobile: representative routes are usable at 390 px", async ({
  page,
}) => {
  await login(page);
  await page.setViewportSize({ width: 390, height: 844 });
  const report: Record<string, unknown> = {};
  for (const [name, url, ready] of [
    ["overview", "/#overview", READY.overview],
    [
      "trade-story",
      "/#trades?trade=pos_fixture01",
      async (p: Page) =>
        expect(p.getByTestId("trade-chain")).toBeVisible({ timeout: 10000 }),
    ],
    ["luffy", "/#luffy", READY.luffy],
    [
      "knowledge",
      "/#knowledge",
      async (p: Page) =>
        expect(p.locator(".graph-list button").first()).toBeVisible({
          timeout: 10000,
        }),
    ],
    ["operations", "/#operations", READY.operations],
    [
      "strategy-workspace",
      "/#strategies?id=spec_fixture_trend",
      async (p: Page) =>
        expect(p.getByTestId("strategy-workspace").locator("dl").first())
          .toBeVisible({ timeout: 10000 }),
    ],
  ] as [string, string, (p: Page) => Promise<void>][]) {
    await page.goto(LIVE + url);
    await ready(page);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - innerWidth,
    );
    report[name] = { horizontalOverflowPx: overflow };
    expect(overflow, name).toBeLessThanOrEqual(0);
    await settle(page);
    await page.screenshot({ path: out(`mobile-${name}.png`), fullPage: true });
  }
  writeFileSync(out("mobile.json"), JSON.stringify(report, null, 2));
});

test("M3 accessibility: axe WCAG 2.1 AA on all nine LIVE routes and key details", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  const report: Record<string, unknown> = {};
  const targets: [string, (p: Page) => Promise<void>][] = [
    ...Object.entries(READY).map(
      ([r, ready]) => [`/#${r}`, ready] as [string, (p: Page) => Promise<void>],
    ),
    [
      "/#trades?trade=pos_fixture01",
      async (p) => expect(p.getByTestId("trade-chain")).toBeVisible(),
    ],
    [
      "/#strategies?id=spec_fixture_trend",
      async (p) =>
        expect(p.getByTestId("strategy-postmortem")).toBeVisible({
          timeout: 10000,
        }),
    ],
    [
      "/#research?combo=a1b2c3d4e5f60718",
      async (p) =>
        expect(p.getByTestId("research-lineage")).toBeVisible({
          timeout: 10000,
        }),
    ],
  ];
  for (const [url, ready] of targets) {
    await page.goto(LIVE + url);
    await ready(page);
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    report[url] = result.violations.map((v) => ({
      id: v.id,
      impact: v.impact,
      nodes: v.nodes.map((n) => n.target.join(" ")),
    }));
  }
  writeFileSync(out("accessibility.json"), JSON.stringify(report, null, 2));
  for (const [url, v] of Object.entries(report)) expect(v, url).toEqual([]);
});

/** The conversation must be the usable surface at every width: measured
 * geometry and a real send/reply, not only overflow or axe. */
test("M3 LUFFY conversation is usable at 390, 759, 768, 1024 and 1440 px", async ({
  page,
  request,
}) => {
  await login(page);
  const report: Record<string, unknown> = {};
  for (const width of [390, 759, 768, 1024, 1440]) {
    await state(request, { chat: "mentions", reset_calls: true });
    await page.setViewportSize({ width, height: 900 });
    await page.goto(LIVE + "/#luffy");
    await expect(page.getByLabel("Message LUFFY")).toBeVisible();
    const main = (await page.locator("main").boundingBox())!;
    const convo = (await page.locator(".conversation").boundingBox())!;
    const transcript = (await page.locator(".transcript").boundingBox())!;
    const input = page.getByLabel("Message LUFFY");
    const box = (await input.boundingBox())!;
    const inner = main.width - (width < 760 ? 28 : 56); // main padding
    // the conversation takes the full content width unless a real side
    // column fits beside it (≥ 1100 px), and is never a sliver
    if (width < 1100)
      expect(convo.width, `${width}: stacked conversation width`).toBeGreaterThanOrEqual(inner - 2);
    else
      expect(convo.width, `${width}: side-by-side conversation width`).toBeGreaterThanOrEqual(560);
    expect(transcript.height, `${width}: transcript height`).toBeGreaterThanOrEqual(240);
    expect(box.width, `${width}: composer width`).toBeGreaterThanOrEqual(
      Math.min(300, width - 90),
    );
    expect(box.x + box.width, `${width}: composer inside viewport`).toBeLessThanOrEqual(width);
    // type, send and receive: the reply with its exact-id evidence renders
    // the conversation fits one screen: once scrolled to, the composer and
    // its Send control are on screen together with the transcript
    expect(convo.height, `${width}: conversation fits the viewport`).toBeLessThanOrEqual(900);
    await page.locator(".conversation").scrollIntoViewIfNeeded();
    await expect(page.getByRole("button", { name: /Send/ })).toBeInViewport();
    await input.fill(`Width ${width}: what opened?`);
    await input.press("Enter");
    const reply = page.getByTestId("reply-evidence").last();
    await expect(reply).toBeVisible({ timeout: 10000 });
    const r = (await reply.boundingBox())!;
    expect(r.width, `${width}: reply evidence width`).toBeGreaterThanOrEqual(
      Math.min(260, width - 110),
    );
    await expect(page.locator(".transcript")).toBeInViewport();
    expect((await calls(request)).gateway, `${width}: no owner request`).toEqual([]);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - innerWidth,
    );
    expect(overflow, `${width}: horizontal overflow`).toBeLessThanOrEqual(0);
    report[width] = {
      mainWidth: Math.round(main.width),
      conversationWidth: Math.round(convo.width),
      transcriptHeight: Math.round(transcript.height),
      composerWidth: Math.round(box.width),
      replyEvidenceWidth: Math.round(r.width),
      horizontalOverflowPx: overflow,
    };
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: out(`luffy-width-${width}.png`), fullPage: true });
  }
  writeFileSync(out("luffy-widths.json"), JSON.stringify(report, null, 2));
});
