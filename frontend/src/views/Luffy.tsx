import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import { Send, MicOff, Square, FileText } from "lucide-react";
import { recordHref, type RecordKind } from "../links";
import { usePreview, type Message } from "../context";
import { Badge, EvidenceButton } from "../components/ui";
import type { RequestState } from "../components/Avatar";
import { NeedsYou, OwnerEvidence } from "../components/OwnerEvidence";
import { Card, CardHead } from "../components/glass";
import { PageHeader } from "../components/PageHeader";
export default function Luffy() {
  const { adapter, scenario, messages, setMessages, session } = usePreview();
  const readOnly = session?.dashboard_mode === "READ_ONLY_GUI";
  const live = import.meta.env.MODE === "production" || adapter.mode === "LIVE";
  const [input, setInput] = useState("");
  const [state, setState] = useState<RequestState>("idle");
  const [partial, setPartial] = useState("");
  const [error, setError] = useState("");
  const request = useRef<{ controller: AbortController; id: number } | null>(
    null,
  );
  const nearBottom = useRef(true);
  const [unread, setUnread] = useState(false);
  const transcript = useRef<HTMLDivElement>(null);
  const composer = useRef<HTMLTextAreaElement>(null);
  const busy = state === "pending" || state === "responding";
  const coreState = state === "cancelled" ? "idle" : state;
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
  async function send() {
    const text = input.trim();
    if (readOnly || !text || request.current || messages.length >= 100) return;
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
          mentions:
            reply.links === undefined && reply.consulted === undefined
              ? undefined
              : {
                  links: reply.links,
                  linksNote: reply.linksNote,
                  unresolved: reply.unresolved,
                  counts: reply.counts,
                  consulted: reply.consulted,
                  consultedNote: reply.consultedNote,
                },
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

  const label: Record<string, [string, string, string]> = {
    idle: ["Ready", "#3ddc97", "Waiting for you. Rings turn slowly while the core rests."],
    pending: ["Reading the journal…", "rgb(var(--a2))", "Rings speed up while LUFFY gathers evidence."],
    responding: ["Replying", "#3ddc97", "Steady glow while the answer arrives."],
    error: ["No reply", "#ff7b72", "The request failed; the core turns red and stops."],
  };
  const [stateLabel, stateColor, stateNote] = label[coreState] ?? label.idle;
  const orbCls = { idle: "idle", pending: "thinking", responding: "replying", error: "failed", cancelled: "idle" }[coreState as string] ?? "idle";
  const cited = collectEvidence(messages);
  const quick = live
    ? ["How am I doing today?", "What positions are open?", "Which strategy is weakest?", "What is research testing?"]
    : ["Ask about the sample research receipt"];
  const timeOf = (id: number) => (id > 1e12 ? new Date(id).toISOString().slice(11, 16) + " UTC" : "");
  return (
    <>
      <PageHeader title="LUFFY">
        <span style={{ fontSize: 13, color: "var(--txt2)" }}>your trading partner · answers from recorded evidence</span>
      </PageHeader>
      <div className="gl-row" style={{ flex: 1, minHeight: 0 }} data-core-state={coreState}>
        <Card className="gl-chat" label="Conversation">
          <div className="gl-orbwrap">
            <div className={`orb ${orbCls}`} role="img" aria-label={`LUFFY core: ${stateLabel}`}>
              <span className="ring r1" /><span className="ring r2" /><span className="ring r3" /><span className="ring r4" /><span className="ring r5" /><span className="ring r6" /><span className="core" />
            </div>
            <span className="cz" style={{ fontSize: 28, color: "rgb(var(--hi))", letterSpacing: ".2em", paddingLeft: ".2em" }}>LUFFY</span>
            <span style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 15, color: stateColor }}>
              <i style={{ width: 9, height: 9, borderRadius: "50%", background: stateColor, boxShadow: `0 0 10px ${stateColor}` }} />
              <span data-testid="core-state-label">{stateLabel}</span>
            </span>
            <span style={{ fontSize: 13, color: "var(--txt2)", maxWidth: 360, lineHeight: 1.5 }}>{stateNote} The core reflects this request's state.</span>
            <ol className="core-states" aria-label="Core request states" style={{ display: "none" }}>
              {CORE_STATES.map(([st, l]) => (<li key={st} data-on={coreState === st} aria-current={coreState === st ? "step" : undefined}>{l}</li>))}
            </ol>
          </div>

          <div className="gl-log" role="log" aria-label="Message transcript" tabIndex={0} ref={transcript}
            onScroll={() => {
              const el = transcript.current;
              if (el) { nearBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 64; if (nearBottom.current) setUnread(false); }
            }}>
            {messages.map((m) => (
              <article className={`msgin ${m.role}`} key={m.id} style={{ alignItems: m.role === "owner" ? "flex-end" : "flex-start" }}>
                <span style={{ fontSize: 12, color: "var(--txt3)" }}>
                  <b style={{ color: m.role === "owner" ? "#c9d4df" : "rgb(var(--a2))" }}>{m.role === "owner" ? "You" : "LUFFY"}</b>
                  {timeOf(m.id) ? ` · ${timeOf(m.id)}` : ""}{live && m.requestId ? ` · reply ${m.requestId.slice(0, 8)}` : ""}
                </span>
                <div className="bubble" style={{ borderRadius: m.role === "owner" ? "14px 14px 4px 14px" : "14px 14px 14px 4px", background: m.role === "owner" ? "#ffffff0d" : "rgb(var(--a) / .08)", borderColor: m.role === "owner" ? "#ffffff1a" : "rgb(var(--a) / .3)" }}>
                  <p>{m.text}</p>
                </div>
                {m.outcome === "cancelled" && <p className="gl-note">Cancelled — no reply · {m.cancellationReason}</p>}
                {m.outcome === "failed" && <p className="gl-note" style={{ color: "#ff7b72" }}>Failed — no reply</p>}
                {m.mentions?.links && m.mentions.links.length > 0 && (
                  <div className="gl-cards">{m.mentions.links.map((l) => <RecCard key={`${l.kind}:${l.id}`} l={l} />)}</div>
                )}
                {m.evidence.length > 0 && <div className="gl-cards">{m.evidence.map((e) => <EvidenceButton key={e.id} value={e} />)}</div>}
                {m.mentions && <ReplyEvidence m={m.mentions} />}
              </article>
            ))}
            {busy && (
              <div className="msgin" style={{ display: "flex", alignItems: "center", gap: 10, color: "var(--txt2)", fontSize: 13 }}>
                {state === "responding" && partial ? <div className="bubble" style={{ background: "rgb(var(--a) / .08)", borderColor: "rgb(var(--a) / .3)" }}><p>{partial}</p></div> : (
                  <><span className="typing"><span /><span /><span /></span>{state === "pending" ? (live ? "LUFFY is reading the journal…" : "Waiting for the fixture adapter…") : "Responding…"}</>
                )}
              </div>
            )}
            {error && <p className={state === "error" ? "gl-err" : "gl-note"} role={state === "error" ? "alert" : "status"}>{error} {state === "error" && "Your message remains in the transcript. Send a new message to retry."}</p>}
          </div>
          {unread && <button className="gl-btn" type="button" onClick={jumpToLatest} style={{ alignSelf: "center" }}>Jump to latest ↓</button>}

          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {quick.map((q) => (
              <button key={q} type="button" className="gl-chip2" disabled={readOnly || busy} onClick={() => { setInput(q); composer.current?.focus(); }}>{q}</button>
            ))}
          </div>
          <form className="gl-composer" onSubmit={(e) => { e.preventDefault(); void send(); }}>
            <button type="button" className="mic" disabled aria-label="Voice unavailable" title="Voice is not connected"><MicOff size={20} aria-hidden="true" /></button>
            <label htmlFor="message" className="gl-sr">Message LUFFY</label>
            <textarea ref={composer} id="message" rows={1} value={input} onChange={(e) => setInput(e.target.value)} maxLength={2000} disabled={readOnly}
              placeholder={readOnly ? "Chat is disabled in read-only GUI mode" : live ? "Ask LUFFY about trades, strategies, risk…" : "Ask about the sample evidence…"}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void send(); } }} />
            {busy ? (
              <button key="cancel" type="button" className="gl-btn" onClick={(e) => { e.preventDefault(); e.stopPropagation(); cancel(); }}><Square size={15} aria-hidden="true" /> Cancel request</button>
            ) : (
              <button key="send" type="submit" className="gl-btn primary" aria-label="Send" disabled={readOnly || !input.trim() || messages.length >= 100}><Send size={16} aria-hidden="true" /> Send</button>
            )}
          </form>
          <p className="gl-note">{readOnly ? "Chat disabled in read-only GUI mode" : "Voice unavailable · Enter to send · Shift+Enter for a new line"}{messages.length >= 100 ? " · transcript limit reached (100 messages); reload to start a new session" : ""}</p>
        </Card>

        <aside className="gl-side" style={{ maxWidth: 440 }}>
          <Card className="pad" label="Evidence used">
            <CardHead icon={<FileText size={16} />} title="Evidence used" />
            <span style={{ fontSize: 13, color: "var(--txt2)" }}>Every record LUFFY cited in this conversation. Click one to jump to it.</span>
            {cited.length === 0 ? <p className="gl-note">Nothing cited yet. Records appear here when a reply names one by exact id.</p>
              : cited.map((l) => <RecCard key={`${l.kind}:${l.id}`} l={l} />)}
            <div className="gl-callout">
              LUFFY can explain and suggest. It cannot approve, spend, activate or trade from this conversation. Anything that changes trading needs your approval under <a className="gl-link" href="#overview">Needs you</a> on the Overview.
            </div>
          </Card>
          {live && (
            <Card className="pad gl-legacy" label="Needs you">
              <CardHead icon={<FileText size={16} />} title="Needs you" />
              <NeedsYou />
            </Card>
          )}
          {live && (
            <Card className="pad gl-legacy" label="Evidence query">
              <CardHead icon={<FileText size={16} />} title="Evidence query" />
              <OwnerEvidence />
            </Card>
          )}
          <Card className="pad" label="Owner controls">
            <CardHead icon={<FileText size={16} />} title="Owner controls" />
            <p style={{ margin: 0, fontSize: 13, color: "var(--txt2)", lineHeight: 1.5 }}>
              {live ? "Conversation uses typed internal queries. Needs You records exact owner decisions through the existing control gateway; recovery uses Operations. Voice is not connected." : "Deterministic fixture replies. No LLM, microphone, approvals or trading commands are connected."}
            </p>
            {live && <a className="gl-btn" href="#operations">Open Operations ↗</a>}
          </Card>
        </aside>
      </div>
    </>
  );
}

const KIND_GLYPH: Record<string, string> = { strategy: "S", trade: "T", decision: "D", research: "R" };
type Link = NonNullable<NonNullable<Message["mentions"]>["links"]>[number];
/** One record named by exact id; the link goes to the record. */
function RecCard({ l }: { l: Link }) {
  return (
    <a className="rcard" href={recordHref(MENTION_KIND[l.kind], l.id)} data-record={`${l.kind}:${l.id}`}>
      <span className="m glyph">{KIND_GLYPH[l.kind] ?? "·"}</span>
      <span style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        <span style={{ fontSize: 11, color: "var(--txt3)", textTransform: "uppercase", letterSpacing: ".1em" }}>{l.kind}</span>
        <span style={{ fontSize: 14, fontWeight: 600, overflowWrap: "anywhere" }}>{l.label}</span>
        <span style={{ fontSize: 12, color: "var(--txt2)" }}>exact id · {l.id}</span>
      </span>
      <span style={{ marginLeft: "auto" }} aria-hidden="true">→</span>
    </a>
  );
}
/** Records named in the transcript, newest first, one card per record. */
function collectEvidence(messages: Message[]): Link[] {
  const seen = new Set<string>();
  const out: Link[] = [];
  for (const m of [...messages].reverse())
    for (const l of m.mentions?.links ?? [])
      if (!seen.has(`${l.kind}:${l.id}`)) { seen.add(`${l.kind}:${l.id}`); out.push(l); }
  return out;
}

/** The request states the core can show; only the current one is lit. */
const CORE_STATES: [RequestState, string][] = [
  ["idle", "Ready"],
  ["pending", "Request pending"],
  ["responding", "Responding"],
  ["error", "Error"],
];
const MENTION_KIND: Record<string, RecordKind> = {
  strategy: "strategy",
  trade: "trade",
  decision: "decision",
  research: "research",
};
/** Record mentions and consulted reads of one LIVE reply. */
export function ReplyEvidence({ m }: { m: NonNullable<Message["mentions"]> }) {
  return (
    <div className="reply-evidence" data-testid="reply-evidence">
      <span className="reply-evidence-label">
        Records mentioned by exact id · context, not the reply's source
      </span>
      {m.links === null || m.links === undefined ? (
        <p className="quiet" data-testid="lookup-unavailable">
          <Badge tone="amber">UNAVAILABLE</Badge> {m.linksNote}
        </p>
      ) : m.links.length > 0 ? (
        <ul aria-label="Records mentioned in this reply">
          {m.links.map((l) => (
            <li key={`${l.kind}:${l.id}`}>
              <a
                className="record-link"
                href={recordHref(MENTION_KIND[l.kind], l.id)}
                data-record={`${l.kind}:${l.id}`}
              >
                {l.kind} · {l.label}
              </a>{" "}
              <small>exact id</small>
            </li>
          ))}
        </ul>
      ) : (
        <p className="quiet">No stored record was mentioned by id.</p>
      )}
      {m.counts && m.counts.truncated > 0 && (
        <p className="quiet" data-testid="links-truncated">
          {m.counts.resolved - m.counts.truncated} of {m.counts.resolved}{" "}
          matched stored records shown.
        </p>
      )}
      {m.unresolved && m.unresolved.length > 0 && (
        <p className="quiet" data-testid="unresolved-ids">
          Not found in the stores (unresolved ids): {m.unresolved.join(", ")}
          {m.counts && m.counts.unresolved > m.unresolved.length
            ? ` (${m.unresolved.length} of ${m.counts.unresolved} shown)`
            : ""}
        </p>
      )}
      {m.consulted ? (
        m.consulted.length > 0 && (
          <p className="quiet consulted">
            Reads consulted (read-only):{" "}
            {m.consulted.map((c, i) => (
              <code key={i}>
                {`${c.tool}(${c.arguments || ""})${c.error ? ` failed: ${c.error}` : c.rows !== null ? ` → ${c.rows} rows` : ""}`}
              </code>
            ))}
          </p>
        )
      ) : (
        <p className="quiet">{m.consultedNote}</p>
      )}
    </div>
  );
}
