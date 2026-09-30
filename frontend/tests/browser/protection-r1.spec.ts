import { test, expect, type Page } from "@playwright/test";
import { login, state, LIVE } from "./liveHelpers";

/** LUFFY-PROTECTION-SNAPSHOT-R1: the owner page shows the kernel's read-only
 * protection snapshot truthfully and never causes a venue read itself. */
test.beforeEach(async ({ request }) =>
  state(request, {
    scenario: "normal",
    gateway: "up",
    chat: "ok",
    fail: [],
    ttl: 43200,
    reset_calls: true,
  }),
);

const visibility = (page: Page) =>
  page.locator(".status-row", { hasText: "Protection verification" });

test("fresh complete snapshot reads VERIFIED", async ({ page }) => {
  await login(page);
  await expect(visibility(page).locator(".badge")).toHaveText("VERIFIED");
  await expect(page.locator(".bottom-grid table tbody tr").first()).toContainText(
    "Checked",
  );
});

for (const [scenario, label, reason] of [
  ["protection_unreadable", "UNREADABLE", "venue_timeout"],
  ["protection_partial", "UNPROTECTED", "position_unprotected"],
  ["protection_missing", "UNAVAILABLE", "no_protection_snapshot"],
] as const) {
  test(`snapshot ${scenario} is never shown healthy`, async ({ page, request }) => {
    await state(request, { scenario });
    await login(page);
    await expect(visibility(page).locator(".badge")).toHaveText(label);
    await expect(visibility(page).locator(".badge.mint")).toHaveCount(0);
    const overview = await page.evaluate(async () =>
      (await fetch("/owner-api/v1/overview")).json(),
    );
    const reasons = overview.positions.flatMap(
      (p: { protection: { reasons: string[] } }) => p.protection.reasons,
    );
    expect(reasons.join(" ")).toContain(reason);
    if (scenario === "protection_missing")
      await expect(page.getByText("NOT VERIFIED · no protection check")).toBeVisible();
  });
}

test("browser traffic never reaches a venue", async ({ page }) => {
  const hosts = new Set<string>();
  page.on("request", (r) => hosts.add(new URL(r.url()).host));
  await login(page);
  await expect(visibility(page).locator(".badge")).toHaveText("VERIFIED");
  await page.reload();
  await expect(visibility(page).locator(".badge")).toHaveText("VERIFIED");
  expect([...hosts]).toEqual([new URL(LIVE).host]);
});
