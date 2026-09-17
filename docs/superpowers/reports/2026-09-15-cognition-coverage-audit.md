# Cognition coverage audit — 2026-09-15

Design and review: Astra/Codex. Implementation: Claude. Progress assessment: Luna.
Status: fixed-window experiment completed; wider evaluation remains deferred.

## Result

All **940 missing 4h candles across 42 symbols** were retrieved from Binance
USD-M production public REST into a separate research artifact. All 42 responses
passed validation, and all 42 overlap bars matched the original OHLCV exactly.
No original candle was replaced. No production database was read or written by
the audit tool, and no live trading behavior was changed.

This establishes present source availability of every missing key. Together with
the read-only cycle/refresh diagnosis, it strengthens the storage-selection
explanation. It does **not** establish historical arrival times, historical
membership, the exact last fetch reason, or same-source provenance of all original
rows. Only one overlapping bar per affected symbol was compared.

## Scope and preservation

- Original: `/tmp/luffy-history-20260915`; original report remains unchanged.
- Separate audit: `/tmp/luffy-coverage-audit-20260915`.
- Original 63-symbol membership, 84 decision times, 4h timeframe, warmup,
  resolve deadline and full configuration preserved.
- Grid: 2026-08-26 16:00 UTC through 2026-09-14 20:00 UTC, 116 bars per symbol.
- Decisions: [2026-08-31 00:00 UTC, 2026-09-14 00:00 UTC).
- Resolve until: 2026-09-15 00:00 UTC; config_id `cfg_fe9a78632b92e8d3`.
- Five original files copied byte-for-byte into `baseline/`; independently
  verified identical after fetch. All original 6,368 candle records are preserved
  exactly as the prefix of the repaired fixture.
- Research DB: 7,308 rows, 63 symbols. Unknown original taker_buy stays SQL NULL.
- All missing keys accounted once: recovered 940; absent, failed, invalid and
  not attempted all zero. No retries, fallback providers or symbol aliases.

## Commands and source evidence

```bash
./venv/bin/python -m scripts.cognition_coverage_audit plan \
  --original-dir /tmp/luffy-history-20260915 \
  --output-dir /tmp/luffy-coverage-audit-20260915
./venv/bin/python -m scripts.cognition_coverage_audit fetch \
  --audit-dir /tmp/luffy-coverage-audit-20260915
```

The existing directories are exclusive artifacts: these commands refuse to
replace them. Use a new output directory for a later independent experiment.

Endpoint: `https://fapi.binance.com/fapi/v1/klines`, interval 4h, fixed per-symbol
start/end ranges, unsigned public GET. Maximum requested range 25 bars, limit
100, 20-second timeout, no retries. Actual acquisition:
2026-09-15T14:56:29.017Z through 2026-09-15T14:57:09.064Z.

Source contract references: Binance's official
[USD-M connector](https://github.com/binance/binance-futures-connector-python/blob/main/binance/um_futures/market.py#L118)
and [production base URL](https://github.com/binance/binance-futures-connector-python/blob/main/binance/um_futures/__init__.py#L3).
The audit records the actual raw responses and validates their 12-field shape,
timestamps, closed-bar bounds, finite OHLCV, and duplicates before accepting rows.

`fetch/raw/` holds response bytes; `requests.json` holds URL, parameters, status,
retrieval times and body hashes; `ledger.json` identifies every missing key and
its request. `manifest.json` hashes output files, excluding itself.

## Original versus repaired replay

| Measure | Original | Repaired |
|---|---:|---:|
| Present candles | 6,368 | 7,308 |
| Missing candles | 940 | 0 |
| Eligible decision-symbol rows | 4,642 | 5,292 |
| Stale decision-symbol rows | 650 | 0 |
| Selected / episodes | 241 | 251 |
| Unresolved outcomes, all types | 921 | 0 |
| Relative-baseline cohort_incomplete | 383 | 0 |

Selection changed: 27 `(decision time, symbol)` pairs occur only in the original,
37 only in the repaired run, and 214 in both. This is sensitivity to missing
observations, not evidence that selection improved. Detailed original/repaired
baseline descriptive statistics and row changes are in `fetch/comparison.json`.
No subset was selected, no parameter was tuned, and no window was widened.

The tool reproduced the original trace byte-for-byte before replaying repaired
input, then ran the serialized repaired fixture twice with identical trace bytes.
Original availability assumptions remain; added rows use `available_ms=close_ms`
as an explicitly retrospective simulation assumption. Actual retrieval timestamps
are recorded separately. This is not a verified point-in-time replay.

## Verification and review

- Existing baseline: 73 tests passed independently in 2.99 seconds.
- Final combined cognition/history/audit suite: **114 passed in 9.90 seconds**.
- New regression coverage includes overflow/nonfinite values, malformed and
  duplicate rows, redirects, bounded response reads, rate-limit stops, preservation
  failures, evidence surviving DB/replay failures, and deterministic fake fetches.
- Codex reviewed and returned fixture, numeric validation, evidence persistence,
  and original-file preservation defects to Claude; fixes passed before fetching.
- Luna reviewed the completed manifest/comparison and found no blocker to closing
  this fixed-window audit; retrospective provenance limitations still apply.
- Parent independently checked all recorded output hashes, original/copy byte
  equality, original candle preservation, membership/decision equality and DB counts.
- Original run, original source/test files and pre-existing workspace changes were
  preserved. Only the new audit script/tests/specification/report were authored.

## Identity (file-byte SHA256)

- Fetch manifest: `ef861f3907e297064e9276e1cba96f46e66543cadf9c1046c9ec09b30abd1ec9`
- Repaired input: `6101ad0ca5bc6ecfa76f6be0dcfe0e208df16cb2ef404197f2a0d4d94cb9a80d`
- Repaired trace: `ed4778256493ba9949c6c7a01d6a07ff768f095e0e9b2392a7302293ec10a950`
- Comparison: `2424a35038bb2991826829f88e6c1644e34632016e9beb87b5ac191ab8d2351f`

The canonical repaired trace hash is
`a53aae289df9d5062fc986411f5c01d3ab091bfd13050fda222bfba357592ea1`;
it differs from the file-byte hash by definition of serialization.

## Next boundary

This fixed-window coverage experiment is complete. A continuing research
collector would need freshness requirements independent of the live scan
universe; that is a separate design. Before wider evaluation, predeclare the
new window/cohort and source policy, retain the original and repaired runs
alongside it, and keep arrival/provenance limitations explicit. No claim about
scanner quality, significance, predictive edge, PnL or profitability follows.
