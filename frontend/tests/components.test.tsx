import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import { PreviewProvider } from "../src/context";
import Luffy from "../src/views/Luffy";
import { EvidenceButton } from "../src/components/ui";
import { evidence, fixtureAdapter } from "../src/adapters/fixture";
import type { OwnerAdapter } from "../src/adapters/contracts";
describe("conversation and evidence", () => {
  it("blocks whitespace and preserves owner text on adapter failure", async () => {
    const chat = vi
      .fn()
      .mockRejectedValue(new Error("Disconnected; no replacement"));
    const user = userEvent.setup();
    render(
      <PreviewProvider adapter={{ ...fixtureAdapter, chat }}>
        <Luffy />
      </PreviewProvider>,
    );
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    await user.type(screen.getByLabelText("Message LUFFY"), "Example question");
    await user.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Disconnected");
    expect(screen.getByText("Example question")).toBeInTheDocument();
    expect(chat).toHaveBeenCalledOnce();
  });
  it("cancels a pending reply without appending late output", async () => {
    let resolve!: (v: { text: string; evidence: [] }) => void;
    let signal!: AbortSignal;
    const adapter: OwnerAdapter = {
      ...fixtureAdapter,
      chat: async (_t, _s, s) => {
        signal = s;
        return await new Promise((r) => (resolve = r));
      },
    };
    const user = userEvent.setup();
    render(
      <PreviewProvider adapter={adapter}>
        <Luffy />
      </PreviewProvider>,
    );
    await user.type(screen.getByLabelText("Message LUFFY"), "Cancel this");
    await user.click(screen.getByRole("button", { name: "Send" }));
    await user.click(screen.getByRole("button", { name: "Cancel request" }));
    expect(signal.aborted).toBe(true);
    resolve({ text: "Late forbidden output", evidence: [] });
    await Promise.resolve();
    expect(screen.queryByText("Late forbidden output")).not.toBeInTheDocument();
  });
  it("opens an accessible evidence dialog and returns focus on Escape", async () => {
    const user = userEvent.setup();
    render(<EvidenceButton value={evidence} />);
    const trigger = screen.getByRole("button", { name: /Inspect evidence/ });
    await user.click(trigger);
    expect(screen.getByRole("dialog")).toHaveTextContent("INCONCLUSIVE");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
