import { test, expect } from "@playwright/test";
import { login, state, calls } from "./liveHelpers";

// LUFFY-OWNER-CLOSE-TRADE-UI-R1 is DEFERRED / SAFETY_BLOCKED: the LIVE React
// Trades page must not render or send a close-one-trade control.
test.beforeEach(async ({ request }) => {
  await state(request, {
    scenario: "normal",
    gateway: "up",
    fail: [],
    ttl: 43200,
    reset_calls: true,
  });
});

test("an open trade's record in the LIVE Trades page has no close control", async ({
  page,
  request,
}) => {
  const mutations: string[] = [];
  page.on("request", (r) => {
    if (
      r.url().endsWith("/graphql") &&
      (r.postData() ?? "").includes("mutation")
    )
      mutations.push(r.postData() ?? "");
  });
  await login(page);
  await page.getByRole("link", { name: "Trades", exact: true }).click();
  await expect(page.getByTestId("trade-range")).toContainText("Records 1–");
  await page
    .locator("section")
    .filter({
      has: page.getByRole("heading", { name: "Trade book", exact: true }),
    })
    .getByRole("button", { name: "Inspect BTC/USDT" })
    .click();
  const drawer = page.getByRole("dialog", { name: "BTC/USDT" });
  await expect(drawer).toContainText("Journal entry");
  await expect(page.getByRole("button", { name: /close trade/i })).toHaveCount(
    0,
  );
  await expect(page.getByText(/send close/i)).toHaveCount(0);
  expect(mutations).toEqual([]);
  const sent = (await calls(request)).gateway as { operation: string }[];
  expect(sent.filter((c) => c.operation === "close_trade")).toEqual([]);
});
