import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { writeFileSync } from "node:fs";
const routes = [
  "Overview",
  "Trades",
  "Research",
  "Strategies",
  "LUFFY",
  "Operations",
  "Live System",
  "Knowledge",
  "Diagnostics",
];
test.beforeEach(async ({ page }) => {
  await page.route("**/*", (route) => {
    const u = new URL(route.request().url());
    return u.hostname === "127.0.0.1" ? route.continue() : route.abort();
  });
});
test("navigation, deep links, lazy loading and all four actual screens", async ({
  page,
}) => {
  const errors: string[] = [];
  const requests: string[] = [];
  page.on("request", (req) => requests.push(req.url()));
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.getByText("$102,480.50").first()).toBeVisible();
  await expect(page.locator("canvas").first()).toBeVisible();
  await expect(page.getByRole("navigation").getByRole("link")).toHaveText(
    routes,
    { useInnerText: true },
  );
  expect(
    await page.evaluate(() =>
      performance
        .getEntriesByType("resource")
        .map((r) => r.name)
        .filter((n) => /GraphView|Avatar/.test(n)),
    ),
  ).toEqual([]);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "evidence/overview.png", fullPage: true });
  for (const [route, slug] of [
    ["LUFFY", "luffy"],
    ["Knowledge", "knowledge"],
    ["Live System", "live-system"],
  ]) {
    await page
      .getByRole("navigation")
      .getByRole("link", { name: route, exact: true })
      .click();
    if (slug === "luffy")
      await expect(
        page.getByRole("img", { name: /mechanical core/ }),
      ).toBeVisible();
    else await expect(page.locator(".react-flow__node").first()).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: `evidence/${slug}.png`, fullPage: true });
  }
  for (const name of [
    "Trades",
    "Research",
    "Strategies",
    "Operations",
    "Diagnostics",
  ]) {
    await page
      .getByRole("navigation")
      .getByRole("link", { name, exact: true })
      .click();
    await expect(
      page.getByText("Not implemented in this preview."),
    ).toBeVisible();
  }
  await page.goto("/#knowledge");
  await expect(page.locator(".react-flow__node")).toHaveCount(11);
  expect(errors).toEqual([]);
  expect(
    requests.every((url) => {
      const u = new URL(url);
      return (
        u.origin === "http://127.0.0.1:4174" &&
        (u.pathname === "/" || u.pathname.startsWith("/assets/"))
      );
    }),
  ).toBe(true);
});
test("chat streams, inspects evidence, cancels, handles errors and cannot execute commands", async ({
  page,
}) => {
  await page.goto("/#luffy");
  const input = page.getByLabel("Message LUFFY");
  await input.fill("Explain sample research");
  await input.press("Enter");
  await expect(
    page.getByRole("button", { name: "Cancel request" }),
  ).toBeVisible();
  await expect(page.locator(".mechanical-avatar")).toHaveAttribute(
    "data-state",
    "pending",
  );
  await expect(page.getByText(/DEMO fixture reply/)).toBeVisible();
  await page.getByRole("button", { name: /Inspect evidence/ }).click();
  await expect(page.getByRole("dialog")).toContainText("context_only");
  await page.keyboard.press("Escape");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "evidence/luffy.png", fullPage: true });
  await input.fill("Please resume trading");
  await input.press("Enter");
  await expect(
    page.getByText(/This preview cannot execute commands/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send", exact: true }),
  ).toBeVisible();
  await input.fill("Cancel message");
  await input.press("Enter");
  await page.getByRole("button", { name: "Cancel request" }).click();
  await expect(page.getByText(/Request cancelled/)).toBeVisible();
  await page.getByLabel("Fixture scenario").selectOption("error");
  await input.fill("Failure example");
  await input.press("Enter");
  await expect(page.getByRole("alert")).toContainText("No substitute");
  await page.getByLabel("Fixture scenario").selectOption("normal");
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Overview", exact: true })
    .click();
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "LUFFY", exact: true })
    .click();
  await expect(
    page.getByText("Failure example", { exact: true }),
  ).toBeVisible();
});
test("graph search, keyboard selection, zoom/pan, neighbours, lenses and list inspector", async ({
  page,
}) => {
  await page.goto("/#knowledge");
  await expect(page.locator(".react-flow__node")).toHaveCount(11);
  const viewport = page.locator(".react-flow__viewport");
  const before = await viewport.getAttribute("style");
  await page.getByRole("button", { name: "Zoom In", exact: true }).click();
  await expect(viewport).not.toHaveAttribute("style", before!);
  const pane = page.locator(".react-flow__pane");
  const box = (await pane.boundingBox())!;
  const panBefore = await viewport.getAttribute("style");
  await page.mouse.move(box.x + 50, box.y + 30);
  await page.mouse.down();
  await page.mouse.move(box.x + 100, box.y + 90, { steps: 8 });
  await page.mouse.up();
  await expect(viewport).not.toHaveAttribute("style", panBefore!);
  await page.getByRole("button", { name: "Fit View", exact: true }).click();
  await page
    .getByRole("button", { name: "Inspect Liquidity hypothesis", exact: true })
    .focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("complementary", { name: "Node inspector" }),
  ).toContainText("Liquidity hypothesis");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "evidence/knowledge.png", fullPage: true });
  await page
    .getByRole("button", { name: "Explore neighbours", exact: true })
    .click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page
    .getByRole("button", { name: "Show full lens", exact: true })
    .click();
  await page.getByRole("button", { name: "Code", exact: true }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(2);
  await page.getByRole("button", { name: "Evidence", exact: true }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await page.getByRole("button", { name: "Timeline", exact: true }).click();
  await expect(page.locator(".graph-list button")).toHaveCount(11);
  await expect(page.locator(".graph-list time").first()).toBeVisible();
  await page.getByRole("button", { name: "Show graph" }).click();
  await page.getByLabel("Search nodes").fill("outcome");
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await page.getByRole("button", { name: "List view" }).click();
  await page.getByRole("button", { name: /Outcome record Outcome/ }).click();
  await expect(
    page.getByRole("complementary", { name: "Node inspector" }),
  ).toContainText("fixture:k9");
  await page.getByLabel("Search nodes").fill("no match");
  await expect(
    page.getByText("No matching nodes. Change search or lens."),
  ).toBeVisible();
});
test("system activity requires a supplied event, reduced motion stays static", async ({
  page,
}) => {
  await page.goto("/#live-system");
  await expect(page.locator(".react-flow__node")).toHaveCount(9);
  await page.getByRole("button", { name: "Inspect Risk", exact: true }).click();
  await expect(
    page.getByRole("complementary", { name: "Node inspector" }),
  ).toContainText("fixture:s5");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: "evidence/live-system.png", fullPage: true });
  await expect(page.locator(".react-flow__edge.animated")).toHaveCount(0);
  await page.getByRole("button", { name: "Replay fixture event" }).click();
  await expect(page.locator(".react-flow__edge.animated")).toHaveCount(1);
  await expect(page.locator(".react-flow__edge.animated")).toHaveCount(0, {
    timeout: 4000,
  });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.getByRole("button", { name: "Replay fixture event" }).click();
  await expect(page.getByRole("status")).toContainText("static highlight");
  await expect(page.locator(".react-flow__edge.animated")).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(
        () =>
          document.getAnimations().filter((a) => a.playState === "running")
            .length,
      ),
    )
    .toBe(0);
  await page.getByLabel("Fixture scenario").selectOption("stale");
  await expect(
    page.getByText(
      "No fresh events supplied. Activity animation is unavailable.",
    ),
  ).toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "LUFFY", exact: true })
    .click();
  await page.getByLabel("Message LUFFY").fill("Show research");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".mechanical-avatar")).toHaveAttribute(
    "data-animating",
    "false",
  );
});
test("missing/error states clear old data on all query surfaces", async ({
  page,
}) => {
  for (const route of ["overview", "knowledge", "live-system"]) {
    await page.goto(`/#${route}`);
    await page.getByLabel("Fixture scenario").selectOption("missing");
    if (route === "overview") {
      await expect(page.getByText("Position data unavailable")).toBeVisible();
      await expect(page.getByText("$102,480.50")).toHaveCount(0);
    } else {
      await expect(
        page.getByText("Graph data unavailable. No nodes were substituted."),
      ).toBeVisible();
    }
    await page.getByLabel("Fixture scenario").selectOption("error");
    await expect(page.getByRole("alert")).toContainText("No substitute");
    await expect(page.locator(".react-flow__node")).toHaveCount(0);
  }
});
test("no WebGL needed, mobile layout and keyboard evidence dialog", async ({
  page,
}) => {
  await page.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function (
      this: HTMLCanvasElement,
      type: string,
      ...args: unknown[]
    ) {
      if (type.includes("webgl")) return null;
      return original.apply(this, [type, ...args] as never);
    } as typeof original;
  });
  await page.setViewportSize({ width: 390, height: 844 });
  for (const route of ["overview", "luffy", "knowledge", "live-system"]) {
    await page.goto(`/#${route}`);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    if (route === "overview") {
      await expect(page.getByText("$102,480.50").first()).toBeVisible();
      await expect(page.locator("canvas").first()).toBeVisible();
      await expect(page.getByText("Loading chart…")).toHaveCount(0);
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
    }
    if (route === "luffy")
      await expect(
        page.getByRole("img", { name: /mechanical core/ }),
      ).toBeVisible();
    if (route === "knowledge" || route === "live-system")
      await expect(page.locator(".graph-list button").first()).toBeVisible();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: `evidence/mobile-${route}.png`,
      fullPage: true,
    });
  }
  await page.goto("/#overview");
  const evidence = page.getByRole("button", { name: /Inspect evidence/ });
  await evidence.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(evidence).toBeFocused();
});
test("axe WCAG checks on four rendered surfaces and evidence drawer", async ({
  page,
}) => {
  const report: Record<string, unknown> = {};
  for (const route of ["overview", "luffy", "knowledge", "live-system"]) {
    await page.goto(`/#${route}`);
    if (route === "overview")
      await expect(page.locator("canvas").first()).toBeVisible();
    if (route === "overview") {
      await expect(page.getByText("$102,480.50").first()).toBeVisible();
      await expect(page.locator("canvas").first()).toBeVisible();
      await expect(page.getByText("Loading chart…")).toHaveCount(0);
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
    }
    if (route === "luffy")
      await expect(
        page.getByRole("img", { name: /mechanical core/ }),
      ).toBeVisible();
    if (route === "knowledge" || route === "live-system")
      await expect(page.locator(".react-flow__node").first()).toBeVisible();
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    report[route] = result.violations;
    expect(result.violations, route).toEqual([]);
  }
  await page.goto("/");
  await page.getByRole("button", { name: /Inspect evidence/ }).click();
  const drawer = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  report.drawer = drawer.violations;
  writeFileSync("evidence/accessibility.json", JSON.stringify(report, null, 2));
  expect(drawer.violations).toEqual([]);
});

test("slow avatar and graph chunks cannot block navigation or the composer", async ({
  page,
}) => {
  await page.route("**/assets/Avatar-*.js", async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue();
  });
  await page.route("**/assets/GraphView-*.js", async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue();
  });
  await page.goto("/#luffy");
  await page.getByLabel("Message LUFFY").fill("Composer remains usable");
  await expect(page.getByLabel("Message LUFFY")).toHaveValue(
    "Composer remains usable",
  );
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Knowledge", exact: true })
    .click();
  await expect(
    page.getByText("Loading view… Navigation remains available."),
  ).toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Overview", exact: true })
    .click();
  await expect(page.getByText("$102,480.50").first()).toBeVisible();
});
test("hiding the page stops animation and route exit cancels pending work", async ({
  page,
}) => {
  await page.goto("/#luffy");
  await page.getByLabel("Message LUFFY").fill("Pending fixture");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.locator(".mechanical-avatar")).toHaveAttribute(
    "data-animating",
    "true",
  );
  // Deterministic visibility transition; the real handler and Motion renderer run.
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(page.locator(".mechanical-avatar")).toHaveAttribute(
    "data-animating",
    "false",
  );
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Overview", exact: true })
    .click();
  await expect(page.locator(".mechanical-avatar")).toHaveCount(0);
  await page.waitForTimeout(1200);
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "LUFFY", exact: true })
    .click();
  await expect(page.getByText(/DEMO fixture reply/)).toHaveCount(0);
});
