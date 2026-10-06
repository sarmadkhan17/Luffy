# DATA-06 — current-build closure (account eligibility explicitly UNKNOWN)

Scope: architecture/Attention build phase, offline. Owner decision: private per-account
eligibility and leverage verification is deferred to future live-activation readiness.

Engineering commits (reused, not re-run in this reconciliation):
- `a186b16` — universe scan records an explicit reason code for every excluded USDT ticker.
- `91fab98` — durable append-only registry snapshot, refresh, universe-revision and untradeable history.

## Closing-condition proof
- Registry refresh records market capabilities: public exchangeInfo -> immutable `RegistrySnapshot`
  (constraints, listing, status); failed refreshes keep the last good snapshot and are logged.
- Eligibility/version history: `registry_snapshots` / `registry_contents` / `registry_refreshes`
  (byte-exact `snapshot_json`, hash-verified, corruption detected, environment-scoped, append-only).
- Explicit exclusions: `universe_revisions` store members, reason codes (blacklisted,
  runtime_untradeable, ticker_invalid_or_identity_unverified, below_min_volume, below_min_price,
  listing_too_new_or_unverified, below_top_n_rank), selection config, revision id, majors_unobserved.
- Restart-safe refusals: `universe_untradeable` per environment; a demo refusal is not a production fact.
- No permanent top-N / guessed leverage / environment substitution: top-N recomputed from venue
  tickers each rescan; `VenueTarget` rejects host/environment mixing; `observe_leverage` and
  `capability()` refuse missing or mismatched evidence.
- Attention compute subset stays distinct: `registry_selector.select` is pure, never touches the
  universe, and carries eligibility as recorded state only.

## Account eligibility semantics
Stored and observed account eligibility is UNKNOWN with no eligibility basis. UNKNOWN never authorizes
trading or exposure. Majors stay observable; absence from venue truth is recorded and never implies tradable.
Entry capability / Risk fail-closed behavior is unchanged (verified: no capability, or an UNKNOWN-eligibility
record, cannot pass `entry_authority.capability`).

## Tests (recorded at 91fab98)
- `tests/test_data06_universe_truth.py`: 17 passed.
- 268 passed across universe/attention/identity/entry-authority/EXE-01 suites; two pre-existing HEAD failures
  excluded (`test_observer_attention_repair::test_normal_outcome_capture_nullable_metadata_and_bad_capture_isolation`,
  `test_final_audit_feed_identity::test_frozen_kernel_cycle_reaches_exit_and_successful_heartbeat`).

## Not wired / limits
- No kernel code constructs `BinanceUsdmRegistryProvider` yet; the store and `recorder` hook exist and are
  tested, production refresh writes begin when the provider is wired.
- Each refresh is a new stored version (records carry their own observation time), ~3 MB/day at 4h cadence.

## Future live gap (mandatory before any ACTIVE trading)
A private account producer is still required for: per-symbol `ACCOUNT_SYMBOL_PERMISSION` eligibility,
`account_trading=ENABLED`, and venue-observed leverage/bracket evidence via `observe_leverage`, registered as a
fresh `entry-capability.v1` receipt. Until it exists no live entry can pass the capability gate.
