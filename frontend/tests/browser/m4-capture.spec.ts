/** M4 review capture. The LIVE server below is a temporary seeded test root.
 * Every value in these images is synthetic; no production request is made. */
import { test, expect, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";
import { ev } from "./evidencePath";
import { LIVE, login, state } from "./liveHelpers";

const out = (name: string) => ev(`evidence/m4/${name}`);
const routes = ["overview", "trades", "research", "strategies", "luffy", "operations", "live-system", "knowledge", "diagnostics"];
const settle = (page: Page) => page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))));

test.beforeEach(async ({ request }) => {
  await state(request, { scenario: "normal", m2: true, gateway: "up", chat: "ok", fail: [], ttl: 43200, reset_calls: true, slow_decisions: 0 });
});

test("nine routes at identical desktop viewport", async ({ page }) => {
  test.setTimeout(120000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  const result: Record<string, unknown> = {};
  for (const route of routes) {
    const start = performance.now();
    await page.goto(`${LIVE}/#${route}`);
    await expect(page.locator("main h1")).toHaveText(route === "live-system" ? "Live System" : route === "luffy" ? "LUFFY" : route[0].toUpperCase() + route.slice(1));
    await expect(page.getByTestId("route-failed")).toHaveCount(0);
    await expect(page.getByTestId("route-loading")).toHaveCount(0);
    if (route === "overview") await expect(page.locator(".chart canvas").first()).toBeVisible();
    if (route === "knowledge" || route === "live-system") await expect(page.locator(".react-flow__node").first()).toBeVisible();
    if (route === "luffy") await expect(page.getByRole("img", { name: /mechanical core/ })).toBeVisible();
    await settle(page);
    await page.screenshot({ path: out(`desktop-${route}.png`) });
    result[route] = { readyMs: Math.round(performance.now() - start), viewport: [1440, 1000] };
  }
  writeFileSync(out("desktop-routes.json"), JSON.stringify(result, null, 2));
});

test("selected evidence and mobile review frames", async ({ page, request }) => {
  test.setTimeout(120000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await login(page);
  for (const [name, route, selector] of [
    ["trade-story", "trades?trade=pos_fixture01", "[data-testid='trade-chain']"],
    ["strategy-workspace", "strategies?id=spec_fixture_trend", "[data-testid='strategy-workspace']"],
    ["research-detail", "research?combo=a1b2c3d4e5f60718", "[data-testid='research-item']"],
  ]) {
    await page.goto(`${LIVE}/#${route}`);
    await expect(page.locator(selector)).toBeVisible({ timeout: 10000 });
    await settle(page);
    await page.screenshot({ path: out(`${name}.png`) });
  }
  await page.goto(`${LIVE}/#knowledge`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  await page.getByRole("button", { name: "Inspect Absorption Test", exact: true }).click();
  await expect(page.getByRole("complementary", { name: "Node inspector" })).toContainText("Absorption Test");
  await settle(page);
  await page.screenshot({ path: out("knowledge-selected.png") });
  await page.goto(`${LIVE}/#live-system`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  await page.locator(".react-flow__node").first().click();
  await expect(page.getByRole("complementary", { name: "Node inspector" })).toContainText("Selected record");
  await settle(page);
  await page.screenshot({ path: out("live-system-selected.png") });
  await state(request, { chat: "mentions" });
  await page.goto(`${LIVE}/#luffy`);
  await page.getByLabel("Message LUFFY").fill("What did you open recently?");
  await page.getByLabel("Message LUFFY").press("Enter");
  await expect(page.getByTestId("reply-evidence")).toBeVisible();
  await settle(page);
  await page.screenshot({ path: out("luffy-active.png") });

  await page.setViewportSize({ width: 390, height: 844 });
  for (const [name, route, selector] of [
    ["overview", "overview", ".chart canvas"],
    ["luffy", "luffy", ".composer textarea"],
    ["knowledge", "knowledge", ".graph-list button"],
    ["trade-story", "trades?trade=pos_fixture01", "[data-testid='trade-chain']"],
  ]) {
    await page.goto(`${LIVE}/#${route}`);
    await expect(page.locator(selector).first()).toBeVisible({ timeout: 10000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0);
    await settle(page);
    await page.screenshot({ path: out(`mobile-${name}.png`) });
  }
});

test("state-tied motion, static non-evidence, and reduced motion", async ({ page, request }) => {
  test.setTimeout(120000);
  const observations: Record<string, unknown> = {};
  // The isolated demo adapter emits actual partial reply chunks, so each UI
  // state is observable without inventing a production streaming contract.
  await page.goto("http://127.0.0.1:4174/#luffy");
  const core = page.getByRole("img", { name: /mechanical core/ });
  await expect(core).toHaveAttribute("data-state", "idle");
  observations.idle = await core.getAttribute("data-animating");
  await page.getByLabel("Message LUFFY").fill("Show evidence");
  await page.getByLabel("Message LUFFY").press("Enter");
  await expect(core).toHaveAttribute("data-state", "pending");
  observations.pending = await core.getAttribute("data-state");
  await expect(core).toHaveAttribute("data-state", "responding", { timeout: 5000 });
  observations.responding = await core.getAttribute("data-state");
  await expect(core).toHaveAttribute("data-state", "idle", { timeout: 5000 });
  observations.reply = await page.locator(".message.luffy").count();
  await page.screenshot({ path: out("motion-luffy-idle-after-reply.png") });

  await state(request, { chat: "fail" });
  await login(page);
  await page.goto(`${LIVE}/#luffy`);
  await page.getByLabel("Message LUFFY").fill("Read a record");
  await page.getByLabel("Message LUFFY").press("Enter");
  await expect(page.getByRole("img", { name: /mechanical core/ })).toHaveAttribute("data-state", "error");
  observations.error = await page.getByRole("img", { name: /mechanical core/ }).getAttribute("data-animating");
  await page.screenshot({ path: out("motion-luffy-error.png") });

  await page.goto(`${LIVE}/#knowledge`);
  await expect(page.locator(".react-flow__node").first()).toBeVisible();
  await page.getByRole("button", { name: "Inspect Aggressor Flow", exact: true }).click();
  await page.getByText("Trace a directed path").click();
  await page.getByLabel("Path destination").selectOption({ label: "Absorption Test" });
  observations.path = await page.locator(".path-trace [role='status']").innerText();
  observations.highlightedEdges = await page.locator(".react-flow__edge.path-selected").count();
  expect(observations.highlightedEdges).toBeGreaterThan(0);
  await page.screenshot({ path: out("motion-knowledge-path.png") });

  await page.goto(`${LIVE}/#live-system`);
  await expect(page.getByTestId("system-observed")).toBeVisible();
  const observed = page.locator(".flow-lane.lane-observed").first();
  const declared = page.locator(".flow-lane.lane-declared-only").first();
  observations.observed = { state: await observed.getAttribute("data-flow"), animation: await observed.locator(".lane-line").evaluate((e) => getComputedStyle(e, "::before").animationName) };
  observations.declared = { state: await declared.getAttribute("data-flow"), animation: await declared.locator(".lane-line").evaluate((e) => getComputedStyle(e, "::before").animationName) };
  expect(observations.observed).toEqual({ state: "fresh", animation: "evidence-arrival" });
  expect(observations.declared).toEqual({ state: null, animation: "none" });
  await page.screenshot({ path: out("motion-live-flows.png") });

  await page.goto(`${LIVE}/#trades?trade=pos_fixture01`);
  await expect(page.getByTestId("trade-chain")).toBeVisible();
  observations.tradeStages = await page.getByTestId("trade-chain").locator("li").count();
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("link", { name: "Knowledge" }).click();
  observations.routeTransition = await page.locator(".page-heading").evaluate((e) => getComputedStyle(e).animationName);

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto(`${LIVE}/#luffy`);
  const reducedCore = page.getByRole("img", { name: /mechanical core/ });
  await expect(reducedCore).toHaveAttribute("data-animating", "false");
  observations.reduced = { core: await reducedCore.getAttribute("data-animating"), headingDuration: await page.locator(".page-heading").evaluate((e) => getComputedStyle(e).animationDuration) };
  writeFileSync(out("motion-observations.json"), JSON.stringify(observations, null, 2));
});
