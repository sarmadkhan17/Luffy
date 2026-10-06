# GUI-02 dependency intake / OUT-03 replay evidence map

Result: OUT-03 BLOCKED, with implementation evidence mapped at offline scope.
GUI-02 is BLOCKED by OUT-03 and OWN-01. ACC-02 is already CLOSED at
architecture/offline scope; its evidence remains acc02-lineage-sol/closure.yaml.
No dependency is bypassed and no GUI implementation is undertaken.

OUT-03 is the first unmet direct prerequisite in the requested dependency
order. All three direct requirements have NORMAL priority. OUT-03 also gates
OWN-01, so mapping it first removes duplicated investigation.

SDD sections 19.3 and 25.3 require historical versions and the full forensic
story. The row requires original-cut reconstruction, hash verification and
idempotent retry. The following existing evidence supports that requirement:

| Requirement | Existing source and regression evidence | Limit |
|---|---|---|
| Data, world, context, spec, configuration and book at the original cut | learning/decision_sources.py; learning/capture_runtime.py; learning/capture.py; test_decision_sources.py | Exact captured inputs, not a certification of every data consumer |
| Decision, allocation and terminal action stay bound | capture.chain_event / forward_event / trade_event; test_stage7_replay_world_closure_r2.py; test_normal_learning_loop_closure.py | Failed terminal delivery cannot fall back to a complete scan; ambiguous allocation refuses |
| Portfolio/Risk and exit authority | capture.validate_chain and replay_manifest; stage7 normal replay/world R2 report | Replay of retained Risk input does not prove achieved fills or execution realism |
| Missing, substituted and corrupt sources refuse | test_missing_exact_source_cannot_be_replaced_with_latest; test_tampered_source_or_manifest_refused; test_parent_provenance_is_verified_beyond_the_outer_manifest_hash | Incomplete source coverage remains incomplete even if an outer digest is recomputed |
| Restart and duplicate delivery | test_current_state_does_not_change_history_and_restart_is_deterministic; test_duplicate_outcome_and_proposal_never_duplicate_application; test_restart_resumes_the_queue_exactly_once | Synthetic offline journal fixtures; no live deployment proof |

Historical reports: docs/superpowers/reports/2026-10-02-stage7-decision-replay-source-integration-r1.md
and docs/superpowers/reports/2026-10-03-stage7-normal-replay-world-consumer-closure-r2.md.
Historical reports supply navigation and scope, not current closure by themselves.
Source-manifest.json pins the exact inspected checkout bytes, including existing
uncommitted capture changes; tests.txt records the fresh focused regression run.

OUT-03 cannot close while its original dependencies remain unmet:

| Dependency | Canonical intake state | Next proof |
|---|---|---|
| OUT-01 | EVIDENCE_TO_MAP | Map normal scan/allocation/HOLD/Risk refusal/execution identity and original manifests; satisfy DEC-04 and DATA-02 dependencies |
| DATA-03 | EVIDENCE_TO_MAP | Reconcile the existing data03-consumer-cuts engineering package only after its final evidence and all consumer-cut closure conditions are verified |
| WRLD-06 | EVIDENCE_TO_MAP | Map exact-cut, future, multiple-revision and restart selection; satisfy WRLD-04 and DATA-05 dependencies |

The DATA-03 package is present in the dirty shared workspace. Its engineering
report explicitly says control-plane reconciliation is pending. This intake
does not adopt its PROVEN classifications as tracker closure or overwrite that
work. Unmapped evidence is not a finding that implementation is absent.
No additional implementation defect is established by this mapping.

Cross-tracker changes are limited to OUT-03 and GUI-02 status/evidence, a
matching dependency-map record and queue explanation in canonical YAML,
STATE and NEXT, optional Markdown references and bundle hashes. Requirements,
dependency edges, other row statuses and visual/live obligations stay intact.
OWN-01 remains EVIDENCE_TO_MAP with OUT-03 and MEM-02 dependencies.

Next prerequisite mapping: OUT-01 (first original OUT-03 dependency; not
automatically selected). Next GUI item: GUI-02 after OUT-03 and OWN-01 are
satisfied. Missing/unbound/unknown links must remain explicit in that work.
No Kernel, Dashboard, provider, venue, deployment or owner visual proof is
claimed by this package.

Validation: the first 82-case batch ended with 58 passes, one failure and 23
errors. The root filesystem had zero free bytes; SQLite raised `database or
disk is full`, followed by a missing-savepoint exception, and pytest could not
create numbered temporary directories. The affected cases are listed in
retry-cases.json. Retrying with pytest temporary files on the workspace volume
also exposed fixture use of the system temporary directory. Only the completed
first run's own `/tmp/pytest-of-sarmad/pytest-217` fixtures were removed, freeing
about 1.3 GB; unrelated temporary files were preserved. Exact retry outcomes
are retained in the retry logs. No all-pass assertion may be inferred from the
historical reports or from the 58 first-run passes.

The 14 control checks pass (control-validation.json). `git diff --check` passes
for these control documents. `graphify update .` completed its AST-only refresh;
no semantic extraction or LLM labeling was run.

Final validation: **82 distinct cases successful** across the original and
selective retry runs (58 + 15 + 9). The final nine cases pass with both
`TMPDIR` and `SQLITE_TMPDIR` on the workspace volume. Counts do not sum
repeated passing cases; only previously unsuccessful cases were retried.
All original failure logs remain present. OUT-03 remains BLOCKED because
OUT-01, DATA-03 and WRLD-06 are unmet; successful offline tests do not close
those dependencies or OWN-01/GUI-02. See terminal.yaml.
