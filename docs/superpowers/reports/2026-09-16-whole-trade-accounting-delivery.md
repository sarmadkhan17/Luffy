# M8.1 whole-trade reconciliation and completeness gates

September 16, 2026. **232 final-source tests passed in 13.60 seconds**, including
37 new whole-trade tests and the prior 17-module accounting/recovery/protection,
typed-memory and population regression set. M8.1 remains **partial**, **12/55**.
This delivers a manually invoked accounting tool and verified-outcome bridge,
not complete observed accounting or an automatic accounting service.

## Delivered behavior

[Reconciler](../../../trader/engine/trade_accounting.py) replays the deployed
immutable booking receipts, checks their identity and before/after chain, and
joins the entry and every reduction/final exit to terminal venue order versions.
Actual fill quantities must match every leg and balance to flat. Complete symbol
history may contain no unowned fills, journal ownership may not overlap, and a
venue one-way flat snapshot is required. Fill prices must reproduce venue gross
P&L; all entry commissions are included. Open trades, legacy entries without
receipts, native/ambiguous exits without an exact order reference, reused order
IDs, nonterminal orders and conflicting observations remain retryable unknowns.
No legacy numeric `realized_pnl` is promoted or overwritten.

Three bounded histories are retained: account fills, signed funding income and
published funding events. Requests use explicit inclusive time bounds, a
conservative 80-day retention limit, six-day windows, at most 16 pages per
history and at most 32 owned orders. Saturated windows split recursively without
skipping same-timestamp records. Missing pages, conflicts between parent/child
responses, saturated single timestamps, fetch failures and exhausted budgets
remain explicit retries. Error responses retain exception types, not request
credentials. Invalid numeric responses remain serializable missing evidence.

Funding completeness additionally requires published events bracketing the
entire filled trade, including an event strictly after the final fill. Each event
with owned exposure must have exactly one actual signed cashflow. A missing
cashflow, ambiguous fill/event boundary or income without owned exposure refuses
completion. An empty income page alone cannot establish zero. Zero funding is
accepted only with complete bounded income/event history and no intervening
funding event. These are **as-of venue-history checks**, not a guarantee against
later venue corrections. Public rates identify settlement events; rates are not
used to invent cashflows.

Fees and funding retain separate currency subtotals. Only USDT cashflows can
currently produce a scalar net trade result. Non-USDT fees/income have explicit
`currency_conversion_missing_retry`; no FX rate or zero conversion is invented.
Net trade P&L means fill-realized P&L minus commissions plus signed funding. It
excludes infrastructure, LLM/service costs and separate rebates/other income;
there is no complete operating-economics or rent-coverage claim.

[CLI](../../../scripts/reconcile_trade_accounting.py):

```bash
./venv/bin/python -m scripts.reconcile_trade_accounting --trade-id ID --output /tmp/NEW-whole-trade.json
./venv/bin/python -m scripts.reconcile_trade_accounting --replay /tmp/NEW-whole-trade.json
# Only after a complete receipt exists; optional explicit typed-memory import:
./venv/bin/python -m scripts.reconcile_trade_accounting --replay /tmp/NEW-whole-trade.json --import-memory data/investigation.db
```

Capture is demo-only and read-only; it writes a new file exclusively. It submits
no orders and does no network work in the kernel safety path. Retry into another
new filename. The old normal and emergency capture/replay formats still work.
The optional memory bridge calls the existing verified-execution adapter only
for complete receipts and embeds the raw capture in the typed outcome. Typed
replay reconstructs attribution, quantities, cashflows and completeness. Memory
imports require an existing forward activation boundary and use a separate
`verified-trade:` key; earlier unknown cases are not rewritten. Reimport of the
same artifact is idempotent; conflicting terminal artifacts are refused. Existing
payload limits still apply. No live memory import was performed in this session. The frozen PIT builder
explicitly excludes this new raw-capture source as `unsupported_record_kind`;
adding it to research requires a separately reviewed producer/protocol, not a
change to the active frozen population collection.

## Evidence and deployment

- [Verification and hashes](../artifacts/execution-accounting/2026-09-16-whole-verification.json):
  232 passing tests; not the full repository suite. Previous 195-test evidence
  remains valid for its recorded source. Do not add overlapping totals.
- [Synthetic capture](../artifacts/execution-accounting/2026-09-16-whole-synthetic.json)
  and [typed outcome](../artifacts/execution-accounting/2026-09-16-whole-synthetic-outcome.json):
  entry, partial and final fill; negative funding and all three fees produce
  fixture net 25 USDT. Source-free CLI and embedded typed replay passed. This is
  synthetic arithmetic, not observed performance.
- [Legacy retry capture](../artifacts/execution-accounting/2026-09-16-whole-legacy-retry.json):
  existing XRP demo trade remains `legacy_entry_receipt_missing_retry`, with null
  net/funding and learning disabled. It does not create a retrospective booking.
- [Demo endpoint smoke test](../artifacts/execution-accounting/2026-09-16-whole-endpoints.json),
  22:41:31 UTC: six-day XRP queries enumerate four fills, three funding-income
  rows and 18 funding events; V2 returns an explicit flat BTC one-way position.
  This proves the read endpoints work in demo, **not whole-trade attribution**.
- [Runtime](../artifacts/execution-accounting/20260916T223504Z-whole-runtime.json),
  check begun **22:35:04 UTC**: kernel remains PID **300976**, ACTIVE/demo; XRP
  short 68.9, LINK short 36.24 and AAVE short 0.9 each match journal ownership/size
  and native reduce-only STOP_MARKET protection. Heartbeat age 41 seconds.
  No pending entry recovery; watchdog enabled. Both population consumers ran
  naturally at 22:35:01 UTC with hooks enabled. Owner access at
  `192.168.126.131:8080`: unauthenticated API 401, login/API/cookie dashboard 200.
  Both accounting ledgers still have zero forward receipts. No trade was forced.

[Final read-only state](../artifacts/execution-accounting/2026-09-16-whole-final-state.json),
**22:51:05 UTC**: same kernel PID, ACTIVE, heartbeat age 34.5 seconds, no pending
recovery, watchdog enabled and zero accounting receipts. Both consumers ran at
22:50:01 UTC. Both local population indices retain one activation receipt; frozen
hashes match the earlier runtime capture. This is not in-window coverage evidence.
[Troubleshooting](../artifacts/execution-accounting/2026-09-16-whole-troubleshooting.jsonl)
records the Claude/sandbox limitations and retained missing-data cases.

The prior emergency/normal kernel slices remain demo-deployed from 22:09 UTC.
**No kernel/dashboard restart or control/config change was needed for this manual
CLI slice.** All previously verified execution/recovery source hashes match.
The typed replay addition is available to new consumer processes; there is no
scheduled whole-trade capture/import or new live outcome. Collection and existing
watchdog scheduling are unchanged. Frozen files and indices are preserved.

## Legacy P&L consumer review

These readers were inspected, not migrated. Changing their inputs is a separate
behavioral change and must preserve deterministic risk and existing admission
boundaries.

| Consumer | Current evidence problem / remaining work |
|---|---|
| `strategy/blend.py` | Performance weighting uses numeric journal P&L. Introduce an explicit verified-accounting input and neutral handling for unknowns before treating it as net economics. |
| `strategy/promotion.py` | Legacy lifecycle PF, wins and streaks use journal P&L, including zero defaults. Preserve the distinct spec-admission path; migrate only with reviewed unknown-data behavior. |
| `research/runner.py:_brake` | Research forward brake reads journal P&L. Remains behind the recorded research stop; do not restart Gate 2 to test it. |
| `dashboard/server.py`, `api/graphql_schema.py`, `chat/{engine,tools}.py` | Summary/display values remain legacy booked numbers, not verified whole-trade net. Need explicit accounting-basis labels and verified/unknown breakdowns. |
| `knowledge/vault.py`, `brain/postmortem.py` | Narrative rollups and wins use the same legacy values. Must not present them as reconciled economics. |
| `engine/rent.py` / `rent_keeper.py` | Separate venue-income ledger, not journal P&L. Currency aggregation is not gated, missing income defaults to zero, and saturated equal-timestamp pagination can advance past records. Requires its own completeness/currency repair; this slice does not validate rent PASS/FAIL. |
| `engine/risk.py` | Daily breaker/drawdown track marked equity, not this new trade receipt. Leave deterministic limits and equity safeguards unchanged. |
| Existing typed journal imports / PIT dataset | Legacy accounting remains unknown/excluded. The new explicit verified receipt path does not overwrite those cases or frozen datasets. |

## Exact remaining work and scheduled observation

1. Observe the first naturally occurring booking; export/replay it to a new file.
   For a naturally closed trade with a complete entry receipt, run the new capture,
   retain every incomplete attempt and retry after the funding publication frontier
   or missing venue evidence arrives. Do not force a trade. Current legacy open
   positions may never acquire complete entry provenance and must stay unknown.
2. Verify a naturally complete receipt and explicit memory import. Add a bounded
   off-path automatic retry/capture service only as the next engineering slice;
   the current CLI has manual retries and no scheduled queue. Preserve immutable
   terminal records and source availability clocks.
3. Extend explicit conversion evidence for non-USDT currencies; complete emergency
   funding/ownership integration and native/ambiguous exit provenance. Handle
   concurrent/reopened symbols without weakening current conservative refusals.
4. Migrate the reviewed reporting/economic consumers with explicit unknowns;
   separately review decision-affecting consumers. Include other cashflows and
   operating costs before claiming complete economics.

At the final **22:51 UTC September 16** check, September 17 00:00 UTC was still in the future.
The first in-window M3.1 observation and exact forecast maturity checks were
**premature**, and the September 18 00:00 UTC full-window review was also premature.
Keep M3.1 collection unchanged. After opening, verify naturally scheduled receipts
and capture/replay to a new file. At/after the cut, reconcile **both receipt
indices**, eligibility/selection reasons, missed registrations, unresolved/expired
cases and **all classification-neutral terminal outcomes**. Rerun exact outcome
verification when mature and preserve `missing_versions_retry`. No prospective
backfill, M3.2 search, Gate 2 restart or coverage acceptance. A separate frozen
search protocol is still required. Referee/handoff remain false.

Claude CLI was checked and remained session-limited; direct implementation used
the existing authorization. No trading-selection/risk change, new candidate,
admission, improved-performance claim or roadmap dependency change.

API contract references, checked during implementation: Binance
[account trade history](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade),
[income history](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account),
and [funding event history](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data).
