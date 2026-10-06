import { test, expect } from "@playwright/test";
import { LIVE, login, state } from "./liveHelpers";
test.beforeEach(async ({ request }) => {
  await state(request, { scenario: "normal", gateway: "up", fail: [], reset_calls: true });
});
test("OWN-04 overview labels permission separately from unknown process evidence", async ({ page }) => {
  await login(page);
  await expect(page.getByText("Stored control permission · not evidence of running work.")).toBeVisible();
  await expect(page.locator(".status-row", { hasText: "Kernel process" })).toContainText("UNKNOWN");
  await expect(page.getByTestId("control-state")).toHaveText("FROZEN");
});
test("OWN-04 cached activity ages locally and disconnect clears current activity", async ({ page }) => {
  await login(page);
  await page.clock.install();
  const at = new Date().toISOString();
  let failed = false;
  await page.route("**/owner-api/v1/system", async route => {
    if (failed) return route.fulfill({ status: 503, json: { error: "disconnected" } });
    const response = await route.fetch();
    const value = await response.json();
    for (const node of value.nodes) if (node.id === "dashboard") node.telemetry = {
      observed_at: at, age_s: 0, stale_after_s: 1, freshness: "fresh", health: "active",
      source: "fixture server response", summary: "response observation" };
    await route.fulfill({ json: value });
  });
  await page.goto(LIVE + "/#live-system");
  const node = page.locator(".react-flow__node", { hasText: "Dashboard" });
  await expect(node).toContainText("ACTIVE");
  await page.clock.fastForward(2000);
  await expect(node).toContainText("STALE");
  await expect(node).not.toHaveClass(/status-active/);
  failed = true;
  await page.clock.fastForward(31000);
  await expect(node).toContainText("UNAVAILABLE");
  await expect(node).not.toHaveClass(/status-active/);
});
