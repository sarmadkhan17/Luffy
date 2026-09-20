# P5/C3 Hit/Miss Majority and Macro-F1 Certificate — Frozen Acceptance

Status: **ACCEPTED**

Freeze date: `2026-09-21`

The fixed P5/C3 population model was not rebuilt or changed. This certificate
conditions the focal linked-pattern membership as hit or miss, reuses the
accepted nested Gaussian rectangle Taylor/moment enclosure, and propagates the
strict majority decisions through the frozen categorical probability-mass
macro-F1 estimand. It uses Arb outward bounds, no RNG, and no 27-kernel run.

## Primary certificate

- Arb precision: `224` bits; Taylor order: `14`; initial partition: `80 x 80`
- Hit survival interval:
  `[0.4724273731768301238967237842825142024594242684543132781982421875,
  0.472427373181546576832935853407224868760749814100563526153564453125]`
- Hit majority: **strictly below `20/31`**, hence class `2`
- Miss survival interval:
  `[0.69844799989785392115132479050332303671666522859595715999603271484375,
  0.69844799989961473729428264971275108763393291155807673931121826171875]`
- Miss majority: **strictly above `20/31`**, hence class `0`
- Signed macro-F1 lift interval:
  `[0.12423300332997044049725490506147496105882055417168885469436645507811572698462328144653567066184613090121658108805310552249386740235270604665,
  0.12423300333194475372047057160768135375406018283683806657791137695311572698462328144653567066184613090121658108805310552249386740235270604665]`
- Signed-lift radius: `9.8715661160783327310319634761981433e-13`
- Zero separation: `0.12423300332997044049725490506147496`
- Deterministic refinements: `96`; final bands/leaves/evaluations:
  `97 / 7876 / 60638`

## Independent higher-precision replay

- Arb precision: `384` bits; Taylor order: `14`; distinct initial partition:
  `82 x 82`
- Hit survival interval:
  `[0.4724273731768785230679505611484092497676101629622280597686767578125,
  0.4724273731814979447073197908746333695262364926747977733612060546875]`
- Miss survival interval:
  `[0.698447999897828462110728063756692751695709375781007111072540283203125,
  0.698447999899640321648321725049590735778792804921977221965789794921875]`
- Signed macro-F1 lift interval:
  `[0.12423300332996815957587666394886423878081889900689323743184407552083333333333333333333333333333333333333333333333333756323639552608203879004,
  0.12423300333194710857653133539065433884237184732531507809956868489583333333333333333333333333333333333333333333333333756323639552608203879004]`
- Signed-lift radius: `9.8947450032733572089505003077647416e-13`
- Zero separation: `0.12423300332996815957587666394886424`
- Deterministic refinements: `57`; final bands/leaves/evaluations:
  `90 / 7441 / 42254`
- Replay relationship: hit, miss, population, and lift intervals all overlap
  their primary counterparts.
- Replay result: **PASS**

## Frozen decision

Both runs put the hit interval strictly below and the miss interval strictly
above `20/31`. Both independently satisfy signed-lift radius `<= 1e-12` and
non-null zero exclusion `>= 1e-10`. P5/C3 is therefore fully certified for its
population, hit, miss, and frozen categorical macro-F1 estimands.

This report and its companion manifest are immutable evidence. Later evidence
must be added as a separately identified artifact; do not edit this freeze.
