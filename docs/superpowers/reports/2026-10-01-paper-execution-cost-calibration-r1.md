# LUFFY-PAPER-EXECUTION-COST-EVIDENCE-CALIBRATION-R1

PASS / OUTCOME B: INSUFFICIENT_EVIDENCE. The evidence inventory, deterministic primitives and fail-closed integration are testable; no production execution-cost method is validated. This is the final Stage-5 cost package for now. Further calibration is PARKED until new real evidence exists. NEXT is LUFFY-PORTFOLIO-ALLOCATOR-R1 (Stage 6 Portfolio Intelligence). Stage 5 remains economically incomplete.

Worktree: `/mnt/luffy-data/luffy/workspaces/paper-execution-cost-calibration-r1`, branch `luffy-paper-execution-cost-evidence-calibration-r1`, exact base `be2bf1860b0c66957d7d3af8c32d6a0e2a55d209`. No commit, deployment or restart. Production checkout and its unrelated frontend/M4/provenance changes are preserved. Local Claude execution returned “Not logged in”; work proceeded with available tools within the authorized package.

## Commission authority

UNAVAILABLE / AUTHORIZED_SOURCE_MISSING. Source inspection found `scripts/read_prod_fee.py` (an authenticated production-key commission query), actual-fill fee capture and generic configuration comments, but no saved applicable connected-account rate receipt. The script was NOT invoked; no account endpoint or additional credential scope was called. Only the nonsecret `BINANCE_DEMO=true` flag was inspected from production configuration. The production-key script's rate prose cannot establish the connected demo account's rate, paper maker/taker, historical applicability, fee asset treatment or executable commission notional. Actual historical fill fees remain evidence for those exact orders only. No commission adapter is registered.

## Funding

The public [Binance funding history authority](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data) describes historical funding timestamps/rates and inclusive request boundaries. `funding_events.lookup` issues only public bounded GETs, stores complete UTF-8 response bodies, hashes, exact symbol/event timestamps, published rates and mark prices, endpoint, schema version and local request/receipt timestamps. Pagination follows actual returned events; no funding schedule is assumed. Replay refuses wrong symbol, timestamp, ordering, hash, incomplete pagination and nonhistorical intervals.

`funding-public-observation.json` preserves one actual bounded BTCUSDT public lookup over 2026-09-30 UTC through 2026-10-01 UTC: three returned historical events. This is an endpoint observation, not a fabricated trade or a paper interval. Published mark prices are retained as source observations; no event-time paper position notional is asserted.

Crossed-event FUNDING remains UNAVAILABLE: no current production versioned paper trade/cost tables or frozen event-time position/mark/notional authority exist. Entry reference or fill price is never substituted for an event-time basis. The production endpoint also cannot establish a demo-market paper funding obligation. Exact trade-boundary events are refused pending venue timing proof.

One restricted registered adapter accepts only proven NO-CROSSING intervals for futures with a frozen `venue_environment=production`, exact trade timestamps/instrument binding, complete raw page replay and an empty inclusive interval (including boundaries). Missing environment is never inferred from current config. Existing paper rows do not capture that environment, so this adapter cannot silently complete them. This prospective integration supports NOT_APPLICABLE only; crossed-event amounts remain unsupported. No runtime network caller, paper-table migration, automatic collection or historical receipt backfill was added. Immutable finalized receipts remain immutable.

## Real execution inventory

`inventory.json` preserves read-only SQLite source inspection plus deterministic replay of the 24 existing hashed accounting receipts in a disposable in-memory database. Production tables `trade_legs`, `trade_fills` and `execution_accounting` contain zero rows. No production versioned paper/cost tables exist. Raw accounting receipts are inspected as evidence; journal-only trade rows are not treated as fills.

Receipt replay proves six unique exact-order venue fills across four orders: SOL/USDT entry buy (one fill), SOL/USDT exit sell (three fills), HYPE/USDT entry buy (one fill), HYPE/USDT exit sell (one fill). Four fills are direct evidence; two additional unique entry fills are retained in the receipts' venue-window observations. All six have fees, and four orders have complete fills relative to booked quantity. Their requested quantities, reference prices, decision timestamps and submission timestamps are unavailable. Recorded `exec_mode=live` belongs to the connected demo operational setup; no real-money execution inference is made.

| Instrument | Receipt legs | Unique exact-attributed fills | Complete booked-order fills / fees | Requested size / reference / decision / submission | Matched calibration orders |
|---|---:|---:|---:|---|---:|
| SOL/USDT | 3 | 4 | 2 / 2 | all 0 | 0 |
| HYPE/USDT | 2 | 2 | 2 / 2 | all 0 | 0 |
| AAVE/USDT | 3 | 0 | 0 / 0 | all 0 | 0 |
| AVAX/USDT | 1 | 0 | 0 / 0 | all 0 | 0 |
| LINK/USDT | 2 | 0 | 0 / 0 | all 0 | 0 |
| NEAR/USDT | 2 | 0 | 0 / 0 | all 0 | 0 |
| SUI/USDT | 4 | 0 | 0 / 0 | all 0 | 0 |
| TAO/USDT | 2 | 0 | 0 / 0 | all 0 | 0 |
| UNI/USDT | 2 | 0 | 0 / 0 | all 0 | 0 |
| XRP/USDT | 1 | 0 | 0 / 0 | all 0 | 0 |
| ZEC/USDT | 2 | 0 | 0 / 0 | all 0 | 0 |

The existing shadow depth DB contains 192 production snapshots: 12 each for AAVE, AVAX, BNB, BTC, BZ, DOGE, ETH, FIL, HYPE, KORU, LINK, LIT, MOVR, NEAR, QNT and SKHYNIX USD-T symbols. Zero snapshots carry decision/intent/order links. Symbol/time joins are not attempted. Event-linked spread/depth, latency, requested-size/depth ratios and volatility regimes are unavailable. Partial-fill and cancellation/rejection coverage relative to requested orders is unavailable; zero measured partial orders does not prove absence of partial execution.

Matched dataset: 0 orders; instruments/sides empty; latency, size and regime coverage UNAVAILABLE. Existing exact execution measurements are retained in the inventory artifact for later calibration/capacity work. No arbitrary minimum N, fitting, validated model, impact inference or capacity authority is created. SLIPPAGE_MODEL=NOT_VALIDATED and protocol SLIPPAGE=UNAVAILABLE.

## Static observation and future capture

`execution_calibration.book_walk` deterministically consumes ordered levels at exact requested quantity using Decimal arithmetic. It returns coverage, full-size book VWAP (null on insufficient depth), top price, adverse static-book price difference, exact levels/quantities consumed and snapshot identity. Every output is labeled STATIC_BOOK_QUOTE / NOT_REALIZED_SLIPPAGE / NOT_MARKET_IMPACT_PROOF. It cannot be frozen as a slippage source.

`execution_shadow.capture` is a single public depth GET linked to caller-supplied existing decision and intent IDs, symbol/environment, side, quantity, event timestamp and phase. It preserves request/response delay and explicitly labels a later snapshot NOT_EXACT_EVENT_TIME_BOOK. It returns a hash-bound envelope for caller-owned shadow storage and starts no process. It has no production caller, storage loop, deployment or execution hook. Its collection bounds and depth freshness setting do not validate any economic rule. Real deployment is needed for prospective runtime capture and remains outside this package.

Exact dataset construction reuses the existing execution read model with stronger market/symbol/order/trade/side/fill-ID checks, rejects duplicate scoped order links and never uses fuzzy attribution. Missing timestamps are not replaced by journal observation time. With no matched dataset, further model calibration stops here.

## Required future evidence and stop

- Applicable connected-account commission receipts with account/environment/time scope, rate/classification and fee currency/discount treatment. Additional authenticated acquisition requires separate authorization; no current-rate retroactivity.
- Frozen paper market-data environment and exact position quantity plus authoritative event-time mark/notional at every crossed funding event; complete funding intervals and venue boundary semantics.
- Existing decisions/order intents with exact immutable IDs, requested quantities, reference price/basis and decision/submission timestamps, linked complete and partial venue fills/fees and terminal cancellation/rejection outcomes.
- Prospectively linked side depth/spread captures around decisions and submissions, keeping actual delays and snapshot IDs; predeclared collection/storage bounds when wired. No retrospectively reconstructed books.
- Measured size/depth ratios, spread/volatility conditions, latency and partial/rejection coverage that support a separately justified validation protocol. No sample-count shortcut.

Calibration is PARKED pending this evidence. No additional Stage-5 cost micro-package. Execution-cost calibration and liquidity capacity remain independent authorities. CAPACITY=UNAVAILABLE, FIRST_LIVE=BLOCKED, PRODUCTION_ECONOMIC_PROBATION=INCOMPLETE. Thresholds 15 trades / 40% win rate / 1.15 net profit factor, exact version/install binding, exit semantics and the existing first-live fences are preserved.

## Verification

Focused tests cover funding page/boundary/instrument/environment refusal and no-crossing protocol replay, static arithmetic and insufficient depth, static-source refusal, exact fill/reference linkage and duplicate-order rejection, read-only inventory, zero/small dataset nonvalidation, public intent capture, economic probation/approval refusal, first-live and Risk/control fences. No full suite. Final test counts and graph update status are recorded in STATE.yaml.
