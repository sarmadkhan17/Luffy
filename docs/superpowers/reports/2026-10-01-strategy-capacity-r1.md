# LUFFY-STRATEGY-CAPACITY-R1 — strategy capacity contract

- Branch: `luffy-strategy-capacity-r1` (worktree `/mnt/luffy-data/luffy/workspaces/strategy-capacity-r1`), base `luffy-integrated-core-r1` 548ebb7. Uncommitted.
- Verdict: **PASS** (infrastructure TESTED; effective capacity UNAVAILABLE by design).
- Trading behavior changed: **NO**. Not deployed.

## Question answered

"How much real exposure can this exact immutable strategy version safely request RIGHT NOW?"

Answer today: every bound that has a truthful source is computed and recorded
separately; **effective capacity is UNAVAILABLE** because no registered
liquidity / market-impact capacity model exists (and funding, venue maxima,
leverage brackets and per-symbol account eligibility are not captured). No
conservative number is invented.

## Contract: `strategy-capacity-receipt.v1` (`trader/strategy/capacity.py`)

One immutable receipt per exact `strategy_id / version_id / spec_hash /
instrument_id / market_type / as_of_ms`, stored append-only in
`strategy_capacity_receipts` (UPDATE/DELETE abort).

- `receipt_id = sha256(identity + inputs_sha256)`.
- `inputs`: every observed value — raw `state_kv` records
  (`account_observation`, `risk_assessment`, `risk_state`), the Risk book
  (journal open trades) and closed-trade count, the venue position observation
  (`portfolio.observation.v1`), the registry record with `snapshot_id`, the last
  15 closed spec-timeframe bars, supplied liquidity evidence, and the staleness
  bounds applied (with their source constants).
- `result = compute(inputs)`; `load()` re-runs `compute` and refuses on any
  difference (replay). Status: `ESTABLISHED | PARTIAL | UNAVAILABLE`.

## Bounds, by source (never substituted for each other)

| Dimension | Source | Result |
|---|---|---|
| RISK `risk_rule` | `RiskManager.check_entry` itself on a DB-less RiskManager; policy digest must equal configured **and** running (`risk_assessment` via `current_truth.read_risk`) | ESTABLISHED when inputs are; BLOCK → 0; parity with a real RiskManager asserted exactly |
| ACCOUNT `equity` | `account_observation` via `current_truth.read_account` (FRESH/VENUE_FALLBACK, authoritative, ≤ `ACCOUNT_STALE_S`) | ESTABLISHED / `ACCOUNT_EQUITY_{MISSING,STALE,…}` |
| ACCOUNT `funding` | none recorded (only `totalMarginBalance`) | UNAVAILABLE `NO_RECORDED_AVAILABLE_MARGIN_OBSERVATION` |
| PORTFOLIO | journal book, used only if a complete venue observation ≤ `protection_snapshot.STALE_AFTER_S` confirms each instrument/side/size (reconcile 1% rule) | ESTABLISHED / `VENUE_POSITIONS_NOT_OBSERVED`, `…_STALE`, `JOURNAL_VENUE_BOOK_DISAGREES` |
| STRATEGY `stop_geometry` | `SpecExit.from_spec`: ATR multiple on the declared timeframe (`indicators.atr`, latest closed bars) or percent; Kernel 0.4% floor; price = last closed spec-bar close (labelled not a live fill) | ESTABLISHED / `SPEC_DECLARES_NO_STOP_GEOMETRY`, `MARKET_BARS_*` |
| VENUE status, minimums | instrument registry (`LOT_SIZE` min/step, `MIN_NOTIONAL`) | ESTABLISHED |
| VENUE maximums | not captured by the registry | UNAVAILABLE `VENUE_MAXIMUM_NOT_CAPTURED` |
| VENUE leverage | bracket presence only | UNAVAILABLE `VENUE_LEVERAGE_BRACKET_NOT_CAPTURED` |
| VENUE account eligibility | public registry → UNKNOWN | UNAVAILABLE `ACCOUNT_SYMBOL_ELIGIBILITY_UNKNOWN` |
| LIQUIDITY | no registered model (`REGISTERED_LIQUIDITY_MODELS = {}`) | UNAVAILABLE `NO_REGISTERED_LIQUIDITY_CAPACITY_MODEL`; supplied volume/slippage evidence recorded and ignored |
| EFFECTIVE | all mandatory dimensions ESTABLISHED → min of bounds, venue step floor, 0 below venue minimum | UNAVAILABLE `MANDATORY_DIMENSION_UNAVAILABLE`, no number |

Existing evidence that is **not** a capacity model (recorded in every receipt):
`risk.slippage_atr_frac` (backtest cost charge), Trade Provenance entry
reference price (per-trade slippage basis), `DepthScout` (signal), exchange 24h
volume (no registered participation policy). The 2026-09-02 book-walk and
fill-impact measurements exist only as a `config.yaml` comment.

## Strategy Factory integration (`factory_handoff.py`)

- `CAPACITY` (carried by StrategyVersion and approval-request records) is now
  the contract reference `{"status": "EVALUATED_AT_USE", "contract":
  "strategy-capacity-receipt.v1", …}` — never a number. `version_id` identity
  is unchanged (capacity is not part of it).
- `evaluate_capacity(...)` loads and re-verifies the exact version, gathers,
  builds and records one receipt.
- `eligible_for_first_live(..., capacity_receipt_id, now_ms)` requires a
  receipt for this exact version that is **current** at `now_ms` (same
  configured and running Risk policy, unsuperseded `state_kv` records and
  trades, fresh account/Risk/venue-position evidence, no newer closed spec bar)
  with effective ESTABLISHED. Otherwise it fails closed with `capacity_*`
  reasons (`capacity_receipt_not_asserted`, `capacity_not_established`,
  `capacity_receipt_wrong_version`, `capacity_risk_policy_changed`,
  `capacity_inputs_superseded:*`, `capacity_*_stale`, …). Read-only.
- Effect: **no version can be first-live eligible until effective capacity can
  be ESTABLISHED.** Owner approval still binds the exact version only.

## Tests

- `tests/test_strategy_capacity.py` — 21 passed: risk bound = `check_entry`
  exactly; exposure reduces capacity (margin cap binds; already-exposed → 0);
  BLOCK → 0; unconfirmed/stale/disagreeing venue book → UNAVAILABLE; missing and
  stale equity; funding ≠ equity; running ≠ configured policy; venue minimums,
  venue max never promoted; missing/invalid stop geometry; raw volume cannot
  establish liquidity; absent model fails closed; effective rule with injected
  hypothetical dimensions; replay/tamper/immutability; wrong version refused;
  changed policy → not current; stale/superseded receipt; eligibility
  integration; read-only eligibility (DB dump identical); no order/Risk/control
  writes or Kernel/engine imports.
- `tests/test_strategy_factory_handoff.py` — 53 passed (eligibility assertions
  now expect `capacity_receipt_not_asserted` as the only remaining reason).
- Full suite (serial, `--continue-on-collection-errors`): package 72 failed /
  6994 passed / 16 errors; untouched base 548ebb7: 72 failed / 6973 passed /
  16 errors. The failing and erroring node-ID sets are **identical** (all
  pre-existing: owner_interface r3–r6 / exit_ab_shadow missing legacy
  `index.html`, research-universe depth on an empty candles DB, caller/byte-
  identity guards, signal params gaining `spec_sha256`, etc.); +21 passes are
  the new capacity tests.

## Missing capacity evidence (exact)

1. A registered liquidity / market-impact capacity model (SDD §16.3: spread,
   depth, volume, expected impact, turnover, historical slippage) with its own
   authority and validation.
2. Persisted current order-book depth observations with source time.
3. Measured slippage vs order size per instrument as a queryable artifact.
4. A recorded available-margin (funding) observation.
5. Venue maximum quantity/notional (`MARKET_LOT_SIZE` maxQty etc.) and leverage
   brackets in the instrument registry.
6. Authoritative per-symbol account eligibility.
7. A persisted venue position observation (the Kernel holds
   `portfolio.observation.v1` in memory only; receipts need a caller-supplied
   observation).

## Boundaries kept

No deploy, no activation, no orders, no Risk limit / owner policy / leverage /
config change, no participation-rate limit, intelligence shadow untouched,
frontend untouched, `research.referee=false` and `research.handoff=false`
unchanged. Stage 5 is not PROVEN/ACTIVE.
