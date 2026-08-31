---
type: strategy
state: paper
family: ema_trend
origin: brain
---
# ema_trend variant (20.0)

> **Hypothesis.** The market conditions show overfitting in ema_trend, so I set conservative parameters to avoid chasing noise and focus on stable trends with moderate pullback entries.
>
> **Invalidation.** Demote on PF<0.85/20 trades or 6 straight losses.

## Genes
```json
{"adx_min": 20.0, "pullback_atr": 0.6, "trend_tf": "15m"}
```

## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **paper**

Related: [[Regime Playbook]], [[MOC]]
