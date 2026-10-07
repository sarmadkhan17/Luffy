/** Owner controls (Resume / Freeze / Halt / PANIC) from the approved Trades header.
 * Every action is a typed request to the kernel's Owner Interface (GraphQL
 * owner_control / panic) through adapter.control. The browser changes no state
 * itself, sends nothing while the Owner Interface health read is not AVAILABLE,
 * and reuses one request id per intended action until the kernel answers. */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { OctagonAlert, Pause, Play, Snowflake } from "lucide-react";
import { usePreview } from "../context";
import { DetailPanel, KV } from "./glass";
import * as pending from "../adapters/pending";
import type { ControlOperation, ControlResult, PendingRequest, Position } from "../adapters/contracts";

type Key = "resume" | "freeze" | "halt" | "panic";
const C = { resume: "#3ddc97", freeze: "#56ccf2", halt: "#f2b44a", panic: "#ff7b72" } as const;
const ICON = { resume: Play, freeze: Snowflake, halt: Pause, panic: OctagonAlert } as const;

interface Def {
  label: string;
  title: string;
  go: string;
  what: string;
  effect: string;
  warn?: string;
  ack?: string;
  tip: string;
}
const DEFS: Record<Key, Def> = {
  resume: {
    label: "Resume", title: "Resume LUFFY?", go: "Request resume",
    what: "Requests the Supervisor's guarded recovery. LUFFY becomes ACTIVE — scanning every cycle and able to open new trades — only if the Supervisor and Risk prove it safe from venue, protection and Risk evidence. It may stay contained.",
    effect: "Keeps being managed with its stop", tip: "Lift the freeze or halt (guarded)",
  },
  freeze: {
    label: "Freeze", title: "Freeze new trades?", go: "Freeze",
    what: "Blocks new entries. LUFFY keeps running and keeps managing exits and protection on what is open until you resume.",
    effect: "Stays open; exits and protection keep running", tip: "Stop new entries, keep managing positions",
  },
  halt: {
    label: "Halt", title: "Halt LUFFY?", go: "Halt",
    what: "Stops entries and in-process exit management (targets and trails). Exchange stops remain on the venue.",
    effect: "Only the exchange stop protects it",
    warn: "While halted LUFFY will not trail stops or react to anything. Use this only when you need it fully still.",
    tip: "Stop entries and exit management",
  },
  panic: {
    label: "PANIC", title: "Close everything now?", go: "Close all & freeze",
    what: "The kernel flattens every position at market and freezes LUFFY. Use only in an emergency.",
    effect: "Closed at market now",
    warn: "A market close ignores your stops and may lock in a worse price than they would.",
    ack: "I understand this closes every position at market price now",
    tip: "Emergency: close all positions and freeze",
  },
};

type Outcome =
  | { kind: "answer"; key: Key; result: ControlResult; request: PendingRequest }
  | { kind: "unknown"; key: Key; request: PendingRequest };

export function OwnerControls({ control, positions, supervisorNote }: { control: string; positions: Position[] | null; supervisorNote?: string }) {
  const { adapter } = usePreview();
  const qc = useQueryClient();
  const health = useQuery({
    queryKey: ["owner-interface"],
    queryFn: ({ signal }) => adapter.ownerInterface!(signal),
    enabled: !!adapter.ownerInterface,
    staleTime: 0,
    refetchInterval: 30000,
    refetchIntervalInBackground: false,
  });
  const [ask, setAsk] = useState<Key | null>(null);
  const [ack, setAck] = useState(false);
  const [inFlight, setInFlight] = useState<Key | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [storageWarning, setStorageWarning] = useState(false);
  const [, rerender] = useState(0);

  const op = (k: Key): ControlOperation => (k === "resume" && control === "HALTED" ? "unhalt" : k);
  const available = !!adapter.control && health.data?.availability === "AVAILABLE" && !health.isFetching;
  const why = !adapter.control
    ? "Controls are unavailable: this GUI is read-only."
    : health.error
      ? `Owner Interface health read failed: ${(health.error as Error).message}`
      : health.data?.availability === "UNAVAILABLE"
        ? `Owner Interface unavailable (${health.data.reasons.join(", ") || "no reason given"}) — nothing is sent while the kernel cannot receive it.`
        : health.isPending ? "Checking the Owner Interface…" : "";
  const off = (k: Key) =>
    !available || inFlight !== null ||
    (k === "resume" && control === "ACTIVE") || (k === "freeze" && control === "FROZEN") || (k === "halt" && control === "HALTED");

  async function send(k: Key, request: PendingRequest) {
    setInFlight(k);
    try {
      const result = await adapter.control!(op(k), request);
      if (result && pending.DEFINITIVE.includes(result.status)) pending.done(request.id);
      setOutcome(result ? { kind: "answer", key: k, result, request } : { kind: "unknown", key: k, request });
    } catch (e) {
      setOutcome({ kind: "unknown", key: k, request });
      void e;
    } finally {
      setInFlight(null);
      rerender((n) => n + 1);
      void qc.invalidateQueries({ queryKey: ["overview"] });
      void qc.invalidateQueries({ queryKey: ["owner-interface"] });
    }
  }
  function start(k: Key) {
    const existing = pending.find(op(k));
    setOutcome(null);
    if (existing) void send(k, existing); // unresolved earlier attempt: same id, no new consent
    else { setAck(false); setAsk(k); }
  }
  function confirm() {
    if (!ask) return;
    const k = ask;
    const { entry, durable } = pending.create(op(k));
    if (!durable) setStorageWarning(true);
    setAsk(null);
    void send(k, entry);
  }

  const tone = control === "ACTIVE" ? "#3ddc97" : control === "FROZEN" ? "#56ccf2" : control === "HALTED" ? "#f2b44a" : "#8d9fb2";
  const def = ask ? DEFS[ask] : null;
  const retrying = (k: Key) => !!pending.find(op(k));
  const answered = outcome && outcome.kind === "answer" ? outcome : null;

  return (
    <>
      <div role="group" aria-label="Owner controls" className="gl-ownr">
        <span className="state">
          <i className={control === "ACTIVE" ? "gl-ping" : "gl-blinkdot"} style={{ background: tone, boxShadow: `0 0 8px ${tone}` }} />
          <span>
            <small>Owner controls</small>
            <b className="m" style={{ color: tone }} data-testid="control-state">{control}</b>
          </span>
        </span>
        {(Object.keys(DEFS) as Key[]).map((k) => {
          const Icon = ICON[k];
          return (
            <button key={k} type="button" className={`gl-oc ${k}`} disabled={off(k)} title={off(k) && why ? why : DEFS[k].tip} aria-label={DEFS[k].label} onClick={() => start(k)} data-op={op(k)}>
              <Icon size={15} aria-hidden="true" />
              {inFlight === k ? `${DEFS[k].label}…` : retrying(k) ? `Retry ${DEFS[k].label}` : DEFS[k].label}
            </button>
          );
        })}
      </div>

      <DetailPanel open={!!def} onClose={() => setAsk(null)} title={def ? def.title : ""} description={def ? "Owner control" : undefined}>
        {def && ask && (
          <>
            <p style={{ margin: 0, fontSize: 14, lineHeight: 1.5, color: "#c9d4df" }}>{def.what}</p>
            <h3 className="cz" style={{ margin: 0, fontSize: 11, color: "rgb(var(--mut))" }}>What happens to your positions</h3>
            {positions === null ? <p className="gl-note">Open positions could not be read; the kernel decides from venue state.</p>
              : positions.length === 0 ? <p className="gl-note">No open positions are recorded in the journal.</p>
              : positions.map((p) => (
                <div className="gl-list" key={p.id ?? p.symbol} style={{ padding: "6px 10px", borderRadius: 8, background: "#ffffff08" }}>
                  <span><b className="m">{p.symbol.split("/")[0]}</b>{p.side.toLowerCase()}{p.leverage ? ` · ${p.leverage}x` : ""}{p.journalStop?.price ? ` · journal stop ${p.journalStop.price}` : ""}</span>
                  <span style={{ color: C[ask] }}>{def.effect}</span>
                </div>
              ))}
            {ask === "resume" && supervisorNote && <p className="gl-err">{supervisorNote}</p>}
            {def.warn && <p className="gl-err" style={{ borderColor: `${C[ask]}66`, color: C[ask] }}>{def.warn}</p>}
            {def.ack && (
              <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
                <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
                {def.ack}
              </label>
            )}
            <div className="gl-actions">
              <button type="button" className="gl-btn" onClick={() => setAsk(null)}>Cancel</button>
              <button type="button" className="gl-btn primary" style={{ background: C[ask] }} disabled={!available || (!!def.ack && !ack)} onClick={confirm}>{def.go}</button>
            </div>
            <p className="gl-note">Sent to the kernel's Owner Interface as one request with a single id; the browser never changes state itself.</p>
          </>
        )}
      </DetailPanel>

      <DetailPanel
        open={!!outcome}
        onClose={() => setOutcome(null)}
        title={outcome ? (outcome.kind === "unknown" ? `${DEFS[outcome.key].label}: no answer` : `${DEFS[outcome.key].label}: ${outcome.result.status}`) : ""}
        description={outcome ? `Request ${outcome.request.id}` : undefined}
      >
        {outcome?.kind === "unknown" && (
          <p style={{ margin: 0 }}>The outcome is unknown. The same request id is kept; pressing {DEFS[outcome.key].label} again resolves it without a second execution.</p>
        )}
        {answered && (
          <>
            <p style={{ margin: 0 }} data-testid="control-outcome">{answered.result.message}{answered.result.replayed ? " (recorded result)" : ""}</p>
            <KV items={[
              ["Disposition", answered.result.disposition],
              ["Control state", `${answered.result.controlStateBefore ?? "?"} → ${answered.result.controlStateAfter ?? "?"}`],
              ["Reasons", answered.result.reasons.join(", ") || "—"],
              ["Supervisor", answered.result.supervisorOutcome],
              ["Audit events", answered.result.auditEventIds.join(", ") || "—"],
            ]} />
          </>
        )}
        {storageWarning && <p className="gl-err">Browser storage is unavailable: an unresolved request is kept only until this page reloads.</p>}
      </DetailPanel>
    </>
  );
}
