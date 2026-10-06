import { test, expect } from "@playwright/test";
import { login, state, LIVE } from "./liveHelpers";
import { mkdirSync } from "node:fs";

test.beforeEach(async ({request}) => {
  await state(request, { scenario: "normal", gateway: "up", fail: [], reset_calls: true });
});
test("GUI-01 stopped kernel, figures and exact owner evidence remain readable", async ({page}) => {
  await page.route("**/owner-api/v1/overview", async route => {
    const response = await route.fetch();
    const d = await response.json();
    d.kernel_process.state = "STOPPED";
    d.heartbeat.runtime_state = "STOPPED";
    await route.fulfill({json: d});
  });
  await page.route("**/owner-api/v1/needs-you", route => route.fulfill({json: {
    generated_at: new Date().toISOString(), pending_total: 1, truncated: false, unavailable: [],
    items: [{ item_id: "approval-exact-42", action_type: "RESEARCH", validity: "VALID", reason: "Exact evidence fixture", affected_object: "candidate-42", binding_hash: "hash-42", evidence: {path: "evidence/exact-42"} }]
  }}));
  await login(page);
  await expect(page.locator(".status-row", {hasText: "Kernel process"})).toContainText("STOPPED");
  await expect(page.getByTestId("realized-today")).toHaveText("2.00 USDT");
  await page.getByTestId("overview-sources").locator("summary").click();
  await expect(page.getByTestId("overview-sources")).toContainText("overview-journal-realized.v2");
  await expect(page.getByTestId("overview-sources")).toContainText("overview-entry-exposure.v1");
  await page.getByTestId("owner-item-approval-exact-42").locator("summary").click();
  await expect(page.getByTestId("owner-item-approval-exact-42")).toContainText("evidence/exact-42");
  await expect(page.getByTestId("owner-item-approval-exact-42")).toContainText("candidate-42");
  await page.getByTestId("overview-sources").locator("summary").click();
  mkdirSync("../docs/tracker/evidence/gui01-overview-sol", {recursive:true});
  await page.screenshot({path:"../docs/tracker/evidence/gui01-overview-sol/overview-stopped-desktop.png",fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:"../docs/tracker/evidence/gui01-overview-sol/overview-stopped-mobile.png",fullPage:true});
});
test("GUI-01 partial realized stays unavailable with explicit coverage", async ({page, request}) => {
  await page.route("**/owner-api/v1/overview", async route => {
    const response = await route.fetch(); const d = await response.json();
    d.realized_today.value = null; d.realized_today.quality = "PARTIAL_UNKNOWN";
    d.realized_today.coverage = {known:1,total:2,complete:false};
    await route.fulfill({json:d});
  });
  await login(page);
  await expect(page.getByTestId("realized-today")).toHaveText("UNAVAILABLE");
  await page.getByTestId("overview-sources").locator("summary").click();
  await expect(page.getByTestId("overview-sources")).toContainText("PARTIAL_UNKNOWN");
});
test("GUI-01 missing observations do not become zero", async ({page, request}) => {
  await state(request,{scenario:"missing"}); await login(page);
  // An empty readable journal today is a measured zero; missing equity remains unknown.
  await expect(page.getByTestId("realized-today")).toHaveText("0.00 USDT");
  await expect(page.locator(".capital-figure", {hasText:"Account equity"})).toContainText("UNAVAILABLE");
  await expect(page.getByTestId("control-state")).toHaveText("UNAVAILABLE");
});
