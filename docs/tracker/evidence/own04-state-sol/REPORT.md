# OWN-04 declared and observed state

Scope: OWN-04 only, architecture/offline GUI proof. Source identity is pinned in
source-manifest.json, not loaded deployment identity. SEC-01, RUN-01 and GOV-07
remain deferred with their original future activation conditions. No Kernel,
production Dashboard, trading or provider was launched. Browser checks use the
isolated temporary-journal FastAPI fixture and fake IPC/chat.

## Evidence mapped before fixes

| Distinction | Existing evidence | Demonstrated gap / bounded correction |
| --- | --- | --- |
| Configured vs observed | System source-map edges are declared architecture; no events are animated. owner_reads.observed_flows counts exact retained journal records and returns unobserved edges explicitly. Bootstrap's owner-interface configured flag disclaims health; IPC availability is separately polled. | Add explicit configured enabled/disabled/UNKNOWN provenance to System; never turn configuration into health. |
| Running vs stopped | KernelInstance already owns a lifetime lock and binds PID/start ticks and heartbeat instance. SafetyObserver independently detects missing/stale/restarted producers. | System called every timestamped heartbeat ACTIVE without consulting process evidence. Add an owner read of existing lock/identity and /proc ticks; no lock/identity creation. Released lock means STOPPED; absent/mismatched/unverified evidence means UNKNOWN. Legacy/unbound heartbeats cannot establish RUNNING. |
| Publication vs successful work | Heartbeat.validate_heartbeat and SafetyObserver enforce identity, sequence, chronology and successful-cycle age. | Owner API used publication time alone. Validate the existing heartbeat contract and use the older of publication/work for freshness; separately expose process, work and reported control. SAFE Supervisor passes are historical outcomes, not ACTIVE work. |
| Waiting vs failed vs intentionally disabled | Health-file status and config are already independent records; workers record waiting/error/disabled. Diagnostics exposes watchdog.off independently. | System renders WAITING, FAILED, DISABLED and STOPPED reports explicitly. Diagnostics labels status as last reported, alongside age/quality. No status or disable flag implies a running daemon. |
| Fresh vs stale | Server uses shared truth for heartbeat; HealthStatus labels stale health as Last reported. Protection already expires locally. | Graph/IPC and Overview account/heartbeat/owner-attention could retain fresh presentation after lost polls. Add observation deadlines, local clock ticks, polling and disconnect invalidation. Diagnostics accepted future/bool/nonfinite collector timestamps; use shared truth validation. |
| Available vs UNKNOWN | No-telemetry nodes, null unavailable values, unknown journal activity health, guarded IPC reads and owner read-only query envelopes already differ. | Preserve those distinctions. Frontend also refuses legacy ACTIVE work health for components whose actual work is not proven; only the responding Dashboard has current activity evidence on this endpoint. |
| Control vs actual work | read_control reads state_kv; journal decisions/equity/research are already labelled activity records, not health. OwnerService/queries expose exact record hashes and NOT_ASSESSED freshness. | Overview explicitly says stored control permission, not evidence of running work. API control timestamp is labelled read_time; retained ACTIVE permission remains independent of STOPPED/UNKNOWN work. |

The map used the source and retained contracts before implementation; intake.yaml
preserves the original row conditions and dependency graph. The local Claude
availability probe returned Not logged in; SOL executed this bounded task directly.

## Actual gaps and change

Only the demonstrated display/reader gaps above were repaired. No worker,
heartbeat producer, safety policy, risk/control state, configuration, approval
boundary or operational authority changed. The reader may briefly probe an
existing advisory lock, releases it immediately, and never creates identity files.
Observation is a snapshot; a later death/restart requires a later observation.
Cached observations expire locally, and failed reads become unavailable/UNKNOWN.
Retained records remain historical evidence; they are never promoted into animated
or ACTIVE work. Backend responses to old frontend contracts remain conservative.

The first adverse Python run had four fixture teardown failures because Journal
has no close() method; the assertions passed and that invalid cleanup was removed.
The original frontend run exposed one ordering regression introduced by this
change (stale-with-no-time should stay STALE); it was corrected. Initial logs are
retained. Two component QueryClient failures and two ticker-contract failures
reproduce against an isolated git archive of the intake revision; these are not
OWN-04 regressions and were explicitly excluded from final passing runs. Legacy
owner-interface R3–R6 tests cannot collect because their removed HTML fixture is
absent. A combined Python run exhausted the default 1024 descriptor limit; final
runs use a shell-local 8192 limit and separate groups. The browser executable was
found in the existing shared cache and explicitly supplied after the initial
missing-path run. No dependencies were downloaded.

## Terminal decision and impact check

OWN-04 is **BLOCKED**, with its demonstrated implementation and offline behavior
proof complete. The unchanged dependencies are MON-01 and MON-05. MON-01's
independent observer contract maps to safety.py, watchdog.py and the passing
heartbeat/restart/restamp/malformed/mutation cases in the monitoring suite; its
canonical row remains EVIDENCE_TO_MAP. MON-05 is not fully demonstrated: retained
PERF-01 evidence still records unattributed wrapper time, instrumentation overhead,
exact database/GIL/scheduler waits, child phases and recurring-cycle attribution
gaps. Showing UNKNOWN/unavailable measurement honestly does not close the exact
operation-attribution requirement. Only OWN-04's workflow row changes; monitoring
rows are not silently closed, deferred, or weakened. No live Kernel/provider/trading
is required to reconcile those architecture/offline dependencies.

| Impacted row | Result |
| --- | --- |
| GUI-01 Overview | OWN-03 remains satisfied. ACC-03 and dependency-blocked OWN-04 remain. New control/process/work/expiry evidence is mapped; account economics, global signals, exact Needs You navigation and per-screen evidence still need reconciliation. No visual approval is inferred. |
| GUI-06 Operations | Stored permission and journal work remain distinct. Existing operations/owner-read regressions apply; full scans/priorities/refusals/work evidence is not newly closed. |
| GUI-07 Live System | Adds matching process evidence, conservative unknowns, explicit worker reports and browser stale/disconnect proof. Declared edges still differ from observed record flows; full screen condition remains independent. |
| GUI-09 Diagnostics | Health reports are historical, future/malformed clocks fail closed, watchdog disable flag stays distinct. Full phase/resource/provider cost attribution remains unavailable where unmeasured. |
| GUX-02 / GUX-04 | Local expiry and failure handling improved; no whole-row closure or duplicate/reconnect acceptance inferred. |
| MON-01 | Exact offline heartbeat observer behavior mapped; status/closing condition unchanged. |
| MON-05 | Exact retained measurement gaps preserved; status/closing condition unchanged. |

Next GUI item: **ACC-03 evidence mapping**, recommendation only, unselected. It is
an independent remaining GUI-01 prerequisite while MON-01/MON-05 await their own
reconciliation. No new work outside OWN-04 was implemented. Loaded deployment
identity remains UNKNOWN, and separate owner visual acceptance remains required.

Concurrent RISK-02 work was committed independently during this task. Its row and evidence are preserved unchanged; control-validation compares the OWN-04 implementation parent, while intake retains the initial source revision.
