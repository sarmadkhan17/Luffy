# P7/C4 Persistence Truth Certificate — Frozen Acceptance

Status: **ACCEPTED**

Freeze date: `2026-09-21`

Scope: the frozen P7/C4 *persistence* estimand only — the 24 persistence targets
(hypotheses 136–159: 8 in sector A, clusters 8–15; 16 in sector B, clusters
16–31), the four A/B × target/non-target conditional medians, the population
median, and the population MAD. No RNG, no simulation world, no categorical
rework, no protocol change, no other cell touched.

## Estimand (unchanged)

`signed effect = (median(hypothesis rows) − median(all rows)) / (MAD(all rows) + 1)`
(`m32_search._metric`, persistence branch). Row outcome = frozen N1 base noise
(t₃/√3 with a 10 % common `2×t₃/√3` jump) + `0.25·N1_null_MAD·K`, where `K` is the
number of the 24 targets containing the row. The base outcome CDF is the frozen
closed form `0.9·F_V + 0.1·F_(V+2U)`; it reproduces the frozen `N1_null_MAD`
`0.47472299235208826` to `3.4e-16` (independent check of the closed form against
the Gauss–Legendre derivation in `m32_scheme_d_validation.derive_n1_null_mad`).

## Reduction

Sector factors `U` (A) and `V` (B) are standard normal with correlation `−1/2`
(C4 loadings `(1/2, ±√3/2)`); membership is `√.6·F + √.4·e > 0.6744897501960817`
(exact binary64). Given `(U, V)` memberships are independent, so each count-mass
numerator is a finite sum of `E[f(U) g(V)]` with `f = pᵃqᵇ`. Mehler's expansion
turns each into `Σₙ ρⁿ αₙ(f) αₙ(g)`, `ρ = −1/2`, with orthonormal Hermite
coefficients from **1-D** Arb integrals. The tail after order `N` is bounded by
`|ρ|^(N+1)·√(E f² − Σαₙ²)·√(E g² − Σβₙ²)` (Parseval + Cauchy–Schwarz); the
Gaussian tail beyond `|z| > B` by Cramér's inequality. Every enclosure is
intersected with `[0, 1]`; normalization (`Σ mass ∋ 1`) and the population mean
count (`∋ 24·P(member)`) fail closed.

An earlier unfinished draft of this script integrated the correlated 2-D masses
directly with `rel_tol=arb(1)`, which lets Arb stop at 100 % relative error; its
checkpoint had enclosures up to `0.04` wide. It was discarded and never used.

## Result

| Class | Conditional median | Signed effect | Primary radius | Replay radius | Sign |
|---|---|---|---:|---:|---:|
| `A_target` | 0.80492643240203526951… | 0.07648003856891814848… | 4.660e-18 | 4.048e-21 | +1 |
| `A_non_target` | 0.75304279396213104173… | 0.04452845430408327967… | 7.058e-18 | 3.677e-21 | +1 |
| `B_target` | 1.21327089140272742755… | 0.32795146160628651135… | 6.809e-18 | 1.069e-20 | +1 |
| `B_non_target` | 1.15928163150786324442… | 0.29470316910411543693… | 4.659e-17 | 7.854e-21 | +1 |

- Population median: `0.68073657577114413163…` (radius 2.168e-18)
- Population MAD: `0.62382052826739141371…` (radius 1.388e-17)
- Minimum zero separation: `0.044528` (gate `1e-10`); maximum primary effect radius
  `4.659e-17` (gate `1e-12`).

All four classes are strict positive non-nulls (no cancellation between the
opposed-sector effects at any level near the gate).

## Runs

- Primary: `224` bits, Hermite order `48`, bound `16`, `54` exponent pairs, 37.1 s.
- Independent replay: `384` bits, order `60`, bound `20`, reversed traversal,
  205.6 s. All 19 interval-overlap and classification
  checks pass (`replay_result.json`); the replay's largest effect radius is
  `1.069e-20`.
- Non-certificate cross-check (deterministic double-precision trapezoid over the
  two sector factors, no RNG): all four effects agree with the certificate to
  `≤ 6.5e-15`.

## Frozen decision

P7/C4 persistence is **fully certified**. This closes the P7/C4 persistence row
family only. The consolidated 384-row truth tables and
`proof_certificate.json` (`freeze_possible: false`, 16 refused cells) are **not**
regenerated here, so the pre-RNG freeze is still not possible.
