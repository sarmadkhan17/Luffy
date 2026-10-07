import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
const fixtures = JSON.parse(readFileSync("/mnt/luffy-data/test-tmp/glacier-browser-fixtures.json", "utf8"));
const routes = ["Overview", "Trades", "Research", "Strategies", "LUFFY", "Operations", "Live System", "Knowledge", "Diagnostics"];
async function intercept(page: import("@playwright/test").Page, overrides: Record<string, any> = {}) {
  const data = { ...structuredClone(fixtures), ...overrides };
  const writes: string[] = [], errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await page.route("**/*", async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.origin !== "http://127.0.0.1:4180") throw new Error(`External request forbidden: ${url.origin}`);
    if (url.pathname.startsWith("/owner-api/") || url.pathname.startsWith("/api/") || url.pathname.includes("graphql")) {
      if (request.method() !== "GET") writes.push(url.pathname);
      const body = data[url.pathname];
      return route.fulfill({ status: body ? 200 : 503, contentType: "application/json", body: JSON.stringify(body ?? { error: "not reported by isolated browser fixture" }) });
    }
    return route.continue();
  });
  return { writes, errors };
}
for (const name of routes) {
  test(`${name}: permanent route renders approved Glacier with isolated read-only truth`, async ({ page }) => {
    const { writes, errors } = await intercept(page);
    const slug = name.toLowerCase().replaceAll(" ", "-");
    await page.goto(`/#${slug}`);
    await expect(page.getByRole("heading", { name, exact: true }).first()).toBeVisible();
    await expect(page.getByTestId("mode-banner")).toContainText("READ-ONLY");
    await expect(page.getByTestId("route-failed")).toHaveCount(0);
    if (name === "Overview" || name === "Trades") {
      await expect(page.getByTestId("runtime-labels")).toContainText("account environment unavailable");
      await expect(page.getByTestId("runtime-labels")).toContainText("READ_ONLY_GUI");
    }
    if (name === "Trades") for (const label of ["Resume", "Freeze", "Halt", "PANIC"]) await expect(page.getByRole("button", { name: label, exact: true })).toBeDisabled();
    if (name === "Strategies") await expect(page.getByRole("heading", { name: "Versioned strategy authority" })).toBeVisible();
    if (name === "LUFFY") await expect(page.getByTestId("provider-state")).toContainText("Provider: DISABLED");
    if (name === "Operations") await expect(page.getByTestId("operations-disclosure")).toContainText("counted, not listed");
    if (name === "Live System") {
      await expect(page.getByRole("button", { name: "Strategy Governor — DECLARED ARCHITECTURE" })).toBeVisible();
      await expect(page.locator(".pk")).toHaveCount(0);
      await page.getByRole("button", { name: "Risk — DECLARED ARCHITECTURE" }).click();
      await expect(page.getByRole("dialog")).toContainText("final entry authority");
      await page.getByRole("button", { name: "Close panel" }).click();
    }
    if (name === "Knowledge") {
      await page.getByRole("button", { name: "List", exact: true }).click();
      await page.getByRole("group", { name: "View" }).getByRole("button", { name: "Map", exact: true }).click();
    }
    if (name === "Diagnostics") {
      await expect(page.getByRole("table", { name: "Filesystem capacity" })).toContainText("Root /");
      await expect(page.getByRole("table", { name: "Filesystem capacity" })).toContainText("Recovery");
      await expect(page.getByTestId("growth-baseline")).toContainText("Growth baseline unavailable");
    }
    await page.screenshot({ path: `/mnt/luffy-data/test-tmp/glacier-${slug}.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("heading", { name, exact: true }).first()).toBeVisible();
    expect(writes).toEqual([]); expect(errors).toEqual([]);
  });
}
for (const environment of ["demo", "testnet", "production"]) test(`dynamic ${environment} account labels require fresh backend evidence`, async ({ page }) => {
  const boot = structuredClone(fixtures["/owner-api/v1/bootstrap"]);
  boot.dashboard_mode = "LIVE";
  boot.runtime_truth.account = { venue: "verified-venue", environment, observed_at_ms: Date.now(), snapshot_id: "test-snapshot", freshness: "fresh", source: "isolated test fixture" };
  await intercept(page, { "/owner-api/v1/bootstrap": boot });
  await page.goto("/#trades");
  await expect(page.getByTestId("runtime-labels")).toContainText(`LIVE GUI · verified-venue · ${environment} account`);
  await expect(page.getByTestId("runtime-labels")).not.toContainText("Binance demo");
});
test("research bank count and registered look are independent of disabled referee", async ({ page }) => {
  const board = structuredClone(fixtures["/owner-api/v1/research/board"]);
  Object.assign(board, { candidates_by_state: { queued: 4 }, referee_enabled: false, held_out_looks: { n: 2 }, stores: { ...board.stores, registrations: 2 } });
  await intercept(page, { "/owner-api/v1/research/board": board });
  await page.goto("/#research");
  await expect(page.getByText("queued: 4")).toBeVisible();
  await expect(page.getByTestId("referee-status")).toContainText("Referee disabled · 2 registered held-out look(s) exist");
  await expect(page.locator("main")).not.toContainText("none yet");
});
test("exact version shows stale approval and unavailable economics separately from legacy", async ({ page }) => {
  const board = structuredClone(fixtures["/owner-api/v1/strategies/board"]);
  board.versions = [{ strategy_id: "s-test", version_id: "v-test", created_at_ms: Date.now(), version: { spec: { name: "Synthetic version fixture" }, spec_hash: "hash-test", lineage_sha256: "lineage-test", parent_version_id: null }, validation: null, install: null, probation: null, approval: { item_id: "request-test", affected_object: "v-test", status: "STALE", validity: "STALE" }, governor: { to_state: "PAUSED", reason_code: "fixture reason", transition_id: "transition-test" }, lifecycle: "PAUSED", economics: null, errors: {} }];
  board.authority_errors = {};
  await intercept(page, { "/owner-api/v1/strategies/board": board });
  await page.goto("/#strategies");
  await page.getByRole("button", { name: /Synthetic version fixture/ }).click();
  await expect(page.getByRole("dialog")).toContainText("hash-test");
  await expect(page.getByRole("dialog")).toContainText("lineage-test");
  await expect(page.getByRole("dialog")).toContainText("STALE");
  await expect(page.getByRole("dialog")).toContainText("UNAVAILABLE — no current exact economics");
});
