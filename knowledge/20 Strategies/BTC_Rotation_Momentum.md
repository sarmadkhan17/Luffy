---
type: strategy
state: retired
family: spec
origin: authored
author: Strategist
---
# BTC Rotation Momentum

> **Hypothesis.** Crypto capital rotates BTC to ETH to large caps to small caps; alts that are positive yet lagging a fresh BTC impulse get chased by late rotation buyers within hours.
>
> **Invalidation.** Demote if profit factor is below 1.0 over 20 trades or BTC correlation collapses.

# BTC Rotation Momentum

**Thesis** — Crypto capital rotates BTC to ETH to large caps to small caps; alts that are positive yet lagging a fresh BTC impulse get chased by late rotation buyers within hours.

**Invalidation** — Demote if profit factor is below 1.0 over 20 trades or BTC correlation collapses.

- Timeframe: `1h`  ·  Direction: `long`
- Regimes: TRENDING_UP
- Data: ohlcv
- Provenance: seed

## Logic
```
long:   btc_ret(4) > 0.008 and ret(4) > 0 and rel_strength_btc(4) < 0
filter: corr_btc(96) > 0.3
stop:   {'kind': 'atr', 'mult': 2.0}
target: {'kind': 'rr', 'v': 2.0}
trail:  {'kind': 'none'}
time:   max 24 bars
```


## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **retired**

Related: [[Regime Playbook]], [[MOC]]

Same family (`spec`): [[Aggressor_Divergence_At_Highs]], [[Aggressor_Thrust_Breakout]], [[Donchian_Breakout_Trail]], [[Funding_Price_Divergence]]

Filed by [[Strategist]]
