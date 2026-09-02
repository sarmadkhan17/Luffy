---
type: strategy
state: retired
family: spec
origin: strategist
author: Strategist
---
# Momentum Divergence Trail

> **Hypothesis.** When price makes an extreme low/high but momentum over the same window refuses to confirm, passive/lagging flow is absorbing the last move; late trend chasers provide the fuel for a snap that a trailing exit lets run.
>
> **Invalidation.** If divergence entries stop reaching 1R and repeatedly hit the ATR stop, the absorption edge is gone.

# Momentum Divergence Trail

**Thesis** — When price makes an extreme low/high but momentum over the same window refuses to confirm, passive/lagging flow is absorbing the last move; late trend chasers provide the fuel for a snap that a trailing exit lets run.

**Invalidation** — If divergence entries stop reaching 1R and repeatedly hit the ATR stop, the absorption edge is gone.

- Timeframe: `1h`  ·  Direction: `both`
- Regimes: RANGING
- Data: ohlcv
- Provenance: strategist

## Logic
```
long:   prev(pct_rank(close, 96), 1) >= 0.15 and pct_rank(close, 96) < 0.15 and pct_rank(rsi(14), 96) > 0.30
short:  prev(pct_rank(close, 96), 1) <= 0.85 and pct_rank(close, 96) > 0.85 and pct_rank(rsi(14), 96) < 0.70
filter: adx(14) < 25
filter: rel_volume(24) > 1.0
stop:   {'kind': 'atr', 'mult': 2.5}
target: {'kind': 'none'}
trail:  {'arm_at_r': 1.0, 'kind': 'atr', 'mult': 3.0}
time:   max 200 bars
```

## Measured regime evidence
```json
{
  "RANGING": {
    "hit_rate": 0.1,
    "median_pf": 0.31,
    "p25_pf": 0.133,
    "total_trades": 107,
    "windows": 20
  }
}
```


## Live record
- closed trades: 0 · wins: 0
- realized P&L: +0.00 USDT
- state: **retired**

Related: [[Regime Playbook]], [[MOC]]

Same family (`spec`): [[Aggressor_Divergence_At_Highs]], [[Aggressor_Thrust_Breakout]], [[BTC_Rotation_Momentum]], [[Donchian_Breakout_Trail]], [[Funding_Price_Divergence]]

Filed by [[Strategist]]
