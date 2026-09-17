---
type: strategy
state: demoted
family: ema_trend
origin: seed
author: Strategist
---
# EMA Stack Pullback

> **Hypothesis.** Trends persist short-term due to anchoring and delayed discretionary entry; buying shallow pullbacks within an aligned EMA stack captures continuation at reduced risk.
>
> **Invalidation.** Demote after 6 consecutive losses or PF<0.8 over 20 trades.

## Genes
```json
{"adx_min": 22.0, "pullback_atr": 0.8, "trend_tf": "15m"}
```

## Live record
- closed trades: 2 · wins: 1
- realized P&L: +91.38 USDT
- state: **demoted**

Related: [[Behavioral Momentum]], [[Regime Playbook]], [[MOC]]

Same family (`ema_trend`): [[ema_trend_harvested]], [[ema_trend_variant_(22.0)]], [[ema_trend_variant_(22.0)_·adx_min=26.8812]]

Filed by [[Strategist]]
