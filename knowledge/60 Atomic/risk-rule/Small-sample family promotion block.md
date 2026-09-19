---
type: risk-rule
family: promotion-gating
status: candidate
confidence: observed
claim: A strategy family should not be promoted when its aggregate is driven by mixed variants and only one member has n>10; in this snapshot ema_trend aggregates 19/13/+$131.52 but is composed of small mixed variants, and only sweep_reversal has n>10 (18/10/-$98.95).
relations:
  supports: []
  contradicts: []
  works_in: []
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - Autopsy — 2026-09-11 02:29 UTC
source_titles:
- Autopsy — 2026-09-11 02:29 UTC
source_paths:
- knowledge/30 Postmortems/20260911-0229 Autopsy.md
---

# Small-sample family promotion block

A strategy family should not be promoted when its aggregate is driven by mixed variants and only one member has n>10; in this snapshot ema_trend aggregates 19/13/+$131.52 but is composed of small mixed variants, and only sweep_reversal has n>10 (18/10/-$98.95).
