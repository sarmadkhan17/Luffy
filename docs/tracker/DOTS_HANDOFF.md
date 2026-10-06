> Current OWN-03 update (2026-10-06): CLOSED at architecture/offline scope. Exact approval/autonomy evidence: [REPORT.md](evidence/own03-boundary-sol/REPORT.md). OWN-04 is next GUI dependency recommendation only, unselected. GUI-01 still needs ACC-03 and OWN-04. No other owner-interface row newly closes; no activation authorized. Earlier queue statements below are historical.

> Current owner decision (2026-10-06): SEC-01 and RUN-01 are DEFERRED / NOT_REQUIRED_FOR_CURRENT_RELEASE, excluded from current critical-path blockers. Evidence, unresolved findings, completed implementation and closing conditions are preserved. Revisit before future production/live activation requiring their guarantees. OBS-05 remains terminal CLOSED; no successor is selected. DATA-08 evidence mapping is recommended; GUI completion and offline testing are eligible under existing contracts. Earlier selection/blocker statements below are historical. See canonical YAML, STATE and NEXT.

> Current terminal update (2026-10-06): PERF-03 is BLOCKED at its unchanged capture-evidence gate; narrow offline correction committed at a799e6c365074cab5597a80261bbdb9c3dda3b12. Exact result: [terminal.yaml](evidence/perf03-capture-r1/terminal.yaml). OBS-01 is the single selected diagnosis-only successor by safe-runtime critical path; not started. PERF-01 remains BLOCKED/UNKNOWN. Earlier selection statements below are historical; no runtime/provider/trading activation is authorized.

# Dots handoff — LUFFY tracker-based work

## Start here

This is an engineering handoff, not a trading component or permission to run LUFFY.
Read SDD.md, STATE.yaml and NEXT.yaml first, then this directory's README, the
canonical tracker YAML and the latest attributed observation. SDD remains the
only architecture authority. The tracker is its detailed evidence/work ledger.

OPEN-09 control-plane adoption is CLOSED with explicit owner acceptance. YAML is the canonical detailed ledger; the read-only Dashboard Tracker is the primary working view. Markdown is optional generated reference. Existing XLSX is DEPRECATED / NON-AUTHORITATIVE history, preserved unchanged and excluded from adoption/synchronization checks. Engineering owns YAML and optional Markdown updates. No Tracker mutation API/UI is approved.

## Latest result, not a new run

Implementation baseline: 9385f792f32277bb0b826dba83247b8f5ccf1f8a.
Run: scheduling-observation-prep-r1-idi3eex0-kernel-1012489.
Source: owner-pasted result, not fresh VM verification.

WORK-01 is BLOCKED. The four harness conflicts were reported fixed.
The first cycle finished during shutdown at 259.776 seconds after a stop request
at 240.033 seconds against the existing 240-second limit. Zero complete
observation-window cycles and no recurring-cycle distribution were measured.
Kernel and task observers stopped; Dashboard did not launch.
FROZEN, operator hold, recovery latch and watchdog.off were retained.
The final cleanup correction was fixture-tested after execution; it was not
followed by a new observation run.

Exact VM reports and harness hashes must be read locally before treating their
details as independently verified. The small attributed summary is in evidence/.

## One active item and the next candidate

Single selected NEXT item: PERF-01 — SELECTED_NOT_STARTED / DIAGNOSIS_ONLY.
Attribute evidence-store waits and repeated
market/evidence work from the retained run before choosing one coherent change.
PERF-02 stays narrowly CLOSED: observer imports are light. This does not prove
Kernel latency improved. PERF-03 stays OPEN: Attention capture took 535.604 ms
against the 50 ms advisory budget.

Research did NOT reach its initial 300-second wake in the latest run.
Do not repeat an earlier busy-gate refusal as an observation from this run.

Do not start all OPEN or EVIDENCE_TO_MAP items. Those statuses are neither
missing-code findings nor execution permissions.

## Adoption and next-task boundary

The approved tracker mapping is adopted without changing any of the 176 IDs or row closing conditions. STATE preserves the older 958c9eb/266.7s attempt as historical and records the latest owner-reported 9385f792/259.776s attempt separately. Repository control changes do not establish a later observed runtime revision.

The Dashboard Tracker adds the owner-approved tenth route through the existing authenticated Owner API, reading fixed repository YAML/NEXT paths only. Missing/malformed/duplicate-ID/unsupported-status or inconsistent NEXT data produces an explicit error; no fabricated or empty successful ledger. Focused in-process/API/frontend tests and build are implementation evidence only. No service startup, deployment or browser observation is claimed.

PERF-01 remains OPEN in the tracker and SELECTED_NOT_STARTED / DIAGNOSIS_ONLY in NEXT. No implementation fix is selected. No runtime execution, new observation, threshold change, workload reduction, provider enablement or trading activation follows from adoption. Do not use this handoff to start services.

## Engineering discipline after import

Every action has one tracker ID, parent requirement, reason, assignee, base
revision, allowed files/actions and closing condition. New findings receive
linked IDs with evidence and an origin classification; unknown origin stays
unknown. Preserve prior closure and acceptance versions.

Update canonical YAML through engineering/control-plane changes. Dashboard Tracker reads it directly; regenerate optional Markdown when maintained. Historical XLSX is deprecated/non-authoritative and must not be treated as another writable or synchronized authority.

Use existing reports/tests before re-running reviews. Separate implementation,
fixture tests, real runtime evidence and owner GUI approval. A valid empty or
warm-up result does not prove an unexercised capability. No agent grants owner
visual approval. Keep the GUI backlog visible alongside backend work.

Default next investigation is read-only PERF-01 attribution. Inspect retained
timings and exact code/data boundaries; do not assume a database wait, GIL cost
or CPU cause from wall-minus-thread-CPU. Produce one evidence-supported change
boundary and its test/observation closing condition before implementing it.

## Permission boundary

No automatic ACTIVE/resume, venue entries, discretionary orders, live-account
switch, provider enablement, paid calls, risk/threshold change, destructive
cleanup, migration, service restart, new worker enablement or new infrastructure.
The old FROZEN observation authorization is not permission for unlimited reruns.
Request the specific operational authorization before a new run.

Fresh venue state must be established by existing authorized read-only mechanisms
before an operational handoff. Historical SOL/AVAX quantities are not current
venue proof. Do not clear safety incidents or the recovery latch by hand.

Do not run the desktop app as root, copy credentials into chat, or assume prompt
instructions create OS-enforced isolation. Local-computer access is broader than
a repository permission; review the app's permission controls.

## VM connection guide (official documentation checked 2026-10-05)

The official Linux desktop preview lists Ubuntu 24.04 and 26.04 support and
provides architecture-specific .deb packages:
https://learn.chatgpt.com/docs/linux/linux-app

Use uname -m to choose x64 versus ARM64. Download only through the official page.
Install the exact downloaded package with apt, open ChatGPT as the normal user
and sign in to the account that has Dots access.

Dots local access is optional and initially off. Connect the Ubuntu computer
from the desktop app, review the permission request and confirm Allow access.
Local work runs in separate Work/Codex tasks. Start with the local verification
above; do not assume this existing cloud conversation gains VM access.
https://help.openai.com/en/articles/20001530-getting-started-with-your-dot

UI wording and rollout may differ. If the connection control is absent, report
the screen/app version rather than installing unofficial wrappers or exposing
SSH/ports. The VM and desktop app must stay available for tasks requiring it.

No dot or background task was launched by publishing this file.
