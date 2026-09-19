---
type: strategy
state: paper
family: spec
origin: measured
author: Strategist
---
# Donchian Breakout Trail

> **Hypothesis.** A break of a multi-week range persists because risk is repriced slowly: systematic participants scale in over days and trapped counter-positions cover into the move, so the tail of a breakout is fatter than its body. A trailing stop harvests that tail, while a fixed target amputates it and turns a positive-expectancy mechanism into a losing one.
>
> **Invalidation.** Retire if the BOTH-directions configuration drops below profit factor 1.0 over 40 out-of-sample trades, or if it stops beating an always-long and always-short control on the same exit geometry. Do not judge the long and short legs separately: each fails alone and the pair is the strategy.

# Donchian Breakout Trail

**Thesis** — A break of a multi-week range persists because risk is repriced slowly: systematic participants scale in over days and trapped counter-positions cover into the move, so the tail of a breakout is fatter than its body. A trailing stop harvests that tail, while a fixed target amputates it and turns a positive-expectancy mechanism into a losing one.

**Invalidation** — Retire if the BOTH-directions configuration drops below profit factor 1.0 over 40 out-of-sample trades, or if it stops beating an always-long and always-short control on the same exit geometry. Do not judge the long and short legs separately: each fails alone and the pair is the strategy.

- Timeframe: `4h`  ·  Direction: `both`
- Regimes: any
- Data: ohlcv
- Provenance: measured

## Logic
```
long:   close > donchian_hi(100)
short:  close < donchian_lo(100)
stop:   {'kind': 'atr', 'mult': 2.0}
target: {'kind': 'none'}
trail:  {'arm_at_r': 1.0, 'kind': 'atr', 'mult': 4.0}
time:   max 500 bars
```


## Live record
- closed trades: 11 · wins: 0
- realized P&L: -42.14 USDT
- state: **paper**

Related: [[Regime Playbook]], [[MOC]]

Same family (`spec`): [[Aggressor_Divergence_At_Highs]], [[Aggressor_Thrust_Breakout]], [[BTC_Rotation_Momentum]], [[Funding_Filtered_Trend_Pullback]], [[Funding_Price_Divergence]], [[Momentum_Divergence_Trail]]

Filed by [[Strategist]]
