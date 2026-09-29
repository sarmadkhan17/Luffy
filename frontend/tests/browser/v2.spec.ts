import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { login, state } from "./liveHelpers";
const evidence = process.env.OWNER_EVIDENCE_DIR ?? "evidence/v2";
const routes = [
  "Overview",
  "Trades",
  "Research",
  "Strategies",
  "LUFFY",
  "Operations",
  "Live System",
  "Knowledge",
  "Diagnostics",
];
const slug = (r: string) => r.toLowerCase().replaceAll(" ", "-");
test.beforeEach(async ({ request }) => {
  await state(request, {
    scenario: "normal",
    gateway: "up",
    chat: "ok",
    fail: [],
    ttl: 43200,
    reset_calls: true,
  });
});
test("V2 all nine desktop and mobile surfaces, accessibility and screenshots", async ({
  page,
}) => {
  test.setTimeout(180000);
  mkdirSync(evidence, { recursive: true });
  const report: Record<string, unknown> = {};
  await login(page);
  for (const mobile of [false, true]) {
    await page.setViewportSize(
      mobile ? { width: 390, height: 844 } : { width: 1440, height: 1080 },
    );
    for (const r of routes) {
      await page
        .getByRole("navigation")
        .getByRole("link", { name: r, exact: true })
        .click();
      await expect(
        page.getByRole("heading", { name: r, exact: true, level: 1 }),
      ).toBeVisible();
      await page.waitForLoadState("networkidle");
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `${r} overflow`,
      ).toBe(true);
      const a = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      report[`${mobile ? "mobile" : "desktop"}-${slug(r)}`] = a.violations.map(
        (v) => ({ id: v.id, nodes: v.nodes.length }),
      );
      expect(a.violations, `${r} accessibility`).toEqual([]);
      await page.screenshot({
        path: `${evidence}/${mobile ? "mobile-" : ""}${slug(r)}.png`,
        fullPage: true,
      });
    }
  }
  writeFileSync(
    `${evidence}/accessibility.json`,
    JSON.stringify(report, null, 2),
  );
});
test("V2 trade search, filters, drawer, mobile details and empty results", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("link", { name: "Trades", exact: true }).click();
  await page.getByLabel("Search Trades").fill("SOL");
  await expect(page.locator("table").first().locator("tbody tr")).toHaveCount(
    1,
  );
  await page.getByRole("button", { name: "Inspect SOL/USDT" }).click();
  await expect(page.getByRole("dialog")).toContainText("Journal entry");
  await expect(page.getByRole("dialog")).toContainText("Venue fills");
  await page.getByLabel("Close record").click();
  await page.getByLabel("Search Trades").fill("impossible-symbol");
  await expect(page.getByText("No records match these filters.")).toBeVisible();
  await page.getByLabel("Search Trades").fill("");
  await page.getByLabel("Filter Trades").selectOption("closed");
  await expect(page.locator("table").first().locator("tbody tr")).toHaveCount(
    1,
  );
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("table").first().locator("tbody tr")).toContainText(
    "closed",
  );
});
test("V2 unavailable and error sources never become successful activity", async ({
  page,
  request,
}) => {
  await state(request, { fail: ["/graphql", "/api/investigations/latest"] });
  await login(page);
  await page.getByRole("link", { name: "Operations", exact: true }).click();
  await expect(page.getByText("Data unavailable")).toHaveCount(2);
  await expect(page.locator("body")).not.toContainText(
    "No records returned by this source.",
  );
  await state(request, { scenario: "stale", fail: [] });
  await page.reload();
  await expect(page.locator(".runtime-cards").first()).toContainText("STALE");
  await page.getByRole("link", { name: "Research", exact: true }).click();
  await expect(page.getByText("INCONCLUSIVE", { exact: true })).toBeVisible();
  await expect(
    page.getByText("NOT_ESTABLISHED", { exact: true }),
  ).toBeVisible();
});
test("V2 reduced motion and mobile core remain responsive", async ({
  page,
  request,
}) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.setViewportSize({ width: 390, height: 844 });
  await state(request, { chat: "slow" });
  await login(page);
  await page.getByRole("link", { name: "LUFFY", exact: true }).click();
  await expect(page.locator(".mobile-core")).toBeVisible();
  await page.getByLabel("Message LUFFY").fill("Check evidence");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".mechanical-avatar")).toHaveAttribute(
    "data-animating",
    "false",
  );
  await page.getByLabel("Message LUFFY").fill("next message remains editable");
  await expect(page.getByLabel("Message LUFFY")).toHaveValue(
    "next message remains editable",
  );
  await page.getByRole("button", { name: /Cancel/ }).click();
  await expect(page.locator(".mechanical-avatar")).toHaveAttribute(
    "data-state",
    "idle",
  );
});

test("Knowledge traces real directed paths and preserves missing-path uncertainty", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("link", { name: "Knowledge", exact: true }).click();
  await page
    .getByRole("button", { name: "Inspect Aggressor Flow", exact: true })
    .click();
  await page.getByText("Trace a directed path", { exact: true }).click();
  await page
    .getByLabel("Path destination")
    .selectOption({ label: "Liquid Active Market" });
  await expect(page.locator(".path-trace")).toContainText("works_in");
  await expect(page.locator(".path-trace")).toContainText(
    "Aggressor Flow → Liquid Active Market",
  );
  await page.getByLabel("Path destination").selectOption({ label: "Map" });
  await expect(page.locator(".path-trace")).toContainText(
    "No directed path in the returned records",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  const axe = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(axe.violations).toEqual([]);
  await page.getByRole("button", { name: "List view", exact: true }).click();
  await page.evaluate(() => {
    (document.activeElement as HTMLElement)?.blur();
    window.scrollTo(0, 0);
  });
  await page.screenshot({
    path: `${evidence}/mobile-path-trace.png`,
    fullPage: true,
  });
});
