# M3.2 Scheme D validation — owner clarifications I1–I8 (addendum)

> **STATUS: CLARIFICATION ADDENDUM — NOT AUTHORIZATION TO RUN.** No RNG, seed,
> world, benchmark, historical search, Gate 2, referee, handoff or demo/live
> action is authorized by this document. It changes no numeric design value,
> scenario, truth row, threshold, gate, seed map or sample size.

- Date: 2026-09-21
- Amends nothing in: `docs/superpowers/specs/2026-09-19-m32-scheme-d-actual-statistic-validation-protocol.md`
  and `docs/superpowers/specs/2026-09-20-m32-scheme-d-actual-statistic-validation-protocol-pre-rng-correction.md`;
  it fixes wording those documents leave undefined, before any RNG is spent.
- Frozen truth package (`7279ce0`, SHA-256 `a100a19c0d789acf42ab42e53355b4eb4d3827ddfbca866fb6a9231e6e7013eb`) is untouched:
  refusal, sampling and accounting rules do not enter the population estimands.
- Applies to validation bundle revision 2 (supersedes bundle v1, SHA `0b42c6a38c3329eff6ec760176b682003ff993b0976d45588f72eebb4be78a3e`).

## 1. I4 — "degenerate" statistic (protocol-substantive clarification)

**Protocol text (§9.2):** "a zero or degenerate centered statistic" and "The
centered statistic is degenerate if it is constant across the group, as
established by a deterministic check."

**Owner clarification.** "Degenerate" means the hypothesis's **outcome column is
constant**: over the observed rows of the world, the column the hypothesis reads
(persistence values for persistence hypotheses; the 3-class label for
`case_kind`, transition and continuation hypotheses) takes a single value. A
permutation only rearranges those rows, so the statistic is then constant across
the permutation group. Such a hypothesis is refused (reason
`degenerate_constant_outcome`).

**An observed zero lift is valid evidence and MUST NOT be refused.** A
hypothesis whose observed statistic is exactly 0 (for example a categorical
macro-F1 lift of exactly 0 because the hit, non-hit and overall majorities
coincide) is tested: its exceedance count and p-value are computed by the normal
rule `p = (1 + X)/(B + 1)` with the tie tolerance, and it takes part in BH.

**Why (deterministic arithmetic, no simulation).** Under N1/N3 class
probabilities (.65, .25, .10), the probability that a matched set's plurality
class is class 0 is 0.916 at 12 matched rows, 0.969 at 20 and 0.997 at 40, so an
exactly zero lift is the typical outcome. Refusing observed zeros would refuse a
majority of (world, hypothesis) pairs, trip the §9.3 1% hypothesis-refusal
blocker in every N1/N3/P cell, and make the refusal depend on the observed
outcome. Under this clarification refusals depend only on memberships, masks and
the constancy of the outcome column.

**Consequences.** Hypothesis-refusal semantics only. Floors, the 1% blockers, BH
with m = 384, p-value construction, gates and power definitions are unchanged.

## 2. Other owner decisions recorded (no protocol text changes)

| # | Owner decision |
|---|---|
| I1 | Dependence group = block index; episode = unique row / episode identity. |
| I2 | Permutation-invariant support accounting (matched, episodes, dependence groups are invariant; observed support is bounded from below by the worst case over permutations within strata); observed support must suffice for the statistic to be defined. |
| I3 | Hypothesis-level refusal. A world with zero tested hypotheses is a refused world: excluded from Type-I and FDR, a failure for power, counted in the world-refusal blocker. |
| I5 | The sampler order is frozen through the hash-bound runner source. |
| I6 | Marginal-calibration worlds whose h(w) is refused are excluded from the empirical-CDF denominator. |
| I7 | BH compares exactly, in integer arithmetic (`q = 1/20`, reject at equality). |
| I8 | The reference (imported `_metric`) path is allowed only for the phase-90 benchmark; a full-run optimized implementation needs the §3 exact-equivalence proof and its own separate benchmark. |

## 3. Class-edge conformance correction

§5 defines missing-fraction classes `[0,.2), [.2,.4), [.4,.6), [.6,1]`. The
runner's earlier binary-float rule (`1.0 - mean`) assigned exactly 20% masked
(16 of 80) to class 0. Revision 2 classifies by integer counts:
`class = [5m >= T] + [5m >= 2T] + [5m >= 3T]` for `m` masked of `T` unique rows
(`T = 80`), i.e. the exact half-open edges. This conforms the implementation to
§5; it is not a design change.

## 4. Boundaries

Nothing here authorizes the phase-90 benchmark or any run. Authorization remains
an owner-created record bound to the revision-2 bundle SHA-256.
