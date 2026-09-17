# Gate 2 — the prediction test. DESIGN ONLY.

Written 2026-09-16, corrected the same day after review. **Nothing here is
implemented in the runtime, and nothing may be wired into it until the
calibration blockers in §12 are closed.** No runtime, config or ledger change
accompanies this document.

**No ledger evidence was consulted, and none is written.** No row of
`research_tests`, `research_candidates` or any other table was read while
writing this, and this document performs no ledger operation. It therefore
makes **no claim** about what those tables currently hold — not that a `gate2`
row is absent, not that a `gate1` row is absent, not that a reservation does or
does not exist. Two things are stated instead, and both are established without
a ledger read:

- **the recorded stop stands** — `research.referee: false` and
  `research.handoff: false`, the gate decision of 2026-09-15;
- **no code can write a gate-2 row** — `trader/research/gate2.py` does not
  exist, and `ledger.py` has no `reserve_test` or `settle_test`.

Scope: how a thesis that reached `well_formed_untested` (the contract in
`trader/research/thesis.py`) would be tested against held-out outcomes, what
it would be charged, and what it must NOT be allowed to conclude. The worked
subject is candidate `55243573515adc3b` and its registered thesis
`local_trend_pullback` (artifact
`docs/superpowers/artifacts/2026-09-16-candidate-55243573515adc3b/`), whose
two predictions are

- **H1** `ret(6) * ret(24) < 0`, `mean_r`, `>`, `complement`
- **H2** `pct_rank(efficiency_ratio(30), 200) > 0.5`, `mean_r`, `>`, `complement`

---

## 1. What gate 2 asks, and what gate 1 already asked

Gate 1 (`referee.gate1`) rotates the WHOLE rule's entry times — per-symbol in
`consistency`, and under one common offset in `portfolio_null.common_rotation`
— and asks whether entering at those times beats entering at an arbitrary
offset. It is a test of **entry timing**.

Gate 2 holds the entries fixed. It takes the rule's realized trades exactly as
the engine produced them, attaches a label to each one from information that
had closed by that trade's own entry bar, and asks whether the label separates
mean R.

**Gate 1's common entry rotation does not automatically test the
conditional-outcome null.** Rotating entries destroys the trade population
itself: a rotated draw has different entry bars, different stops, different
exits, and therefore a different set of labelled outcomes. It can say nothing
about whether a label attached to the *unrotated* trades carries information.
A gate-2 null must resample at the level of the realized trades, not of the
entry signal. Passing gate 1 is a prerequisite (§9), never a substitute.

## 2. The partition (P1, never P2)

**Post-partition only.** Run the unchanged compiled rule, build each leg's
`trade_table`, take `walk_table(table, long, short, risk)` → `[(entry_i,
exit_i, r)]`, and only then split that list by the condition evaluated at
`entry_i`. The trade boundaries, the R multiples, the one-position-at-a-time
walk and the dust guard are all identical to the untouched rule, so subset and
complement are a true partition of one population.

The rejected alternative is re-entry (`entries AND cond`). Because
`walk_table` holds one position at a time, suppressing an early entry lets a
later one open; subset and complement then partition nothing and the
comparison has quietly become a different strategy. Same family as "filters
kill the tail".

**Invariant, checked every run:** `|subset| + |complement| + |unknown|` equals
the length of the untouched `walk_table` output, and the multiset of R values
over the three groups equals the untouched multiset.

### 2a. Timing: `entry_i` is the signal bar and the fill bar — the engine's convention, not a claim about live execution

`vector_backtest._trade(i, ...)` enters at `closes[i]` — the entry price IS
bar `i`'s close. `walk_table` opens a trade at bar `i` when `long[i]` or
`short[i]` is set, and `legs_for` computes those arrays from
`compiled.entries(sym_frame, ...)`, a function of bars up to and including
`i`. A prediction condition evaluated on the same frame at the same index `i`
therefore uses exactly the information that had closed when the **modelled**
fill price was set. Within the engine's convention there is no fill-bar
leakage: the condition cannot see bar `i+1`, and it is allowed to see bar `i`,
because the modelled fill is bar `i`'s close.

**That is an internal consistency statement, not proof that such a fill is
attainable live.** Filling at a bar's close requires acting at the instant the
bar closes. The live path signals on a closed bar and sends its order at best
60 seconds later — hours later when risk blocks it — which is why
`signal_bar_age_min` exists and why CLAUDE.md records the signal-to-fill delay
as unmeasured. So gate 2 reproduces the engine's point-in-time convention and
keeps its features honest **with respect to that convention**. Whether the
price is obtainable is separate admission and model risk, shared with every
other number this engine produces, and gate 2 neither tests it nor improves it.

Two requirements follow and must be asserted, not assumed:

1. The condition is evaluated on **`bundle.sym_frames[sym]`**, the same frame
   object `legs_for` passed to `compiled.entries`, indexed identically.
   `walk_table` returns the raw frame index `i`; `fills_for` maps through
   `l._grid` to a shared bar grid. Gate 2 uses `walk_table` directly and never
   the grid index.
2. A point-in-time probe (§11, row 12) appends synthetic future bars to the
   frame's tail and asserts every mask value at every `entry_i` is unchanged.

### 2b. Unknown is a third group, never the complement

`dsl.evaluate_bool` ends in `v.fillna(False).astype(bool)`. Under it a bar
where the condition is **undefined** — warmup, a NaN feature, a stale `ref` —
reads as `False` and lands in the complement. For H2 that would put every one
of the first 230 bars of each symbol into "below-median efficiency", which is
a fabricated measurement in the sense CLAUDE.md already names: a default that
reads as a real reading.

Gate 2 therefore must **not** call `evaluate_bool`. It needs
`mask_with_unknown(tree, ctx) -> (true_mask, known_mask)`:

- Each leaf `Compare` is *known* at bar `i` only where every operand, obtained
  through `dsl.evaluate` as a numeric series, is finite.
- **Initial implementation uses the strict rule:** a bar is known only when
  every leaf of the condition is known there. Kleene three-valued combination
  (`A and B` is known-False when either side is known-False) is a later
  refinement and needs its own calibration row before it is allowed; strict is
  the fail-closed choice because it can only shrink both arms.
- A trade whose entry bar is not known goes into `unknown`. It is counted,
  reported per symbol and per slice, and enters **neither** arm and **no**
  bootstrap block.

## 3. The metric

`mean_r` is the **trade-weighted** arithmetic mean of `r` over a group: every
complete trade in the group counts exactly once, at the R the engine produced.
`r` is the engine's own R multiple from `walk_table` — unit P&L divided by the
stop distance, with `taker_fee_pct`, `slippage_atr_frac` and the signed funding
series already charged inside `_trade`. Gate 2 recomputes no cost and
re-derives no R, so the fee and funding model is the engine's by construction.

**No trimming of any kind.** No winsorizing, no outlier rule, no per-symbol
reweighting, no cap on a single trade's contribution. A trend book's R
distribution is right-skewed with a fat tail, and the tail is where the
mechanism's return lives — "filters kill the tail" is already on the record as
what happens when it is cut. Any future trimming would be a different statistic
and would need its own registration and its own calibration.

**What `walk_table` does and does not enforce.** From source: `walk_table` reads
`risk_per_trade_pct` and `equity` only through its dust guard
(`staked * pos[i] < 10`); the R values themselves come from the precomputed
trade table and do not depend on the balance, exactly as `_trade`'s docstring
states. **`max_open` is not read by `walk_table` at all.** It walks one symbol's
long/short pair, one position at a time, with `cursor = exit_i + 1`. So the
gate-2 population is per-symbol sequential and is **not** concurrency-capped;
nothing here may claim the live 8-slot cap was applied to it. That cap changes
compounded return, not the per-trade R multiples this metric averages, which is
why the metric is defensible without it — but the distinction must be stated,
not blurred.

Universe and geometry are **fixed** at the candidate's own: `tf=4h`,
`geo=trail`, the rule's `ExitSpec` unchanged, the slice's symbol list
unchanged. Gate 2 may not re-tune, re-time or re-scope anything.

### 3a. The signed contrast

For a prediction with target group `S`, baseline group `B` and `relation`:

```
mu_S = mean_r(S);  mu_B = mean_r(B)
delta_hat = (mu_S - mu_B) if relation == ">" else (mu_B - mu_S)
```

`delta_hat` is the **signed mean-R contrast**, oriented so that the thesis
predicts `delta_hat > 0`.

**A nonpositive effect is a FAIL, decided independently of the p-value.**
`delta_hat > 0` means strictly greater than zero; `delta_hat == 0` is not
positive. A prediction with `delta_hat <= 0` fails whatever `p` reads, and the
two are recorded as separate fields so neither can stand in for the other.

No claim is made that a one-sided p at `delta_hat <= 0` is necessarily ≥ 0.5.
An earlier draft asserted that; it is withdrawn. The p here is a bootstrap
estimate over a discrete, skewed and possibly degenerate resampling
distribution, and no such inequality is guaranteed for it. Relying on one would
let a numerically small p substitute for the sign check.

## 4. The null: a common-calendar non-overlapping block (cluster) bootstrap

### 4a. Why not a label permutation

Permuting the subset label across trades assumes trades are exchangeable. They
are not: on a market-wide rule every symbol receives the same entry condition
on the same bar, so the trades cluster in calendar time, and both H1 and H2
are local trend states that are themselves persistent. A permutation null
would price a shared-regime coincidence as information — the same error the
raw cross-symbol `consistency_p` makes, which is why gate 1 charges
`consistency_p_dependent`.

### 4b. The design — and its correct name

**It is a non-overlapping block (cluster) bootstrap, not a moving-block
bootstrap.** The blocks are consecutive, disjoint calendar intervals, drawn
with replacement; the cluster is a calendar interval shared by every symbol. No
overlapping windows are formed at any point. An earlier draft called this a
moving-block bootstrap; that is wrong and is corrected here, because the two
resampling schemes have different consistency conditions and citing the wrong
one imports a guarantee this design does not have.

**Blocks are calendar intervals on one shared UTC clock**, not per-symbol
trade indices. Every symbol's trades inside a block move together, so
cross-symbol dependence inside a block is preserved exactly as it occurred.

- `BLOCK_DAYS = 30`, **fixed a priori.** The evaluated span is cut into
  consecutive non-overlapping 30-day calendar blocks from the first eligible
  entry bar.
- A trade belongs **wholly to the block containing its entry bar**. A
  boundary-crossing exit — a trade that opens in block `k` and closes in block
  `k+1` or later — is not split, not duplicated and not dropped. The same rule
  applies to both arms and to every bootstrap draw. Overlapping trades across
  symbols are kept as they occurred: within a symbol `walk_table` never
  overlaps, across symbols it does, and that overlap is part of the dependence
  the block is preserving.
- **Assignment by entry has a cost, and the cost must be measured, not
  assumed.** A trade whose holding period is a material fraction of 30 days
  carries outcome information across the block edge. Resampling blocks
  independently then breaks a dependence that really exists, which narrows the
  null and inflates the false-pass rate. The size of the effect depends on the
  ratio of holding period to block length, so it is a **calibration input**
  (§11c row 6b): a fixture sweeping exit durations from a small fraction of the
  block to beyond it, with the measured false-pass rate reported at each. Until
  that number exists, "not split, not dropped" describes the rule being used —
  it does not establish that the rule is harmless.
- A resample draws `K` blocks with replacement, where `K` is the number of
  blocks in the evaluated span, and rebuilds the **complete trade cohort** of
  each drawn block — every trade of both arms in that block, never a per-arm
  resample, which would break the within-block correlation between the arms.
- `delta_b` is recomputed from the resampled cohort with the same formula as
  §3a.
- **Null-centering:** the bootstrap describes the sampling distribution of
  `delta_hat`, not of zero. The null draw is `delta_b - delta_hat`. The
  one-sided p is

  ```
  p_hat = (1 + #{ b : delta_b - delta_hat >= delta_hat }) / (B + 1)
  ```

  in the same arithmetic form as `portfolio_null.common_rotation`.

- **`p_hat` is APPROXIMATE, and its only guaranteed property is Monte-Carlo
  resolution.** The `(1 + ·)/(B + 1)` form bounds the MC error of a finite draw
  count and puts a floor of `1/(B+1)` under the answer. It does **not** confer
  the exact finite-sample validity a permutation test has. A permutation p is
  exact because the permutation group is an exact symmetry of its null;
  resampling calendar blocks with replacement is not such a symmetry. The
  resampling distribution only *approximates* the sampling distribution of
  `delta_hat`, and how well it does so under a shared factor, long memory and
  boundary spillover is exactly what §11c rows 1–3 and 6b must measure. Until
  they have, `p_hat` is an **uncalibrated statistic** and must be reported as
  one. It is never to be described as exact.

- **Degenerate draws are counted, never dropped.** A resampled cohort can leave
  an arm empty — no drawn block carried it — or carry zero variance, and
  `delta_b` is then undefined. Dropping such a draw from the denominator would
  shrink `B` after seeing the data and bias `p_hat` downward, so:
  - an undefined `delta_b` counts **toward the numerator** (treated as at least
    as extreme as observed), which can only raise `p_hat`;
  - the denominator is always `B + 1`; the draw count is fixed before the
    resample and never changes;
  - the number of degenerate draws is recorded in `detail`, and if it exceeds
    a predeclared `DEGENERATE_MAX_FRAC` (0.20 in this design) the prediction is
    **untestable**: `p = 1.0`, charged, reason recorded. A statistic whose null
    distribution is mostly undefined has not been estimated.

- **A degenerate observed statistic is untestable, not significant.** If
  `delta_hat` itself is undefined — an empty arm, or zero variance in the
  pooled R values so the contrast has nothing to be compared against — the
  prediction records `p = 1.0`, charged, no pass. Zero variance is not a
  result.

- **Sensitivity at 60 and 90 days is DIAGNOSTIC ONLY.** It is computed,
  recorded in `detail`, and **never** substituted for the 30-day p. Selecting
  the best block length after seeing three p-values is the failure this whole
  pipeline exists to prevent. The charged p is the 30-day p, always.

### 4c. Minimum evidence — design choices, not proofs

Per evaluated slice, a prediction is **eligible** only if all hold:

| floor | value | what it is |
|---|---|---|
| non-overlapping 30-day blocks with BOTH arms non-empty | ≥ **12** | a design choice |
| complete trades in the slice | ≥ **60** | matches `referee.MIN_TRADES` |
| complete trades per arm | ≥ **20** | a design choice |

These numbers are **design choices requiring synthetic calibration** (§11,
§12). They are **not** a proof of independence and must never be described as
one. 12 blocks is 360 days of calendar; whether 12 correlated 30-day blocks
carry 12 blocks' worth of information is exactly what the calibration has to
measure, and the answer this repo already has for an analogous question — 15
correlated perps carry about 4 markets' worth of evidence — says the honest
prior is "fewer than 12".

**Inference from this gate remains EXPERIMENTAL until calibration establishes
its validity under shared regime and long memory.** Block bootstraps are
consistent for weakly dependent series at a block length that grows with the
sample; a **fixed** 30-day block on a market with long memory, a market-wide
entry condition and holding periods that cross block edges has no such
guarantee here, and none is claimed.

## 5. The leakage firewall

Three walls, each independently sufficient to void a gate-2 result.

**Wall 1 — code.** Only `referee.py` and `job.referee_job` may read held-out
prices. Gate 2's evaluation runs inside the same child process, reached
through `referee`'s existing `load_heldout`, and returns only the summary
`detail` dict. `thesis.py` stays pure and never gains a data path. A test
asserts `trader/research/gate2.py` imports no store, no journal and no
network client.

**Wall 2 — time.** Every trade counted by gate 2 must have an entry bar that
closes at or after the thesis registration instant (§7), and an exit that
completes at or before the evaluation instant. Feature *inputs* may reach back
before registration — a lookback is not an outcome — but no *outcome* may.

**Wall 3 — hashes.** A gate-2 result is only interpretable against the exact
inputs that produced it. The **sealed bundle hash** (§8) pins them, is written
into the reservation before any price is read, and is re-checked on replay.

## 6. Evidence-access audit — A and B, separately

Before any read, each held-out slice gets its own audit row. The question is
not "did gate 2 read it" but "has any outcome from this slice already been
seen by anyone who influenced this thesis".

| verdict | meaning | consequence |
|---|---|---|
| `clean` | no recorded look, no appearance in any report, no development inspection | usable as gate-2 evidence |
| `inspected` | appeared in a report, a screen, an ad-hoc measurement or a prior gate look | **development only** — never gate-2 evidence |
| `unknown` | exposure cannot be established either way | treated as **`unavailable`**, identical to `inspected` |

**Unknown exposure ⇒ unavailable.** There is no benefit of the doubt: the
default for an unestablished provenance is exclusion.

Audit inputs, per slice, all offline: whether `research_tests` holds any row
for this hash naming the slice; whether `research_candidates.gate1`/`gate3`
record a reading on it; whether any file under `docs/superpowers/` or
`knowledge/` quotes a number from it; and an explicit human declaration of
development inspection, recorded as a signed line in the spec, not inferred.

**Applied to `55243573515adc3b` today:** slice A (unseen markets before the
cut) and slice B (all markets from 2025-03-08T16:48Z) are both **`unknown`**.
No ledger was read for this audit, so nothing here asserts what
`research_tests` does or does not contain — and the verdict does not need it.
`unknown` is the fail-closed default, and an absent record would not be
evidence of non-exposure in any case: the candidate's whole discovery history
was explored by hand while the search was being written. Both slices are
therefore **unavailable**. The consequence is §7, not a waiver.

## 7. The prospective cut

Because A and B are unavailable, the first admissible gate-2 evidence for this
candidate must come from bars that did not exist when the protocol was
registered.

**A prospective evaluation is a SEPARATE registration, not a relabelling.**
Slices A and B were named as this candidate's held-out slices and were audited
`unavailable`. They do not become available by being renamed, and a
registration naming A/B cannot be settled with prospective data. The
prospective evaluation is registered anew, with its own protocol record, its
own required-slice set (the prospective slice alone), its own endpoints and its
own place in the LORD++ sequence. The A/B registration, if one is ever written,
stays unsettled and unusable.

- **Registration is an explicit, sealed protocol record — not a file mtime.**
  `registration_ms` is a UTC timestamp written *inside* a registration JSON and
  covered by `registration_sha256` (§8). That record must state: the exact
  protocol (partition, metric, contrast, null, block length, draw count, seed,
  floors, decision rule), the data endpoints (the slice's symbol list,
  `first_entry_ms`, `evaluation_ms`), the full text of H1 and H2 with their
  required slices, and every revision with its own timestamp and reason. It is
  written **after** the design and calibration approvals of §12 — not before.

  **The artifact written today is NOT a registration.** An earlier draft said
  `registration_ms` "is the `manifest.json` write time"; that is withdrawn. An
  mtime is a filesystem attribute: mutable, unsealed, recording no protocol, no
  endpoint and no approval. The thesis freeze pins **what was predicted**; it
  does not start a protocol. **No prospective collection has begun, and no gate
  timer has started.**
- **Closed-data warmup — it costs bars, not calendar.** The first eligible
  entry bar is the first 4h bar whose close is ≥ `registration_ms`. Its index in
  the loaded frame must be ≥ `max(vector_backtest.WARMUP, L)`, where `L` is the
  longest lookback in the rule and in both conditions measured in base bars:
  `zscore(close, 96)` inside `ref("alts", …)` = 96 alt-index bars, `ret(24)` =
  24, `pct_rank(efficiency_ratio(30), 200)` = 230, and `WARMUP = 210`, so
  `L = 230`.

  Those 230 bars may be — and must be — taken from data that **had already
  closed before registration**. A lookback is an input, not an outcome (§5
  Wall 2). So the warmup does **not** add 38 days to the accrual clock, and
  nothing in this design licenses adding them. The requirement is only that the
  loaded frame *begins* at least `max(WARMUP, L)` bars before the first
  eligible entry bar, and that `ref("vix", ret(30))` has 30 daily VIX closes
  with a feed fresh inside `max_stale_ms = 100h`.
- **Accrual: ≥ 360 days.** 12 non-overlapping 30-day blocks each carrying both
  arms is at minimum 360 days of forward calendar after registration — more
  whenever a block fails to populate both arms. That is the real cost of this
  design and it is not negotiable downward by shortening the block: the block
  length is fixed a priori (§4b).
- **Completion policy, and the engine's terminal exit.** `_trade` sets
  `end = min(i + max_bars, n - 1)` and defaults `exit_i, exit_px = end,
  closes[end]`. A trade that has not stopped, targeted or signalled out is
  therefore **force-closed at the last bar of the loaded frame**, at that bar's
  close, and the tuple it returns is indistinguishable from a genuine
  `max_bars` time exit. Counting those as realized outcomes prices a frame-edge
  artifact as a result. So:
  - a trade is **complete** only if `exit_i < n - 1`, or
    `i + max_bars <= n - 1` (its time exit was reachable inside the frame);
  - anything else is **incomplete**: excluded from both arms, from every block
    and from every bootstrap draw, and counted in `detail`;
  - to make that deterministic rather than a function of how much data happened
    to load, the **entry-eligibility endpoint is predeclared** as
    `evaluation_ms` minus `max_bars` bars, so every eligible entry can complete
    inside the loaded frame.
- **The endpoint is never chosen from counts.** `evaluation_ms` is a calendar
  instant fixed in the registration. It may not be "when 12 blocks have
  accrued", may not be extended because a reading came up short, and may not be
  brought forward because a reading looks good. If the predeclared endpoint
  arrives with fewer than 12 qualifying blocks, the prediction is untestable:
  `p = 1.0`, charged (§9).
- The evaluated slice is `[first eligible entry bar, predeclared entry
  endpoint]`.

## 8. Reserve before read, durably

**Every look is charged the moment a held-out price is read.** Gate 1 enforces
this with an in-memory `looked=True`; that does not survive a crash. Gate 2
must make the charge durable *before* the read.

**Two digests, not one.** An earlier draft sealed a single `bundle_sha256` over
the registration and the code — that is everything **except the bytes the
answer is computed from**, and it does not pin a result. The corrected design
separates them:

1. **`registration_sha256` — the protocol digest.** Computable **before any
   read**, and what goes into the reservation row:

   ```
   thesis_sha256, identity_sha256, evidence_sha256, candidate_hash,
   prediction_id, canonical_target, metric, relation, baseline,
   required_slices (ordered), tf, geo, symbols (sorted),
   registration_ms, first_entry_ms, entry_endpoint_ms, evaluation_ms,
   block_days, degenerate_max_frac, draws B, seed, floors (12/60/20),
   decision rule,
   risk: {taker_fee_pct, slippage_atr_frac, funding_rate_8h, bar_minutes,
          risk_per_trade_pct, equity}
   code: sha256 of thesis.py, gate2.py, referee.py, portfolio_null.py,
         vector_backtest.py, dsl.py, features.py, features_deriv.py,
         features_xs.py
   ```

   (`max_open` is deliberately absent: `walk_table` does not read it — §3.)

2. **`input_data_sha256` — the sealed input-data digest.** A content manifest
   over the bytes actually read: for every symbol, the `(first_ts, last_ts, bar
   count, sha256)` of the OHLCV slice loaded **including its warmup bars**; the
   same for every `ref` series and every funding or derivative series charged.
   It is populated **only by the authorized loader, and only after the durable
   reservation exists**.

**You cannot know the dataset digest before the first read.** `data/candles.db`,
`data/derivs.db` and the `refs` table are mutable stores that the recorder
threads extend and repair — `RefStore.save` is an `INSERT OR REPLACE` that
rewrites history, and `repair_partial_bars` has rewritten closed bars. The only
way to hash the input in advance is a **trusted precomputed storage
attestation**: an immutable snapshot — a content-addressed export, or a
read-only copy taken under a lock — whose digest was recorded by something
other than the process now reading it. **No such facility exists in this repo
today.** So the honest protocol is: reserve on the protocol digest, have the
loader seal the data digest, then settle with both. Both digests appear in the
settled row, and a result quoted without `input_data_sha256` is not
reproducible and is not interpretable.

**Protocol.**

1. Offline refusals first (§10). No read, no row, no charge.
2. `t, alpha_t = ledger.next_alpha(...)` — **inside the same transaction as
   step 4**, see "Atomicity and ordering" below.
3. **Precision check before the read.** The bootstrap p has floor
   `1/(B+1)`, so the look can only resolve `alpha_t` if
   `ceil(1/alpha_t) - 1 <= B_cap` — the same arithmetic as
   `portfolio_null.draws_for`. If it cannot, the candidate is **DEFERRED**:
   no read, no row, no charge. It is never tested at a level it cannot reach.
   At `research.fdr_target 0.10`, `lord_w0 0.05`, a first look with no prior
   rejections sits at `alpha_t = 2.502e-03`, needing `B >= 399`; a tenth look
   at `1.823e-04` needs `B >= 5486`. `B` is drawn from a configured cap in the
   same spirit as `referee_max_draws`.
4. Reserve the complete predeclared prediction family in one transaction,
   assigning fixed LORD++ levels for H1 and H2 before any price is loaded.
   Each reservation is an INSERT with `p = NULL`, `rejected = 0`, and
   `detail={"state":"reserved", "registration_sha256": …}` committed through
   `Journal._tx()`. H2 must already be reserved when H1 is read; H1's outcome
   may not determine whether H2 is charged, reordered, or omitted.
5. The authorized loader reads, seals `input_data_sha256` into the rows, and
   computes.
6. `ledger.settle_test(seq, p, detail)` settles each reserved row in sequence
   order, filling `p`, `rejected`, and the full detail, including both digests.

**Atomicity and ordering.** Three properties `ledger.py` does not have today,
and which must exist before any gate-2 row is written:

1. **`next_alpha` and the reservation must be one atomic step.** From source:
   `Ledger.next_alpha` reads `research_tests` through `journal.query()`, which
   runs on a thread-local connection with no transaction wrapper, and
   `record_test` then opens its own `_tx()`. Two concurrent looks can read the
   same `t` and be charged the same `alpha_t`. `reserve_test` must compute `t`
   and `alpha_t` **and** INSERT inside a single `Journal._tx()`, so the
   sequence is what the table says and nothing else.
2. **Settlement follows a fixed ledger order.** For one candidate, rows settle
   in `seq` order; a later prediction may not settle before an earlier one. A
   LORD++ level at look `t` depends on which earlier looks rejected, so
   out-of-order settlement retroactively changes the level a look was charged
   at.
3. **A repeated candidate cannot reset the budget, and H1's outcome may not
   choose H2.** Re-submitting the same candidate — same hash, same thesis —
   does not start a fresh sequence; it appends to the existing one at the next
   `t`. And **everything is predeclared before any outcome is read**: both
   predictions with their full text, their required slices, the evaluation
   endpoint, the block length, the draw count, the seed and the floors. H2 may
   not be written, re-scoped, re-ordered or dropped once H1's result is known,
   and there is **no optional stopping** — the sequence does not end early
   because H1 rejected, and does not continue because H1 did not.

**Crash handling.** A reservation that never settles leaves a row with
`p = NULL` and `rejected = 0`. `next_alpha` counts it in `t` (`len(rows) + 1`)
and never in the rejection list, so a crashed look **spends budget and earns
nothing** — the conservative direction, and the same answer as an untestable
look.

**Resume is free only against an identical byte snapshot.** A `reserved` row
may be resumed and settled on the same `seq` only if **both** digests
reproduce: `registration_sha256`, and an `input_data_sha256` recomputed from a
durable snapshot **pinned to that original reservation**. Re-reading the same
bytes is not a new look; re-reading a mutable store is.

**If the crash left no durable snapshot digest** — the loader died before
sealing it, or the snapshot is gone — the row is settled at **`p = 1.0`** and
the look stays spent. There is **no free retry against a mutable database**: a
second read of a store that may have moved underneath is a new look, at a new
`t`. A stale `reserved` row is never rewritten to point at a new bundle. Any
difference at all — one more bar, a different seed, an edited feature file — is
a new look.

**Nothing was reserved, read or charged by this document, because it performs
no ledger operation at all.** No claim is made about what `research_tests`
currently holds; no row of it was read. What is established from source: no
code path can write a `gate2` row, because `trader/research/gate2.py` does not
exist and `ledger.py` has no `reserve_test` or `settle_test`.

## 9. Charging, the decision rule, and what it does NOT guarantee

- Each prediction is **one ordered charged test** in the existing LORD++
  sequence in `research_tests`, in the thesis's own prediction order: `H1`
  then `H2`, gates `gate2:H1`, `gate2:H2`. The sequence is shared with gate 1,
  so a candidate with 2 predictions consumes three looks in total.
- **Per-prediction p is the max over ALL REQUIRED slices — never over the
  eligible ones.** The registration (§8) names each prediction's slice set
  before any read, and every slice it names is **required**. For a prediction
  to pass, **every** required slice must be eligible under the a-priori floors
  of §4c **and** read `delta_hat > 0`. The charged p is

  ```
  p = max over ALL required slices s of p_s,
      where an INELIGIBLE required slice contributes p_s = 1.0
  ```

  so one ineligible required slice sets the prediction's p to 1.0 by
  construction. Taking the max over eligible slices only — as an earlier draft
  did — turns a missing reading into a free pass and lets an unlucky window, or
  a chosen one, remove exactly the slice that would have read badly. That is
  the bug this rule exists to prevent.

  Eligibility is computed and sealed from trade counts **before** any contrast
  or p-value, so a slice can never be dropped for reading badly either. Every
  required slice appears in `detail` with its counts, its eligibility verdict
  and its reason. A required slice is never silently dropped, and "no slice was
  eligible" is `p = 1.0`, not an absence.
- **Insufficient evidence after the read is `p = 1.0`, not a refund.** Fewer
  than 12 qualifying blocks, fewer than 60 trades, fewer than 20 in an arm —
  each records `p = 1.0` with the reason, and the look stays charged. This
  mirrors `gate1`'s "untestable is a p of 1.0, not a pass and not a skip".
- **Pass rule.** With `m` predictions, `ceil(2m/3)` must reject at their own
  `alpha_t`, each with `delta_hat > 0` **on every required slice**. For
  `m = 2`, `ceil(4/3) = 2`: **both H1 and H2 must reject.** (m=3 → 2 of 3;
  m=4 → 3 of 4.) A nonpositive `delta_hat` anywhere in a prediction's required
  set is a fail for that prediction regardless of its p (§3a).

**The error guarantee is not established.** LORD++ controls a per-row FDR over
a sequence of tests under its own assumptions. It does **not** thereby
establish a family-wise or all-admission error guarantee for a conjunction
rule ("k of m must reject") whose constituent tests are computed from the
**same trades** and are therefore strongly dependent — H1 and H2 partition one
population, and both are persistent local trend states on correlated symbols.
No claim of a calibrated family error rate is made here. Closing that gap is
an explicit blocker (§12), not a detail.

## 10. Fail-closed surface

The initial implementation supports exactly:

| field | supported |
|---|---|
| target | `condition` only |
| metric | `mean_r` only |
| baseline | `complement` only |
| relation | `>` and `<` |

Everything else — `null_pctile`, `hit_rate`, any `subset` target (`leg`,
`markets`, `regime`, `era`), baseline `all` — is **`unsupported_target`**:
decided offline from the frozen thesis, **no held-out read, no row, no charge**,
gate 2 returns `not_applicable`, and the candidate does **not** advance. A
generic, unrecognised or newly-added metric fails the same way. Silence is
never a pass.

`null_pctile` in particular is deliberately excluded: a per-symbol rotation
percentile re-imports exactly the cross-symbol dependence gate 1 exists to
deflate.

## 11. Proposed API, state contract, synthetic test matrix, steps

### 11a. API (not written)

```python
# trader/research/gate2.py — DESIGN ONLY
SCHEMA        = "luffy.gate2.v1"
BLOCK_DAYS    = 30          # fixed a priori; 60/90 are diagnostics only
MIN_BLOCKS    = 12
MIN_TRADES    = 60          # == referee.MIN_TRADES
MIN_ARM       = 20
DEGENERATE_MAX_FRAC = 0.20  # untestable above this share of degenerate draws
DIAGNOSTIC_BLOCK_DAYS = (60, 90)

def supported(pred: dict) -> tuple[bool, str]:
    """Offline. False + reason for anything outside §10. No read."""

def mask_with_unknown(tree, ctx) -> tuple[np.ndarray, np.ndarray]:
    """(true_mask, known_mask). Strict: known only where every leaf is
    finite. NEVER dsl.evaluate_bool — its fillna(False) hides unknown."""

def partition(legs, exit_spec, risk, tf, tree) -> dict:
    """{'trades': [Labelled(symbol, entry_ms, exit_ms, r, group)],
        'counts': {'subset','complement','unknown','incomplete'}}
       group in {'subset','complement','unknown'}; P1 only."""

def contrast(trades, relation: str) -> dict:
    """{'delta_hat', 'mu_subset', 'mu_complement', 'n_subset',
        'n_complement'} — the signed contrast of §3a."""

def blocks(trades, first_ms: int, block_days: int) -> list:
    """Consecutive NON-OVERLAPPING calendar blocks, drawn later with
       replacement (a cluster bootstrap, not a moving-block one).
       A trade belongs wholly to its ENTRY block."""

def block_bootstrap(trades, delta_hat, first_ms, block_days, draws, seed):
    """{'p_hat', 'draws', 'degenerate_draws', 'blocks', 'blocks_both_arms',
        'null_median'} — the null-centred one-sided APPROXIMATE p of §4b.
       Denominator is always draws+1; a degenerate draw counts toward the
       numerator and is never dropped. Above DEGENERATE_MAX_FRAC, or with a
       degenerate delta_hat, returns p_hat = 1.0 with a reason."""

def eligible(counts, blocks_both_arms) -> tuple[bool, str]:
    """A-priori floors of §4c. Never reads a contrast or a p."""

def evaluate_prediction(pred, required_slices, cfg, seed) -> dict:
    """Per-slice eligibility sealed FIRST, then contrasts, then
       p = max over ALL REQUIRED slices, an ineligible required slice
       contributing p_s = 1.0. Never a max over the eligible subset.
       {'p','delta_hat','slices',...} with one entry per required slice."""

def gate2(thesis_report, payload, ledger, cfg) -> dict:
    """Reserve → read → settle per prediction, in thesis order.
       {'passed', 'per_prediction', 'reason'}"""
```

### 11b. State contract

| surface | change |
|---|---|
| `research_tests.gate` | new values `gate2:H<n>` alongside `gate1`. Column comment updated. |
| `research_tests.p` | may be `NULL` while a look is `reserved`. `next_alpha` already counts such a row in `t` and never in the rejection list — no change needed, but a test must pin that behaviour. |
| `Ledger.reserve_test(...) -> int` | NEW. Computes `t`/`alpha_t` **and** INSERTs `p=NULL`, `rejected=0`, `detail={"state":"reserved","registration_sha256":…}` inside ONE `Journal._tx()`. Returns `seq`. |
| `Ledger.seal_inputs(seq, input_data_sha256)` | NEW. The authorized loader's only write: stamps the data digest onto a `reserved` row. |
| `Ledger.settle_test(seq, p, detail)` | NEW. UPDATE that row only, in `seq` order. Refuses a `seq` that is not `reserved`, and refuses to settle a pass on a row carrying no `input_data_sha256`. |
| `research_candidates.state` | `referee_passed` → `gate2_fail` \| `reason_passed`; `deferred` reused when the precision check refuses the look. The column is free TEXT, so no migration — only the schema comment and every reader. |
| `reason_passed` | written **only** when gate 1 rejected, gate 3 passed, AND gate 2 passed. Three gates, one state. |
| `kernel._research_handoff` | unchanged; it reads `reason_passed` and nothing else. `research.handoff` stays `false`. |

### 11c. Synthetic test matrix

Every row is a generated fixture with a known answer. Rows 1–3 are the
calibration; the rest are correctness.

| # | fixture | must hold |
|---|---|---|
| 1 | no conditional information, independent symbols, i.i.d. returns | one-sided binomial UPPER bound on the false-pass rate ≤ the **actual** `alpha_t` in use; rep count chosen from that bound (§12), not a fixed 2000 |
| 2 | no conditional information, **one shared factor** driving all symbols | bounded false-pass rate reported; **approval blocker** if the bound exceeds nominal |
| 3 | no conditional information, **long-memory** returns (persistent AR / fractional) | false-pass rate at 30-day blocks reported; block-length adequacy measured, not assumed |
| 3b | no conditional information, **regime-switching** generator whose state lasts several blocks | bounded false-pass rate reported; this is the shape the 12-block floor is least able to survive |
| 3c | no conditional information, **heavy-tailed** R (right-skewed, fat tail) with 20-trade arms | bounded false-pass rate reported; bootstrap coverage of a mean contrast degrades exactly here |
| 3d | `unknown` group concentrated non-randomly (warmup, stale `ref`, thin liquidity) | not missing at random; bounded false-pass rate and the induced arm imbalance reported |
| 3e | the gate-1 **selection** applied to the generator, so only candidates that rejected at gate 1 reach gate 2 | bounded false-pass rate under the conditioned population, not an unconditioned draw |
| 4 | planted conditional effect of known size | power curve vs effect size, block count and trades per arm; publish the **minimum detectable effect** at the charged `alpha_t` |
| 4b | H1 and H2 both evaluated on the **same** trade population | the joint false-pass rate of the `k of m` conjunction, which per-row LORD++ does not establish (blocker 2) |
| 5 | condition undefined on 30% of entry bars | `|subset|+|complement|+|unknown|` == untouched trade count; no unknown in either arm or any block |
| 6 | trades whose exit crosses a block edge | assigned by entry only; deterministic; identical rule in both arms and every draw |
| 6b | holding period swept from a small fraction of the block to beyond it, no conditional information | false-pass rate reported at each ratio: the **cost of entry-block assignment**, measured rather than assumed (§4b) |
| 6c | resample draws that leave an arm empty or zero-variance | denominator stays `draws+1`; degenerate draws counted toward the numerator, never dropped; above `DEGENERATE_MAX_FRAC` the prediction is `p = 1.0`, charged |
| 6d | a trade force-closed at the frame edge (`exit_i == n-1`, `i + max_bars > n-1`) | counted `incomplete`; in no arm, no block and no draw; never read as a time exit |
| 7 | one arm holds 19 trades | `p = 1.0`, charged, not a pass |
| 8 | only 11 blocks carry both arms | `p = 1.0`, charged, not a pass |
| 9 | `alpha_t` finer than `1/(B+1)` | DEFERRED: no read, no row, no charge |
| 10 | crash between reserve and settle | `reserved` row persists; `t` advances; no rejection; identical bundle resumes on the same `seq`; **changed** bundle charges a new look |
| 11 | `hit_rate` / `subset` / `baseline: all` / unknown metric | `unsupported_target`, no read, no row, no state change |
| 12 | future bars appended to the frame tail | every mask value at every `entry_i` unchanged |
| 13 | partition vs untouched run | R multiset and trade list reproduce `walk_table` exactly (P1, not P2) |
| 14 | 60/90-day diagnostics disagree with 30-day | charged p is the 30-day p; diagnostics recorded only |
| 15 | slice A **required** and ineligible by the a-priori floors, slice B required and eligible with a tiny p | the prediction's charged `p == 1.0` — the max is over ALL required slices, with A contributing 1.0. A is recorded with its counts and reason, never dropped from the max, and eligibility was sealed before any contrast |
| 16 | `delta_hat <= 0` with a small `p_hat` | FAIL on the sign check alone; the two fields are recorded separately and no `p >= 0.5` behaviour is assumed |
| 17 | a prospective evaluation after A/B were audited `unavailable` | a NEW registration with its own required-slice set and its own `t`; the A/B registration is never relabelled or settled with prospective data |

### 11d. Implementation steps (blocked at step 0)

0. **Close §12.** Nothing below may be written first.
1. `mask_with_unknown` + tests 5, 12 — pure, no held-out path.
2. `partition` + test 13 against the untouched `walk_table`.
3. `contrast`, `blocks`, `block_bootstrap`, `eligible` + tests 6, 7, 8, 14, 15.
4. **Calibration harness**, rows 1–4. Publish the measured false-pass rates
   before any gate wiring. If row 2 or row 3 inflates, the design changes —
   block length, statistic, or the gate is not built.
5. `Ledger.reserve_test` / `settle_test` + tests 9, 10.
6. `supported` + test 11, wired so an unsupported thesis never reaches a read.
7. `gate2()` inside the referee child; the import-surface test of Wall 1.
8. Wire `reason_passed` behind gate 1 AND gate 3 AND gate 2, with
   `research.handoff` still `false`.

## 12. Blockers — no gate pass is available today

1. **Calibration.** An offline synthetic harness may be implemented and run
   before any gate wiring or held-out read. Rows 1–3 and 3b–3e, 6b and 6c
   must publish one-sided binomial upper bounds at the actual charged alpha
   levels, with replication counts chosen to make those bounds informative,
   plus power/MDE results for row 4. Until these are accepted, the 12/60/20
   floors and the 30-day block are design choices with no established validity
   under shared regime or long memory, and gate inference remains experimental.
2. **Family error rate.** The "k of m must reject" conjunction over dependent
   predictions has no established error guarantee under per-row LORD++. Its
   calibration must include the full shared LORD++ stream, gate-1 selection,
   fixed pre-read family reservations, and H1/H2 dependence. It needs an
   explicit human approval before `reason_passed` may be written.
3. **Evidence access.** A and B are `unknown` ⇒ unavailable (§6). The only
   admissible slice is prospective (§7), which needs ≥ 360 days of forward
   4h data after a 230-bar warmup that has not started.
4. **Referee stop.** `research.referee: false` and `research.handoff: false`.
   The gate decision of 2026-09-15 left the referee off.
5. **No reservation, no writes.** Nothing is reserved, charged or written by
   this document.

Gate 2 **plus** the existing gate 1 **and** gate 3 are all required before any
`reason_passed` is written. None of the three is satisfied for
`55243573515adc3b`.
