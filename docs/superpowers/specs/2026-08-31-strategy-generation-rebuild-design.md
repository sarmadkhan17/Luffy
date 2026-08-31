# Strategy Generation Rebuild — Design

**Date:** 2026-08-31
**Status:** approved for planning
**Supersedes:** the `Genome` / `FAMILY_GENE_SPECS` / `library.EVALUATORS` representation

---

## Context

Luffy's strategy pipeline produces no strategy a quant would recognise as one.
The book contains `vwap_fade variant (1.9493)` and four copies of
`ema_trend harvested`. Nothing in it is out-of-the-box, and nothing in it
carries a definition you could read and falsify.

This is not a search problem. It is a **representation problem**, and it has
three measured causes.

### 1. Novelty is destroyed at intake, by instruction

`harvester.py:299-315` tells the extraction LLM, verbatim:

> *"Our system can only express these strategy families … map it to the
> NEAREST family and fill unspecified genes with schema midpoints."*

The Researcher reads arXiv q-fin, Quantpedia, AlphaArchitect, Robot Wealth
and Financial Hacker. A paper on funding-rate exhaustion or order-flow
imbalance is then flattened into `ema_trend(adx_min=25)`. The journal records
the outcome exactly: **409 `harvest_idea` events, 4 `harvest_accepted` — and
all four came from `source: "in-house analyst grid"`, not from the web.** Of
409 ideas scraped from the internet, **zero** ever became a strategy. The two
that reached the book are both named `ema_trend harvested`. The pipeline is a
funnel with eight holes in the bottom, and nothing fits through them.

### 2. There is no artifact worth calling a strategy

A strategy today is a family string plus 2–3 floats. Worse,
`proposer._hypothesis()` (`proposer.py:165-171`) loops the seed population and
returns **the seed's hypothesis verbatim** for every variant — so every
`ema_trend` in the book claims the identical inefficiency. Names are generated
as `f"{family} variant ({z_entry or adx_min or 'v2'})"`.

Exits are not part of a strategy at all. `backtest.py:95-103` reads
SL 2.5·ATR / TP 4.5·ATR / 32-bar hold from global `config.yaml:risk` and
applies them to **every strategy ever tested**. One arbitrary R:R needing
~36% win rate to break even before fees. The 0/24 honest-backtest result
recorded in `strategy-families-fail-honest-backtest` may say more about that
fixed exit geometry than about the families.

### 3. The inputs can only yield already-known strategies

`DataFeed` is OHLCV-only. The entire feature vocabulary is eight price-derived
functions in `agents/indicators.py`. You cannot invent something novel from
OHLCV that has not been invented a million times. Binance publishes, free:
funding rate history, open interest, taker buy/sell ratio, top-trader
long/short ratio, liquidations, order-book depth, and spot–perp basis. None of
it is ingested.

The one escape hatch — `proposer._invent_family()`, where the LLM writes real
evaluator code — is capped at 1/day, gated at PF ≥ 1.3 internal-only, and
**`data/invented_families.json` does not exist**. Zero inventions have ever
persisted.

### Intended outcome

The org roles already declared in `org.yaml` get a real work product flowing
between them: Researcher → Strategist → Analyst → Trader, with the Librarian
holding every artifact. The Strategist **writes** strategies instead of
mapping them to templates. The Analyst judges them honestly and decides
keep-vs-replace against the book. And a strategy becomes one self-contained,
readable, falsifiable object.

---

## What a strategy becomes: `StrategySpec`

One artifact, three consumers: the Strategist writes it, the Analyst compiles
and judges it, the Librarian prints it.

```python
@dataclass
class StrategySpec:
    id: str
    name: str                # written by the Strategist, never auto-generated
    thesis: str              # the inefficiency, falsifiable, >= 80 chars
    invalidation: str        # how it dies, >= 30 chars
    provenance: dict         # {source_url, source_kind, author, parent_id, harvested_at}
    universe: dict           # {include: [...], exclude: [...], min_volume_usdt: N}
    timeframe: str           # "5m" | "15m" | "1h"
    direction: str           # "long" | "short" | "both"
    entry_long: str          # DSL expression -> bool series
    entry_short: str         # DSL expression, "" when long-only
    filters: list[str]       # DSL expressions, ANDed with entry
    exit: ExitSpec           # PART OF THE STRATEGY, not global config
    regime_filter: list[str] # orchestrator compatibility
    markets: list[str]
    generation: int
    parent_id: str
    # derived by the compiler, never declared by the LLM:
    data_requires: list[str] # ["ohlcv", "funding", "open_interest", ...]
```

```python
@dataclass
class ExitSpec:
    stop:   dict   # {"kind":"atr","mult":1.8} | {"kind":"pct","v":0.008}
                   # | {"kind":"swing","lookback":20}
    target: dict   # {"kind":"atr","mult":3.2} | {"kind":"rr","v":2.0}
                   # | {"kind":"none"}
    trail:  dict   # {"kind":"none"} | {"kind":"atr","mult":2.0,"arm_at_r":1.0}
    time:   dict   # {"max_bars": 24}
    signal_exit: str   # optional DSL expression, "" = none
```

`data_requires` is **derived by the compiler** from the features the
expressions actually use. The LLM cannot misdeclare it.

**Storage:** add a nullable `spec_json TEXT` column to the `strategies` table
(SQLite `ALTER TABLE`, no data migration). Rows with `kind="spec"` route to the
compiler; existing rows keep working through the old `Genome` path throughout
the parallel build.

---

## The DSL

A restricted Python expression, parsed with `ast.parse(expr, mode="eval")`.

```
close > ema(20) and adx(14) > 25 and funding_z(96) < -1.5
```

**Allowed AST nodes:** `Expression`, `BoolOp(And|Or)`, `UnaryOp(Not|USub)`,
`BinOp(Add|Sub|Mult|Div)`, `Compare(Lt|LtE|Gt|GtE|Eq|NotEq)`,
`Call` (callee must be a registered feature name), `Name` (must be a 0-arity
feature), `Constant` (int/float/str).

Everything else raises `SpecError`. No attribute access, no subscripts, no
lambdas, no comprehensions, no imports, no assignment. This is **strictly
safer** than the current `_sandbox_evaluator()`, which `exec()`s arbitrary
LLM-written Python behind a builtins whitelist — a whitelist that does not stop
`__class__`/`__subclasses__` traversal.

**Evaluation** walks the AST bottom-up; every node returns a `pd.Series` or a
scalar. Comparisons and boolean ops become vectorized `&` / `|`. The result is
a boolean Series aligned to the frame index. Feature results are memoised per
`(name, args)` per compile-run, because `ema(20)` typically appears in both the
entry and the filters.

### Numeric literals are genes, for free

The compiler walks the AST collecting `Constant` nodes and their positions.
Each becomes a tunable parameter with a range inferred from the feature
registry: an argument position uses the feature's declared `arg_specs`
(`ema`'s period → int 5..200); a comparison threshold uses the *other* side's
declared output `domain` (`rsi` → 0..100, `adx` → 0..60, any `_z` feature →
−4..4).

This deletes `FAMILY_GENE_SPECS` and `PARAM_DEFAULTS` entirely. The Strategist
writes structure; an optimizer (next spec) perturbs literals; the same text is
what the Librarian prints. One representation, no drift — and no repeat of the
`ema_trend` fast_len/slow_len bug where the Python evaluator and the Pine
template scored different strategies.

---

## Feature library

`trader/strategy/features.py` — the registry **is** the Strategist's
vocabulary, so novelty scales directly with it.

```python
@dataclass
class Feature:
    name: str
    fn: Callable            # (ctx, *args) -> pd.Series
    arg_specs: list[tuple]  # [(type, lo, hi)] — drives literal tuning
    domain: tuple | None    # output range — drives threshold tuning
    requires: list[str]     # ["ohlcv"] | ["funding"] | ...
```

| Group | Features |
|---|---|
| price/vol | `close open high low volume ema(n) sma(n) rsi(n) atr(n) adx(n) bb_upper(n,k) bb_lower(n,k) bb_pctb(n,k) vwap(n) donchian_hi(n) donchian_lo(n) swing_hi(n) swing_lo(n) ret(n) realized_vol(n)` |
| transforms | `zscore(f,n) pct_rank(f,n) slope(f,n) htf(tf,f)` |
| time | `hour_utc dow is_session('us'\|'asia'\|'eu')` |
| cross-asset | `btc_ret(n) btc_ema_dist(n) corr_btc(n) rel_strength_btc(n)` |
| **derivatives/flow** | `funding funding_z(n) funding_cum(n) oi oi_ret(n) oi_z(n) basis basis_z(n) taker_ratio taker_ratio_z(n) ls_ratio_top ls_ratio_global liq_notional(side,n) liq_imbalance(n)` |

Price features wrap `agents/indicators.py` where one already exists, returning
Series rather than the scalars several of those functions currently return
(`atr`, `adx`, `anchored_vwap`, `zscore`). Unit tests assert the Series' last
value equals the existing scalar function, so the two judges never diverge.

`htf(tf, f)` reuses `backtest.resample()` and the point-in-time discipline in
`backtest.ctx_at()` (`backtest.py:121-131`) — a higher-timeframe bar is only
visible once it has closed.

---

## The compiler — three targets

`trader/strategy/compile.py`: `compile_spec(spec) -> CompiledStrategy`

1. **`entries(frames) -> (long: BoolArray, short: BoolArray)`** — fully
   vectorized. Feeds the backtester.
2. **`to_evaluator() -> Callable[[spec_like, Snapshot], StrategySignal|None]`** —
   evaluates the DSL over the snapshot window and reads the last bar. This is
   the **entire live integration**: `orchestrator.py:210` calls
   `strat_lib.evaluate(genome, snap)` and needs no change at all.
3. **`to_markdown() -> str`** — the Librarian's card: thesis, provenance, the
   literal DSL text, the exit rules, and the latest evidence.
4. **`to_pine() -> str`** — best-effort. Features with no Pine analogue
   (funding, OI, liquidations) mark the spec `tv_testable: false` and the
   TradingView stage is skipped with a recorded reason. Honest degradation
   replaces the current silent family-proxy substitution that
   `luffy-gauntlet-repair` already had to neutralise.

---

## Vectorized backtester

`trader/strategy/vector_backtest.py` — same `BacktestResult` dataclass, so
every downstream threshold, `passes()` call and `score_results()` comparison
keeps working.

1. Compute long/short boolean arrays and the ATR array once, vectorized.
2. Iterate **only over candidate entry indices** (`np.flatnonzero(long|short)`),
   skipping any that fall inside an open position.
3. For each entry, resolve the exit by vectorized forward search over the
   bounded window — pessimistic SL-before-TP when both are hit intrabar, plus
   trail arming. `ExitSpec` supplies the geometry instead of `config.yaml`.
4. Reuse the fee / slippage / funding accounting from `backtest.py:137-163`
   **verbatim**, so results are directly comparable to the old engine.

Entries are sparse — a few hundred over 8000 bars — so the cost drops from
8000 Python evaluator calls to ~200 bounded window scans. Target: the 5-symbol
× 8000-bar gauntlet goes from ~76 s to under 1 s, retiring
`gauntlet_max_full_runs: 2` and the whole in-tick budget problem.

**The equivalence harness is mandatory, not optional.** Port the eight
families to seed specs, run both backtesters over identical frames, and assert
trade-for-trade equality or a documented, understood delta. Without it the
cutover is faith-based.

---

## Data layer — derivatives and flow

`trader/data/derivatives.py`, mirroring the store patterns already in
`feed.py:107-153` (`_store_load` / `_store_save` / `_merge_save`), persisting
to `data/derivs.db`.

| Source | Endpoint | History | Backtestable |
|---|---|---|---|
| Funding rate | `/fapi/v1/fundingRate` | years | **now** |
| Spot–perp basis | spot + perp klines | years | **now** |
| Open interest | `/futures/data/openInterestHist` | ~30 d | after ~2 months of recording |
| Taker buy/sell | `/futures/data/takerlongshortRatio` | ~30 d | after ~2 months of recording |
| Long/short ratio | `/futures/data/topLongShortPositionRatio`, `/globalLongShortAccountRatio` | ~30 d | after ~2 months of recording |
| Liquidations | ws `!forceOrder@arr` | **none** | forward-only |

Because the ~30-day endpoints and liquidations cannot be backfilled, **the
recorder ships in phase 1** even though the strategies that consume it arrive
later. Every 15 minutes the kernel appends the latest observations, and history
accumulates past the API window.

**Point-in-time alignment is the correctness risk.** Every derivative series is
reindexed onto the 15m bar index using the same `searchsorted` discipline as
`backtest.ctx_at()`, forward-filling only observations that had already
settled. Funding especially: an 8-hourly rate is knowable at settlement, not
before, so it is shifted. A lookahead test — shifting a series must change
backtest results — guards this.

---

## The org pipeline

Roles in `org.yaml` are metadata-only today (`wraps` names dotted paths, but
nothing routes through them). This gives each a typed input and output.

| Role | in → out | Code |
|---|---|---|
| **Researcher** | web → `Idea` + provenance | `brain/harvester.py`, `brain/crawler.py` |
| **Harvester** | exchange → OHLCV + derivatives/flow | `data/feed.py`, `data/derivatives.py` |
| **Strategist** | Idea + doctrine + feature vocabulary → **`StrategySpec`** | `brain/strategist.py` |
| **Analyst** | Spec → compile → honest gauntlet → keep / replace / reject | `brain/analyst.py` (new) |
| **Trader** | approved specs → orders | `engine/executor.py`, `engine/exits.py` |
| **Librarian** | every spec, verdict, autopsy → vault | `knowledge/vault.py` |

**Researcher change:** the extraction prompt stops asking for a family. It asks
for the *mechanism* — what inefficiency, what observable triggers it, what
would invalidate it — and emits a structured `Idea`. Nothing is flattened.

**Strategist change:** given Ideas, doctrine (`data/doctrine.json`), and the
full feature vocabulary, it writes a `StrategySpec` in the DSL. This is the
first time the LLM is asked to do the thing it is actually good at: composing a
mechanism and naming it. It stops choosing floats.

**Analyst (new, `brain/analyst.py`)** absorbs `strategy/evidence.py` and the
verdict half of `brain/judge.py`:
- compile → purged walk-forward across `backtest_symbols` with fees,
  slippage and funding
- gate on OOS trades / PF / drawdown / train-test robustness, as `evidence.py`
  already does
- **portfolio keep-or-replace**: build the candidate's per-bar return series
  from its backtest trades and correlate against every active and paper
  strategy. Keep when it passes AND (`max_corr < 0.6` OR
  `PF > 1.15 × best_correlated.PF`). When the book is at `max_active`, replace
  the weakest member only if the candidate's marginal contribution — portfolio
  PF with it minus without — is positive. A PF 1.05 uncorrelated spec is worth
  more than a PF 1.3 clone, and nothing in the current pipeline can see that.
- write the verdict, with numbers, as a `brain_event` and a vault note

**Naming collision to resolve:** `org.yaml` auto-appends one "Analyst" node per
`Analyst` subclass in `trader/agents/` — those are *market* analysts. The new
role is declared as **`Quant Analyst`** to keep the roster unambiguous.

---

## Migration — build parallel, cut over on evidence

The kernel is live. Nothing routes through the new path until the equivalence
harness passes.

**Phase 1 — Representation** (no behaviour change)
- `strategy/spec.py`, `strategy/features.py`, `strategy/dsl.py`
- eight families ported to seed specs in `data/seed_specs/*.json`
- `data/derivatives.py` + recorder thread — **starts now**, so 30-day-window
  history begins accumulating
- tests: DSL parse/reject matrix, feature-vs-`indicators.py` equality

**Phase 2 — Speed and equivalence**
- `strategy/vector_backtest.py`
- equivalence harness: 8 seed specs, old vs new engine, identical frames
- benchmark proving ≥50× on the 5-symbol × 8000-bar gauntlet

**Phase 3 — Data features**
- funding and basis features (backtestable immediately)
- OI / taker / LS features (recording under way)
- lookahead tests on every derivative series

**Phase 4 — Roles**
- Researcher extraction prompt → mechanism, not family
- `Strategist.write_spec()`
- `brain/analyst.py` with portfolio keep/replace
- Librarian spec cards via `vault.refresh_strategy_notes()`

**Phase 5 — Cutover**
- `kernel._load_population()` (`kernel.py:112-128`) builds a shim for
  `kind="spec"` rows carrying `.family = f"spec:{id}"`, `.markets` and
  `.regime_filter` from the spec, and calls
  `library.register_evaluator(f"spec:{id}", compiled.to_evaluator())`.
  `library.evaluate()` dispatches on `genome.family`
  (`library.py:293`), so `orchestrator.py:201-215` — including its
  `markets` and `regime_filter` guards — is **untouched**. `Genome` rows load
  exactly as they do today.
- shadow-run both paths, compare decisions
- retire `library.EVALUATORS`, `FAMILY_GENE_SPECS`, `PARAM_DEFAULTS`, and the
  `exec()`-based `_sandbox_evaluator`

---

## Verification

```bash
# unit + integration
./venv/bin/python -m pytest tests/ -q

# DSL safety: every rejection case must raise SpecError
./venv/bin/python -m pytest tests/test_dsl.py -q

# features match the existing scalar indicators
./venv/bin/python -m pytest tests/test_features.py -q

# equivalence: old vs new backtester, 8 seed specs, same frames
./venv/bin/python scripts/backtest_equivalence.py

# speed: must clear 50x on 5 symbols x 8000 bars
./venv/bin/python scripts/bench_vector_backtest.py

# lookahead: shifting any derivative series must change results
./venv/bin/python -m pytest tests/test_pit_alignment.py -q

# end-to-end: an Idea becomes a written spec, judged, with a vault card
./venv/bin/python scripts/strategist_dry_run.py --idea-file data/sample_ideas.json
```

**Acceptance:** an idea scraped from a source outside the eight families —
a funding-rate or basis mechanism — reaches a written `StrategySpec` with a
name a human wrote, a falsifiable thesis, its own exits, a readable DSL body,
an honest multi-symbol walk-forward verdict, and a Librarian card. That is the
thing the current pipeline cannot produce.

---

## Out of scope (next specs)

- **Parameter search / optimizer** over the extracted literals (TPE, CMA-ES).
  Worthless until the DSL and fast backtester exist; trivial afterwards.
- **Multiple-testing control** — PBO, deflated Sharpe. Becomes urgent the
  moment search runs thousands of specs; the current 70/30 split does not
  cover best-of-N selection.
- **Grading the 99,651 HOLD decisions.** The journal holds 99,753 cycles and
  **139 graded outcomes** — a ~700× starved learning signal, with a complete
  feature vector already recorded for every one. Backfilling forward returns
  turns 139 samples into ~100k, properly feeds `brain/meta_label.py`, and gives
  the Strategist an empirical prior instead of a random draw. High value,
  separable, and worth its own spec.
- Liquidation-driven strategies — data does not exist yet by construction.
