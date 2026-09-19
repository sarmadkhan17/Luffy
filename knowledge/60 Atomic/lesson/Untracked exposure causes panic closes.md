---
type: market-mechanism
family: risk_first
status: candidate
confidence: observed
claim: Accumulated untracked pre-existing positions become the dominant journal activity and are eventually liquidated via panic closes, producing uncontrolled realized losses (net ~-36.17 USDT across 8 fills in this case).
relations:
  supports:
  - Untracked positions are a hard risk-limit breach
  contradicts: []
  works_in: []
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - Autopsy — 2026-08-26 10:15 UTC
source_titles:
- Autopsy — 2026-08-26 10:15 UTC
source_paths:
- knowledge/30 Postmortems/20260826-1015 Autopsy.md
---

# Untracked exposure causes panic closes

Accumulated untracked pre-existing positions become the dominant journal activity and are eventually liquidated via panic closes, producing uncontrolled realized losses (net ~-36.17 USDT across 8 fills in this case).
