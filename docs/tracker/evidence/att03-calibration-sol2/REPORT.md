# ATT-03 — existing Attention values calibration review

**BLOCKED_UNSUPPORTED_CALIBRATION_VALUES**, architecture/offline scope.
Every current component and its ancillary controls is inventoried in
[component-inventory.json](component-inventory.json). No production formula,
weight, threshold, configuration, clock, warm-up/null behavior or ranking rule
was changed. ATT-01 and DATA-04 remain CLOSED. ATT-03's condition is unchanged.

## Exact components, values and origins

Let N=20, S=5, r be log returns, sigma the sample standard deviation of the
first N returns, and R_S the sum of the last S returns. Each usable ranking
component contributes its absolute value to the existing **maximum**, not a
weighted sum. The shared admission cutoff is **2.0**. Missing values contribute
nothing. There are no fitted component weights or w1..w6 implementation.

| Component | Exact measure / current values | Origin and evidence classification |
|---|---|---|
| Volume anomaly | `(ln(1+v_anchor)-mean(prior N ln(1+v)))/sd(prior N ln(1+v))`; N=20 | Initial implementation 214947a and 2026-09-15 cognition specification. Normalization is mathematically defined; N20 and cutoff2 are inherited, explicitly uncalibrated defaults. |
| Volatility transition | `ln(sd(last S r)/sd(first N r))/sqrt(1/(2(S-1))+1/(2(N-1)))`; N20, S5 | 214947a. A large-sample scale approximation; windows and operational cutoff are not calibrated. |
| Relative return divergence | `(R_S-median_cohort(R_S))/(sigma*sqrt(S))`; N20, S5, min_cohort4 | 214947a. Estimated cohort median and denominator do not create a calibrated tail probability. |
| World volume anomaly | Accepted exact-cut `volume_anomaly` observation, `z_score` unit; standard producer uses the identical N20 volume transform | 640a5f2. A duplicate of volume anomaly, not an independent signal or additional weight. Exact duplicate parity is supported; calibration inherits the volume gap. Optional worker seam; currently disabled in config and not supplied to normal broad admission. |
| Positioning extreme | `max_series(abs((anchor-mean(previous 20))/sd(previous 20)))` over funding and ls_ratio; 21 required samples | 640a5f2, package-owned reference20. Conditional Gaussian predictive tails are supported below; natural-series calibration is absent. Optional worker input, not supplied to normal broad admission. |
| Correlation change | `abs((atanh(r_recent)-atanh(r_baseline))/sqrt(1/(120-3)+1/(30-3)))`; 120/30 nonoverlapping returns, 151 closes; one frozen leave-one-out basket with equal `1/n` peer weights, min_peers4 | 640a5f2, package-owned policy. Fisher scaling is conditionally supported under the IID controls; operational dependence/threshold calibration is not established. Normal broad component only when history is available. |
| Market breadth (supporting, not a score term) | `breadth_up=count(R_S>0)/cohort`; `market_z=median(R_S)/(median(sigma)*sqrt(S))`; broad iff abs(z)>=1.5 and max(up,1-up)>=0.75 | 214947a inherited, uncalibrated framing defaults. Four retained synthetic null/broad/isolated/contested fixtures classify as intended; they are not an independent calibration set. |
| Participation (supporting, not a score term) | `ln(current/previous)` within identical kind/source; supporting evidence iff abs(change)>=0.05; stale after2 bars | 214947a inherited supporting-evidence rule. Endpoint-availability mechanics tested; no labelled natural calibration. Normal capture supplies no participation points. |

Other values: divergence framing2, persistence outcome1, horizon5, cognition
K3 are inherited prototype settings, not additional ranking terms. Ratio
continuity900000ms, maximum age2700000ms, funding slack1800000ms follow recorder
cadence/package policy; no measured calibration of their error tolerance exists.
Admission max12, event2/exploration2, minimum0 are **owner-adopted engineering
budgets** in SDD8.4/config.yaml, not calibrated score thresholds. Cognition can
order governed HIGH/NORMAL/LOW priorities; normal admission explicitly ignores
learned priority for compute selection. Reasons, urgency and unmeasured cost
remain descriptive; no new numeric scoring system is introduced.

The older `agent_calibration` vote/confidence mechanism and unrelated Gate-2
research calibration cannot certify these Attention values. No such evidence
is transferred into this row.

## Frozen retained evidence

Read-only transactions froze **59 scans / 944 symbol rows** from three stores:

| Store | Scans | Eligible rows | Cutoff crossings | Scope limit |
|---|---:|---:|---:|---|
| data/attention.db | 1 | 1 | 1 | 15 other rows remain warm-up; exact acquired receipts retained |
| data/declared-population/attention.db | 54 | 864 | 570 | Declared collection cohort, repeated/overlapping scans; not independent trials |
| data/exit-ab-shadow/declared-population/attention.db | 4 | 64 | 34 | Older source lacks per-candle market receipts; historical source limits preserved |

All **59** reference/hash joins and common-field score/eligibility/ranking
replays agree. New additive history diagnostics are not falsely compared to
older output schemas. No historical timestamps are rewritten. Source archives,
record digests and per-scan metrics are retained; no provider or production
store write was used.

The stores have **zero independent anomaly labels**. Consequently natural
false-trigger and missed-trigger rates remain **null**, not inferred from
observed scores, selection, forward returns, P&L or the absence of a trade.
No positioning input or independent World label set was found in these scans.
An additional bounded, exploratory read of retained derivs.db at the frozen
operational scan cut used the existing verified receipt reader: BTC funding
was usable, while BTC ls_ratio and both ETH series were stale. Exact selected
revisions and null/stale statuses are retained in retained-positioning-results.json.
They were not injected into old scans or normal admission and carry no independent
event labels; one usable series is not a positioning calibration set. Older history/audit reports are retained, but their
/tmp artifact directories no longer exist. Those reports explicitly assumed
zero arrival lag/static membership and left the defaults uncalibrated; they
cannot silently become verified current point-in-time calibration data.

## Fixed offline experiments

[protocol.json](protocol.json) was written before measurement. No target error
rate or acceptance criterion was invented. Fixed seed20261006, eight-symbol
cohorts, 400 independent trials each for IID Gaussian and stationary AR(1)
phi0.6; 200 trials for each fixed planted perturbation. These are **model
conditional diagnostics**, not estimates of crypto-market error rates.
Synthetic Gaussian positioning levels test the normalization algebra; they
are not observed positive L/S ratios or a model validated against funding.

The initial all-optional run is preserved separately. A documented profile
addendum separates the **actual normal broad components** (volume, volatility,
divergence, plus correlation when available) from optional positioning/world
worker inputs. It changes no seed, value, effect size or acceptance target.

At the unchanged shared cutoff2:

| Component | Stationary IID cutoff-crossing rate | Stationary AR(1) rate |
|---|---:|---:|
| Volume | 7.19% | 8.41% |
| Volatility | 8.00% | 15.63% |
| Divergence | 6.00% | 25.16% |
| World volume duplicate | 7.19% | 8.41% |
| Positioning two-series maximum | 12.75% | 16.78% |
| Correlation change | 4.25% | 15.34% |

Normal broad composite crossings: **23.19%** per symbol in IID controls
(cohort-bootstrap95% interval21.78–24.66%) and **51.56%** under AR(1)
(49.88–53.34%). At least one symbol crossed in86.25% and99.75% of eight-member
cohorts respectively. All-optional diagnostic composites were32.75% and59.94%.
These are false *distribution-change* flags under the specified stationary
null labels. Random unusual observations are still possible under stationarity;
Attention's intended investigate-worthy event definition needs owner acceptance.
None of these numbers is claimed to violate an unspecified operational limit.

| Fixed positive control | Relevant-component miss rate | Composite miss rate (all optional) |
|---|---:|---:|
| Anchor log-volume +3 marginal sigma | 23.5% | 15.5% |
| Last-five volatility multiplied by3 | 23.0% | 8.5% |
| Last-five return mean +3 marginal sigma | 0/200 | 0/200 |
| Both positioning anchors +3 marginal sigma | 2.0% | 1.5% |
| Correlation0.8 baseline →0 recent | 0.5% | 0.5% |

Main-path composite misses for the volume/volatility/divergence/correlation
plants are18.5%/9.5%/0%/0.5%. Positioning is absent from that main-path input;
its76% main-profile non-trigger rate is not a failure of an active positioning
component. Top-three cognition misses and admission-policy selection over
extended inputs are separately recorded. Exploration admission is never
reported as anomaly detection; zero admission-policy misses in the small
cohort do not mean zero missed triggers. Zero of200 misses still has a
Wilson95% upper bound about1.88%, not proof of zero probability.

## What the measurements support

The volume/positioning denominator estimates its own reference mean/variance.
Under independent Gaussian levels, `(new-mean)/sd = sqrt(1+1/20)*t_19`.
At cutoff2 the conditional two-sided rate is **6.586%**, not the standard-normal
4.550%; the two-independent-series maximum is **12.738%**. Measured rates are
consistent with these conditional references. Correlation's4.25% IID rate is
consistent with the Gaussian/Fisher reference. **This supports the mathematics
under those assumptions; it does not calibrate real-series assumptions,
component combinations, multiple assets, thresholds or acceptable misses.**

The component tails differ and depend materially on serial dependence. A
shared numeric2 across these features cannot be called an operationally
calibrated significance level. There is no owner-approved false/missed-trigger
budget, real-event labelling definition or untouched labelled validation
period for these exact windows/cohort/profile. Determinism and adoption are
not substitutes for that missing evidence. No value was retuned.

## Exact remaining evidence and owner boundary

Unsupported for ATT-03 closure: shared2 and its max-across-components/cohort
interpretation; N20/S5, positioning reference20/max-of-two, correlation120/30
and peer floor4 as operational calibration choices; supporting broad1.5/.75,
divergence2, persistence1, participation.05 and freshness tolerances if their
calibration is claimed. No fitted component weights exist to certify.

Needed: an owner-approved definition of investigate-worthy positives and
nulls; tolerated false/missed triggers per symbol/cohort/time budget; the
applicable normal versus optional component profile, universe and event sizes;
labelled retained point-in-time windows with acquisition/revision/membership
ancestry; and an untouched confirmation plan using the **unchanged values**.
Natural dependence, varying cohort size, unavailable components, warm-up,
repeated cuts, rotation and capacity censoring must remain explicit.

**OWNER DECISION REQUIRED: YES** for that acceptance target/scope and validation
plan, not permission to silently retune. A decision to accept inherited policy
without calibration would require explicitly revising the original condition;
this package does not infer or make that revision. Ordinary evidence collection
can continue without changing values. No live collection is authorized here.

## Checks and scope

- 68 existing warm-up/null, cognition/replay and ATT-01 candidate tests passed.
- 11 evidence checks pass: frozen hashes/source bytes, all59 replay joins,
  fixed trial counts, unchanged initial metrics, six independent-process
  deterministic cases, World duplicate parity and nonzero uncertainty at zero
  observed misses. Details in verification.json.
- AST-only Graphify refresh; no semantic extraction or LLM labelling.
- Application source/config is byte-identical to the pinned start manifest.
- Only ATT-03 control/evidence is updated. Other requirement rows, closing
  conditions, runtime stops, dependencies and activation permissions stay intact.

The first evidence-verifier attempt compared Python tuple roles to JSON list roles
using object equality. It was corrected to the existing canonical-byte contract;
the initial failure log is retained. No result or production code changed.
Final control validation: 10 checks passed, including unchanged conditions,
only ATT-03 row edits, four-way parity, exact hashes and read-only Tracker availability.
