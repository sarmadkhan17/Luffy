import { readFileSync, readdirSync } from "node:fs";
import { test, expect } from "@playwright/test";
import { login, state, LIVE } from "./liveHelpers";

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
for (const activation of ["click", "Enter", "Space"])
  test(`V2.1 cancel with draft never submits via ${activation}`, async ({
    page,
  }) => {
    let submissions = 0;
    await page.route("**/owner-api/v1/chat", async (route) => {
      submissions++;
      // Keep response pending deterministically, independent of LLM/server timing.
      await new Promise<void>((resolve) => page.once("close", () => resolve()));
      await route.abort().catch(() => {});
    });
    await login(page);
    await page.getByRole("link", { name: "LUFFY", exact: true }).click();
    const composer = page.getByLabel("Message LUFFY");
    await composer.fill("first request");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect.poll(() => submissions).toBe(1);
    await composer.fill("unsent draft must remain");
    const cancel = page.getByRole("button", { name: "Cancel request" });
    if (activation === "click") await cancel.click();
    else {
      await cancel.focus();
      await cancel.press(activation);
    }
    await expect(
      page.getByText(/Cancelled — no reply · Cancelled by owner/),
    ).toBeVisible();
    await expect(composer).toHaveValue("unsent draft must remain");
    await expect(
      page.getByRole("button", { name: "Send", exact: true }),
    ).toBeVisible();
    expect(submissions).toBe(1);
    await expect(page.locator(".transcript")).not.toContainText(
      "unsent draft must remain",
    );
  });

test("V2.1 all mobile routes are visible without horizontal scrolling and keyboard reachable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  const nav = page.getByRole("navigation");
  await expect(nav.getByRole("link")).toHaveCount(9);
  for (const link of await nav.getByRole("link").all()) {
    const box = await link.boundingBox();
    expect(
      box &&
        box.x >= 0 &&
        box.x + box.width <= 390 &&
        box.y + box.height <= 844,
    ).toBeTruthy();
    await link.focus();
    await link.press("Enter");
    await expect(link).toHaveAttribute("aria-current", "page");
  }
  await expect(nav.getByText("Controls · Panic")).toBeVisible();
});

test("V2.1 topology uses real Owner Interface health and distinguishes absent telemetry", async ({
  page,
  request,
}) => {
  await login(page);
  await page.getByRole("link", { name: "Live System", exact: true }).click();
  const risk = page.getByRole("button", { name: "Inspect Risk", exact: true });
  await expect(risk).toContainText("NO TELEMETRY");
  await expect(risk).not.toContainText("UNAVAILABLE");
  await expect(
    page.getByRole("button", { name: "Inspect Owner Interface", exact: true }),
  ).toContainText("AVAILABLE");
  await state(request, { gateway: "down" });
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Inspect Owner Interface", exact: true }),
  ).toContainText("UNAVAILABLE");
});

test("V2.1 knowledge shows modification age without asserting freshness; legacy fallback is explicit", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("link", { name: "Knowledge", exact: true }).click();
  await page
    .getByRole("button", { name: "Inspect Aggressor Flow", exact: true })
    .click();
  await expect(page.getByText(/freshness not assessed/).first()).toBeVisible();
  await expect(
    page.locator(".react-flow__edge-text").filter({ hasText: "wikilink" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(page.locator(".graph-list time").first()).toContainText(" UTC");
  await expect(page.locator(".graph-list time").first()).not.toContainText(
    "+00:00",
  );
  await page.getByRole("link", { name: "Research", exact: true }).click();
  await expect(page.locator('a[href="/legacy/"]')).toBeVisible();
  await expect(page.locator(".research-directory button")).toHaveCount(10);
  await page.locator(".research-directory button").last().click();
  await expect(page.locator(".research-workspace")).toContainText(
    "Unavailable",
  );
});

test("V2.1 open trade truth, bounded trade history and UTC presentation", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("link", { name: "Trades", exact: true }).click();
  await expect(page.getByText("Realized so far ·").first()).toBeVisible();
  await expect(page.getByTestId("trade-range")).toContainText(
    "Records 1–3 of 3 trades",
  );
  await expect(page.locator("table").first()).toContainText(" UTC");
  await expect(page.locator("table").first()).not.toContainText("+00:00");
  await page
    .locator("table")
    .first()
    .getByRole("button", { name: "Inspect BTC/USDT", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("Realized so far");
});

for (const [amount, cls] of [
  [-25, "rose"],
  [0, "neutral"],
  [25, "mint"],
] as const)
  test(`V2.1 realized today ${amount} has sign-aware styling`, async ({
    page,
  }) => {
    await page.route("**/owner-api/v1/overview", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.realized_today.value = amount;
      await route.fulfill({ response, json: body });
    });
    await login(page);
    await expect(page.getByTestId("realized-today")).toHaveClass(
      new RegExp(cls),
    );
    await expect(page.getByText(/News guard: unavailable/)).toBeVisible();
  });

test("V2.1 login preserves requested slash hash safely", async ({ page }) => {
  await page.goto(LIVE + "/owner-preview/#/knowledge");
  await page.getByLabel("Dashboard password").fill("fixture-token");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(
    page.getByRole("heading", { name: "Knowledge", level: 1, exact: true }),
  ).toBeVisible();
  expect(new URL(page.url()).hash).toBe("#/knowledge");
  await expect(page.getByTestId("mode-banner")).toContainText("LIVE");
  await expect(page.getByTestId("mode-banner")).not.toContainText(
    "DEMO — synthetic",
  );
});

test("V2.1 Decisions binds offset paging with lookahead and never claims a frozen complete history", async ({
  page,
}) => {
  const offsets: number[] = [];
  await page.route("**/graphql", async (route) => {
    const body = route.request().postDataJSON();
    if (!body.query?.includes("decisions(")) return route.continue();
    const offset = body.variables.offset;
    offsets.push(offset);
    await route.fulfill({
      json: {
        data: {
          decisions: Array.from({ length: offset ? 1 : 101 }, (_, i) => ({
            id: String(offset + i),
            symbol: `PAIR${offset + i}`,
            ts: "2026-09-29T00:00:00Z",
            action: "HOLD",
            executed: false,
            skip_reason: "recorded",
          })),
        },
      },
    });
  });
  await login(page);
  await page.getByRole("link", { name: "Trades", exact: true }).click();
  const panel = page.locator("section").filter({
    has: page.getByRole("heading", { name: "Decision journal", exact: true }),
  });
  await panel.getByRole("button", { name: "Next" }).click();
  await expect(panel).toContainText("PAIR100");
  await expect(panel.getByRole("button", { name: "Next" })).toBeDisabled();
  expect(offsets).toContain(100);
  await panel.getByRole("button", { name: "Previous" }).click();
  await expect(panel).toContainText("PAIR0");
});

test("V2.1 stale exposure, partial protection and LIVE failures remain truthful", async ({
  page,
  request,
}) => {
  await page.route("**/owner-api/v1/overview", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.protection.status = "VERIFIED";
    body.protection.snapshot.venue_positions = false;
    for (const p of body.positions) p.protection.status = "VERIFIED";
    await route.fulfill({ response, json: body });
  });
  await login(page);
  await expect(
    page.locator(".status-row").filter({ hasText: "Protection verification" }),
  ).toContainText("PARTIAL");
  await page.unroute("**/owner-api/v1/overview");
  await state(request, { scenario: "stale" });
  await page.reload();
  const exposure = page.locator("section").filter({
    has: page.getByRole("heading", { name: "Gross exposure", exact: true }),
  });
  await expect(exposure).toContainText("STALE");
  await state(request, { fail: ["/owner-api/v1/overview"] });
  await page.reload();
  await expect(page.getByText("Data unavailable")).toBeVisible();
  await expect(page.getByTestId("mode-banner")).toContainText("LIVE");
  await expect(page.locator("body")).not.toContainText("synthetic data");
});

test("V2.1 visual corrections and diagnostic availability match the health contract", async ({
  page,
}) => {
  await login(page);
  expect(
    await page
      .locator(".page-heading")
      .evaluate((el) => getComputedStyle(el, "::after").content),
  ).toBe("none");
  await page.getByRole("link", { name: "Diagnostics", exact: true }).click();
  const panel = page.locator("section").filter({
    has: page.getByRole("heading", { name: "Owner Interface", exact: true }),
  });
  await expect(
    panel.locator(".badge").filter({ hasText: /^AVAILABLE$/ }),
  ).toHaveClass(/mint/);
  await page.getByRole("link", { name: "Strategies", exact: true }).click();
  await page
    .getByRole("button", { name: "Inspect Donchian", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("Registry ID");
  await expect(page.getByRole("dialog")).toContainText("Recorded lifecycle");
  await page.getByLabel("Close record").click();
  await page.getByRole("link", { name: "LUFFY", exact: true }).click();
  const desktop = await page.locator(".conversation").boundingBox();
  expect(desktop!.height).toBeGreaterThanOrEqual(720);
  await page.setViewportSize({ width: 390, height: 844 });
  const core = await page.locator(".mobile-core").boundingBox();
  expect(core!.width).toBeGreaterThanOrEqual(80);
  for (const badge of await page
    .locator(".badge:visible,.eyebrow:visible")
    .all())
    expect(
      await badge.evaluate((el) => parseFloat(getComputedStyle(el).fontSize)),
    ).toBeGreaterThanOrEqual(11);
});

test("V2.1 Owner Interface telemetry cannot turn stale or invalid observation times into healthy state", async ({
  page,
}) => {
  let observed = "2000-01-01T00:00:00Z";
  await page.route("**/owner-api/v1/owner-interface", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.observed_at = observed;
    await route.fulfill({ response, json: body });
  });
  await login(page);
  await page.getByRole("link", { name: "Live System", exact: true }).click();
  const node = page.getByRole("button", {
    name: "Inspect Owner Interface",
    exact: true,
  });
  await expect(node).toContainText("STALE");
  await expect(node).toContainText("Last reported: AVAILABLE");
  observed = "invalid";
  await page.reload();
  await expect(node).toContainText("UNAVAILABLE");
});

test("V2.1 reauthentication retains the current route and Operations uses legacy path", async ({
  page,
}) => {
  await login(page);
  await page.getByRole("link", { name: "Operations", exact: true }).click();
  await expect(
    page.getByRole("link", { name: "Open legacy dashboard ↗" }),
  ).toHaveAttribute("href", "/legacy/");
  await page.getByRole("link", { name: "Knowledge", exact: true }).click();
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByLabel("Dashboard password").fill("fixture-token");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Knowledge", level: 1, exact: true }),
  ).toBeVisible();
});

test("V2.1 LIVE build excludes fixtures, dead preview labels and sourcemaps", () => {
  const files = readdirSync("dist/assets");
  expect(files.filter((f) => f.endsWith(".map"))).toEqual([]);
  const code = files
    .filter((f) => f.endsWith(".js"))
    .map((f) => readFileSync(`dist/assets/${f}`, "utf8"))
    .join("\n");
  for (const forbidden of [
    "DEMO — synthetic",
    "DEMO mode",
    "Fixed fixture snapshot",
    "Synthetic evidence",
    "Market observation",
    "Liquidity hypothesis",
    "Strategy version",
  ])
    expect(code).not.toContain(forbidden);
});
