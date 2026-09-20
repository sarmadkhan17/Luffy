# P5/C3 Population-Majority Certificate — Frozen Acceptance

Status: **ACCEPTED**

Classification: **population majority strictly above `20/31`**

Freeze date: `2026-09-20`

This report freezes the accepted P5/C3 population-only certificate. It records
the completed primary certificate and the already-completed independent
higher-precision replay. No new numerical run, algorithm change, subdivision
policy, RNG, or protocol change was used to create this freeze.

## Certified intervals

- Primary Arb precision: `224` bits
- Primary interval:
  `[0.645216427771829392546478549481683481928584334281467566037294269634151944648755335137471153,
  0.662187252542225967878166476727777231928584334281467566037294269634151944648755335137471153]`
- Independent replay Arb precision: `384` bits
- Replay interval:
  `[0.645216427771705111575396679089493803661445700301031304870729099062428241714014254309347057,
  0.662187252542101686907084606335587553661445700301031304870729099062428241714014254309347057]`
- Relationship: the replay interval overlaps the primary interval; neither
  interval contains the other. The replay lower endpoint is slightly below the
  primary lower endpoint, so a containment claim would be false.
- Exact replay lower-bound margin over `20/31`:
  `33674154736861970520308969936285284413641653766633488679552737571099058648709762444910674114519036247404270648723 / 610731096044114427790325121552226013978735958692214423353198547765808687458206764476921126950716192432502733849755648`
- Margin decimal:
  `0.0000551374491244664141063565088486423711231196558700145481484539011379191333690930190244763868`
- Independent replay result: **PASS**

## Deterministic checkpoint and refinement receipt

- Checkpoint records: `134` (`1` initial plus `133` refinements)
- Refinement actions: `110 split_z`, `23 split_g`
- Independent decisions matching persisted actions: `133/133`
- Initial state: `16` bands, `256` leaves, `1,280` evaluations
- Terminal state: `39` bands, `918` leaves, `49,435` evaluations
- Terminal checkpoint matches the primary report: `true`
- RNG used: `false`
- Protocol changed: `false`

## Verified SHA-256 identities

- Primary certifier: `76814e9bd8a57beb3360ece0f61a69a98ff9d3e34405eb00a499f1ddb61dbf05`
- Primary checkpoint: `3f5bdcd54590c14dae4c94670cbbb86973ba729b3b06697d9eebe34e52b59066`
- Primary report: `65d2d35f4ed8fdd4d73cf6047b91f6f42d85300b20b615387361033491bcfc36`
- Frozen categorical estimand spec: `8c1ed60f8b6e9f22608c6c491506e8b0c888dd296c02b62c266a1e40ace92755`

## Frozen decision

The independent `384`-bit replay passed, its enclosure overlaps consistently
with the primary `224`-bit enclosure, and its strict lower bound exceeds
`20/31` by the positive exact margin recorded above. P5/C3 is therefore
accepted as a population majority strictly above `20/31`.

This report and its companion manifest are immutable evidence. Do not edit
them. Any later evidence must be added as a separately identified artifact
under separately authorized protocol.
