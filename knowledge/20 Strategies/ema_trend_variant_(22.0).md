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
- closed trades: 9 · wins: 2
- realized P&L: -299.17 USDT
- state: **retired**

Related: [[Behavioral Momentum]], [[Regime Playbook]], [[MOC]]

Same family (`ema_trend`): [[EMA_Stack_Pullback]], [[ema_trend_harvested]]

Filed by [[Strategist]]
