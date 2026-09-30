import { test, expect, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { cpus, totalmem, platform, release } from "node:os";
import { ev } from "./evidencePath";
async function settle(page: Page) {
  await page.evaluate(
    () =>
      new Promise<void>((r) =>
        requestAnimationFrame(() => requestAnimationFrame(() => r())),
      ),
  );
}
test("record fixture rendering performance and browser resources", async ({
  browser,
}) => {
  test.setTimeout(90000);
  const cold: unknown[] = [];
  const navigation: unknown[] = [];
  const resources: unknown[] = [];
  for (let i = 0; i < 3; i++) {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1080 },
    });
    const page = await context.newPage();
    const session = await context.newCDPSession(page);
    await session.send("Network.enable");
    await session.send("Network.setCacheDisabled", { cacheDisabled: true });
    await page.addInitScript(() => {
      (window as unknown as { longTasks: number[] }).longTasks = [];
      new PerformanceObserver((list) => {
        for (const e of list.getEntries())
          (window as unknown as { longTasks: number[] }).longTasks.push(
            e.duration,
          );
      }).observe({ type: "longtask", buffered: true });
    });
    const start = performance.now();
    await page.goto("http://127.0.0.1:4174");
    await expect(page.getByRole("navigation")).toBeVisible();
    const navigationMs = performance.now() - start;
    await expect(page.getByText("$102,480.50").first()).toBeVisible();
    const usefulMs = performance.now() - start;
    await expect(page.locator("canvas").first()).toBeVisible();
    await settle(page);
    const chartMs = performance.now() - start;
    cold.push({
      navigationMs,
      usefulOverviewMs: usefulMs,
      chartRenderedMs: chartMs,
      ...(await page.evaluate(() => ({
        navigation: performance.getEntriesByType("navigation")[0].toJSON(),
        longTasks: (window as unknown as { longTasks: number[] }).longTasks,
        transferBytes: performance
          .getEntriesByType("resource")
          .reduce(
            (s, e) => s + (e as PerformanceResourceTiming).transferSize,
            0,
          ),
      }))),
    });
    await context.close();
  }
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1080 },
  });
  const page = await context.newPage();
  const session = await context.newCDPSession(page);
  await session.send("Performance.enable");
  await page.goto("http://127.0.0.1:4174");
  await expect(page.locator("canvas").first()).toBeVisible();
  async function sample(name: string) {
    await session.send("HeapProfiler.collectGarbage");
    const start = (await session.send("Performance.getMetrics")).metrics;
    const wall = performance.now();
    await page.waitForTimeout(1200);
    const end = (await session.send("Performance.getMetrics")).metrics;
    const get = (m: typeof start, n: string) =>
      m.find((v) => v.name === n)?.value ?? 0;
    resources.push({
      surface: name,
      sampleMs: performance.now() - wall,
      rendererTaskCpuMs:
        (get(end, "TaskDuration") - get(start, "TaskDuration")) * 1000,
      jsHeapUsedMiB: get(end, "JSHeapUsedSize") / 1048576,
      domNodes: get(end, "Nodes"),
      domCounters: await session.send("Memory.getDOMCounters"),
      layouts: get(end, "LayoutCount") - get(start, "LayoutCount"),
      scriptMs:
        (get(end, "ScriptDuration") - get(start, "ScriptDuration")) * 1000,
    });
  }
  await sample("Overview / chart rendered");
  for (const round of ["first visit", "cached visit"])
    for (const name of ["LUFFY", "Knowledge", "Live System", "Overview"]) {
      const start = performance.now();
      await page
        .getByRole("navigation")
        .getByRole("link", { name, exact: true })
        .click();
      if (name === "LUFFY")
        await expect(page.getByLabel("Message LUFFY")).toBeVisible();
      else if (name === "Overview")
        await expect(page.getByText("$102,480.50").first()).toBeVisible();
      else
        await expect(page.locator(".react-flow__node").first()).toBeVisible();
      await settle(page);
      navigation.push({
        round,
        surface: name,
        elapsedMs: performance.now() - start,
      });
      if (round === "first visit") {
        if (name === "LUFFY")
          await expect(
            page.getByRole("img", { name: /mechanical core/ }),
          ).toBeVisible();
        await sample(name);
      }
    }
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Knowledge", exact: true })
    .click();
  const searchStart = performance.now();
  await page.getByLabel("Search nodes").fill("evidence");
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await settle(page);
  const graphSearchMs = performance.now() - searchStart;
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "LUFFY", exact: true })
    .click();
  const typingStart = performance.now();
  await page.getByLabel("Message LUFFY").fill("Explain the fixture research");
  await settle(page);
  const composerInputMs = performance.now() - typingStart;
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".mechanical-avatar")).toHaveAttribute(
    "data-animating",
    "true",
  );
  await sample("LUFFY / actual fixture reply with Motion");
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Live System", exact: true })
    .click();
  await page.getByRole("button", { name: "Replay fixture event" }).click();
  await sample("Live System / supplied event animation");
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "LUFFY", exact: true })
    .click();
  await sample("Before 206 route changes / LUFFY");
  const churnNames = ["Overview", "LUFFY", "Knowledge", "Live System"];
  for (let i = 0; i < 206; i++) {
    await page
      .getByRole("navigation")
      .getByRole("link", { name: churnNames[i % 4], exact: true })
      .click();
    await settle(page);
  }
  await sample("After 206 route changes");
  await page.goto("about:blank");
  await sample("Preview closed / blank page");
  const report = {
    scope:
      "Synthetic local frontend only; no production/backend/core/database inference",
    conditions: {
      browser: browser.version(),
      platform: platform(),
      release: release(),
      cpu: cpus()[0]?.model,
      logicalCpus: cpus().length,
      hostRamGiB: totalmem() / 1073741824,
      viewport: "1440x1080",
      headless: true,
      network: "loopback; no throttling",
      coldTrials: 3,
      resources:
        "CDP renderer task time, JS heap after forced GC; not whole-browser RSS/GPU or core isolation proof",
      fixtures: {
        equityPoints: 30,
        positions: 2,
        knowledgeNodes: 12,
        knowledgeDefaultVisible: 11,
        knowledgeEdges: 11,
        systemNodes: 9,
        systemEdges: 9,
        events: 1,
        graphCap: 40,
        chatLimit: 100,
      },
      adapterDelayMs: 100,
    },
    cold,
    navigation,
    interaction: { graphSearchMs, composerInputMs },
    resources,
  };
  writeFileSync(ev("evidence/performance.json"), JSON.stringify(report, null, 2));
  await context.close();
});
