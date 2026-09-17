---
type: strategy
state: retired
family: ema_trend
origin: mutation
author: Strategist
---
# ema_trend variant (22.0) ·adx_min=26.8812

> **Hypothesis.** The market shows a strong trending environment with low robustness for ema_trend, so I will stick to default parameters to avoid overfitting.
>
> **Invalidation.** Demote on PF<0.85/20 trades or 6 straight losses.

## Genes
```json
{"adx_min": 26.8812, "pullback_atr": 0.8, "trend_tf": "15m"}
```

## Live record
- closed trades: 8 · wins: 8
- realized P&L: +46.33 USDT
- state: **retired**

Related: [[Behavioral Momentum]], [[Regime Playbook]], [[MOC]]

Same family (`ema_trend`): [[EMA_Stack_Pullback]], [[ema_trend_harvested]], [[ema_trend_variant_(22.0)]]

Filed by [[Strategist]]
