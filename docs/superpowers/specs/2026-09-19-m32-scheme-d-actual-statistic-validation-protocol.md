# M3.2 Scheme D — actual-statistic synthetic validation protocol

> **STATUS: DRAFT PRESPECIFICATION ONLY — NOT AUTHORIZED TO RUN.**
>
> No worlds have been generated, no permutation draws made, and no outcomes
> observed under this protocol. This document does not freeze Scheme D, and it
> does not authorize historical search, Gate 2, a held-out look, or any
> demo/live use. Running it requires (a) the pre-run freeze in §14 committed
> and hash-manifested and (b) explicit owner authorization recorded after that
> freeze.

- Date drafted: 2026-09-19
- Baseline repository commit: `ab9e39683f5e9f756fc7ef6690133ecb5b608f64`
- Sources read for this draft:
  - `CLAUDE.md`;
  - the two 2026-09-19 Scheme D reports;
  - the current M3.2 code (`trader/cognition/m32_search.py`,
    `m32_protocol.py`, `m32_correction.py`).
- **The numeric design below is fixed.** The generated config must encode it
  exactly. Any difference between the config and this document is an
  integrity stop (§13).

---

## 1. Purpose and scope

Earlier Scheme D evidence used a single surrogate statistic. This protocol asks
one question: **Does Scheme D block permutation, used with the actual M3.2
statistic across the full fixed 384-hypothesis family and followed by
Benjamini–Hochberg at q = 0.05, control the global-null error rate and the FDR,
give calibrated marginal p-values, and have adequate power on synthetic worlds
where the ground truth is known by construction?**

In scope:

- Synthetic worlds only, generated from a frozen config.
- The actual M3.2 metric code (§3), the fixed 384-hypothesis family (§4),
  unchanged Scheme D conditioning (§5), and BH across all 384 hypotheses.

Out of scope, and not affected by any result here:

- Historical or real-data evaluation of any kind, including production
  `data/*.db`, the network and the exchange.
- Gate 2, the referee, admission, and live or demo trading.
  `research.referee=false` and `research.handoff=false` stay unchanged.
- Changing, tuning or choosing between Scheme D variants.
- Pooling with, or reinterpreting, the earlier surrogate-statistic Scheme D
  evidence. That evidence stays separate (§13).

## 2. Immutable design summary

| Item | Fixed value |
|---|---|
| Statistic | current `trader/cognition/m32_search.py::_metric` (§3) |
| Family | exactly 384 fixed hypotheses (§4) |
| Multiplicity | BH step-up, q = 0.05, m = 384 always |
| Conditioning | Scheme D unchanged (§5) |
| Correlation structures | C0–C4 (§6) |
| Null DGPs | N0–N3 × C0–C4 = 20 null cells × 2000 worlds |
| Power scenarios | P1–P7 × C0–C4 = 35 power cells × 1000 worlds, base DGP N1 |
| Permutation draws | B = 767,999 per tested world, sampled uniformly **with replacement**, shared by all 384 hypotheses |
| Master seed | 2026091902 |
| RNG | `numpy.random.PCG64` via `SeedSequence` (§8) |
| Environment | Python 3.12.3, NumPy 2.5.2, `OPENBLAS_NUM_THREADS=1` |

Frozen implementation hashes (SHA-256), verified before any RNG:

| File | SHA-256 |
|---|---|
| `trader/cognition/m32_search.py` | `1081e3482c0d709b81b4b93db2fa94437ebead2e4bc92c78b9b359f7030a827b` |
| `trader/cognition/m32_protocol.py` | `ade9d184c8e84a2e0be2219c106e6b77403089637dabd19f33bd34995be1e3bd` |
| `trader/cognition/m32_correction.py` | `67a617be122c05616aa653cacc501f8e1658e493e659e9530e25c48153f10db7` |

## 3. Actual statistic

The statistic is the one computed by current
`trader/cognition/m32_search.py::_metric`. The implementation file is the
authority; this summary does not replace it.

- **Persistence hypotheses:**
  `signed = (median(matched) − median(all)) / (MAD + 1)`. Test statistic is
  `T = |signed|`.
- **Categorical hypotheses (`case_kind`, transition categorical,
  continuation):** `signed` is the macro-F1 lift over the
  unconditional-majority baseline. Test statistic is `T = |signed|`.

The runner must either import this function, or use a byte-identical vendored
copy of the functions it needs. In both cases it must prove equivalence on
deterministic fixtures (§14). No reimplementation, approximation or
vectorized rewrite may be used unless that equivalence proof covers it
exactly: identical float64 outputs on all fixtures, including tie and
degenerate cases.

Each hypothesis kind reads the outcome column defined by the current code.
The **population signed metric** of a hypothesis is the value of that same
functional evaluated on the generator's population distribution. It is used
only for the analytic truth table (§11.2).

## 4. Hypothesis family (m = 384)

Hypothesis definitions and membership rules are fixed before any outcome
exists. The rules are committed in the hypothesis membership table. Per-world
membership realizations come from the membership stream (§8) and never use
outcomes.

| Index range | Kind | Target / metric | Membership threshold `τ` | Count |
|---|---|---|---|---|
| 0–127 | state | `case_kind` (macro-F1 lift) | 0.6744897501960817 (upper 25%) | 128 |
| 128–255 | state | persistence (median shift / (MAD+1)) | 0.6744897501960817 (upper 25%) | 128 |
| 256–319 | transition | categorical (macro-F1 lift) | 0.8416212335729143 (upper 20%) | 64 |
| 320–383 | sequence | continuation (macro-F1 lift) | 0.8416212335729143 (upper 20%) | 64 |
| | | | **Total** | **384** |

Cluster and sector labels are fixed as follows:

- Cluster `c(h) = h mod 32`. That gives 32 clusters of 12. Each cluster
  contains:
  - 4 `case_kind` hypotheses: `c, c+32, c+64, c+96`;
  - 4 persistence hypotheses: `128+c, 160+c, 192+c, 224+c`;
  - 2 transition hypotheses: `256+c, 288+c`;
  - 2 sequence hypotheses: `320+c, 352+c`.
- Sector `s(h) = A` if `c(h) < 16`, else `B`. That gives two sectors of 192
  hypotheses, balanced by kind.

BH is applied across all 384 hypotheses in every tested world:

- Sort the tested p-values ascending. Reject the hypotheses with the `k*`
  smallest values, where `k*` is the largest `k` such that
  `p_(k) ≤ k · 0.05 / 384`.
- **Refused hypotheses stay in the denominator** (m = 384 always). They are
  ranked after all tested hypotheses, so they can never be rejected. Their
  p-value field is recorded as explicit `null`, never as `p = 1`, and is
  never treated as evidence.
- Refusals are counted and reported separately (§9).

## 5. Scheme D conditioning (unchanged)

- **Strata `D`:** the recurring calendar quarter × regime × the block's
  realized missing-fraction class. Class edges are
  `[0, .2), [.2, .4), [.4, .6), [.6, 1]`.
- **Unit:** whole blocks.
- **One synchronized permutation per draw.** Each draw is a single
  permutation of the whole-block **outcome + mask tensor** within `D` strata,
  and it is applied identically to all 384 hypotheses.
- **What stays in place:** features and memberships stay at their block
  slots.
- **Forbidden:** row-level transforms, symbol-level transforms, per-hypothesis
  permutations, and any independent re-draw of masks.
- Because a mask moves with its outcome and permutation happens only within
  its class, every block keeps its realized missing-fraction class.

## 6. Correlation structures

### 6.1 Membership latents

- There is one latent vector `Z(r) ∈ R³⁸⁴` for each unique row
  `r = (block, symbol, key)`.
- `Z(r)` is drawn **independently for every unique row and every block**.
  All factors and idiosyncratic terms are fresh for each unique row.
- There is **no additional factor shared across rows**, whether within a
  block, across symbols, or across blocks.
- The linked copy of key 4 (§7.1) has no latent of its own.
- Membership rule: `M_h(r) = 1{Z_h(r) + s(r) > τ_h}`, where `s(r)` is the
  nuisance membership shift of §7. It is zero except in N2 and N3.
- Under every null DGP, outcomes are independent of `Z` given `D`.
- All structures are factor-constructed and therefore PSD.
- Statistic correlation is induced through overlapping memberships and shared
  outcome columns. It is reported descriptively per cell and is never a gate.

### 6.2 Structures

All `e_h`, `f`, `g` and `f_c` below are iid N(0,1).

| Code | Name | Construction | Implied latent correlation |
|---|---|---|---|
| 0 | C0 independent | `Z_h = e_h` | 0 |
| 1 | C1 equicorrelated | `Z_h = √.30·f + √.70·e_h` | ρ = .30 |
| 2 | C2 equicorrelated | `Z_h = √.70·f + √.30·e_h` | ρ = .70 |
| 3 | C3 clustered | `Z_h = √.10·g + √.60·f_{c(h)} + √.30·e_h` (32 clusters × 12) | within .70, between .10 |
| 4 | C4 signed sectors | `Z_h = √.60·(l_h·f) + √.40·e_h`, `f ∈ R²` | within .60, cross −.30 |

C4 is fixed at λ = 0.60 and θ = 60°, with unit-norm loadings:

- sector A: `l = (cos 60°, sin 60°) = (0.5, 0.8660254037844386)`;
- sector B: `l = (0.5, −0.8660254037844386)`.

This gives within-sector correlation 0.60 and cross-sector correlation
`0.60·cos 120° = −0.30`.

C4 is a deliberate stress case outside BH's PRDS comfort zone. C4 failures
are **blocking**. They must not be relabelled as exploratory.

## 7. Data-generating processes

### 7.1 World geometry (all DGPs)

- 48 blocks, indexed `b = 0 … 47`. Each block spans 28 days.
- 16 symbols.
- 5 unique observation keys per symbol per block, `k = 0 … 4`. That gives
  80 unique rows per block and 3,840 per world.
- Each (block, symbol) also carries one **linked copy of key 4**:
  - it moves with its block;
  - it is excluded from every statistic, floor and support count;
  - it exists to exercise the link and footprint checks;
  - it never crosses a block boundary.
- Quarter `= 1 + (b mod 4)`.
- Regime `= (b // 3) mod 2`.

These rules give 8 quarter × regime strata with fixed sizes:

| Stratum (quarter, regime) | Size |
|---|---|
| (1,0) | 8 |
| (1,1) | 4 |
| (2,0) | 4 |
| (2,1) | 8 |
| (3,0) | 8 |
| (3,1) | 4 |
| (4,0) | 4 |
| (4,1) | 8 |

The missing-fraction class may split these strata further, per world.

In N0, every block is in class `[0,.2)`, so
`|G| = (8!)⁴·(4!)⁴ ≈ 8.77×10²³`. This value is deterministic and does not
depend on outcomes.

### 7.2 Outcome and nuisance specification

Persistence noise:

- **"Gaussian":** iid N(0,1) per unique row.
- **"t₃":** unit-variance Student-t₃ per unique row, i.e. `t₃/√3`.
- **"Common jump":** each block independently, with probability 0.10, adds
  `2 × (unit-variance t₃)` to every row in that block. Jumps are independent
  between blocks.
- **null MAD:** the MAD of the DGP's per-row null persistence distribution
  (base noise plus jump mixture, before nuisance shifts). For
  Gaussian-noise DGPs it is 0.6744897501960817. For t₃-noise DGPs it is
  derived by deterministic root-finding from the frozen distribution. The
  config records it, and the check suite verifies it. It is not estimated
  from realized worlds.

Categorical outcomes (the 3-class labels read by `case_kind`, transition and continuation hypotheses) are iid per row, with class probabilities given below.

| Code | Name | Persistence | Categorical probs | Missingness | Nuisance / confounding |
|---|---|---|---|---|---|
| 0 | N0 balanced Gaussian | Gaussian | `[1/3, 1/3, 1/3]` | none (complete masks) | none |
| 1 | N1 imbalanced / heavy-tailed | t₃ + common jump | `[.65, .25, .10]` | none | none |
| 2 | N2 exogenous coverage | Gaussian | `[1/3, 1/3, 1/3]` | see below | see below |
| 3 | N3 nuisance + censoring | t₃ + common jump | `[.65, .25, .10]` | publication lag, see below | see below |

**N2 missingness:**

- Each block draws a latent coverage class from four equally likely classes.
  The class missing rates are `[.08, .30, .50, .72]`.
- Each unique row is then masked independently, as Bernoulli(rate).
- Both draws come from the data stream, before and independent of outcomes.

**N2 confounding:** the confounders are functions of the block's **realized**
missing-fraction class `j ∈ {0,1,2,3}`, which is the class used in `D`.
Confounding is therefore fully conditioned by `D`.

| Effect | Class 0 | Class 1 | Class 2 | Class 3 |
|---|---|---|---|---|
| Membership shift `s(r)` | 0 | 0.4 | 0.8 | 1.2 |
| Persistence outcome shift (null-MAD units) | 0 | 0.5 | 1.0 | 1.5 |

**N3 membership and outcome nuisance:**

| Effect | Q1 | Q2 | Q3 | Q4 | Regime 1 |
|---|---|---|---|---|---|
| Membership shift `s(r)` | −0.8 | −0.2 | 0.4 | 0.9 | +1.2 |
| Persistence outcome shift (null-MAD units) | +0.9 | −0.5 | +0.3 | −1.0 | +1.5 |

- The regime-1 shifts are added to the quarter shifts. Regime 0 adds nothing.
- These are mean shifts only.

**N3 censoring:**

- For each (block, symbol), draw a lag `L = min(G − 1, 5)`, where
  `G ~ Geometric(0.5)` on `{1, 2, …}`.
- Keys `5 − L … 4` of that symbol in that block are masked.
- Censoring never crosses a block boundary.
- Each block's realized missing-fraction class defines `D`.

**Outcome independence and validity:**

- Within a block, outcomes depend only on:
  - the block's realized class (N2);
  - the block's quarter and regime (N3);
  - the common jump (N1, N3);
  - iid noise.
- Outcomes are independent between blocks.
- Every null DGP is therefore exchangeable within `D`. Each world carries a
  generator-issued exchangeability certificate, and the runner checks it
  (§9).
- Each of the 20 null cells has 2000 fixed worlds.

### 7.3 Invalid and refusal cases

Invalid and refusal cases are **deterministic pre-run unit checks only**:

- They generate no Monte Carlo outcomes, use no RNG and make no draws.
- They cannot be forced through by any flag.

Each invalid synthetic-metadata case must be refused 100% of the time, before
any RNG is constructed. The minimum case set is one case per world-level
refusal reason in §9. Examples:

- between-block dependence;
- a linked copy of key 4 placed in a different block (a footprint or link
  crossing);
- unconditioned drift in mean or variance;
- missingness that depends on outcomes;
- an omitted quarter, regime or coverage confounder;
- a clock or link violation;
- an absent or invalid certificate;
- a hash or version mismatch.

## 8. Seed map

Every generator is built as
`numpy.random.Generator(numpy.random.PCG64(numpy.random.SeedSequence([master, phase_code, correlation_code, dgp_code, world_index, stream_code])))`
with `master = 2026091902`.

| Field | Codes |
|---|---|
| `phase_code` | null = 10, power = 20, checks = 30 |
| `correlation_code` | C0 = 0, C1 = 1, C2 = 2, C3 = 3, C4 = 4 |
| `dgp_code` (phase 10) | N0 = 0, N1 = 1, N2 = 2, N3 = 3 |
| `dgp_code` (phase 20: scenario code) | P1 = 101, P2 = 102, P3 = 103, P4 = 104, P5 = 105, P6 = 106, P7 = 107 |
| `dgp_code` (phase 30) | scenario code of the cell being checked |
| `world_index` | 0 … 1999 (null), 0 … 999 (power), 0 (checks) |
| `stream_code` | data = 0, membership = 1, permutation = 2, injection = 3; bootstrap = 4 (phase 30 only) |

Rules:

- **No seed reuse between cells.** The runner keeps a registry of every tuple
  it constructs, and a duplicate tuple is an integrity stop.
- **Common random numbers** are used only to pair a null world with its
  effect world inside the same power cell. Power world `(20, c, P, w)`
  generates its pre-injection N1 family from streams 0 and 1, then applies
  the injection from stream 3. That pre-injection family is not separately
  tested. No other pairing exists across cells, scenarios or correlations.
  This pairing is declared here, before any outcome exists.
- **Fixed consumption order within a world:**
  1. Data stream: latent coverage classes, masks and lags. The realized
     classes are computed from these.
  2. Membership stream: `Z`.
  3. Data stream, continued: common jumps, then outcomes.
  4. Injection stream (power only).
  5. Compute the observed statistics.
  6. Permutation stream.
- **Benchmark isolation.** Benchmark or smoke runs use phase code 90 only.
  The runner refuses tuples with phase 10, 20 or 30 outside an authorized
  validation run.

## 9. Support and refusal rules (all evaluated before draws)

### 9.1 World-level refusals

When any of the following fails, the whole world is refused:

- any frozen mismatch in source, config, version, environment or hash;
- a missing or invalid exchangeability certificate;
- between-block dependence, or a block footprint or link that crosses a block
  boundary;
- unconditioned drift in mean or variance;
- missingness that depends on outcomes;
- an omitted quarter, regime or coverage confounder;
- a clock or link violation;
- a family size that is not exactly 384;
- fewer than 24 movable blocks, or fewer than 3 movable strata;
- a conservative permutation-orbit support `|G| < 1,536,000`, or a
  two-sided floor `2/|G| > 1/768,000`.

`|G|` is defined as the product over movable strata of
`n_s! / ∏_j m_{s,j}!`, where `m_{s,j}` counts blocks whose outcome + mask
tensors are byte-identical. The orbit floor is a **deliberately strong
support requirement**: it ensures the exact two-sided floor is no coarser
than the Monte Carlo floor. It is not required by the sampling scheme,
which samples with replacement (§10).

Integrity failures (hash, environment or config mismatch, seed mismatch) also
stop the run (§13).

### 9.2 Hypothesis-level refusals

A refused hypothesis stays in the m = 384 denominator as a non-rejection. Its
p-value is recorded as `null`. The reasons are:

- a missing or nonfinite target;
- any block below 8 observed unique labels (the linked copy of key 4 is not
  counted);
- below the existing floors:
  - state hypotheses: matched ≥ 12, observed ≥ 8, episodes ≥ 3;
  - transition and sequence hypotheses: matched ≥ 8, episodes ≥ 3;
- fewer than 3 dependence groups;
- a zero or degenerate centered statistic.

The last two reasons below are operationalizations of existing refusals:

- The floors must hold both on the observed arrangement and on a conservative
  worst-case bound over permutations within strata. The bound is the sum over
  matched slots of the minimum observed count among blocks in that slot's
  stratum. This guarantees the metric is defined for every draw.
- The centered statistic is degenerate if it is constant across the group,
  as established by a deterministic check.

### 9.3 Refusal accounting

- Refusals are reported per cell by reason code: world-level and
  hypothesis-level counts, plus the rate among injected hypotheses.
- **Blocking rule:** a valid cell blocks the protocol if its world-refusal
  rate exceeds 1%, or its hypothesis-refusal rate exceeds 1%. The
  hypothesis-refusal rate is refused (world, hypothesis) pairs divided by
  384 × non-refused worlds.
- A refusal never counts as a Type-I success, and it never earns FDP = 0
  credit (§11.2).
- Every invalid synthetic-metadata case must refuse 100%, before any RNG or
  draws.
- Separately, an injected cell whose analytic truth table cannot classify all
  384 hypotheses is refused **before any RNG** (§11.2).

## 10. Draws, p-values, and B rationale

### 10.1 Draws (original Scheme D sampling, preserved)

- Per tested world, draw B = 767,999 permutations **uniformly with
  replacement** from the full within-strata group. The identity is included
  as a possible draw.
- Each draw is one uniform permutation per movable stratum, generated from
  the permutation stream in ascending (quarter, regime, class) stratum order.
- The same mapping is applied to all 384 hypotheses.
- Exactly B successful draws are required. There is no replacement or retry
  of any draw. Any failed draw stops the run for integrity reasons (§13).

### 10.2 P-values

- `p_h = (1 + X_h) / (B + 1)`, where `X_h` counts draws with
  `T*_h ≥ T_h − 1e-12·max(1, |T_h|)`.
- Ties are included, and the tolerance is conservative.
- The same rule applies to every hypothesis.

### 10.3 Rationale for B

- The smallest possible BH threshold is `0.05/384 = 1/7,680`.
- The MC floor `1/(B+1) = 1/768,000` is 100× finer than that threshold.
- The relative MC standard error at `p = 1/7,680` is
  `√((1−p)/(p·B)) = √(7,679/767,999) ≈ 0.09999 ≤ 10%`.

## 11. Injections, analytic truth, and definitions

### 11.1 Injections

Injections are applied **after** the N1 null family is generated. They must
not change memberships, masks, strata or support.

**Categorical OR (target class = label index 2 in the frozen encoding):**

- For the rows matched by the injected hypothesis, the target-class
  probability becomes `p₂' = OR·p₂ / (1 − p₂ + OR·p₂)`.
- Implementation: each matched row that is not in class 2 switches to class 2
  with probability `(p₂' − p₂)/(1 − p₂)`, using an injection-stream uniform.
- Where injected hypotheses overlap, they are applied sequentially in
  ascending `h` order.

**Persistence shift:**

- Add `k × (N1 null MAD)` deterministically to matched rows. No RNG is used.
- Overlapping shifts add together.

Injected sets are fixed by these index rules:

| Code | Scenario | Injected target set | Size | Role |
|---|---|---|---|---|
| 101 | P1 | {0} | OR = 3 | acceptance |
| 102 | P2 | {128} | +1.0 null MAD | acceptance |
| 103 | P3 | {320} | OR = 3 | acceptance |
| 104 | P4 | {0, 33, 130, 163, 260, 293, 326, 359} (clusters 0–7, 2 per kind) | OR = 2 / +0.5 MAD | acceptance |
| 105 | P5 | for each `c ∈ {0,1,2,3}`: {c, c+32, 128+c, 160+c, 256+c, 288+c, 320+c, 352+c} (32 total, 8 per kind) | OR = 2 / +0.5 MAD | acceptance |
| 106 | P6 | {c : c=0…7} ∪ {128+c : c=8…15} ∪ {256+c : c=16…23} ∪ {320+c : c=24…31} (one per cluster, 8 per kind) | OR = 2 / +0.5 MAD | acceptance |
| 107 | P7 | {c : c=0…23} ∪ {128+c : c=8…31} ∪ {256+c : c=16…31} ∪ {288+c : c=0…7} ∪ {320+c : c=0…15, 24…31} (96 total, 3 per cluster, 24 per kind) | OR = 1.5 / +0.25 MAD | sensitivity only, not acceptance |

Each power cell (7 scenarios × 5 correlations = 35 cells) has 1000 fixed
worlds on base DGP N1.

### 11.2 Analytic truth table (required before outcomes)

Overlapping memberships can give **non-injected** hypotheses a nonzero
population estimand. For example, a correlated membership latent or a shared
outcome column can shift the "all" median. Ground truth is therefore not the
injected set alone.

- **Symbolic propagation.** Before any RNG, the harness symbolically
  propagates each injected cell (P1–P7 × C0–C4) through the frozen generator.
  The result is an immutable analytic truth table covering all 384
  hypotheses. For each hypothesis it records:
  - **true null:** the population signed metric (§3) is *proved exactly
    zero*, for example by an independence or symmetry argument. No numerical
    tolerance is allowed;
  - **analytic non-null:** the population signed metric is proved nonzero,
    and its sign is proved.
- **Null cells.** For the 20 null cells, the table is also emitted. Every
  hypothesis in them is a true null by construction.
- **Unclassifiable cells.** If any hypothesis in a cell cannot be proved into
  one of the two classes, with a proven sign for non-nulls, that cell
  **refuses before RNG**. An acceptance cell that refuses means the protocol
  cannot be run as frozen. A new dated protocol is then required.
  No RNG has been spent at that point.
- **Freeze.** The truth tables are committed and hash-manifested in §14. They
  must not be edited after any RNG use.

### 11.3 Definitions

- **Injected target set:** the §11.1 set. It alone drives power.
- **True discovery (power):** a BH rejection of an injected target whose
  observed `signed` has the truth-table sign.
- **False discovery (FDR numerator `V`):**
  - a BH rejection of a truth-table **true null**;
  - a rejection of an analytic non-null with the wrong sign.
  A wrong-sign rejection of an injected target is also a missed detection for
  power.
- **Attribution error:** a correct-sign rejection of an analytic non-null
  that is **not** an injected target (spillover).
  - It is not a false discovery, and it does not count toward power.
  - It is reported per cell as a separate count and rate, and disclosed in
    the owner review.
- **R:** all BH rejections.
- **World FDP:** `V / max(R, 1)`.
- **Refused injected target:** a missed detection.
- **Refused world:** in power cells, a failure (all targets missed; the world
  stays in the denominator). In Type-I and FDR computations the world is
  excluded, and it never contributes FDP = 0 or a non-rejection. The 1%
  blocker (§9.3) applies in every case.

## 12. Acceptance criteria (per cell; no pooling across cells)

All Wilson intervals are two-sided 95% with `z = 1.959964`.

### 12.1 Type-I / global null

Applies to each of the 20 null cells, with `n` = non-refused worlds. Both of
the following must hold for the rate of any BH rejection:

- point estimate ≤ 0.05;
- Wilson upper bound ≤ 0.06.

### 12.2 FDR

Applies to each mixed-null injected cell: P1–P6 × C0–C4, which is 30
acceptance cells. `V` is defined by §11.3. Both of the following must hold:

- mean world FDP ≤ 0.05;
- world-bootstrap 95% upper bound ≤ 0.06.

The bootstrap uses 10,000 resamples of non-refused worlds with replacement.
The upper bound is the 9,750th order statistic of the resampled means. The
seed is `[2026091902, 30, correlation_code, scenario_code, 0, 4]`.

P7 FDR is reported only descriptively. Any P7 exceedance must be disclosed in
the owner review.

### 12.3 Marginal p calibration (secondary guard; any failure blocks)

- In each null cell, world `w` contributes the p-value of the predeclared
  hypothesis `h(w) = w mod 384`. This gives 2,000 independent p-values per
  cell.
- At `t ∈ {0.01, 0.05, 0.10}`, compute the empirical CDF `F̂(t)` over
  non-refused worlds.
- Check it against a two-sided exact binomial (Clopper–Pearson) band with a
  simultaneous 99% level, Bonferroni-corrected over 3 × 20 = 60 comparisons.
  That is `α = 0.01/60` per comparison, split equally between the two tails.
- **Criterion:** the band must contain `t`.
- A pooled all-hypothesis ECDF is reported descriptively only.

### 12.4 Power

Applies to each correlation cell of P1–P6 (P7 is descriptive only):

| Scenario | Criterion |
|---|---|
| P1, P2, P3 | Wilson lower bound on P(the injected target is a true discovery) ≥ 0.80 |
| P4 | Wilson lower on P(≥ 1 true discovery) ≥ 0.90 **and** Wilson lower on P(≥ 4/8 true discoveries) ≥ 0.75 |
| P5, P6 | Wilson lower on P(≥ 1 true discovery) ≥ 0.95 **and** Wilson lower on P(≥ 16/32 true discoveries) ≥ 0.75 |

No acceptance-power scenario may meet its power criteria while exceeding the
FDR criteria in §12.2.

### 12.5 Overall

The protocol **passes** only if all of the following hold:

- all 20 null cells pass §12.1 and §12.3;
- all 30 FDR cells pass §12.2;
- all 30 acceptance-power cells pass §12.4;
- no refusal blocker (§9.3) fires;
- no pre-RNG truth-table refusal (§11.2) occurs;
- no integrity stop (§13) occurs.

C4 cells carry the same weight as every other cell.

**Descriptive outputs, never gates:**

- the induced statistic-correlation summary for each cell;
- refusal breakdowns;
- attribution-error counts;
- P7 results;
- runtime.

## 13. Stopping, integrity, and separation of evidence

### 13.1 Fixed sampling

- Sample sizes are fixed.
- Interim visibility is limited to counts and runtime.
- There is no early success, no futility stopping and no tuning.
- No scenario, cell, DGP or correlation may be added or removed after any
  outcome.

### 13.2 Complete-all rule

- Every cell is completed even if a statistical criterion has already failed,
  so that stopping is never optional.
- The only exception is an integrity stop.

### 13.3 Integrity stops

Any of the following stops the entire run:

- a hash, environment or config mismatch, including any difference between
  the config and this document's fixed numeric design;
- an output-path collision;
- an RNG or seed mismatch, or a duplicate seed tuple;
- a failed draw;
- a nonfinite metric value computed during draws;
- any invariant or check failure.

A nonfinite **input** target is different: it is a pre-draw hypothesis
refusal (§9.2).

On an integrity stop:

- Preserve the partial receipt immutably.
- Do not rerun or replace any world or draw.
- Treat the attempt as spent. Any retry requires a new dated protocol and a
  new master seed.

### 13.4 Separate evidence

- The earlier surrogate-statistic Scheme D evidence stays separate and is not
  pooled with results from this protocol.
- Results here are not combined with any real-data result.

## 14. Required pre-run freeze

All of the following must be committed and SHA-256 manifested **before any
RNG is constructed**:

1. The generated synthetic config, encoding exactly the fixed numeric design
   in this document:
   - geometry and quarter/regime rules (§7.1);
   - membership thresholds (§4);
   - DGP distributions, rates and shifts (§7.2);
   - C0–C4 constructions, including C4 λ = 0.60, θ = 60° (§6);
   - base DGP N1 for power worlds;
   - injection rules and index sets (§11.1);
   - PCG64 and the seed map (§8);
   - with-replacement uniform within-strata sampling with B = 767,999 and
     no replacement of failed draws (§10.1);
   - the tie rule `T* ≥ T − 1e-12·max(1,|T|)` (§10.2);
   - the derived null-MAD values, with their verification.
2. The hypothesis membership table (384 rows, with indices, kinds,
   thresholds, cluster and sector labels).
3. The effect-assignment table (scenario codes, target indices, magnitudes,
   target class).
4. The **analytic truth tables** for every null and injected cell (§11.2).
   Each table records the true-null or analytic-non-null class, the proven
   sign, and a reference to the proof.
5. The exact latent loading matrices and the implied covariance matrices for
   C0–C4, each with a PSD proof (minimum eigenvalue recorded).
6. The runner and the deterministic check suite. The check suite covers:
   - metric equivalence on fixtures, against the imported function or a
     byte-identical vendored copy;
   - BH with refused hypotheses kept in m = 384;
   - the p-value formula and tie rule;
   - with-replacement sampling that includes the identity;
   - orbit and floor computation;
   - every invalid-case refusal, including the linked-copy crossing case;
   - uniqueness of seed tuples;
   - checks that the config matches this document;
   - exclusive-create behaviour.
7. A requirements lock, and interpreter/NumPy/BLAS metadata (Python 3.12.3,
   NumPy 2.5.2, `OPENBLAS_NUM_THREADS=1`).
8. A verification that the three implementation hashes in §2 match, and that
   the baseline commit `ab9e39683f5e9f756fc7ef6690133ecb5b608f64` is an
   ancestor of the freeze commit.
9. Import isolation. The runner must not access a production DB, the network
   or kernel/runtime modules. It asserts this at startup by checking loaded
   modules and blocking socket creation.

Outputs:

- All outputs are exclusive-create.
- Each world gets one aggregate receipt: per-hypothesis `X_h`, `p_h`
  (or `null`), observed `signed`, refusal codes, discovery classification
  (true, false or attribution error), and the world's seed tuple. Each
  receipt is hashed.
- Per-cell summaries and a final run manifest cover all receipt hashes.
- Raw permutations and raw permuted statistics are not stored.

## 15. Compute estimate (estimate, not measured)

| Quantity | Value |
|---|---|
| Worlds | 20 × 2000 + 35 × 1000 = **75,000** |
| Synchronized permutation maps | 75,000 × 767,999 = **57,599,925,000** |
| Hypothesis-draw evaluations | × 384 = **22,118,371,200,000** |
| Raw float64 statistic matrix | ≈ **176.9 TB**, so it is never materialized; only aggregates are streamed |
| Permutation-generation lower bound | a prior run of 10,000 × 1,999 single-statistic draws took 49.4 s (≈ 2.47 µs per map), which extrapolates to ≈ **39.5 CPU-hours** |
| Naive 384-statistic scaling | ≈ **15,200 CPU-hours** |
| Expected optimized runtime | hundreds to low thousands of CPU-hours (**unmeasured**) |
| Receipts | < 10 GB |

These figures are extrapolations. Before the owner is asked for authorization,
the actual optimized runtime must be benchmarked on a tiny noninferential
workload under phase code 90.

## 16. Decision semantics

- **All acceptance cells pass.** Scheme D, with the actual M3.2 statistic, BH
  across 384 and this conditioning, becomes **eligible for separate owner
  review** only. It is not frozen, and nothing is authorized. Historical
  search, any held-out look, Gate 2, the referee, handoff and demo/live use
  each still need their own authorization under their own protocols. The
  recorded dependence-corrected gate stop is unaffected.
- **Any acceptance cell fails, or a refusal blocker fires.** Scheme D is
  **not validated** for the actual statistic. The failure is reported as
  observed, with no reinterpretation. No parameter, scenario or definition may
  be changed under this protocol. Any redesign needs a new dated protocol with
  a new seed.
- **Pre-RNG truth-table refusal.** The protocol cannot run as frozen. No RNG
  has been spent. Revision requires a new dated protocol.
- **Integrity stop.** The attempt is spent. Report the partial receipt. The
  procedures in §13 apply.

## 17. Single next task

1. Implement the synthetic-only harness, the deterministic check suite, and
   the symbolic truth-table propagation (§14 items 4 and 6).
2. Generate the config and **verify** that it encodes exactly the fixed
   numeric design in this document.
3. Produce the analytic truth tables, and confirm that every cell is fully
   classified.
4. Benchmark a tiny noninferential workload using phase code 90 only. No
   acceptance-world seed tuples may be constructed.
5. Commit and SHA-256 manifest everything listed in §14.
6. Then ask for owner authorization before any validation run.

This protocol stays **NOT AUTHORIZED TO RUN** until that authorization is
recorded.
