---
type: strategy
state: demoted
family: rsi_extreme
origin: analyst
author: Strategist
---
# RSI Exhaustion Reclaim

> **Hypothesis.** When RSI(14) crosses back out of oversold(<30) or overbought(>70), the panic/exhaustion leg is spent and price snaps toward the mean within a few 15m bars; classic Connors-style reclaim is a documented, non-curve-fit intraday edge. Held <=32 bars (~8h), never weekly.
>
> **Invalidation.** Demote on PF<0.85 over 20 trades or 6 straight losses; retire if live WR<40%/PF<1.15 after probation of 15 trades.

## Genes
```json
{"rsi_len": 14, "os_level": 30.0, "ob_level": 70.0}
```

## Live record
- closed trades: 2 · wins: 0
- realized P&L: -124.17 USDT
- state: **demoted**

Related: [[Statistical Mean Reversion]], [[Regime Playbook]], [[MOC]]

Filed by [[Strategist]]
