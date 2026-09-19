---
type: risk-rule
family: risk_first
status: candidate
confidence: observed
claim: Positions not tracked by a strategy (pre-existing positions) violate risk limits and must be flattened before they can force a panic close, since they bypass heat and averaging controls.
relations:
  supports: []
  contradicts: []
  works_in: []
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - Autopsy — 2026-08-24 21:42 UTC
  - Autopsy — 2026-08-24 21:47 UTC
source_titles:
- Autopsy — 2026-08-24 21:42 UTC
- Autopsy — 2026-08-24 21:47 UTC
source_paths:
- knowledge/30 Postmortems/20260824-2142 Autopsy.md
- knowledge/30 Postmortems/20260824-2147 Autopsy.md
merged_from:
- Untracked positions are a risk-limit violation
- Untracked positions are a hard risk-limit breach
---

# Untracked positions are a risk-limit violation

Positions not tracked by a strategy (pre-existing positions) violate risk limits and must be flattened before they can force a panic close, since they bypass heat and averaging controls.
