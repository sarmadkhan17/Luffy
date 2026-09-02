---
type: strategy
state: retired
family: spec
origin: authored
author: Strategist
---
# Aggressor Divergence At Highs

> **Hypothesis.** When price reaches a new high but the aggressor-buy share is falling, the advance is being absorbed by passive sellers rather than driven by demand. Distribution looks like strength on a price chart and like weakness on a flow chart.
>
> **Invalidation.** Retire if worst-symbol out-of-sample profit factor stays below 1.0 over 30 trades, or if the mechanism's driving series stops being published.

# Aggressor Divergence At Highs

**Thesis** — When price reaches a new high but the aggressor-buy share is falling, the advance is being absorbed by passive sellers rather than driven by demand. Distribution looks like strength on a price chart and like weakness on a flow chart.

**Invalidation** — Retire if worst-symbol out-of-sample profit factor stays below 1.0 over 30 trades, or if the mechanism's driving series stops being published.

- Timeframe: `15m`  ·  Direction: `short`
- Regimes: RANGING, TRENDING_UP
- Data: ohlcv
- Provenance: authored

## Logic
```
short:  close >= donchian_hi(48) and zscore(taker_buy_frac, 192) < -0.3
stop:   {'kind': 'atr', 'mult': 1.5}
target: {'kind': 'rr', 'v': 2.0}
trail:  {'kind': 'none'}
time:   max 32 bars
```


## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **retired**

Related: [[Regime Playbook]], [[MOC]]

Same family (`spec`): [[Aggressor_Thrust_Breakout]], [[BTC_Rotation_Momentum]], [[Donchian_Breakout_Trail]], [[Funding_Price_Divergence]]

Filed by [[Strategist]]
