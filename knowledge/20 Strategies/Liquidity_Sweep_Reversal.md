---
type: strategy
state: demoted
family: sweep_reversal
origin: seed
author: Strategist
---
# Liquidity Sweep Reversal

> **Hypothesis.** Resting stops beyond obvious swings are liquidity; their harvest by larger players marks exhaustion, and fast reclaim of the level signals the real direction (Wyckoff spring).
>
> **Invalidation.** Demote after 6 consecutive losses or PF<0.85/20 trades.

## Genes
```json
{"max_reclaim_bars": 3, "min_sweep_frac": 0.002}
```

## Live record
- closed trades: 18 · wins: 10
- realized P&L: -98.95 USDT
- state: **demoted**

Related: [[Auction Market Theory]], [[Regime Playbook]], [[MOC]]

Same family (`sweep_reversal`): [[sweep_reversal_variant_(v2)]]

Filed by [[Strategist]]
