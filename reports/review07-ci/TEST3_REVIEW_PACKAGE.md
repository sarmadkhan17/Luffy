# test_statistical_policy_and_frozen_evidence_unchanged — evidence for named-human review

Evidence only. The test and every pin are unchanged; nothing here is approved.
Baseline `fc6c0b2`; candidate = combined Review 07 tree (`a11bf24`).

Each assertion of the test was evaluated independently:

| # | Pin | Result | Introduced by |
|---|-----|--------|---------------|
| 1 | `git diff fc6c0b2` empty for `combo.py`, `fdr.py`, `thresholds.py` | FAIL (`thresholds.py` only) | c9cfe7a (D1-D4) |
| 2 | historical docs (`docs/tracker`, `superpowers/evidence`, `superpowers/specs`) byte-identical | pass | — |
| 3 | every config section except `strategies` equal | FAIL: `research`, `tv_harness`, `crawler` | `referee_max_attempts` c9cfe7a; `run_timeout_s`, `passages_per_mine` ef53aa3 |
| 4 | `strategies` keys equal outside the lifecycle allow-list | FAIL: `tv_interval`, `tv_period`, `tv_min_oos_trades`, `tv_require_positive_oos` removed | ef53aa3 |
| 5 | `paper_probation_trades == 10` | pass | — |
| 6 | `vector_backtest.py` decision gate `min_trades=max(3,int(train.trades*0.2)), min_pf=1.0` present before and after | pass | — |

Pin 1 fails first, so the test stops there; pins 3-4 also fail on the remediation commit `81565a0` alone (`tv_harness`).
`fdr.py` and `combo.py` (alpha-investing and the combination definition) are byte-identical to `fc6c0b2`.

## Contract differences (old -> new)

### thresholds.py (c9cfe7a) — behaviour change
```
old:  try: arr = _values(tree, ctx) except: continue ; total += int(arr.size)
new:  total += len(ctx.index) ; try: arr = _values(...) except: continue
```
`finite_frac = n_finite / total`. A symbol whose feature evaluation raises used to be dropped from the
denominator (it could not lower `finite_frac`); it now contributes its rows to the denominator and no finite values.
- Quantile cuts (`p10..p90`, `min`, `max`) and `n` are unchanged for every input.
- `finite_frac` is lower or equal; `usable = n >= MIN_SAMPLES and finite_frac >= MIN_FINITE_FRAC` can therefore flip
  True -> False, never False -> True. Stricter, and it removes an optimistic bias; it does not change a threshold value or alpha.
- `arr.size` also equalled `len(ctx.index)` whenever evaluation succeeded, so results differ only when an evaluation raises.
- Reviewer decision: accept as an owner-approved "eligible discovery accounting" correction (and update the pin to the new bytes), or revert.

### config.yaml — removed keys (values at `fc6c0b2`); no value of any risk or research-budget key changed
| key | old | reader at `fc6c0b2` | status now |
|-----|-----|---------------------|------------|
| research.referee_max_attempts | 3 | runner retry loop | reader and loop removed (c9cfe7a); a crashing look is charged p=1 once |
| crawler.passages_per_mine | 10 | brain/crawler.py `mine_budget` | reader removed (ef53aa3) |
| tv_harness.run_timeout_s | 90 | brain/tv_harness.py | reader removed (ef53aa3) |
| strategies.tv_interval / tv_period | 1h / 1y | none (tv.py) | tv.py deleted (ef53aa3) |
| strategies.tv_min_oos_trades / tv_require_positive_oos | 5 / true | scripts/validate_population.py | script deleted (ef53aa3) |
| strategies.max_active, tv_validation, auto_demote_drawdown_pct | 6 / true / 8.0 | none (already allow-listed) | unchanged from the earlier lifecycle approval |

Only `strategies.paper_probation_trades` 15 -> 10 is a changed value (lifecycle approval, already allow-listed).
The CI's RISK_POLICY_CONFIG_CHANGED finding is this set; no `risk:` value differs.

### Behavioural effect to confirm
`referee_max_attempts` was a held-out-budget rule: 3 failed dispatches then a p=1 charge. Now: reserve before dispatch,
one failure charges p=1, no re-dispatch (`test_a_crashing_look_is_charged_once_without_retry`). Spend is never lower than before.

## Decisions requested
1. thresholds.py denominator change: accept (re-pin) or revert.
2. Removed dead config keys: accept (extend the test's allow-list) given their readers were deleted in the same commits.
3. charge-once replacing three attempts: accept as D1's approved held-out budget behaviour.
