import {test,expect} from "@playwright/test";
import {LIVE,login} from "./liveHelpers";

test("typed owner interrogations and Needs You are available without fixture substitution",async({page})=>{
  await login(page); await page.goto(LIVE+"/#luffy");
  await expect(page.getByTestId("needs-you-items")).toBeVisible();
  await page.getByText("Interrogate exact internal evidence").click();
  await page.getByLabel("Evidence class").selectOption("portfolio");
  await page.getByLabel("Exact stored identity").fill("missing-cut");
  await page.getByRole("button",{name:"Read evidence"}).click();
  await expect(page.getByTestId("owner-evidence-query")).toContainText("UNAVAILABLE");
  await expect(page.getByTestId("owner-evidence-query")).toContainText("exact_records_UNAVAILABLE");
  await expect(page.locator("body")).not.toContainText("Synthetic/fixture");
});

test("Knowledge lenses distinguish event chronology from file modification and absent code",async({page})=>{
  await login(page); await page.goto(LIVE+"/#knowledge");
  await page.getByRole("button",{name:"Evidence",exact:true}).click();
  await expect(page.getByRole("button",{name:"Timeline",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"Timeline",exact:true}).click();
  await expect(page.locator("body")).toContainText("File modification time");
  await expect(page.locator("body")).toContainText("Event chronology");
  await page.getByRole("button",{name:"Code",exact:true}).click();
  await expect(page.locator("body")).toContainText("No matching nodes");
});

test("failed Needs You endpoint is explicit and cannot enable approvals",async({page})=>{
  await page.route("**/owner-api/v1/needs-you",r=>r.fulfill({status:503,contentType:"application/json",body:'{"error":"unavailable"}'}));
  await login(page);
  await page.goto(LIVE+"/#luffy");
  await expect(page.getByTestId("needs-you-items")).toContainText("No substitute data was loaded");
  await expect(page.getByRole("button",{name:"Approve exact request"})).toHaveCount(0);
});

test("pending owner actions survive completed history and later pages are reachable",async({page})=>{
  const item={item_id:"pending-first-live",action_type:"first_live",reason:"Pending first-live request",validity:"VALID",receipt:null,decision_operation:"approval_decision"};
  await page.route("**/owner-api/v1/needs-you*",r=>r.fulfill({contentType:"application/json",body:JSON.stringify({generated_at:new Date().toISOString(),items:[{...item,reason:r.request().url().includes("offset=100") ? "Oldest pending request" : item.reason}],pending_total:101,total:101,truncated:!r.request().url().includes("offset=100"),unavailable:[]})}));
  await login(page);
  await page.goto(LIVE+"/#overview");
  await expect(page.getByTestId("needs-you-items")).toContainText("101 pending or stale owner items");
  await expect(page.getByTestId("needs-you-items")).toContainText("Pending first-live request");
  await page.goto(LIVE+"/#luffy");
  await page.getByRole("button",{name:"More owner items"}).click();
  await expect(page.getByTestId("needs-you-items")).toContainText("Oldest pending request");
});

test("Research route renders stored questions rather than false absence",async({page})=>{
  await login(page);
  await page.route("**/owner-api/v1/research*",r=>r.fulfill({contentType:"application/json",body:JSON.stringify({generated_at:new Date().toISOString(),available:true,partial:true,reason:"quantitative result store unavailable",questions:[{record_id:"question:q0",identity:"q0",source:"research_questions",sha256:"a".repeat(64),value:{question:"Stored inconclusive question"}}],questions_page:{offset:0,limit:50,has_more:false},unavailable:[]})}));
  await page.goto(LIVE+"/#research");
  await page.getByRole("button",{name:/Questions/}).click();
  await expect(page.locator("body")).toContainText("question:q0");
  await expect(page.locator("body")).not.toContainText("no persisted research-question store");
});
