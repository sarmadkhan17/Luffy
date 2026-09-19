---
type: strategy
family: spec
status: candidate
confidence: unverified
claim: When price reaches a new high but the aggressor-buy share is falling, the advance is being absorbed by passive sellers rather than driven by demand; distribution looks like strength on a price chart and like weakness on a flow chart. Short when close >= donchian_hi(48) and zscore(taker_buy_frac, 192) < -0.3, with ATR stop (1.5x), 2R target, no trail, max 32 bars.
relations:
  supports: []
  contradicts: []
  works_in:
  - RANGING
  - TRENDING_UP
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - Aggressor Divergence At Highs
source_titles:
- Aggressor Divergence At Highs
source_paths:
- knowledge/20 Strategies/Aggressor_Divergence_At_Highs.md
---

# Aggressor Divergence At Highs

When price reaches a new high but the aggressor-buy share is falling, the advance is being absorbed by passive sellers rather than driven by demand; distribution looks like strength on a price chart and like weakness on a flow chart. Short when close >= donchian_hi(48) and zscore(taker_buy_frac, 192) < -0.3, with ATR stop (1.5x), 2R target, no trail, max 32 bars.
