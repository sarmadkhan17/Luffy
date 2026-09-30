/** Root cutover: the React owner frontend is the dashboard at /. Every hash
 * route direct-loads and survives a reload; nothing requests the removed
 * /owner-preview/ or /legacy/ paths or the deleted legacy scripts. */
import { test, expect } from "@playwright/test";
import { LIVE, login } from "./liveHelpers";

const ROUTES: [string, string][] = [
  ["overview", "Overview"],
  ["trades", "Trades"],
  ["research", "Research"],
  ["strategies", "Strategies"],
  ["luffy", "Luffy"],
  ["operations", "Operations"],
  ["live-system", "Live System"],
  ["knowledge", "Knowledge"],
  ["diagnostics", "Diagnostics"],
];

test("all nine root hash routes direct-load and refresh", async ({ page }) => {
  const bad: string[] = [];
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (/^\/(owner-preview|legacy)(\/|$)|^\/(attention|investigation)\.js$/.test(p))
      bad.push(p);
  });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page);
  for (const [route, title] of ROUTES) {
    await page.goto(LIVE + `/#${route}`);
    await expect(page.locator(".page-heading h1")).toHaveText(new RegExp(`^${title}$`, "i"));
    await expect(page.locator('nav a[aria-current="page"]')).toHaveAttribute(
      "href",
      `#${route}`,
    );
    await page.reload();
    await expect(page.locator(".page-heading h1")).toHaveText(new RegExp(`^${title}$`, "i"));
    await expect(page.getByTestId("mode-banner")).toContainText("LIVE");
    await expect(page.locator('a[href^="/legacy"]')).toHaveCount(0);
  }
  expect(bad).toEqual([]);
  expect(errors).toEqual([]);
});

test("deferred features keep their reviewed truth at /", async ({ page }) => {
  await login(page);
  await page.goto(LIVE + "/#overview");
  await expect(page.getByText(/News guard: unavailable/)).toBeVisible();
  for (const route of ["overview", "trades", "operations"]) {
    await page.goto(LIVE + `/#${route}`);
    await page.waitForLoadState("networkidle");
    await expect(
      page.getByRole("button", { name: /close (this |one )?trade/i }),
    ).toHaveCount(0);
  }
});

test("root timing: cold / and warm Overview", async ({ browser }) => {
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await login(page);
  await page.goto("about:blank");
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
  let t0 = Date.now();
  await page.goto(LIVE + "/");
  await expect(page.locator(".page-heading h1")).toHaveText("Overview");
  const coldNav = Date.now() - t0;
  await expect(page.getByText("5,164.50 USDT").first()).toBeVisible();
  const coldUseful = Date.now() - t0;
  await cdp.send("Network.setCacheDisabled", { cacheDisabled: false });
  await page.goto(LIVE + "/#overview");
  await page.goto("about:blank");
  t0 = Date.now();
  await page.goto(LIVE + "/#overview");
  await expect(page.getByText("5,164.50 USDT").first()).toBeVisible();
  const warmUseful = Date.now() - t0;
  console.log(
    "ROOT_TIMING " + JSON.stringify({ coldNav, coldUseful, warmUseful }),
  );
  await ctx.close();
});
