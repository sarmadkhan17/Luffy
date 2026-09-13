# Phase 1: Correctness & Booking Fixes - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-09-13
**Phase:** 01-correctness-booking-fixes
**Areas discussed:** Production fee source, Live-mode guard, Warmup scope

Per the operator's standing preference (Claude makes engineering calls), the
FIX-01 clock fix and the choice not to scale WARMUP by duration were decided
by Claude and stated rather than asked. Only key/money/live-behaviour
questions went to the operator.

---

## Production fee source (FIX-03)

| Option | Description | Selected |
|--------|-------------|----------|
| Read-only prod key | Operator creates a reading-only, IP-restricted production key under separate .env names; one-shot script reads commissionRate and records it | ✓ |
| Published rate, provisional | Record Binance's published VIP0 0.05% as unmeasured; fails success criterion 2 as written | |
| I'll run the call myself | Operator runs the read with their own credentials and pastes the answer | |

**User's choice:** Read-only prod key

---

## Live-mode guard (FIX-03)

| Option | Description | Selected |
|--------|-------------|----------|
| Hard refusal | Kernel refuses to boot with BINANCE_DEMO=false without a recorded production fee | |
| Record only | Record the fee and document it; no startup check | ✓ |

**User's choice:** Record only

---

## Warmup scope (FIX-04)

Raised after scouting found that at 4h the 30-day decay slice is 180 bars,
under `simulate`'s `WARMUP + 1` early return, so the live Donchian spec can
never be retired for decay.

| Option | Description | Selected |
|--------|-------------|----------|
| Fix at 4h in Phase 1 | Warm up on bars before the window, score only the window; before/after readings recorded; first decay sweep may retire a live strategy | ✓ |
| Guard only, new phase for 4h | 1d+ guard now; 4h fault recorded and fixed in an inserted phase 1.1 | |

**User's choice:** Fix at 4h in Phase 1

---

## Claude's Discretion

- FIX-01: route `_load_cached` cache age and horizon through `self._now()` (production seam, not a test patch).
- FIX-04: do not scale WARMUP by duration (bar-count lookback is correct; duration scaling breaks 15m); warmup prefix from prior bars plus a measured loud guard.
- Guard thresholds, script naming, caller threading, plan split.

## Deferred Ideas

- Boot guard on BINANCE_DEMO=false: declined by the operator.
- Switching `taker_fee_pct` to production: part of the go-live decision.
