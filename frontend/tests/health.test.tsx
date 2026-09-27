import { render, screen } from "@testing-library/react";
import { it, expect } from "vitest";
import HealthStatus, {
  healthPresentation,
} from "../src/components/HealthStatus";
import { knowledgeNodes } from "../src/adapters/fixture";
it("stale health can only be historical, not current active styling", () => {
  const node = {
    ...knowledgeNodes[0],
    health: "active" as const,
    evidence: { ...knowledgeNodes[0].evidence, freshness: "stale" as const },
  };
  expect(healthPresentation(node)).toEqual({
    primary: "STALE",
    tone: "stale",
    previous: "ACTIVE",
  });
  render(<HealthStatus node={node} />);
  expect(screen.getByText("STALE")).toBeVisible();
  expect(screen.getByText("Last reported: ACTIVE")).toBeVisible();
});
it("missing telemetry differs from unknown current health", () => {
  const base = { ...knowledgeNodes[0], health: "unknown" as const };
  expect(healthPresentation(base).primary).toBe("UNKNOWN");
  expect(
    healthPresentation({
      ...base,
      evidence: {
        ...base.evidence,
        freshness: "unavailable",
        observedAt: null,
      },
    }).primary,
  ).toBe("UNAVAILABLE");
});
