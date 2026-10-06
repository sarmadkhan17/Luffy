# [SOL-1][QNT-03] dependence and contributor lineage

CLOSED at architecture/offline engineering scope. Engineering commit: a801cfd. Canonical control-plane reconciliation is pending. No tracker, STATE, NEXT or manifest file is included in the change. Dependencies QNT-02 and WRLD-07 were already CLOSED.

## Existing implementation mapped first

`null_baseline` already provides the independent diagnostic, matched-rank effective sample correction and continuous binomial-tail calculation. `referee.consistency` already estimates dependence from null profit factors at matched rotations; `gate1` already gates on corrected consistency on A/B and common rotation on B. `portfolio_null.common_rotation` already shifts every contributor by one offset, preserves costs/funding and shared alignment, scores one account at its concurrency limit and reports the finite-draw empirical p-value. These controls were reused.

WRLD-07 supplies immutable bounded relationship measurements, exact evidence hashes, versioned WorldHistory and exact-cut reconstruction. No relationship estimator or replacement history store was introduced. Kernel admission/rejection events already retain the complete returned evidence dict (`kernel.py` admission and install paths); the referee's final record drops verbose symbol summaries but now retains contributor receipts.

## Actual gaps and reproduced baseline

The pinned 45e825d Analyst admitted fifteen shared high percentile votes with independent p approximately 3e-15, without any dependence or common-control evidence. Its admission summary discarded the symbol percentile map. Baseline probe output is retained in baseline.json.

The same baseline referee returned rho=0.722 from only 21 measurable pairs out of 28, silently discarding the seven unmeasured pairs. It also returned rho=0.7167 on mismatched symbol calendars: equal array offsets were being treated as common calendar offsets. Fixed regressions refuse both cases. Exact copies of one outcome input could also contribute repeated votes; tests now pin contributor retention with one unique evidence unit.

## Change and proof

Admission uses the maximum of dependence-corrected consistency and common-rotation p; both must exist and clear the existing configured threshold. The raw independent p is labelled diagnostic and cannot admit a strategy. Missing dependence/common controls, unaligned or irregular calendars, insufficient common draws and repeated contributor symbols are refused as untestable.

The joint timing receipt retains every contributor and fingerprints its frame columns/dtypes/content, directional signals, funding and scoring boundary. Exact alias copies contribute once. Named BTC, reference-market, derivative and universe sources are fingerprinted without turning repeated upstream sources into additional observations. The hypothesis, exit semantics, risk/cost settings, shared timeframe, account/concurrency parameters, seeds, draw counts, null statistic and preserved/changed structures accompany the result and receipt hash. Cached trade tables cannot override the frozen inputs in this path.

Effective n is estimated from the *joint null statistics*, as in the existing referee, rather than an assumed return-correlation coefficient. A shared-factor synthetic test has eight distinct outcome inputs, measured null rho above 0.5, effective n below three and corrected p no smaller than the independent diagnostic. Eight exact alias copies retain eight contributor records but contribute at most one scored unit and cannot produce a consistency pass. Duplicate symbols and incomplete pairwise estimates cannot create confidence.

WRLD-07 is consumed at exact scored historical cuts. Receipts retain each model ID, relationship version/value, measurement scope, current eligibility, evidence references and exact Observation records. Only current measured rolling-correlation groups enter the measured dependency context. Changed signs/versions are read at their own cuts; expired records cease to qualify; absent cuts never fall back to a later relationship. The common control includes all contributors, which covers the measured groups and unknown dependencies as well. Return correlation is context, **not calibrated correlation of percentile votes**, so replacing null-rank rho with WRLD-07's numeric return correlation would be unjustified. No permanent correlation is assumed, and historical measurements do not grant independent votes. Independent symbol shuffles are never a substitute for the common-dependence control in this timing hypothesis.

A producer integration test runs the actual Analyst joint-evidence method with retained synthetic frames, supplies the same world/universe context, preserves signed funding, uses one scoring boundary and produces the common control. Exact-input disk reload plus a new Python interpreter reproduces the complete numerical result, dependency treatment, contributors and receipt hash. Contributor order changes preserve the receipt; changing a signal changes its evidence ID and receipt.

## Validation and limits

After RESUME all pytest executions used `/home/sarmad/.local/bin/luffy-pytest`, whose unique basetemp and TMPDIR reside on `/mnt/luffy-data/test-tmp/sarmad`. `df -h /` was run before and after each substantial run. Root free space remained 8.2G (83% used).

- Focused QNT-03/QNT-02/WRLD-07, null, referee and evaluation-context run: **128 passed in 91.73s**.
- Broader Analyst/advisory run: **28 passed, 5 failed in 104.20s**.
- The exact five failures against the pinned 45e825d Analyst: **5 failed in 4.82s**, with the same missing `_Journal.query` retirement fixture errors and legacy stored regime-weight expectations. These unrelated paths were left unchanged. The baseline plugin loads the pinned original Analyst before test collection; reproduction setup is in validation.json.

Pre-pause raw pytest checks are not substituted for the mandatory-wrapper results. The pre-pause graph update was stopped on RESUME to honor the manifest restriction; pre-existing shared graph changes were not committed or reverted. No provider calls, trading action, production process restart, threshold relaxation or research feature enablement was performed. A new interpreter in a synthetic test is the restart proof, not a claim of production runtime validation. This is not crypto-market calibration or evidence of a profitable edge. Unsupported/insufficient joint evidence can reduce admissions by returning untestable; this is intentional.

QNT-08 is closer, not yet eligible: QNT-06 is CLOSED; QNT-03 now has engineering closure evidence; QNT-05 remains unmet in the canonical tracker and depends on QNT-04. No other lane was advanced. Canonical QNT-03/QNT-08 reconciliation remains pending.
