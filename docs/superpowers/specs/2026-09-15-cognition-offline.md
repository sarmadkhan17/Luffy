# Offline cognition — first slice (2026-09-15)

Architecture: Astra. Implementation: Claude. Status: **offline verified only.**

A replayable, point-in-time chain that explains why an asset deserves
investigation:

    explicit input file -> observations -> attention + market state
        -> competing hypotheses (selected episodes) + baseline samples (all eligible)
        -> outcomes, resolved separately, only after their deadline

Code: `trader/cognition/` (`contracts.py`, `attention.py`, `hypotheses.py`,
`replay.py`). Tests: `tests/test_cognition_{contracts,attention,replay}.py`.

```bash
./venv/bin/python -m trader.cognition.replay --input FIXTURE.json --output TRACE.json [--end-ms MS] [--k 3] [--min-salience 2.0]
```

No default paths. No runtime hook.

## What this slice is not

- Not wired into the kernel, orchestrator, executor, analyst admission, the
  vault, the live DB or `config.yaml`. It imports only stdlib and itself
  (enforced by `test_cognition_imports_only_stdlib_and_itself` and a
  subprocess `sys.modules` check).
- No network, exchange or LLM calls. Tests block sockets.
- **No shadow, live, edge or profitability claim.** Passing tests say the
  mechanics are point-in-time correct on synthetic fixtures, nothing more.
- The hypothesis templates are illustrative research framings, not discovered
  strategies, edges or causal facts. `probability` is `null` until calibrated.
- Economic pressure never forces a trade: nothing here can trade. A decision
  that selects nothing is a normal, recorded outcome; every unselected
  eligible asset is kept as a baseline sample.

## Reconciliation with REQUIREMENTS.md

`REQUIREMENTS.md` (not edited) describes analysts emitting **votes**
(conviction/confidence) that blend into a decision, over a crypto majors +
top-alts universe. The newer requirements this slice follows differ, and win
for this scope:

| REQUIREMENTS.md | this slice |
|---|---|
| analysts vote a direction with conviction | attention is **nondirectional**; salience is symmetric in sign |
| votes blend into one score | competing hypotheses stay separate; contradicting evidence is kept, not netted |
| journal records decisions incl. skipped setups | kept: every member gets a row and reason; eligible-but-unselected assets are baseline samples with outcomes |
| crypto majors + top-12 alts | any instrument set, from timestamped membership in the input |
| agent accuracy feeds vote weights | no weighting; probabilities unset until a calibration exists |

Nothing here changes the running system's voting blend.

## Input contract (`cognition.input.v1`)

```json
{"schema": "cognition.input.v1", "timeframe": "1h",
 "decision_times": [ms, ...],
 "membership":    [{"symbol", "from_ms", "to_ms"|null, "available_ms", "source"}],
 "candles":       [{"symbol", "open_ms", "open", "high", "low", "close", "volume",
                    "available_ms"?, "closed"?, "source"?}],
 "participation": [{"symbol", "kind", "event_ms", "available_ms", "value", "source"}]}
```

- All timestamps are integer epoch ms, UTC.
- **Candle semantics.** `open_ms` is the bar open, aligned to the timeframe
  grid. The bar closes at `open_ms + tf`; that close is its event time.
  `available_ms` defaults to the close and may not precede it. A bar is
  usable at `as_of` iff `close <= as_of` and `available_ms <= as_of`. This is
  the `core.types.closed_bars` rule plus arrival time (stdlib re-statement, so
  no pandas import). `"closed": false` rows are rejected as `incomplete_bar`.
- **Revisions.** Several rows may share `(symbol, open_ms)` with different
  `available_ms`; at `as_of` the latest one available is used. Identical rows
  collapse. Two different rows with the same `available_ms` raise.
- **Membership** is as-of too. An interval's identity is `(symbol, source,
  from_ms)`; rows sharing it are revisions (e.g. a later row setting `to_ms`
  removes the asset). At `as_of` only the latest revision with
  `available_ms <= as_of` counts, so a removal changes nothing before it was
  known. Same identity and `available_ms` with a different `to_ms` raises.
- **Participation** series are `(symbol, kind, source)`. Per event the latest
  revision available at `as_of` is used; same series/event/availability with a
  different value raises. A change is `ln(current/previous)` within one series
  only — never across kinds or sources. Each series is its own observation,
  recording both endpoints (record id, event and available times, value); its
  `available_ms` is the max of the two endpoints.
- **Validation.** Non-finite numbers, non-positive prices, inconsistent OHLC,
  negative volume, misaligned or non-integer timestamps are rejected with a
  reason into `rejected_inputs`. Missing is `null`, never 0.

## Records (all carry `schema_version: cognition.v1`)

- **Observation** — `obs_id, kind, symbol ("*" = market), as_of_ms, event_ms,
  available_ms, source, status, value, detail`. Status is one of
  `ok | missing | stale | warmup | gap | degenerate | insufficient_cohort`.
  Kinds: `candle_window`, `volume_anomaly`, `volatility_transition`,
  `relative_return_divergence`, `participation`, `market_breadth`.
- **Decision** (attention/state) — `decision_id, as_of_ms, anchor_close_ms,
  deadline_ms, members, universe[] (status, eligible, rank, salience,
  components, dominant, reason, evidence ids), market_state, cohort, selected,
  observations, episodes, baseline_samples`.
- **Hypothesis** — `hyp_id, episode_id, template, statement, supporting_ids,
  contradicting_ids, prediction, deadline_ms, falsification, probability=null`.
- **Outcome** — `outcome_id, subject_id, subject_kind (hypothesis|baseline_raw|baseline_relative),
  deadline_ms, resolved_at_ms, status, measurement`. Stored in the trace's
  top-level `outcomes`, never inside the decision that produced the subject.

Trace floats are rounded to 12 **significant** digits, so a nonzero micro value
never becomes 0. The cohort's `sigma` is stored and used at full precision.

IDs are the first 16 hex of sha256 over canonical JSON of their defining keys
(decision: schema, config, timeframe, as_of; the rest chain off the decision).

## Attention (formulas in `attention.py` docstring)

N = `window` (20), S = `short` (5). Warmup: N+S+1 consecutive closed bars
ending at the anchor (newest bar closed by `as_of`). Missing anchor → `stale`;
missing leading bars → `warmup`; interior hole → `gap`. On the N+S log returns:

    volume_z    = (ln(1+v_anchor) - mean_N) / sd_N       of ln(1+v) over the prior N bars
    vol_trans_z = ln(sd(last S) / sd(first N)) / sqrt(1/(2(S-1)) + 1/(2(N-1)))
    div_z       = (R_S - median_cohort R_S) / (sd(first N) * sqrt(S))
    salience    = max |component| over components that exist

`div_z` exists only with ≥ `min_cohort` (4) eligible assets. An asset whose
return scale (sd, sd·√S, sd·√horizon) is not finite and positive is
`degenerate` / `unusable_return_scale`; a non-finite component is null. Ranking key
`(-salience, symbol)`; selected iff salience ≥ `min_salience` (2.0), no open
episode for the symbol (dedup until its deadline), and fewer than `k` (3)
already chosen. Omission reasons: `stale | missing | warmup | gap |
unusable_return_scale | no_components | below_min_salience | open_episode |
beyond_top_k | universe_cap`.

Market state: `breadth_up`, `market_z = median R_S / (median sd * sqrt S)`,
`dispersion`; `broad` iff |market_z| ≥ 1.5 and max(breadth_up, 1−breadth_up) ≥ 0.75.

**CPU bounds:** per decision O(members × (N+S+1) × revisions); members capped
by `max_symbols` (500); decisions capped by `max_decisions` (5000); K bounded;
resolution work is one exact-bar lookup per pending subject per evaluation time.

## Hypotheses (templates in `hypotheses.py`)

For each selected asset, all three are emitted; the episode's `framing` says
which the evidence currently favours:

| framing | rule |
|---|---|
| `unknown` (insufficient_required_evidence) | market breadth or the asset's divergence observation is not `ok` |
| `market_wide` | broad and not divergent (|div_z| < 2) |
| `asset_specific` | divergent and not broad |
| `contested` | broad and divergent — both kept, with each side's contradictions |
| `unknown` (no_discriminating_evidence) | neither |

Predictions, deadline D = anchor close + `horizon` (5) bars:

- `market_continuation`: cohort median forward return over (anchor, D] keeps the
  sign of the current median R_S, and |rel_forward_z| < `persist_z` (1.0).
- `asset_divergence`: |rel_forward_z| ≥ 1.0.
- `unknown`: neither competitor is confirmed.

`rel_forward_z = (fwd − median_cohort fwd) / (sd * sqrt(horizon))`, cohort frozen at
decision time, anchor closes and sd taken from the decision record.

## Outcomes and replay

- Evaluation times = decision times ∪ `--end-ms`. At each, pending outcomes
  with deadline ≤ T are tried with data available at T, then the decision (if
  any) is appended. Nothing earlier is modified.
- **Exact horizon, tolerance 0**: the bar closing exactly at D. A later bar is
  never used instead. `measurement` records `bar_close_ms` and
  `bar_available_ms`.
- Baseline outcomes are measured for **every eligible** asset (selected flag
  kept), so selected vs unselected forward behaviour can be compared for
  selection bias. Two per asset: `baseline_raw` (own forward return and
  |z|; needs only the asset's own deadline bar) and `baseline_relative`
  (needs the full cohort).
- **Full frozen cohort.** The cohort median, and so every hypothesis and
  `baseline_relative` outcome, requires the deadline bar of *every* asset in
  the decision's cohort. A subset is a different cohort and is never graded;
  the outcome stays pending (`cohort_incomplete`, with `missing_cohort`).
  Resolved relative outcomes record every cohort bar's close/available time.
- A hypothesis whose asset scale is unusable resolves `not_testable`
  (`unusable_scale`) instead of dividing.
- If the decision's cohort was below `min_cohort`, hypothesis and
  `baseline_relative` outcomes resolve as `not_testable` after the deadline. `market_continuation` is also
  `not_testable` when the cohort median R_S was exactly 0 (no direction to
  continue); `unknown` is then confirmed iff `asset_divergence` was not. Anything lacking its bar or cohort
  median at the final evaluation time is exported `unresolved` with a reason
  (`deadline_not_reached | deadline_bar_unavailable | cohort_incomplete`).
- **Validation of knobs.** `CognitionConfig` rejects bools, non-integers for
  counts, non-finite floats and out-of-bounds values (e.g. `min_salience` NaN,
  `broad_breadth` outside [0.5, 1], `divergence_z`/`persist_z` ≤ 0).
  `end_ms` must be a non-negative integer ms timestamp (CLI exits with usage).
- A resolved outcome is final. A revision that arrives after resolution does
  not change it.

## Acceptance → tests

1. Prefix invariance, late arrivals, incomplete bars —
   `test_future_data_and_late_arrivals_do_not_rewrite_earlier_decisions`,
   `test_bar_usable_only_once_closed_and_available`, `test_invalid_records_are_rejected_with_reasons`.
2. Broad vs isolated vs unknown, contradictions — `test_cognition_attention.py`.
3. Determinism, stable ties, ≤ K, dedup — `test_trace_is_deterministic_with_unique_ids`,
   `test_selection_is_bounded_with_stable_ties`, `test_duplicate_inputs_do_not_duplicate_episodes`,
   `test_open_episode_is_not_duplicated_on_the_next_decision`.
4. Outcomes — `test_outcomes_cover_selected_and_unselected_after_deadline`,
   `test_before_deadline_everything_is_unresolved`,
   `test_missing_deadline_bar_stays_unresolved_and_no_later_bar_is_substituted`,
   `test_late_deadline_bar_resolves_only_once_it_has_arrived`.
5. Offline / no authority — `no_network` autouse fixture, import allowlist and
   `sys.modules` checks, CLI subprocess with sockets disabled, `tmp_path` only.

## Known limitations

- Synthetic fixtures only; no real-market export has been run through it.
- Thresholds (2.0, 1.5, 0.75, 1.0) are unexamined defaults, not calibrated.
- Median-based cohort statistics are crude with small universes.
- Participation is recorded and cited as supporting evidence only; its
  staleness does not gate framing.
- An episode's dedup window ends at its deadline even if its outcome is still
  unresolved.
- The trace is one JSON document; very long replays will want streaming output.
