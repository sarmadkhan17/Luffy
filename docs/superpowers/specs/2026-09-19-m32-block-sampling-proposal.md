# M3.2 future sampling and null proposal

Status: DESIGN PROPOSAL ONLY. Not frozen, scheduled, activated, or authorized to
run search. The historical protocol, artifact, pattern IDs, support floors and
search budget remain frozen and unchanged. This document is not a replacement
for the historical protocol and confers no trading or Gate 2 authority.

## Why the current design cannot be certified

`dataset._group` conservatively unions market-wide interval overlaps and sequence
parents. It is a valid dependency bookkeeping upper bound, not proof that separate
components are independent. Continuous rolling episodes can connect indefinitely.
The corrective audit now only merges existing groups; it never splits them.

`m32_search._null_p` rotates numeric labels or shuffles categorical labels separately
inside (cell, symbol, group). It neither permutes whole episode/sequence chains nor
uses one synchronized market-wide transformation. Two rows of one underlying case
can separate; BTC and alt shocks can be shifted differently. The full-sample null
also lacks the declared calendar conditioning. Merely adding observations/groups
does not repair exchangeability. Keep this runner descriptive and do not rerun it.

## Exact candidate sampling design for validation

This deliberately conservative design is a calibration target, not a claim that
28-day separation proves independence or that the duration is economically useful.

1. Freeze a new protocol and feature/classifier/input manifest at least 24 hours
   before T0. T0 is the first Monday 00:00 UTC satisfying that guard. Membership is
   the existing explicit 16-symbol universe; all selection dispositions remain
   visible. Historical candidates, vocabulary, bins, prediction mappings and
   canonical pattern IDs must be fixed before T0; no fitting on forward labels.
2. Global blocks are [T0+28*k days, T0+28*(k+1) days), for every symbol together.
   Days [0,17) are a source/warm-up buffer, [17,24) admit registration, [24,25)
   allow outcomes to mature, and [25,28) are embargo. Raw observation continues
   throughout; exclusion from a test is explicit, not deletion or a collector gap.
3. The feature manifest permits at most 100 closed 4h bars (400 hours), strictly
   available by registration. A regime receipt can use the existing classifier on
   exactly that bounded prefix, with the resulting distinct classifier/prefix
   version frozen for shadow evidence only. No historical reconstruction, ambient
   unversioned memory, unbounded EMA state, or current-store substitution qualifies.
   If any feature needs more history, this proposal fails; do not silently stretch
   or shorten its buffer. No trading classifier or trading behavior changes.
4. Admit only existing forecast/investigation horizons of at most 24 hours.
   All feature inputs must begin within the block, and all outcome event/availability
   clocks must precede day 25. Late/absent evidence remains unknown, with original
   registration denominators retained. Never select blocks for favorable coverage
   or outcomes. Publication delays do not extend a frozen cut.
5. Sequence parents and shared cases must lie in the same block and obey the
   existing known-before-registration and 24-hour sequence-gap conditions. Retain
   cross-boundary links and flag/merge the affected blocks; never sever a link to
   claim replication. Repeated versions/trades/cases deduplicate globally. Residual
   dependencies or missing boundary provenance prevent an independence certificate.
6. Outcomes are observation/path labels unless actual canonical accounting has
   been replayed and joined to an eligible PIT execution episode before the cut.
   No fabricated costs, zero-filled accounting, diagnostic trades, or forced trades.
   Selected/ignored/skipped/failure/unavailable cases remain separate.

## Exact null contract to implement and calibrate before any freeze

For the original association estimand, the candidate null transformation is a
permutation of WHOLE market-block outcome tensors, never rows or individual symbols.
Each tensor includes every symbol, episode horizon, linked sequence terminal,
availability/missingness status and selected/ignored/skip/failure disposition.
Move linked copies of a target together using a canonical underlying-outcome key.

Permute only among blocks with identical, predeclared eligible-unit/link topology,
symbol/horizon roster and required calendar/regime stratum. Re-key by that topology;
no nearest-neighbor matching, dropped cells, time interpolation, or independent
symbol offsets. No admissible non-identity transformation means UNTESTABLE, not a
fallback shuffle. Use the original fixed draw budget, seed discipline and full
attempt ledger; preserve BH reporting and all support floors. The two-sided
Monte Carlo tail uses (1+exceedances)/(1+successful draws), including failed-draw
accounting. A synchronized reversal is falsification, not a second selection test.

Required assumption: whole-block outcomes are exchangeable conditional on the
registered topology/strata under the null. Calendar separation alone does not prove
this; trending/nonstationary or autocorrelated block effects may invalidate it.
The sparse exact-stratum permutations may also make this proposal unusable. Retain
that failure rather than relaxing strata after observing results.

## Calibration and freeze gate

Before implementation of collection, use synthetic data only to measure test size
and power under common market factors, serial persistence, overlapping episodes,
unequal group sizes, missingness, publication lag, rare types and regime/calendar
imbalance. Include adversarial nonexchangeability cases that must refuse. Declare
the minimum worthwhile effect and error budget; calibrate the full fixed search
family, not an isolated unadjusted test. The three-group/eight-label floors remain
necessary but are not power guarantees. Group count is not effective sample size.

Freeze a finite block count and stopping rule only after this calibration meets the
unchanged statistical requirements and owner review accepts the assumptions and
collection cost. If it cannot, reject this proposal and design a separately reviewed
dependence-aware test; do not change the current grouping to increase power.

Single next task: implement and test a PURE SYNTHETIC block-null/coverage/power
calibration harness for this proposal. No real-data search, new collection window,
regime backfill, runtime flag change, risk/exit change, Gate 2 or live handoff.
