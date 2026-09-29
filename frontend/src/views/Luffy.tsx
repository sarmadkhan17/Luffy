import { lazyChunk } from "../lazyChunk";
import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import { Send, MicOff, Square, ArrowUpRight } from "lucide-react";
import { usePreview } from "../context";
import {
  Badge,
  EvidenceButton,
  VisualBoundary,
  useMedia,
} from "../components/ui";
import type { RequestState } from "../components/Avatar";
const Avatar = lazyChunk(() => import("../components/Avatar"));
export default function Luffy() {
  const { adapter, scenario, messages, setMessages } = usePreview();
  const live = import.meta.env.MODE === "production" || adapter.mode === "LIVE";
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
        <Avatar state={state === "cancelled" ? "idle" : state} />
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
    const history = messages
      .filter(
        (m) => m.id !== 0 && (m.role === "luffy" || m.outcome === "answered"),
      )
      .map((m) => ({
        who: m.role === "luffy" ? ("Luffy" as const) : ("owner" as const),
        text: m.text,
      }))
      .slice(-20);
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
        history,
      );
      if (controller.signal.aborted) return;
      setMessages((m) => [
        ...m.map((message) =>
          message.id === ownerId
            ? { ...message, outcome: "answered" as const }
            : message,
        ),
        {
          id: ownerId + 1,
          role: "luffy",
          text: reply.text,
          evidence: reply.evidence,
          requestId: reply.requestId,
        },
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
            <Badge>
              {live
                ? "Text conversation · no controls"
                : "Fixture conversation"}
            </Badge>
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
                {m.role === "owner" ? "YOU" : "LUFFY"}{" "}
                <span>
                  {live
                    ? m.requestId
                      ? `REPLY ${m.requestId.slice(0, 8)}`
                      : "LIVE"
                    : "DEMO"}
                </span>
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
              <p>
                {partial ||
                  (live
                    ? "Waiting for the Luffy backend…"
                    : "Waiting for the fixture adapter…")}
              </p>
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
            placeholder={
              live
                ? "Ask about positions, P&L, decisions…"
                : "Ask about the sample evidence…"
            }
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
              <button
                key="cancel"
                type="button"
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  cancel();
                }}
              >
                <Square size={15} /> Cancel request
              </button>
            ) : (
              <button
                key="send"
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
            {live
              ? "Live replies do not carry evidence links yet. Check claims against Overview and Knowledge before acting."
              : "Responses link to their supporting records. Inspect freshness and limitations before interpreting a claim."}
          </p>
          <a className="text-link" href="#knowledge">
            Explore Knowledge <ArrowUpRight size={15} />
          </a>
        </div>
        <div className="side-note">
          <h3>{live ? "Owner controls" : "Isolated conversation"}</h3>
          <p>
            {live
              ? "Replies come from Luffy's read-only chat backend. It cannot freeze, halt, resume or trade — owner controls are on Operations. The backend supplies no evidence identifiers yet, so replies carry none. Voice is not connected."
              : "Deterministic fixture replies. No LLM, microphone, approvals or trading commands are connected."}
          </p>
          {live && (
            <a className="button secondary" href="#operations">
              Open Operations ↗
            </a>
          )}
        </div>
      </aside>
    </div>
  );
}
