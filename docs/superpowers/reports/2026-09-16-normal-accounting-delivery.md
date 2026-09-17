# M8.1 normal booking provenance and demo rollout

September 16, 2026. **195 tests passed against final source** across 17 relevant
modules in 14.98 seconds. The normal booking and prior emergency accounting slices
are now loaded by the demo kernel. M8.1 remains **partial**, total **12/55**:
funding and complete whole-trade attribution are still unavailable.

## Concrete changes

Ordinary journal updates previously discarded the exact fill evidence behind a
booked amount. Missing commission/P&L fields could become zero, and a partially
filled final close used estimated P&L even when its actual leg fills were present.

- [Journal](../../../trader/core/journal.py) now writes an immutable entry,
  reduction/alignment or final-close receipt in the same transaction as the trade
  update. It retains trade/decision/strategy identity, before/after state, actual
  observation clock, evidence and a reproducible assessment. Storage failure rolls
  back the booking and TP1 flag together. A duplicate final journal close callback
  cannot add P&L a second time. This does not provide general order-submission
  idempotency; ambiguous submitted exits remain a separate recovery limitation.
- [Executor](../../../trader/engine/executor.py) retains confirmed entry-order
  references and normalized exact exit fills. Partial-close retries retain the
  actual remaining position and use available venue leg P&L/commissions. Duplicate
  fill delivery is deduplicated by ID; conflicting IDs are refused. An unconfirmed
  partial close cannot shrink journal quantity using its requested amount or price
  hint. It emits `partial_fill_unconfirmed` for reconciliation instead.
- [Booking assessment](../../../trader/engine/booking.py) validates each exit leg's
  order/fill IDs, symbol, side, quantity, clocks, finite prices/P&L/fees and USDT fee
  currency. Valid evidence is **verified leg fills only**, never complete economic
  P&L. Missing/non-USDT commissions, missing P&L and conflicts stay unverified.
- Symbol/time-window venue subtotals remain explicitly **unattributed to the whole
  trade**. Their raw normalized observations are retained at ordinary final closes
  and boot-time alignments. Full history pages, missing fields/currency/IDs and
  fetch errors cannot silently produce an actual subtotal; exception types only
  are retained. Legacy mark-based native-stop/ghost/panic bookings now receive
  unverified receipts through the shared Journal methods. Their mark values and
  inferred reason labels are not promoted to actual fills.
- Recovered entries retain the recovery intent and entry order reference in their
  booking receipt. The prior atomic emergency round-trip archive remains intact.

Legacy numeric `realized_pnl` is retained for compatibility and may still be
estimated or incomplete. The existing typed-memory adapter already imports it as
unknown; that guard was reused. Every new receipt/export has null funding and net
economic P&L and `learning_eligible=false`. Legacy strategy/risk readers of numeric
journal P&L have not all been migrated to verified accounting. Do not claim that
all historical P&L consumers now use fully attributed economics.

## Read-only owner export and replay

```bash
./venv/bin/python -m scripts.export_trade_accounting --trade-id ID --output /tmp/NEW-bookings.json
./venv/bin/python -m scripts.export_trade_accounting --replay /tmp/NEW-bookings.json
```

[CLI](../../../scripts/export_trade_accounting.py) reads one consistent journal
snapshot, makes no venue calls and writes a new artifact exclusively. Each booking
has a digest; offline replay recomputes its assessment and refuses promotion of an
incomplete export. A legacy trade without receipts stays explicitly incomplete;
this command does not backfill its history. Existing emergency capture/replay
commands remain available. Normal exports are manually invoked, not scheduled.

## Evidence and limits

- [Final tests and hashes](../artifacts/execution-accounting/2026-09-16-normal-verification.json):
  195 passing tests cover booking storage/replay, partial/final arithmetic, missing
  fees, duplicate fills, unknown responses, rollback, recovery, protection,
  reconciliation, manual closes, typed memory and prospective population regressions.
  This is not the full repository suite. Prior 119/277-test evidence remains valid
  for its recorded source; overlapping totals must not be added together.
- [Synthetic partial/final artifact](../artifacts/execution-accounting/2026-09-16-normal-synthetic.json):
  three receipts (entry, partial, final), both exit legs verified; whole-trade
  accounting still incomplete. Offline CLI replay passed after the temporary
  source database was removed. Fixture P&L is not observed performance.
- [Read-only legacy artifact](../artifacts/execution-accounting/2026-09-16-normal-legacy.json):
  an existing open demo journal trade has zero receipts, preserving the historical
  gap. It is not reclassified as newly observed execution or prospectively backfilled.
- [Troubleshooting](../artifacts/execution-accounting/2026-09-16-normal-troubleshooting.jsonl):
  Claude remained session-limited; direct work followed existing authorization.
  `git diff --check` passed; unrelated workspace changes were preserved.

## Deployment and actual runtime

[Preflight](../artifacts/execution-accounting/2026-09-16-normal-preflight.json),
September 16 22:08:27 UTC: three demo venue positions matched journal ownership
and quantity and had matching reduce-only STOP_MARKET protection. No pending
recovery, control ACTIVE, referee/handoff false; watchdog enabled.

[Controlled rollout](../artifacts/execution-accounting/2026-09-16-normal-rollout.json)
started 22:09:00 UTC: kernel PID 262048 replaced by **300976**, with watchdog paused
for the restart command and restored at 22:09:01 UTC. No dashboard restart or
operator control change. This supersedes the earlier emergency report's staged-only
status. The initial restart interval is preserved; no claim of uninterrupted scans.

[Post-rollout receipt](../artifacts/execution-accounting/2026-09-16-normal-runtime.json),
checks begun 22:10:50 UTC, verifies a fresh post-restart heartbeat, ACTIVE demo
runtime and all three matched/protected venue positions: XRP short 68.9, LINK short
36.24, AAVE short 0.9. Population consumers ran naturally at 22:10:01/02 UTC, status
ok and hooks enabled. Attention worker alive. Watchdog restored; referee/handoff
false. Existing dashboard binding `192.168.126.131:8080` preserved: unauthenticated
API 401; authenticated API, browser login and cookie dashboard 200. No secrets saved.

Both accounting tables exist, but each still has **zero forward records**. This
proves deployment/initialization and synthetic correctness, not an observed live
accounting round trip. No emergency or ordinary trade was forced for evidence.
Tested source and doctrine/frozen declaration/freeze hashes were unchanged across
rollout. M3.1 exports and local indices remain intact.

## Next exact work

Continue M8.1 by joining all entry/exit legs with proven ownership and quantities,
then attributing signed funding cashflows and non-USDT fee currency explicitly.
Define and measure history completeness before asserting zero funding. Retain
unknowns for missing order versions, legacy entry receipts and ambiguous/partial
exit submissions; add bounded retry/export integration without network work in
fast safety paths. Connect only complete receipts to the existing verified-outcome
adapter and review remaining legacy P&L consumers. Observe the first natural
booking and replay its receipt; do not manufacture a trade to obtain one.

M3.1 collection is still scheduled September 17 00:00 to September 18 00:00 UTC;
full-window review remains due at/after the cut. Exact forecasts are still premature
at this session's clock; preserve missing-version retries. No M3.2 without coverage
review and a separate frozen protocol; no Gate 2 restart. Execution accounting and
confirmation behavior changed, not strategy selection or risk limits. No candidate,
admission or better-performance claim. No roadmap dependency change was needed.
