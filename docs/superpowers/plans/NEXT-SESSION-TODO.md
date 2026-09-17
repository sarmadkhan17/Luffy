# Luffy — next session handoff

> Historical handoff from August 31. For the current work queue, start with
> [docs/NEXT_SESSION.md](../../NEXT_SESSION.md) and its canonical checklist.
> The historical contents below are preserved as context.

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

## Pipeline — BUILT 2026-08-31

The org pipeline is wired end to end and verified against live data:

**Scraper → Harvester → Strategist → Analyst → Trader → Risk Officer → Librarian**

| Role | Module | State |
|---|---|---|
| Scraper (prose) | `brain/scraper.py`, `crawler.py`, `ideas.py` | renamed from `harvester.py`; idea queue live |
| Harvester (numbers) | `brain/harvester.py`, `data/derivatives.py`, `data/coinalyze.py`, `data/mcp_client.py` | NEW role |
| Strategist | `brain/spec_writer.py` | now fed idea + doctrine + data brief |
| Strategy Analyst | `brain/analyst.py` | + `confirm_on_tv()`, `rank_for_now()` |
| Trader | `engine/executor.py`, `exits.py` | unchanged |
| Risk Officer | `engine/risk.py` | **max 4 open trades** |
| Librarian | `knowledge/vault.py` | spec cards via `to_markdown()` |

### The data, not the test

- **`basis` implemented** (`derivatives.basis` / `basis_history`, spot vs perp
  klines). It was registered as a feature with no fetcher, so every basis spec
  reported UNTESTED forever. **2 years x 5 symbols backfilled** — now `usable`.
- **`brief()` derives its bar from the Analyst's own gate** (90% of the
  backtest frame = 75 days today), not a hardcoded day count. An earlier cut
  used fixed thresholds and reported `oi` as usable when it is 31 days — the
  brief must never promise what the Analyst will refuse.
- The brief goes to the Strategist in **its own prompt section**. Folded into
  `doctrine` it was truncated away at 600 chars and the model never saw it.
  Verified: the Strategist now writes against `funding`/`basis` and avoids the
  thin positioning series unprompted.

### MCP

- `.mcp.json` gains the official **CoinGecko MCP** (`https://mcp.api.coingecko.com/mcp`,
  keyless) for the agent layer.
- `data/mcp_client.py` gives the **kernel** its own MCP client (Streamable
  HTTP JSON-RPC, no new dependency) — `.mcp.json` only ever configured the
  coding agent, so the daemon could not reach a server. Configured under
  `mcp.servers` in config.yaml.
- Note: the public CoinGecko MCP exposes only `execute`/`search_docs` — an
  agent interface, not a numeric feed. The Harvester therefore prefers REST
  where a REST source exists and uses MCP for what only MCP serves.

### Open — needs you

**Coinalyze API key.** `oi`, `taker_ratio` and `ls_ratio` are stuck at ~31
days against a 75-day requirement, because Binance retains them ~30 days. They
can NEVER clear the gate from Binance alone — waiting until 2026-11-01 does not
fix it, it just delays it. Coinalyze serves them far deeper on a free key
(40 req/min). `data/coinalyze.py` is written and wired; set
`COINALYZE_API_KEY` in `.env` and it activates.

It is **unverified against a live key** — we have none. `resolve()` discovers
the venue-suffixed symbol format from `/future-markets` at runtime rather than
hardcoding a guess, and every call degrades to an empty frame, so a wrong
guess costs a log line. Verify once the key exists.

### Weighted combination of the active set

The orchestrator always combined strategies — it sums every eligible signal
alongside the analyst votes. What it did NOT do is weigh them: every strategy
entered the sum at a flat `STRATEGY_VOTE_WEIGHT = 0.45`, so a mechanism
printing PF 1.8 in the live regime counted exactly as much as one limping at
0.9. Analysts had `base_weights x acc_mult x regime_fit` from the start;
strategies had nothing.

`trader/strategy/blend.py` gives them the same treatment:

    weight = performance_multiplier(closed trades)   # realized PF
           x regime_multiplier(measured regime evidence, live regime)

Both shrink toward 1.0 by how much evidence exists (half-weight at 10 trades
/ 5 windows), because the failure mode is "two lucky trades doubled a
strategy's voice". Each FACTOR is bounded to [0.5, 1.5] so neither alone pins
the product at the [0.25, 2.0] ceiling — pinned weights lose the ordering
inside the set, which is the whole point. Nothing is ever muted: silencing is
retirement, the Analyst's job, not the blender's.

**A second dead-code bug fixed on the way.** `set_measured_regimes()` computed
regime evidence and only RETURNED it. `vault.py` and `blend.py` both read
`spec.provenance["regime_evidence"]` and nothing ever wrote it, so the regime
half of the weighting would have been permanently neutral. It is now
persisted.

Weights are all 1.0 today — the four live specs have no closed trades and no
measured regime evidence yet. That is honest, not broken: they differentiate
as evidence accumulates.

### Decisions taken (I chose; say if you disagree)

- **TradingView is ADVISORY**, never a gate — recorded beside the spec, never
  blocking admission. A TV gate would silently kill every derivatives
  mechanism, since only ~half the book compiles to Pine at all.
- **The set is COMBINED, and now weighted.** `rank_for_now()` was the wrong
  shape — a cosmetic ordering nothing consumed. Replaced by
  `Analyst.active_set()`, which reports the set with the same weights the
  orchestrator applies. See "Weighted combination" below.
- **`risk_per_trade_pct` left at 1.5.** With a 4-trade cap that is 6% max heat
  against a 15% cap, so the heat cap no longer binds and ~60% of the risk
  budget sits idle. Raise it if you want the old budget used.

---

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
