# Exit A/B shadow: virtual path updater

Research only. No live exit, risk, order, spec, or config was changed. The
updater reads the declared receipt store read-only and writes only
`data/exit-ab-shadow/ab-shadow.db`.

## What was missing

`record` wrote an opening stub per path
(`{"entry_price":…,"state":"open","virtual_only":true}`) and nothing advanced
it, so the frozen paired comparison had zero observations. `scripts/exit_ab_shadow.py`
now carries an `update` subcommand that replays both frozen geometries over
exact closed declared 4h bars.

## Geometry

Both paths share the protocol's initial risk (2 x ATR(14) of the declared entry
bar, measured from the entry close) and the +1R arm. Bar order mirrors
`vector_backtest._trade` exactly — extend the favorable extreme from the bar,
ratchet the stop, then test the cross on that same bar — so the control is the
live geometry rather than a re-derivation.

- **A (`A_control_4x_atr`)**: after +1R, stop = extreme − 4 x ATR of the current
  bar; ratchet only.
- **B (`B_25pct_giveback`)**: after +1R, stop = `entry + 0.75 * (peak - entry)`;
  ratchet only. One expression covers both sides because `(peak - entry)` already
  carries the sign.

Replay always runs from entry over the declared series, so the updater is
deterministic and idempotent: a second run on the same declared scan advances
nothing.

## Safeguards

- Only bars in the latest complete declared scan are used; a sequence break,
  an entry bar absent from that scan, or ATR(14) history short of coverage is
  a recorded gap and a refusal, never a bridged or zero-filled bar.
- Gaps are deduplicated on (reason, detail), so repeated updates do not inflate
  the gap count.
- An already-closed path is frozen. If replay disagrees with a recorded exit,
  the recorded exit stands and a `closed_path_replay_mismatch` gap is written.
- The `opportunities` table is never written by `update`.

## Result of the first update

Declared scan `declared_752236e6932148f19a4b50c090986946`
(as-of 1789873378562; 26 bars/symbol, newest 4h open 1789848000000).
4 opportunities, 8 paths advanced, 0 gaps.

| opportunity | path | state | bars | peak R | locked R | current R | armed |
|---|---|---|---|---|---|---|---|
| AVAX/USDT BUY 1789819200000 | A | open | 2 | 1.3315 | −1.0000 | 1.1785 | yes |
| AVAX/USDT BUY 1789819200000 | B | **closed** | 2 | 1.3315 | 0.9986 | 1.1785 | yes |
| AVAX/USDT BUY 1789833600000 | A | open | 1 | 0.9441 | −1.0000 | 0.8025 | no |
| AVAX/USDT BUY 1789833600000 | B | open | 1 | 0.9441 | −1.0000 | 0.8025 | no |
| AVAX/USDT BUY 1789848000000 | A | open | 0 | 0.0000 | −1.0000 | 0.0000 | no |
| AVAX/USDT BUY 1789848000000 | B | open | 0 | 0.0000 | −1.0000 | 0.0000 | no |
| SUI/USDT BUY 1789833600000 | A | open | 1 | 0.0993 | −1.0000 | −0.3160 | no |
| SUI/USDT BUY 1789833600000 | B | open | 1 | 0.0993 | −1.0000 | −0.3160 | no |

A exits: 0. B exits: 1.

The single B exit (AVAX/USDT BUY 1789819200000) armed and crossed on the same
bar 1789848000000: the bar ran to 10.176 (+1.33R), the 75% threshold ratcheted
to 9.99325, and the bar's low of 9.565 crossed it. Virtual exit +0.9986R.
A's 4 ATR trail on that bar computed 8.85143, below the initial stop 8.896, so
A's ratchet held at −1.0R and A remains open.

## Evidence gaps

1. **Gross only.** `costs_applied: false`. The frozen entry binding fixes entry
   at the unslipped signal-bar close, so every R above is gross of the declared
   taker fee (0.0004/side), slippage (0.015 ATR) and funding. Net economics is a
   later stage and must not be read off these numbers.
2. **Intrabar order is assumed, not observed.** The one B exit armed and crossed
   within a single 4h bar. OHLC does not order the high and the low, so that
   exit rests on the engine's pessimism convention. A and B share the
   convention, so the pairing is fair, but the B exit R is convention-dependent.
3. **The one closed path is unpaired.** B closed while A stayed open on the same
   opportunity, so there is not yet a single completed A/B pair.
4. **Sample is nil.** 4 opportunities, all BUY, 3 of 4 on AVAX/USDT, 0–2
   post-entry bars each, against a protocol proving count of 30. Nothing about
   either geometry is inferable yet.
5. **Portfolio protocol is not modeled.** Sizing, concurrency, heat caps, the
   derisk ladder and the daily breaker are declared in the protocol but the
   updater produces per-opportunity virtual geometry only.

## Files

- `scripts/exit_ab_shadow.py` — `update` subcommand and its helpers.
- `tests/test_exit_ab_shadow_paths.py` — 17 focused tests.
