---
type: strategy
state: retired
family: bb_fade
origin: analyst
author: Strategist
---
# BB 2.5σ Band Fade

> **Hypothesis.** A close back inside 2.5-sigma Bollinger bands after piercing them marks a failed extension: trapped late entrants unwind and price reverts toward the mean. Mid-width bands fire roughly twice as often as 3-sigma while keeping a modest positive expectancy. Intraday holds only (<=8h).
>
> **Invalidation.** Demote on PF<0.85 over 20 trades or 6 straight losses; retire if live WR<40%/PF<1.15 after 15-trade probation.

## Genes
```json
{"bb_len": 30, "bb_k": 2.5}
```

## Live record
- closed trades: 1 · wins: 1
- realized P&L: +43.51 USDT
- state: **retired**

Related: [[Statistical Mean Reversion]], [[Regime Playbook]], [[MOC]]

Same family (`bb_fade`): [[BB_3σ_Exhaustion_Fade]]

Filed by [[Strategist]]
