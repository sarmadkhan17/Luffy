import { test, expect, type Page } from "@playwright/test";
import { login, state } from "./liveHelpers";

/** LUFFY-PROTECTION-SNAPSHOT-R2: protection evidence expires on the browser
 * clock from the snapshot's own checked_at. A failed poll keeps the old query
 * data, but never keeps it VERIFIED. */
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

const badge = (page: Page) =>
  page.locator(".status-row", { hasText: "Protection verification" }).locator(".badge");

test("VERIFIED → API failures → clock passes expiry → STALE", async ({ page }) => {
  await page.clock.install();
  await login(page);
  await expect(badge(page)).toHaveText("VERIFIED");
  // every overview poll fails from now on
  let failed = 0;
  await page.route("**/owner-api/v1/overview", (route) => {
    failed += 1;
    return route.abort("failed");
  });
  await page.clock.fastForward("00:40"); // one failed poll; 70 s after checked_at
  await expect.poll(() => failed).toBeGreaterThan(0);
  await expect(badge(page)).toHaveText("VERIFIED");
  await page.clock.fastForward("01:00"); // 130 s after checked_at (> 120 s)
  await expect(badge(page)).toHaveText("STALE");
  await expect(badge(page)).not.toHaveClass(/mint/);
  const cells = page.locator(".bottom-grid table tbody tr .protection-cell .badge");
  await expect(cells.first()).toHaveText("STALE");
  await expect(page.locator(".bottom-grid table tbody tr").first()).toContainText(
    "Last reported: VERIFIED",
  );
});

test("VERIFIED → UNREADABLE when the next poll succeeds with a failed check", async ({
  page,
  request,
}) => {
  await login(page);
  await expect(badge(page)).toHaveText("VERIFIED");
  await state(request, { scenario: "protection_unreadable" });
  await page.reload();
  await expect(badge(page)).toHaveText("UNREADABLE");
});

test("missing → first snapshot → VERIFIED; PARTIAL → VERIFIED", async ({ page, request }) => {
  await state(request, { scenario: "protection_missing" });
  await login(page);
  await expect(badge(page)).toHaveText("UNAVAILABLE");
  await state(request, { scenario: "normal" });
  await page.reload();
  await expect(badge(page)).toHaveText("VERIFIED");
  await state(request, { scenario: "protection_partial" });
  await page.reload();
  await expect(badge(page)).not.toHaveText("VERIFIED");
  await state(request, { scenario: "normal" });
  await page.reload();
  await expect(badge(page)).toHaveText("VERIFIED");
});
