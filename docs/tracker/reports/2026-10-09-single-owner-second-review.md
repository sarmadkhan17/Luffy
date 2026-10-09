# LUFFY single-owner tracker — second review (incident and blocker reconciliation)

Targeted correction of Sol-1's single-owner audit (`7a5f1dd`). All 179 rows keep their IDs, original statuses, dependencies, closing conditions, evidence and owner decisions. The new fields are audit metadata and proposals. They grant no operating authority and defer or close nothing.

Detail lives in the canonical YAML: `metadata.second_review`, the root section `single_owner_second_review` (50 incidents, 14 current blockers, enums), and `single_owner_classification.second_review` on 89 rows (all 58 P0 rows plus 31 named rows).

## Identity

| Item | Value | Verified |
|---|---|---|
| Sol-1 audit | `7a5f1dd212f9f038d7409b70786e1719d7d6f029`, branch `sol1-single-owner-tracker-audit` | commit exists; its tracker SHA-256 `42bc8406…704a` matches Sol-1's receipt |
| Production | `9319ecfcca62536b7e5de48c0e3ee4e39dc2c828` | `git worktree list` shows production at this HEAD |
| Protected candidate | `b839d99d6b37421ae60f302b8af225df50737e47` | fast-forward descendant of production, 26 commits ahead, **not deployed** |
| This review | branch `opus-tracker-second-review-20261009`, parent `7a5f1dd` | isolated worktree `/mnt/luffy-data/luffy/opus-tracker-second-review` |

The candidate source was read from its worktree. I did not check out, merge or run it.

## A. Final priority counts

| P0 | P1 | P2 | P3 | Total |
|---:|---:|---:|---:|---:|
| 58 | 79 | 30 | 12 | 179 |

**No priority or primary-category change is justified.** The corrections are in blocker validity, operating level and P0 nature. That distinction is what matters for the P0 set:

| P0 nature | Rows |
|---|---:|
| Safety invariant, closed offline (preserve; do not reopen) | 31 |
| Safety invariant, mapping obligation (EVIDENCE_TO_MAP; not a missing-code finding) | 15 |
| Unsatisfied operational proof | 10 (RISK-03, EXE-05, EXE-06, RUN-01, RUN-02, RUN-03, GOV-07, SEC-01, PERF-01, MON-01) |
| Real-money authorization gate | 2 (STR-03, REL-01) |

So 58 P0 rows reduce to 10 with unsatisfied operational proof. The rest are invariants to preserve or evidence to map.

## B. Current DEMO blocker table

Levels. **OBSERVATION** is a contained FROZEN Kernel start with operator hold and research denial, no entries. FROZEN still runs reconciliation and exit/protection duties, so it is not zero-side-effect. **DEMO_ACTIVE** means entries are permitted.

| ID | Level | Validity | Kind | Blocker | Minimum requirement |
|---|---|---|---|---|---|
| BLK-01 | OBS, DEMO | TRUE | OPERATIONAL_EVIDENCE | The only candidate Journal is deliberately unbound; startup refuses it (`startup_risk_malformed_or_daily_unavailable`). | Owner approves the account day and equity explanation; then the evidence-backed Risk sequence (INC-B5). |
| BLK-02 | OBS, DEMO | TRUE | OPERATIONAL_EVIDENCE | No current whole-account inventory. The 2026-10-09T00:48Z flat observation is stale. | Authorized GET-only 5-endpoint batch inside the startup window. |
| BLK-03 | OBS, DEMO | UNVERIFIED | EXTERNAL_DEPENDENCY | The Kernel venue reader needs a distinct `BINANCE_READ_API_KEY`. It returned -2015 on 2026-10-08 and has not been shown valid since. | Owner fixes the read key, or approves another read path (a source change). |
| BLK-04 | OBS, DEMO | TRUE | INTEGRATION_GAP | Production is `9319ecf`; every correction is in `b839d99`. The production publisher does not exist and `jit_risk_init.py` is marked DRAFT. | Authorized deployment of exactly `b839d99` with frontend build `c1bf5ba3…`; reviewed publisher; loaded-revision check. |
| BLK-05 | OBS, DEMO | TRUE | OPERATIONAL_EVIDENCE | Containment must hold: FROZEN, operator hold, `watchdog.off`, DENIED research epoch, no other DB writer. | Publication gate on the real candidate; writer re-check just before exchange. |
| BLK-06 | OBS, DEMO | CONDITIONAL | OPERATIONAL_EVIDENCE | Root filesystem 94% used (3.2 GB free). | Capacity check on `/mnt/luffy-data` at execution time. |
| BLK-07 | OBS | CONDITIONAL | OPERATIONAL_EVIDENCE | Phase B needs the first observation inside 240 s. Historic first cycles were 223.759 s and 259.776 s. A miss fails closed. | Attempt the contained start; refusal is a safe outcome. |
| BLK-13 | OBS, DEMO | CONDITIONAL | OPERATIONAL_EVIDENCE | Dashboard binds 192.168.126.131; auth middleware exists, reachability unknown. | Keep minimum auth; verify only if the Dashboard is started. |
| BLK-08 | DEMO | TRUE | OPERATIONAL_EVIDENCE | No recurring successful-work/timing proof, no live boot/stop proof. | At least 3 cycles in about 15 min inside 240 s, repeat flat inventory, graceful stop. |
| BLK-09 | DEMO | TRUE | OPERATIONAL_EVIDENCE | No admitted strategy (18 retired, 2 paper, 1 demoted); research is fenced. | One exact admitted version through the single admission path. |
| BLK-10 | DEMO | TRUE | OPERATIONAL_EVIDENCE | The allocator refuses without a verified receipt with ESTABLISHED `expected_net_value`, plus five ESTABLISHED size bounds and capacity. | One qualified exact-scope economics/capacity receipt. No fallback economics. |
| BLK-11 | DEMO | UNVERIFIED | OPERATIONAL_EVIDENCE | Sizing assumes config leverage 5; venue leverage, margin type and position mode are unverified. | Separately authorized bounded GET before any entry. |
| BLK-12 | DEMO | CONDITIONAL | OPERATIONAL_EVIDENCE | Remaining baseline failures have unverified downstream scenarios. | Map or fix the safety-relevant ones; weaken no assertion. |
| BLK-14 | REAL_MONEY | TRUE | OWNER_DECISION | STR-03/REL-01 approval, production calibration, off-host recovery. | Later authorization. Does not block OBS or DEMO. |

Why BLK-10 is stated this narrowly: `trader/portfolio/allocator.py` (`_economic_reasons`, `_size`) and `economics.py` emit `CALIBRATED_MODEL_AUTHORITY_UNPROVEN`, `CALIBRATED_ECONOMIC_RESERVE_METHOD_AND_COVERAGE_MISSING` and `CALIBRATED_FORWARD_COST_SCOPE_BRIDGE_MISSING`. They also require a frozen entry capability. I did not confirm whether a paper-status spec can obtain that capability.

## C. Recent incident reconciliation

Full records, with evidence paths and `INSPECTED / COMMIT_VERIFIED / UNAVAILABLE` labels, are in `single_owner_second_review.incidents`. Each maps to existing owner IDs; no new tracker ID was needed.

Status counts across the 50 incidents: INTEGRATED_NOT_DEPLOYED 23, DEMONSTRATED 10, UNRESOLVED 8, FIXED_OFFLINE 4, HISTORICAL 3, OPERATIONALLY_VERIFIED 2.

| Incident | Owner IDs | Failure | Correction / evidence | Source status | Remaining proof |
|---|---|---|---|---|---|
| A1 Source divergence | GOV-07, GOV-05 | Production `9319ecf` vs candidate `b839d99` | Linear ancestry verified | INTEGRATED_NOT_DEPLOYED | Loaded revision after authorized start |
| A2 Seven identity refusals | OBS-01, RISK-03 | Phase A/B returned `allow=True` for unresolved Risk | `0c328af`; the 7 tests pass, file hash `e674bd17…` identical to the original | INTEGRATED_NOT_DEPLOYED | Real account-bound Risk |
| A3 Phase A/B | OBS-01, OBS-05, RUN-01 | Stale receipts accepted; no current-boot requirement | `f9a2ae8`; `ACCOUNT_STALE_S = 300.0` at `trader/dashboard/current_truth.py:26` | INTEGRATED_NOT_DEPLOYED | First cycle inside 240 s |
| A4 Descriptor lifecycle | DATA-12, MON-02 | FD leak; RLIMIT_NOFILE exhaustion in an 813-test run | `c0f01fb` | INTEGRATED_NOT_DEPLOYED | None operational |
| A5 `__new__` restore failure | BKP-02, DATA-12 | `restore_refused:AttributeError` | `adabdec`. **Introduced by `c0f01fb`** (commit message states it) | INTEGRATED_NOT_DEPLOYED | None known |
| A6 Auxiliary handles | RUN-01, MON-02 | Scratch server held writable DB/WAL/SHM descriptors; exclusivity unprovable (other users' PIDs uninspectable) | Processes stopped; no source fix | HISTORICAL | Re-verify before exchange |
| A7 Containment | RUN-02, RUN-01 | Entries must stay impossible | FROZEN + hold + `watchdog.off`; 11-case gate | INTEGRATED_NOT_DEPLOYED | Gate on real candidate |
| A8 Default-ACTIVE Journal | RUN-02, RISK-01 | New Journal not intrinsically contained | **No source default change found**; procedural only | DEMONSTRATED | Do not create a Journal outside the gate |
| A9 Crash-safe publication | BKP-02, GOV-07 | Two renames cannot publish WAL state | `renameat2(RENAME_EXCHANGE)` plus backup; recovery tooling, not in `b839d99` | FIXED_OFFLINE | Reviewed production publisher |
| B1–B6 Risk | RISK-03, RISK-04 | Unbound baseline; daily state null; **wrong ordering** | `3b23fb4`; floor 5519.90634238 enforced; v2 runbook order | INTEGRATED_NOT_DEPLOYED / UNRESOLVED | See procedure below |
| B7 Account binding | SEC-01, RISK-03 | Binding is key-derived | Trading-key GETs HTTP 200 on 2026-10-08T11:21Z and 2026-10-09T00:48Z, same scope | OPERATIONALLY_VERIFIED (stale) | Fresh capture |
| B8 Flatness | EXE-05, EXE-06 | Journal tables prove nothing | 2026-10-09T00:48Z: positions 0, orders 0, algo 0, margin 0, equity 5178.82661257 | OPERATIONALLY_VERIFIED (stale) | Recapture in window |
| B9 Leverage | RISK-02, EXE-05 | Config leverage 5 unverified at venue | None | UNRESOLVED | Authorized bounded GET |
| C1 SOL native exit | ACC-01, EXE-06 | Algo `1000000229852282` filled SELL 5.77 @ 116.15 at 2026-10-07T13:13:52.781Z; local row stayed open | `20dae12`; isolated replay books 27.4279258 (exit commission only); entry fees/funding unknown | INTEGRATED_NOT_DEPLOYED | Old Journal never reconciled (see OD-3) |
| C2–C3, C7 Native-exit safety | ACC-01/02, EXE-03 | Scope, replay, evidence, atomicity, exact fills (A–E) | `REVIEW.json` A–E PASS, SQL-abort and process-loss probes | INTEGRATED_NOT_DEPLOYED | No live native exit yet |
| C4 AVAX | EXE-05, ACC-01 | Long 77 with stop on 10-08; flat on 10-09 | **Closure mechanism and P&L not in any inspected evidence** | DEMONSTRATED | Optional authorized history read |
| C5–C6 Downtime and restart | EXE-06, EXE-02 | Venue changed while Kernel was down | `20dae12`, startup binding | INTEGRATED_NOT_DEPLOYED | Live restart proof |
| D1–D3 Strategy preservation | STR-04, STR-01 | Early export lost restrictions | `6021a30`; 21/21 rows, 18 columns exact; 18 retired / 2 paper / 1 demoted | INTEGRATED_NOT_DEPLOYED | None; early export UNAVAILABLE |
| D4–D5 Assets and evaluation | BKP-01, STR-02 | Six model assets; evaluator compatibility | Hash-verified; 14 evaluators resolve, 7 specs compile | FIXED_OFFLINE | Real inputs; live cycle |
| D6–D8 Intelligence | STR-04, QNT-09, OUT-01 | No admitted strategy; no economics; no learning chain | None (by design) | UNRESOLVED | See section E |
| E1–E4 Research authority | QNT-05, QNT-04 | Fresh Journal created default alpha 0.1 / w0 0.05; CLI `--once` and held-out loaders bypassed `enabled=false` | `b839d99` deny-only epoch authority | INTEGRATED_NOT_DEPLOYED | DENIED epoch present in the published Journal |
| E5–E8 | QNT-05, RES-09 | No release path; `research_tests` = 0; budget history uncertain | By design; nothing fabricated | UNRESOLVED / DEMONSTRATED | Future owner release contract |
| F1–F2 Timing | PERF-01, RUN-03, MON-01 | 259.776 s / 223.759 s first cycles | Offline probes only | INTEGRATED_NOT_DEPLOYED / UNRESOLVED | Present recurring health |
| F3–F4 Storage and descriptors | DATA-12, MON-02 | About 31 GB of test families removed; 94% full | None specific | DEMONSTRATED | Capacity check |
| F5 134 failures | GOV-04 | Figure not located | 24 legacy failures, a 14-failure log, 2 promotion failures are inspectable | DEMONSTRATED | See limitations |
| F6–F9 Archive, stores, cross-DB, rehearsal | BKP-01, OPEN-06 | Hash/permission protection only; 116 unmatched references | Final rehearsal PASS; `READY_FOR_CONTROLLED_CUTOVER: NO` | DEMONSTRATED / HISTORICAL | Owner accepts the limitation |

### Risk bootstrap, exact remaining procedure (RISK-03)

1. Owner approves the earliest account day (proposed `2026-10-10T00:00:00Z`) and the equity explanation: 5199.24 on 2026-10-08 with AVAX open versus 5178.83 flat on 2026-10-09.
2. Separately authorized GET-only batch: `account`, `positionRisk`, `openOrders`, `openAlgoOrders`, `multiAssetsMargin`. Positions, ordinary orders and algo orders must all be empty and margin zero. t0 is the receipt of `/fapi/v3/account`, with the raw bytes saved.
3. On a private copy of the prestaged Journal: `record_margin` → `update_equity(evidence)` on the **empty** Risk state → `repair_baseline(5519.90634238)`.
   - Reverse order creates a permanent `daily_state_unavailable` Journal. The first runbook and the final rehearsal candidate show this. The "do not reverse" rule rests on the Opus appendix, which I could not inspect, plus the runbook; `repair_baseline`'s docstring confirms only that it never changes control state.
4. Guarded publication, then `restart.sh` inside the freshness window. The runbook proposes aborting if more than 240 s has passed since t0.
5. Phase B uses the Kernel's own fresh observation and must finish within the 240 s startup timeout.

At 5178.83 the drawdown from the preserved peak is about 6.2%, below the 8% de-risk step in `config.yaml`. FROZEN observation releases no entries.

## D. Misclassified or excessive blockers

| ID | First-audit position | Correction |
|---|---|---|
| SEC-01 | Account visibility unverified after -2015 | Trading-key GETs succeeded (stale). Remaining: the **Kernel venue reader requires a separate read key** (`venue_reads.py`), whose validity is unverified. That is a real dependency of OBS and DEMO, not optional hardening, unless the owner approves another read path. |
| RISK-03 | "Map own behavior" generic gap | A specific, evidence-backed procedure and DRAFT tooling exist (above). |
| GOV-07, RUN-01 | Broad identity/live-proof gaps | Essential: loaded-source identity and single executor. Not required: remote Git parity, historical process reconstruction, enterprise deployment. RUN-01 live proof is produced by the observation itself. |
| PERF-01 | Single open gap | Split: present recurring health is a TRUE DEMO blocker; historical causal attribution is not a blocker. The 240 s contract is not waived. |
| STR-03, REL-01 | P0 gates | Real-money only; NOT_CURRENT for OBS and DEMO. |
| QNT-09 | "Conditional" for DEMO | TRUE blocker for DEMO entries only, with the exact allocator requirements above. |
| SOL/AVAX reconciliation | Implied pending work | Under the clean-epoch route the old Journal is archived, not reconciled, so this is an owner decision (OD-3), not a blocker. |
| RES-09, OPEN-01..03 | Open blockers | Future research activation is not required. The persisted DENIED epoch is. |
| VIS-01..09, GUI-07, GUX-08 | Open | Aesthetic acceptance has no safety role; NOT_CURRENT. |
| SEC-02, GUX-10 | Auth required | **Kept**: minimum auth on LAN-bound controls, applies once the Dashboard starts. No enterprise identity administration. |
| ACC-01/02, DEC-03, PORT-01/04, EXE-04, OBS-01, QNT-04/05 | CLOSED | Not reopened. Later corrections are separate incidents with INTEGRATED_NOT_DEPLOYED status. |

## E. Essential intelligence gaps

Beyond safe startup, a functioning trader still needs:

1. **Strategy supply.** All 21 inherited strategies are retired, paper or demoted. New ones need research, which the DENIED epoch fences until a future owner release contract exists.
2. **Calibrated economics.** Gross-value model, uncertainty reserve and cost bridge for one exact scope (QNT-09, PORT-02, EXE-07).
3. **Real inputs and a completed live cycle.** Startup and heartbeat are not cycle completion.
4. **A prospective outcome-to-learning chain** (OUT-01, OPEN-07, LRN-01/03, MEM-01).
5. **Decay versus no-opportunity discrimination** once a strategy exists (STR-05, OUT-04).
6. **Net economics after operating costs** (GOV-10, REL-04), without a profit quota or forced trade.

## F. Single-owner simplifications (proposals)

- **KEEP:** RISK-03/04, EXE-05/06, RUN-02, ACC-01, DEC-03, PORT-04 (financial safety); SEC-02, GUX-10 (minimum auth).
- **SIMPLIFY:** GOV-07 (local identity, no remote parity); PERF-01 and MON-05 (present health over historical attribution); SEC-01 (key-derived scope plus a working read path); QNT-09 (one exact scope).
- **DEFER:** STR-03, REL-01, BKP-03 (real money); VIS-01..09, GUI-07, GUX-08; RES-09 and OPEN-01..03 (research activation); REL-02/03 (existing owner deferrals).

None is adopted. Existing deferrals for SEC-01, RUN-01, GOV-07, MON-01, MON-05, REL-02 and REL-03 remain visible and unchanged.

## G. Owner decisions

1. **OD-1.** Earliest account day, and acceptance of the equity change 5199.24 → 5178.83.
2. **OD-2.** Fix the read-only key, or approve a different read path.
3. **OD-3.** Whether SOL/AVAX history is ever booked or stays unreconciled evidence. The AVAX closure mechanism is unverified.
4. **OD-4.** Accept the cross-database limitation (116 unmatched references) and the non-immutable archive.
5. **OD-5.** Economics scope and the first admitted version.
6. **OD-6.** Whether PERF-01 and GOV-07 may be accepted in their separated forms.
7. **OD-7.** Source `config.yaml` has `research.enabled=true` (tracked config must stay unchanged for identity). Accept the DENIED epoch as the fence, or approve a config change with a new runtime identity.

## H. Validation receipt

| Check | Result |
|---|---|
| Rows / unique IDs | 179 / 179 |
| Priorities | P0 58, P1 79, P2 30, P3 12 (0 changes); every row has a primary category |
| Status counts | CLOSED 67, EVIDENCE_TO_MAP 72, AWAITING_OWNER 15, AWAITING_EVIDENCE 9, DEFERRED 7, BLOCKED 5, OPEN 4 (unchanged) |
| Unchanged legacy data | Removing only the new fields yields a document **deep-equal to Sol-1's audited tracker**; removing Sol-1's additions as well yields a document deep-equal to the production `9319ecf` tracker. This covers statuses, closing conditions, dependencies, evidence and owner decisions. |
| Tracker SHA-256 | production `6bc43589…80b2` → Sol-1 `42bc8406…704a` → this review `9e529139…e4de`; 1,093,932 bytes (limit 2 MiB) |
| Incident integrity | 50 incidents: owner IDs exist, status and evidence enums valid, each owner row back-references its incidents |
| API | `read_tracker()` AVAILABLE, 179 items, version `1.62-single-owner-second-review-opus` |
| Existing tests | `tests/test_launch_preflight.py` tracker preflight consumer: 1 passed. Frontend `tests/tracker-phase.test.ts`: 4/4 passed (temporary `node_modules` symlink, removed). |
| Known failing test | `tests/test_dashboard_phase.py::test_tracker_and_truth_available_without_kernel` fails on a stale assertion (`OBS-05` vs the actual `PORT-04`). **Identical failure on Sol-1's tracker (before my change)**, so it predates this review. The API returned 200. No test or source edited. |
| Files changed | `docs/tracker/LUFFY_Product_Tracker_v1.yaml`; `docs/tracker/reports/2026-10-09-single-owner-second-review.md` |

**Limitations.**
- Unavailable artifacts, so claims resting on them are labelled UNAVAILABLE and not independently inspected: the Opus Risk-baseline appendix, the Opus research staging package and eight-table hash receipt, any report of "134 remaining baseline failures", and the original lossy strategy export.
- AVAX closure and P&L are not in any evidence I inspected.
- Specific Journal-growth measurements are not in the inspected evidence.
- Local Claude CLI was not probed. Prior reports record "Not logged in", and no delegated review is claimed.
- No Graphify command ran, because `graphify query` refreshes a cache stamp the task forbids touching.
- No venue or provider call, Kernel or Dashboard start, source edit, production write, or full-suite run.

## Smallest actual prerequisites

**Next contained DEMO observation:** BLK-01, 02, 03, 04, 05, plus the BLK-06 capacity check, and BLK-13 only if the Dashboard is started. BLK-07 is a safe-failure risk, not a precondition.

**DEMO ACTIVE, additionally:** BLK-08 (recurring health and boot/stop proof), BLK-09 (one admitted strategy), BLK-10 (qualified economics), BLK-11 (venue leverage), BLK-12 (remaining test scenarios), and owner release of FROZEN and the holds.
