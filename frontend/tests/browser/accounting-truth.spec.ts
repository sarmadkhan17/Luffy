import {test,expect} from "@playwright/test";
import {login,state,LIVE} from "./liveHelpers";
import {mkdirSync} from "node:fs";
test.beforeEach(async({request})=>{
  await state(request,{scenario:"normal",gateway:"up",fail:[],reset_calls:true});
});
test("Overview UNKNOWN journal placeholder never becomes flat P&L",async({page,request})=>{
  await state(request,{accounting_close:{pnl:0,evidence:{basis:"reconcile_ghost_unpriced",pnl_status:"UNKNOWN",pnl_value_class:"UNKNOWN",exit_price_source:"journal_entry_not_a_fill"}}});
  await login(page);
  await expect(page.getByTestId("realized-today")).toHaveText("UNAVAILABLE");
  await page.getByTestId("overview-sources").locator("summary").click();
  await expect(page.getByTestId("overview-sources")).toContainText("PARTIAL_UNKNOWN");
  await expect(page.getByTestId("overview-sources")).toContainText("journal_entry_not_a_fill");
  await page.getByTestId("overview-sources").locator("summary").click();
  await expect(page.getByTestId("overview-evidence")).toContainText("UNKNOWN");
  mkdirSync("../docs/tracker/evidence/accounting-truth-followup-sol",{recursive:true});
  await page.screenshot({path:"../docs/tracker/evidence/accounting-truth-followup-sol/overview-unknown.png",fullPage:true});
  await page.goto(LIVE+"/#trades?trade=t-btc");
  const economics=page.getByRole("region",{name:"Economics evidence"});
  await expect(page.getByRole("table",{name:"Journal bookings"})).toContainText("UNKNOWN");
  await expect(page.getByRole("table",{name:"Journal bookings"})).not.toContainText("JOURNAL_BOOKED");
});
test("Overview derived P&L remains visibly estimated",async({page,request})=>{
  await state(request,{accounting_close:{pnl:10,evidence:{basis:"panic_order_unconfirmed",pnl_status:"ESTIMATE",pnl_value_class:"DERIVED_ESTIMATE",exit_price_source:"ticker_last_not_a_fill"}}});
  await login(page);
  await expect(page.getByTestId("realized-today")).toHaveText("12.00 USDT");
  await expect(page.locator(".capital-figure",{hasText:"Realized today"})).toContainText("estimate");
  await expect(page.getByTestId("overview-evidence")).toContainText("estimate");
  await page.screenshot({path:"../docs/tracker/evidence/accounting-truth-followup-sol/overview-estimate.png",fullPage:true});
  await page.goto(LIVE+"/#trades?trade=t-btc");
  await expect(page.getByRole("table",{name:"Journal bookings"})).toContainText("DERIVED_ESTIMATE");
  await expect(page.getByRole("table",{name:"Journal bookings"})).toContainText("ticker_last_not_a_fill");
  await expect(page.getByRole("table",{name:"Venue monetary fields"})).not.toContainText("DERIVED_ESTIMATE");
});
