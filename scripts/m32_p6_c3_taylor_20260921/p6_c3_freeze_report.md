# P6/C3 Reduced Taylor/Moment Certificate — Frozen Acceptance

Status: **ACCEPTED**

Freeze date: `2026-09-21`

This certificate covers only the eight frozen P6/C3 kernels. It preserves the
existing global/cluster Gaussian reduction for the fixed multiset of eight
state singleton clusters, sixteen linked singleton clusters, and eight empty
clusters. Empty and singleton factors are collapsed analytically; the four
remaining focal joint-membership primitives use nested outward Arb centered
Taylor moments. The conservative existing C3 tail bound `66*Q(16)` is retained.
No RNG, broader kernel run, or protocol change occurred.

## Primary certificate

- Arb precision: `224` bits; Taylor order: `14`; initial axes: `80 x 80`
- Runtime: `126.36769586900482 s`
- Deterministic refinements: `19`; final bands/leaves/evaluations:
  `83 / 6661 / 88264`
- Population survival interval:
  `[0.632869405354628042393587, 0.632869405354628042399551]`
- Population class: `2`, strictly below `20/31`

| Kernel | Hit class | Miss class | Population class | Signed lift interval |
|---|---:|---:|---:|---|
| `0a1637679c1456f959ab` | 2 | 0 | 2 | `[0.0911259385067780, 0.0911259385086622]` |
| `3cc260c76449c068d392` | 2 | 0 | 2 | `[0.0906701562445056, 0.0906701562463682]` |
| `7dff14c356c8ce2d6260` | 2 | 0 | 2 | `[0.1025351108478722, 0.1025351108497835]` |
| `a9e4a3a0e3cbc9ae4b4d` | 2 | 0 | 2 | `[0.0860638572118316507202, 0.0860638572118316507937]` |
| `b10fa79c353f595cb688` | 2 | 0 | 2 | `[0.1031034585454157, 0.1031034585474002]` |
| `b3bc2b2a3e245b0236c4` | 2 | 0 | 2 | `[0.0969888499588430604688, 0.0969888499588430605318]` |
| `d4943315538638e73a34` | 2 | 0 | 2 | `[0.0977073583146851563472, 0.0977073583146851564234]` |
| `f851e8e3226de21f6f32` | 2 | 0 | 2 | `[0.1101369647827759775596, 0.1101369647827759776247]` |

All eight kernels are positive non-nulls. The largest primary signed-lift
radius is `9.9224594977910442888946462858257291e-13`; the smallest primary
zero separation is `0.086063857211831650720229539618520780`.

The primary checkpoint has `20` deterministic records: one initial state and
nineteen refinements.

## Independent higher-precision replay

The replay used `384`-bit Arb precision, Taylor order `16`, a distinct finer
`82 x 82` initial partition, and reversed inner traversal. It completed without
refinement in `108.18226297898218 s` with `82 / 6724 / 53792`
bands/leaves/evaluations. It reproduced all eight classifications and winner
triples. Every hit, miss, population, joint-hit, joint-miss, and signed-lift
interval overlaps its primary counterpart. Its largest signed-lift radius is
`1.9645255077413027841766457459726780e-14`; its smallest zero separation is
`0.086063857211831650756879561245002784`. Replay result: **PASS**.

Total recorded certificate runtime was `234.549958847987 s`.

## Frozen decision

All strict hit/miss/population classifications, signed-lift radius bounds, and
non-null zero-exclusion requirements pass in both runs. P6/C3 is fully
certified. Together with the previously frozen P4/C3-C4, P5/C3, P5/C4, and
P6/C4 certificates, categorical truth is now complete for the frozen scope.

Single next task: owner review of the complete frozen categorical truth before
authorizing any downstream M3.2 use; do not start search or Gate 2.
