---
type: strategy
state: paper
family: ema_trend
origin: brain
---
# ema_trend variant (28.0)

> **Hypothesis.** The current market shows high returns but low robustness, suggesting a strong but unstable trend that requires strict trend confirmation and conservative pullback entries.
>
> **Invalidation.** Demote on PF<0.85/20 trades or 6 straight losses.

## Genes
```json
{"adx_min": 28.0, "pullback_atr": 1.2, "trend_tf": "15m"}
```

## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **paper**

Related: [[Regime Playbook]], [[MOC]]
