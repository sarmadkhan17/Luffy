---
type: strategy
state: paper
family: sweep_reversal
origin: seed
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
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **paper**

Related: [[Regime Playbook]], [[MOC]]
