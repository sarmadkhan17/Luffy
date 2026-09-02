---
type: strategy
state: retired
family: bb_fade
origin: analyst
author: Strategist
---
# BB 3σ Exhaustion Fade

> **Hypothesis.** A close back inside a 3-sigma Bollinger band after piercing it marks an exhausted counter-move: late entrants are trapped and price snaps back toward the mean. Wide 3σ bands filter noise so only genuine exhaustion events fire, in every regime. Intraday holds only (<=8h).
>
> **Invalidation.** Demote on PF<0.85 over 20 trades or 6 straight losses; retire if live WR<40% after 15-trade probation.

## Genes
```json
{"bb_len": 20, "bb_k": 3.0}
```

## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **retired**

Related: [[Statistical Mean Reversion]], [[Regime Playbook]], [[MOC]]

Same family (`bb_fade`): [[BB_2.5σ_Band_Fade]]

Filed by [[Strategist]]
