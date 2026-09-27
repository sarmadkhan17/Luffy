import {
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  useCallback,
} from "react";
import { Send, MicOff, Square, ArrowUpRight } from "lucide-react";
import { usePreview } from "../context";
import {
  Badge,
  EvidenceButton,
  VisualBoundary,
  useMedia,
} from "../components/ui";
import type { RequestState } from "../components/Avatar";
const Avatar = lazy(() => import("../components/Avatar"));
export default function Luffy() {
  const { adapter, scenario, messages, setMessages } = usePreview();
  const [input, setInput] = useState("");
  const [state, setState] = useState<RequestState>("idle");
  const [partial, setPartial] = useState("");
  const [error, setError] = useState("");
  const request = useRef<{ controller: AbortController; id: number } | null>(
    null,
  );
  const mobile = useMedia("(max-width: 759px)");
  const nearBottom = useRef(true);
  const [unread, setUnread] = useState(false);
  const transcript = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const busy = state === "pending" || state === "responding";
  const abortPending = useCallback(
    (reason: string) => {
      const pending = request.current;
      if (!pending) return;
      pending.controller.abort();
      request.current = null;
      setMessages((m) =>
        m.map((message) =>
          message.id === pending.id
            ? { ...message, outcome: "cancelled", cancellationReason: reason }
            : message,
        ),
      );
    },
    [setMessages],
  );
  useEffect(() => () => abortPending("Left the conversation"), [abortPending]);
  useEffect(() => {
    abortPending("Fixture scenario changed");
    setState("idle");
    setPartial("");
    setError("");
  }, [scenario, abortPending]);
  useEffect(() => {
    const el = transcript.current;
    if (!el) return;
    if (nearBottom.current) {
      el.scrollTo({ top: el.scrollHeight });
      setUnread(false);
    } else setUnread(true);
  }, [messages, partial, state]);
  function jumpToLatest() {
    nearBottom.current = true;
    transcript.current?.scrollTo({ top: transcript.current.scrollHeight });
    setUnread(false);
  }
  const avatar = (
    <VisualBoundary fallback={<span>Core unavailable</span>}>
      <Suspense
        fallback={<span className="avatar-placeholder">Core loading</span>}
      >
        <Avatar state={state} />
      </Suspense>
    </VisualBoundary>
  );
  async function send() {
    const text = input.trim();
    if (!text || request.current || messages.length >= 100) return;
    const controller = new AbortController();
    const ownerId = Date.now();
    request.current = { controller, id: ownerId };
    setInput("");
    setState("pending");
    setError("");
    setPartial("");
    setMessages((m) => [
      ...m,
      { id: ownerId, role: "owner", text, evidence: [], outcome: "pending" },
    ]);
    try {
      const reply = await adapter.chat(
        text,
        scenario,
        controller.signal,
        (chunk) => {
          if (!controller.signal.aborted) {
            setState("responding");
            setPartial(chunk);
          }
        },
      );
      if (controller.signal.aborted) return;
      setMessages((m) => [
        ...m.map((message) =>
          message.id === ownerId
            ? { ...message, outcome: "answered" as const }
            : message,
        ),
        { id: ownerId + 1, role: "luffy", ...reply },
      ]);
      setPartial("");
      setState("idle");
    } catch (e) {
      if (controller.signal.aborted) return;
      setMessages((m) =>
        m.map((message) =>
          message.id === ownerId ? { ...message, outcome: "failed" } : message,
        ),
      );
      setState("error");
      setError(e instanceof Error ? e.message : "Reply unavailable");
    } finally {
      if (request.current?.controller === controller) request.current = null;
    }
  }
  function cancel() {
    abortPending("Cancelled by owner");
    setState("cancelled");
    setPartial("");
    setError("Request cancelled. No action was taken.");
    composer.current?.focus();
  }
  return (
    <div className="conversation-layout">
      <section className="panel conversation" aria-label="Conversation">
        <div className="panel-heading conversation-header">
          {mobile && <div className="mobile-core">{avatar}</div>}
          <div className="conversation-title">
            <span className="status-dot" />
            <h2>LUFFY</h2>
            <Badge>Fixture conversation</Badge>
          </div>
          <span className="quiet">Text only</span>
        </div>
        <div
          className="transcript"
          role="log"
          aria-label="Message transcript"
          ref={transcript}
          onScroll={() => {
            const el = transcript.current;
            if (el) {
              nearBottom.current =
                el.scrollHeight - el.scrollTop - el.clientHeight < 64;
              if (nearBottom.current) setUnread(false);
            }
          }}
        >
          {messages.map((m) => (
            <article className={`message ${m.role}`} key={m.id}>
              <div className="message-role">
                {m.role === "owner" ? "YOU" : "LUFFY"} <span>DEMO</span>
              </div>
              <p>{m.text}</p>
              {m.outcome === "cancelled" && (
                <p className="message-outcome">
                  Cancelled — no reply · {m.cancellationReason}
                </p>
              )}
              {m.outcome === "failed" && (
                <p className="message-outcome">Failed — no reply</p>
              )}
              {m.evidence.map((e) => (
                <EvidenceButton key={e.id} value={e} />
              ))}
            </article>
          ))}
          {busy && (
            <article className="message luffy">
              <div className="message-role">
                LUFFY{" "}
                <span>
                  {state === "pending" ? "REQUEST PENDING" : "RESPONDING"}
                </span>
              </div>
              <p>{partial || "Waiting for the fixture adapter…"}</p>
            </article>
          )}
          {error && (
            <p
              className={state === "error" ? "inline-error" : "quiet"}
              role={state === "error" ? "alert" : "status"}
            >
              {error}{" "}
              {state === "error" &&
                "Your message remains in the transcript. Send a new message to retry."}
            </p>
          )}
        </div>
        {unread && (
          <button className="jump-latest" type="button" onClick={jumpToLatest}>
            Jump to latest ↓
          </button>
        )}
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            void send();
          }}
        >
          <label htmlFor="message">Message LUFFY</label>
          <textarea
            ref={composer}
            id="message"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            maxLength={2000}
            placeholder="Ask about the sample evidence…"
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <div className="composer-actions">
            <span className="quiet">
              <MicOff size={14} /> Voice unavailable · Enter to send
            </span>
            {busy ? (
              <button type="button" onClick={cancel}>
                <Square size={15} /> Cancel request
              </button>
            ) : (
              <button
                className="primary"
                disabled={!input.trim() || messages.length >= 100}
                type="submit"
              >
                Send <Send size={15} />
              </button>
            )}
          </div>
          {messages.length >= 100 && (
            <p role="status">
              Preview transcript limit reached (100 messages). Reload to start a
              new session.
            </p>
          )}
        </form>
      </section>
      <aside className="conversation-side">
        {!mobile && avatar}
        <div className="avatar-description">
          <Badge tone={state === "error" ? "rose" : "neutral"}>
            {state === "idle" ? "Ready" : state}
          </Badge>
          <p>The core reflects this request’s state.</p>
        </div>
        <div className="side-note">
          <div className="eyebrow">EVIDENCE, NOT ASSUMPTIONS</div>
          <h2>Keep the source in view.</h2>
          <p>
            Responses link to their supporting records. Inspect freshness and
            limitations before interpreting a claim.
          </p>
          <a className="text-link" href="#knowledge">
            Explore Knowledge <ArrowUpRight size={15} />
          </a>
        </div>
        <div className="side-note">
          <h3>Isolated conversation</h3>
          <p>
            Deterministic fixture replies. No LLM, microphone, approvals or
            trading commands are connected.
          </p>
        </div>
      </aside>
    </div>
  );
}
