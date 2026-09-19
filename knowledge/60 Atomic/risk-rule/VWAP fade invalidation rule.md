---
type: risk-rule
family: vwap_fade
status: candidate
confidence: unverified
claim: Demote if winrate<40% over 15 trades or PF<1.0 over 20.
relations:
  supports: []
  contradicts: []
  works_in: []
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - VWAP Extreme Fade
  - vwap_fade variant (1.5957)
  - vwap_fade variant (2.5)
source_titles:
- VWAP Extreme Fade
- vwap_fade variant (1.5957)
- vwap_fade variant (2.5)
source_paths:
- knowledge/20 Strategies/VWAP_Extreme_Fade.md
- knowledge/20 Strategies/vwap_fade_variant_(1.5957).md
- knowledge/20 Strategies/vwap_fade_variant_(2.5).md
merged_from:
- VWAP Extreme Fade Invalidation
- VWAP fade invalidation rule
- VWAP fade demotion rule
---

# VWAP fade invalidation rule

Demote if winrate<40% over 15 trades or PF<1.0 over 20.
