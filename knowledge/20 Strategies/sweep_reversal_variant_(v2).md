---
type: strategy
state: retired
family: sweep_reversal
origin: brain
---
# sweep_reversal variant (v2)

> **Hypothesis.** The market shows a strong tendency for liquidity sweeps to fail and reverse, with a notable out-of-sample return of 14.87% indicating a robust mean-reversion behavior after sweeps.
>
> **Invalidation.** Demote on PF<0.85/20 trades or 6 straight losses.

## Genes
```json
{"max_reclaim_bars": 3, "min_sweep_frac": 0.002}
```

## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **retired**

Related: [[Regime Playbook]], [[MOC]]
