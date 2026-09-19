---
type: lesson
family: risk_first
status: candidate
confidence: observed
claim: When all closed trades are untracked pre-existing positions closed for panic, realized PnL is dominated by a single large loser (ZEC short -35.97 of -36.17 total), showing untracked exposure drives outsized panic-close losses.
relations:
  supports:
  - Untracked positions are a risk-limit violation
  - Untracked positions are a hard risk-limit breach
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
- Panic closes concentrate losses in untracked positions
- Panic closes of untracked positions realize uncontrolled losses
---

# Panic closes of untracked positions realize uncontrolled losses

When all closed trades are untracked pre-existing positions closed for panic, realized PnL is dominated by a single large loser (ZEC short -35.97 of -36.17 total), showing untracked exposure drives outsized panic-close losses.
