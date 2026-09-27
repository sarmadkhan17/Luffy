import { test, expect, type Page } from "@playwright/test";
import { writeFileSync } from "node:fs";
const harness = "http://127.0.0.1:4175/tests/harness/index.html";
async function topology(page: Page) {
  return page.evaluate(() => {
    const nodeElements = Array.from(
      document.querySelectorAll<HTMLElement>(".react-flow__node"),
    );
    const boxes = nodeElements.map((el) => ({
      id: el.dataset.id!,
      rect: el.getBoundingClientRect(),
    }));
    const segments: {
      edge: string;
      source?: string;
      target?: string;
      a: number[];
      b: number[];
    }[] = [];
    const renderedPaths: Record<string, string> = {};
    const overlaps: string[] = [];
    const closeRuns: string[] = [],
      portApproaches: string[] = [];
    const hits: string[] = [],
      directions: string[] = [],
      markers: string[] = [];
    for (const group of document.querySelectorAll<SVGGElement>(
      ".react-flow__edge",
    )) {
      const path = group.querySelector<SVGPathElement>(
        ".react-flow__edge-path",
      )!;
      renderedPaths[group.dataset.id!] = path.getAttribute("d")!;
      const matrix = path.getScreenCTM()!;
      const screen = (length: number) => {
        const p = path.getPointAtLength(length);
        return new DOMPoint(p.x, p.y).matrixTransform(matrix);
      };
      const inverse = matrix.inverse();
      const unrelatedPorts = boxes.flatMap((box) =>
        [
          new DOMPoint(box.rect.left, (box.rect.top + box.rect.bottom) / 2),
          new DOMPoint(box.rect.right, (box.rect.top + box.rect.bottom) / 2),
        ].map((p) => ({ id: box.id, point: p.matrixTransform(inverse) })),
      );
      const length = path.getTotalLength(),
        first = screen(0),
        last = screen(length);
      // Match the rendered endpoints geometrically, independently of routing data.
      const source = boxes.find(
        (b) =>
          Math.abs(first.x - b.rect.right) < 3 &&
          first.y >= b.rect.top &&
          first.y <= b.rect.bottom,
      );
      const target = boxes.find(
        (b) =>
          Math.abs(last.x - b.rect.left) < 3 &&
          last.y >= b.rect.top &&
          last.y <= b.rect.bottom,
      );
      directions.push(`${source?.id}->${target?.id}`);
      const coordinates = (
        path.getAttribute("d")!.match(/-?\d+(?:\.\d+)?/g) ?? []
      ).map(Number);
      for (let i = 2; i < coordinates.length; i += 2)
        segments.push({
          edge: group.dataset.id!,
          source: source?.id,
          target: target?.id,
          a: coordinates.slice(i - 2, i),
          b: coordinates.slice(i, i + 2),
        });

      for (const port of unrelatedPorts) {
        if (port.id === source?.id || port.id === target?.id) continue;
        for (let i = 2; i < coordinates.length; i += 2) {
          const [ax, ay, bx, by] = coordinates.slice(i - 2, i + 2);
          const dx = bx - ax,
            dy = by - ay;
          const fraction = Math.max(
            0,
            Math.min(
              1,
              ((port.point.x - ax) * dx + (port.point.y - ay) * dy) /
                (dx * dx + dy * dy || 1),
            ),
          );
          if (
            Math.hypot(
              ax + fraction * dx - port.point.x,
              ay + fraction * dy - port.point.y,
            ) < 47.9
          ) {
            portApproaches.push(`${group.dataset.id}:${port.id}`);
            break;
          }
        }
      }
      if (!path.getAttribute("marker-end"))
        markers.push(group.dataset.id ?? "missing");
      for (let n = 0; n <= length; n += 2) {
        const p = screen(n);
        for (const b of boxes) {
          if (
            p.x > b.rect.left + 1 &&
            p.x < b.rect.right - 1 &&
            p.y > b.rect.top + 1 &&
            p.y < b.rect.bottom - 1
          )
            hits.push(`${group.dataset.id}:${b.id}`);
        }
      }
    }
    for (let i = 0; i < segments.length; i++)
      for (let j = i + 1; j < segments.length; j++) {
        const a = segments[i],
          b = segments[j];
        if (a.edge === b.edge) continue;
        for (const axis of [0, 1]) {
          const other = 1 - axis;
          if (
            Math.abs(a.a[axis] - a.b[axis]) < 0.01 &&
            Math.abs(b.a[axis] - b.b[axis]) < 0.01 &&
            Math.abs(a.a[axis] - b.a[axis]) < 35.9
          ) {
            const length =
              Math.min(
                Math.max(a.a[other], a.b[other]),
                Math.max(b.a[other], b.b[other]),
              ) -
              Math.max(
                Math.min(a.a[other], a.b[other]),
                Math.min(b.a[other], b.b[other]),
              );
            if (length > 1) {
              const commonPorts = segments.filter((s) => s.edge === a.edge);
              const ports = [
                ...(a.source === b.source ? [commonPorts[0].a] : []),
                ...(a.target === b.target ? [commonPorts.at(-1)!.b] : []),
              ];
              const overlapEnds = [
                Math.max(
                  Math.min(a.a[other], a.b[other]),
                  Math.min(b.a[other], b.b[other]),
                ),
                Math.min(
                  Math.max(a.a[other], a.b[other]),
                  Math.max(b.a[other], b.b[other]),
                ),
              ];
              // Fan-in/fan-out can meet within 72 flow units of its common
              // port; remote shared tracks are checked even with shared nodes.
              if (
                ports.some(
                  (p) =>
                    overlapEnds.every((v) => Math.abs(v - p[other]) <= 72.1) &&
                    [a.a[axis], b.a[axis]].every(
                      (v) => Math.abs(v - p[axis]) <= 72.1,
                    ),
                )
              )
                continue;
              closeRuns.push(`${a.edge}:${b.edge}`);
              if (Math.abs(a.a[axis] - b.a[axis]) < 0.01)
                overlaps.push(`${a.edge}:${b.edge}`);
            }
          }
        }
      }
    return {
      renderedPaths,
      hits: [...new Set(hits)],
      directions,
      markers,
      overlaps: [...new Set(overlaps)],
      closeRuns: [...new Set(closeRuns)],
      portApproaches: [...new Set(portApproaches)],
    };
  });
}
test("F1 spacing, unrelated ports, exact directions, filtering and 40-node fan-in/back-edges", async ({
  page,
  browser,
}) => {
  await page.goto("/#live-system");
  await expect(page.locator(".react-flow__edge")).toHaveCount(9);
  const result = await topology(page);
  expect(result.hits).toEqual([]);
  expect(result.closeRuns).toEqual([]);
  expect(result.portApproaches).toEqual([]);
  expect(result.overlaps).toEqual([]);
  expect(result.markers).toEqual([]);
  expect(result.directions.sort()).toEqual(
    [
      "s0->s1",
      "s1->s2",
      "s2->s3",
      "s4->s3",
      "s3->s5",
      "s5->s6",
      "s6->s7",
      "s7->s8",
      "s8->s4",
    ].sort(),
  );
  // Mutation negative control: the exact independently reported paths must fail
  // the new checker even though they do not enter an unrelated node rectangle.
  const original = await page.evaluate(() => {
    const ids = ["se3", "se7"];
    const paths = ids.map((id) =>
      document.querySelector<SVGPathElement>(
        `.react-flow__edge[data-id="${id}"] .react-flow__edge-path`,
      )!,
    );
    const original = paths.map((path) => path.getAttribute("d")!);
    paths[0].setAttribute(
      "d",
      "M 210 556 L 232.5 556 L 232.5 569.8 L 617.5 569.8 L 617.5 477.5 L 937.5 477.5 L 937.5 56 L 960 56",
    );
    paths[1].setAttribute(
      "d",
      "M 850 306 L 878.5 306 L 878.5 390.5 L 611.5 390.5 L 611.5 556 L 640 556",
    );
    return original;
  });
  const knownBad = await topology(page);
  expect(knownBad.closeRuns).toContain("se3:se7");
  expect(knownBad.portApproaches).toContain("se3:s8");
  await page.evaluate((original) => {
    ["se3", "se7"].forEach((id, i) =>
      document
        .querySelector(
          `.react-flow__edge[data-id="${id}"] .react-flow__edge-path`,
        )!
        .setAttribute("d", original[i]),
    );
  }, original);
  await page
    .getByRole("button", { name: "Inspect Analyst", exact: true })
    .click();
  await page.getByRole("button", { name: "Explore neighbours" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(4);
  const filtered = await topology(page);
  expect(filtered.hits).toEqual([]);
  expect(filtered.closeRuns).toEqual([]);
  expect(filtered.portApproaches).toEqual([]);
  expect(filtered.markers).toEqual([]);
  expect(filtered.directions.sort()).toEqual(
    ["s2->s3", "s4->s3", "s3->s5"].sort(),
  );
  await page.goto(harness + "?scale");
  await expect(page.locator(".react-flow__node")).toHaveCount(40);
  await expect(page.locator(".react-flow__edge")).toHaveCount(39);
  const max = await topology(page);
  expect(max.hits).toEqual([]);
  expect(max.closeRuns).toEqual([]);
  expect(max.portApproaches).toEqual([]);
  expect(max.overlaps).toEqual([]);
  expect(max.markers).toEqual([]);
  expect(max.directions.sort()).toEqual(
    Array.from({ length: 39 }, (_, i) => `scale-${i}->scale-${i + 1}`).sort(),
  );
  await page.getByLabel("Search nodes").fill("Sample 00");
  await expect(page.locator(".react-flow__node")).toHaveCount(10);
  const searched = await topology(page);
  expect(searched.hits).toEqual([]);
  expect(searched.closeRuns).toEqual([]);
  expect(searched.portApproaches).toEqual([]);
  expect(searched.markers).toEqual([]);
  expect(searched.directions.sort()).toEqual(
    Array.from({ length: 9 }, (_, i) => `scale-${i}->scale-${i + 1}`).sort(),
  );
  await page.goto(harness + "?scale&branched");
  await expect(page.locator(".react-flow__node")).toHaveCount(40);
  await expect(page.locator(".react-flow__edge")).toHaveCount(44);
  const branched = await topology(page);
  expect(branched.hits).toEqual([]);
  expect(branched.closeRuns).toEqual([]);
  expect(branched.portApproaches).toEqual([]);
  expect(branched.markers).toEqual([]);
  expect(branched.directions.sort()).toEqual(
    [
      ...Array.from({ length: 39 }, (_, i) => `scale-${i}->scale-${i + 1}`),
      "scale-0->scale-2",
      "scale-3->scale-0",
      "scale-5->scale-2",
      "scale-8->scale-4",
      "scale-12->scale-6",
    ].sort(),
  );
  const detail = await browser.newPage({
    viewport: { width: 1440, height: 1080 },
    deviceScaleFactor: 2,
  });
  await detail.goto("http://127.0.0.1:4174/#live-system");
  await expect(detail.locator(".react-flow__edge")).toHaveCount(9);
  await detail.screenshot({
    path: "evidence/live-system-2x.png",
    fullPage: true,
  });
  await detail.close();
  writeFileSync(
    "evidence/topology-regression.json",
    JSON.stringify(
      { default: result, filtered, maximum: max, searched, branched, knownBad },
      null,
      2,
    ),
  );
});
test("F2 stale primary status is consistent on nodes, inspector and list; unknown stays distinct", async ({
  page,
}) => {
  await page.goto("/#live-system");
  await expect(page.locator(".react-flow__node")).toHaveCount(9);
  await expect(page.locator('[data-id="s3"] .health-status strong')).toHaveText(
    "UNAVAILABLE",
  );
  await page.getByLabel("Fixture scenario").selectOption("stale");
  await expect(
    page.locator(".react-flow__node .health-status>strong"),
  ).toHaveText(Array(9).fill("STALE"));
  await expect(
    page.locator(".system-node.status-active,.system-node.status-idle"),
  ).toHaveCount(0);
  await expect(page.locator(".react-flow__edge.animated")).toHaveCount(0);
  await page
    .getByRole("button", { name: "Inspect Market Data", exact: true })
    .click();
  const inspector = page.getByRole("complementary", { name: "Node inspector" });
  await expect(inspector.locator(".health-status strong")).toHaveText("STALE");
  await expect(inspector).toContainText("Last reported: ACTIVE");
  await page.getByRole("button", { name: "List view" }).click();
  await expect(page.locator(".graph-list .health-status>strong")).toHaveText(
    Array(9).fill("STALE"),
  );
  await page.screenshot({ path: "evidence/system-stale.png", fullPage: true });
});
test("cancel markers survive button, scenario, route exit and subsequent messages", async ({
  page,
}) => {
  await page.goto("/#luffy");
  const input = page.getByLabel("Message LUFFY");
  await input.fill("Button abort");
  await input.press("Enter");
  await page.getByRole("button", { name: "Cancel request" }).click();
  await expect(page.locator(".message-outcome")).toContainText(
    "Cancelled — no reply",
  );
  await input.fill("Scenario abort");
  await input.press("Enter");
  await page.getByLabel("Fixture scenario").selectOption("stale");
  await expect(page.locator(".message-outcome")).toHaveCount(2);
  await input.fill("Route abort");
  await input.press("Enter");
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "Overview", exact: true })
    .click();
  await page
    .getByRole("navigation")
    .getByRole("link", { name: "LUFFY", exact: true })
    .click();
  await expect(page.locator(".message-outcome")).toHaveCount(3);
  await input.fill("Following request");
  await input.press("Enter");
  await expect(
    page.getByRole("button", { name: /Inspect evidence/ }),
  ).toBeVisible();
  await expect(page.locator(".message-outcome")).toHaveCount(3);
});
test("streaming respects reading position and jump to latest; dangling refs are unavailable", async ({
  page,
}) => {
  await page.goto(harness + "?long-chat");
  await page.getByRole("button", { name: "chat", exact: true }).click();
  await page.getByLabel("Message LUFFY").fill("Streaming fixture");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.locator(".transcript").evaluate((el) => {
    el.scrollTop = 0;
    el.dispatchEvent(new Event("scroll"));
  });
  await expect(
    page.getByRole("button", { name: "Jump to latest ↓" }),
  ).toBeVisible();
  await expect
    .poll(() => page.locator(".message.luffy").last().textContent())
    .toContain("Fixture streaming text. Fixture streaming text.");
  await expect
    .poll(() => page.locator(".transcript").evaluate((el) => el.scrollTop))
    .toBe(0);
  await page.getByRole("button", { name: "Jump to latest ↓" }).click();
  await expect
    .poll(() =>
      page
        .locator(".transcript")
        .evaluate((el) => el.scrollHeight - el.scrollTop - el.clientHeight),
    )
    .toBeLessThan(64);
  await page.goto(harness + "?dangling");
  await expect(page.locator(".react-flow__node")).toHaveCount(9);
  await page
    .getByRole("button", { name: "Inspect Market Data", exact: true })
    .click();
  await expect(
    page.getByRole("button", {
      name: /Unavailable reference: unavailable-record/,
    }),
  ).toBeDisabled();
  await expect(page.locator(".react-flow__node")).toHaveCount(9);
});
test("500-input scope and search reuse graph, hidden replay cleanup, mobile core and opt-in graph", async ({
  page,
}) => {
  await page.goto(harness + "?scale");
  await page.getByRole("button", { name: "knowledge", exact: true }).click();
  await expect(
    page.getByText("40 of 500 matching nodes", { exact: false }),
  ).toBeVisible();
  await page
    .locator(".react-flow")
    .evaluate((el) => el.setAttribute("data-instance-probe", "retained"));
  await page.getByLabel("Search nodes").fill("Sample 03");
  await expect(page.locator(".react-flow")).toHaveAttribute(
    "data-instance-probe",
    "retained",
  );
  await expect(page.locator(".react-flow__node")).toHaveCount(10);
  await page.goto("/#live-system");
  await page.getByRole("button", { name: "Replay fixture event" }).click();
  await expect(page.locator(".react-flow__edge.animated")).toHaveCount(1);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect(page.locator(".react-flow__edge.animated")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#luffy");
  const core = page.getByRole("img", { name: /mechanical core/ });
  await expect(core).toBeVisible();
  const box = (await core.boundingBox())!;
  expect(box.width).toBe(72);
  expect(box.y + box.height).toBeLessThan(844);
  await page.goto("/#knowledge");
  await expect(page.locator(".graph-list button")).toHaveCount(11);
  await page.getByRole("button", { name: "Show graph" }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(11);
  await expect(
    page.getByRole("button", { name: "Zoom In", exact: true }),
  ).toBeVisible();
  const control = (await page.locator(".graph-controls").boundingBox())!;
  const pane = (await page.locator(".react-flow__pane").boundingBox())!;
  expect(control.y).toBeGreaterThanOrEqual(pane.y + pane.height - 1);
});

test("record 500-input / 40-visible real graph performance", async ({
  page,
}) => {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Performance.enable");
  await page.addInitScript(() => {
    (window as unknown as { longTasks: number[] }).longTasks = [];
    new PerformanceObserver((list) => {
      for (const e of list.getEntries())
        (window as unknown as { longTasks: number[] }).longTasks.push(
          e.duration,
        );
    }).observe({ type: "longtask", buffered: true });
  });
  const started = performance.now();
  await page.goto(harness + "?scale");
  await expect(page.locator(".react-flow__node")).toHaveCount(40);
  const systemLoadMs = performance.now() - started;
  const nav = performance.now();
  await page.getByRole("button", { name: "knowledge", exact: true }).click();
  await expect(
    page.getByText("40 of 500 matching nodes", { exact: false }),
  ).toBeVisible();
  await expect(page.locator(".react-flow__node")).toHaveCount(40);
  const knowledgeNavMs = performance.now() - nav;
  const cached = [];
  for (let i = 0; i < 3; i++) {
    await page.getByRole("button", { name: "system", exact: true }).click();
    await expect(page.locator(".react-flow__node")).toHaveCount(40);
    const start = performance.now();
    await page.getByRole("button", { name: "knowledge", exact: true }).click();
    await expect(page.locator(".react-flow__node")).toHaveCount(40);
    cached.push(performance.now() - start);
  }
  const before = (await cdp.send("Performance.getMetrics")).metrics.find(
    (m) => m.name === "TaskDuration",
  )!.value;
  const start = performance.now();
  await page
    .getByLabel("Search nodes")
    .pressSequentially("Sample 00000000", { delay: 20 });
  const searchWallMs = performance.now() - start;
  const after = (await cdp.send("Performance.getMetrics")).metrics.find(
    (m) => m.name === "TaskDuration",
  )!.value;
  await page.getByLabel("Search nodes").fill("");
  await expect(page.locator(".react-flow__node")).toHaveCount(40);
  await cdp.send("HeapProfiler.collectGarbage");
  const memory = await cdp.send("Memory.getDOMCounters");
  const heap = (await cdp.send("Performance.getMetrics")).metrics.find(
    (m) => m.name === "JSHeapUsedSize",
  )!.value;
  const report = {
    fixture:
      "test-only built harness; eager graph import; real React Flow, no stubs",
    inputKnowledgeNodes: 500,
    visibleCap: 40,
    systemNodes: 40,
    systemEdges: 39,
    systemLoadMs,
    knowledgeNavMs,
    cachedKnowledgeMs: cached,
    searchKeystrokes: 15,
    searchWallMs,
    searchRendererMs: (after - before) * 1000,
    jsHeapMiB: heap / 1048576,
    ...memory,
    longTasks: await page.evaluate(
      () => (window as unknown as { longTasks: number[] }).longTasks,
    ),
  };
  writeFileSync(
    "evidence/scale-performance.json",
    JSON.stringify(report, null, 2),
  );
});
