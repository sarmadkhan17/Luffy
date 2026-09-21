# M3.2 pre-RNG truth package (v4), September 21

Package: `scripts/m32_scheme_d_pre_rng_truth_20260921/package/` (55 cells x 384
rows = 21,120 rows). Generator `generate_truth_package.py`, independent verifier
`verify_truth_package.py` (stdlib only, imports neither the generator nor
trader), guard test `tests/test_m32_pre_rng_truth_package.py`. No RNG, no
simulation, no protocol change, no new categorical certification.

## What was merged

- 20 null cells (N0-N3 x C0-C4): true null by construction (protocol section 11.2).
- P1-P7 x C0-C4: categorical rows keyed by the frozen kernel identity
  (`sha256(spec)[:20]`, re-derived independently from scenario/correlation/index).
  Frozen evidence rows: base certificate 1784 (43 common-majority true-null
  kernels), completion 1280 (20 signed-lift kernels), P4/C3 4, P4/C4 4, P5/C4 256,
  P6/C3 256, P5/C3 16 (one kernel), P6/C4 48 (one kernel).
- Every source file is checked against both the package registry and its own
  family freeze manifest; primary and replay are both required to agree.

## Persistence proofs

`PERSISTENCE_GAUSSIAN_COUPLING_SIGN_V1` replaces the "monotone shift" slogan.
It is a full proof (regression on the membership latent, a shared-uniform
quantile coupling of member vs population, pathwise monotone count `S`,
strictness from full-support residuals, strictly increasing outcome CDF, unique
medians, division by `MAD+1 >= 1`). Its premises are checked row by row in exact
rational arithmetic from the factor form `sum w_k s_k s_k^T + d I` (positive
definite, unit variance): all target correlations `>= 0` (or all `<= 0` with a
non-target), strictness witness, `|rho| < 1`. It proves 2,475 rows in 24 cells
(2,219 positive, 256 negative: every opposite-sector row of P2/P4/P5/P6 x C4).
P7/C4, where the signs are opposed, is proved numerically by the frozen Arb
certificate (`03ba946`), 128 positive rows. No persistence row is refused.

## Remaining unresolved

448 categorical rows in 2 cells, 9 kernels with no frozen certificate: P5/C3
four kernels (`362941837322c8d33797` 8 rows, `5b0820e2459afe050862` 112,
`82fa6cbd43d44f3d5ad8` 112, `f94178b70f5311220985` 8) and P6/C4 five kernels
(`192a266e63028fd9405f` 8, `1c44894573b030f89efe` 56, `444db08366c304e754fc` 64,
`63a8f6ab39aa1458349a` 64, `7d91c0980805e807c160` 16). The earlier P5/C3 and
P6/C4 "full certificates" each covered one canary kernel, so categorical truth
was not complete. `freeze_possible` is `false`; P5/C3 and P6/C4 are both
power-acceptance cells, so the corrected protocol cannot run as frozen yet.

The P5/C3 canary is mapped to kernel `3b715db927f6e887cfda` structurally (a
focal linked target in a 2-state/4-linked cluster, `r = 10/11`, matching the
certifier's `d0**2*d1**4` / `r*p1*d0**2*d1**3` / `p**4, h*p**3, m*p**3`
constants); the verifier re-checks those strings. The previous handoff's
"categorical truth complete" statement was wrong for these two cells.

## Verification

Verifier PASS: 55 cells, 21,120 rows, 21 sources, 56 manifest entries;
regression against the superseded v3 tables: 9,216 resolved rows compared, 0
changed. Tamper tests (categorical sign flip, fake-resolving an uncertified
kernel, persistence sign flip, theorem-premise edit) are each rejected even with
the manifest rehashed. Generator output is byte-deterministic.

## Resolution (same day): the nine kernels are certified

The 448 unresolved rows are closed by the nine-kernel certificate in
`scripts/m32_p5c3_p6c4_remaining_20260921/` ([report](../../../scripts/m32_p5c3_p6c4_remaining_20260921/freeze_report.md),
manifest `freeze_manifest.json`): 9 attempted, 9 certified, 0 refused; every
kernel is a strict `2 / 0 / 0` positive non-null, largest primary lift radius
`9.9928e-13`, smallest zero separation `0.0903`, independent 384-bit replay
PASS. The package was regenerated (55 cells x 384 rows = 21,120; 0 unresolved,
0 uncertified kernels) and the independent verifier passes, including tamper
tests (sign flip, fake common-majority null, hidden un-resolve, source drift;
each rejected with the manifest rehashed where applicable) and 0 changes
against v3 over 9,216 resolved rows. `freeze_possible` is now `true`. No RNG,
search, Gate 2 or protocol change.
