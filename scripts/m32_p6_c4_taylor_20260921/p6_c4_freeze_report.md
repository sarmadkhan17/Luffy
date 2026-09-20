# P6/C4 Reduced Taylor/Moment Certificate — Frozen Acceptance

Status: **ACCEPTED**

Freeze date: `2026-09-21`

This certificate preserves the existing analytic bivariate Gaussian reduction
`f = -x/2 + sqrt(3)*y/2` and the frozen P6/C4 specification. It tightens only
the reduced PGF integrals and their frozen categorical macro-F1 propagation by
using the successful P5/C3 higher-order centered-moment pattern. It uses Arb
outward bounds, no RNG, no protocol change, and no remaining-kernel run.

## Primary certificate

- Arb precision: `224` bits; Taylor order: `14`; initial partition: `80 x 80`
- Hit survival interval:
  `[0.4658928691088690821680228480727858908494454226456582546234130859375,
  0.4658928691137570493556040347737923212889654678292572498321533203125]`
- Hit majority: **strictly below `20/31`**, hence class `2`
- Miss survival interval:
  `[0.69647766120334243149922665884477002151697888621129095554351806640625,
  0.6964776612051367765028890739753553162927346420474350452423095703125]`
- Miss majority: **strictly above `20/31`**, hence class `0`
- Population survival interval:
  `[0.6503607027853291822530603447495161001939848827737105704492606374104477432311711815159603971413324907316114749126926262942895206578703073425802744,
  0.6503607027859794221442544822719560221885174795010780609792746754963852432311711815159603971413324907316114749126926262942895206578703073425802744]`
- Population majority: **strictly above `20/31`**, hence class `0`
- Signed macro-F1 lift interval:
  `[0.1253409335085910427939121877053315253410422277132359643777211507161520153435844790356428862254359127325222792746312629850040883984315293022334927,
  0.1253409335104991076661930299943522223079147200526980062325795491536520153435844790356428862254359127325222792746312629850040883984315293022334927]`
- Signed-lift radius: `9.5403243614042114451034848343624617e-13`
- Zero separation: `0.12534093350859104279391218770533153`
- Runtime: `63.20319119800115` seconds
- Deterministic refinements: `1`; final bands/leaves/evaluations:
  `81 / 6480 / 13282`

## Independent higher-precision replay

- Arb precision: `384` bits; Taylor order: `14`; distinct initial partition:
  `82 x 82`
- Signed macro-F1 lift interval:
  `[0.1253409335088220984204978691693710413090911970357410609722137451171875,
  0.1253409335102680436178410982965558684298912339727394282817840576171875]`
- Signed-lift radius: `7.2297259867161456359241356040001847e-13`
- Zero separation: `0.12534093350882209842049786916937104`
- Runtime: `71.2943937079981` seconds
- Deterministic refinements: `0`; final bands/leaves/evaluations:
  `82 / 6724 / 13448`
- Hit, miss, population, and signed-lift intervals all overlap their primary
  counterparts.
- Replay result: **PASS**

## Frozen decision

Both runs certify hit class `2`, miss class `0`, and population class `0` with
strict interval separation. Both independently satisfy signed-lift radius
`<= 1e-12` and non-null zero exclusion `>= 1e-10`. P6/C4 is fully certified.

This report and its companion manifest are immutable evidence. Later evidence
must be added as a separately identified artifact; do not edit this freeze.
