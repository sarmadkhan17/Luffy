---
type: risk-rule
family: operational-integrity
status: candidate
confidence: observed
claim: Reconciled ghost fills (e.g. MAGMA -199.24) are operational artifacts and must be separated from strategy PnL attribution, since 1 of 12 recent closes was a reconciled_ghost.
relations:
  supports: []
  contradicts: []
  works_in: []
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - Autopsy — 2026-08-31 00:10 UTC
  - Autopsy — 2026-08-31 02:10 UTC
  - Autopsy — 2026-08-31 09:38 UTC
source_titles:
- Autopsy — 2026-08-31 00:10 UTC
- Autopsy — 2026-08-31 02:10 UTC
- Autopsy — 2026-08-31 09:38 UTC
source_paths:
- knowledge/30 Postmortems/20260831-0010 Autopsy.md
- knowledge/30 Postmortems/20260831-0210 Autopsy.md
- knowledge/30 Postmortems/20260831-0938 Autopsy.md
merged_from:
- Ghost fills must be excluded from strategy PnL
- Reconciled ghost fills distort strategy PnL
- Ghost-adjusted loss attribution
---

# Reconciled ghost fills distort strategy PnL

Reconciled ghost fills (e.g. MAGMA -199.24) are operational artifacts and must be separated from strategy PnL attribution, since 1 of 12 recent closes was a reconciled_ghost.
