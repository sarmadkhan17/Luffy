# M3.2 Scheme-D v4 shard integration

Scope: deterministic integration of the frozen v4 validation bundle with a
v4-bound shard layer. This report covers integration and qualification
evidence only. It is **not** validation evidence and grants **no**
execution authorization.

Prior report: [v4 validation bundle](2026-09-22-m32-validation-bundle-v4.md).

## What changed

New, v4-only tooling (the v3 layer, v1–v3 bundles and the truth package are
untouched):

- `scripts/m32_scheme_d_sharding_v4_20260923/shard_layer.py`: v4 sibling of
  the v3 shard layer. Storage, exclusivity, resume, audit and merge mechanics
  are copied from v3. Two changes only:
  - Hash-binds the frozen v4 bundle manifest, runner and acceptance module and
    refuses mismatches before importing either module.
  - Evaluator and `expected_tuples` dispatch generation/injection on
    `cell["scenario"] is not None`, matching the frozen v4 `run_cell`. The v3
    layer's `kind == "power"` dispatch would silently skip injection for the
    v4 `perturbed_null` P1/P3 cells. This closes the dispatch blocker recorded
    in the v4 bundle report.
- Import isolation so the same-named v3/v4 `scheme_d_runner`/`acceptance`
  modules cannot collide in `sys.modules`, in either load order.
- `rng_guard.py`, `shard_fixture.py`: RNG-constructor guard and RNG-free
  arithmetic fixture.
- `scripts/m32_scheme_d_qualification_v4_20260923/qualify_v4.py` and
  `QUALIFICATION_RECEIPT.json`.
- `tests/test_m32_shard_layer_v4.py`.
- `scripts/m32_scheme_d_sharding_v4_20260923/LAYER_MANIFEST.json`
  (this task): deterministic integration manifest. The shard layer docstring
  already referred to this file for `production_gate`.

## Evidence

| Item | Result |
|---|---|
| Focused tests `tests/test_m32_shard_layer_v4.py` | 23 passed |
| Qualification `qualify_v4.py run` | PASS (4/4 checks: own hash bindings, v3 untouched vs freeze commit, v4 static equivalence gate with 7,592,064 comparisons and 0 mismatches, 3-shard fixture canary) |
| RNG constructor attempts | 0 |
| Random variates requested | 0 |
| Validation worlds generated | 0 |
| Authorization minted | false |
| Production / full validation authorized | false |

Qualification scope is `single_host_in_process_deterministic_binding_only`.
It is **not** the full host, filesystem or performance qualification that the
v3 qualifier ran.

## Bindings

| Artifact | SHA-256 |
|---|---|
| v4 `BUNDLE_MANIFEST.json` (bundle SHA) | `a1e671f581d984521cab43199694f31bec41d0233c4232c1a4e7799114da3023` |
| v4 `runner/scheme_d_runner.py` | `7e4d693bdaffc7cf35370fe3425858ea195fc70eb0c6425c4461836008410548` |
| v4 `runner/acceptance.py` | `a575c07fcef86948c50467dc1f32a7bdba1ea32d78a3897b0e2eb1d4569d7ec1` |
| `shard_layer.py` | `5e322153c62b103d00e9e898b30df4efc2031ea66c7144b79d5037b4d03c2170` |
| `rng_guard.py` | `14e7d40f1e0b2d1ff95951e7e40cc520f40c0e9ea1e89903daf7d33f75aa6aa2` |
| `shard_fixture.py` | `104a516bb26644adbbea18a996ced23230d7ab326eb0b7c57a60637442f76433` |
| `qualify_v4.py` | `17f07ee4a42efcb19fdb8ed325c87e6b22f424869a568532c2def5dd77746e8f` |
| `QUALIFICATION_RECEIPT.json` (file) | `779708c1a914859e088426cbfd0f9b3035d7a2418076c0f4fc8eddc3adf35cba` |
| Qualification `receipt_sha256` (self-hash, verified) | `5055e9d8998cf6a524663982133b8fb668acfbc05e15fb2bc8d7526bd031cb41` |
| `tests/test_m32_shard_layer_v4.py` | `4b2a3eedbbd2f7a18dc56f38f7c552136a038e131248894ffe98b79be0402b63` |
| Frozen truth package (unchanged) | `a100a19c0d789acf42ab42e53355b4eb4d3827ddfbca866fb6a9231e6e7013eb` |
| `LAYER_MANIFEST.json` embedded `manifest_sha256` | `c8f3b43fe14cecd61d8381cfb4967a79b9c1c66485341430ebce159447bcdabc` |

Commits: v4 freeze `079e8b5c590429aeb0ca5c7b0b261328291c6b2e` (tag
`m32-scheme-d-v4-freeze`, tag object `aa721cdb…`); shard integration
`2324c4186a062eba2d9c1b70fd5c1a2d9d3adf8a`; qualification
`4d3a0ae195cf7ae1eca9b536b5cb4c309c9afd0a`.

The manifest was generated twice with identical output. Every file hash
matches the pinned constants in `shard_layer.py` and the qualification receipt.

## Owner decision on preparation exception

The owner reviewed and **accepted** the documented v4 preparation exception on
2026-09-23. The historical preparation audit remains unchanged and continues
to record that one delegated draft test constructed 40
Generator/PCG64/SeedSequence objects, with zero random variates drawn and zero
validation worlds generated. This acceptance does **not** authorize a benchmark,
pilot, validation run, cloud execution, or any other RNG-using execution.

## Remaining blockers (nothing below is authorized)

1. **Measured v4 benchmark/runtime evidence before any full-validation
   authorization.** `production_context` requires `runtime_estimate_cpu_hours`
   and a 64-hex `fast_benchmark_receipt_sha256`. The existing phase-90
   benchmark, scaling and projection artifacts are bound to v3 and cannot be
   reused. A v4 benchmark draws RNG, so it needs separate owner authorization.
2. **Full host/filesystem/performance qualification for v4.** This covers
   multi-process flock exclusivity, worker scaling and shared-mount
   capability. It was out of scope for `qualify_v4.py`.
3. **v4-bound owner validation authorization.** It must bind this
   `bundle_sha256`, `shard_layer_sha256` and `shard_worlds`. A v3
   pilot/validation authorization is refused by construction.
4. Any v4 pilot needs its own authorization. The v3 pilot is not transferable.

No search, Gate 2, referee/handoff, risk, demo or live-system change was made.
`research.referee=false` and `research.handoff=false` are unaffected.
