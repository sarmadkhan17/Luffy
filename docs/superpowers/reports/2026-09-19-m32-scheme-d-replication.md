# M3.2 scheme D independent-seed replication — 2026-09-19

**Synthetic only.** This report records the single authorized replication of
scheme D's `coverage_class_confounding` size excursion. It authorizes no
historical M3.2 evaluation, Gate 2, admission, scheme freeze or real-data use.

## Provenance

| Item | Value |
|---|---|
| Frozen baseline commit | `12ffa3e9b8e28a816a53ad00c7b63d7e773793e8` |
| Prespec + runner committed before outcomes | `a947630ceabe5d1c9292c4345891791f00cf8faa` |
| Prespec | `scripts/m32_scheme_d_replication_20260919/replication_prespec.json` — `35786d1833fd0a5f67b1d3896ab9e5be71b66c0155da1169970d7ff3d199b747` |
| Runner | `scripts/m32_scheme_d_replication_20260919/run_replication.py` — `f843bd6e06a84cc0b51bc01e71026f3ae9a55c77f9a6e2aa9b234c5e2f8af190` |
| Output manifest | `scripts/m32_scheme_d_replication_20260919/output_manifest.json` |
| Frozen `sim.PS` canonical SHA256 | `ed40c92a7ad8b601594c7e970c4c5456fa898ca166cad1a1b0003d47039b5047` |
| Scenario canonical SHA256 | `be6ae07b012dab18653d075d81cbf73d0fdfa4b0bf12f3dfcfcb5979041b1190` |
| Started / finished (UTC) | `2026-09-19T19:16:21.403553Z` / `2026-09-19T19:17:10.943840Z` |
| Runtime | 49.423985719680786 s (49.67188024520874 s incl. verification) |
| Environment | Python 3.12.3 (`/home/sarmad/trader/venv/bin/python`), numpy 2.5.2, `Linux-7.0.0-31-generic-x86_64-with-glibc2.39`, x86_64, 2 CPUs, `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS`/`MKL_NUM_THREADS` unset, `sys.dont_write_bytecode=true` |

Neither the prespec nor the runner changed after `a947630` (working-tree hashes
match the prespec and results). No frozen comparison file was modified.

## Run

- Call: `sim.run_world(sc, w, 'D', 0.0, 1999, forced=False, tag=7)` for
  `w in range(0, 10000)`. Scenario `coverage_class_confounding` (code 8),
  effect 0.0, B = 1999, `forced=False`.
- Root seed **2026091901** (runtime override of `sim.SEED`; frozen prespec
  master seed 20260919 and `sim.PS` unchanged).
  World data: `SeedSequence([2026091901, 8, world_index])`; permutations:
  `SeedSequence([2026091901, 8, world_index, 3, 7])`.
- 10000 worlds fixed in advance; no sequential look, early stop, tuning or
  rerun. One attempt (`attempt_marker.json`, attempt 1).
- Only this one cell was run. No other scenario and no paired-control cell.
  Scheme D's quarter × regime × missing-fraction-class conditioning (edges
  0.2/0.4/0.6) was unchanged.

## Results (exactly from `results.json`)

| Quantity | Value |
|---|---|
| Worlds | 10000 |
| Tested | 9994 |
| Refused | 6 — `eight_label_floor_unmet` (world indices 736, 2517, 4829, 4867, 8258, 8259) |
| Rejections at α = 0.05 | 495 |
| Rate | 495/9994 = 0.04952971783069842 (**4.9529718%**) |
| Wilson 95% | [0.045446263520251634, 0.05395933947056872] (**4.5446264%–5.3959339%**) |
| Decision | `SIZE_CONTROL_NOT_REJECTED` (`size_control_rejected_wilson_lower_gt_05 = false`) |

The six refused worlds are excluded from the tested denominator and were
assigned no p-value.

**p CDF (descriptive only)**

| p ≤ | 0.01 | 0.05 | 0.1 | 0.5 | 0.9 |
|---|---|---|---|---|---|
| Fraction | 0.010706423854312587 | 0.04952971783069842 | 0.09895937562537523 | 0.4954972983790274 | 0.8959375625375225 |

**p quantiles (descriptive only)**

| q | 0.05 | 0.25 | 0.5 | 0.75 | 0.95 |
|---|---|---|---|---|---|
| p | 0.050825000000000016 | 0.249 | 0.5055 | 0.758 | 0.952 |

**Support (min / median / max)**

| Diagnostic | All worlds with availability | Tested worlds |
|---|---|---|
| Strata | 17 / 23 / 26 | — |
| Movable strata | 8 / 13 / 19 | — |
| Movable blocks | 30 / 38 / 48 | — |
| log10 G | 8.281267450269635 / 10.485387432925558 / 14.381637995387198 | — |
| p floor (`lower_bound_1_over_G`) | 4.153000702881757e-15 / 3.2704880535193986e-11 / 5.23278088563102e-09 | same |
| Distinct drawn stats | — | 1998 / 1999 / 1999 |
| Draws | — | 1999 / 1999 / 1999 |

Zero-orbit refusals 0; worlds with orbit smaller than draws 0; worlds with
floor exceeding α 0; successful draws per tested world 1999.

## Independent root audit

The root session independently audited `worlds.jsonl`: 10000 ordered, unique
world receipts; counts and p-CDF match `results.json`; total draws
19978006 (= 9994 × 1999); `SHA256SUMS` verified against the files.
Post-run integrity in `results.json`: every check true (`integrity_ok: true`)
— frozen files, wrapper and prespec unchanged; `sim.PS` equal to frozen bytes,
snapshot and canonical hash; seed override confirmed; no frozen function
replaced; no `__pycache__`; reference outputs unchanged.

## Output files

All six under
`docs/superpowers/artifacts/m32-scheme-d-replication/2026-09-19-seed-2026091901/`:

| File | SHA256 |
|---|---|
| `attempt_marker.json` | `2624db25b83863b35f3e6cca0f0af8ee940e3a536b659b6e710fc2bf4cc062df` |
| `run_start_receipt.json` | `a881e3ec02ec5265655ab72accadf993d2a436d0bc1605ae6d92d406aceebd20` |
| `worlds.jsonl` | `5d2d175364834637262c406303a08833ec8111ac9b33f404c8b484bec6d57675` |
| `progress.log` | `43344f3acefbc0e1ee3cf3303d4e6f1c1d521823ded693f54f6d175504cc6d92` |
| `results.json` | `2009ffe35d5b8e5b114cf39be222468bdfbdbb22b47246aff71ad8d7a16c97f4` |
| `SHA256SUMS` (covers the other five) | `423b455dda5a7bae62344429f2b105c76f1a9d290c49d19698c99a5cd1b33c3e` |

## Original run — separate, not pooled

The original conditioning-comparison run (seed 20260919, worlds 0..1999)
remains separate evidence: 2000 tested, 0 refused, **120/2000 = 6.0%**,
Wilson 95% [0.050411197114316506, 0.07127580449318606], size control
rejected under the same rule. It is not pooled with, adjusted by or erased by
this replication. Its immutable reference outputs, governed by
`scripts/m32_conditioning_comparison_20260919/reference_outputs_manifest.json`
(`12311ab247853d08fc51a57a87613b213a2fc2bf43a04054adbaeae6bb5653ad`), were
verified unchanged:

- `docs/superpowers/artifacts/m32-conditioning-comparison/2026-09-19/checks.json` — `3a05670915b8e3431d4ea3855b9726644099a79121f690c2d2199602c28f2515`
- `docs/superpowers/artifacts/m32-conditioning-comparison/2026-09-19/results.json` — `d2093bc8f83ed648247afeabcf397ab385ca8c7e7c4f6ed4179a97e61c33a67c`
- `docs/superpowers/artifacts/m32-conditioning-comparison/2026-09-19/run.log` — `588d55d8ca3fb78528d398102c5e8448d0c3648e24a9d2fcdaec9bfdb09f5e99`

## Decision

Under the fixed rule (size control rejected iff Wilson 95% lower > 0.05), the
replication lower bound is 0.045446 < 0.05: **size control not rejected**. The
earlier size rejection in this cell **did not replicate**. That is consistent
with Monte Carlo fluctuation in the original run; it is not proof of the cause
of the original excursion, and it is not a demonstration of equivalence to
nominal size.

**Scheme D status:** it may proceed only as a *conditional candidate* for
further synthetic validation. It remains **blocked** for freeze and for any
historical use because:

1. the statistic is a synthetic surrogate, not the actual M3.2 statistic;
2. invalid-scenario refusals depend on oracle metadata;
3. power and draw resolution under the actual correlated BH384 family remain
   unvalidated.

**Limitations of this evidence.**

- The six support refusals (`eight_label_floor_unmet`) are excluded from the
  tested denominator; no p-value was assigned to them.
- No invalid scenario was retested. The guards were preserved unchanged, but
  that is not new empirical validation of the guards.
- Only one cell was run; other scenarios and paired controls were not
  re-examined.

## Single next task (not executed)

Draft, **but do not run**, a prespecified synthetic validation protocol for
**unchanged** scheme D that uses the actual M3.2 statistic and the declared
correlated 384-hypothesis family. Before any outcome is seen, the protocol must
specify refusal requirements (including how non-oracle refusal is decided) and
draw-resolution requirements (p-floor relative to the BH384 thresholds and the
draw budget). This task has not been started.
