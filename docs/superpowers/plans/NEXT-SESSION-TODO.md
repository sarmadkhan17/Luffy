# Luffy — next session handoff

**State on 2026-08-31.** Everything below is committed on `master`. The kernel
is running with the spec population loaded (`population=19` = 15 legacy
genomes + 4 compiled specs). 414 tests pass.

Read first: `docs/superpowers/specs/2026-08-31-strategy-generation-rebuild-design.md`
and the memory notes `strategy-mechanism`, `fix-the-data-not-the-test`,
`strategy-families-fail-honest-backtest`.

**Governing premise: no edge lasts.** Selection asks "is this working NOW"
(90-day pooled gate), retirement asks "has this stopped" (30-day, PF < 0.85).
Do not reintroduce lifetime pass/fail gates — under one, all 27 strategies
scored zero.

---

## 1. Verify the loop actually fired (do this first, 10 min)

`kernel._strategy_mechanism_loop` sleeps 300s after boot then runs every 3h.
It had not yet logged a cycle when the session ended.

```bash
grep -E "mechanism:|spec_admitted|spec_rejected|spec_write_failed" logs/luffy.log | tail
# force one cycle without waiting:
./venv/bin/python -c "
from trader.kernel import Kernel
k=Kernel(); print(k._mechanism_once(per_cycle=1, max_book=8))"
```
If `Kernel()` needs args, read `trader/kernel.py:60-100`. Expect either
`spec_admitted` or `spec_rejected` in `brain_events`.

## 2. Frontend — specs are invisible in the dashboard (NOT DONE)

The dashboard was never updated for the spec population. `kind='spec'` rows
carry `params='{}'` and a populated `spec_json`, so anything rendering
`params` shows an empty strategy.

- Find the strategies route in `trader/dashboard/server.py` (`/api/strategies`
  returned 404 — the real path is elsewhere, check the GraphQL schema in
  `trader/api/graphql_schema.py` too).
- Render for spec rows: name, thesis, timeframe, direction, `entry_long`,
  `filters`, exit geometry, measured `regime_filter`, and whether it fits the
  CURRENT regime (`Analyst.current_regime()`).
- `CompiledStrategy.to_markdown()` already renders exactly this — reuse it
  rather than rebuilding the layout.
- Verify `trader/dashboard/web/index.html` does not break on a null `params`.

## 3. Researcher still flattens every idea (NOT DONE — highest leverage)

`trader/brain/harvester.py:299-315` still instructs the LLM:

> "Our system can only express these strategy families ... map it to the
> NEAREST family and fill unspecified genes with schema midpoints"

This is why 409 scraped ideas produced zero strategies. Replace it: ask for
the MECHANISM (what inefficiency, what observable triggers it, what would
invalidate it) and emit a structured `Idea`, then hand it to
`SpecWriter.write(idea=...)` which already accepts exactly that shape and is
verified working against DeepSeek. Do the same for
`harvester.idea_to_genome()` and `crawler.py`.

## 4. Librarian writes no spec cards (NOT DONE)

`trader/knowledge/vault.py:refresh_strategy_notes()` only knows genome rows.
Extend it to emit `CompiledStrategy.to_markdown()` for spec rows, including
the regime evidence in `spec.provenance["regime_evidence"]`.

## 5. TradingView path is built but unwired (NOT DONE)

`CompiledStrategy.to_pine()` works — 13 of 27 specs compile to Pine v5 and
pass the existing lint gate; the other 14 are honestly excluded because
funding/OI/flow do not exist on a Pine chart.

Nothing calls it. Wire it into `trader/brain/tv_harness.py` as a confirmation
stage after `Analyst.admit()`, budget-capped via `tv_harness.daily_runs`.
Keep it ADVISORY — `luffy-gauntlet-repair` records what happened when a TV
proxy was allowed to gate.

**Credentials:** the harness uses `assisted_login()` with a persistent browser
profile, not stored secrets — run
`./venv/bin/python -m trader.brain.tv_harness --login` once and the session
persists for months. `.mcp.json` has only the Motion servers; there is no
TradingView MCP. Do not put TV credentials in `.env`.

## 6. Three of four live specs are gated to unproven regimes

`Analyst.set_measured_regimes()` keeps the declared filter when no regime
meets the evidence bar (>=5 windows, >50% hit rate, median PF >= 1) — an empty
filter would silence the strategy entirely. So only **Aggressor Thrust
Breakout** currently matches the live regime (TRENDING_DOWN); the other three
sit idle by design.

Re-run `Analyst.regime_fitness()` for each as data accumulates, and consider
whether the 5-window minimum is right at 15m where windows are plentiful.

## 7. Data that unlocks 11 untested mechanisms

Binance caps OI / taker ratio / long-short at a hard ~30 days. The recorder
runs every 15m (`kernel._derivatives_recorder`) and history accumulates
forward. Around **2026-11-01** those 11 specs become honestly testable — until
then `spec_evidence.missing_data()` correctly refuses them via `MIN_COVERAGE`.

Check progress:
```bash
./venv/bin/python -c "
from trader.data.derivatives import DerivFeed
import pandas as pd
for k,(n,lo,hi) in sorted(DerivFeed().coverage().items()):
    print(k, n, pd.to_datetime(lo,unit='ms',utc=True).date(),
          '->', pd.to_datetime(hi,unit='ms',utc=True).date())"
```

`basis` features are registered but NOTHING FETCHES basis — it is in `SERIES`
but not in `_SERIES_FETCHERS`, so basis specs will always report untested.
Either implement it (spot vs perp klines, both have years of history) or drop
the feature.

## 8. Deferred by design — pick up when the above is done

- **Optimizer.** Every numeric literal is already a tunable gene with an
  inferred range (`dsl.extract_literals` / `apply_literals`). Nothing consumes
  them. Add TPE/CMA-ES plus PBO or deflated Sharpe — with 0.027s backtests a
  search of thousands is trivial, which makes overfit control mandatory, not
  optional.
- **Grade the 99,651 HOLD decisions.** The journal holds ~99.7k cycles and
  ~139 graded outcomes: a ~700x starved signal with a complete feature vector
  already recorded for every one. Backfilling forward returns properly feeds
  `trader/brain/meta_label.py`.
- **Retire the legacy path.** `library.EVALUATORS`, `FAMILY_GENE_SPECS`,
  `PARAM_DEFAULTS` and the `exec()`-based `proposer._sandbox_evaluator` all
  still exist and still run 15 genome strategies. Retire only after the spec
  population has traded enough paper to trust.

## Known-good invariants — do not break

- `orchestrator.py:201-215` is UNTOUCHED. Specs reach it by registering their
  evaluator under `f"spec:{id}"`, because `library.evaluate()` dispatches on
  `genome.family`. Keep it that way.
- `scripts/backtest_equivalence.py` must stay PASS.
- `scripts/bench_vector_backtest.py` must stay above 20x (currently 482x).
- `spec_evidence` reports UNTESTED, never a score, when data is absent or does
  not span the frame. Do not "fix" a failing test by relaxing this.
