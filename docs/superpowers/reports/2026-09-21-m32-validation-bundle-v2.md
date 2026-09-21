# M3.2 Scheme D validation bundle, revision 2 (RNG-free), September 21

Bundle: `scripts/m32_scheme_d_validation_bundle_v2_20260921/` (manifest
`BUNDLE_MANIFEST.json`). **Bundle-v2 SHA-256:
`397961d944f98f730eb723d537594d56428cf72db349449ad848678ad85f0c4e`.**
Supersedes v1 (`scripts/m32_scheme_d_validation_bundle_20260921/`, SHA
`0b42c6a3…78a3e`), which is left byte-identical and must not be authorized.
Consumes the frozen truth package (`7279ce0`, `a100a19c…13eb`) unchanged. No RNG,
seeds, benchmark worlds, search or Gate 2. Owner clarification addendum:
`docs/superpowers/specs/2026-09-21-m32-scheme-d-owner-clarifications-i1-i8.md`
(I4 clarification plus the other recorded owner decisions; hash-bound in the manifest).

## Changes versus v1 (only these three, plus the recorded decisions)

1. **Class edges (protocol section 5):** `missing_class(masked, total)` uses integer
   counts, `[5m>=T]+[5m>=2T]+[5m>=3T]`; exactly 16/80 masked is now class 1 (the
   float rule gave class 0). Exact-edge checks: masked 16, 32, 48 -> classes 1, 2, 3.
2. **I3:** a world with zero tested hypotheses returns `world_refused =
   ["no_tested_hypotheses"]` (hypothesis refusals still recorded); it is excluded from
   Type-I/FDR, a failure for power, and counted in the 1% world-refusal blocker.
3. **I6:** `calibration_pass` drops worlds whose h(w) was refused from the ECDF
   denominator; result gains `calibration_worlds`.
4. Config: schema v2, `owner_decisions` (I1-I8 + class edge, each with
   `changed_vs_v1`) replaces `interpretations_flagged_for_owner_review`;
   `class_rule`, the I3 world-refusal reason and the I6 note are recorded.
   I4 (zero lift never refused; only a constant outcome column is degenerate),
   I1, I2, I5, I7 and I8 are recorded as decided with unchanged code.

## Verification

- Preflight 27/27 PASS (v1's 22 plus class-edge integer rule over all 81 masked
  counts, `generate_world` class at exact lag boundaries, zero-tested-world refusal,
  observed-zero-lift tested not refused, calibration exclusion), 0 generator
  constructions.
- Independent verifier PASS (re-derives tables/PSD/null MAD/config, checks git
  ancestry and tracking, v1 immutability and supersession, addendum binding,
  isolation, RNG scan; probes class edges, I3 and I6 in a clean subprocess against
  its own exact Clopper-Pearson; reruns preflight to an identical report).
- 9 v2 tamper cases rejected (float class rule, off-by-one edge, I3 removed, I6
  reverted, config drift x3, addendum drift, v1 modification).

## Limits unchanged from v1

The reference `_metric` path is only for the phase-90 benchmark (about 7.2 s per
384-hypothesis vector; a full run is infeasible without an exact-equivalence-proven
optimized statistic and its own benchmark). RNG paths are exercised only via a
deterministic pattern stub. The authorization record format is unchanged and must
carry the v2 bundle SHA.
