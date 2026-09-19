---
type: strategy
family: spec
status: candidate
confidence: observed
claim: 'A break of a multi-week Donchian range persists because risk is repriced slowly: systematic participants scale in over days and trapped counter-positions cover into the move, so the tail of a breakout is fatter than its body. A trailing stop harvests that tail, while a fixed target amputates it and turns a positive-expectancy mechanism into a losing one. Long: close > donchian_hi(100); short: close < donchian_lo(100); stop: ATR 2.0; target: none; trail: arm at 1R, ATR 4.0; max 500 bars.'
relations:
  supports: []
  contradicts: []
  works_in: []
  fails_in: []
  causes: []
  precedes: []
  derived_from:
  - Donchian Breakout Trail
source_titles:
- Donchian Breakout Trail
source_paths:
- knowledge/20 Strategies/Donchian_Breakout_Trail.md
---

# Donchian Breakout Trail

A break of a multi-week Donchian range persists because risk is repriced slowly: systematic participants scale in over days and trapped counter-positions cover into the move, so the tail of a breakout is fatter than its body. A trailing stop harvests that tail, while a fixed target amputates it and turns a positive-expectancy mechanism into a losing one. Long: close > donchian_hi(100); short: close < donchian_lo(100); stop: ATR 2.0; target: none; trail: arm at 1R, ATR 4.0; max 500 bars.
