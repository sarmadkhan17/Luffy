---
type: risk-rule
family: execution-safety
status: candidate
confidence: observed
claim: An order that exists without a corresponding journal entry must be caught by a reconcile sweep; otherwise a crash between order placement and journaling leaves an untracked position.
relations:
  supports:
  - Journal-before-risk write ordering
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

# Unjournaled order requires reconcile sweep

An order that exists without a corresponding journal entry must be caught by a reconcile sweep; otherwise a crash between order placement and journaling leaves an untracked position.
