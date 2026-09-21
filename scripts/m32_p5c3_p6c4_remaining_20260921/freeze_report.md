# Remaining P5/C3 and P6/C4 categorical kernels — Frozen Acceptance

Status: **ACCEPTED** (9 attempted, 9 certified, 0 refused). Freeze date: `2026-09-21`.

Scope: exactly the nine categorical kernels the pre-RNG truth package left
unresolved — P5/C3 `362941837322c8d33797`, `5b0820e2459afe050862`,
`82fa6cbd43d44f3d5ad8`, `f94178b70f5311220985` (240 rows) and P6/C4
`192a266e63028fd9405f`, `1c44894573b030f89efe`, `444db08366c304e754fc`,
`63a8f6ab39aa1458349a`, `7d91c0980805e807c160` (208 rows). 448 rows total.

Method: the accepted P5/C3 global/cluster and P6/C4 two-factor Gaussian
reductions with the outward Taylor/centered-moment enclosure, unchanged; only
the per-kernel kernel functions differ, derived from each frozen spec (spec
hash re-checked against the kernel id). The estimand is the frozen
probability-mass macro-F1 lift (general hit/miss/population class triple,
identical to the accepted P5/C4 certifier). Strict class = interval fully above
(class 0) or below (class 2) `20/31`. No RNG, no simulation, no search, no
Gate 2, no protocol change, no other cell or kernel.

Validation of the generalisation: the same code, run on the two already-frozen
canaries (P5/C3 `3b715db927f6e887cfda`, P6/C4 `0493407cf230203cfe7b`),
reproduces their frozen hit, miss, population and lift enclosures (every
interval overlaps the frozen certificate). See `validation/`.

## Results (primary 224-bit, order 14, axis 80; replay 384-bit, order 16, axis 82, reversed inner traversal)

All nine kernels resolve hit / miss / population to class `2 / 0 / 0`
(hit majority strictly below, miss and population strictly above `20/31`) and
are positive non-nulls.

| Kernel | Cell | Rows | Primary signed lift (radius) | Replay radius |
|---|---|---:|---|---:|
| `362941837322c8d33797` | P5/C3 | 8 | 0.13070426009904045 (9.94e-13) | 3.42e-13 |
| `5b0820e2459afe050862` | P5/C3 | 112 | 0.10132596035578945 (9.95e-13) | 7.59e-14 |
| `82fa6cbd43d44f3d5ad8` | P5/C3 | 112 | 0.09034576637371869 (9.88e-13) | 7.40e-14 |
| `f94178b70f5311220985` | P5/C3 | 8 | 0.13771419461417584 (9.99e-13) | 2.46e-13 |
| `192a266e63028fd9405f` | P6/C4 | 8 | 0.11309549413846897 (8.16e-13) | 4.61e-14 |
| `1c44894573b030f89efe` | P6/C4 | 56 | 0.10422205506128344 (9.81e-13) | 6.24e-14 |
| `444db08366c304e754fc` | P6/C4 | 64 | 0.09438198548465452 (8.90e-13) | 6.18e-14 |
| `63a8f6ab39aa1458349a` | P6/C4 | 64 | 0.13749129836791515 (9.89e-13) | 2.97e-14 |
| `7d91c0980805e807c160` | P6/C4 | 16 | 0.13228041541851119 (9.95e-13) | 2.49e-14 |

Largest primary radius `9.9928e-13` (<= `1e-12`); smallest primary zero
separation `0.09034576637273` (>= `1e-10`). Full intervals are in
`primary.json` / `replay.json`.

## Independent replay

`merge_and_check.py` (exact dyadic-rational arithmetic) reports **PASS** for all
nine: ids match specs, both certified, classification and winner triples equal,
hit/miss/population/joint/lift intervals overlap primary vs replay, distinct
precision/axis/order/traversal, radii <= 1e-12, non-null zero exclusion >= 1e-10,
and a float64 Gauss-Hermite estimate (sanity only, not evidence) falls inside
each certified interval.

`primary.json`, `replay.json`, `replay_result.json` and `certify_remaining.py`
are hashed in `freeze_manifest.json`; per-kernel outputs, checkpoints and logs
are under `primary/`, `replay/`, `logs/`. Runtime is a safety cap only:
every kernel stopped by deterministic acceptance (`stopped_by: accepted`).
