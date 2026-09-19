---
type: risk-rule
family: execution-safety
status: candidate
confidence: observed
claim: Persisting the decision outcome before placing risk (or before the risk guard) makes crashes idempotent, because a crash after journaling cannot cause the same signal to refire as an unrecorded entry.
relations:
  supports: []
  contradicts: []
  works_in: []
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - ZEC duplicate-entry crash-loop
source_titles:
- ZEC duplicate-entry crash-loop
source_paths:
- knowledge/30 Postmortems/20260824-2032 ZEC duplicate-entry crash-loop.md
---

# Journal-before-risk write ordering

Persisting the decision outcome before placing risk (or before the risk guard) makes crashes idempotent, because a crash after journaling cannot cause the same signal to refire as an unrecorded entry.
