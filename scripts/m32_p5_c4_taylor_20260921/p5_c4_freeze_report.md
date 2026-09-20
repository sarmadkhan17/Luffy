# P5/C4 Reduced Taylor/Moment Certificate — Frozen Acceptance

Status: **ACCEPTED**

Freeze date: `2026-09-21`

This certificate covers only the six frozen P5/C4 kernels. It preserves the
existing one-dimensional analytic reduction for sector counts `((8,16),(0,0))`:
the empty opposite sector is integrated exactly, while the nonempty sector is
enclosed with Arb centered Taylor moments. A separately evaluated validated
one-dimensional Arb integral guards every primitive. No RNG, P6/C3 work,
protocol change, or broader kernel run occurred.

## Primary certificate

- Arb precision: `224` bits; Taylor order: `14`; axis: `80`
- Common population survival interval:
  `[0.69879171144864337434126012818311040398455212128606918524678295898060229384498933,
  0.69879171144867224661949067051174543719582253218828528444941586303333666884498933]`
- Population class: `0`, strictly above `20/31`

| Kernel | Frozen focal variant | Hit class | Miss class | Population class | Signed lift interval |
|---|---|---:|---:|---:|---|
| `2ca6beeb42d57d3f72d6` | state, empty sector, non-target | 0 | 0 | 0 | `[0,0]` |
| `6f3f08bc43774f2f2c2d` | state, nonempty sector, target | 2 | 0 | 0 | `[0.1860725434901878, 0.1860725434902944]` |
| `80c0bb2935ef7accbdc2` | linked, nonempty sector, target | 2 | 0 | 0 | `[0.1696464654746945, 0.1696464654747883]` |
| `927c037efde9d39f01ec` | linked, nonempty sector, non-target | 2 | 0 | 0 | `[0.1633170360869511, 0.1633170360870496]` |
| `cda53e68321634ca8982` | state, nonempty sector, non-target | 2 | 0 | 0 | `[0.1784213914212873, 0.1784213914214113]` |
| `dcc1c4cae778c6d4e914` | linked, empty sector, non-target | 0 | 0 | 0 | `[0,0]` |

The two all-class-0 variants are algebraic true nulls. The other four are
positive non-nulls. Their largest primary signed-lift radius is
`6.1972490288843685816988493542112337e-14`; their smallest primary zero
separation is `0.16331703608695111226917925094945325`.

## Independent higher-precision replay

The independent replay used `384`-bit Arb precision, Taylor order `16`, axis
`82`, validated-integral tolerance `1e-18`, and the non-heap validated
integration path. It reproduced all six classifications and winner triples.
Every hit, miss, population, and signed-lift interval overlaps its primary
counterpart. Its largest non-null lift radius is
`3.6950654165232159646323206736440170e-14`. Replay result: **PASS**.

## Runtime and decision

Primary runtime was `1.8370841529686004 s`; replay runtime was
`2.393144005036447 s`; total recorded process runtime was
`4.2302281580050474 s`.

All strict majority, radius, and non-null zero-exclusion requirements pass.
P5/C4 is fully certified. The only remaining unresolved categorical family is
P6/C3.

Single next task: independently certify P6/C3 only with its existing analytic
reduction and the unchanged frozen estimand.
