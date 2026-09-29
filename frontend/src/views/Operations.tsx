import { timestamp as utc } from "../time";
/** Owner controls. Every action is a typed request to the kernel's Owner
 * Interface (GraphQL owner_control / panic). The browser sets no state, has no
 * fallback path, and sends nothing while the Owner Interface is not available.
 * Each intended action has one request id + issue time, reused until the kernel
 * gives a definitive answer. */
import { Decisions, RuntimeStatus, Investigations } from "./Activity";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import * as Dialog from "@radix-ui/react-dialog";
import { ShieldAlert, RefreshCw, X } from "lucide-react";
import { usePreview } from "../context";
import { Badge, Panel } from "../components/ui";
import * as pending from "../adapters/pending";
import type {
  ControlOperation,
  ControlResult,
  PendingRequest,
} from "../adapters/contracts";

export const CONTROLS: {
  op: ControlOperation;
  label: string;
  ask: string;
  detail: string;
  danger?: boolean;
}[] = [
  {
    op: "freeze",
    label: "Freeze",
    ask: "Freeze: block new entries, keep managing exits?",
    detail: "Blocks entries. Exits and protection keep running.",
  },
  {
    op: "halt",
    label: "Halt",
    ask: "Halt: stop entries and exit management? Venue stops stay.",
    detail:
      "Stops entries and in-process exit management. Exchange stops remain.",
  },
  {
    op: "resume",
    label: "Resume (guarded)",
    ask: "Resume: request a guarded recovery check? ACTIVE only if the Supervisor and Risk prove it safe.",
    detail:
      "Requests the Supervisor's guarded recovery. It may stay contained.",
  },
  {
    op: "unhalt",
    label: "Unhalt",
    ask: "Unhalt: request a guarded, Risk-checked recovery out of HALTED?",
    detail: "Explicit release from HALTED through the same guarded path.",
  },
  {
    op: "panic",
    label: "Panic",
    ask: "PANIC: flatten ALL positions and freeze?",
    detail: "Kernel flattens every position and freezes.",
    danger: true,
  },
];

type Outcome =
  | {
      kind: "answer";
      op: ControlOperation;
      result: ControlResult;
      request: PendingRequest;
    }
  | { kind: "unknown"; op: ControlOperation; request: PendingRequest };

export default function Operations() {
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
  const [confirming, setConfirming] = useState<
    (typeof CONTROLS)[number] | null
  >(null);
  const [inFlight, setInFlight] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [storageWarning, setStorageWarning] = useState(false);
  const [, rerender] = useState(0);
  const available =
    !!adapter.control &&
    health.data?.availability === "AVAILABLE" &&
    !health.isFetching;
  const unresolved = pending
    .entries()
    .filter((e) => CONTROLS.some((c) => c.op === e.action));
  const recoveryBusy = inFlight === "resume" || inFlight === "unhalt";

  async function send(op: ControlOperation, request: PendingRequest) {
    setInFlight(op);
    try {
      const result = await adapter.control!(op, request);
      if (result && pending.DEFINITIVE.includes(result.status))
        pending.done(request.id);
      setOutcome(
        result
          ? { kind: "answer", op, result, request }
          : { kind: "unknown", op, request },
      );
    } finally {
      setInFlight(null);
      rerender((n) => n + 1);
      void qc.invalidateQueries({ queryKey: ["overview"] });
      void qc.invalidateQueries({ queryKey: ["owner-interface"] });
    }
  }
  function start(c: (typeof CONTROLS)[number]) {
    const existing = pending.find(c.op);
    if (existing)
      void send(c.op, existing); // an unresolved attempt: same id, no new consent
    else setConfirming(c);
  }
  function confirm() {
    if (!confirming) return;
    const { entry, durable } = pending.create(confirming.op);
    if (!durable) setStorageWarning(true);
    const op = confirming.op;
    setConfirming(null);
    void send(op, entry);
  }

  if (!adapter.control || !adapter.ownerInterface)
    return (
      <section className="panel empty">
        <h2>Owner controls unavailable in this mode.</h2>
        <p>
          This adapter does not expose the reviewed Owner Interface controls.
        </p>
      </section>
    );
  return (
    <div className="workspace-stack">
      <div className="operations">
        <Panel
          title="Owner Interface"
          aside={
            <Badge
              tone={health.data?.availability === "AVAILABLE" ? "mint" : "rose"}
            >
              {health.isPending
                ? "Checking"
                : health.error
                  ? "UNAVAILABLE"
                  : (health.data?.availability ?? "UNAVAILABLE")}
            </Badge>
          }
        >
          <div data-testid="owner-interface-status">
            {health.error ? (
              <p role="alert">
                Health read failed: {(health.error as Error).message} Controls
                are disabled.
              </p>
            ) : health.data ? (
              health.data.availability === "AVAILABLE" ? (
                <p>
                  Kernel answered at {utc(health.data.observedAt)} · control
                  state <strong>{health.data.controlState ?? "unknown"}</strong>
                  {health.data.recoveryInProgress
                    ? " · recovery in progress"
                    : ""}
                </p>
              ) : (
                <p role="alert">
                  Not available ({health.data.reasons.join(", ") || "no reason"}
                  ). Controls are disabled — nothing is sent while the kernel
                  cannot receive it.
                </p>
              )
            ) : (
              <p>Checking the kernel Owner Interface…</p>
            )}
          </div>
          <button
            onClick={() => void health.refetch()}
            disabled={health.isFetching}
          >
            <RefreshCw size={14} /> Re-check
          </button>
        </Panel>
        <Panel title="Owner controls" aside={<ShieldAlert size={17} />}>
          <p className="quiet">
            Each control is a typed request executed by the kernel. The browser
            never changes state itself. Resume and Unhalt are guarded: the
            kernel decides from venue, protection and Risk evidence.
          </p>
          <div
            className="control-grid"
            role="group"
            aria-label="Owner controls"
          >
            {CONTROLS.map((c) => {
              const retry = pending.find(c.op);
              return (
                <div className="control-item" key={c.op}>
                  <button
                    className={c.danger ? "danger" : undefined}
                    data-op={c.op}
                    disabled={
                      !available ||
                      inFlight === c.op ||
                      ((c.op === "resume" || c.op === "unhalt") && recoveryBusy)
                    }
                    onClick={() => start(c)}
                  >
                    {inFlight === c.op
                      ? `${c.label}…`
                      : retry
                        ? `Retry ${c.label}`
                        : c.label}
                  </button>
                  <span className="quiet">
                    {retry
                      ? `Unresolved request ${retry.id.slice(0, 8)} — retry sends the same id. `
                      : ""}
                    {c.detail}
                  </span>
                </div>
              );
            })}
          </div>
          {!available && (
            <p role="status" data-testid="controls-closed">
              Controls fail closed until the Owner Interface health read
              succeeds.
            </p>
          )}
          {storageWarning && (
            <p role="alert">
              Browser storage unavailable: an unresolved request is kept only
              until this page reloads.
            </p>
          )}
          {outcome && (
            <div
              className={`control-outcome ${outcome.kind === "unknown" ? "unknown" : outcome.result.disposition.toLowerCase()}`}
              role="status"
              data-testid="control-outcome"
            >
              {outcome.kind === "unknown" ? (
                <p>
                  <strong>{outcome.op}: no answer — outcome unknown.</strong>{" "}
                  The same request id ({outcome.request.id}) is kept; retry
                  resolves it without a second execution.
                </p>
              ) : (
                <>
                  <p>
                    <strong>
                      {outcome.op}: {outcome.result.status}
                    </strong>{" "}
                    · {outcome.result.disposition}
                    {outcome.result.replayed ? " · recorded result" : ""}
                  </p>
                  <p>{outcome.result.message}</p>
                  <dl>
                    <dt>Control state</dt>
                    <dd>
                      {outcome.result.controlStateBefore ?? "?"} →{" "}
                      {outcome.result.controlStateAfter ?? "?"}
                    </dd>
                    <dt>Reasons</dt>
                    <dd>{outcome.result.reasons.join(", ") || "—"}</dd>
                    <dt>Request</dt>
                    <dd>{outcome.result.requestId ?? outcome.request.id}</dd>
                    <dt>Audit events</dt>
                    <dd>{outcome.result.auditEventIds.join(", ") || "—"}</dd>
                  </dl>
                </>
              )}
            </div>
          )}
          {unresolved.length > 0 && (
            <details className="space-top" open>
              <summary>Unresolved requests ({unresolved.length})</summary>
              <ul>
                {unresolved.map((e) => (
                  <li key={e.id}>
                    {e.action} · {e.id} · issued{" "}
                    {utc(new Date(e.t).toISOString())}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <p className="quiet space-top">
            Close one trade awaits the separate Owner Interface binding package.
          </p>
        </Panel>
      </div>
      <RuntimeStatus compact />
      <Decisions compact />
      <Investigations />
      <Dialog.Root
        open={!!confirming}
        onOpenChange={(o) => !o && setConfirming(null)}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="dialog-overlay" />
          <Dialog.Content className="evidence-drawer confirm-dialog">
            <div className="panel-heading">
              <Dialog.Title>Confirm {confirming?.label}</Dialog.Title>
              <Dialog.Close className="icon-button" aria-label="Cancel">
                <X size={20} />
              </Dialog.Close>
            </div>
            <Dialog.Description>{confirming?.ask}</Dialog.Description>
            <div className="composer-actions space-top">
              <Dialog.Close className="button secondary">Cancel</Dialog.Close>
              <button
                className={confirming?.danger ? "danger" : "primary"}
                onClick={confirm}
                disabled={!available}
              >
                Send {confirming?.label} to the Owner Interface
              </button>
            </div>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
