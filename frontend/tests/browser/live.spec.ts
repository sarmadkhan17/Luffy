/** LIVE binding against the real FastAPI app (tests/owner_frontend_server.py):
 * auth Guard, /owner-api/v1, GraphQL owner mutations, the LIVE build served at
 * /. Kernel Owner Interface and chat LLM are fakes. */
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

import { LIVE, H, state, calls, login } from "./liveHelpers";

const FIXTURE_TEXT = /Synthetic|fixture|DEMO —/i;

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

test("LIVE overview binds real backend values, never fixtures", async ({
  page,
}) => {
  const requests: string[] = [];
  page.on("request", (r) => requests.push(new URL(r.url()).pathname));
  await login(page);
  await expect(page).toHaveTitle(/LIVE/);
  await expect(page.getByTestId("control-state")).toHaveText("FROZEN");
  await expect(page.getByText("5,164.50 USDT").first()).toBeVisible();
  const rows = page.locator(".bottom-grid table tbody tr");
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("VERIFIED");
  await expect(rows.first()).toContainText("order ref recorded");
  // optional enrichment arrives separately
  await expect(rows.first()).toContainText("10.00 USDT");
  // Exact backend provenance may name its fixture source in this test server.
  await expect(page.locator(".command-deck")).not.toContainText(FIXTURE_TEXT);
  await expect(page.getByLabel("Fixture scenario")).toHaveCount(0);
  expect(requests.some((p) => p.includes("fixture"))).toBeFalsy();
  expect(requests).toContain("/owner-api/v1/bootstrap");
  expect(requests).toContain("/owner-api/v1/overview");
});

test("stale protection can never appear healthy", async ({ page, request }) => {
  await state(request, { scenario: "stale" });
  await login(page);
  const rows = page.locator(".bottom-grid table tbody tr");
  await expect(rows).toHaveCount(2);
  for (const row of await rows.all()) {
    await expect(row.locator(".badge").first()).toHaveText("STALE");
    await expect(row).toContainText("Last reported: VERIFIED");
    await expect(row.locator(".badge.mint")).toHaveCount(0);
  }
  const visibility = page.locator(".status-row", {
    hasText: "Protection verification",
  });
  await expect(visibility.locator(".badge")).toHaveText("STALE");
  await expect(visibility.locator(".badge.mint")).toHaveCount(0);
  await expect(
    page.locator(".status-row", { hasText: "Kernel heartbeat" }),
  ).toContainText("STALE");
  await expect(page.getByText(/STALE — last reported/)).toBeVisible();
});

test("missing data is shown missing, not zero", async ({ page, request }) => {
  await state(request, { scenario: "missing" });
  await login(page);
  await expect(page.getByTestId("missing-sections")).toContainText(
    "account (no_equity_records)",
  );
  await expect(page.getByTestId("control-state")).toHaveText("UNAVAILABLE");
  await expect(page.locator(".stat-value").first()).toHaveText("UNAVAILABLE");
  await expect(page.getByText("Equity history unavailable")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(FIXTURE_TEXT);
});

test("LIVE backend failure cannot fall back to DEMO fixtures", async ({
  page,
  request,
}) => {
  await state(request, {
    fail: ["/owner-api/v1/overview", "/owner-api/v1/knowledge"],
  });
  await login(page);
  await expect(page.getByRole("alert")).toContainText(
    "Backend error 500 (injected_failure)",
  );
  await expect(page.locator(".stat-value")).toHaveCount(0);
  await page.goto(LIVE + "/#knowledge");
  await expect(page.getByRole("alert")).toContainText("No substitute data");
  await expect(page.locator(".react-flow__node")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(FIXTURE_TEXT);
  // the served LIVE bundle carries no fixture module at all
  const html = await (
    await request.get(LIVE + "/", { headers: H })
  ).text();
  const scripts = [...html.matchAll(/src="([^"]+\.js)"/g)].map((m) => m[1]);
  for (const s of scripts) {
    const js = await (await request.get(LIVE + s, { headers: H })).text();
    expect(js).not.toContain("Synthetic preview");
    expect(js).not.toContain("DEMO fixture reply");
  }
});

test("unreachable backend shows no data, then reconnects", async ({ page }) => {
  await page.route("**/owner-api/v1/bootstrap", (r) =>
    r.abort("connectionrefused"),
  );
  await page.goto(LIVE + "/");
  await page.getByLabel("Dashboard password").fill("fixture-token");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("unreachable")).toContainText(
    "No data is shown",
  );
  await expect(page.locator(".stat-value")).toHaveCount(0);
  await page.unroute("**/owner-api/v1/bootstrap");
  await page.getByRole("button", { name: "Retry now" }).click();
  await expect(page.getByTestId("control-state")).toHaveText("FROZEN");
});

test("guarded Resume goes through the Owner Interface", async ({
  page,
  request,
}) => {
  const posts: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST") {
      const path = new URL(r.url()).pathname;
      if (
        path === "/graphql" &&
        r.postDataJSON()?.query?.includes("decisions(")
      )
        return;
      posts.push(path);
    }
  });
  await login(page);
  await page.goto(LIVE + "/#operations");
  await expect(page.getByTestId("owner-interface-status")).toContainText(
    "control state FROZEN",
  );
  await page.locator('button[data-op="resume"]').click();
  await expect(page.getByRole("dialog")).toContainText("guarded recovery");
  await page.getByRole("button", { name: /Send Resume/ }).click();
  const outcome = page.getByTestId("control-outcome");
  await expect(outcome).toContainText("resume: CONTAINED");
  await expect(outcome).toContainText("owner_resume_required");
  await expect(outcome).toContainText("FROZEN → FROZEN");
  const c = await calls(request);
  const ops = c.gateway.map((g: { operation: string }) => g.operation);
  expect(ops.filter((o: string) => o === "resume")).toHaveLength(1);
  expect(c.control_state).toBe("FROZEN");
  expect(
    posts.filter((p) => p !== "/auth/login").every((p) => p === "/graphql"),
  ).toBeTruthy();
  // definitive answer: the request id is released
  expect(
    await page.evaluate(() =>
      Object.keys(localStorage).filter((k) => k.startsWith("luffy.owner.req.")),
    ),
  ).toEqual([]);
});

test("Owner Interface unavailable: controls fail closed", async ({
  page,
  request,
}) => {
  await state(request, { gateway: "down" });
  await login(page);
  await page.goto(LIVE + "/#operations");
  await expect(page.getByTestId("owner-interface-status")).toContainText(
    "kernel_unavailable",
  );
  await expect(page.getByTestId("controls-closed")).toBeVisible();
  for (const op of ["freeze", "halt", "resume", "unhalt", "panic"])
    await expect(page.locator(`button[data-op="${op}"]`)).toBeDisabled();
  const c = await calls(request);
  expect(
    c.gateway.every((g: { operation: string }) => g.operation === "health"),
  ).toBeTruthy();
});

test("unknown outcome keeps the id; retry re-sends the same request", async ({
  page,
  request,
}) => {
  await login(page);
  await page.goto(LIVE + "/#operations");
  await expect(page.getByTestId("owner-interface-status")).toContainText(
    "control state",
  );
  await state(request, { gateway: "unknown_once" });
  await page.locator('button[data-op="halt"]').click();
  await page.getByRole("button", { name: /Send Halt/ }).click();
  await expect(page.getByTestId("control-outcome")).toContainText(
    "OUTCOME_UNKNOWN",
  );
  await expect(page.locator('button[data-op="halt"]')).toHaveText("Retry Halt");
  await page.locator('button[data-op="halt"]').click(); // no new consent for the same action
  await expect(page.getByTestId("control-outcome")).toContainText(
    "halt: ACCEPTED",
  );
  const halts = (await calls(request)).gateway.filter(
    (g: { operation: string }) => g.operation === "halt",
  );
  expect(halts).toHaveLength(2);
  expect(halts[0].request_id).toBe(halts[1].request_id);
  expect(halts[0].issued_at_ms).toBe(halts[1].issued_at_ms);
});

test("chat cannot mutate controls", async ({ page, request }) => {
  const posts: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST") {
      const path = new URL(r.url()).pathname;
      if (
        path === "/graphql" &&
        r.postDataJSON()?.query?.includes("decisions(")
      )
        return;
      posts.push(path);
    }
  });
  await login(page);
  await page.goto(LIVE + "/#luffy");
  for (const text of [
    "freeze everything now",
    "/panic",
    "resume trading please",
  ]) {
    await page.getByLabel("Message LUFFY").fill(text);
    await page.getByRole("button", { name: "Send" }).click();
    await expect(
      page.getByText(`Fixture backend reply to: ${text}`),
    ).toBeVisible();
  }
  const c = await calls(request);
  expect(c.gateway).toEqual([]);
  expect(c.control_state).toBe("FROZEN");
  expect(posts.filter((p) => p !== "/auth/login")).toEqual([
    "/owner-api/v1/chat",
    "/owner-api/v1/chat",
    "/owner-api/v1/chat",
  ]);
  // no evidence ids are invented
  await expect(
    page.getByRole("button", { name: /Inspect evidence/ }),
  ).toHaveCount(0);
});

test("chat cancellation and failure keep the transcript honest", async ({
  page,
  request,
}) => {
  await login(page);
  await page.goto(LIVE + "/#luffy");
  await state(request, { chat: "slow" });
  await page.getByLabel("Message LUFFY").fill("slow question");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText("Waiting for the Luffy backend…")).toBeVisible();
  await page.getByRole("button", { name: "Cancel request" }).click();
  await expect(
    page.getByText(/Cancelled — no reply · Cancelled by owner/),
  ).toBeVisible();
  await state(request, { chat: "fallback" });
  await page.getByLabel("Message LUFFY").fill("second question");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByRole("alert")).toContainText("llm_unavailable");
  await expect(page.getByText("Failed — no reply")).toBeVisible();
  await expect(page.getByText("slow question")).toBeVisible();
});

test("session expiry and logout clear private data", async ({
  page,
  context,
}) => {
  await login(page);
  await page.goto(LIVE + "/#luffy");
  await page.getByLabel("Message LUFFY").fill("private question");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(
    page.getByText("Fixture backend reply to: private question"),
  ).toBeVisible();
  await context.clearCookies(); // server-side: session gone → 401
  await page.reload();
  // a full navigation to a guarded URL lands on the login page, not cached data
  await expect(page.getByLabel("Dashboard password")).toBeVisible();
  await expect(page.locator("body")).not.toContainText("private question");
  // in-app expiry (401 on a data call while the app is open)
  await login(page);
  await page.goto(LIVE + "/#luffy");
  await page.getByLabel("Message LUFFY").fill("second private question");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(
    page.getByText("Fixture backend reply to: second private question"),
  ).toBeVisible();
  await context.clearCookies();
  await page.evaluate(() => (location.hash = "#trades"));
  await expect(page.getByTestId("signed-out")).toBeVisible();
  await expect(page.locator("body")).not.toContainText(
    "second private question",
  );
  await expect(page.locator("body")).not.toContainText("USDT");
  expect(
    await page.evaluate(() =>
      Object.keys(localStorage).concat(Object.keys(sessionStorage)),
    ),
  ).toEqual([]);
  // explicit logout
  await login(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByTestId("signed-out")).toContainText("You signed out");
  const r = await page.request.get(LIVE + "/owner-api/v1/overview");
  expect(r.status()).toBe(401);
});

test("session expiry by time clears the page before any 401", async ({
  page,
  request,
}) => {
  await state(request, { ttl: 6 });
  await login(page);
  await expect(page.getByTestId("control-state")).toHaveText("FROZEN");
  await expect(page.getByTestId("signed-out")).toContainText(
    "session expired",
    {
      timeout: 12000,
    },
  );
  await expect(page.locator("body")).not.toContainText("FROZEN");
});

test("Knowledge binds vault relations and lists what it cannot draw", async ({
  page,
}) => {
  await login(page);
  await page.goto(LIVE + "/#knowledge");
  await expect(page.locator(".react-flow__node")).toHaveCount(4);
  await expect(page.getByRole("button", { name: /^Evidence/ })).toBeEnabled();
  await expect(page.getByRole("button", { name: /^Code/ })).toBeEnabled();
  const undrawn = page.getByTestId("undrawn");
  await expect(undrawn).toContainText("2 connections not drawn — see list");
  await undrawn.locator("summary").click();
  await expect(undrawn).toContainText("Missing Concept");
  await expect(undrawn).toContainText("Nowhere Note");
  await expect(undrawn).toContainText("endpoint record not supplied");
  await page.getByRole("button", { name: "Inspect Aggressor Flow" }).click();
  await expect(page.locator(".inspector")).toContainText(
    "knowledge/10 Theories/Mechanisms/Aggressor Flow.md",
  );
  await expect(page.locator(".inspector")).toContainText("File modified");
  await expect(page.locator(".inspector")).toContainText(
    "Unavailable reference: unresolved:Missing Concept",
  );
});

test("Live System separates architecture, telemetry, stale and missing", async ({
  page,
  request,
}) => {
  await login(page);
  await page.goto(LIVE + "/#live-system");
  await expect(page.locator(".react-flow__node")).toHaveCount(11);
  const node = (name: string) =>
    page.locator(".react-flow__node", { hasText: name });
  await expect(node("Kernel cycle")).toContainText("UNKNOWN");
  await expect(node("Risk")).toContainText("NO TELEMETRY");
  await expect(node("Orchestrator")).toContainText("UNKNOWN");
  await expect(page.locator(".react-flow__edge.animated")).toHaveCount(0);
  await expect(
    page.getByText(/No component activity event stream exists/),
  ).toBeVisible();
  const drawn = await page.locator(".react-flow__edge").count();
  const undrawn = page.getByTestId("undrawn");
  expect(
    drawn +
      (drawn < 11
        ? Number((await undrawn.textContent())?.match(/\d+/)?.[0])
        : 0),
  ).toBe(11);
  await state(request, { scenario: "stale" });
  await page.reload();
  await expect(node("Kernel cycle")).toContainText("STALE");
  await expect(node("Kernel cycle")).toContainText("Last reported: UNKNOWN");
  await expect(node("Supervisor")).toContainText("STALE");
  await expect(page.locator(".system-node.status-active")).toHaveCount(2); // dashboard and the independently polled Owner Interface
});

test("other routes: real contracts or explicit unavailability", async ({
  page,
}) => {
  await login(page);
  await page.goto(LIVE + "/#trades");
  await expect(page.locator("table").first()).toContainText("SOL/USDT");
  await expect(page.getByText("Journal records · partial")).toBeVisible();
  await page.goto(LIVE + "/#strategies");
  await expect(page.locator("table")).toContainText("Donchian");
  await page.goto(LIVE + "/#diagnostics");
  await expect(page.locator(".log-tail")).toContainText("line two");
  await page.goto(LIVE + "/#research");
  await expect(page.getByTestId("research-unavailable")).toContainText(
    "no persisted research-question store",
  );
  await page.getByRole("button", { name: /^Results/ }).click();
  await expect(page.getByText("donchian_hi(100)")).toBeVisible();
});

test("LIVE accessibility: axe on primary LIVE surfaces", async ({ page }) => {
  await login(page);
  const report: Record<string, unknown> = {};
  for (const route of [
    "overview",
    "operations",
    "luffy",
    "knowledge",
    "live-system",
    "trades",
  ]) {
    await page.goto(LIVE + `/#${route}`);
    await page.waitForLoadState("networkidle");
    await page.waitForTimeout(300);
    const r = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    report[route] = r.violations.map((v) => ({
      id: v.id,
      nodes: v.nodes.length,
    }));
    expect(r.violations, route).toEqual([]);
  }
});
