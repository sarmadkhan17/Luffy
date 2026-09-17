# Gate-2 assumptions-first inference design (conditional proposal)

Written by Claude, 2026-09-16. **Documentation only.** This is a conditional
mathematical proposal, not an approved protocol, not a registered experiment and
not production evidence. Nothing here admits a candidate, changes a gate, spends
an error budget, or certifies any numeric constant.

No experiment was run, no data, config, ledger, log, artifact or /tmp dossier was
inspected, and no runtime or operational state was touched in producing it.

**Read scope, by role.** *Claude's drafting reads* were limited to `CLAUDE.md`,
`2026-09-16-gate2-next-session.md`, `2026-09-16-gate2-inference-requirements.md`
and lines 120-190 of `2026-09-16-gate2-prediction-test.md`. *The coordinating
review* additionally read the full original prediction-design document and one
public mathematical reference for the Markov/Chebyshev background
(<https://www.stat.berkeley.edu/~stark/SticiGui/Text/clt.htm>); that reference
supplies textbook inequality background only and **certifies no production bound,
constant or protocol here**. Neither role performed held-out, database, config,
ledger or broad log reads. All prior experiments (unstudentized,
studentized, stationary/HAC), the frozen thesis, the frozen protocol, both /tmp
dossiers and every frozen artifact and hash manifest remain untouched and remain
the authority for their own recorded results. The recorded dependence-corrected
gate stop stands; `research.referee=false` and `research.handoff=false` are
unchanged.

The three failed calibration variants were all *estimator-first*: a resampling
scheme was chosen and its finite-sample error rate was then measured. This design
is *assumptions-first*: the permitted outcome-tail and dependence class is stated
first, each quantitative bound is traced to a source, and the guarantee is derived
inside that class. Its coverage is therefore a theorem conditional on supplied
constants, not a simulated rate — but it is only as good as the constants, and
**no constant is certified today**.

---

## 1. Sample metric preserved; population target specified below

The registered metric is preserved exactly. `mean_r` is the **untrimmed
trade-weighted arithmetic mean** of the engine's own `r` over a group: every
complete trade counts exactly once, at the R `walk_table`/`_trade` produced, with
`taker_fee_pct`, `slippage_atr_frac` and signed funding already charged inside the
engine. No trimming, winsorizing, outlier rule, per-symbol reweighting or cap on a
single trade's contribution is introduced. Gate 2 recomputes no cost and re-derives
no R.

The partition is unchanged: for a prediction with target group `S`, the arms are
the subset `S` and its **complement** `B` within the slice, `unknown` trades (entry
bar condition not known under the strict three-valued rule) enter **neither** arm
and no block, and `unknown` is still counted and reported per symbol and per slice
so that **all original trades are accounted for**. Universe and geometry stay at the
candidate's own `tf=4h`, `geo=trail`, unchanged `ExitSpec`, unchanged symbol list.

The signed contrast keeps its registered orientation:

```
delta      = mu_S - mu_B          (population, relation ">")
delta_hat  = mean_r(S) - mean_r(B) (sample, relation ">")
```

For `relation == "<"` every statement below applies with the arms swapped (`S` and
`B` exchanged in the contrast, the rectangle, the corner ratios and the power
condition); no separate derivation is needed. A nonpositive `delta_hat` remains a
FAIL decided independently of any inferential quantity.

**The point estimate is a ratio of sums, never an average of block means.** The
per-block means are not the estimator and are not the target; blocks with zero
trades in an arm are real zero-count blocks, not missing observations to be
dropped or reweighted.

---

## 2. Sampling units and the block vector

Fix, before any look, a **common calendar** partition of the evaluation span into
`n` non-overlapping blocks `k = 1..n`, identical for every symbol (the existing
common-calendar requirement; per-symbol cuts leak eras). `n` is fixed in advance and
is **not** a function of observed counts, outcomes or any stopping rule.

For each block `k` define the four-coordinate vector

```
Z_k = (A_Sk, N_Sk, A_Bk, N_Bk)

A_gk = sum of the original engine R over all complete trades assigned to arm g
       and block k                              (arm sum, untrimmed)
N_gk = number of such trades                    (arm count; zero-count blocks
                                                 contribute N_gk = 0 and A_gk = 0)
```

Trades are aggregated **across all symbols** into the same block; no within-symbol
or between-symbol independence is assumed anywhere in this design. Assignment of a
trade to a block uses the registered entry-bar rule. Write `Z_jk` for coordinate
`j ∈ J = {A_S, N_S, A_B, N_B}` and

```
Zbar_j = (1/n) * sum_{k=1..n} Z_jk
```

The sample statistic is exactly the registered one:

```
mean_r(g) = (sum_k A_gk) / (sum_k N_gk) = Zbar_{A_g} / Zbar_{N_g}
```

so the whole inference reduces to simultaneous bounds on four coordinate means.

---

## 3. Population target — and an open ambiguity that needs authorization

Conditional on the selection sigma-field `F` (Section 8), the target for arm `g` is

```
mu_g = ( sum_{k=1..n} E[A_gk | F] ) / ( sum_{k=1..n} E[N_gk | F] )
     = m_{A_g} / m_{N_g},      m_j := (1/n) * sum_k E[Z_jk | F]
```

with `m_{N_g} > 0` required for the target to be defined.

This is the **expected-count-weighted trade population** over the fixed `n` blocks.
It is explicitly:

- **not** `E[ sample ratio ]` — the expectation of a ratio is not the ratio of
  expectations, and this design never claims the plug-in estimator is unbiased;
- **not** a conditional-on-realised-counts null — conditioning on the observed
  `N_gk` would define a different estimand and a different (generally
  non-comparable) null;
- **not** an average of per-block means — blocks with more expected trades weigh
  more, which is what "trade-weighted" means at the population level.

**Open ambiguity, flagged rather than resolved.** The frozen prediction spec fixes
the *sample* statistic unambiguously (trade-weighted, untrimmed) but does not, in
the lines read here, fix which population object that statistic estimates. If the
originally intended population is something else — e.g. a realised-count conditional
population, an equal-weight-per-block population, or a per-symbol-then-pooled
population — then `mu_g` above is a **redefinition** of the estimand, and no evidence
run or registration may rely on it until that change is explicitly authorized in a
separately registered contract amendment. This report does not silently redefine the
target population and does not assert that the expected-count-weighted reading is the
registered one.

The expected-count-weighted reading is a **working mathematical interpretation
pending alignment**. It blocks registration and evidence access, not design work:
continuing to develop, compare or replace this design does not require resolving A6
first, and A6 is not an additional permission gate on further analysis.

---

## 4. Required quantitative inputs: variance bounds and a covariance envelope

The design consumes, as *supplied and justified* inputs, for each coordinate
`j ∈ J`:

- **Per-coordinate conditional variance bound** `v_j`, valid uniformly over blocks:

  ```
  Var(Z_jk | F) <= v_j        for every k = 1..n
  ```

- **Absolute conditional covariance envelope** `c_j(h) >= 0`, valid uniformly over
  block pairs at lag `h`:

  ```
  |Cov(Z_jk, Z_{j,k+h} | F)| <= c_j(h)    for every k and every h >= 1
  ```

No independence is assumed within a symbol, between symbols, or between blocks;
the envelope is the only dependence input.

**Measurability and finiteness of the inputs.** Every supplied quantity — `K`, `M`,
the lag `m`, each `v_j` and each `c_j(h)` — must be a **raw scalar** that is either
deterministic or **`F`-measurable**, and **finite**. They may not depend on data
outside `F` (in particular not on the evaluation outcomes), and an infinite or
unbounded value is not admissible: a supplied `+inf`, an unbounded second moment, or
an envelope with divergent `sum_h c_j(h)` yields no `V_j(n)` and therefore no
guarantee. Where a constant is `F`-measurable rather than deterministic, all bounds
below hold pointwise on `F` in exactly the same form, since the entire derivation is
conditional on `F`.

### 4a. The variance-of-the-mean bound

```
Var(Zbar_j | F)
  = (1/n^2) [ sum_{k=1..n} Var(Z_jk | F)
              + 2 * sum_{h=1}^{n-1} sum_{k=1}^{n-h} Cov(Z_jk, Z_{j,k+h} | F) ]
 <= (1/n^2) [ n*v_j + 2 * sum_{h=1}^{n-1} (n-h) * c_j(h) ]
  =: V_j(n)
```

using only bilinearity of covariance, the triangle inequality, and the count of
`(n-h)` ordered pairs at lag `h`. This is an upper bound; it is valid under
arbitrary dependence consistent with the supplied envelope, and it does **not**
require stationarity, mixing rates, or any resampling scheme.

### 4b. A concrete sufficient class for `v_j`

A sufficient — not necessary — structural class:

- **Bounded potential trade entries per block.** There are at most `K` potential
  trade-entry slots in any block (across the entire universe), counting trades whose
  *entry* bar falls in the block, where absent, incomplete or
  `unknown` slots contribute exactly zero to both `A_gk` and `N_gk`.
- **Uniform conditional second moment.** For every slot, `E[R_slot^2 | F] <= M`,
  where `R_slot` is the original engine R of that slot (zero when the slot is
  absent/unknown).

Then, writing `A_gk = sum_{i=1..K} X_i` with `X_i = R_i * 1{slot i in arm g}`,
Cauchy-Schwarz gives `(sum_i X_i)^2 <= K * sum_i X_i^2`, hence

```
Var(A_gk | F) <= E[A_gk^2 | F] <= K * sum_{i=1..K} E[X_i^2 | F] <= K^2 * M
   =>  v_{A_g} = K^2 * M

N_gk takes values in [0, K], so, self-contained:
   Var(N_gk | F) <= E[(N_gk - K/2)^2 | F] <= K^2 / 4
   =>  v_{N_g} = K^2 / 4
```

The count step needs no named inequality: variance is the minimum over constants `c`
of `E[(N_gk - c)^2 | F]`, so it is at most the value at `c = K/2`, and
`|N_gk - K/2| <= K/2` pointwise because `N_gk ∈ [0, K]`.

**Where `K` and `M` must come from — and must not come from.**

- `K` must be a **proved** bound from the engine, universe and time grid: the
  declared symbol list size, the `walk_table` per-symbol sequential structure
  (one position at a time, `cursor = exit_i + 1`, `max_open` not read), the `4h`
  bar count per block, and the long/short pair structure. It is a combinatorial
  bound on how many trades can possibly **enter** in a block — block assignment is
  by the registered entry-bar rule (Section 2), so `K` bounds trades *entering* a
  block, not trades closing into it — derived from source and the fixed calendar,
  not an observed maximum and not a typical count.
- `M` must be **externally justified**. Stop distances do **not** bound R: gaps,
  funding, slippage, partial/late protective execution and adverse fills can each
  carry realised R beyond the nominal stop. Any `M` asserted from "R is capped at
  -1 on the downside and the trail caps the upside" is invalid. A defensible `M`
  needs an argument about the realised R second moment under the engine's actual
  cost and exit model, from a source that is not the same short cohort being tested.
- Estimating `K` or `M` from the evaluation cohort does **not** make them known.
  A plug-in second moment is an estimate with its own error and would void the
  finite-sample guarantee.

**No constants are certified now.** `K` and `M` are symbols in this document.

### 4c. Admissible forms of the envelope `c_j(h)`

Two admissible routes, both requiring proof:

1. **Exact m-dependence of the complete block vectors.** `Z_k` and `Z_{k+h}` are
   conditionally independent given `F` for all `h > m`. Then, by Cauchy-Schwarz,
   `c_j(h) = v_j` is a safe envelope choice for `h <= m` and `c_j(h) = 0` for `h > m`, giving

   ```
   V_j(n) <= [ n*v_j + 2*v_j * sum_{h=1}^{min(m, n-1)} (n-h) ] / n^2
          <= v_j * (2m + 1) / n
   ```

   Under the sufficient class of 4b this yields the closed forms

   ```
   V_{A_g}(n) <= K^2 * M * (2m+1) / n
   V_{N_g}(n) <= (K^2 / 4) * (2m+1) / n
   ```

2. **Quantitatively supplied decay.** Any `c_j(h)` with a proved derivation and a
   finite `sum_h c_j(h)` is admissible; `V_j(n)` is then evaluated from 4a directly.

**m-dependence must be of the complete block vectors, not merely of prices.** The
objects that must decouple are `Z_k` — arm sums and arm counts of completed engine
trades — so `m` must dominate every channel that links blocks:

- **Holding across boundaries.** A trade entering in block `k` and exiting later
  makes `Z_k` depend on bars in subsequent blocks; `m` must cover the maximum
  possible holding span in blocks, from a proved exit-geometry bound, not an
  observed median.
- **Feature lookback.** Indicator warmup and lookback (including `WARMUP=210` bars
  and any `htf()`/`ref()` alignment) couple the entry condition to earlier blocks.
- **Position sequencing.** `cursor = exit_i + 1` makes the eligibility of the next
  trade on a symbol a function of the previous trade's exit; occupancy carries
  state across boundaries.
- **Selection / partition state.** The subset label (H1/H2-style local trend
  states) is itself persistent, and any selection-induced linkage must be inside
  the envelope, not outside it.

**Calendar blocking does not certify m-dependence, and neither does a sample ACF.**
Choosing 30-day blocks does not make blocks independent; an autocorrelation estimate
that decays to noise is an estimate on the same cohort, not proof that long
dependence is absent. The three prior calibration experiments do not certify these
assumptions.

---

## 5. Simultaneous rectangle by Chebyshev and a union bound

Fix `alpha ∈ (0,1)` in advance. Define the half-widths

```
r_j(alpha) = sqrt( 4 * V_j(n) / alpha )          for j in J
```

and the rectangle

```
R(alpha) = X_{j in J} [ Zbar_j - r_j(alpha),  Zbar_j + r_j(alpha) ]
```

**Claim.** `P( m_j in [Zbar_j - r_j, Zbar_j + r_j] for all four j | F ) >= 1 - alpha`.

**Proof.** Fix `j` and suppose `V_j(n) > 0`, so `r_j > 0`. Let
`D_j = (Zbar_j - m_j)^2`, a nonnegative random variable with
`E[D_j | F] = Var(Zbar_j | F) <= V_j(n)` by Section 4a. Markov's inequality applied
to `D_j` at threshold `r_j^2` gives

```
P( |Zbar_j - m_j| >= r_j | F ) = P( D_j >= r_j^2 | F )
                              <= E[D_j | F] / r_j^2
                              <= V_j(n) / r_j^2
                              = V_j(n) * alpha / (4 * V_j(n))
                              = alpha / 4
```

This is the squared-deviation Markov (Chebyshev) step, and it needs only the
existence of the conditional variance bound — no tail shape, no symmetry, no
asymptotics, no resampling distribution. Union-bounding the four coordinate failure
events,

```
P( any coordinate misses | F ) <= 4 * (alpha/4) = alpha
```

so the complement, simultaneous coverage of all four population coordinates, has
conditional probability at least `1 - alpha`. The union bound requires no
independence between coordinates, which matters because `A_S` and `N_S` (and the
`S`/`B` pair) are strongly dependent by construction. ∎

**Degenerate case `V_j(n) = 0`, handled exactly.** If `V_j(n) = 0` then
`Var(Zbar_j | F) = 0`, so `Zbar_j = m_j` almost surely and the zero-width interval
`{Zbar_j}` covers with conditional probability 1. Set `r_j(alpha) = 0`; the union
bound is unaffected (that coordinate contributes failure probability 0, not
`alpha/4`). No division by zero occurs anywhere, and no widening is applied to
"regularize" the case.

**Missing or invalid bounds.** If any `v_j` is not supplied, or `c_j(h)` is not
supplied for all required lags, or a supplied constant fails its provenance
requirement, the procedure returns **UNAVAILABLE** for that prediction. It does not
substitute a sample variance, a plug-in second moment, a fitted mixing length or a
bootstrap width. UNAVAILABLE is not a pass and not a fail, and it grants nothing.

**Budget semantics, stated precisely.** An assumption failure detected **before any
evidence access** means: do not start evidence access and do not reserve — nothing is
spent because nothing was begun. An assumption failure discovered **after a held-out
look** does **not** refund or erase spending: the look already happened, the existing
accounting rules and the no-free-retry rule apply unchanged, and UNAVAILABLE is
recorded as the outcome of a spent attempt rather than as a costless abort. This
document performs no ledger action of any kind and does not itself reserve, settle,
refund or annotate anything.

---

## 6. From the rectangle to a ratio contrast, and the decision rule

Write the rectangle endpoints per arm `g`:

```
a_g^- = Zbar_{A_g} - r_{A_g},   a_g^+ = Zbar_{A_g} + r_{A_g}
d_g^- = Zbar_{N_g} - r_{N_g},   d_g^+ = Zbar_{N_g} + r_{N_g}
```

**Positivity gate on the counts.** Both count-mean lower endpoints must be strictly
positive:

```
d_S^- > 0  and  d_B^- > 0
```

If either fails, the arm's population mean is not bounded by this construction (the
denominator interval contains zero, so the ratio range is unbounded), the result is
**no rejection**, and it is recorded as such. This is a fail-closed outcome, not an
error to be worked around by enlarging the arm, moving the calendar or dropping a
coordinate.

**Corner enumeration with sign preservation.** On `d > 0` the map `(a,d) -> a/d` is
increasing in `a` for fixed `d`, and monotone in `d` for fixed `a` (decreasing when
`a > 0`, increasing when `a < 0`, constant when `a = 0`). Extremes over the
rectangle are therefore attained at corners, for numerators of either sign:

```
mu_g^- = min over the four corners { a_g^± / d_g^± }
mu_g^+ = max over the four corners { a_g^± / d_g^± }
```

Negative arm sums are handled correctly by taking the min/max over all four corners
rather than pairing "low numerator with high denominator" by assumption.

**Lower contrast and the rejection rule.**

```
L = mu_S^- - mu_B^+
```

Reject the null `H0: delta <= 0` **only if all** of the following hold:

1. `L > 0`;
2. `delta_hat > 0` (the registered sign check, strict);
3. the eligibility floors registered in the frozen protocol are met, unchanged.

**Validity.** On the coverage event of Section 5 (conditional probability
`>= 1 - alpha`), `m_{A_g}` and `m_{N_g}` lie in their intervals for both arms, so
`mu_g = m_{A_g}/m_{N_g}` lies in `[mu_g^-, mu_g^+]` by the corner enumeration. Hence
`delta = mu_S - mu_B >= mu_S^- - mu_B^+ = L`. If `H0` holds, i.e. `delta <= 0`, then
`L > 0` forces `delta >= L > 0`, a contradiction, so `{L > 0}` is contained in the
coverage-failure event and has conditional probability at most `alpha`. Conditions
2 and 3 only shrink the rejection region, so the conditional type-I error is at
most `alpha`; averaging over `F` preserves it unconditionally. ∎

Condition 2 is in fact implied by condition 1 — the sample point
`(Zbar_{A_g}, Zbar_{N_g})` is the centre of the rectangle, so `delta_hat` lies in
`[mu_S^- - mu_B^+, mu_S^+ - mu_B^-]` and `L > 0` gives `delta_hat >= L > 0`. It is
retained as an explicit, independently recorded fail-closed guard, exactly as the
frozen protocol requires the sign and the inferential quantity to be separate fields
where neither can stand in for the other.

**Reverse orientation.** For `relation == "<"`, swap the arms throughout: the null
is `mu_B - mu_S <= 0`, the lower contrast is `L = mu_B^- - mu_S^+`, and
`delta_hat = mu_B_hat - mu_S_hat`. Nothing else changes.

---

## 7. Optional p-value by inversion — and why omitting it is the recommended default

The fixed-level test above is the deliverable. A p-value is **optional** and is not
required by anything in this design.

If one is wanted, note the tests are nested in `alpha`: `r_j(alpha)` is strictly
decreasing in `alpha` when `V_j(n) > 0`, so the rectangle shrinks and the rejection
region is monotone increasing in `alpha`. Define

```
p = inf { a in (0,1) : the level-a test rejects }      (inf of empty set = 1)
```

For a true null and any `a' > a`: `{p < a'}` implies rejection at some level below
`a'`, hence rejection at `a'` by nestedness, so `P(p < a') <= P(reject at a') <= a'`.
Taking `a' ↓ a` gives `P(p <= a) <= inf_{a' > a} a' = a`, the endpoint/continuity
step that closes the boundary case (`L(a)` is continuous in `a` for `V_j(n) > 0`,
and the rejection rule is strict, `L > 0`). So `p` is a valid super-uniform p-value.
In the degenerate all-`V_j(n) = 0` case the widths are identically zero, the test no
longer depends on `alpha`, and `p` collapses to `0` or `1`; that case must be
recorded explicitly rather than reported as a continuous p.

**Recommendation: omit `p` and report the fixed-level decision plus `L`.** This is a
simplicity preference, not a validity objection. Under the stated assumptions the
inverted `p` **is** super-uniform, exactly as proved above; it may be conservative because the
variance bounds and union bound can be loose. A non-rejection establishes neither
the null nor the absence of an edge. Reporting a fixed level is easier to read and harder to over-read, but it
**does not eliminate any accounting**: a fixed-level decision consumes the same
error budget under the same rules as any other evidence output. A numerically small
inverted `p` must never be allowed to substitute for the sign check or the
eligibility floors.

---

## 8. Selection, conditioning and the sigma-field `F`

Every moment condition, covariance envelope and target definition above is stated
**conditional on `F`**, and `F` must contain the *entire* information used to reach
this prediction: gate-1 discovery output and its dependence-corrected/common-rotation
machinery, the candidate's selection and freezing, the slice and symbol list, the
prediction text, the partition rule, the calendar, `n`, `alpha`, and any ranking or
filtering applied along the way.

Requirements:

- `v_j`, `c_j(h)` and the target `mu_g` must be valid **given `F`**, not merely
  marginally. A second-moment bound that holds unconditionally can fail conditionally
  on a selection event that preferentially picks large-variance cohorts.
- **Fresh, independently sampled units are one sufficient route to this, not the
  only one, and not automatic.** Carving out a new slice from the same history does
  not create independence from `F`, and renaming a window does not make it untouched.
  A genuinely fresh sampling route must be argued, not asserted.
- **No optional stopping and no count-dependent horizon.** `n`, the calendar and the
  evaluation span are fixed before any look. Extending the span because counts were
  low, stopping early because `L` turned positive, or re-cutting blocks after seeing
  the partition all void the guarantee.
- Gate 1's existing dependence correction and common-rotation check are preserved;
  nothing here substitutes a raw pooled consistency p-value or weakens the recorded
  stop.
- Repeated analyst decisions still need episode-level clustering before evaluation;
  that requirement is upstream of this design and is not discharged by it.

---

## 9. Sufficient power condition (bound, not a calibrated MDE)

Fix a second level `gamma ∈ (0,1)` and build a second rectangle by the identical
argument: with conditional probability at least `1 - gamma`,
`|Zbar_j - m_j| <= r_j(gamma)` for all four `j` simultaneously. Define the
high-probability widths

```
w_j = r_j(alpha) + r_j(gamma) = sqrt(4*V_j(n)/alpha) + sqrt(4*V_j(n)/gamma)
```

On that event the realised level-`alpha` interval for coordinate `j` is contained in
`[m_j - w_j, m_j + w_j]`, so the realised `[mu_g^-, mu_g^+]` is contained in the
corner range of the population-centred rectangle
`a_g ∈ [m_{A_g} ± w_{A_g}], d_g ∈ [m_{N_g} ± w_{N_g}]`.

**Sufficient condition for the *mathematical* rejection region, with conditional
probability `>= 1 - gamma`.** What (P1)/(P2) below deliver is `L > 0` — conditions 1
and 2 of the Section 6 rule. They say **nothing** about condition 3, the eligibility
floors registered in the frozen protocol, which are a separate event that can fail on
the same realisation (too few trades in an arm, per-symbol coverage, or any other
registered floor), and which this argument does not control.

```
(P1)  m_{N_g} - w_{N_g} > 0            for g in {S, B}
      (expected denominators strictly exceed their widths)

(P2)  min-corner ratio of arm S over the widened rectangle
        minus max-corner ratio of arm B over the widened rectangle  >  0
```

A convenient sufficient form of (P2): for `d_g >= m_{N_g} - w_{N_g} > 0`,

```
| a_g/d_g - mu_g | = | a_g - mu_g*d_g | / d_g
                  <= ( |a_g - m_{A_g}| + |mu_g| * |d_g - m_{N_g}| ) / d_g
                  <= ( w_{A_g} + |mu_g| * w_{N_g} ) / ( m_{N_g} - w_{N_g} )
```

so (P2) holds whenever the true contrast exceeds the sum of the two arm envelopes:

```
delta  >  sum_{g in {S,B}}  ( w_{A_g} + |mu_g| * w_{N_g} ) / ( m_{N_g} - w_{N_g} )
```

**From rejection region to full-gate power.** Let `E_floor` be the event that the
registered eligibility floors are met. Then

```
P( full rejection | F ) = P( {L > 0} ∩ E_floor | F )
                       >= 1 - gamma - P( E_floor^c | F )
```

so a power statement for the gate as actually implemented requires a **separately
proved** bound

```
P( eligibility floors fail | F ) <= eta
```

and only then

```
P( reject with floors | F ) >= max( 0, 1 - gamma - eta )
```

**Without such an `eta` there is no lower bound on full-gate power at all** — (P1)
and (P2) alone bound only the mathematical part. `eta` must be derived from the floor
definitions and the same `F`-conditional count model (it is a statement about the
`N_gk`, not about `delta`), and it is **not supplied here**. Quoting `1 - gamma` as
the gate's power would be wrong whenever the floors can bind.

**Scaling and feasibility.** Under the m-dependent sufficient class of 4b/4c,
`V_j(n) <= v_j (2m+1)/n`, so

```
w_{A_g} <= 2*K*sqrt( M*(2m+1)/n ) * ( 1/sqrt(alpha) + 1/sqrt(gamma) )
w_{N_g} <=   K*sqrt(   (2m+1)/n ) * ( 1/sqrt(alpha) + 1/sqrt(gamma) )
```

These are **inequalities, not proportionalities**: they substitute the upper bound
`V_j(n) <= v_j (2m+1)/n` into `r_j(·) = sqrt(4 V_j(n)/·)`, which is increasing in
`V_j(n)`, so the right-hand sides bound the widths from above and are not claims
about their true scale.

Chebyshev's dependence on the level is `sqrt(1/alpha)`, not `sqrt(log(1/alpha))`.
At the rare-error levels the previous experiments targeted this is **loose**: halving
`alpha` inflates the level-`alpha` half-width `r_j(alpha)` by exactly `sqrt(2)` (the
combined width `w_j = r_j(alpha) + r_j(gamma)` grows by less, since the `r_j(gamma)`
term is unchanged), and the required `n` scales roughly like
`K^2 * M * (2m+1) / (alpha * delta^2 * m_N^2)`. Rare-`alpha` feasibility is therefore
the binding practical question for this design, and it is honest to say so up front:
the guarantee is uniform and finite-sample, and it is paid for in sample size.

**No calibrated MDE is stated and none can be**, because `K`, `M` and `m` are
uncertified symbols and `m_{N_g}`, `mu_g` are unknown population quantities. Any
detectable-effect number produced before those are fixed would be fiction. Also note
explicitly: **"the variance is finite" is not enough.** Without a *quantitative*
bound there is no `V_j(n)`, no width, no guarantee and no power statement — this is
the same obstacle the inference-requirements note identified for unbounded tails.

---

## 10. Family-level statements (symbolic only)

Recorded for completeness; nothing here is proposed for adoption now.

- **Conjunction over a family requiring every prediction.** If valid constituent
  p-values are later constructed, `p_family = max_j p_j` is valid for the family
  null "at least one constituent null is true": whenever constituent `i` is true,
  `{max_j p_j <= a} ⊆ {p_i <= a}`, which has probability `<= a`. No independence
  between constituents is needed.
- **Without p-values.** The same conjunction works with fixed-level tests directly:
  reject the family at level `alpha` only if **every** constituent rejects at level
  `alpha`. If constituent `i`'s null is true, the family rejection event is contained
  in constituent `i`'s rejection event, probability `<= alpha`.
- **Across a predeclared sequence of families**, fixed spending `beta * w_j` with
  `w_j >= 0` and `sum_j w_j <= 1` bounds any-false-family error by `beta` via a union
  bound, with no dependence-based reward assumptions.

These are **symbolic** statements. **No numerical `beta` or `alpha` target is
selected here, LORD++ is not replaced or modified, no admission is granted, and no
approval is implied.** The practical dependency order from the requirements note
stands: justify the marginal inferential model and sampling units, then validate the
actual selection/partition protocol, then approve any family-error objective.

---

## 11. Provenance of every assumption

| # | Assumption / constant | Statement | Source class | Status today |
|---|---|---|---|---|
| A1 | Estimand: untrimmed trade-weighted mean R | Section 1 | Frozen prediction spec §3 (read) | **Registered — preserved unchanged** |
| A2 | Partition S / complement B, `unknown` excluded but counted | Section 1 | Frozen prediction spec §2 (read) | **Registered — preserved unchanged** |
| A3 | Sign check `delta_hat > 0` separate from inferential quantity | Section 6 | Frozen prediction spec §3a (read) | **Registered — preserved unchanged** |
| A4 | Common calendar blocks, fixed `n`, fixed before any look | Section 2 | Repo research safeguard (shared calendar cut) | **Design requirement — must be preregistered** |
| A5 | Population target `mu_g` = expected-count-weighted ratio of sums | Section 3 | This document | **UNCERTIFIED — open ambiguity vs originally intended population; requires explicit authorization (see A6)** |
| A6 | Target-population authorization | If the intended population differs from A5, A5 must not be used | Contract amendment by the user | **OPEN — not resolved here, not silently redefined** |
| A7 | `K` = bounded potential trade slots per block | `Var(A_gk\|F) <= K^2 M`, `Var(N_gk\|F) <= K^2/4` | Must be *proved* from engine source + declared universe + `4h` time grid | **NOT DERIVED — symbol only; production uncertified** |
| A8 | `M` = uniform conditional second moment `E[R_slot^2\|F] <= M` | Section 4b | Must be *externally justified*; stop distance is **not** a valid bound (gaps, funding, slippage, late protective fills) | **NOT JUSTIFIED — symbol only; production uncertified** |
| A9 | Covariance envelope `c_j(h)` | Exact m-dependence of complete block vectors, or quantitative decay | Must be *proved*; calendar blocking and sample ACF certify neither | **NOT PROVED — production uncertified** |
| A10 | `m` covers holding across boundaries, feature lookback/warmup, position sequencing, selection-state persistence | Section 4c | Must be proved per channel from exit geometry + feature contracts + `walk_table` structure | **NOT PROVED — production uncertified** |
| A11 | Conditional validity of A7–A10 given `F` (gate-1 + entire selection information) | Section 8 | Must be argued for the actual selection/evaluation protocol | **NOT ESTABLISHED — production uncertified** |
| A12 | No optional stopping, no count-dependent horizon | Section 8 | Design requirement | **Must be preregistered** |
| A13 | Chebyshev + union-bound coverage | Section 5 | Mathematical — proved here from squared-deviation Markov; textbook background only at the SticiGui CLT/inequalities page read in review | **Proved, conditional on A7–A11 supplying `V_j(n)`** |
| A14 | Corner ratio bound + `d^- > 0` gate | Section 6 | Mathematical — proved here | **Proved** |
| A15 | Type-I `<= alpha` for the decision rule | Section 6 | Mathematical — proved here | **Proved, conditional on A5 and A7–A12** |
| A16 | Power condition (P1)/(P2) | Section 9 | Mathematical — derived here | **Derived for the mathematical rejection region `{L > 0}` only; not numerically evaluable (A7–A10 uncertified)** |
| A16b | Eligibility-floor failure bound `P(floors fail\|F) <= eta` | Section 9 | Must be *proved* from the registered floor definitions and the `F`-conditional count model | **NOT SUPPLIED — without it there is no full-gate power lower bound; with it, `P(reject with floors\|F) >= max(0, 1-gamma-eta)`** |
| A16c | Supplied constants are raw scalars, `F`-measurable and finite | Section 4 | Design requirement on `K`, `M`, `m`, `v_j`, `c_j(h)` | **Must be preregistered and checked** |
| A17 | Optional inverted p-value | Section 7 | Mathematical — nestedness + endpoint/continuity | **Valid (super-uniform) under the stated assumptions if constructed; omission recommended for simplicity only, and omission does not remove any accounting** |
| A18 | Family conjunction / union-bound spending | Section 10 | Mathematical, symbolic | **Symbolic only — no target, no LORD++ replacement, no approval** |

Nothing in the "production uncertified" rows may be filled in from the evaluation
cohort itself, from a fitted mixing length, from an apparent autocorrelation cutoff,
or from any of the three completed calibration experiments' synthetic fixtures.

---

## 12. Preregistration checklist for a future session

**No experiment is registered and none is run today.** Before any registration, all
of the following must be written down and frozen:

**Assumption sourcing**
1. Derivation of `K` from engine/universe/time-grid facts, with the source lines it
   rests on, and the argument that no block can exceed it.
2. External justification of `M`, explicitly addressing gaps, funding, slippage and
   protective-execution shortfall; explicit statement that stop distance does not
   bound R.
3. Proof or supplied decay for `c_j(h)`, at the level of complete block vectors, with
   the four coupling channels of Section 4c each covered and `m` (or the decay)
   attributed per channel.
4. Argument that 1–3 hold **conditionally on `F`**, including gate-1 selection.
5. Statement of whether A5 matches the originally intended population; if not, an
   explicit authorization request before proceeding.

**Design fixing**
6. Calendar definition, `n`, evaluation span, `alpha`, `gamma`, eligibility floors,
   orientation per prediction — all fixed before any look, with no count-dependent
   horizon and no optional stopping.
7. Decision-rule spec: `UNAVAILABLE` semantics, `V_j(n) = 0` handling, `d^- <= 0` →
   no rejection, whether `p` is reported at all (default: not reported).
8. Accounting for all original trades: `S`, `B`, `unknown` counts reconciling to the
   engine's total, per symbol and per slice.

**Power**
9. Evaluate (P1)/(P2) symbolically with the now-sourced `K`, `M`, `m` and a
   *pre-specified* plausible `delta` and `m_{N_g}`, and state the implied `n`. If the
   required `n` exceeds the available span, record that the design is underpowered
   **before** running it — underpowered is not evidence of no edge, and an
   underpowered run must not be started and then reinterpreted. Any *full-gate* power
   claim additionally requires the separately proved eligibility-floor bound `eta`
   (A16b); (P1)/(P2) alone bound only `{L > 0}`.
10. Record explicitly that the `sqrt(1/alpha)` dependence makes rare-`alpha`
    operation expensive, and what level is therefore being requested.

**Assumption-violation challenges**
11. Preregistered adversarial checks that *violate* the stated class — heavier tails
    than `M` permits, dependence longer than `m` permits, selection correlated with
    block variance — to characterise how the rule degrades. These are diagnostics of
    robustness under violation, not calibration evidence, and passing them is not
    admission; the guarantee is a theorem inside the class, so a violated-class
    failure is expected and must be reported as such.
12. Statement that passing any synthetic family remains evidence about that family,
    not production approval.

**Preservation**
13. Confirmation that the frozen thesis, untrimmed metric, all three completed
    experiments, both /tmp dossiers, and every frozen artifact/hash manifest remain
    unmodified, and that no gate, ledger, referee/handoff, reservation/settlement,
    `reason_passed` or admission state is touched.

---

## 13. Status and successor step

**Status.** Gate 2 remains blocked. Candidate `55243573515adc3b` still carries its
recorded handoff status `well_formed_untested` / `waiting_for_evidence`; this
document makes no ledger observation and does not change it. This design is a
*conditional* proposal: the coverage, type-I and power statements of Sections 5, 6
and 9 are proved, but every one of them is conditional on `K`, `M` and the covariance
envelope, and **none of those is certified**. With those constants absent, the
procedure's correct output today is `UNAVAILABLE` — which is the intended fail-closed
behaviour, not a defect.

Also note the power caveat: Sections 5, 6 and 9 prove type-I control and a bound for
the mathematical rejection region, but no full-gate power lower bound exists without
the separately proved eligibility-floor bound `eta` (A16b), which is not supplied.

What this design buys over the three failed variants: the guarantee is a
finite-sample theorem that requires no stationarity, no mixing rate, no resampling
distribution, no studentization and no independence within or between symbols. That
theorem is **valid only inside the supplied class** — implementations still need
verification, and misspecification of the class (heavier tails than `M` permits,
dependence longer than `m` permits, constants that are not `F`-valid) invalidates the
guarantee rather than degrading it gracefully. What it costs: Chebyshev widths with
`sqrt(1/alpha)` scaling, and a hard dependency on constants that must be proved
rather than measured.

**Successor step.** The useful outcome is **reliable conditional-prediction evidence
with feasible power on a feasible calendar** — not the implementation of this
particular conservative test. Accordingly, before further harness implementation:

1. **Choose a feasible evidence design** with justified quantitative tail and
   dependence controls, and a stated **minimum useful effect size and time horizon**.
2. **Compare** this theorem-based baseline against a redesigned prospective
   selection/evaluation separation. Note that fresh chronological data is **not**
   automatically independent of `F`; any such separation must be argued.
3. Where feasibility is binding on `M` or on the dependence lag `m`, that comparison
   comes first: do **not** demand deriving `K` before anything else if the `M` /
   dependence feasibility question is what actually decides viability. Deriving `K`
   from engine, universe and time-grid facts remains a well-defined source-reading
   exercise and is still worth doing once it is on the critical path.
4. If the required bounds cannot be justified, or the implied power is infeasible on
   the available span, the correct outputs are either a **redesign of evidence
   collection** or a recorded **"no justified inference"** — reported as the useful
   finding rather than patched with an estimate. Do not select a fourth bootstrap
   variant, do not run an experiment, and do not register a family error target.

Resolving A6 (the target-population ambiguity) is a user decision and should be
raised before any registration; see Section 14 for how it bears on continuing design
work.

**Preservation.** All prior experiments, reports, reviews, verifications, frozen
artifacts, hash manifests, the frozen thesis and the frozen protocol are preserved
intact. No trading, referee, handoff, reservation, settlement, `reason_passed` or
admission change is made or proposed for today. Existing human-approval
prerequisites remain in force.

---

## 14. Practical decision framing (user steering, recorded)

The end result that matters is **reliable conditional-prediction evidence with
feasible power on a feasible calendar**. Process and structure are instruments, not
objectives.

- **Open to revision, by separate documentation.** The workflow, the block structure,
  the evaluation sequencing, the eligibility floors and the accounting architecture
  are **design choices**, not fixed constraints. Any of them may be revised if the
  revision improves validity or useful power, provided the change is documented and
  registered separately. Preserving the current architecture is **not** an objective
  in itself and must not be elevated into one.
- **Untouched regardless.** Currently frozen artifacts and the recorded operational
  stop remain untouched by this framing. Revising a design choice on paper is not
  permission to alter frozen state.
- **A6 is alignment, not a gate on thinking.** As stated in Section 3, the
  expected-count-weighted target is a working interpretation pending alignment; it
  blocks registration and evidence access, not continued design work.
- **Success criterion.** Implementing *this* conservative test is not the goal. If a
  different design yields justified inference with better power at acceptable
  validity, it wins.
- **Order of work.** Before further harness implementation: pick a feasible evidence
  design with justified quantitative tail/dependence controls and a stated minimum
  useful effect and horizon; compare the theorem baseline here against a redesigned
  prospective selection/evaluation separation (remembering that fresh chronological
  data is not automatically independent). Do not force `K` to the front of the queue
  if `M` or dependence feasibility is the binding question.
- **Exit conditions.** If the bounds cannot be justified or the power is infeasible,
  recommend redesigning evidence collection, or report **no justified inference**. A
  fourth bootstrap tuning attempt is not an option.

**Scope of this steering.** It authorizes **no operational change, no metric change,
no evidence access and no experiment.** It is a statement about which directions are
open to design work, nothing more.

---

**Report path:** `docs/superpowers/reports/2026-09-16-gate2-assumptions-first-design.md`
