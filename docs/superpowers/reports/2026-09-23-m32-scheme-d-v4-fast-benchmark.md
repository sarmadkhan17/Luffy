# M3.2 Scheme-D v4 fast benchmark

Scope: one owner-authorized, local-CPU, non-inferential benchmark run of the
frozen v4 Scheme-D runner. This benchmark used RNG only in the frozen phase-90
benchmark mode. It did **not** run validation, pilot, search, Gate 2, referee,
handoff, demo, live trading or cloud compute.

## Bindings

- frozen v4 bundle SHA-256:
  `a1e671f581d984521cab43199694f31bec41d0233c4232c1a4e7799114da3023`
- v4 shard layer SHA-256:
  `5e322153c62b103d00e9e898b30df4efc2031ea66c7144b79d5037b4d03c2170`
- benchmark authorization SHA-256:
  `f0c9cccf45aedbe893d17afd3b126abc9f8f0c32ed376b894ad9bf53274bdba4`
- benchmark receipt file SHA-256:
  `f70ace0a02ab6187647ecc33446238cf5ba9dbe97320194ea75f32876972f23e`
- benchmark receipt internal SHA-256:
  `c9d81319f6ba09cc05a0a8500088e3a1e837f1d72926b7b8ba59a62d366158eb`
- benchmark summary SHA-256:
  `8aede3c371c71ff15f34a3986aa131b5e9eb048d2a59d572ebdc0ea672571fa2`
- benchmark evidence commit:
  `137ca2f`

## Result

- runtime: 22.24969653703738 s
- throughput: 975.4548174287327 draws/s/core
- full-workload projection: 16406.782531828467 CPU-hours
- projected CPU-years: 1.871638436211324
- reference/FastMetric bit identity: PASS
- cloud used: false
- inferential validation run: false
- validation authorized: false

The projection remains too large for a practical full run on the current
4-vCPU VM. This result exists to bind runtime evidence for future authorization
and host qualification; it is not evidence that the full validation has run.

## Remaining blockers

1. Full v4 host/filesystem/performance qualification:
   multi-process flock/exclusivity, worker scaling and shared-mount capability.
2. A future owner decision on any v4 pilot authorization.
3. A later, separate owner decision on any full v4 validation authorization,
   bound to the exact v4 bundle, shard layer, shard size and benchmark evidence.

No v3 pilot or validation authorization is transferable to v4.
