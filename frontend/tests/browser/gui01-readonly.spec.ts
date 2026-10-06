import {test,expect} from "@playwright/test";
test("GUI-01 real read-only server renders Overview with Kernel STOPPED",async({page})=>{
  await page.goto("/"); await page.getByLabel("Dashboard password").fill("fixture-token");
  await page.getByRole("button",{name:"Sign in"}).click();
  await expect(page.locator(".status-row",{hasText:"Kernel process"})).toContainText("STOPPED");
  await expect(page.getByTestId("realized-today")).toHaveText("2.00 USDT");
  await expect(page.getByTestId("control-state")).toHaveText("FROZEN");
  await expect(page.getByTestId("needs-you-items")).toBeVisible();
  await expect(page.getByTestId("mode-banner")).toContainText("READ-ONLY GUI");
  await page.getByTestId("overview-sources").locator("summary").click();
  await expect(page.getByTestId("overview-sources")).toContainText("overview-journal-realized.v2");
  await page.getByTestId("overview-sources").locator("summary").click();
  await page.screenshot({path:"../docs/tracker/evidence/gui01-overview-sol/readonly-stopped.png",fullPage:true});
});
