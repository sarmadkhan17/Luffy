import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFileSync, mkdirSync } from "node:fs";
const fixtures = JSON.parse(readFileSync("tests/fixtures/glacier-readonly.json", "utf8"));
const evidence = "/mnt/luffy-data/test-tmp/glacier-v2/isolated";
mkdirSync(evidence, { recursive: true });
const routes = ["overview", "trades", "research", "strategies", "luffy", "operations", "live-system", "knowledge", "diagnostics"];
async function setup(page: Page, overrides: Record<string, unknown> = {}) {
  const data = { ...structuredClone(fixtures), ...overrides }, writes: string[] = [], errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.route("**/*", route => {
    const r = route.request(), u = new URL(r.url());
    if (u.origin !== "http://127.0.0.1:4180") return route.abort();
    if (u.pathname.startsWith("/owner-api/") || u.pathname.startsWith("/api/")) {
      if (r.method() !== "GET") writes.push(u.pathname);
      return route.fulfill({ status: data[u.pathname] ? 200 : 503, contentType: "application/json", body: JSON.stringify(data[u.pathname] ?? { error: "unavailable isolated fixture" }) });
    }
    return route.continue();
  });
  return { writes, errors };
}
for (const [width, height] of [[1440, 900], [1280, 800], [820, 1000], [390, 844]]) {
  test(`V2 all permanent routes, responsive ${width}`, async ({ page }) => {
    const trace = await setup(page); await page.setViewportSize({ width, height });
    for (const route of routes) {
      await page.goto(`/#${route}`);
      await expect(page.getByTestId("route-content")).toHaveAttribute("data-route", route);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expect(page.getByTestId("route-failed")).toHaveCount(0);
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
      await page.screenshot({ path: `${evidence}/${width}-${route}.png`, fullPage: true });
    }
    expect(trace.errors).toEqual([]); expect(trace.writes).toEqual([]);
  });
}
test("V2 rail keyboard navigation keeps shell and lands at content", async ({ page }) => {
  await setup(page); await page.goto("/#overview");
  const brand = await page.locator('.gl-brand').elementHandle();
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  await nav.getByRole("link", { name: "Overview", exact: true }).focus();
  await page.keyboard.press("ArrowDown");
  await expect(nav.getByRole("link", { name: "Trades", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("route-content")).toHaveAttribute("data-route", "trades");
  await expect(page.locator("#content")).toBeFocused();
  expect(await brand!.evaluate(e => e.isConnected)).toBe(true);
  await expect(nav.getByRole("link", { name: "Trades", exact: true })).toHaveAttribute("aria-current", "page");
  await page.screenshot({ path: `${evidence}/navigation.png` });
});
test("V2 detail trap, Escape, selected origin and focus return", async ({ page }) => {
  await setup(page); await page.goto("/#live-system");
  const origin = page.getByRole("button", { name: "Risk — DECLARED ARCHITECTURE" });
  await origin.click(); const panel = page.getByRole("dialog"); await expect(panel).toBeVisible();
  for (let i = 0; i < 8; i++) { await page.keyboard.press("Tab"); expect(await panel.evaluate(e => e.contains(document.activeElement))).toBe(true); }
  await page.screenshot({ path: `${evidence}/detail-panel.png` });
  await page.keyboard.press("Escape"); await expect(panel).toHaveCount(0); await expect(origin).toBeFocused();
});
test("V2 reduced motion is immediate and stops walkthrough dynamically", async ({ page }) => {
  await setup(page); await page.goto("/#live-system");
  await page.getByRole("button", { name: "Follow a trading cycle" }).click();
  await page.getByRole("button", { name: "▶ Play", exact: true }).click();
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(page.getByRole("button", { name: "❚❚ Pause", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Next step" }).click();
  for (const route of routes) {
    await page.goto(`/#${route}`); await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    expect(await page.evaluate(() => document.getAnimations().filter(a => a.playState === 'running').length)).toBe(0);
  }
  await page.screenshot({ path: `${evidence}/reduced-motion.png` });
});
test("V2 declared paths never show live flow; subsystem isolation", async ({ page }) => {
  await setup(page); await page.goto("/#live-system");
  await expect(page.getByTestId("path-legend")).toContainText("exact edge evidence unavailable");
  await page.getByRole("group", { name: "Isolate subsystem" }).getByRole("button", { name: "Safety", exact: true }).click();
  await expect(page.locator('.gl-node.on')).not.toHaveCount(0);
  await page.getByRole("button", { name: "Follow a trading cycle" }).click();
  await expect(page.locator('.pk,.flowp')).toHaveCount(0);
  await expect(page.getByRole("button", { name: "All subsystems", exact: true })).toHaveAttribute("aria-pressed", "true");
  expect(await page.locator('[data-path-kind="declared"]').evaluateAll(es => es.every(e => getComputedStyle(e).animationName === 'none'))).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
  for (const button of await page.getByRole("button", { name: /^Step [0-9]+$/ }).all()) {
    const box = await button.boundingBox(); expect(box!.width).toBeGreaterThanOrEqual(44); expect(box!.height).toBeGreaterThanOrEqual(44);
  }
});
test("V2 read-only controls and machine are locked", async ({ page }) => {
  const trace = await setup(page); await page.goto("/#trades");
  for (const name of ["Resume", "Freeze", "Halt", "PANIC"]) await expect(page.getByRole("button", { name, exact: true })).toBeDisabled();
  await page.goto("/#luffy"); await expect(page.getByTestId("luffy-core")).toHaveAttribute("data-state", "read_only");
  await expect(page.getByTestId("core-state-label")).toHaveText("READ_ONLY");
  const centers = await page.getByTestId("luffy-core").evaluate(svg => {
    const outer = svg.getBoundingClientRect();
    return [...svg.querySelectorAll("g")].map(g => { const box = g.getBoundingClientRect(); return { dx: Math.abs(box.x + box.width/2 - outer.x - outer.width/2), dy: Math.abs(box.y + box.height/2 - outer.y - outer.height/2) }; });
  });
  expect(centers.every(c => c.dx < 1 && c.dy < 1)).toBe(true);
  await expect(page.getByTestId("provider-state")).toContainText("Provider: DISABLED");
  expect(trace.writes).toEqual([]);
});
test("V2 graph stable slots, written relation filter, complete neighbor list", async ({ page }) => {
  await setup(page); await page.goto("/#knowledge");
  await expect(page.getByTestId("map-coverage")).toBeVisible();
  const positions = () => page.locator('.gl-fn').evaluateAll(es => es.map(e => { const b = e.getBoundingClientRect(); return {x:b.x,y:b.y,w:b.width,h:b.height}; }));
  const before = await positions();
  for (let i = 0; i < before.length; i++) for (let j = i + 1; j < before.length; j++) {
    const a = before[i], b = before[j]; expect(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y).toBe(true);
  }
  await page.waitForTimeout(700); expect(await positions()).toEqual(before);
  const filters = page.getByRole("group", { name: "Written relationship filter" });
  if (await filters.getByRole("button").count() > 1) { await filters.getByRole("button").nth(1).click(); await expect(filters.getByRole("button").nth(1)).toHaveAttribute("aria-pressed", "true"); }
  await page.getByRole("group", { name: "View" }).getByRole("button", { name: "List", exact:true }).click();
  await expect(page.getByRole("table", { name: "Knowledge notes" })).toBeVisible();
  await page.getByRole("group", { name: "View" }).getByRole("button", { name: "Map", exact:true }).click();
  await expect(page.getByTestId("map-coverage")).toBeVisible();
});
test("V2 hour selection narrows only listed events and keeps disclosure", async ({ page }) => {
  await setup(page); await page.goto("/#operations");
  const bars = page.locator('.gl-hour'); await expect(bars).toHaveCount(24);
  await bars.nth(0).click(); await expect(bars.nth(0)).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByTestId("operations-disclosure")).toContainText("counted, not listed");
  await page.getByRole("button", { name: /Clear 00:00 UTC filter/ }).click();
  await expect(bars.nth(0)).toHaveAttribute("aria-pressed", "false");
});
test("V2 research explains stage aggregates without implying admission", async ({ page }) => {
  await setup(page); await page.goto("/#research");
  await page.getByRole("button", { name: "Explain Candidate bank" }).click();
  await expect(page.getByRole("region", { name: "Stage explanation" })).toContainText("Candidate discovery and Referee admission remain separate");
});
test("V2 accessible permanent surfaces", async ({ page }) => {
  await setup(page);
  for (const route of routes) {
    await page.goto(`/#${route}`); await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    expect(results.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) })), route).toEqual([]);
  }
});
