# LUFFY-CAPACITY-EVIDENCE-CAPTURE-R1 — capacity evidence capture

- Branch: `luffy-capacity-evidence-capture-r1` (worktree `/mnt/luffy-data/luffy/workspaces/capacity-evidence-capture-r1`), base `luffy-strategy-capacity-r1` b35a4bc. Uncommitted.
- Verdict: **PASS**. Evidence capture TESTED; liquidity capacity model UNAVAILABLE (by design); effective capacity UNAVAILABLE.
- Trading behavior changed: **NO**. Kernel code changed only to persist what existing reads return; not deployed.
- Shadow: optional public depth collector ran 12 cycles (192 snapshots, 0 rejections) in `/mnt/luffy-data/luffy/shadow/capacity-evidence-r1-20261001`, then was STOPPED at the owner's request (stop_file). Its data is kept.

This package creates no liquidity/capacity model, participation rate, impact threshold or retention limit.

## 1. Available margin — `account-margin-observation.v1` (`trader/engine/evidence_capture.py`)

Source: the **same** `GET /fapi/v3/account` response `Kernel._fetch_balance_fresh` already reads Risk's equity (`totalMarginBalance`) from. Binance documents a top-level `availableBalance` in that response. The Kernel now keeps that response's bytes, the unsigned URL and its timing (never the query, signature or key). `_risk_step` writes one record per cycle to `state_kv.account_margin_observation`.

- Fields: value (exact venue string plus float), asset `USDT` (labelled as the same denomination `account_observation` uses; multi-assets mode is not disclosed by the response), venue, market_type, environment (from a known-host table), endpoint, request_start / received / observed ms, response SHA-256 and byte count, the `totalMarginBalance` from the same response, and `observation_id`.
- If the field is absent: `UNAVAILABLE / NOT_PRESENT_IN_EXISTING_AUTHORIZED_ACCOUNT_EVIDENCE`. Other outcomes: `NO_AUTHORIZED_ACCOUNT_RESPONSE_THIS_CYCLE`, `ACCOUNT_RESPONSE_MALFORMED`, `AVAILABLE_BALANCE_MALFORMED`, `ACCOUNT_REQUEST_URL_UNRECOGNISED`.
- No new request. A test counts exactly one account GET per `_risk_step`, and a static check confirms `trader/kernel.py` still has exactly two `requests.get(` calls.
- The capture is wrapped in its own try block. The full-suite comparison caught a regression: in an earlier draft, a response object without `.content` pushed the equity read onto the wallet fallback. This is fixed, and the existing `test_real_balance_paths_record_basis_and_completion` passes.
- Production status: **not observed yet**. The kernel is not redeployed, so field presence in the live demo response is unverified. The code records whichever case occurs.

## 2. Venue filters (`core/instrument_registry.py`, `data/binance_usdm_registry.py`)

`OrderConstraints` adds these fields from public exchangeInfo only: `maximum_quantity` (LOT_SIZE maxQty), `market_minimum_quantity`, `market_maximum_quantity` and `market_quantity_step` (MARKET_LOT_SIZE). The existing fields are min qty, step, min notional, price tick, status and contract type. Absent filters stay `None`. exchangeInfo publishes no maximum-notional filter, so it is recorded as `NOT_PUBLISHED_IN_EXCHANGEINFO`. Snapshot ids change for new snapshots because the canonical payload now has more fields. No stored id is recomputed anywhere, so nothing breaks.

## 3. Leverage brackets — UNAVAILABLE

No existing authorized path keeps them. ccxt calls `/fapi/v1/leverageBracket` inside `fetch_positions`, but `load_leverage_brackets` keeps only `[notionalFloor, maintMarginRatio]` per tier and discards `initialLeverage` and `notionalCap`. No Luffy code reads the raw response. No credential or request was added. The capacity dimension carries this as `detail`.

## 4. Account eligibility — UNKNOWN (unchanged)

Registry records remain `Eligibility.UNKNOWN`. Listing, venue `TRADING` status, account-global `ENABLED` and configured symbols are not converted to eligibility, and a test asserts this.

## 5. Venue position snapshot — `venue-position-snapshot.v1`

This is the verified `portfolio.observation.v1` that `_detect_exchange_exits` already builds from `fetch_positions()`, embedded unchanged so its `observation_id` still verifies. It adds venue, market_type, per-position symbol/side/quantity, the venue's `info.entryPrice` string (mark price is intentionally not persisted), observed/received/request ms, source identity, completeness, the basis "venue position response; not the journal", and `snapshot_id`.

- Latest: `state_kv.venue_position_snapshot`.
- History: append-only `venue_position_snapshots` (UPDATE/DELETE abort). A row is added only when the venue book differs from the last row, so storage is bounded by book changes rather than the per-symbol-per-cycle read rate (about 16 reads/min).
- Freshness is judged at use against `protection_snapshot.STALE_AFTER_S`.
- Reconciliation semantics are unchanged: `_detect_exchange_exits` only additionally keeps the rows it already had.

## 6. Order-book depth — shadow collector

- `trader/observability/depth_evidence.py` validates and stores. `scripts/capacity_depth_shadow.py` runs the collector and fetches through the existing hardened public fetcher (no auth, no proxy, no redirect, bounded bytes).
- Record: symbol, instrument_id, market_type, environment, exact bid/ask `[price, qty]` strings, level counts, configured depth, `lastUpdateId`, venue `E`/`T`, request/receipt ms, endpoint, response SHA-256 and bytes. Rows are immutable.
- Rejected (telemetry only, never depth): malformed JSON or shape, missing ids or times, malformed or non-positive levels, unordered or crossed book, more levels than requested, `|receipt − E| > max_age_ms` (5000 ms collector validity bound), fetch or HTTP failure.
- Bounds: ≤16 symbols (hard cap 32), depth 20, 60 s interval, 72 h, 4400 cycles, 2 GiB DB, STOP file, SIGTERM.
- No safe-size or participation computation.

## 7. Execution / slippage — `execution-evidence.v1` (`trader/engine/execution_evidence.py`)

A read-only model over `trade_legs` / `trade_fills` / `entry_identity_json`. It produces one row per Luffy order with a venue order id in a known market scope. Each row has: strategy/version as frozen at entry, instrument, side, requested/booked qty, recorded reference price and basis, fills attributed by exact order id, VWAP, commissions per asset (kept apart from price), side-signed slippage in bps (positive = adverse), timestamps and `measurement_id`.

A missing reference gives `slippage = UNAVAILABLE / NO_REFERENCE_PRICE`, and the price is never filled in. Rows with incomplete fills are labelled `MEASURED_ON_PARTIAL_FILLS`. There is no aggregation.

Production journal (read-only query, 2026-10-01): `trade_legs` = 0 rows and `trade_fills` = 0 rows. Trade provenance recording is not deployed on main, and the last trade opened 2026-09-26. **0 measurements exist yet.**

## 8. Storage (measured)

| Source | Measurement |
|---|---|
| depth snapshot (depth 20) | 1,621 B on disk / 972 B payload per snapshot (96 snapshots, first 6 cycles) |
| cadence | 16 per 60 s cycle = 960/h = 23,040/day (cycle takes about 18 s) |
| projection at that cadence | ≈ 37 MB/day; ≈ 112 MB for the 72 h run; ≈ 1.12 GB per 30 days |
| margin observation | one `state_kv` row, overwritten (about 1.2 KB) |
| venue snapshot | `state_kv` latest plus one row per distinct venue book |

`storage.json` in the shadow dir computes the rate as n / (last − first), which reads about 13% high over short spans (first cycle counted with no interval). The cadence figures above are the ones to use.

## 9. Capacity contract (`trader/strategy/capacity.py`)

- ACCOUNT.funding: ESTABLISHED only when the margin record verifies, is `AVAILABLE`, is within `ACCOUNT_STALE_S`, and its `totalMarginBalance` equals the equity the account dimension uses. The result is a margin bound only. No quantity is derived, because that needs the venue's per-symbol leverage setting, which is not captured. `ACCOUNT` becomes ESTABLISHED when this holds.
- PORTFOLIO: uses the supplied observation, or else the persisted snapshot, re-verified.
- VENUE.maximums: ESTABLISHED from MARKET_LOT_SIZE maxQty, which applies to the market orders Luffy sends. LOT_SIZE maxQty is recorded beside it.
- VENUE.leverage stays UNAVAILABLE and account_eligibility stays UNKNOWN.
- LIQUIDITY: **UNAVAILABLE** (`NO_REGISTERED_LIQUIDITY_CAPACITY_MODEL`). Depth or slippage evidence passed in is ignored.
- `KV_KEYS` adds both new records, so newer evidence supersedes older receipts.
- **Effective capacity: UNAVAILABLE.** Remaining missing items: liquidity model, leverage brackets, account eligibility.

## Tests

- `tests/test_capacity_evidence_capture.py`: 33 passed, covering every item in the package test list.
- `tests/test_strategy_capacity.py`: the venue test was updated to the new maxima semantics.
- Focused current-truth, capacity and evidence set: 331 passed.
- Full suite: 75 failed and 16 errors, with node-ID sets **identical** to base b35a4bc (all pre-existing, several from files missing in the worktree). The one failure mine introduced was found by this comparison and fixed (§1). `test_registry_provider.py::test_provider_wiring_is_confined_to_attention_integration` also fails on the base (forced_liquidation_recorder) and is unchanged.

## Not done / boundaries

No liquidity model, no participation or impact rule, no Risk, leverage, execution or config change, no production restart, intelligence shadow (PID 2949543) untouched, no frontend. `research.referee=false` and `research.handoff=false` are unchanged.
