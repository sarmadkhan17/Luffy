# M3.2 Scheme-D v4 host qualification

Scope: full RNG-free host/filesystem/performance qualification of the current
single LUFFY VM against the v4 shard layer and the measured v4 benchmark
evidence. This is qualification evidence only. It does **not** authorize a
pilot or validation run.

## Bindings

- frozen v4 bundle SHA-256:
  `a1e671f581d984521cab43199694f31bec41d0233c4232c1a4e7799114da3023`
- v4 shard layer SHA-256:
  `5e322153c62b103d00e9e898b30df4efc2031ea66c7144b79d5037b4d03c2170`
- measured benchmark receipt SHA-256:
  `f70ace0a02ab6187647ecc33446238cf5ba9dbe97320194ea75f32876972f23e`
- host qualifier SHA-256:
  `77208d0be4f820d8514eb2af4de8dca4fd2047236bc8f71edcdac6077bdfae1f`
- qualification evidence commit:
  `42386606c14bdc7a2b6351dcbf78512163621608`

## Result

Single-host receipt verdict: **PASS**.

All exercised checks passed:

- bindings
- environment gate and environment-lock hash
- CPU features
- NumPy/OpenBLAS identity
- filesystem policy
- hardlink/EEXIST semantics
- local flock exclusivity
- renameat2 no-replace semantics
- file and directory fsync
- atomic publish
- deterministic fixture canary
- numeric canary
- FastMetric bit identity
- FastMetric throughput
- worker scaling

Cross-host flock was **NOT_EXERCISED** because only one host is available.

The qualified target filesystem is local `ext4` on `/dev/sda2`.
Qualified worker count is **4**.

The host receipt attests:

- RNG constructed: false
- benchmark run: false
- authorization read: false
- validation worlds: 0

Receipt internal SHA-256:
`b0d5a1be8bed679e5911daa67ee773a78ddd7c5016898cb2e70f4cbafee0b666`

Receipt file SHA-256:
`451c1c5ec33ce5a1cc03008344a8cbe99e7644c676a6d8d7d135ca625b9998ae`

Combined report internal SHA-256:
`f03a28773a4b1a8fdd67c7a1820321152e50cc6685f57dd4c6c693a594b64a85`

Combined report file SHA-256:
`b54a14104c8850d68991578610a29b13e8b29e22a40f1f30b24efb5986b5858d`

## Capacity / authorization interpretation

The host itself is qualified for single-host execution mechanics, but the
combined report deliberately sets `execution_hosts_ready_for_authorization=false`.
Reasons:

1. only one host is evidenced, so there is no cross-host flock or cross-host
   numeric/canary comparison;
2. projected full-run capacity on the 4 qualified workers is about
   **190.13 days**.

Therefore this evidence does **not** support authorizing the full 75,000-world
validation on the current machine.

A bounded v4 pilot may be considered separately by the owner, using a distinct
authorization bound to the exact v4 bundle, shard layer, shard-world count and
measured benchmark. Such a pilot would not make the full validation authorized.

No v3 pilot or validation authorization is transferable to v4.
