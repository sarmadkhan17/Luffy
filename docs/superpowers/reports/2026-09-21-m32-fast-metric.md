# M3.2 optimized exact-equivalent metric, September 21

Module `trader/cognition/m32_fast_metric.py` (`FastMetric`), suite and benchmark in
`scripts/m32_fast_metric_20260921/`, smoke test `tests/test_m32_fast_metric_equivalence.py`.
The production `_metric` stays the reference; the estimand and statistic are unchanged. No protocol
world, protocol seed, validation run, search or Gate 2. Randomized fixtures use fixture-only seed
314159265 (not the protocol master seed, never combined with the protocol seed map).

## Approach

Permutation-invariant quantities are computed once per world (class counts and class list, overall
median and MAD, baseline scores, value ranking). Hit-row class counts come from pair-block tensors
`P[b, b', h]`, so a draw is a 48-row gather and sum instead of a 3840 x 384 pass. `Counter.most_common(1)`
first-occurrence tie-breaks are reproduced from first-occurrence tensors, only for hypotheses that tie.
The persistence hit median is an order statistic found through a pair-block rank-chunk histogram plus an
in-chunk count (numpy `cumsum` was the bottleneck). `sum(scores)` is CPython 3.12's Neumaier compensated
sum replicated column-wise (`sum([0.1,0.2,0.3]) == 0.6`, naive addition gives 0.6000000000000001). Outside
the proven domain (class codes outside 0..9; both +0.0 and -0.0 among valid persistence values) it
falls back to the reference, so equivalence is total by construction.

## Equivalence (bit level: None <-> untestable, otherwise identical float64 bit patterns)

7,592,064 comparisons over 22,314 statistic vectors and 4,529 fixtures: **0 mismatches**. Sections:
2,006,000 compensated-sum comparisons against builtin `sum`; exhaustive tiny geometry (1,458 outcome/mask
fixtures x all 64 memberships x 3 labels x all 6 block permutations); 16 hand-built adversarial fixtures
x 4 chunk sizes x all 24 block permutations (ties at every level, absent and sparse classes, single class,
all/most unobserved, constant/tied/negative/huge/subnormal persistence, NaN/inf observed persistence,
deviation overflow); fallback domains; 3,000 fixed-seed random fixtures (random geometry, class alphabets,
masks, memberships, chunk sizes); 4 full-geometry fixtures (N0-N3-style arrays) against the harness
pipeline. Coverage: 1.06M hit-majority ties, 1.05M non-hit ties, 3,884 overall ties, 795k zero lifts,
874k even-count medians, 467,069 untestable (None) cases, 110 fallback vectors.

## Benchmark (fixed synthetic full-geometry fixture, 384 hypotheses, single core)

| | Reference | Optimized |
|---|---|---|
| Seconds per 384-hypothesis vector | 7.62 | 0.00091 (8,356x) |
| End-to-end draw (map + statistic + exceedance) | 7.62 | 0.00098 (7,794x) |
| Peak RSS | (harness 62 MiB) | 86.9 MiB; tensors 34 MB; tracemalloc peak 40 MB |

Per-world encode about 0.09 s amortized. Projection for 75,000 worlds x 767,999+1 vectors:
reference 1.22e8 CPU-hours; optimized **15,636 CPU-hours (1.78 CPU-years)**, excluding per-world
generation, refusal evaluation and receipt writing (small) and parallel overhead. The benchmark fixture
had 0 mismatches against the reference.

## Practicality

About 163 days of wall time on this 4-core host, so not practical locally; embarrassingly parallel by
world, roughly 2.5 days on 256 vCPUs (estimate; cloud cost is my assumption, on the order of $500-1,000 at
$0.03-0.06 per vCPU-hour). Not integrated into the runner: that changes the bundle, so it needs a
revision and a new owner authorization for any benchmark.
