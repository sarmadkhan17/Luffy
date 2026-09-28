# LUFFY-OWNER-INTERFACE-GATEWAY-V1 (revision 6, Opus independent review PASS)

- Date: 2026-09-28
- Implementer: Claude (Opus 5.5), revisions 1–5; Astra, revision 6 at the
  owner's explicit instruction. Independent reviewer for revision 6: **Opus,
  PASS (2026-09-28)**. Earlier Astra reviews blocked revisions 1–5; revision 5 left the
  single reservation-write failure classification blocker addressed here.
- Worktree: `.claude/worktrees/owner-interface-gateway`, branch
  `owner-interface-gateway-v1`, base `main` `acbd0ce`. **Not committed, not
  deployed, no restart, watchdog untouched.** Production DB, venue, `.env`,
  sockets and processes were not modified; all evidence comes from temp
  journals, temp sockets, a recording venue double and a Node harness.
- `graphify-out/` was restored to HEAD (generated churn is excluded from the
  package; run `graphify update .` at merge time).

## Verdict

Revision 6 is implemented by Astra at the owner's explicit request. **Opus
independent review: PASS** (see §Owner decisions and review). No
implementation self-certification is an independent package PASS. Uncommitted, undeployed; no restart, watchdog change,
production mutation, or live provider connection.

The sole remaining revision-5 blocker was a definitive reservation-failure
refusal while another delivery of that request could execute. Revision 6
reconciles the durable record after that failure and otherwise returns an
unresolved outcome. Schema v3, successful reservation/execution paths, panic
ordering, control fences and adapter architecture are unchanged.

The owner decided §R5-L items 1–4 (below). Deployment mode is **ATOMIC**
(kernel and dashboard together), after the IPC-directory prerequisite below and
the owner's separate deployment authorization.

## Owner decisions and review (2026-09-28)

Opus independent review of revision 6: **PASS** — reservation race, result
semantics, adapter retry/idempotency, crash safety and R5 regression all pass;
no remaining implementation blockers. Non-blocking notes (not reopened): the
unknown-outcome wording "retry only the same request ID" is not actionable for
a Telegram/CLI owner re-typing a command (a new logical request; containment is
idempotent and a second recovery is refused while one runs); after the 120 s
OpenClaw confirmation TTL the same applies.

Owner decisions on §R5-L:

1. **IPC directory:** `owner_interface.ipc_dir: /home/sarmad/.luffy/run`
   (persistent; directory 0700, socket and channel keys 0600). Linger is
   **not** enabled for Luffy. The kernel creates `run/` (0700) but not its
   parent: before the first start, create `/home/sarmad/.luffy` owned by
   sarmad and not group/other writable (e.g. `install -d -m 0700
   /home/sarmad/.luffy`). A missing or insecure directory makes the owner
   interface fail closed (UNAVAILABLE); trading continues.
2. **Kernel-only authority:** ACCEPTED. Panic, close-trade and market-type
   require a running kernel; unavailable IPC fails closed; no adapter-local
   fallback.
3. **Same-uid model:** ACCEPTED for V1 as a *trusted same-user application
   boundary*, not OS isolation (§Q.6 residual stands).
4. **Panic semantics:** ACCEPTED. Containment at acceptance, then
   kernel-owned flatten/drain; an older recovery/resume cannot defeat an
   accepted panic.

§R5-L item 5 (production `main` still carries the old chat ops path) is
resolved by deploying this package.

Residual limitation stated plainly: channel keys stop *accidental* or
*low-privilege-path* impersonation and make a fabricated listener
detectable, but any process running as the kernel's uid can read the keys —
and the journal and credentials file. True authority separation needs the
dashboard and provider bridges under separate OS users (§Q.6).

## Revision 6 — reservation failure reconciliation

### Root cause and narrow fix

A reads no row; B reserves the same id; A's INSERT fails. The former exception
branch returned REFUSED solely because A had not reserved, despite B still
being able to execute. The browser then discarded the logical request id.

`OwnerService.reserve` now calls `_reconcile_reservation_failure` on that
exception. It reuses `_existing` to recover the durable answer:

| Observable record | Answer |
|---|---|
| RESERVED / running PENDING | IN_PROGRESS |
| DONE | Exact recorded result, with `replayed=true` |
| Unresolved PENDING from a previous boot | OUTCOME_UNKNOWN |
| Abandoned RESERVED from a previous boot | Existing abandoned-before-execution settlement |
| No row visible, or reconciliation cannot read/settle the journal | OUTCOME_UNKNOWN / persistence_unavailable |

Absence is not a proof against a concurrent delivery. This branch never
claims, executes, generates another id, or automatically retries the operation.
No schema migration or successful-path database call was added.

### Shared result and adapter semantics

- REFUSED: proven not to execute; a terminal refusal may clear the browser id.
- IN_PROGRESS: the same request is durably reserved/running; keep its id.
- OUTCOME_UNKNOWN: execution cannot currently be established; it may have run
  or may still run. Keep the id and resolve it using that id.
- COMPLETED disposition: durable final result available; normal completion
  cleanup applies.

The common renderer now explicitly says an unknown request may still run.
Dashboard, Telegram, IPC, CLI and the fixture provider adapters use the same
contract. Browser cleanup already preserves IN_PROGRESS and OUTCOME_UNKNOWN;
new Chromium reload regressions prove it. Telegram redelivery retains its
update-derived id. OpenClaw/WhatsApp remain NOT_CONFIGURED.

### Regression and negative-control evidence

`tests/test_owner_interface_r6.py` gates the exact A/B race with Events, tests
RESERVED/PENDING/DONE/terminal-refused/absent/unreadable records, then checks
one execution and exact same-id replay. A separate signed-IPC regression
runs the full race through the real client/listener.

`tests/test_owner_dashboard_browser.py` adds typed-result reload tests for
IN_PROGRESS, OUTCOME_UNKNOWN, REFUSED and successful completion.

Five new negative controls restore immediate REFUSED, skip durable
reconciliation, render unknown as Nothing changed, clear an unknown browser
request, or allocate a fresh id on retry. Each must fail on the incorrect
result or request identity, rather than a runtime/import error.

The revision-5 reservation-write-failure test now expects OUTCOME_UNKNOWN
while still proving that the failed delivery executes nothing. This is the
only changed existing outcome expectation.

### Validation

- New race tests plus Chromium suite: **26 passed** (11 R6 service/IPC/
  Telegram cases and 15 Chromium cases).
- Combined focused suites: **808 passed**, including all revision-5 gateway,
  recovery/concurrency, chat, socket, authorization, panic and audit regressions.
- Five added semantic negative controls passed (**55 gateway negative-control
  cases total**); 17 tests were added overall.
- Actual subprocess crashes: reserved → abandoned_before_execution; claimed
  or transition-applied/result-missing → OUTCOME_UNKNOWN; result-persisted →
  exact replay. All four added zero control transitions on retry.
- Full suite: **3279 passed, 50 failed, 12 errors**, 14 m 51 s. Compared
  against the existing independent clean-base and revision-5 XML evidence:
  **zero differences** in failing/error ids, status, normalized message or
  traceback. The clean base was not rerun. Both host-timing qualification tests
  pass here, explaining the implementer's earlier 52-versus-50 discrepancy.
- `git diff --check`: clean.

Sequential benchmarks ran after pytest, first on the reviewed revision-5
snapshot, then on revision 6 (500 status requests, 200 freeze/halt requests):

| Measurement | Revision 5 | Revision 6 |
|---|---:|---:|
| Status p50 / p95, ms | 2.347 / 4.018 | 2.419 / 5.040 |
| Freeze/halt p50 / p95, ms | 7.114 / 11.643 | 6.792 / 12.552 |
| Target 20 Hz: achieved requests/s | 18.7 | 18.9 |
| Synthetic cycle polling/quiet ratio | 0.998× | 0.890× |
| Idle listener CPU over 3 s | 0.00056 s | 0.00058 s |

The successful path adds no query, write, lock or adapter call. Median latency
is similar in these samples; the host-sensitive cycle ratios do not establish
an improvement or qualify production performance.

Evidence directory: `/tmp/astra-owner-r6-gxebr7gv/` — `new-tests.log`,
`focused.log`, `full.log`, `full.xml`, `failure-comparison.json`,
`probe_crash.log`, `collected-gateway.txt`, `benchmark-r5.json`,
`benchmark-r6.json`, and `revision6-code.patch`.

No remaining implementation blocker from W is known after these tests.
Opus independent review: PASS. Owner decisions recorded above; deploy
kernel/dashboard atomically once deployment is authorized.

All tests run in `/tmp/astra-owner-r6-gxebr7gv/candidate`, a disposable snapshot,
with temporary journals/sockets and fake venues. Chromium routes its test
pages locally. The Python guard rejects production/candidate writes and
network connect/bind; browser pages are intercepted fixtures. Production is
not used as a fixture. The AST-only Graphify update completed; its generated
output is archived outside the package rather than included as source churn.

### Revision-6 changed files

- `trader/owner/service.py`
- `trader/owner/contract.py` (contract documentation only)
- `trader/owner/adapters/render.py` (shared unknown-outcome wording)
- `tests/test_owner_interface_r6.py` (new)
- `tests/test_owner_interface_r5.py` (corrected reservation-failure expectation)
- `tests/test_owner_dashboard_browser.py`
- this report

The revision-5 sections below are historical implementation evidence. In
particular, their old reservation-write-failure REFUSED classification is
superseded by this revision's unresolved result contract.

## Revision 5 — fourth review blockers (R5-A…N)

### R5-A. W1 panic ordering fix

Reproduced defect: FROZEN → an old resume pauses inside its recovery pass →
panic accepted → the old recovery's CAS still matched its own watermark →
ACTIVE; the drain then read that activation as "newer intent" and kept ACTIVE.

Fix (`OwnerService._panic`, `Kernel._drain_panic`):
1. **Acceptance contains and advances the fence.** Under the control fence,
   accepting a panic applies an owner hold: FROZEN, or a HALTED hold when
   already HALTED (never de-escalates). That hold is an intent event, so the
   control-intent fence advances.
2. **Old work keeps its old identity.** An old recovery that already took
   intake is bound (Supervisor `set_if_current`) to its containment event;
   the panic hold moved the state off RECOVERY, so its activation CAS fails.
   An old recovery not yet at intake carries its admission watermark
   (`expected_intent_event_id`, revision 4) and is refused as superseded.
   A CAS that wins the fence *before* acceptance is simply followed by the
   panic's hold (ACTIVE→FROZEN): the fence totally orders them.
3. **The drain uses the panic's identity, and only owner intent is newer.**
   The pending panic is `{request_id, intent_event_id = the acceptance hold}`.
   After the flatten, only intent events after that hold whose actor is an
   owner actor (`operator`/`dashboard`/`chat` — an owner resume, halt,
   freeze, panic) count as newer. Supervisor/system transitions, such as the
   completion of older work, never do. No newer owner intent → contained
   (HALTED kept, otherwise FROZEN). Newer owner intent → its state stands
   (normal rules). The flatten runs either way.

Tests (`tests/test_owner_interface_r5.py`): A recovery paused → panic →
recovery finishes; B panic accepted synchronously just before the recovery's
CAS; C panic racing the CAS while the verified Risk proof is held (fence
orders it; contained either way); D panic → a system-actor ACTIVE lands
("older completion") → drain freezes; E genuinely newer owner resume after the
panic stands, and a panic while HALTED never de-escalates.

### R5-B. Owner-intent identity strategy

Owner intent is identified by control-event ids of `state_change`/
`state_hold` events written under the control fence (a total order). Every
owner request that can change containment records one: freeze/halt (their
transition or owner hold), panic (its acceptance hold), resume/unhalt (the
Supervisor intake event, actor = owner). Every activation is a CAS bound to
a specific earlier event id; a recovery's admission watermark is stored in its
reservation (`owner_requests.intent_event_id`). "Newer owner intent" =
an owner-actor intent event with a larger id. Timing plays no role.

### R5-C. W2 cross-tab persistence design (option B, no lease)

One localStorage key per unresolved request: `luffy.owner.req.<id>` →
`{action, id, t}`. Creating writes only its own new key; completing removes
exactly its own key; lookups scan fresh every time; the oldest unresolved
request for an action is reused. There is no whole-map read-modify-write, so
no ordering of two tabs' reads and writes can overwrite or erase another
tab's entry, and two tabs creating the same action concurrently both keep
their ids. localStorage is not assumed to be locked. No controlling-tab lease
is used, so there is no ownership transfer or lease expiry to get wrong:
every tab may issue controls, and every tab reuses any tab's unresolved id.
A failed storage write keeps the entry in memory for that page and warns the
owner once. A `storage`-event/BroadcastChannel sync is unnecessary for
correctness, because every lookup reads the shared store fresh.

### R5-D. Two-tab browser results

`tests/test_owner_dashboard_browser.py`, headless **Chromium 151** via
Playwright. Two same-origin pages in one browser context share real
localStorage. Tab A opens tab B (`window.open`, synchronous access) and hooks
its *own* Storage writes, so B's code runs between A's read and A's write:
the lost-update window of parallel tabs. The pass criterion is
implementation-independent: every id a tab sent without a definitive answer
is re-sent by a freshly loaded tab (a reload).

| Scenario | Current (rev 5) | Rev-4 whole-map block (fixture) |
|---|---|---|
| s1 two tabs, different requests, reload recovers both | pass | pass (sequential) |
| s2 completion interleaved with sibling creation | pass | **fails** (sibling id erased) |
| s3 simultaneous creation, same action | pass (both ids kept) | **fails** (one id lost) |
| s4 stale tab write interleaved with newer write | pass | **fails** (newer id overwritten) |
| s5 double-click + reconnect reuse one id | pass | pass |
| mutant: completion clears its siblings | — | **fails** |

The Node harnesses (revisions 2–4) still run the same block for the
single-tab behaviours (unknown outcome keeps id and time, containment never
blocked by recovery, per-trade close ids, storage-failure warning).

### R5-E. W3 reservation / admission model

`owner_requests.state`: **RESERVED → PENDING → DONE** (schema v3 adds
`reserver`, the reservation token, and `intent_event_id`).

- **Reserve** at the earliest admission point: Telegram dispatch (listener
  thread, before queueing), IPC receipt (inside `execute` / before a
  class-slot refusal), or service entry. `INSERT` on the primary key with a
  random token. The first delivery wins; nothing executes an id that is not
  reserved.
- **Only the reservation owner progresses it:** claim is
  `UPDATE … SET state='PENDING' WHERE state='RESERVED' AND reserver=token`,
  plus intake audit, in one transaction.
- **A duplicate** is answered from the row: RESERVED/PENDING in this boot →
  **IN_PROGRESS**; DONE → the recorded result (**COMPLETED**, or the recorded
  REFUSED); PENDING from a dead boot → **OUTCOME_UNKNOWN**; claimed here but
  unrecorded → **OUTCOME_UNKNOWN**. It is never definitively refused because
  execution has not started. Duplicates no longer wait on the executor's lock.
- **Definitive REFUSED** only when the request can never execute:
  unauthorized, malformed, stale (recorded at reservation), or refused by the
  reservation owner itself (busy, second recovery, superseded), which is
  recorded DONE so every other delivery sees the same answer.
- **Result vocabulary** (`OwnerResult.status` + `disposition`): REFUSED,
  IN_PROGRESS, OUTCOME_UNKNOWN, COMPLETED (any recorded final outcome),
  UNAVAILABLE. Revision-4 names `PENDING`/`ERROR` are aliases of
  IN_PROGRESS/OUTCOME_UNKNOWN. Malformed requests are now REFUSED, not
  ERROR: they can never execute.
- **A control never runs without its durable record:** a reservation write
  failure → REFUSED `persistence_unavailable`, nothing executes; a claim
  failure leaves the reservation unclaimed (never executes).

### R5-F. Crash / idempotency matrix

| Point of failure | Retry of the same id answers | Can it execute again? |
|---|---|---|
| Rejected before reservation (auth, malformed) | REFUSED (not recorded; never reveals a record) | no, never executable |
| Stale at admission | REFUSED `request_stale` (recorded DONE) | no |
| Reservation write fails | REFUSED `persistence_unavailable` | no, nothing reserved |
| Reserved, waiting (lock / queue / slot) | IN_PROGRESS | only its owner may claim |
| Reserved, owner refuses (busy, second recovery, superseded) | recorded REFUSED | no |
| Kernel restart after reservation, before claim | REFUSED `abandoned_before_execution` (settled once, recorded) | no; it provably never ran |
| Claimed, then kernel restart | OUTCOME_UNKNOWN `outcome_unknown_after_restart` | no |
| Claimed, result persistence fails (same boot) | OUTCOME_UNKNOWN `outcome_unknown` | no |
| Client timeout after reservation | OUTCOME_UNKNOWN → IN_PROGRESS → recorded result | no |
| Duplicate after final result | recorded result (COMPLETED / recorded REFUSED) | no |

Tests: `test_w3_ipc_duplicate_before_claim`,
`test_w3_telegram_duplicate_before_execution`,
`test_w3_service_busy_while_same_id_reserved`,
`test_w3_restart_after_reservation_before_execution`,
`test_w3_restart_after_claim_is_outcome_unknown`,
`test_w3_timeout_after_reservation`, `test_w3_reservation_failure_never_executes`,
`test_w3_refusals_before_reservation_stay_definitive`; adapter admission overlap
= `test_owner_interface_r4::test_w4_admission_refusal_consults_the_record`
(a single recovery slot held by the executor: the duplicate gets
IN_PROGRESS, the completed request is replayed, and a never-seen request is
REFUSED and stays refused).

### R5-G. Audit-buffer bound fix

The buffer never exceeds `audit_buffer_max`. At the cap, the oldest row not
in a committing batch is evicted. If every row is in flight, the **new** row
is the one discarded; in-flight rows are never discarded. Exactly one row is
counted per loss, and the count is written as an `AUDIT_DROPPED` accounting
row when the journal accepts writes again. Control requests are unaffected:
their durable record (reservation, claim, completion and their audit rows) is
written synchronously and a control cannot run without it (R5-E).
Tests: `test_aud_cap_minus_one_and_cap_hold_everything`,
`test_aud_cap_with_all_rows_in_flight` (buffer stays at the cap; all in-flight
rows persisted; drops counted exactly), `test_aud_concurrent_append_and_flush_failure`
(rolled-back flush during appends: in-flight rows restored,
persisted + dropped = emitted). The invariant `len(buffer) ≤ cap` is asserted
after every append.

### R5-H. Focused test results

- `tests/test_owner_interface_r5.py` 26 passed; `tests/test_owner_dashboard_browser.py`
  9 passed (real Chromium).
- All gateway, touched dashboard/browser, chat-safety, recovery/concurrency,
  panic/manual-close and persistence suites: **791 passed** (`test_owner_*`,
  `test_owner_recovery*`, `test_kernel_boot_recovery`,
  `test_activation_risk_baseline*`, `test_chat_agent`, `test_dashboard_auth`,
  `test_supervisor`, `test_attention_view`, `test_rent_wiring`,
  `test_manual_close`, `test_control_state_recovery`, `test_attention_telemetry`,
  `test_entry_control_fence`).
- Contract changes reflected in earlier tests (intended, listed): duplicates
  answer IN_PROGRESS instead of waiting (`test_i_concurrent_duplicates…`,
  r4 W4); malformed requests are REFUSED (`test_m3`, `test_m4`); claims need a
  reservation (`test_j2`); a panic's transition event is its acceptance hold
  (`test_w5_panic…`); negative-control mutants re-pointed at `_existing` /
  `reserve`; JS storage stubs gained `key/length/removeItem`.

### R5-I. Full-suite comparison

Candidate (worktree, `--continue-on-collection-errors --tb=line -rfE`):
**3260 passed, 52 failed, 12 errors** (13 m 17 s). A clean `git archive acbd0ce`
copy ran the same 64 failing/erroring files with identical flags: 52 failed,
12 errors (plus 273 passing tests in those files).

| Comparison | Result |
|---|---|
| Failing/erroring test ids | 64 vs 64: none only-candidate, none only-baseline |
| FAILED vs ERROR status per id | 0 differences |
| Normalized error message per id | 0 differences (21 raw differences were pytest truncating the summary line at different characters of the two root paths; after normalizing the truncated path: 0) |
| Normalized `--tb=line` traceback lines (untruncated) | 0 only-candidate, 0 only-baseline |

The failures are the known environmental ones (untracked research
artifacts/data absent from fresh trees, pre-existing `naked_position`,
`partial_close`, `cognition_contracts`). In this run the two host-timing
`quick_host_run` tests failed on both sides alike. Comparison file (job
temp, not in the package): `$CLAUDE_JOB_DIR/tmp/oig5_failure_comparison.json`.

### R5-J. Negative controls (the seven required, plus extras)

Each was confirmed to fail at the semantic assertion, not incidentally:

| # | Required control | Implementation | Caught by → failing assertion |
|---|---|---|---|
| 1 | panic fence omitted | `_nc_panic_fence_omitted` (rev-4 acceptance: watermark only) | check A → old resume ACTIVATED; check B → ACTIVATED/not FROZEN |
| 2 | old recovery completion counted as new intent | `_nc_completion_counted_as_intent` (system actor treated as owner) | check D → final state not FROZEN |
| 2b | drain compares wrong identity | `_nc_drain_wrong_identity` (drain-start watermark) | check E → newer owner resume overwritten |
| 3 | old localStorage whole-map implementation | rev-4 block fixture in real Chromium | s2, s3, s4 → unresolved id not recoverable |
| 4 | stale tab overwrites sibling request | rev-4 block, s4 interleaving | s4 → newer id lost |
| 4b | completion erases sibling | mutant clearing siblings on completion | s2 → sibling id erased |
| 4c | simultaneous creation loses one id | rev-4 block, s3 interleaving | s3 → one id lost |
| 5 | no pre-claim reservation | `_nc_no_reservation` (row only at claim) | IPC and Telegram duplicate checks → duplicate not IN_PROGRESS |
| 6 | duplicate before claim returns REFUSED | `_nc_duplicate_refused` | IPC and Telegram duplicate checks → duplicate not IN_PROGRESS |
| 7 | audit buffer allows cap+1 | `_nc_cap_plus_one` (rev-4 overflow) | `len(buffer) ≤ cap` invariant |

Revision 5 adds 13 negative-control cases (9 Python + 4 browser); all
earlier negative controls still pass.

### R5-K. Performance regression check

`scripts/bench_owner_interface.py`, 3 runs after the full suite (host load
average ≈1.4–1.6 from the live kernel):

| Measure | Revision 5 | Revision 4 (1 run) |
|---|---|---|
| Idle listener + flusher CPU / 3 s | 0.34–0.41 ms | 0.41 ms |
| `status` over IPC p50 / p95 | 1.17–1.25 / 2.11–2.28 ms | 1.20 / 2.25 ms |
| `freeze`/`halt` over IPC p50 / p95 (now incl. reservation) | 3.10–3.41 / 6.02–6.63 ms | 3.51 / 6.29 ms |
| Duplicate replay p50 | 1.11–1.21 ms | — |
| Synthetic cycle under 20 Hz status | ×0.42 / 1.02 / 0.83 | ×1.01 |

The quiet baseline itself varied 8.7–20.5 ms between runs, so the cycle ratio
is noise-dominated on this host. No regression is visible; no improvement is
claimed. Production-cycle impact remains unmeasured.

### R5-L. Exact remaining blockers

Code: none known after this revision; this needs independent re-review.
Owner decisions / operational prerequisites (unchanged, not code defects):
1. Persistent private IPC location: `Linger=no` removes `/run/user/1000`; either
   `loginctl enable-linger sarmad` or `owner_interface.ipc_dir` under
   `/home/sarmad` (0750). `data/` (0775) is refused by design.
2. Accept that panic, close-trade and market-type need a running kernel
   (nothing is queued while it is down; exchange stops remain the protection).
3. Accept the trusted same-uid adapter model (not OS-enforced isolation).
4. Semantics to confirm: a panic now contains **immediately at acceptance**
   (FROZEN, or a HALTED hold), then flattens next cycle; previously FROZEN
   came only after the flatten. After the flatten, only newer owner intent
   can leave containment.
5. Production `main` still carries the old operational chat path (`do_ops=True`);
   this candidate removes it. Not touched here.

### R5-M. Deployment mode

**BLOCKED** until items L.1–L.4 are decided by the owner; then **ATOMIC**
(kernel and dashboard together). Not KERNEL_FIRST_SAFE: an old dashboard would
keep direct setters and chat control paths. Not DASHBOARD_FIRST_SAFE: a new
dashboard against an old kernel fails closed and has no controls. The first
start of the new kernel migrates nothing in place: `owner_requests` v3 is
created fresh (earlier revisions were never deployed; a v2 table from a test
run is refused by the schema check).

### R5-N. Exact changed files (revision 5)

- `trader/owner/contract.py`: IN_PROGRESS / OUTCOME_UNKNOWN statuses,
  `Disposition`, `disposition` on results.
- `trader/owner/service.py`: reservation lifecycle (`reserve`,
  `settle_refusal`, `_existing`, `_settle_orphan`, owner-only `_claim`),
  schema v3, panic acceptance hold, exact audit bound, malformed → REFUSED.
- `trader/owner/ipc.py`: malformed → REFUSED; OUTCOME_UNKNOWN naming.
- `trader/owner/adapters/render.py`: panic wording.
- `trader/kernel.py`: `_tg_dispatch` reserves every control at dispatch,
  and second recovery → `settle_refusal`; `_handle_tg_command` simplified;
  `_drain_panic` owner-intent rule.
- `trader/api/graphql_schema.py`: `disposition` field.
- `trader/dashboard/web/index.html`: per-request storage keys.
- Tests: new `tests/test_owner_interface_r5.py`,
  `tests/test_owner_dashboard_browser.py`, `tests/fixtures/owner_block_rev4.js`
  (revision-4 JS, never served); updated `tests/test_owner_interface.py`,
  `tests/test_owner_interface_boundary.py`, `tests/test_owner_interface_r3.py`,
  `tests/test_owner_interface_r4.py`, `tests/test_owner_dashboard_js.py`.
- `engine/supervisor.py`: unchanged in revision 5 (the revision-4 optional
  `expected_intent_event_id` remains). `engine/state.py`, `engine/risk.py`,
  `engine/control_fence.py`: unchanged.

## Revision 4 — third review blockers (R4-W1…W5)

| # | Reviewed counterexample | Fix | Regression (`tests/test_owner_interface_r4.py`) | Mutant (rev-3 behaviour) → fails at |
|---|---|---|---|---|
| R4-W1 | Admit UNHALT → pause before OwnerService → newer HALT → release ⇒ ACTIVE | A recovery is bound to the control-intent watermark **at admission** (Telegram: at dispatch; other paths: on arrival at `OwnerService`). The Supervisor refuses it as `superseded_by_newer_intent` **inside its intake fence** if any intent event landed since, so no window remains between check and intake. Futures (`request_owner_recovery`) and spot (`request_owner_release`) both. | `test_w1_admitted_unhalt_superseded_by_newer_halt` (exact interleaving), `test_w1_supervisor_binding_futures_and_spot`, `test_w1_service_binds_at_arrival_by_default` | watermark dropped → final state ACTIVE, not HALTED |
| R4-W2 | Accept PANIC → accept HALT → drain ⇒ HALTED→FROZEN | The pending panic stores `{request_id, intent_event_id}` read **under the control fence at acceptance**; `_drain_panic` applies FROZEN only if no intent event followed acceptance (legacy `"1"` = accepted at drain start). The flatten still runs: the panic was accepted and not withdrawn. | `test_w2_halt_after_panic_acceptance_is_kept`, `test_w2_panic_without_newer_intent_freezes` (+ R3 panic tests) | drain-start watermark → FROZEN over HALTED |
| R4-W3 | Tab B's stale in-memory map erased Tab A's unresolved PANIC id | No per-tab authoritative map: every access is a **fresh read-modify-write of one key** in shared localStorage; deletion only if the stored id still matches; memory holds only entries whose storage write failed | `test_w3_other_tab_never_erases_unresolved_id` (two isolated Node contexts sharing one localStorage; B cancels, A unknown, B completes, A reloads and reuses id A without new consent; PENDING keeps its id) | cached per-tab read → B writes `{}`, reload gets a new id |
| R4-W4 | Class-slot refusal answered REFUSED for a running/completed request | `record_admission_refusal` authorizes, then **consults the idempotency record** before refusing: DONE → replay; running → `PENDING request_in_progress` (non-definitive, the browser keeps the id); unresolved → ERROR outcome unknown; only a never-seen request gets a definitive `REFUSED owner_interface_busy`. A refused sender never sees a recorded result. | `test_w4_admission_refusal_consults_the_record` (default slots: two deliveries occupy recovery; third → PENDING; completed → replay; unknown → REFUSED), `test_w4_refused_sender_never_sees_a_recorded_result` | blind refusal → REFUSED for the running request |
| R4-W5 | Overflow counted in-flight rows as dropped (8 persisted, 3 "dropped") | Overflow evicts only the oldest row **not in a committing batch** (in-flight sequence numbers tracked per flush); only real evictions are counted | `test_w5_overflow_counts_only_discarded_rows` (flush paused before commit, buffer overflowed; asserts persisted + dropped == emitted and dropped == actually missing) | in-flight eviction → persisted + dropped ≠ emitted |

Also fixed while writing R4-W4: `_activation_event(None)` raised when a
recovery result carried no request event id (the real Supervisor always sets
one; now guarded).

**Core change (explicitly listed, additive):** `Supervisor.request_owner_recovery`
and `request_owner_release` gain an optional `expected_intent_event_id`
(default `None` = previous behaviour unchanged), checked inside the existing
intake fence alongside the existing watermark checks; `Kernel.owner_resume`
passes it through. No other Risk/recovery semantics changed; all existing
Supervisor, owner-recovery, Risk-guard and activation-baseline suites pass.

**Behaviour to note (conservative by design):** a Telegram `/unhalt` admitted
*before* a quickly following `/halt` is applied is superseded and refused,
even if the owner sent them as "halt, then unhalt" in rapid succession —
newer containment always wins; the owner re-sends `/unhalt`.

**Count correction (review §T):** revision 3's file had 21 tests including
**10** mutant cases (not 12). Revision 4 adds 14 tests including 5 mutant
cases: 38 supplied negative-control cases in total (23 + 10 + 5).

**Revision-4 results:** `tests/test_owner_interface_r4.py` 14 passed (5
mutant cases); new + touched suites **747 passed**; full suite **3226
passed, 52 failed, 12 errors** (13 m 22 s) — failing/erroring ids identical to
revisions 1–3 (set difference empty both ways; the 52-vs-50 difference is the
two host-timing `quick_host_run` tests, as before). Benchmark sanity run:
status p50 1.20 ms / p95 2.25 ms, freeze/halt p50 3.51 ms / p95 6.29 ms,
20 Hz cycle ×1.01 (noise-dominated host; no production claim).

**Audit durability, restated precisely:** normal flushing loses nothing
(rows leave memory only after commit; a failed flush retries them). A process
crash can lose the unflushed read/ingress window (≤64 rows or ~2 s), never a
control record. Only a prolonged journal failure that fills the 10 000-row
buffer evicts rows, and exactly those evictions are counted and recorded
when the journal recovers.

Revision-4 changed files: `trader/engine/supervisor.py` (optional binding),
`trader/kernel.py` (`owner_resume` passthrough, admission watermark in
`_tg_dispatch`, `_handle_tg_command(admitted_intent_event_id=…)`,
`_drain_panic` acceptance watermark), `trader/owner/service.py`
(`admission_watermark`, admitted binding, panic JSON token, replay-aware
admission refusal, in-flight-aware overflow, `_activation_event` guard),
`trader/dashboard/web/index.html` (shared-storage helpers),
`tests/test_owner_interface_r4.py` (new), fixture/mutant updates in
`tests/test_owner_interface.py`, `tests/test_owner_interface_r3.py`,
`tests/test_owner_dashboard_js.py`, `tests/test_owner_interface_boundary.py`,
`tests/test_activation_risk_baseline.py` (the `spot_direct_active` mutant now
accepts and forwards the new keyword so it stays a live mutant),
`scripts/bench_owner_interface.py`.

## Revision 3 — second review blockers (R3-W1…W4)

Scope kept narrow: only the four reproduced defect groups, plus the missing
W5/W11 mutants (review §T). New file `tests/test_owner_interface_r3.py`
replays each reviewed interleaving exactly; every check is paired with a
mutant that restores the revision-2 behaviour, and each mutant was confirmed
to fail at the semantic assertion (e.g. the rev-2 dispatch ends ACTIVE, not
HALTED), not incidentally.

| # | Reviewed interleaving | Fix | Regression | Mutant (rev-2 behaviour) |
|---|---|---|---|---|
| R3-W1a | RESUME blocked in venue check → UNHALT → HALT → release ⇒ queued UNHALT ran and left ACTIVE | Telegram **admits at most one recovery** (queued or running); a second is refused at admission (`recovery_in_progress`, audited via `record_admission_refusal`) and never queued; admission is released when the run ends | `test_w1_queued_recovery_cannot_outlive_newer_halt` (final HALTED, one Supervisor request, refusal reply + audit row), `test_w1_recovery_admission_is_released_after_the_run` | rev-2 dispatch → ends ACTIVE |
| R3-W1b | `/status` reply blocked ⇒ later `/halt` waited in the same worker | Four Telegram workers: containment, recovery, reads/informational, **outbound replies**; the executing worker never performs the HTTP reply; the listener only polls and dispatches | `test_w1_blocked_reply_never_delays_halt` (HALTED < 1 s while the reply hangs) | rev-2 dispatch → HALT not applied within 1 s |
| R3-W2 | Late answer for attempt A deleted newer unresolved B; storage failure produced a fresh id | Pending entry cleared **only if its stored id equals the completing request's id**; the in-page map is authoritative and localStorage mirrors it, so a storage failure keeps the id for the page's lifetime and warns the owner once | `test_w2_late_answer_never_deletes_a_newer_unresolved_id`, `test_w2_storage_failure_keeps_the_id_in_page_and_warns` (real JS under Node, exact A/A/B sequence) | unconditional delete; storage-backed-only map |
| R3-W3a | Second panic accepted during flatten was cleared by the first cycle | `panic_requested` holds the **request id as token**; `Kernel._drain_panic` clears with compare-and-clear on the token it consumed; the newer panic runs next cycle (legacy `"1"` still honoured) | `test_w3_panic_during_flatten_stays_pending`, `test_w3_plain_panic_flattens_then_freezes_and_legacy_flag_works` | rev-2 drain → second panic lost |
| R3-W3b | HALT during flatten overwritten by the older panic's FROZEN | The intent watermark is read before the flatten; FROZEN is applied under the control fence **only if no newer intent event** exists; otherwise the newer state is kept and logged | `test_w3_halt_during_flatten_is_kept` | rev-2 drain → FROZEN over HALTED |
| R3-W4a | Rows dequeued before commit; a failed flush lost them | Buffer entries carry sequence numbers; a flush copies, writes, and removes **only committed sequence numbers**; one flusher at a time (no double write); a failed synchronous audit row falls back to the buffer; buffer capped at 10 000 with the drop count written when the journal recovers | `test_w4_rolled_back_flush_loses_nothing` (commit failure injected, then exactly-once), `test_w4_unwritable_journal_is_bounded_and_the_loss_recorded` | rev-2 flush → rows lost |
| R3-W4b | IPC class-slot refusal of a valid HALT had no audit row | `OwnerService.record_admission_refusal(req, reason)`: resolves the principal (no execution), writes an `owner_interface_rejected` control event and an audit row; used by IPC slot refusals and Telegram recovery admission; pre-auth connection-limit drops recorded as ingress refusals | `test_w4_slot_refusal_is_audited` | plain refusal → no audit row |
| §T | W5 and W11 had no mutant | — | existing W5/W11 tests | grants skipped for intents (caught by `test_w5_panic_close_market…`); reads not audited (caught by `test_a_…_audited`) |

**Bounded idempotency, stated explicitly (review §E/N):** a completed request
id is remembered for `request_retention_days` (default 7 d, never below 2×
`max_request_age_s`). Redelivery of the original request inside that window
replays its recorded result. After pruning, the original is still refused as
stale by its issue time. Only a *new* request that reuses an old id with a
fresh issue time could execute again. Unresolved (PENDING) claims are never
pruned.

**Audit durability, stated explicitly:** control requests and their
refusals are written synchronously. Read and IPC ingress rows are batched
(≤64 rows / ~2 s) and leave memory only after commit; a *process crash* can
lose at most the unflushed read/ingress window, never a control record.

Revision-3 changed files: `trader/kernel.py` (`_tg_dispatch` admission and
workers, `_tg_send`, `_tg_refuse`, `_handle_tg_command(reply=…)`,
`_drain_panic`), `trader/owner/service.py` (commit-safe audit buffer,
`record_admission_refusal`, panic token), `trader/owner/ipc.py`
(admission-refusal audit, connection-limit record),
`trader/dashboard/web/index.html` (id-matched clear, in-page map),
`tests/test_owner_interface_r3.py` (new), `tests/test_owner_interface.py`
(fixture hook, panic token, worker shutdown).

Unchanged and still open for the owner (not code defects): persistent
private IPC location (`Linger=no`), acceptance that panic/close-trade need a
running kernel, and that same-uid adapters are *trusted* rather than
*isolated* (review §B: a corrected call graph, not an OS-enforced boundary).

## W (revision 2). Blocker → fix → regression test → negative control

| # | Review blocker | Fix | Regression test (tests/…) | Mutant caught |
|---|---|---|---|---|
| W1 | Code collision turned "Confirm HALT" into a resume | Pending keyed by (provider, sender, code); code = HMAC(per-adapter random secret, proposal) truncated to 10 hex; a collision is **refused**, never merged; the pending entry carries its own operation | `test_owner_interface::test_w1_code_collision_can_never_swap_the_confirmed_operation` (forces Astra's collision m1664/m1864) | code-only / setdefault behaviour |
| W2 | No provider/principal binding; cross-provider confirm | Confirm must come from the same provider **and** sender and still resolve to the same principal; request id derives from (provider, sender, proposal message id) | `test_w2_confirmation_is_bound_to_provider_sender_and_principal` | — (covered by W1/W2 checks) |
| W3 | IPC accepted self-asserted channel/principal | Requests carry no principal (a `principal` key is malformed). Per-channel IPC keys; frame HMAC over (channel, nonce, request); kernel resolves (channel, identity) → principal; frame channel must equal request channel | `test_w3_channel_is_authenticated_not_asserted`, `test_k2_requests_cannot_carry_a_principal` | MAC check disabled |
| W4 | Telegram authorized the chat, not the sender | Identity = `message.from.id`; default binding = the configured chat id only when it is a private chat (positive id == owner's user id); explicit `identities.telegram` replaces it | `test_w4_telegram_foreign_sender_in_owner_chat_is_refused` | chat id used as identity; authz disabled |
| W5 | GraphQL panic / close_trade / set_market_type bypassed grants, forged actors; Telegram `/panic` and `kernel --panic` wrote intent directly | New typed operations `panic`, `close_trade{trade_id}`, `set_market_type{market}` executed by `OwnerService` with grants; actor derived from channel; GraphQL mutations take no `actor`; Telegram `/panic` and `kernel --panic` go through the gateway; kernel close-queue drain made read-and-clear atomic | `test_w5_panic_close_market_are_granted_typed_and_audited`, `test_w5_dashboard_legacy_mutations_use_the_gateway` (zero-grant principal refused; `actor:` argument now a schema error), `test_w5_kernel_drain_consumes_owner_appends`, `test_manual_close.py` | — |
| W6 | Dashboard `refresh()` crash; containment disabled during recovery | Removed dead `#b-active/frozen/halted` references; only RESUME/UNHALT are disabled while a recovery is in flight; FREEZE/HALT/PANIC always send; refresh drops responses older than the newest shown | `test_owner_dashboard_js.py` (real JS under Node) | "block containment" JS mutant |
| W7 | Telegram serialized halt behind resume; 8 recoveries exhausted IPC workers | Telegram owner commands dispatched to two ordered workers (containment, recovery); `OwnerService` refuses a *different* recovery at once (never parks a connection); IPC per-class slots (control 16, read 8, recovery 2) after authentication | `test_w7_telegram_halt_is_not_serialized_behind_resume`, `test_w7_recovery_cannot_exhaust_containment_slots`, `test_w7_ipc_class_slots_reserve_containment`, `test_i2_a_different_recovery_is_refused_at_once` | inline Telegram handling; shared slot pool |
| W8 | Unknown outcomes misclassified; browser ids not durable | Client: only a failed *connect* is UNAVAILABLE; anything after the first byte is ERROR `…outcome_unknown`. Service: a same-boot unresolved claim answers ERROR `outcome_unknown` (never `request_id_conflict`); claim failure → REFUSED `persistence_unavailable` (nothing ran). Browser keeps {request id, click time} per action in localStorage until a definitive answer; retries and reloads reuse both and need no new consent | `test_w8_failure_after_delivery_is_outcome_unknown`, `test_w8_result_persistence_failure_is_outcome_unknown_not_conflict`, `test_w8_claim_failure_means_not_executed`, JS `test_unknown_outcome_retry_reuses_the_original_id_and_time` | post-send → UNAVAILABLE; conflict-on-unresolved; new id per click (JS) |
| W9 | Socket pathname lifecycle; client did not verify server | IPC dir (default `/run/user/<uid>/luffy-owner`) must be a real 0700 dir owned by uid with **no group/other-writable, non-sticky ancestor** (production `data/` at 0775 is refused); keys 0600 opened `O_NOFOLLOW`; `stop()` unlinks only the inode it bound; responses are HMAC'd over (nonce, result) and must match the request id | `test_w9_stop_never_unlinks_a_replacement_socket`, `test_w9_insecure_paths_are_refused[symlink|group_writable_dir|open_parent]`, `test_w9_client_rejects_a_fabricated_listener` (no MAC, wrong key, wrong nonce), `test_w9_socket_dir_and_keys_are_private` | unconditional unlink |
| W10 | `meta=[]/false/0/""` executed; missing Telegram date refreshed | `meta`/`args` must be objects when present; `meta` keys allowlisted (`command`, `confirm_ref`) with a strict value pattern; `issued_at` required on the wire and finite; Telegram updates without date, sender or update id are refused | `test_m_malformed_payload_is_refused` (incl. all four reviewed values), `test_j_stale_future_and_missing_time`, `test_q_…` (no-date update) | meta fail-open (×4) |
| W11 | Audit/metadata/persistence gaps | `owner_audit` table: one row per request (reads, refusals, IPC ingress refusals, replays included); results carry `principal`, `channel`, `request_ref`; arbitrary metadata rejected (a `meta.note` sentinel is malformed); claim+intake audit in one transaction, result audit+completion+audit row in one transaction; schema version checked; retention (requests ≥7 d and ≥2× max age, audit 30 d) | `test_a_…_audited`, `test_n2_schema_version_is_checked`, `test_n3_…pruned…`, `test_n4_read_audit_batch_flushes_by_size`, `test_audit_and_records_carry_no_secrets` | — |

Negative controls from revision 1 (no idempotency, direct ACTIVE, authz off,
stale check off, operational chat, dashboard setter) were re-pointed at the
revised tests and still fail as required.

## A. Control-path inventory (after revision 2)

| Path | Process | Operations | Via OwnerService | Actor | Authentication / grant | Audit |
|---|---|---|---|---|---|---|
| Telegram owner commands `/status /health /freeze /halt /resume /unhalt /panic` | kernel (2 ordered workers) | 7 | yes | derived (`operator`) | chat id (listener) + **sender id → principal** + grants | control pair + audit row |
| Dashboard GraphQL `owner_control`, `panic`, `close_trade`, `set_market_type` | dashboard → IPC → kernel | 8 | yes | derived (`dashboard`) | DashboardAuth session + dashboard channel key + grants | control pair + audit row |
| OpenClaw / WhatsApp adapter | bridge → IPC → kernel | reads; confirmed controls | yes | derived (`operator`) | provider sender (NOT_CONFIGURED) + channel key + confirmation binding + grants | same |
| `trader.owner.cli` / `kernel --panic` | CLI → IPC → kernel | status, health, panic | yes | derived | cli channel key + `local-operator` grant | same |
| `/api/chat` | dashboard | none | — | — | — | — |
| Telegram `/news /rent /scouts /judge /tv` | kernel | informational reads | no | — | chat id | none (unchanged) |
| Risk, MacroGuard, Supervisor internal transitions; executor/exits | kernel | system | no (system authority) | fixed system actors | existing guards | existing |
| `Supervisor.prepare_rollback`, `RiskManager.repair_baseline` | Python API | — | not exposed | caller | existing guards | existing |
| `ControlStateMachine` library | importable | any | no | caller | fence rules | control events |

No owner-facing path writes `control_state`, `panic_requested`,
`close_requests` or `market_type` outside `OwnerService` and the kernel
cycle that consumes them (repo-wide AST test
`test_only_owner_service_executes_owner_transitions`; ACTIVE-setter inventory
in `test_activation_risk_baseline_v2` has no out-of-scope sites). The
`ControlStateMachine` library itself is not process-restricted: any code
running as the kernel uid could import it — an OS-level limit (§Q.6).

## B. Contract (`trader/owner/contract.py`, wire v2)

- Operations — reads: `status`, `health`; containment: `freeze`, `halt`,
  `panic`; recovery: `resume`, `unhalt`; intents: `close_trade {trade_id}`,
  `set_market_type {market: spot|futures}`. Anything else is malformed.
- `OwnerRequest`: `request_id, operation, channel, identity, issued_at
  (required, finite), request_ref, args (typed per operation), meta
  (allowlisted)`. No `principal`.
- `OwnerResult`: `request_id, operation, status, principal, channel,
  request_ref, control_state_before/after, reasons, supervisor_outcome,
  risk_release, audit_event_ids, transition_event_id, replayed, data, ts`.
- Status: `ACCEPTED | ACTIVATED | CONTAINED | ALREADY_SET | REFUSED | PENDING
  | ERROR (outcome unknown) | UNAVAILABLE (never delivered)`.

## C. IPC (`trader/owner/ipc.py`)

AF_UNIX socket in a private directory; peer uid (SO_PEERCRED); per-channel
keys (`dashboard`, `openclaw`, `whatsapp`, `cli`), created 0600 with
`O_EXCL|O_NOFOLLOW`, kept across restarts; request and response HMAC-SHA256
with a client nonce; 64 KiB frames; 32 pre-auth connections, then class slots
(control 16 / read 8 / recovery 2); unauthenticated or refused-peer
connections get no answer (client reports outcome unknown). Stale-socket
cleanup only for a socket nobody answers on; `stop()` removes only its own
inode. A flusher thread writes batched read-audit rows every 2 s.

## D. Authorization

`(channel, identity) → principal → grants`, resolved only in the kernel.
Defaults: `owner` = all operations on telegram/dashboard/openclaw/whatsapp;
`local-operator` = status, health, panic on cli. Identities: dashboard
`session → owner`; cli `local → local-operator`; telegram = owner sender ids
(default: private `TELEGRAM_CHAT_ID`); whatsapp/openclaw empty
(NOT_CONFIGURED). Refusals never claim a request id.

## E. Idempotency and crash behaviour

Unchanged from revision 1 where the review passed it (A–G of review §E), plus:
a same-boot unresolved claim → ERROR `outcome_unknown`; result-persistence
failure → ERROR `result_not_recorded` with the observed status as data; claim
failure → REFUSED `persistence_unavailable`. Recovery requests other than the
running one are refused immediately; duplicates of the running one wait for
its result.

## F–I. Adapters

- **Telegram:** sender-authorized; exact slash commands; missing
  date/sender/update id refused with a reason. Revision 3: four workers
  (containment, recovery, reads/informational, outbound replies) and at most
  one admitted recovery — a second is refused at admission, never queued.
  Futures and spot both reach `Kernel.owner_resume` unchanged.
- **Dashboard:** see W5, W6, W8. Browser keeps one id + click time per action
  until a definitive result; the kernel judges the click's age, not the
  retry's.
- **OpenClaw / WhatsApp:** see W1, W2. Live provider bridge
  **NOT_CONFIGURED**; the adapter's only egress is the per-provider
  `OwnerClient`.

## J. Chat safety

Unchanged and passing (review §I PASS): chat has no operational path; 19
phrases (the review's five included) make zero owner-interface calls and zero
control/panic mutations through `ChatEngine` and through the OpenClaw adapter.
The production-source exposure on `main` reproduced by the review
(`/api/chat` with `do_ops=True`) is removed by this package.

## K. Cross-process safety

Adapter-side modules import no Supervisor/Risk/Executor/exchange/state
machine (AST + subprocess import graph). The dashboard owner path is
`_owner_gateway → OwnerClient`; it holds no state machine, kv write or
control-event writer. GraphQL has no state setter and no `actor` argument.

## L. Audit / secret boundary

`trader/owner/**` references no environment, `.env`, Binance or bot-token
markers (per-file test). Sentinel secrets in env and in the Telegram base URL
never reach `control_events`, `owner_requests` or `owner_audit`
(`test_audit_and_records_carry_no_secrets`, now incl. `/panic`). The channel
keys are Owner Interface credentials only and live outside the repo.

## M. Performance (isolated; `scripts/bench_owner_interface.py`)

Revision 3 (5 runs, after the full suite, host load average ≈1.7 from the
live kernel): idle listener+flusher 0.40–0.54 ms CPU per 3 s; `status` p50
1.26–1.31 ms, p95 2.37–2.49 ms; `freeze`/`halt` p50 3.45–3.93 ms, p95
6.80–6.89 ms. Synthetic cycle under 20 Hz status: ×0.42 / 0.94 / 0.997 /
1.08 / 1.20. The quiet baseline alone varied 8.9–22.4 ms between runs, so
this measurement is noise-dominated on this host: no consistent regression,
and no claim of improvement. Unthrottled status (≈265–320 req/s, client in
the same process): ×0.47–1.70. Production-cycle impact remains unmeasured.

Revision 2 (3 runs):

| Measure | Revision 2 |
|---|---|
| Idle listener + flusher CPU | 0.49–0.59 ms per 3 s |
| `status` over IPC (HMAC, key load, dir checks) | p50 2.2–2.3 ms, p95 3.7–5.4 ms |
| `freeze`/`halt` over IPC | p50 5.5–5.8 ms, p95 9.4–9.7 ms |
| Synthetic cycle under 20 Hz status | **×1.02–1.06** |
| Synthetic cycle under unthrottled status (~200 req/s) | ×1.50–1.68 (stress bound; client runs in the same process here) |

A first revision-2 run measured ×1.42 at 20 Hz: a synchronous audit commit per
read (0.80 of 0.84 ms per status) contended for the journal write lock. Read
and ingress-refusal audit rows are now batched (≤64 rows or ~2 s); controls
remain synchronous. Trade-off: a crash can lose up to ~2 s of *read* audit
rows. Production cycle impact is unmeasured.

## N. Test results

- `tests/test_owner_interface.py` 83, `tests/test_owner_interface_boundary.py`
  104 (incl. 21 negative controls), `tests/test_owner_dashboard_js.py` 6 (Node,
  incl. 2 JS mutants).
- New + touched suites together: **642 passed** (adds `test_manual_close`,
  `test_kernel_boot_recovery`, `test_owner_recovery*`,
  `test_activation_risk_baseline*`, `test_chat_agent`, `test_dashboard_auth`,
  `test_supervisor`, `test_attention_view`, `test_rent_wiring`).
- Revision 3: `tests/test_owner_interface_r3.py` 21 passed (incl. 10
  mutant cases — corrected from "12"); new + touched suites together **733 passed** (adds
  `test_control_state_recovery`, `test_attention_telemetry`); full suite
  **3212 passed, 52 failed, 12 errors** in 14 m 31 s — failing/erroring ids
  identical to revisions 1 and 2 (set difference empty both ways).
- Revision 2 full suite (`--continue-on-collection-errors`): **3191 passed, 52 failed,
  12 errors** in 15 m 41 s. The 64 failing/erroring ids are **identical** to
  revision 1's (set difference empty both ways), which were identical to a
  pristine `acbd0ce` tree: untracked research artifacts/data absent from a
  fresh worktree plus pre-existing `naked_position`, `partial_close`,
  `cognition_contracts` failures. None touches the owner interface.
- Revision-1 count discrepancy (review §S): my 64 ids = the review's 62 plus
  `test_m32_host_qualification_v4::test_quick_host_run_end_to_end…` and
  `test_m32_qualification::test_quick_host_run_end_to_end…` — host-timing runs
  that passed in the reviewer's environment. No owner-interface code involved.

Existing tests changed (intent preserved): Telegram helpers now send a
well-formed owner update (sender, chat, date, unique update id) —
`test_owner_recovery_risk_guard.tg/tg_update`, `test_owner_recovery._tg/
tg_resume`, the listener test (patches `_tg_dispatch`),
`test_kernel_boot_recovery`, `test_owner_recovery_concurrency`; Supervisor
audit assertions expect `principal="owner"` and `meta.identity`;
`test_manual_close` intent side goes through `OwnerService`;
`test_activation_risk_baseline` adapter-shape assertion; `test_chat_agent`
(no `do_ops`); `test_activation_risk_baseline_v2` (no out-of-scope setters).

## P. NOT_CONFIGURED

Live OpenClaw and WhatsApp provider bridges and their sender authentication;
provider identity bindings (empty); gateway exposure of rollback preparation
and Risk baseline repair (not exposed).

## Q. Deployment blockers / prerequisites

1. **IPC directory must survive.** Default `/run/user/1000/luffy-owner`, but
   this host has `Linger=no`, so `/run/user/1000` is removed when the last
   login session ends — the gateway then fails closed (UNAVAILABLE). Before
   deploy either `loginctl enable-linger sarmad` or set `ipc_dir` to a private
   path with safe ancestors (e.g. under `/home/sarmad`, 0750). `data/`
   (0775) is refused by design.
2. **Atomic kernel + dashboard deploy** (review §U/V). Old dashboard +
   new kernel keeps the old direct setter and chat ops live; new dashboard +
   old kernel fails closed for every owner control (including panic and
   close-trade, which previously worked without the kernel).
3. **Behaviour change for owner decision:** `kernel --panic`, dashboard panic
   and close-trade now require a running kernel; nothing is queued while it
   is down (the exchange stops remain the protection).
4. Telegram: if the configured chat is a group, add the owner's user id to
   `owner_interface.identities.telegram`; otherwise all owner commands are
   refused (`identity_not_bound`).
5. First kernel boot creates `owner_requests` + `owner_audit` in `luffy.db`
   and the channel keys; an older kernel ignores them.
6. **Residual (not fixable in-process):** kernel, dashboard and any bridge run
   as uid 1000 (review §R), so each can read every channel key, the journal
   and the credentials file. Real separation = separate OS users for dashboard
   and bridges with read-only journal access.
7. Pre-existing, out of scope: the dashboard process still reads Binance keys
   for read-only venue views.

## R. Changed files (package; `graphify-out/` excluded)

New: `trader/owner/{__init__,contract,authz,service,ipc,cli}.py`,
`trader/owner/adapters/{__init__,render,telegram,dashboard,openclaw}.py`,
`tests/test_owner_interface.py`, `tests/test_owner_interface_boundary.py`,
`tests/test_owner_dashboard_js.py`, `scripts/bench_owner_interface.py`, this
report.

Modified: `trader/kernel.py` (owner service/IPC wiring, Telegram dispatch
workers and adapter branch, `--panic` via gateway, atomic close-queue drain),
`trader/api/graphql_schema.py`, `trader/dashboard/server.py`,
`trader/dashboard/web/index.html`, `trader/chat/engine.py`,
`trader/chat/agent.py`, `config.yaml`; tests `test_activation_risk_baseline.py`,
`test_activation_risk_baseline_v2.py`, `test_attention_view.py`,
`test_chat_agent.py`, `test_kernel_boot_recovery.py`, `test_manual_close.py`,
`test_owner_recovery.py`, `test_owner_recovery_concurrency.py`,
`test_owner_recovery_risk_guard.py`.

Core Risk/recovery modules: `engine/state.py`, `engine/risk.py`,
`engine/control_fence.py` unchanged. `engine/supervisor.py` has one additive,
optional parameter (revision 4, above). Kernel changes outside owner wiring:
the atomic close-queue drain and the panic drain (`_drain_panic`).
