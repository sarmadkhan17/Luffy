# LUFFY-CAPACITY-EVIDENCE-INTEGRATION-R1 — integration report

Date: 2026-10-01. Verdict: PASS. State: TESTED (focused tests, with one reproduced baseline failure). Uncommitted. NOT_DEPLOYED.

## Provenance

| Role | Worktree | Branch | Commit |
|---|---|---|---|
| Target (authority) | `workspaces/capacity-evidence-integration-r1` | `luffy-capacity-evidence-integration-r1` | `6b7f0f7f434fbe6b7c227595a43d4a325dc849e6` (F1-F4 authority fix, pushed) |
| Source (capacity evidence) | `workspaces/capacity-evidence-capture-r1` | `luffy-capacity-evidence-capture-r1` | `b35a4bc` + uncommitted package |
| Control-plane authority closure | `workspaces/strategy-capacity-r1` | `luffy-strategy-capacity-r1` | `6b7f0f7` + uncommitted STATE/NEXT |

Source and authority worktrees were only read; their status is unchanged.

## Method and file manifest

Tracked source changes were replayed as `git -C <source> diff b35a4bc -- <5 files>`
applied with `git apply` onto 6b7f0f7 (applied cleanly; kernel hunks at +24 line
offset only). Untracked files copied byte-for-byte (`cp -p`, verified with `cmp`).

Replayed (tracked):
- `trader/core/instrument_registry.py` — identical to source
- `trader/data/binance_usdm_registry.py` — identical to source
- `trader/strategy/capacity.py` — identical to source (no authority delta vs b35a4bc)
- `tests/test_strategy_capacity.py` — identical to source
- `trader/kernel.py` — delta applied, not overwritten. The integration diff vs
  6b7f0f7 equals the source diff vs b35a4bc hunk-for-hunk (only index/line
  offsets differ). F1/F4 kernel hunks (`SpecExit.from_spec(versioned=...)`,
  `live_entry_block` fence in `_try_enter`, `se.frozen_levels`) are untouched.

Copied (new):
- `trader/engine/evidence_capture.py`
- `trader/engine/execution_evidence.py`
- `trader/observability/depth_evidence.py`
- `scripts/capacity_depth_shadow.py`
- `tests/test_capacity_evidence_capture.py`
- `docs/superpowers/reports/2026-10-01-capacity-evidence-capture-r1.md`

Unchanged from 6b7f0f7: `trader/engine/executor.py`, `trader/engine/exits.py`,
`trader/strategy/factory_handoff.py`, `trader/research/runner.py`,
`trader/research/combo.py`, `trader/core/reason_codes.py` and their tests.

Control plane: `STATE.yaml`, `NEXT.yaml` (package-specific additions only; see below).
This report is the only other new file.

## Control-plane reconciliation

From authority worktree: strategy capacity now `committed b35a4bc`;
`completed_cross_stage_authority_fix_r1` (F1-F4 PASS, commit 6b7f0f7);
intelligence shadow status corrected to `AUTHORIZATION_PENDING`.
From source worktree: `completed_capacity_evidence_capture_r1` (TESTED) and the
shadow-process list (renamed `shadow_processes`; depth collector STOPPED).
Added `completed_capacity_evidence_integration_r1`. Stage 5 recorded incomplete:
`VERSIONED_PAPER_EXECUTION_MISSING`. NEXT `work_package` is
`LUFFY-VERSIONED-PAPER-EXECUTION-R1 SELECTED_NOT_STARTED`; the source's
Portfolio Allocator selection was not imported. The previous intelligence-shadow
work package moved to `intelligence_spine_shadow_package`, with its parked
boundaries left as they were.

## Depth collector: STOPPED

`/mnt/luffy-data/luffy/shadow/capacity-evidence-r1-20261001` (read only):
- `STOP` file present (02:42)
- final `collector_log.jsonl` line `{"stopped": "stop_file", "ms": 1790811736012}`; 12 cycle lines
- `storage.json`: 192 snapshots, 0 rejections, 299008 B; `depth.db` (opened
  read-only/immutable): `depth_observations` 192, `depth_rejections` 0
- no `capacity_depth_shadow` python process present

Before/after checks of all eight files in that directory matched SHA-256,
size and modification time exactly; the STOP file was preserved. The final
process check still found no depth collector and the same intelligence runner.

Intelligence shadow runner PID 2949543 is alive and was not touched. Being alive
does not count as authorization (status remains AUTHORIZATION_PENDING).

## Focused tests

Imports resolve to this worktree (`trader/__init__.py`,
`trader/engine/evidence_capture.py`, `trader/strategy/capacity.py` under
`workspaces/capacity-evidence-integration-r1`).

```
OPENBLAS_NUM_THREADS=1 /mnt/luffy-data/luffy/production/venv/bin/python -m pytest -q -p no:cacheprovider \
  tests/test_cross_stage_authority.py tests/test_strategy_factory_handoff.py \
  tests/test_strategy_capacity.py tests/test_capacity_evidence_capture.py \
  tests/test_current_truth_completion_clock.py tests/test_current_truth_integration.py \
  tests/test_current_truth_corrections.py tests/test_current_truth_contract.py \
  tests/test_trade_provenance.py tests/test_entry_control_fence.py \
  tests/test_entry_geometry_end_to_end.py tests/test_reconcile.py \
  tests/test_reconcile_alignment_commits.py tests/test_portfolio_observation.py \
  tests/test_decision_rejection_reason_codes.py
```

Result: **569 passed, 1 failed** (110 s). The one failure:
- `tests/test_decision_rejection_reason_codes.py::test_every_code_referenced_exists_and_every_code_is_used`
  (`trader/cognition/research_bank_view.py:219 rc.AUTHORITY`). This is a known
  baseline failure: it also fails at 6b7f0f7 code (rerun of only that node in the
  authority worktree). Not fixed here.

`git diff --check`: clean (trailing-whitespace check of the new files also clean).
The full suite was not run.

## Evidence contracts retained (TESTED, from source package)

- `account-margin-observation.v1`: availableBalance comes from the same `/fapi/v3/account`
  response used for Risk equity. No new request is made, and failures are isolated.
  If the field is absent, the value is UNAVAILABLE.
- `venue-position-snapshot.v1`: immutable `venue_position_snapshots`, replayable into capacity receipts.
- `depth-observation.v1`: public shadow collector (now STOPPED).
- `execution-evidence.v1`: exact-order attributed fills and recorded reference prices only; missing reference stays UNAVAILABLE. The source package reported 0 production fills/measurements on 2026-10-01; this integration did not remeasure production counts.
- Venue filters: LOT_SIZE maxQty, MARKET_LOT_SIZE min/max/step. Max notional NOT_PUBLISHED_IN_EXCHANGEINFO.
- Liquidity capacity model UNAVAILABLE, so effective capacity UNAVAILABLE.
  Leverage brackets UNAVAILABLE. Account eligibility UNKNOWN.

## Independent integration verification

The parent reviewer verified ten replayed files byte-for-byte against the source.
Kernel's only changed methods versus authority HEAD are `_detect_exchange_exits`,
`_fetch_balance_fresh`, `_risk_step` and the added `_record_capacity_evidence`;
all four match the capacity source by AST. The five authority methods
`_try_enter`, `_protection_for`, `_load_spec_population`, `_research_handoff`
and `_install_version` are unchanged by AST, and the authority implementation
files and regression tests remain byte-identical to HEAD.

Two additional temporary-journal reproductions recorded successful available-margin
and venue-position evidence, then attempted entry through both Kernel and the real
Executor over a fake venue. SHADOW remained blocked by
`version_not_live_authorized:SHADOW`; owner-approved without established capacity
remained blocked by `version_capacity_not_established`. Both sent zero orders.
The focused suite above directly reran F1-F4 on the combined tree; all passed.

## Deployment and next package

NOT_DEPLOYED. Nothing was committed, staged, pushed or restarted, and no
production or shadow data was written. Next package:
`LUFFY-VERSIONED-PAPER-EXECUTION-R1` (SELECTED_NOT_STARTED).

## Limitations

- Focused tests only. The source package's full-suite parity claim (75 failed /
  16 errors, same as b35a4bc) was measured on b35a4bc and was not re-run on 6b7f0f7.
- Kernel capture remains undeployed, so whether the production account response
  contains availableBalance is UNVERIFIED.
- The stopped depth collector's retained observations are shadow evidence only;
  they establish neither a liquidity model nor effective capacity.
