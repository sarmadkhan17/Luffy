---
type: strategy
state: retired
family: spec
origin: authored
author: Strategist
---
# Funding Price Divergence

> **Hypothesis.** Price making new highs while funding cools means the advance is not being bought with leverage. That is the healthy version of a rally and it tends to persist, in contrast to the leverage-driven kind that funding flags.
>
> **Invalidation.** Retire if worst-symbol out-of-sample profit factor stays below 1.0 over 30 trades, or if the mechanism's driving series stops being published.

# Funding Price Divergence

**Thesis** — Price making new highs while funding cools means the advance is not being bought with leverage. That is the healthy version of a rally and it tends to persist, in contrast to the leverage-driven kind that funding flags.

**Invalidation** — Retire if worst-symbol out-of-sample profit factor stays below 1.0 over 30 trades, or if the mechanism's driving series stops being published.

- Timeframe: `15m`  ·  Direction: `long`
- Regimes: RANGING
- Data: funding, ohlcv
- Provenance: authored

## Logic
```
long:   close >= donchian_hi(192) and funding_z(1920) < 0.0
filter: adx(14) > 18
stop:   {'kind': 'atr', 'mult': 2.5}
target: {'kind': 'rr', 'v': 3.0}
trail:  {'kind': 'none'}
time:   max 64 bars
```


## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **retired**

Related: [[Regime Playbook]], [[MOC]]

Same family (`spec`): [[Aggressor_Divergence_At_Highs]], [[Aggressor_Thrust_Breakout]], [[BTC_Rotation_Momentum]], [[Donchian_Breakout_Trail]], [[Funding_Filtered_Trend_Pullback]], [[Momentum_Divergence_Trail]]

Filed by [[Strategist]]
