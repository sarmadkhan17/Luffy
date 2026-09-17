# Gate-2 stationary-bootstrap independent review — 2026-09-16

## Design assessment before implementation

A fresh independent reviewer assessed the next offline revision after the
studentized run failed calibration. This report is new; prior reviews,
protocols, source files and artifact directories remain unchanged. No work is
attributed to the previously pending Luna reviewer or to Claude.

A stationary bootstrap of the ordered, paired 30-day cluster summaries with
Bartlett HAC studentization is a defensible next synthetic experiment because
it explicitly represents dependence across calendar clusters. It is not an
approved gate or a guarantee of valid inference. Its design is informed by
earlier synthetic failures and must be frozen separately with a fresh seed.

## Recommended charged statistic

Retain the untrimmed trade-weighted contrast and paired ratio influence from
the previous revision. For K calendar blocks, including empty blocks, write
u_k for that fitted influence. Fix L=4 original 30-day blocks and use

```
Q = sum_k(u_k**2)
    + 2*sum_{h=1}^{3} (1-h/4)*sum_{k=h}^{K-1} u_k*u_{k-h}
SE_squared = K/(K-1) * Q
```

Unadjusted lag-product sums preserve the positive-semidefinite Bartlett
quadratic form. Do not multiply each lag term by K/(K-h). Refuse nonfinite or
negative variance and zero/tiny SE conservatively; do not disguise a material
negative variance by silently clamping it. The numerical convention must be
frozen explicitly.

Start each resampled sequence with a uniform index among K blocks. For each
subsequent position, restart at a new uniform index with probability 1/4;
otherwise continue to (previous_index+1) modulo K. Generate K positions. Use
one index sequence for both predictions and preserve ordering when recomputing
means, influences and HAC SE. Compare the centered bootstrap-t draw with the
observed t statistic, retaining conservative invalid-draw counting, the full-B
denominator, sign check, eligibility floors and irreversible-failure shortcut.
Circular continuation keeps every original block's marginal selection weight
equal; it also introduces a synthetic endpoint seam, which is a limitation.

The primary agent proposed retaining the 60/90-day diagnostic comparisons with
the same physical expected run length of 120 days: L=2 and L=4/3 respectively,
using restart probability 1/L and Bartlett weights 1-h/L for integer h<L.
This is coherent as a separately specified diagnostic convention. These
comparisons cannot replace the charged 30-day rule or justify selecting a
favorable design after seeing outcomes.

## Prespecified challenges and limitations

Retain all eleven original generators exactly. Add regime mean dwell times
240 and 480 days, with independent label-process coefficients sqrt(.995) and
the fourth root of .995 respectively. Dwell times are stochastic geometric
means, not deterministic durations. Add a 48-block version of the 480-day
regime challenge to help distinguish short-horizon limitations. Freeze these
fixtures, their names and budgets before simulation.

Expected run length is not an independent-block count. At K=12 or 24 and
L=4, only about three or six expected runs fit in the span; tail inference
can remain poor. Bartlett truncation after lag three, geometric resampling
breaks, the circular seam, heavy tails, long memory, sparse eligibility and
selection still threaten validity. New persistence challenges may reject this
revision. Source LORD++ arithmetic remains separate from any dependency
control theorem, and stream results have no approved family-error target.

## Implementation checks requested

Before calibration, check the vectorized HAC against literal scalar quadratic
forms for observed and repeated ordered blocks; check the stationary sampler
under forced continuation, wraparound and restart; and compare full bootstrap
decisions with independent literal recomputation. Include lag-zero/L=1
reduction to the previous cluster SE, ordinary-scale translation/positive-scale
invariance, empty/unknown blocks, floors, zero/nonfinite SE, conservative
invalid-draw handling and shortcut/full-draw equivalence. Verify the generalized
Bartlett convention for the 60/90-day diagnostics as well.

This is a completed design assessment only. Implementation review and a full
frozen calibration remain outstanding at this writing. No runtime wiring,
held-out outcomes, database, configuration, ledger, broad logs, network or
operational state access is part of this review. No trading, referee/handoff,
reservation/settlement, reason_passed or admission change is authorized. All
existing boundaries remain in force.

## Implementation review before calibration

The independent reviewer read the new stationary script, tests and protocol,
and inspected its complete implementation diff against the preserved
studentized script before any stationary calibration cells ran. No blocking
implementation discrepancy was found.

The sampler uses uniform starts, independent geometric restart flags and
circular continuation, retaining one shared H1/H2 sequence. HAC array axes,
lag limits (including noninteger L=4/3), Bartlett weights, repeated ordered
blocks and per-resample ratio means/influences match the reviewed design.
Invalid variances refuse without clipping. The original generator mechanisms
remain unchanged; new 240/480-day regimes and the 48-block override match the
prespecified challenges.

The reviewer inspected literal scalar reference calculations and reconstructed
sampling paths, zero-SE/invalid-draw/shortcut checks, forced continuation and
restart tests, dense Bartlett-form checks, L=1 reduction and exact original
generator preservation checks. The primary agent reported **36 stationary
module tests passed**; this count is primary verification, not an independent
rerun. A minor clarification was requested before freezing: write diagnostic
L values as `4, 2, and 4/3` instead of the ambiguous `4/2/4/3` list.

Subject to that wording clarification, the implementation is ready for its
prespecified offline calibration. No favorable statistical outcome is assumed.
The full fresh run, source/manifest verification and final result review remain
outstanding; no gate wiring, evidence access or admission is authorized.

## Interim completed-cell verification

The independent reviewer verified all four frozen source hashes and the plan
SHA256 `80c06bd0a3220f355e8f231ae4862b49767a9040fed0505394e3402198d0f471`.
The diagnostic L-list clarification is in the frozen protocol. The primary
agent reports the combined **160 focused tests** passed before source freezing;
this updates the earlier primary stationary-module count without claiming an
independent rerun.

The first four completed null cells were checked at both histories for trial
counts, event/estimate agreement, interval ordering, conjunction count bounds
and target-based classification. Second-history H1/H2/joint counts are:

| Fixture | H1 | H2 | Joint |
|---|---:|---:|---:|
| iid | 117 | 65 | 10 |
| shared_factor | 136 | 56 | 24 |
| long_memory | 187 | 81 | 38 |
| regime_switching | 271 | 123 | 77 |

Each denominator is 14170. Iid H1/H2 lower bounds are .00562045 and .00269973,
above their respective targets .00266954 and .00069870. Both long-memory and
regime-switching conjunctions are inflated as well. Iid and shared-factor
conjunction results are inconclusive, not calibrated passes.

No implementation discrepancy was identified against the frozen sampler/HAC
formula and the reviewed literal reference checks. This is not a proof that
software is error-free. The observed iid inflation means unmodelled persistent
dependence alone cannot explain this experiment's failure. Small K, variability
of the HAC denominator and finite-sample resampling/studentization behavior are
plausible mechanisms, not explanations established by the measured results.
No causal diagnosis or numerical remedy is claimed.

The failure is already decisive under the protocol, but the full prespecified
run must finish. No final manifest existed during this interim inspection.
No extra calibration, source modification, retuning or operational action was
performed by this review; all prior artifacts remain preserved.

## Review of the assumptions-first design note

The independent reviewer read
`2026-09-16-gate2-inference-requirements.md` while the frozen run continued.
No material unjustified mathematical claim was found. The rare-contamination
argument applies to unrestricted distribution classes with finite but
unbounded variance: a null distribution can be arbitrarily close in any fixed
finite sample to a positive-contrast distribution. Existence of moments alone
therefore cannot supply a useful uniformly valid test over that whole class.
This does not rule out useful inference under narrower, justified models.

The maximum of valid constituent p-values correctly tests the conjunction
alternative against the union null, without constituent independence. The
fixed-spending union bound is also correct under its stated premise that each
actually evaluated family has valid null size for its selection/evaluation
protocol. It supplies no validity for adaptive selection by itself and is not
claimed to be the current LORD++ accounting rule.

Quantitative tail/dependence bounds are a sufficient design route; parametric
or randomization restrictions can supply different routes with their own
assumptions/nulls. The note appropriately distinguishes these possibilities
from retaining the current weak mean-contrast contract. It neither assigns a
family-error target nor approves runtime integration. Offline proposal of
assumptions does not itself need new permission; their use for protected
production evidence or admission retains the existing explicit prerequisites.
No frozen protocol, artifact or source was changed by this review.

## Final independent verification

The full stationary/HAC experiment is complete. The independent reviewer
verified all **24 manifest entries**, exact JSON-file coverage, all **four
frozen source hashes**, and aggregate equality with all 14 null, four power,
three stream and one diagnostic cell files. Reported results/manifest checksums
match the files. All **75 preservation-audit hashes** match the original files,
including both preceding experiments, their reports and the /tmp dossiers.

The completed report's null table, rounded bounds/classifications, power grid
MDEs and power estimates/lower bounds, diagnostic counts, and stream table
agree with the artifacts. **74 of 84** fixed-history marginal/joint comparisons
are inflated. Iid marginal lower bounds remain .00562045 and .00269973 at the
second history, above their registered levels. The 48-block/480-day challenge
has **128 joint false passes**, lower bound .00626182 above .00069870. Partial-
null streams have **413 and 516** false admissions; the all-null stream has
zero but only **three selected streams**, conditional upper bound .963160.
Those stream measurements remain descriptive and do not close selection or
family-error blockers.

No material discrepancy was found in the final report or handoff. The
inference-requirements note now correctly describes the completed experiment;
its earlier mathematical review stands. The primary **160 pre-freeze tests**
remain primary-attributed verification, and unchanged tests were not rerun.
The experiment rejects this procedure, including its iid marginal behavior;
it does not identify a unique cause or validate a further numerical correction.

Only this new review report was appended during final review. Calibration
sources, plans, results, manifests and previous artifacts were not edited.
Gate 2 remains blocked, the candidate's recorded status is unchanged, and all
operational, evidence-access and admission boundaries remain in force.
