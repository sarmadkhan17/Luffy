import { createContext, useContext, useState, type ReactNode } from "react";
import type { OwnerAdapter, Scenario, Provenance } from "./adapters/contracts";
import { fixtureAdapter } from "./adapters/fixture";
export interface Message {
  id: number;
  role: "owner" | "luffy";
  text: string;
  evidence: Provenance[];
  outcome?: "pending" | "answered" | "cancelled" | "failed";
  cancellationReason?: string;
}
const Context = createContext<{
  adapter: OwnerAdapter;
  scenario: Scenario;
  setScenario: (v: Scenario) => void;
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
} | null>(null);
export function PreviewProvider({
  children,
  adapter = fixtureAdapter,
}: {
  children: ReactNode;
  adapter?: OwnerAdapter;
}) {
  const [scenario, setScenario] = useState<Scenario>("normal");
  const [messages, setMessages] = useState<Message[]>([
    {
      id: 0,
      role: "luffy",
      text: "This is the isolated LUFFY preview. Ask about the sample research receipt to explore a conversation and its evidence. All replies and records here are synthetic.",
      evidence: [],
    },
  ]);
  return (
    <Context.Provider
      value={{ adapter, scenario, setScenario, messages, setMessages }}
    >
      {children}
    </Context.Provider>
  );
}
export function usePreview() {
  const value = useContext(Context);
  if (!value) throw new Error("PreviewProvider required");
  return value;
}
