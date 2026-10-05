# Dots handoff — LUFFY tracker-based work

## Start here

This is an engineering handoff, not a trading component or permission to run LUFFY.
Read SDD.md, STATE.yaml and NEXT.yaml first, then this directory's README, the
canonical tracker YAML and the latest attributed observation. SDD remains the
only architecture authority. The tracker is its detailed evidence/work ledger.

Full YAML/Markdown/XLSX artifact import is pending until verified against
bundle-manifest.json. Do not claim all three files are in Git just because this
handoff exists. Owner approved importing those files and configuring local access.
Publication does not approve every draft verification detail or all future work.

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

Current item: OPEN-09 — repository import and local Dots setup.
Next diagnostic candidate: PERF-01 — attribute evidence-store waits and repeated
market/evidence work from the retained run before choosing one coherent change.
PERF-02 stays narrowly CLOSED: observer imports are light. This does not prove
Kernel latency improved. PERF-03 stays OPEN: Attention capture took 535.604 ms
against the 50 ms advisory budget.

Research did NOT reach its initial 300-second wake in the latest run.
Do not repeat an earlier busy-gate refusal as an observation from this run.

Do not start all OPEN or EVIDENCE_TO_MAP items. Those statuses are neither
missing-code findings nor execution permissions.

## Initial local task

1. Confirm the task is executing on the intended Ubuntu VM, as the normal owner
   user, not on the cloud computer or Windows host. Record hostname, uid, exact
   worktree path, branch/HEAD and Git status. Do not print environment secrets.
2. Keep other engineering writers paused. Detect existing services and observers
   without changing them. Preserve untracked checkpoints and Graphify changes.
3. Import only the three tracker files from the owner-provided bundle after
   verifying every byte count/hash in bundle-manifest.json. Do not replace a
   differing existing tracker silently. Stage explicitly; no git add -A.
4. Verify YAML has 176 unique IDs, 29 GUI items, one IN_PROGRESS item (OPEN-09),
   WORK-01 BLOCKED, and unchanged narrow closures. Verify matching exports.
5. In a docs-only change, link the tracker from STATE/NEXT without rewriting
   historical verdicts. Preserve the latest blocked result and label it
   owner-reported until local source artifacts are checked. Record which exact
   evidence was independently read.
6. Commit/push only the coherent tracker/doc changes. No force push, historical
   branch merge, automatic deployment or runtime-state changes.
7. Report the commit, file hashes, current/next item and all limitations. Local
   access and artifact publication do not mean the product is complete.

## Engineering discipline after import

Every action has one tracker ID, parent requirement, reason, assignee, base
revision, allowed files/actions and closing condition. New findings receive
linked IDs with evidence and an origin classification; unknown origin stays
unknown. Preserve prior closure and acceptance versions.

Update the canonical YAML first. Markdown and Excel are export snapshots, not
separate writable authorities. Regenerate and verify views, or mark them stale
explicitly until synchronized. Do not silently let three status copies diverge.

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
