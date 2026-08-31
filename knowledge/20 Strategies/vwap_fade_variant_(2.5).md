---
type: strategy
state: paper
family: vwap_fade
origin: brain
---
# vwap_fade variant (2.5)

> **Hypothesis.** The current market shows overfitted VWAP fade conditions, so use conservative parameters to avoid losses.
>
> **Invalidation.** Demote on PF<0.85/20 trades or 6 straight losses.

## Genes
```json
{"z_entry": 2.5, "anchor_bars": 96, "max_hold_bars": 24}
```

## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **paper**

Related: [[Regime Playbook]], [[MOC]]
