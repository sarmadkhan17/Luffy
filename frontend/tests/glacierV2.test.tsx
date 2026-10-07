import { describe, expect, it } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GlacierMotion, ChangeValue } from "../src/components/motion";
import { LuffyCore, corePresentation } from "../src/components/LuffyCore";
import { DataTable } from "../src/components/glass";
describe("Glacier V2 truth-preserving presentation", () => {
  it("distinguishes every core state by geometry and accessible text", () => {
    const { rerender } = render(<GlacierMotion><LuffyCore state="idle" /></GlacierMotion>);
    const geometry = new Set<string>();
    for (const state of Object.keys(corePresentation)) {
      rerender(<GlacierMotion><LuffyCore state={state} /></GlacierMotion>);
      geometry.add(screen.getByRole("img").querySelector("path")!.getAttribute("d")!);
      expect(screen.getByRole("img")).toHaveAttribute("data-state", state);
      expect(screen.getByRole("img")).toHaveAccessibleName(`LUFFY core: ${corePresentation[state][0]}`);
    }
    expect(geometry.size).toBe(Object.keys(corePresentation).length);
  });
  it("renders changed values and unavailable immediately without intermediate numbers", () => {
    const { rerender } = render(<ChangeValue value={12.5} />);
    expect(screen.getByText("12.5")).toBeVisible();
    rerender(<ChangeValue value={-200} />); expect(screen.getByText("-200")).toBeVisible();
    expect(screen.queryByText("12.5")).not.toBeInTheDocument();
    rerender(<ChangeValue value={null}>UNAVAILABLE</ChangeValue>);
    expect(screen.getByText("UNAVAILABLE")).toBeVisible();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
  it("keeps selected row identity after sorting and exposes exact active sort", () => {
    render(<DataTable label="records" rows={[{ id:"a", n:1 },{ id:"b", n:2 }]} rowId={r => r.id} onOpen={() => {}}
      cols={[{ key:"id", label:"Record", primary:true, value:r => r.id },{ key:"n", label:"Count", value:r => r.n }]} />);
    fireEvent.click(screen.getByRole("button", { name:/^a$/ }));
    expect(screen.getByText("a").closest("tr")).toHaveAttribute("data-selected", "true");
    fireEvent.click(screen.getByRole("button", { name:"Sort by Count" }));
    expect(screen.getByRole("columnheader", { name:/Count/ })).toHaveAttribute("aria-sort", "ascending");
    expect(screen.getByText("a").closest("tr")).toHaveAttribute("data-selected", "true");
  });
});
