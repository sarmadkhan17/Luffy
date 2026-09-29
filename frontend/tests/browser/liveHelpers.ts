import { expect, type Page, type APIRequestContext } from "@playwright/test";

export const LIVE = "http://127.0.0.1:4176";
export const H = { "x-luffy-token": "fixture-token" };

export async function state(request: APIRequestContext, body: object) {
  const r = await request.post(LIVE + "/__test__/state", { headers: H, data: body });
  expect(r.ok()).toBeTruthy();
}
export async function calls(request: APIRequestContext) {
  return (await request.get(LIVE + "/__test__/calls", { headers: H })).json();
}
export async function login(page: Page) {
  await page.goto(LIVE + "/owner-preview/");
  await page.getByLabel("Dashboard password").fill("fixture-token");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("mode-banner")).toContainText("LIVE");
}

