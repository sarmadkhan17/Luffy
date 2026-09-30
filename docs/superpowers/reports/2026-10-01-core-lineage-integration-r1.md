# LUFFY-CORE-LINEAGE-INTEGRATION-R1

Date: 2026-10-01 · Branch: `luffy-integrated-core-r1` · Verdict: **PASS (TESTED, NOT_DEPLOYED)** ·
Trading behavior changed: **NO**

## Merge

The branch starts at `strategy-factory-r1` `a883313`. It merges `trade-provenance-r1` `54aefc5`
with `--no-commit --no-ff`. The merge base is `d46d045`. The strategy side brings 139 commits and
the provenance side brings 14:
- current truth `87b9e4b`;
- trade provenance `54aefc5`;
- the owner frontend and gateway;
- candle-store and recovery hardening.

Git merged everything except three files. Those were resolved by hand, keeping both sides; no
side was taken wholesale:

| File | Resolution |
|---|---|
| `trader/core/journal.py` | Both additive ALTERs kept: `decisions.reason_codes/_version` and `trades.entry_identity_json`. |
| `trader/strategy/compile.py` | `CompiledStrategy` keeps both `fingerprint` and `spec_sha256`. Signal params stamp `spec_fingerprint`, the signal-occurrence fields and `spec_sha256`. |
| `trader/kernel.py` | Imports `math` + `re`. `_flush_health` and `news_status_line` are both kept. The entry gate keeps the reason codes and adds three things: the current-truth `risk_state` gate, the None-safe daily breaker and `_record_risk_assessment`. |

The legacy `trader/dashboard/web/*` files were removed by the frontend lineage, and
`test_owner_frontend_api` asserts they are gone.

## Schema union

- A fresh DB and DBs built by each parent end up with identical objects and columns after opening
  with the merged code.
- Present:
  - `equity_provenance`;
  - `trade_legs` and `trade_fills`;
  - `trades.entry_identity_json` with its immutability trigger;
  - the research, investigation and strategy-factory tables.
- Legacy trades keep a NULL identity, and a backfill UPDATE is refused (`entry_identity_immutable`).

## Real probation identity bridge

This test path uses no hand-built identity:

1. The Kernel's exact-version install runs, then `_load_population`. The population's
   `spec_sha256` equals `StrategyVersion.spec_hash`.
2. `_entry_provenance` stamps the identity, and `Journal.add_trade` / `close_trade` record the
   trade.
3. `trades.entry_identity_json.spec_sha256` equals `spec_hash`, and probation is SATISFIED on
   exactly those 15 trades.

Refusals on the same path:
- A signal from another evaluator hash is AMBIGUOUS in Provenance, so it is unattributed and
  probation is `INCOMPLETE_TRADE_IDENTITY`.
- An entry with no identity also gives INCOMPLETE.
- Derived versions inherit nothing.

No real probation was run.

## Tests

- Focused suites: **1436 passed**, with two known non-regressions (listed below). The suites
  cover:
  - current truth, Risk baseline and owner reads/recovery;
  - trade provenance and booking;
  - the intelligence spine and Attention wiring;
  - WorldModel;
  - investigation, research and the strategy-factory/exact-probation handoff;
  - single-creation-path;
  - execution, recovery and control;
  - reason codes and the owner frontend API.
- `tests/test_strategy_factory_handoff.py`: 53 passed. It includes the two real-bridge tests.
- Owner interface suites: 433 passed.
- Test fixture updates:
  - `tests/test_attention_supplemental.py` and `tests/test_decision_rejection_reason_codes.py`:
    their fake kernels adopt the current-truth Risk contract (`_fetch_balance_fresh`,
    `update_equity(authoritative=)` returning `risk_state`). Before this, 16 tests failed.
- `git diff --check` is clean for the working changes. The staged whitespace hits are all in
  frozen evidence and frontend files carried unchanged from the provenance lineage.

Known non-regressions:
- `test_every_code_referenced_exists_and_every_code_is_used` also fails at `a883313`. The scanner
  reads the `rc` alias in `research_bank_view.py`.
- `test_owner_interface_r3..r6.py` fail collection identically at `54aefc5`: they read the removed
  legacy `index.html`.
- News icon `st4` is timing-sensitive: its future timestamp is fixed when the test is collected.
  It passes on rerun.
- The `test_trade_booking` CLI test needs `./venv` in the checkout; it passes with one.

## Gaps

- The `risk_state` entry gate has no dedicated `decision-rejection-reason.v1` code. It records
  `entries_not_allowed` with the blocked text.
- No real probation has run: there are 0 research candidates, and `research.referee` /
  `research.handoff` are false.
- Capacity is UNAVAILABLE.
- Production `main` is at `87b9e4b` and does not contain `54aefc5`. Replacing `main` with this
  baseline is an owner decision.
