---
type: strategy
state: paper
family: spec
origin: strategist
author: Strategist
---
# Funding Filtered Trend Pullback

> **Hypothesis.** Leveraged crypto perp trends persist because positioning builds reflexively: profit-takers and mean-reverters fade EMA pullbacks while open interest expands, forcing trapped shorts to cover or longs to add; funding extremes filter crowded late entries.
>
> **Invalidation.** Edge dies if OI expansion no longer precedes continuation or funding extremes stop filtering crowded entries, e.g., structural derivatives market changes.

# Funding Filtered Trend Pullback

**Thesis** — Leveraged crypto perp trends persist because positioning builds reflexively: profit-takers and mean-reverters fade EMA pullbacks while open interest expands, forcing trapped shorts to cover or longs to add; funding extremes filter crowded late entries.

**Invalidation** — Edge dies if OI expansion no longer precedes continuation or funding extremes stop filtering crowded entries, e.g., structural derivatives market changes.

- Timeframe: `15m`  ·  Direction: `both`
- Regimes: TRENDING_UP, TRENDING_DOWN
- Data: funding, ohlcv, open_interest
- Provenance: strategist

## Logic
```
long:   close > ema(200) and ema(50) > ema(200) and prev(close,1) < ema(50) and close > ema(50) and oi_ret(24) > 0.02 and funding_z(96) < 1.5 and rel_volume(24) > 1.0
short:  close < ema(200) and ema(50) < ema(200) and prev(close,1) > ema(50) and close < ema(50) and oi_ret(24) > 0.02 and funding_z(96) > -1.5 and rel_volume(24) > 1.0
filter: adx(14) > 20
stop:   {'kind': 'atr', 'mult': 2.5}
target: {'kind': 'none'}
trail:  {'arm_at_r': 1.0, 'kind': 'atr', 'mult': 3.0}
time:   max 200 bars
```


## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **paper**

Related: [[Regime Playbook]], [[MOC]]

Same family (`spec`): [[Aggressor_Divergence_At_Highs]], [[Aggressor_Thrust_Breakout]], [[BTC_Rotation_Momentum]], [[Donchian_Breakout_Trail]], [[Funding_Price_Divergence]], [[Momentum_Divergence_Trail]]

Filed by [[Strategist]]
