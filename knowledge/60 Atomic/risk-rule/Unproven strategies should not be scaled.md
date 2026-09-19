---
type: risk-rule
family: strategy-validation
status: candidate
confidence: observed
claim: A strategy with no live trades yet is unproven and should not be treated as having an edge; it must first generate trades and data before being relied upon.
relations:
  supports: []
  contradicts: []
  works_in: []
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - Meta-review 2026-08-30
source_titles:
- Meta-review 2026-08-30
source_paths:
- knowledge/30 Postmortems/20260830-1930 Meta-review 2026-08-30.md
---

# Unproven strategies should not be scaled

A strategy with no live trades yet is unproven and should not be treated as having an edge; it must first generate trades and data before being relied upon.
