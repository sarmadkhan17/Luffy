# Stage1 recovery/monitoring R1 blocker fixes

Package: `LUFFY-STAGE1-RECOVERY-MONITORING-BLOCKER-FIX-R1`. Result: PASS, awaiting independent review R2. Branch: `luffy-stage1-recovery-monitoring-foundation-r1`; unchanged HEAD/base: `fcfe9e0359874b18d65a0b1e1ccfd156d18115e1`. Changes remain uncommitted. No deployment, service start, venue call or real order submission occurred.

## Mandatory PIT inventory

`critical_inventory()` now derives PIT closure from `data/pit_population.json`, its current declaration and freeze receipt, all explicitly archived declarations, configured local ledgers, and existing critical stores' `population_meta`/`population_events` indexes. Retained bindings identify declaration version and exact export directory/stream. An acknowledged event with NULL payload requires precisely `sha256(event_id).json` in that bound directory. Pending payloads remain in the consistent SQLite snapshot; an already published pending file must match its payload exactly. This does not archive arbitrary repository directories.

The closure retains exact declaration versions, freeze hash/schema, event id, stable event hash, receipt hash/schema and import clock as typed asset dependency identities, alongside existing exact-byte size/SHA256 and SQLite schema metadata. Existing producer validators validate declarations/freezes. Exported receipts must match the ledger id, declaration/stream, stable hash/import clock, source digest and receipt digest. The two configured receipt streams also cover archives without local ledger configuration, as consumed by the current population capture contract.

Manual inventories cannot omit the config, referenced files, configured ledger, or a known critical ledger merely by leaving it out of the SQLite list. Configuration loss with a retained PIT ledger refuses capture. The closure is recomputed against snapshots before writing COMPLETE and again during verification/restore. File changes, missing referenced receipts, dependency omissions even with a rehashed manifest, and mismatched dependency identities refuse. Completed backups still reopen without surviving source state when supplied their trusted inventory/schema contract.

Absolute references are interpreted relative to the recorded original source root. References outside that tree refuse rather than copying unrelated paths. A historical binding's freeze hash that cannot be resolved through the producer's configured freeze evidence also refuses (`pit_bound_freeze_unresolved`); the producer currently exposes no archived-freeze path. No sibling filename or physical off-host claim is invented. This conservative refusal preserves the COMPLETE invariant when the producer contract lacks required evidence.

## Strict heartbeat contract

The existing `luffy-heartbeat.v1` producer remains the single producer. Its actual kernel successful-cycle context field is `last_successful_cycle_at`. The reader requires this finite numeric timestamp and `0 <= started_at <= last_successful_cycle_at <= publication <= observation`. Producer and boot identities must be nonempty; blank boot identities, bad sequence/publication/start fields, empty context, missing/null/bool/string/NaN/future/pre-boot cycle values refuse. No extra producer field or new staleness threshold was introduced.

The independent observer classifies invalid records UNAVAILABLE, publishes the existing out-of-band safety incident, and latches recovery. Invalid record bytes are never restamped, repeated detections deduplicate, and existing control-entry checks refuse entries. Fresh/stale/restart and normal Supervisor/Risk recovery semantics remain unchanged. Three executable mutants remove the actual context/chronology check; each fails its intended safety assertion.

## Reproducible evidence

Evidence directory: `docs/superpowers/evidence/stage1-recovery-monitoring-blocker-fix-r1/`.

Run each named batch with `./venv/bin/python scripts/stage1_recovery_monitoring_evidence.py <batch>` from `/mnt/luffy-data/luffy/production`. The runner records the exact pytest argv/command and cwd, branch/HEAD/base, selected arguments, source file hashes, runner/guard hashes, isolation environment, UTC start/end/duration, descriptor limits, pytest exit code and pass/fail/error/skip/deselected counts. Separate `.pytest.json` records retain every selected/deselected node, outcome/phase/duration, source module origin, and complete failure traceback. `.txt` retains combined stdout/stderr. No secrets or environment credential values are recorded. The runner is an evidence collector and returns normally after recording a failing pytest child; the child's exact exit code is recorded in `.run.json` and `.pytest.json`.

Internet socket and SQLite production-store guards are active in every batch. SQLite paths must belong to a newly created isolated fixture root; fixtures are removed afterwards. All recorded attempts are zero; no candidate modules were imported into the base run. Tests use fake venues/providers. No production store or startup command was used.

For `legacy-base`, the exact recorded `git archive --format=tar fcfe9e0359874b18d65a0b1e1ccfd156d18115e1 trader tests scripts config.yaml org.yaml requirements.txt SDD.md STATE.yaml NEXT.yaml` command runs in the repository. Its bytes/hash, Git tree identity, extraction procedure and unique workspace-local snapshot cwd are recorded. `tarfile.extractall(..., filter='data')` creates that source-only snapshot; its source hashes and all imported `trader.*` origins are recorded. The same three exact node arguments run on base and candidate under the same guard/plugin procedure. Snapshot/fixtures are removed after execution. This replaces the earlier in-process module substitution claim with an actual isolated exact-base source tree.

`baseline-comparison.json` compares nodeid, phase, relative test path, line and entire crash message; only the absolute source-root prefix is removed. Both versions fail identically at line 628: `assert [] == ['set_leverage']`. The selected test file hash is identical. HALTED/no-order assertions before that legacy expectation pass in both versions. These three failures are recorded, not counted as passes or silently discarded. MI-1 through MI-5 and legacy test expectations remain untouched.

The original `focused-tests.txt` is preserved unchanged with SHA256 in `descriptor-rerun-coverage.json`. It contains progress output only; the old terminal traceback/argv/exit metadata were not retained. The foundation report attributed that attempt to its 1024 descriptor ceiling. We do not fabricate missing historical metadata. The intended 13-file/563-node selection is explicitly reconstructed and collected under current guards without executing another large combined process. Every node maps to a fresh bounded execution batch: 154 control/entry, 121 recovery/owner, 285 Risk/health and 3 separately executed base-identical legacy failures. There are no missing/extra nodes. The 3 Risk/health deselections exactly equal the separately executed legacy selection.

Run `./venv/bin/python docs/superpowers/evidence/stage1-recovery-monitoring-blocker-fix-r1/verify_campaign.py` to independently check guards, counts, metadata, selected-test hash equality, normalized baseline failure equality and the complete 563/563 node mapping. Complete nonzero tracebacks for both final legacy batches are retained in JSON and text. Test records identify their source versions; the final acceptance rerun covers the last backup-only inventory refinement.

| Final batch | Result | Pytest duration |
|---|---|---|
| acceptance | 80 passed, including all six foundation mutants plus three strict-heartbeat mutant cases | 14.68s |
| control-entry | 154 passed | 54.16s |
| recovery-owner | 121 passed | 33.85s |
| risk-health | 285 passed, 3 deselected and separately executed | 94.47s |
| pit-regression | 43 passed | 43.15s |
| legacy-base | 3 identical legacy failures, exit 1, full tracebacks retained | 4.35s |
| legacy-candidate | 3 identical legacy failures, exit 1, full tracebacks retained | 2.83s |
| original-campaign-collection | 563 nodes collected; no tests executed | 5.24s |

## Authority and control plane

Focused tests preserve WAL snapshot consistency, manifest/hash integrity, external filesystem destination seam, secrets exclusions, fail-closed/atomic isolated restore, mandatory Recovery, Journal read/write failure containment, independent alerts, staleness/observer/restart chronology, dedup, Supervisor authority, Risk/control entry and owner health. Only backup inventory and heartbeat validation behavior were changed in this blocker package; no component obtained trading authority.

STATE retains MI-6 and MI-7 as IMPLEMENTED / TESTED / AWAITING_REVIEW and Stage1 `build_complete: false`. NEXT selects `LUFFY-STAGE1-RECOVERY-MONITORING-INDEPENDENT-REVIEW-R2`, type `NARROW_READ_ONLY_REVIEW`. No pre-start readiness was selected. Foundation destination/cadence/retention/notification policy and real off-host/restore/heartbeat/alert/venue-recovery evidence classifications remain unchanged.
