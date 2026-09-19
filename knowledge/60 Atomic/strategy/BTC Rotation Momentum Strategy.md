---
type: strategy
family: spec
status: candidate
confidence: unverified
claim: Long alts when btc_ret(4) > 0.008, ret(4) > 0, and rel_strength_btc(4) < 0, filtered by corr_btc(96) > 0.3, with ATR stop 2.0, RR target 2.0, no trail, max 24 bars, in TRENDING_UP regime.
relations:
  supports:
  - Crypto Capital Rotation Lag
  contradicts: []
  works_in:
  - TRENDING_UP
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - BTC Rotation Momentum
source_titles:
- BTC Rotation Momentum
source_paths:
- knowledge/20 Strategies/BTC_Rotation_Momentum.md
---

# BTC Rotation Momentum Strategy

Long alts when btc_ret(4) > 0.008, ret(4) > 0, and rel_strength_btc(4) < 0, filtered by corr_btc(96) > 0.3, with ATR stop 2.0, RR target 2.0, no trail, max 24 bars, in TRENDING_UP regime.
