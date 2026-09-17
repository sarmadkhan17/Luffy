# Gate-2 independent review — 2026-09-16

The original unstudentized 30-day bootstrap remains rejected by its synthetic
calibration. No material implementation discrepancy was found that reverses
the reported false-pass inflation. This review completes the independent
assessment requested in the calibration handoff; it grants no gate approval.

## Reviewer and scope

A fresh independent review agent performed this assessment separately from
the primary implementing agent. This is not the previously pending Luna
review, and no work is attributed to that reviewer or to Claude.

The reviewer read CLAUDE.md, the original prediction-test design, the synthetic
calibration protocol, handoff and report, the synthetic harness and its tests,
and the existing synthetic calibration artifact files needed for verification.
No held-out data, database, configuration, current ledger, broad logs, network
or operational runtime state was read. No trading, runtime, referee/handoff,
ledger, reason_passed or admission change was made. Existing artifacts and
source files were preserved; this report is a new file.

Candidate `55243573515adc3b` remains, as recorded in the handoff,
`well_formed_untested` / `waiting_for_evidence`; this is not a fresh ledger
observation. Its recorded thesis SHA256 is
`25fe401f6be653b1dfb355f74091ff179237b743517f2d11cfc33144c1357b4d`.

## Verification and findings

- All 22 entries in the original synthetic calibration manifest matched their
  files. The original harness and pure FDR source matched the frozen plan's
  source hashes.
- Every aggregate null, power and stream row matched its corresponding cell
  file: 11 null fixtures, four power grids and three stream models. Recovery
  records missing-cell completion with unchanged frozen seeds and no overwrite.
- Independent execution of `tests/test_gate2_calibrate_synthetic.py` completed
  with **24 passed** (`-q -p no:cacheprovider`).
- The implementation preserves trade-weighted contrasts, shared calendar
  resampling, unknown/incomplete exclusions, eligibility floors, signed effects,
  conservative degenerate draws, and frozen within-family LORD++ levels.
- The irreversible-nonrejection shortcut retains the full planned denominator;
  it cannot create a rejection that full sampling would reject as a failure.
  The existing decision-equivalence tests passed.
- Exact binomial bounds and the distinction between inflated, bounded within
  a fixture and inconclusive outcomes support the report's conservative
  interpretation. Sparse selection cannot establish conditional validity.

One wording clarification belongs in subsequent documentation: `gate1_proxy`
returns the **maximum of three one-sided Gaussian tail probabilities**. Since
that tail decreases with the score, this is equivalently the tail probability
of the minimum score. It does not take a maximum Gaussian score and then its
tail. The proxy remains explicitly separate from production gate 1.

The measured failures under persistence, regime switching and the heavy-tail
arm floor preclude approval of the original statistic. High iid power and
favorable diagnostic cells cannot repair size inflation. No approved stream
family-error target or production selection guarantee has been established.

## Assessment of the proposed next experiment

A separately named, frozen paired calendar-cluster bootstrap-t experiment is
reasonable to test offline. For arm totals N1 and N0 and fitted means mu1 and
mu0, the proposed block influence is

```
u_k = (sum1_k - mu1*n1_k)/N1 - (sum0_k - mu0*n0_k)/N0
se_squared = K/(K-1) * sum_k(u_k**2)
```

The formula is appropriate for the trade-weighted ratio contrast under the
cluster approximation. Recompute the same standard error for every resampled
set of K blocks, including duplicated and empty calendar blocks, and compare
`(delta_b-delta_hat)/se_b` with `delta_hat/se_hat`. Use shared draws for both
predictions. Nonfinite or zero observed standard error must refuse; degenerate
resampled standard errors must count conservatively against rejection.

Before running the full revised calibration, require checks against literal
repeated-block calculations, a nonzero arm contrast with zero cluster standard
error, and shortcut/full-draw decision equivalence. Retain the original stress
fixtures, power grid, partial-null dependent streams, floors and fixed 30-day
rule. Freeze a new seed, source hashes, protocol and plan in new files before
sampling; preserve the original failed experiment unchanged.

Studentization can address scale heterogeneity but does not make persistent
calendar blocks independent, prove selected or sequential validity, or solve
sparse eligibility. Revised calibration may still fail. Any failure must be
reported without choosing a favorable block length or retuning that run.
Human review, an approved family-error target, production gate-1 integration
validation and all existing evidence-access/admission boundaries remain in
force. This assessment authorizes no runtime wiring or held-out evaluation.

## Revised implementation review before calibration

The fresh independent reviewer subsequently read the separate studentized
harness, its tests and protocol, and inspected the complete diff against the
preserved original harness before any revised calibration cells were run.
The paired influence formula, array axes, cluster count including empty and
duplicated blocks, centered bootstrap-t comparison and conservative invalid
standard errors match the proposed experiment. The original generators,
stress/power grids, stream behavior and floors remain unchanged. The new tests
include literal trade-based recomputation for repeated blocks, nonzero contrast
with zero cluster SE and translation/positive-scale invariance.

Two documentation clarifications were requested before source freezing:

- State that B=1431 applies to fixed-history null, power and diagnostic cells.
  Selected stream families compute B=ceil(1/min(family levels))-1 and defer
  before evaluation when the required B exceeds the 10000-draw cap.
- Describe the new selector docstring as the maximum of Gaussian tail
  probabilities, consistently with its unchanged implementation and protocol.

Subject to these clarifications and passing focused tests, there is no
blocking implementation finding for executing the prespecified offline
experiment. This is readiness to measure calibration, not an inference-validity
or gate approval. The primary agent additionally reported 28 revised-module
tests passing after adding invalid-resample/full-denominator and zero-SE
shortcut checks; that count is attributed to the primary execution, not to an
independent rerun. No revised statistical results were inspected in this
pre-run assessment.

## Frozen-run interim verification

After simulation began, the reviewer verified both requested wording fixes
in the frozen sources. All four plan source hashes matched: studentized script,
new tests, revised protocol and pure FDR source. The plan SHA256 was
`b48aea91c3a374ca6e13dce73f6ab207469fe754b94c824c3e0187b288f0c740`.

The reviewer checked the first four completed null cells (iid, shared factor,
long memory and regime switching): both histories have 14170 trials per
endpoint, counts and estimates agree, conjunction counts do not exceed either
marginal count, interval endpoints bracket estimates, and assessment labels
agree with their registered targets. At the second history, long-memory H1
and H2 lower bounds are .00318790 and .00107173, above their respective
.00266954 and .00069870 targets. Regime-switching conjunction has 180/14170
false passes (about 1.270%), with lower bound .00936217, above the .00069870
comparison target. These completed cells already show the revision cannot
clear calibration. Iid and shared-factor endpoints at that history remain
inconclusive, not successful calibration.

This is an interim check only: no final manifest existed at inspection, and
remaining cells still required completion. No full-run completion, gate
approval or statistical retuning is implied. Existing boundaries are unchanged.

## Final completed-run review

The independent reviewer verified the finished studentized run's **21 manifest
entries**, the absence of unlisted JSON files apart from the manifest itself,
and all **four frozen source hashes**. All 11 null, four power and three stream
aggregate records match their cell files; the aggregate diagnostic also matches
its file. Results and manifest checksums match the final calibration report.
The durable preservation audit's **45 file hashes** were independently checked
against existing files, including the original artifacts and /tmp dossiers.

The final report's second-history null table agrees with the artifacts,
including rounded joint bounds and assessments. Across both histories, the
reported **11/66 inflated comparisons** is correct. Reported grid MDEs, power
estimates/lower bounds, diagnostic joint counts and stream admission counts
and bounds agree with the recorded results. All-null streams have zero false
admissions but only nine selected streams, giving a conditional upper bound
about .667258. Partial-null H1-only/H2-only streams have 120/181 false admissions
respectively; these remain descriptive without an approved family target.

No material discrepancy was found in the completed report or handoff. The
prespecified run is complete and the studentized revision fails calibration;
the interim regime-switching and long-memory findings remain decisive. The
primary agent's source-pinned **124 tests before freezing** are recorded as
primary verification, not a new independent test execution. No rerun or
retuning was requested, and no calibration source or artifact was changed by
this review. All protected operational and evidence-access boundaries remain
in force; completed offline work does not authorize gate wiring or admission.
