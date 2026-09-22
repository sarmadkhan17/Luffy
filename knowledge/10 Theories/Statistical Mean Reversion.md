---
type: theory
family: meanrev
status: core-belief
---
# Statistical Mean Reversion

Absent a trend regime, extensions beyond ~2σ from anchored VWAP revert:
liquidity providers earn the panic premium of overreacting traders
(De Bondt–Thaler short-horizon overreaction).

## Guards
- Never fade inside TRENDING regimes — that's paying to catch knives.
- Require z ≥ 2.5 (see [[VWAP Extreme Fade]]).
