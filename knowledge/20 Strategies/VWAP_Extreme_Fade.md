---
type: strategy
state: retired
family: vwap_fade
origin: seed
author: Strategist
---
# VWAP Extreme Fade

> **Hypothesis.** Without a trend regime, extensions beyond ~2.5σ from the anchored VWAP revert as liquidity providers are paid the panic premium of overreaction traders (De Bondt–Thaler).
>
> **Invalidation.** Demote if winrate<40% over 15 trades or PF<1.0 over 20.

## Genes
```json
{"z_entry": 2.5, "anchor_bars": 96, "max_hold_bars": 24}
```

## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **retired**

Related: [[Statistical Mean Reversion]], [[Regime Playbook]], [[MOC]]

Same family (`vwap_fade`): [[vwap_fade_variant_(1.9493)]]

Filed by [[Strategist]]
