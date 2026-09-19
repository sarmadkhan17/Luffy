---
type: risk-rule
family: spec
status: candidate
confidence: unverified
claim: Retire the Donchian Breakout Trail if the BOTH-directions configuration drops below profit factor 1.0 over 40 out-of-sample trades, or if it stops beating an always-long and always-short control on the same exit geometry.
relations:
  supports:
  - Donchian Breakout Trail
  contradicts: []
  works_in: []
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - Donchian Breakout Trail
source_titles:
- Donchian Breakout Trail
source_paths:
- knowledge/20 Strategies/Donchian_Breakout_Trail.md
---

# Donchian Breakout Trail invalidation criteria

Retire the Donchian Breakout Trail if the BOTH-directions configuration drops below profit factor 1.0 over 40 out-of-sample trades, or if it stops beating an always-long and always-short control on the same exit geometry.
