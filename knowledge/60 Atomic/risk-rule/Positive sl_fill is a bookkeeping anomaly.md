---
type: risk-rule
family: data-integrity
status: candidate
confidence: observed
claim: Any trade with close_reason 'sl_fill' and positive realized_pnl (e.g. MAGMA/USDT bb_fade +1.39479015) is a close_reason/sign inconsistency; such paper wins must not influence strategy ranking until audited.
relations:
  supports: []
  contradicts: []
  works_in: []
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - Autopsy — 2026-08-31 09:38 UTC
source_titles:
- Autopsy — 2026-08-31 09:38 UTC
source_paths:
- knowledge/30 Postmortems/20260831-0938 Autopsy.md
---

# Positive sl_fill is a bookkeeping anomaly

Any trade with close_reason 'sl_fill' and positive realized_pnl (e.g. MAGMA/USDT bb_fade +1.39479015) is a close_reason/sign inconsistency; such paper wins must not influence strategy ranking until audited.
