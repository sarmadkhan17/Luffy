import { createContext, useContext, useState, type ReactNode } from "react";
import type {
  OwnerAdapter,
  Scenario,
  Provenance,
  Reply,
} from "./adapters/contracts";
import type { Bootstrap } from "./adapters/live";
export interface Message {
  id: number;
  role: "owner" | "luffy";
  text: string;
  evidence: Provenance[];
  outcome?: "pending" | "answered" | "cancelled" | "failed";
  cancellationReason?: string;
  requestId?: string;
  /** LIVE replies: record mentions and consulted reads (see Reply) */
  mentions?: Pick<
    Reply,
    | "links"
    | "linksNote"
    | "unresolved"
    | "counts"
    | "consulted"
    | "consultedNote"
  >;
}
const Context = createContext<{
  adapter: OwnerAdapter;
  scenario: Scenario;
  setScenario: (v: Scenario) => void;
  messages: Message[];
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  session: Bootstrap | null;
  signOut: (() => void) | null;
} | null>(null);
const greeting = (live: boolean): Message => ({
  id: 0,
  role: "luffy",
  text: live
    ? "Connected to Luffy's conversation backend. Ask about positions, P&L, decisions or strategies. Conversation cannot change control state; owner controls are on Operations."
    : "This is the isolated LUFFY preview. Ask about the sample research receipt to explore a conversation and its evidence. All replies and records here are synthetic.",
  evidence: [],
});
/** The adapter is always explicit: nothing here can default to fixtures. */
export function PreviewProvider({
  children,
  adapter,
  session = null,
  signOut = null,
}: {
  children: ReactNode;
  adapter: OwnerAdapter;
  session?: Bootstrap | null;
  signOut?: (() => void) | null;
}) {
  const [scenario, setScenario] = useState<Scenario>("normal");
  const [messages, setMessages] = useState<Message[]>(() => [
    greeting(adapter.mode === "LIVE"),
  ]);
  return (
    <Context.Provider
      value={{
        adapter,
        scenario,
        setScenario,
        messages,
        setMessages,
        session,
        signOut,
      }}
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
export function useLive() {
  return usePreview().adapter.mode === "LIVE";
}
/** For shared components that may render outside a provider (unit tests). */
export function useModeSafe() {
  return (
    useContext(Context)?.adapter.mode ??
    (import.meta.env.MODE === "production" ? "LIVE" : "DEMO")
  );
}
