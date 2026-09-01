---
type: strategy
state: paper
family: spec
origin: authored
author: Strategist
---
# Aggressor Thrust Breakout

> **Hypothesis.** A range break that coincides with an extreme in aggressor buying is a break someone is paying the spread to cause. Paying up is costly and therefore informative, which separates a real break from drift across a level.
>
> **Invalidation.** Retire if worst-symbol out-of-sample profit factor stays below 1.0 over 30 trades, or if the mechanism's driving series stops being published.

# Aggressor Thrust Breakout

**Thesis** — A range break that coincides with an extreme in aggressor buying is a break someone is paying the spread to cause. Paying up is costly and therefore informative, which separates a real break from drift across a level.

**Invalidation** — Retire if worst-symbol out-of-sample profit factor stays below 1.0 over 30 trades, or if the mechanism's driving series stops being published.

- Timeframe: `4h`  ·  Direction: `both`
- Regimes: TRENDING_UP, TRENDING_DOWN
- Data: ohlcv
- Provenance: authored

## Logic
```
long:   close > donchian_hi(48) and zscore(taker_buy_frac, 192) > 1.0
short:  close < donchian_lo(48) and zscore(taker_buy_frac, 192) < -1.0
stop:   {'kind': 'atr', 'mult': 2.5}
target: {'kind': 'rr', 'v': 3.0}
trail:  {'kind': 'none'}
time:   max 64 bars
```


## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **paper**

Related: [[Regime Playbook]], [[MOC]]

Same family (`spec`): [[Aggressor_Divergence_At_Highs]], [[BTC_Rotation_Momentum]], [[Funding_Price_Divergence]]

Filed by [[Strategist]]
