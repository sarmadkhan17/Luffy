---
type: risk-rule
family: regime_gating
status: candidate
confidence: observed
claim: Short-side entries should require a separate sell-side accuracy gate rather than being justified by raw regime bucket counts, until sell-side 4h accuracy demonstrates an edge.
relations:
  supports:
  - Sell-side signal accuracy collapse
  - SELL signals underperform BUY signals
  contradicts: []
  works_in: []
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - Autopsy — 2026-08-30 12:42 UTC
  - Autopsy — 2026-08-30 19:05 UTC
  - Autopsy — 2026-09-02 02:32 UTC
  - Autopsy — 2026-09-11 12:29 UTC
source_titles:
- Autopsy — 2026-08-30 12:42 UTC
- Autopsy — 2026-08-30 19:05 UTC
- Autopsy — 2026-09-02 02:32 UTC
- Autopsy — 2026-09-11 12:29 UTC
source_paths:
- knowledge/30 Postmortems/20260830-1242 Autopsy.md
- knowledge/30 Postmortems/20260830-1905 Autopsy.md
- knowledge/30 Postmortems/20260902-0232 Autopsy.md
- knowledge/30 Postmortems/20260911-1229 Autopsy.md
merged_from:
- Sell-side accuracy gate for short entries
- Short-side 4h accuracy gate
- SELL block until prospective counterfactual validation
- SELL direction blocked due to sub-chance accuracy
---

# Short-side 4h accuracy gate

Short-side entries should require a separate sell-side accuracy gate rather than being justified by raw regime bucket counts, until sell-side 4h accuracy demonstrates an edge.
