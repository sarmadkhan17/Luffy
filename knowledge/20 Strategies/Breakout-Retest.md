---
type: strategy
state: retired
family: breakout_retest
origin: seed
author: Strategist
---
# Breakout-Retest

> **Hypothesis.** Range breakouts with volume confirmation trap the other side; the retest that holds converts trapped traders into fuel, paying continuation to the breakout direction.
>
> **Invalidation.** Demote after PF<0.9 over 20 trades or DD>8%.

## Genes
```json
{"range_lookback": 48, "vol_mult": 1.4, "retest_atr": 0.5}
```

## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **retired**

Related: [[Behavioral Momentum]], [[Regime Playbook]], [[MOC]]

Filed by [[Strategist]]
