---
type: strategy
state: paper
family: vwap_fade
origin: brain
author: Strategist
---
# vwap_fade variant (1.5957)

> **Hypothesis.** Without a trend regime, extensions beyond ~2.5σ from the anchored VWAP revert as liquidity providers are paid the panic premium of overreaction traders (De Bondt–Thaler).
>
> **Invalidation.** Demote on PF<0.85 over 20 trades or 6 straight losses.

## Genes
```json
{"z_entry": 1.5957, "anchor_bars": 133, "max_hold_bars": 51}
```

## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **paper**

Related: [[Statistical Mean Reversion]], [[Regime Playbook]], [[MOC]]

Filed by [[Strategist]]
