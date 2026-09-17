---
type: strategy
state: retired
family: ema_trend
origin: brain
author: Strategist
---
# ema_trend variant (22.0)

> **Hypothesis.** The market shows a strong trending environment with low robustness for ema_trend, so I will stick to default parameters to avoid overfitting.
>
> **Invalidation.** Demote on PF<0.85/20 trades or 6 straight losses.

## Genes
```json
{"adx_min": 22.0, "pullback_atr": 0.8, "trend_tf": "15m"}
```

## Live record
- closed trades: 9 · wins: 4
- realized P&L: -6.19 USDT
- state: **retired**

Related: [[Behavioral Momentum]], [[Regime Playbook]], [[MOC]]

Same family (`ema_trend`): [[EMA_Stack_Pullback]], [[ema_trend_harvested]], [[ema_trend_variant_(22.0)_·adx_min=26.8812]]

Filed by [[Strategist]]
