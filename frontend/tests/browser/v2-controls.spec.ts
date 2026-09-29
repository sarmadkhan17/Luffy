import { test, expect } from "@playwright/test";
import { login, state, calls } from "./liveHelpers";
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
test("all five controls use confirmations and the Owner Interface", async ({
  page,
  request,
}) => {
  await login(page);
  await page.getByRole("link", { name: "Operations", exact: true }).click();
  for (const op of ["freeze", "halt", "resume", "unhalt", "panic"]) {
    await page.locator(`[data-op="${op}"]`).click();
    await expect(page.getByRole("dialog")).toBeVisible();
    await page
      .getByRole("button", { name: /Send .* to the Owner Interface/ })
      .click();
    await expect(page.getByTestId("control-outcome")).toContainText(`${op}:`);
    await expect(page.getByTestId("control-outcome")).toContainText(
      "Audit events",
    );
  }
  const c = await calls(request);
  expect(
    c.gateway
      .map((x: { operation: string }) => x.operation)
      .filter((op: string) => op !== "health"),
  ).toEqual(["freeze", "halt", "resume", "unhalt", "panic"]);
  expect(c.control_state).toBe("FROZEN");
});
test("IN_PROGRESS retains the same id and time across route exit and retry", async ({
  page,
}) => {
  const sent: { r: string; t: number }[] = [];
  await page.route("**/graphql", async (route) => {
    const d = route.request().postDataJSON();
    if (!d.query.startsWith("mutation")) return route.continue();
    sent.push(d.variables);
    await route.fulfill({
      json: {
        data: {
          res: {
            request_id: d.variables.r,
            operation: "freeze",
            status: "IN_PROGRESS",
            disposition: "IN_PROGRESS",
            message: "Recovery still running",
            audit_event_ids: [],
            reasons: [],
          },
        },
      },
    });
  });
  await login(page);
  await page.getByRole("link", { name: "Operations", exact: true }).click();
  await page.locator('[data-op="freeze"]').click();
  await page.getByRole("button", { name: /Send Freeze/ }).click();
  await expect(page.getByTestId("control-outcome")).toContainText(
    "IN_PROGRESS",
  );
  await page.getByRole("link", { name: "LUFFY", exact: true }).click();
  await page.getByRole("link", { name: "Operations", exact: true }).click();
  await page.getByRole("button", { name: "Retry Freeze", exact: true }).click();
  await expect(page.getByTestId("control-outcome")).toContainText(
    "IN_PROGRESS",
  );
  expect(sent).toHaveLength(2);
  expect(sent[0].r).toBe(sent[1].r);
  expect(sent[0].t).toBe(sent[1].t);
});
