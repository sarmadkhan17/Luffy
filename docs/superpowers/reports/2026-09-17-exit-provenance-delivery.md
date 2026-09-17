# M8.1 emergency whole-accounting and native exit provenance: bounded manual slice

Owner approved this emergency/native-exit design; no repeat approval is needed for
the delivered scope below. Future design changes (learning wiring, automatic
worker integration, partial-exit support) still require separate review. M8.1
remains partial, count **12/55**; the roadmap is unchanged.

## Delivered

Two new engine modules, two new manual read-only CLIs, three new test files.
No safety/kernel/executor/journal/outcomes file was edited; no restart, order,
trading-decision, candidate or performance claim was made anywhere in this work.

[`emergency_whole_accounting.py`](../../../trader/engine/emergency_whole_accounting.py)
extends the existing archived, FLAT `emergency-accounting-intent.v1` record to
whole-trade fills and signed funding, reusing `trade_accounting.py`'s bounded
history/order/fill verification unchanged. There is no `trade-booking.v1` chain
for an emergency exit, so the per-leg breakdown is an explicit, labelled DERIVED
reconstruction, rebuilt fresh on every capture and replay — never cached, never
written back to the journal, never claimed as an actual booking. A verified
result can carry the trade's full USDT arithmetic (gross, fees, signed funding,
net) but is hardcoded `learning_eligible: false`; this module never calls
`cognition.outcomes` and is not wired into any automatic worker or memory import.
Its CLI ([`capture_emergency_whole_accounting.py`](../../../scripts/capture_emergency_whole_accounting.py))
runs a coherent `BEGIN`-snapshotted overlap scan across journal trades, other
archived emergency intents and any active recovery entry for the same symbol;
an omitted or malformed scan — wrong type, negative count, truncated, unparseable
timestamps, bad chronology, non-canonical symbol form, active recovery for the
same symbol (even sharing this intent's own id) — refuses rather than defaulting
to "no conflict found."

[`native_exit_provenance.py`](../../../trader/engine/native_exit_provenance.py)
maps a journal-referenced protective algo ID to its actual executed order and
fills: `algoId -> fapiPrivateGetAlgoOrder -> actualOrderId -> fapiPrivateGetOrder
-> fetch_my_trades`, against the [official field contract](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade#query-algo-order-user_data).
The embedded reference is the trade's own row (including `sl_order_id`, which
`booking.TRADE_FIELDS` excludes), not a loose caller-supplied order id, and is
revalidated on every replay, not just at capture. All order/algo IDs are parsed
as canonical positive integers (`canonical_id()`); zero, negative, bool and
non-numeric values refuse rather than compare as present. Only `algoStatus ==
'FINISHED'` counts as terminal-successful — `TRIGGERED` alone is not execution
proof. This is a full-quantity leg only: **partial native exits are explicitly
unsupported and retry**, and `whole_economics` is always the literal string
`'unknown'` — there is no `learning_eligible` key anywhere in this module's
output. `replay()` proves internal consistency with what `capture()` would have
produced from the same raw fields; it is not a cryptographic venue attestation.

## Test evidence

Final-source targeted run (the same 8 files from the approved brief plus the
new CLI-scan test file): **246 passed, 0 failed, 8.93s**. Full `-v` log:
[`2026-09-17-exit-provenance-targeted-tests.txt`](../artifacts/execution-accounting/2026-09-17-exit-provenance-targeted-tests.txt).
Implementation detail and iteration history: `/tmp/m81-exit-provenance-result.md`.

**No clean full-suite claim is made.** An earlier full-repository run showed
1948 passed / 5 failed; those 5 were isolated and independently re-run by the
coordinator: **5 failed, 10 passed, 1.11s**
([log](../artifacts/execution-accounting/2026-09-17-exit-provenance-unrelated-failures.txt)).
Exact failing tests: `test_cognition_contracts.py::test_cognition_imports_only_stdlib_and_itself`,
and in `test_partial_close_shrinks_notional.py`:
`test_half_the_position_is_half_the_notional`,
`test_notional_tracks_entry_price_not_the_exit_fill`,
`test_the_callers_dict_is_updated_too`, `test_heat_halves_with_the_position`.
Cause, for the record: `trader/cognition/outcomes.py` imports
`trader.engine.trade_accounting`, which is outside that package's own old
import allowlist; separately, the partial-close tests' `FakeEx.create_order`
returns no filled quantity and has no fill-lookup method, so unchanged
`executor.py` `close_partial` (lines 413–422) correctly refuses with
`partial_fill_unconfirmed` and returns `False` with the position unmodified —
the tests do not simulate a confirmed fill. Both are in code this slice never
touched. This is confirmed as **currently sitting in unchanged paths, not
proven pre-existing** — no baseline rerun before this task exists to compare
against. Do not fix either here; they are a separate follow-up.
[Structured reason-code log](../artifacts/execution-accounting/2026-09-17-exit-provenance-troubleshooting.jsonl).

## Runtime and live evidence

[Verification manifest](../artifacts/execution-accounting/2026-09-17-exit-provenance-verification.json),
verified **2026-09-17 01:41:36 UTC**. Runtime artifact filename carries a
misleading `2026-09-16` prefix (reused helper); its embedded `observed_utc`,
**2026-09-17T01:29:49.151515Z**, is authoritative: ACTIVE/demo, unchanged
kernel PID 300976; XRP short 68.9 (`pos_3f5e7f308c`), LINK short 36.24
(`pos_0136aef3df`), AAVE short 0.9 (`pos_57c828f65a`) match the journal with
native protective stops; no watchdog-off; population collection enabled;
`research.referee=false`/`research.handoff=false`; all frozen hashes match.
Emergency-archive and normal-booking counts are both zero. Investigation
health is `degraded` from prior exact-source refusals (unrelated to this
slice); attention health is `ok`.

Three live demo native-exit captures against the current protective stops —
`2026-09-17T01:35:16.477Z`, `01:35:32.565Z`, `01:35:57.332Z` — all returned
`errors: []` and `algo_not_triggered_retry` (`algoStatus=NEW`, `actualOrderId`
empty): no natural exit has occurred yet, so the triggered-and-filled path
remains unverified against a live response. Confirmed against the
[endpoint smoke](../artifacts/execution-accounting/2026-09-17-native-algo-endpoint-smoke.json)
taken earlier the same session (initial verification 00:58:08 UTC, smoke
01:03:14 UTC): 3 algos, all `NEW`, `actualOrderId` empty. Synthetic fixtures
were saved **01:36:25 UTC** (their internal clocks are deliberately epoch —
no live clock was altered): emergency whole-accounting nets **6 USDT** (gross
10 − fees 3 − funding 1); native synthetic leg verifies with `whole_economics:
unknown`. Six frozen prior artifacts were re-replayed with unchanged hashes.

## Parallel M3.1 population state (unrelated to this slice, recorded for continuity)

At **01:07:05.242 UTC**: 33 forecasts, 16 mature (15 `exact_source_version_missing_retry`,
1 `exact_outcome_missing_retry`), 17 immature. **17 raw missing-version retries**
in total — all 16 original (15 resolved + TAO) plus BZ/USDT, whose own maturity
is 04:00 UTC. This raw scan check does not establish whether independent
retained receipts can resolve it; it is a distinct question from losing a
receipt outright. Cohort: 15 unresolved, 2 selected, 13 ignored, 1 dependence
group, 0 terminal. Index/export 30/30 and investigation 15/15, 14 natural scans
each; TAO absent from both — no coverage/search claim. Next maturities: 2 at
04:00 UTC, 15 at 08:00 UTC, investigations 16:00 UTC; full-window review
September 18 00:00 UTC. No backfill/alteration of frozen artifacts; no Gate 2
or M3.2. See [review](../artifacts/pit-dataset/2026-09-17-exit-provenance-review.json),
[capture](../artifacts/pit-dataset/2026-09-17-exit-provenance-capture.json) and
[maturity](../artifacts/pit-dataset/2026-09-17-exit-provenance-maturity.json).

## Exact next work

Observe naturally occurring bookings/emergency archives/native exits as they
happen. Use the existing automatic worker for normal bookings; use the two new
manual commands here only to capture/replay scoped emergency or native evidence
once such an event actually occurs. Once a complete normal whole-trade receipt
exists, verify the already-delivered explicit forward-only import — do not
rebuild it. No forced trade or conversion, and no market-price workaround: the
owner reports no actual conversion history in the past 3 months, which remains
an evidence blocker, not something to route around. Legacy ambiguous-ownership
and source-loss cases remain unknown. Folding emergency/native paths into the
automatic worker or into whole-trade memory is a future, separately reviewed
design — not silently considered done by this delivery.

## Docs changed in this pass

`docs/superpowers/reports/2026-09-17-exit-provenance-delivery.md` (this report),
`docs/NEXT_SESSION.md`, `docs/superpowers/plans/luffy-delivery-checklist.md`.

Subsequent owner-authorized temporary trading is documented separately in the
[controlled demo sample](2026-09-17-controlled-demo-sample.md). Earlier no-order
statements here describe the implementation/verification phase, before that sample.
