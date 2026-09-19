---
type: risk-rule
family: ema_trend
status: candidate
confidence: unverified
claim: Demote the ema_trend strategy when profit factor falls below 0.85 over 20 trades or after 6 consecutive losses.
relations:
  supports: []
  contradicts: []
  works_in: []
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - ema_trend harvested
  - ema_trend variant (20.0)
  - ema_trend variant (22.0)
  - ema_trend variant (22.0) ·adx_min=26.8812
source_titles:
- ema_trend harvested
- ema_trend variant (20.0)
- ema_trend variant (22.0)
- ema_trend variant (22.0) ·adx_min=26.8812
source_paths:
- knowledge/20 Strategies/ema_trend_harvested.md
- knowledge/20 Strategies/ema_trend_variant_(20.0).md
- knowledge/20 Strategies/ema_trend_variant_(22.0).md
- knowledge/20 Strategies/ema_trend_variant_(22.0)_·adx_min=26.8812.md
merged_from:
- ema_trend invalidation rule
- Demote ema_trend variant on PF<0.85 or 6 straight losses
- ema_trend variant 22.0 invalidation rule
- Demote on PF<0.85 or 6 straight losses
---

# ema_trend invalidation rule

Demote the ema_trend strategy when profit factor falls below 0.85 over 20 trades or after 6 consecutive losses.
