---
phase: "1"
slug: "correctness-booking-fixes"
# status lifecycle: draft (seeded by plan-phase) → validated (set by validate-phase §6)
# audit-milestone §5.5 distinguishes NOT-VALIDATED (draft) from PARTIAL (validated + nyquist_compliant: false) (#2117)
status: draft
nyquist_compliant: false
wave_0_complete: false
created: "2026-09-13"
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (main checkout venv) |
| **Config file** | none — default rootdir discovery from invocation cwd |
| **Quick run command** | `/home/sarmad/trader/venv/bin/python -m pytest tests/test_macro_guard.py tests/test_rolling.py tests/test_vector_backtest.py tests/test_null_baseline.py tests/test_selection_window_widens.py -q -p no:cacheprovider` (from the worktree root; no data dependency) |
| **Full suite command** | `/home/sarmad/trader/venv/bin/python -m pytest tests/ -q -p no:cacheprovider`, run with cwd = an isolated scratch worktree whose `data/candles.db` and `data/derivs.db` are symlinks to the main checkout's (never `luffy.db`) |
| **Estimated runtime** | quick ~30 s · full ~165 s |

**Safety:** the main checkout `/home/sarmad/trader` is the live production checkout. Never run tests or scripts with it as cwd, never check out another commit there, and never construct `Journal()` against its `data/luffy.db` (the constructor writes). Read the journal only via `sqlite3.connect("file:...?mode=ro", uri=True)`. Test runs write vault pages into `knowledge/` of whatever tree is cwd, so discard the scratch worktree afterwards.

---

## Sampling Rate

- **After every task commit:** Run the quick run command
- **After every plan wave:** Run the full suite command (scratch worktree with data) plus `scripts/backtest_equivalence.py` and `scripts/bench_vector_backtest.py` there
- **Before `/gsd-verify-work`:** Full suite must be green, both invariant scripts PASS, D-08 before/after evidence surfaced to the operator
- **Max feedback latency:** 60 seconds (quick run)

---

## Per-Task Verification Map

Task IDs are filled in by the planner. Rows are keyed by requirement and behaviour.

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| TBD | TBD | 1 | FIX-01 | — | N/A | unit | `… -m pytest tests/test_macro_guard.py -q` | ✅ | ⬜ pending |
| TBD | TBD | 2 | FIX-01 (D-02) | — | never writes live checkout | integration | full suite command (scratch worktree) | ✅ | ⬜ pending |
| TBD | TBD | 1 | FIX-03 | T-1-secrets | credentials only from `BINANCE_PROD_READONLY_*` env; never printed; GET-only | unit (no network) | `… -m pytest tests/test_production_fee_script.py -q` | ❌ W0 | ⬜ pending |
| TBD | TBD | 1 | FIX-04 | — | N/A | unit | `… -m pytest tests/test_vector_backtest.py -q -k "warmup or score_from"` | partial (existing warmup test ✅, score_from ❌ W0) | ⬜ pending |
| TBD | TBD | 1 | FIX-04 (D-05) | — | N/A | unit | `… -m pytest tests/test_rolling.py -q -k "4h or decay"` | ❌ W0 | ⬜ pending |
| TBD | TBD | 1 | FIX-04 (D-07) | — | N/A | unit | `… -m pytest tests/test_null_baseline.py -q` | ❌ W0 (new cases) | ⬜ pending |
| TBD | TBD | 2 | FIX-04 (D-09) | — | read-only on data | smoke | `… scripts/backtest_equivalence.py` and `… scripts/bench_vector_backtest.py` (scratch worktree) | ✅ | ⬜ pending |
| TBD | TBD | 2 | FIX-04 (D-08) | — | read-only journal access | manual evidence | new `scripts/` before/after evidence script | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_rolling.py` — 4h `_score_window` / `has_decayed` / `recent_verdict` case with a window ≤ `WARMUP+1` bars that must return real trade evidence (D-05 headline)
- [ ] `tests/test_vector_backtest.py` — `simulate(..., score_from=N)` scores only bars ≥ `max(WARMUP, N)`; omitted `score_from` is identical to today's behaviour; existing `test_warmup_signals_are_ignored` stays unmodified
- [ ] `tests/test_null_baseline.py` — short-frame case and too-few-distinct-offsets case both yield empty/UNTESTED, never a low-variance null
- [ ] FIX-03 script smoke test — missing-credential error path, no network
- [ ] D-08 read-only evidence script under `scripts/`

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Production fee read against the live venue | FIX-03 | Needs a production key. Already measured 2026-09-13 (`01-production-fees.json`); a re-run is optional and needs a fresh reading-only, IP-restricted key | Operator sets `BINANCE_PROD_READONLY_KEY/SECRET` and runs the script; compare with the recorded JSON |
| Before/after decay + admission readings for Donchian and `spec_funding_filtered_trend_pullback` reviewed before the kernel runs a decay sweep on fixed code | FIX-04 (D-08) | Operator judgement on a live-book behaviour change | Run the evidence script on pre-fix and post-fix commits; present both readings; operator acknowledges before deploy/restart |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 60s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
