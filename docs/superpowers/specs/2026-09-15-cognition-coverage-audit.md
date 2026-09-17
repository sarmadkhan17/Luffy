# Cognition coverage audit — fixed-window repair experiment

Design/review: Astra/Codex. Implementation: Claude. Progress assessment: Luna.
Status: implemented, reviewed, 114 tests passing; fixed-window fetch and replay completed. Offline research only. See `../reports/2026-09-15-cognition-coverage-audit.md`.

## Question and scope

Can an explicitly sourced historical pull recover the 940 absent 4h candles
without changing the original 63-symbol cohort, observed rows, decision window,
or cognition configuration? This tests sensitivity to missing data, not scanner
quality, historical tradability, profitability, or point-in-time availability.

The read-only diagnosis found 36 symbols with no journal cycles since September
11 and identical last stored opens at September 10 20:00 UTC (864 missing bars).
The six later tails align with symbols ceasing to appear in cycles (76 missing).
The feed refreshes on demand; research deepening skips series at its bar-count
target without checking freshness. This strongly supports storage selection but
does not identify every historical fetch or prove source availability.

## Immutable baseline

Read `/tmp/luffy-history-20260915` only. Never write beneath it, overwrite it,
or re-export from the changing live database. Pin these file SHA256 values:

- input.json: `910fe6d6aae89977b8569261ba1957885bdae26c86f75fde44a95a85e2bf0f65`
- trace.json: `9aace3fe8e8e40ecfdebd989768894c527e10690ff558b21885fc349aa749449`
- report.json: `b804cc66171c9280fdc9a7cfe65679d8d6ec884da762ed39f40322bce3d5d2a7`

Verify the manifest canonical hashes/config and baseline file hashes before
and after work. Preserve copies of baseline files in the new artifact directory
for durability; do not modify the originals. Record manifest and report.md byte
hashes too. Refuse nonempty destinations, destinations overlapping the original
directory, and destinations inside repository `data/`. Resolve symlinks before
these checks. Existing source, tests, runtime files, and workspace changes remain
untouched. No live DataFeed, kernel, credentials, environment loading, or trading
module imports. No production database access is needed.

## Fixed experiment

- Timeframe: 4h, step 14,400,000 ms.
- Warmup first open: 2026-08-26T16:00:00Z.
- Decisions: [2026-08-31T00:00:00Z, 2026-09-14T00:00:00Z), 84 times.
- Resolve until: 2026-09-15T00:00:00Z; last expected open September 14 20:00Z.
- 63 original symbol keys, 116 expected bars each, 7,308 total, 6,368 present.
- Exactly 940 absent keys across 42 symbols. Derive keys from baseline input,
  reconcile against manifest; refuse drift rather than silently changing scope.
- Copy original membership, decision times, participation and original candle
  records exactly. Frozen baseline configuration must equal current defaults;
  refuse config drift. No subset selection, new symbols, horizon changes or tuning.

## Two stages

New script `scripts/cognition_coverage_audit.py`, tests
`tests/test_cognition_coverage_audit.py`. No changes to existing scripts or package.

1. `plan --original-dir PATH --output-dir PATH`: network-free. Verify baseline;
   emit deterministic missing-key inventory and fixed fetch ranges. Copy baseline
   files, record hashes. One request range per affected symbol from its last
   existing bar through last expected open (one existing overlap bar for audit).
   This is at most 25 bars per request for this experiment. Record symbol-to-venue
   mapping explicitly; only exact plain `BASE/USDT` to `BASEUSDT` mapping allowed.
2. `fetch --audit-dir PATH`: explicit public-network operation. Consume and verify
   the frozen plan and copied baseline. Write into a new exclusive `fetch/`
   directory; never overwrite a prior attempt. On failures preserve evidence and
   report unresolved keys. Use a separate fresh plan directory to retry. Fetch
   only public Binance USD-M production klines, not demo or spot. No provider or
   symbol fallback. Maximum 42 requests, no automatic retries; timeout 20 seconds,
   at least 0.3 seconds between requests, stop requesting on 418/429 and classify
   remaining keys as not attempted. No credentials or signed requests.

Use `https://fapi.binance.com/fapi/v1/klines`, interval=4h, startTime=overlap open,
endTime=last expected close minus 1, limit=100 (the fixed ranges fit one page).
Verify the endpoint/field contract against official documentation before live
execution. Inject transport and clock for tests; tests never access the network.
Refuse redirects to other hosts/endpoints. Bound response bytes (1 MB/request).

Endpoint reference checked 2026-09-15: Binance's official [USD-M connector](https://github.com/binance/binance-futures-connector-python/blob/main/binance/um_futures/market.py#L118) specifies `/fapi/v1/klines` and the request parameters; its [client default](https://github.com/binance/binance-futures-connector-python/blob/main/binance/um_futures/__init__.py#L3) is `https://fapi.binance.com`. The human documentation URL currently redirects to a general landing page, so that page alone is not endpoint evidence.

## Evidence and validation

Save raw response bytes (including HTTP error bodies), SHA256, request URL and
parameters, request/retrieval UTC timestamps, HTTP status, response headers useful
for rate limits, and errors. Record the source venue and mapping per request.
Treat network/API errors, empty successful responses, malformed records, conflicts,
and missing timestamps distinctly. An absent response is not proof of delisting
or a market-calendar exception. No interpolation, aggregation or resampling.

Require integer aligned open timestamps, venue close timestamps equal to
open+step-1, finite positive OHLC, high/low enclosing open and close, nonnegative
volume, and fully closed bars within the exact requested range. Reject malformed
responses audibly in the ledger. Duplicate timestamps, even identical, are a
request validation failure; do not choose a winning duplicate. Refuse wrong
shape, out-of-range or invalid rows as a failed request rather than partial
acceptance of a suspect response. JSON NaN/Infinity are invalid.

For the overlap bar, compare OHLCV field by field with baseline and record all
differences. Never replace the baseline row. A mismatch indicates revision or
provenance disagreement, not confirmed same-source continuity. Track it as a
review flag; fetching other symbols may continue, but do not run a combined replay
until all overlapping OHLCV comparisons match. Missing overlap is also a review
flag. No claim is made about revisions outside this one-bar overlap.

Every one of 940 keys gets exactly one ledger status: recovered, absent_in_response,
request_failed, invalid_response, or not_attempted. Recovered requires a fully
validated response. Ledger counts must sum to 940. Source-calendar explanations
require separate evidence and are not inferred by this tool.

## Separate research artifacts and replay

Write a standalone `research.db` only inside fetch output, containing the 6,368
baseline OHLCV rows plus validated recovered keys; baseline taker_buy is unknown
and stays NULL. Source evidence belongs in the ledger, not invented provenance
for old rows. This DB is an artifact; replay uses the explicit repaired fixture.

Create `repaired-input.json` from baseline JSON, adding only recovered candles
with explicit public-source tags. Record actual retrieval times in the ledger;
`available_ms=close_ms` remains a conspicuous retrospective simulation assumption,
not a claim the bars were available historically. Keep membership unchanged.
Record mixed/unknown original provenance and fetched provenance in metadata.

When there are no overlap review flags, replay with the unchanged configuration
and end_ms using existing cognition APIs. Partial recovery is permitted but must
be labeled incomplete; keep remaining bars missing and unresolved. Replay twice
from serialized repaired input and require byte-identical traces. Compare original
and repaired coverage, stale/eligible counts, selected identities, episode counts,
unresolved reasons, and baseline descriptive summaries. Compare by decision time
and symbol, not unstable generated episode IDs. No significance or edge claims.
If overlap flags exist, still produce raw evidence, ledger and research.db, but
explicitly record replay withheld and its reasons. Do not grade a filtered subset.

Emit a machine-readable manifest and readable report with artifact hashes, source
limitations, complete failure accounting, and scope/preservation checks. Outputs
must make it possible to reproduce the replay without the network or live store.

## Acceptance tests and review

- Baseline pinning, canonical/config agreement, 63/116/940 reconciliation.
- Reject dangerous/overlapping/symlink destinations; refuse existing outputs.
- Plan is deterministic and network-free. Original files remain byte-identical.
- Fake transport covers valid recovery, empty/partial response, HTTP/network
  failure, 418/429 stop, malformed prices/timestamps, duplicates, oversized body,
  redirected request, mismatch/missing overlap, and symbol mapping refusal.
- Exactly 940 ledger records; recovered+remaining=940; no baseline replacements.
- Membership/decisions/config/bounds unchanged; deterministic serialized replay.
- No production DB/trading imports; all network calls disabled in tests.
- Run the existing 73 cognition/history tests plus new audit tests.

Astra/Codex reviews implementation and validation before public fetch execution.
Luna assesses progress and remaining uncertainty. Expansion remains deferred;
completion of this experiment is not itself permission to tune or widen evaluation.

## Review clarifications (Luna assessment, Codex disposition)

- Canonical hashing uses existing `cognition_history.sha256_json`: sorted keys,
  separators `(",", ":")`, `allow_nan=False`, UTF-8. Check both byte and canonical
  hashes. A manifest hashes its other output files, never itself; its byte hash
  can be recorded by a later independent review report.
- Before replay, explicitly verify membership, participation, decision times,
  timeframe, baseline candle preservation, recovered-key bounds and full config.
  Refuse a planning symbol without an existing overlap bar. The pinned baseline
  has at least 92 existing bars for each symbol, so this is a drift refusal.
- Any malformed row invalidates all missing keys requested in that response.
- Research DB schema: `candles(symbol TEXT NOT NULL, tf TEXT NOT NULL,
  ts INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL, volume REAL,
  taker_buy REAL, PRIMARY KEY(symbol, tf, ts))`. SQL NULL, not zero, represents
  unknown taker_buy. Only OHLCV is used by the replay.
- Recovered candle source tag: `binance_usdm_public_rest:historical_retrieval`.
  Metadata must identify retrieval provenance and retrospective availability
  assumptions; actual retrieval timestamps live in the request ledger.
- Comparison summaries use existing `cognition_history.summarize` on each trace;
  coverage uses the original fixed grid. Selected identities are `(as_of, symbol)`.
