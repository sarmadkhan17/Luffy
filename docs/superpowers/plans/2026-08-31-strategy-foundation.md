# Strategy Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `Genome`/`FAMILY_GENE_SPECS`/`library.EVALUATORS` with a `StrategySpec` written in a safe expression DSL over a feature library, compiled to a vectorized backtester — and start recording the derivatives/flow data that makes non-obvious strategies expressible.

**Architecture:** A strategy becomes one self-contained JSON artifact whose entry/filter logic is a restricted Python expression (`close > ema(20) and funding_z(96) < -1.5`) parsed with `ast` against a whitelist and resolved against a feature registry. The compiler produces (a) vectorized boolean entry arrays for a fast backtester that iterates only sparse entry indices, and (b) a live evaluator with the *existing* `(genome_like, Snapshot) -> StrategySignal|None` signature so the orchestrator never changes. Nothing routes through the new path until an equivalence harness proves the new engine reproduces the old one trade-for-trade.

**Tech Stack:** Python 3, pandas, numpy, sqlite3, requests, pytest. No new third-party dependencies.

**Spec:** `docs/superpowers/specs/2026-08-31-strategy-generation-rebuild-design.md`

## Global Constraints

- All commands use the local venv: `./venv/bin/python`, `./venv/bin/python -m pytest`.
- **No new third-party dependencies.** pandas, numpy, requests, sqlite3 only.
- **The DSL never executes arbitrary code.** `ast.parse(mode="eval")` + node whitelist. No `exec`, no `eval`, no attribute access, no subscripts, no comprehensions, no lambdas, no keyword arguments.
- **`BacktestResult` (`trader/strategy/backtest.py:24-58`) is unchanged.** Downstream thresholds in `evidence.py`, `promotion.py` and `judge.py` depend on its fields and `passes()`.
- **Cost model is mandatory and comes from `config.yaml:risk`:** `taker_fee_pct`, `slippage_atr_frac`, `funding_rate_8h`, `bar_minutes`. Reuse the accounting in `backtest.py:137-163` verbatim.
- **Point-in-time discipline:** no feature may read a bar that had not closed at the evaluation bar. Higher-timeframe and derivative series follow the `searchsorted` pattern in `backtest.py:121-131`.
- **The live kernel keeps running the old `Genome` path untouched.** No task in this plan modifies `trader/engine/orchestrator.py` or `trader/strategy/library.py`'s dispatch.
- Commit after every task.

---

## File Structure

**Create:**

| File | Responsibility |
|---|---|
| `trader/strategy/spec.py` | `StrategySpec`, `ExitSpec` dataclasses; validation; JSON round-trip |
| `trader/strategy/features.py` | `Feature` dataclass, `FEATURES` registry, `FeatureCtx`, price/transform/time/cross-asset features |
| `trader/strategy/features_deriv.py` | Derivatives/flow features registered into `FEATURES` |
| `trader/strategy/dsl.py` | `parse()`, `evaluate()`, `extract_literals()`, `apply_literals()`, `SpecError` |
| `trader/strategy/compile.py` | `compile_spec()` → `CompiledStrategy` with `entries()`, `to_evaluator()`, `to_markdown()` |
| `trader/strategy/vector_backtest.py` | `vector_backtest()`, `vector_walk_forward()`, `simulate()` |
| `trader/data/derivatives.py` | `DerivFeed` — funding / OI / taker / long-short / basis fetch + `data/derivs.db` store |
| `data/seed_specs/*.json` | The eight legacy families ported to specs |
| `scripts/backtest_equivalence.py` | Old vs new engine, and legacy evaluator vs ported spec |
| `scripts/bench_vector_backtest.py` | Speed benchmark |

**Modify:**

| File | Change |
|---|---|
| `trader/agents/indicators.py` | Add Series-valued variants beside the existing scalar functions; scalars keep working |
| `trader/kernel.py:~150` | Start the derivatives recorder daemon thread |
| `config.yaml` | `derivatives:` section |

**Tests:** `tests/test_spec.py`, `tests/test_features.py`, `tests/test_dsl.py`, `tests/test_compile.py`, `tests/test_vector_backtest.py`, `tests/test_derivatives.py`, `tests/test_pit_alignment.py`

---

### Task 1: StrategySpec and ExitSpec

**Files:**
- Create: `trader/strategy/spec.py`
- Test: `tests/test_spec.py`

**Interfaces:**
- Consumes: nothing
- Produces: `StrategySpec`, `ExitSpec`, `StrategySpec.validate(spec) -> list[str]`, `spec.to_dict() -> dict`, `StrategySpec.from_dict(d) -> StrategySpec`, `spec.tunable_id() -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_spec.py
import pytest
from trader.strategy.spec import StrategySpec, ExitSpec


def _valid() -> StrategySpec:
    return StrategySpec(
        id="seed_test",
        name="Funding Exhaustion Fade",
        thesis="Perpetual funding paid by crowded longs marks positioning "
               "exhaustion; fading price after an extreme funding z-score "
               "captures the unwind as leveraged longs are forced out.",
        invalidation="Retire if OOS profit factor falls below 1.0 over 30 trades.",
        provenance={"source_kind": "authored", "author": "quant"},
        universe={"include": ["BTC/USDT"], "exclude": [], "min_volume_usdt": 0},
        timeframe="15m",
        direction="short",
        entry_long="",
        entry_short="funding_z(96) > 2.0",
        filters=["adx(14) < 25"],
        exit=ExitSpec(stop={"kind": "atr", "mult": 1.8},
                      target={"kind": "rr", "v": 2.0},
                      trail={"kind": "none"},
                      time={"max_bars": 24},
                      signal_exit=""),
        regime_filter=["RANGING"],
        markets=["futures"],
    )


def test_roundtrip_is_lossless():
    s = _valid()
    assert StrategySpec.from_dict(s.to_dict()) == s


def test_valid_spec_has_no_errors():
    assert StrategySpec.validate(_valid()) == []


def test_short_thesis_rejected():
    s = _valid()
    s.thesis = "it goes up"
    assert any("thesis" in e for e in StrategySpec.validate(s))


def test_missing_invalidation_rejected():
    s = _valid()
    s.invalidation = ""
    assert any("invalidation" in e for e in StrategySpec.validate(s))


def test_direction_must_have_matching_entry():
    s = _valid()
    s.direction = "long"          # but entry_long is ""
    assert any("entry_long" in e for e in StrategySpec.validate(s))


def test_unknown_timeframe_rejected():
    s = _valid()
    s.timeframe = "3m"
    assert any("timeframe" in e for e in StrategySpec.validate(s))


def test_unknown_exit_stop_kind_rejected():
    s = _valid()
    s.exit.stop = {"kind": "vibes"}
    assert any("stop" in e for e in StrategySpec.validate(s))


def test_auto_generated_name_rejected():
    """The whole point of a spec is a name a human or the Strategist chose."""
    s = _valid()
    s.name = "ema_trend variant (22.0)"
    assert any("name" in e for e in StrategySpec.validate(s))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_spec.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trader.strategy.spec'`

- [ ] **Step 3: Implement `trader/strategy/spec.py`**

```python
"""StrategySpec — the company's work product.

A strategy is ONE self-contained artifact: a named, falsifiable hypothesis
with its own entry logic, its own filters and its OWN EXITS. It replaces
`Genome`, whose family+params shape forced every scraped idea into one of
eight templates and read exit geometry from global config.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

TIMEFRAMES = {"5m", "15m", "1h"}
DIRECTIONS = {"long", "short", "both"}
REGIMES = {"TRENDING_UP", "TRENDING_DOWN", "RANGING", "VOLATILE"}
MARKETS = {"spot", "futures"}

STOP_KINDS = {"atr", "pct", "swing"}
TARGET_KINDS = {"atr", "rr", "pct", "none"}
TRAIL_KINDS = {"none", "atr"}

#: names the old pipeline auto-generated; a spec must be named, not derived
_AUTONAME = re.compile(r"variant \(|harvested$|^\w+ variant", re.I)

MIN_THESIS = 80
MIN_INVALIDATION = 30


@dataclass
class ExitSpec:
    stop: dict = field(default_factory=lambda: {"kind": "atr", "mult": 2.0})
    target: dict = field(default_factory=lambda: {"kind": "rr", "v": 2.0})
    trail: dict = field(default_factory=lambda: {"kind": "none"})
    time: dict = field(default_factory=lambda: {"max_bars": 32})
    signal_exit: str = ""


@dataclass
class StrategySpec:
    id: str
    name: str
    thesis: str
    invalidation: str
    provenance: dict
    universe: dict
    timeframe: str
    direction: str
    entry_long: str
    entry_short: str
    filters: list
    exit: ExitSpec
    regime_filter: list
    markets: list
    generation: int = 0
    parent_id: str = ""
    #: derived by the compiler from the features actually used — never
    #: declared by the author, so it cannot be misreported
    data_requires: list = field(default_factory=lambda: ["ohlcv"])

    # ── serialisation ────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)

    @staticmethod
    def from_dict(d: dict) -> "StrategySpec":
        d = dict(d)
        ex = d.get("exit") or {}
        d["exit"] = ex if isinstance(ex, ExitSpec) else ExitSpec(**ex)
        return StrategySpec(**d)

    @staticmethod
    def from_json(s: str) -> "StrategySpec":
        return StrategySpec.from_dict(json.loads(s))

    # ── validation ───────────────────────────────────────────────────────
    @staticmethod
    def validate(s: "StrategySpec") -> list[str]:
        """Structural validation only. Expression validity is the DSL's job
        (`dsl.parse`), so a spec can be checked before features are loaded."""
        errs: list[str] = []
        if not (s.id or "").strip():
            errs.append("id is required")
        name = (s.name or "").strip()
        if len(name) < 4:
            errs.append("name must be a real name (>=4 chars)")
        elif _AUTONAME.search(name):
            errs.append(f"name '{name}' looks auto-generated — the Strategist "
                        f"must name the strategy")
        if len((s.thesis or "").strip()) < MIN_THESIS:
            errs.append(f"thesis must state the inefficiency (>={MIN_THESIS} chars)")
        if len((s.invalidation or "").strip()) < MIN_INVALIDATION:
            errs.append(f"invalidation required (>={MIN_INVALIDATION} chars)")
        if s.timeframe not in TIMEFRAMES:
            errs.append(f"timeframe '{s.timeframe}' not in {sorted(TIMEFRAMES)}")
        if s.direction not in DIRECTIONS:
            errs.append(f"direction '{s.direction}' not in {sorted(DIRECTIONS)}")
        if s.direction in ("long", "both") and not (s.entry_long or "").strip():
            errs.append("direction includes long but entry_long is empty")
        if s.direction in ("short", "both") and not (s.entry_short or "").strip():
            errs.append("direction includes short but entry_short is empty")
        bad_reg = set(s.regime_filter or []) - REGIMES
        if bad_reg:
            errs.append(f"unknown regimes: {sorted(bad_reg)}")
        if not s.markets or not set(s.markets) <= MARKETS:
            errs.append(f"markets must be a non-empty subset of {sorted(MARKETS)}")
        errs.extend(_validate_exit(s.exit))
        return errs


def _validate_exit(e: ExitSpec) -> list[str]:
    errs: list[str] = []
    if not isinstance(e, ExitSpec):
        return ["exit must be an ExitSpec"]
    if e.stop.get("kind") not in STOP_KINDS:
        errs.append(f"exit.stop kind must be one of {sorted(STOP_KINDS)}")
    elif e.stop["kind"] == "atr" and not (0.2 <= float(e.stop.get("mult", 0)) <= 6.0):
        errs.append("exit.stop atr mult must be in [0.2, 6.0]")
    elif e.stop["kind"] == "pct" and not (0.001 <= float(e.stop.get("v", 0)) <= 0.2):
        errs.append("exit.stop pct v must be in [0.001, 0.2]")
    if e.target.get("kind") not in TARGET_KINDS:
        errs.append(f"exit.target kind must be one of {sorted(TARGET_KINDS)}")
    if e.trail.get("kind") not in TRAIL_KINDS:
        errs.append(f"exit.trail kind must be one of {sorted(TRAIL_KINDS)}")
    mb = int(e.time.get("max_bars", 0) or 0)
    if not (1 <= mb <= 500):
        errs.append("exit.time.max_bars must be in [1, 500]")
    if e.target.get("kind") == "none" and e.trail.get("kind") == "none" and mb > 200:
        errs.append("a spec with no target and no trail needs a tighter "
                    "max_bars (<=200)")
    return errs
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_spec.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/spec.py tests/test_spec.py
git commit -m "feat(strategy): StrategySpec + ExitSpec — a strategy is one named, falsifiable artifact with its own exits"
```

---

### Task 2: Series-valued indicators

The existing `atr`, `adx`, `anchored_vwap`, `zscore`, `realized_vol` return a
single float (the last bar). The DSL needs whole Series. Adding `*_series`
variants beside them — and having the existing scalar functions delegate —
guarantees the two can never drift, which is exactly the bug
`genome.assert_defaults_consistent()` exists to catch.

**Files:**
- Modify: `trader/agents/indicators.py`
- Test: `tests/test_features.py`

**Interfaces:**
- Consumes: nothing
- Produces: `atr_series(df, period=14) -> Series`, `adx_series(df, period=14) -> Series`, `vwap_series(df, n=96) -> Series`, `zscore_series(s, n=96) -> Series`, `realized_vol_series(df, n=48) -> Series`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_features.py
import numpy as np
import pandas as pd
import pytest

from trader.agents import indicators as ind


@pytest.fixture
def df():
    rng = np.random.default_rng(11)
    n = 500
    close = 50000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    spread = close * 0.0015
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close,
        "high": close + spread,
        "low": close - spread,
        "close": close,
        "volume": rng.uniform(100, 1000, n),
    })


def test_atr_series_last_matches_scalar(df):
    assert ind.atr_series(df, 14).iloc[-1] == pytest.approx(ind.atr(df, 14))


def test_adx_series_last_matches_scalar(df):
    assert ind.adx_series(df, 14).iloc[-1] == pytest.approx(ind.adx(df, 14))


def test_vwap_series_last_matches_scalar(df):
    assert ind.vwap_series(df, 96).iloc[-1] == pytest.approx(
        ind.anchored_vwap(df, 96))


def test_zscore_series_last_matches_scalar(df):
    s = df["close"]
    assert ind.zscore_series(s, 96).iloc[-1] == pytest.approx(
        ind.zscore(s, 96), abs=1e-9)


def test_realized_vol_series_last_matches_scalar(df):
    assert ind.realized_vol_series(df, 48).iloc[-1] == pytest.approx(
        ind.realized_vol(df, 48), rel=1e-6)


def test_series_are_full_length_and_aligned(df):
    for s in (ind.atr_series(df, 14), ind.adx_series(df, 14),
              ind.vwap_series(df, 96), ind.realized_vol_series(df, 48)):
        assert len(s) == len(df)
        assert s.index.equals(df.index)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./venv/bin/python -m pytest tests/test_features.py -v`
Expected: FAIL — `AttributeError: module 'trader.agents.indicators' has no attribute 'atr_series'`

- [ ] **Step 3: Add the Series variants and delegate the scalars**

Append to `trader/agents/indicators.py`, and replace the four scalar bodies as shown:

```python
def _true_range(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    return pd.concat([h - l, (h - c.shift()).abs(),
                      (l - c.shift()).abs()], axis=1).max(axis=1)


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return _true_range(df).rolling(period).mean()


def adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l = df["high"], df["low"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    atr_ = _true_range(df).ewm(alpha=1 / period, adjust=False).mean() + 1e-12
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False).mean() / atr_
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-12)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def vwap_series(df: pd.DataFrame, n: int = 96) -> pd.Series:
    """Rolling n-bar VWAP. At the last bar this equals anchored_vwap(df, n)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).rolling(n).sum()
    vol = df["volume"].rolling(n).sum()
    return pv / (vol + 1e-12)


def zscore_series(s: pd.Series, n: int = 96) -> pd.Series:
    """Rolling z-score. Matches zscore()'s sample std (ddof=1) and its
    0.0-on-degenerate-window behaviour."""
    m = s.rolling(n).mean()
    sd = s.rolling(n).std()
    z = (s - m) / sd.where(sd > 1e-12)
    return z.replace([np.inf, -np.inf], 0.0).fillna(0.0)


def realized_vol_series(df: pd.DataFrame, n: int = 48) -> pd.Series:
    """Rolling std of log returns. Matches realized_vol() when the window is
    full: that function computes r.std()*sqrt(len(r))/sqrt(bars), and
    len(r) == bars once warmed up."""
    r = np.log(df["close"]).diff()
    return r.rolling(n).std()
```

Now replace the four scalar bodies so there is exactly one implementation of
each. Their public signatures and return types do not change:

```python
def atr(df: pd.DataFrame, period: int = 14) -> float:
    return float(atr_series(df, period).iloc[-1])


def adx(df: pd.DataFrame, period: int = 14) -> float:
    return float(adx_series(df, period).iloc[-1])


def anchored_vwap(df: pd.DataFrame, anchor_bars: int = 96) -> float:
    """VWAP over the last `anchor_bars` bars ≈ session/market cost basis."""
    return float(vwap_series(df, anchor_bars).iloc[-1])


def zscore(s: pd.Series, lookback: int = 96) -> float:
    if len(s.tail(lookback).dropna()) < 10:
        return 0.0
    return float(zscore_series(s, lookback).iloc[-1])
```

Leave `realized_vol` as-is: its `sqrt(len(r))/sqrt(bars)` scaling differs
from a plain rolling std on short frames, and `agents/regime.py` depends on
the current values.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./venv/bin/python -m pytest tests/test_features.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the full suite — the scalar functions have new internals**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: no new failures versus the pre-task baseline. Capture the baseline
first with `git stash && ./venv/bin/python -m pytest tests/ -q; git stash pop`
if you did not record it.

- [ ] **Step 6: Commit**

```bash
git add trader/agents/indicators.py tests/test_features.py
git commit -m "feat(indicators): Series-valued atr/adx/vwap/zscore; scalars delegate to them"
```

---

### Task 3: Feature registry and price features

**Files:**
- Create: `trader/strategy/features.py`
- Test: `tests/test_features.py` (append)

**Interfaces:**
- Consumes: `indicators.atr_series/adx_series/vwap_series/zscore_series` (Task 2)
- Produces:
  - `Feature(name, fn, arg_specs, domain, requires)` frozen dataclass
  - `FEATURES: dict[str, Feature]`
  - `register(name, arg_specs=(), domain=None, requires=("ohlcv",))` decorator
  - `FeatureCtx(frames, tf, btc=None, derivs=None)` with `.df`, `.index`, `.get(name, args)`
  - `SERIES_ARG` sentinel marking an argument that is itself an expression

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_features.py
from trader.strategy.features import FEATURES, FeatureCtx


@pytest.fixture
def ctx(df):
    return FeatureCtx(frames={"15m": df}, tf="15m")


def test_registry_has_core_features():
    for name in ("close", "ema", "rsi", "atr", "adx", "vwap", "volume",
                 "abs", "max", "min"):
        assert name in FEATURES, f"{name} missing from FEATURES"


def test_zero_arity_feature_returns_column(ctx, df):
    out = FEATURES["close"].fn(ctx)
    assert out.equals(df["close"])


def test_ema_matches_indicator(ctx, df):
    assert FEATURES["ema"].fn(ctx, 20).iloc[-1] == pytest.approx(
        ind.ema(df["close"], 20).iloc[-1])


def test_atr_feature_matches_indicator(ctx, df):
    assert FEATURES["atr"].fn(ctx, 14).iloc[-1] == pytest.approx(ind.atr(df, 14))


def test_ctx_caches_repeated_calls(ctx):
    a = ctx.get("ema", (20,))
    b = ctx.get("ema", (20,))
    assert a is b, "FeatureCtx must memoise; ema(20) appears in entry and filters"


def test_every_feature_declares_a_domain_or_is_price(ctx):
    """Threshold tuning infers ranges from `domain`; price-scale features
    legitimately have none."""
    for name, f in FEATURES.items():
        assert f.domain is None or (len(f.domain) == 2 and f.domain[0] < f.domain[1])


def test_bounded_features_stay_in_domain(ctx):
    for name in ("rsi", "adx"):
        s = ctx.get(name, (14,)).dropna()
        lo, hi = FEATURES[name].domain
        assert s.min() >= lo - 1e-6 and s.max() <= hi + 1e-6
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_features.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trader.strategy.features'`

- [ ] **Step 3: Implement `trader/strategy/features.py`**

```python
"""The feature registry — the Strategist's entire vocabulary.

Novelty scales directly with what is in this file: a strategy can only
express a mechanism whose observables are registered here. Every feature is
a pure function returning a Series aligned to the base frame's index.

`arg_specs` and `domain` are not documentation — they drive automatic
parameter-range inference for the numeric literals in a DSL expression, which
is what lets an optimizer tune a spec without a hand-written gene schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from ..agents import indicators as ind

#: marks an argument that is a nested expression rather than a literal
SERIES_ARG = "series"


@dataclass(frozen=True)
class Feature:
    name: str
    fn: Callable
    arg_specs: tuple = ()      # per positional arg: (type, lo, hi) or SERIES_ARG
    domain: tuple | None = None    # output range, drives threshold inference
    requires: tuple = ("ohlcv",)


FEATURES: dict[str, Feature] = {}


def register(name: str, arg_specs: tuple = (), domain: tuple | None = None,
             requires: tuple = ("ohlcv",)):
    def deco(fn):
        FEATURES[name] = Feature(name, fn, arg_specs, domain, requires)
        return fn
    return deco


@dataclass
class FeatureCtx:
    """Everything a feature may see at one evaluation.

    `frames` maps timeframe -> DataFrame for the symbol under test. `btc`
    holds the leader's frames. `derivs` holds derivative Series ALREADY
    aligned point-in-time to the base index (see data/derivatives.py).
    """
    frames: dict
    tf: str
    btc: dict | None = None
    derivs: dict | None = None
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def df(self) -> pd.DataFrame:
        return self.frames[self.tf]

    @property
    def index(self):
        return self.df.index

    def get(self, name: str, args: tuple = ()):
        """Memoised feature evaluation. `ema(20)` typically appears in both
        the entry expression and the filters; computing it twice per bar
        across a 5-symbol gauntlet is pure waste."""
        key = (name, self.tf, args)
        if key not in self._cache:
            self._cache[key] = FEATURES[name].fn(self, *args)
        return self._cache[key]

    def scoped(self, tf: str) -> "FeatureCtx":
        """A view on a different timeframe, sharing the cache."""
        return FeatureCtx(self.frames, tf, self.btc, self.derivs, self._cache)


def _s(ctx: FeatureCtx, col: str) -> pd.Series:
    return ctx.df[col]


# ── raw price/volume ─────────────────────────────────────────────────────
for _col in ("open", "high", "low", "close", "volume"):
    register(_col)(lambda ctx, _c=_col: _s(ctx, _c))

register("taker_buy")(lambda ctx: ctx.df.get(
    "taker_buy", ctx.df["volume"] * 0.5))


# ── arithmetic helpers (needed to port the legacy families faithfully) ───
register("abs", arg_specs=(SERIES_ARG,))(lambda ctx, x: _abs(x))
register("max", arg_specs=(SERIES_ARG, SERIES_ARG))(lambda ctx, a, b: _pair(a, b, np.maximum))
register("min", arg_specs=(SERIES_ARG, SERIES_ARG))(lambda ctx, a, b: _pair(a, b, np.minimum))


def _abs(x):
    return x.abs() if isinstance(x, pd.Series) else abs(x)


def _pair(a, b, op):
    if isinstance(a, pd.Series) or isinstance(b, pd.Series):
        idx = a.index if isinstance(a, pd.Series) else b.index
        return pd.Series(op(np.asarray(a, dtype=float),
                            np.asarray(b, dtype=float)), index=idx)
    return float(op(a, b))


# ── indicators ───────────────────────────────────────────────────────────
register("ema", arg_specs=((int, 3, 300),))(
    lambda ctx, n: ind.ema(ctx.df["close"], int(n)))
register("sma", arg_specs=((int, 3, 300),))(
    lambda ctx, n: ctx.df["close"].rolling(int(n)).mean())
register("rsi", arg_specs=((int, 3, 50),), domain=(0.0, 100.0))(
    lambda ctx, n: ind.rsi(ctx.df["close"], int(n)))
register("atr", arg_specs=((int, 3, 100),))(
    lambda ctx, n: ind.atr_series(ctx.df, int(n)))
register("adx", arg_specs=((int, 3, 60),), domain=(0.0, 100.0))(
    lambda ctx, n: ind.adx_series(ctx.df, int(n)))
register("vwap", arg_specs=((int, 12, 400),))(
    lambda ctx, n: ind.vwap_series(ctx.df, int(n)))
register("realized_vol", arg_specs=((int, 8, 400),))(
    lambda ctx, n: ind.realized_vol_series(ctx.df, int(n)))


@register("bb_upper", arg_specs=((int, 5, 100), (float, 0.5, 4.0)))
def _bb_upper(ctx, n, k):
    c = ctx.df["close"]
    n = int(n)
    return c.rolling(n).mean() + float(k) * c.rolling(n).std()


@register("bb_lower", arg_specs=((int, 5, 100), (float, 0.5, 4.0)))
def _bb_lower(ctx, n, k):
    c = ctx.df["close"]
    n = int(n)
    return c.rolling(n).mean() - float(k) * c.rolling(n).std()


@register("bb_pctb", arg_specs=((int, 5, 100), (float, 0.5, 4.0)),
          domain=(-1.0, 2.0))
def _bb_pctb(ctx, n, k):
    lo, hi = _bb_lower(ctx, n, k), _bb_upper(ctx, n, k)
    return (ctx.df["close"] - lo) / (hi - lo).where((hi - lo).abs() > 1e-12)


@register("donchian_hi", arg_specs=((int, 5, 300),))
def _don_hi(ctx, n):
    # shift(1): the current bar's own high must not define the level it breaks
    return ctx.df["high"].rolling(int(n)).max().shift(1)


@register("donchian_lo", arg_specs=((int, 5, 300),))
def _don_lo(ctx, n):
    return ctx.df["low"].rolling(int(n)).min().shift(1)


@register("ret", arg_specs=((int, 1, 200),), domain=(-1.0, 1.0))
def _ret(ctx, n):
    return ctx.df["close"].pct_change(int(n))
```

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_features.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/features.py tests/test_features.py
git commit -m "feat(strategy): feature registry + price features with tunable arg specs"
```

---

### Task 4: Transform, time and cross-asset features

**Files:**
- Modify: `trader/strategy/features.py`
- Test: `tests/test_features.py` (append)

**Interfaces:**
- Consumes: Task 3's `register`, `FeatureCtx`, `SERIES_ARG`
- Produces: features `zscore`, `pct_rank`, `slope`, `hour_utc`, `dow`, `is_session`, `btc_ret`, `btc_ema_dist`, `corr_btc`, `rel_strength_btc`. `htf` is registered as a marker only — the DSL evaluator special-cases it (Task 6) because its second argument must be evaluated in a different timeframe scope.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_features.py
def test_zscore_feature_matches_indicator(ctx, df):
    out = FEATURES["zscore"].fn(ctx, df["close"], 96)
    assert out.iloc[-1] == pytest.approx(ind.zscore(df["close"], 96), abs=1e-9)


def test_pct_rank_is_bounded(ctx, df):
    out = FEATURES["pct_rank"].fn(ctx, df["close"], 96).dropna()
    assert out.min() >= 0.0 and out.max() <= 1.0


def test_hour_utc_matches_timestamps(ctx, df):
    out = FEATURES["hour_utc"].fn(ctx)
    assert int(out.iloc[0]) == int(df["ts"].dt.hour.iloc[0])


def test_is_session_is_boolean(ctx):
    out = FEATURES["is_session"].fn(ctx, "us")
    assert out.dtype == bool
    assert out.any() and not out.all()


def test_btc_features_align_to_base_index(ctx, df):
    ctx.btc = {"15m": df.copy()}
    out = FEATURES["btc_ret"].fn(ctx, 4)
    assert len(out) == len(df)
    assert out.index.equals(df.index)


def test_prev_shifts_by_n_bars(ctx, frame):
    out = FEATURES["prev"].fn(ctx, frame["close"], 1)
    assert out.iloc[5] == pytest.approx(frame["close"].iloc[4])
    assert pd.isna(out.iloc[0])


def test_prev_enables_crossing_logic(ctx):
    from trader.strategy.dsl import evaluate_bool, parse
    cross = evaluate_bool(parse("prev(rsi(14), 1) < 30 and rsi(14) >= 30"), ctx)
    level = evaluate_bool(parse("rsi(14) >= 30"), ctx)
    assert cross.sum() < level.sum(), "a cross must be rarer than a level"


def test_btc_feature_without_btc_frame_is_nan(ctx):
    ctx.btc = None
    out = FEATURES["btc_ret"].fn(ctx, 4)
    assert out.isna().all(), "missing leader data must be NaN, never 0.0 — a " \
                             "zero would read as 'BTC flat' and fire signals"
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_features.py -k "zscore_feature or pct_rank or hour_utc or session or btc" -v`
Expected: FAIL — `KeyError: 'zscore'`

- [ ] **Step 3: Append to `trader/strategy/features.py`**

```python
# ── transforms ───────────────────────────────────────────────────────────
@register("zscore", arg_specs=(SERIES_ARG, (int, 12, 500)))
def _zscore(ctx, s, n):
    return ind.zscore_series(_series(ctx, s), int(n))


@register("pct_rank", arg_specs=(SERIES_ARG, (int, 12, 500)), domain=(0.0, 1.0))
def _pct_rank(ctx, s, n):
    return _series(ctx, s).rolling(int(n)).rank(pct=True)


@register("prev", arg_specs=(SERIES_ARG, (int, 1, 50)))
def _prev(ctx, s, n):
    """The value of an expression n bars ago.

    Crossing logic needs it. Approximating a cross (`prev(rsi(14),1) < 30 and
    rsi(14) >= 30`) with a plain level test (`rsi(14) >= 30`) is a DIFFERENT
    strategy that fires on every bar of a recovery instead of once.
    """
    return _series(ctx, s).shift(int(n))


@register("slope", arg_specs=(SERIES_ARG, (int, 2, 200),))
def _slope(ctx, s, n):
    """Per-bar change over n bars, normalised by the level — unit-free so a
    threshold means the same thing on BTC and on a sub-dollar alt."""
    x = _series(ctx, s)
    return (x - x.shift(int(n))) / (x.abs() + 1e-12) / float(n)


def _series(ctx: FeatureCtx, v) -> pd.Series:
    """Broadcast a scalar argument to the base index."""
    if isinstance(v, pd.Series):
        return v
    return pd.Series(float(v), index=ctx.index)


# ── time ─────────────────────────────────────────────────────────────────
def _ts(ctx: FeatureCtx) -> pd.Series:
    return pd.to_datetime(ctx.df["ts"], utc=True)


register("hour_utc", domain=(0.0, 23.0))(
    lambda ctx: _ts(ctx).dt.hour.astype(float))
register("dow", domain=(0.0, 6.0))(
    lambda ctx: _ts(ctx).dt.dayofweek.astype(float))

_SESSIONS = {"asia": (0, 8), "eu": (7, 16), "us": (13, 22)}


@register("is_session", arg_specs=((str, None, None),))
def _is_session(ctx, name):
    lo, hi = _SESSIONS[str(name)]
    h = _ts(ctx).dt.hour
    return ((h >= lo) & (h < hi)).astype(bool)


# ── cross-asset (leader = BTC) ───────────────────────────────────────────
def _btc(ctx: FeatureCtx) -> pd.DataFrame | None:
    # NEVER `a or b` on DataFrames — truth value is ambiguous and raises.
    # This is the same trap evidence.py:82 documents.
    if not ctx.btc:
        return None
    b = ctx.btc.get(ctx.tf)
    if b is None:
        b = ctx.btc.get("15m")
    return b if b is not None and len(b) else None


def _btc_aligned(ctx: FeatureCtx, col: str = "close") -> pd.Series:
    """BTC's column reindexed onto the base frame's timestamps, taking only
    bars that had CLOSED by each base bar. Missing leader data is NaN, never
    a neutral default: a fabricated 0.0 reads as 'BTC flat' and would fire
    rotation signals on absent data."""
    b = _btc(ctx)
    if b is None or not len(b):
        return pd.Series(np.nan, index=ctx.index)
    base_ts = pd.to_datetime(ctx.df["ts"], utc=True).values
    b_ts = pd.to_datetime(b["ts"], utc=True).values
    pos = np.searchsorted(b_ts, base_ts, side="right") - 1
    vals = np.where(pos >= 0, b[col].values[np.clip(pos, 0, None)], np.nan)
    return pd.Series(vals, index=ctx.index)


@register("btc_ret", arg_specs=((int, 1, 200),), domain=(-1.0, 1.0))
def _btc_ret(ctx, n):
    c = _btc_aligned(ctx)
    return c / c.shift(int(n)) - 1.0


@register("btc_ema_dist", arg_specs=((int, 5, 300),), domain=(-1.0, 1.0))
def _btc_ema_dist(ctx, n):
    c = _btc_aligned(ctx)
    e = c.ewm(span=int(n), adjust=False).mean()
    return (c - e) / (e.abs() + 1e-12)


@register("corr_btc", arg_specs=((int, 12, 500),), domain=(-1.0, 1.0))
def _corr_btc(ctx, n):
    a = ctx.df["close"].pct_change()
    b = _btc_aligned(ctx).pct_change()
    return a.rolling(int(n)).corr(b)


@register("rel_strength_btc", arg_specs=((int, 1, 200),), domain=(-1.0, 1.0))
def _rel_strength_btc(ctx, n):
    """This symbol's n-bar return minus BTC's. Positive = outperforming."""
    n = int(n)
    mine = ctx.df["close"] / ctx.df["close"].shift(n) - 1.0
    c = _btc_aligned(ctx)
    return mine - (c / c.shift(n) - 1.0)


# ── higher timeframe ─────────────────────────────────────────────────────
# Registered as a MARKER: dsl.evaluate() special-cases `htf(tf, expr)` because
# its second argument must be evaluated in a different timeframe scope and
# then reindexed point-in-time onto the base index. The fn here exists only so
# parse() recognises the name and arity.
register("htf", arg_specs=((str, None, None), SERIES_ARG))(
    lambda ctx, tf, expr: expr)
```

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_features.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/features.py tests/test_features.py
git commit -m "feat(strategy): transform, session and cross-asset features"
```

---

### Task 5: DSL parser and safety whitelist

This is the security boundary. It **replaces** `proposer._sandbox_evaluator()`,
which `exec()`s LLM-written Python behind a builtins whitelist — a whitelist
that does not stop `().__class__.__bases__[0].__subclasses__()` traversal.
A node whitelist over `ast.parse(mode="eval")` has no such escape: there is no
node type that can reach an attribute, a subscript, or a name that is not a
registered feature.

**Files:**
- Create: `trader/strategy/dsl.py`
- Test: `tests/test_dsl.py`

**Interfaces:**
- Consumes: `features.FEATURES` (Task 3/4)
- Produces: `SpecError`, `parse(expr: str) -> ast.Expression`, `features_used(tree) -> set[str]`, `data_requires(tree) -> tuple[str, ...]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_dsl.py
import pytest
from trader.strategy.dsl import SpecError, data_requires, features_used, parse


GOOD = [
    "close > ema(20)",
    "adx(14) > 25 and rsi(14) < 30",
    "not (close > vwap(96))",
    "zscore(close, 96) < -2.0",
    "close > ema(20) and (rsi(14) < 30 or bb_pctb(20, 2.0) < 0)",
    "abs(close - ema(50)) > 2.0 * atr(14)",
    "htf('1h', close > ema(50)) and is_session('us')",
    "-1.5 > zscore(volume, 96)",
]

BAD = [
    ("__import__('os').system('ls')", "feature"),
    ("close.__class__", "Attribute"),
    ("close[0]", "Subscript"),
    ("[x for x in close]", "ListComp"),
    ("(lambda: 1)()", "Lambda"),
    ("open('secrets.txt')", "arg"),          # `open` is a feature of arity 0
    ("unknown_feature(3)", "unknown feature"),
    ("ema(n=20)", "keyword"),
    ("close > ema(20) if True else 0", "IfExp"),
    ("close := 5", "SyntaxError"),
    ("ema(20) ** 2", "Pow"),
    ("close; volume", "SyntaxError"),
]


@pytest.mark.parametrize("expr", GOOD)
def test_valid_expressions_parse(expr):
    assert parse(expr) is not None


@pytest.mark.parametrize("expr,hint", BAD)
def test_invalid_expressions_rejected(expr, hint):
    with pytest.raises(SpecError):
        parse(expr)


def test_features_used_is_complete():
    assert features_used(parse("close > ema(20) and adx(14) > 25")) == \
        {"close", "ema", "adx"}


def test_data_requires_is_derived_not_declared():
    assert data_requires(parse("close > ema(20)")) == ("ohlcv",)


def test_empty_expression_rejected():
    with pytest.raises(SpecError):
        parse("")
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_dsl.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trader.strategy.dsl'`

- [ ] **Step 3: Implement the parser half of `trader/strategy/dsl.py`**

```python
"""The strategy expression language.

A restricted Python expression, e.g.

    close > ema(20) and adx(14) > 25 and funding_z(96) < -1.5

parsed with `ast.parse(mode="eval")` against a node whitelist and resolved
against `features.FEATURES`. There is no code execution path: nodes that
could reach an attribute, a subscript, a comprehension or an unregistered
name are rejected before evaluation.

The same text is what the Librarian prints on a vault card, so the strategy a
human reads and the strategy the backtester runs cannot drift apart.
"""
from __future__ import annotations

import ast

from .features import FEATURES, SERIES_ARG


class SpecError(ValueError):
    """A malformed or unsafe strategy expression."""


_ALLOWED = (
    ast.Expression,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div,
    ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq,
    ast.Call, ast.Name, ast.Load, ast.Constant,
)


def parse(expr: str) -> ast.Expression:
    """Parse and validate. Raises SpecError on anything unsafe or unknown."""
    text = (expr or "").strip()
    if not text:
        raise SpecError("empty expression")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as e:
        raise SpecError(f"SyntaxError: {e.msg}") from e

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise SpecError(f"disallowed syntax: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise SpecError("only direct calls to registered features")
            name = node.func.id
            if name not in FEATURES:
                raise SpecError(f"unknown feature '{name}'")
            if node.keywords:
                raise SpecError(f"{name}(): keyword arguments are not allowed")
            spec = FEATURES[name].arg_specs
            if len(node.args) != len(spec):
                raise SpecError(f"{name}() takes {len(spec)} arg(s), "
                                f"got {len(node.args)}")
            _check_args(name, node, spec)
        elif isinstance(node, ast.Name):
            if node.id not in FEATURES:
                raise SpecError(f"unknown name '{node.id}'")
            if FEATURES[node.id].arg_specs:
                raise SpecError(f"'{node.id}' needs "
                                f"{len(FEATURES[node.id].arg_specs)} arg(s)")
    return tree


def _check_args(name: str, node: ast.Call, spec: tuple) -> None:
    for i, (arg, aspec) in enumerate(zip(node.args, spec)):
        if aspec is SERIES_ARG:
            continue
        typ = aspec[0]
        if typ is str:
            if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                raise SpecError(f"{name}() arg {i} must be a string literal")
        elif isinstance(arg, ast.Constant) and not isinstance(
                arg.value, (int, float)):
            raise SpecError(f"{name}() arg {i} must be numeric")


def features_used(tree: ast.Expression) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            out.add(node.func.id)
        elif isinstance(node, ast.Name):
            out.add(node.id)
    return out


def data_requires(*trees: ast.Expression) -> tuple[str, ...]:
    """Union of the data sources every referenced feature declares.

    DERIVED, never declared: a Strategist cannot claim a spec is OHLCV-only
    while reading funding, so the gauntlet can always tell whether the data
    to test it honestly actually exists.
    """
    req: set[str] = set()
    for tree in trees:
        for name in features_used(tree):
            req.update(FEATURES[name].requires)
    return tuple(sorted(req or {"ohlcv"}))
```

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_dsl.py -v`
Expected: all pass. Note `open('secrets.txt')` fails on arity (the `open`
feature takes 0 args) and `ema(20) ** 2` fails because `ast.Pow` is not
whitelisted — both are correct rejections.

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/dsl.py tests/test_dsl.py
git commit -m "feat(dsl): AST-whitelist parser — replaces the exec() sandbox"
```

---

### Task 6: DSL evaluator

**Files:**
- Modify: `trader/strategy/dsl.py`
- Test: `tests/test_dsl.py` (append)

**Interfaces:**
- Consumes: Task 5's `parse`, Task 3/4's `FeatureCtx`
- Produces: `evaluate(tree, ctx) -> pd.Series | float`, `evaluate_bool(tree, ctx) -> np.ndarray[bool]`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_dsl.py
import numpy as np
import pandas as pd
from trader.strategy.dsl import evaluate, evaluate_bool
from trader.strategy.features import FeatureCtx
from trader.agents import indicators as ind


@pytest.fixture
def frame():
    rng = np.random.default_rng(3)
    n = 400
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.003, n)))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": rng.uniform(50, 500, n)})


@pytest.fixture
def ctx(frame):
    return FeatureCtx(frames={"15m": frame}, tf="15m")


def test_comparison_returns_boolean_series(ctx, frame):
    out = evaluate(parse("close > ema(20)"), ctx)
    assert isinstance(out, pd.Series) and out.dtype == bool
    expected = frame["close"] > ind.ema(frame["close"], 20)
    assert out.equals(expected)


def test_and_is_elementwise(ctx):
    both = evaluate_bool(parse("close > ema(20) and adx(14) > 10"), ctx)
    a = evaluate_bool(parse("close > ema(20)"), ctx)
    b = evaluate_bool(parse("adx(14) > 10"), ctx)
    assert np.array_equal(both, a & b)


def test_arithmetic_on_series(ctx, frame):
    out = evaluate(parse("close - ema(20)"), ctx)
    assert out.iloc[-1] == pytest.approx(
        frame["close"].iloc[-1] - ind.ema(frame["close"], 20).iloc[-1])


def test_nan_warmup_is_false_not_true(ctx):
    """A NaN comparison must never be treated as a firing signal."""
    out = evaluate_bool(parse("close > sma(200)"), ctx)
    assert not out[:199].any()


def test_htf_evaluates_in_other_timeframe(ctx, frame):
    from trader.strategy.backtest import resample
    ctx.frames["1h"] = resample(frame, "1h")
    out = evaluate_bool(parse("htf('1h', close > ema(10))"), ctx)
    assert len(out) == len(frame)


def test_htf_has_no_lookahead(ctx, frame):
    """An HTF bar may only be visible once it has closed."""
    from trader.strategy.backtest import resample
    ctx.frames["1h"] = resample(frame, "1h")
    out = evaluate_bool(parse("htf('1h', close > ema(10))"), ctx)
    # the first base bars precede the first CLOSED 1h bar
    assert not out[0]


def test_unary_minus_literal(ctx):
    out = evaluate(parse("zscore(close, 96) < -1.0"), ctx)
    assert out.dtype == bool
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_dsl.py -k evaluate -v`
Expected: FAIL — `ImportError: cannot import name 'evaluate'`

- [ ] **Step 3: Append the evaluator to `trader/strategy/dsl.py`**

```python
import numpy as np
import pandas as pd

_BOOLOP = {ast.And: lambda a, b: a & b, ast.Or: lambda a, b: a | b}
_BINOP = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
          ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b}
_CMP = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
        ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
        ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


def evaluate(tree: ast.Expression, ctx):
    return _eval(tree.body, ctx)


def evaluate_bool(tree: ast.Expression, ctx) -> np.ndarray:
    """Boolean array over the base index. NaN — an indicator still warming
    up, or missing leader/derivative data — is FALSE, never a firing signal."""
    v = _eval(tree.body, ctx)
    if isinstance(v, pd.Series):
        return v.fillna(False).to_numpy(dtype=bool)
    return np.full(len(ctx.index), bool(v))


def _eval(node, ctx):
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        return ctx.get(node.id, ())

    if isinstance(node, ast.Call):
        name = node.func.id
        if name == "htf":
            return _eval_htf(node, ctx)
        args = tuple(_eval(a, ctx) for a in node.args)
        # only literal args participate in the memo key; a Series argument
        # is keyed by the sub-expression's source text
        key = tuple(ast.dump(a) if not isinstance(a, ast.Constant) else a.value
                    for a in node.args)
        cache_key = (name, ctx.tf, key)
        if cache_key not in ctx._cache:
            ctx._cache[cache_key] = FEATURES[name].fn(ctx, *args)
        return ctx._cache[cache_key]

    if isinstance(node, ast.BoolOp):
        vals = [_as_bool(_eval(v, ctx), ctx) for v in node.values]
        op = _BOOLOP[type(node.op)]
        out = vals[0]
        for v in vals[1:]:
            out = op(out, v)
        return out

    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, ctx)
        if isinstance(node.op, ast.Not):
            return ~_as_bool(v, ctx)
        if isinstance(node.op, ast.USub):
            return -v
        return +v

    if isinstance(node, ast.BinOp):
        return _BINOP[type(node.op)](_eval(node.left, ctx),
                                     _eval(node.right, ctx))

    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        out = None
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, ctx)
            r = _CMP[type(op)](left, right)
            out = r if out is None else (_as_bool(out, ctx) & _as_bool(r, ctx))
            left = right
        return out

    raise SpecError(f"cannot evaluate {type(node).__name__}")


def _as_bool(v, ctx):
    """Coerce to a boolean Series on the base index. NaN -> False."""
    if isinstance(v, pd.Series):
        return v.fillna(False).astype(bool)
    return pd.Series(bool(v), index=ctx.index)


def _eval_htf(node: ast.Call, ctx):
    """`htf(tf, expr)` — evaluate `expr` on the `tf` frame, then map each
    higher-timeframe value onto the base bars that came AFTER it closed.

    Same discipline as backtest.ctx_at(): searchsorted with side='right'
    minus one, so a base bar can only see HTF bars already complete.
    """
    tf = node.args[0].value
    if tf not in ctx.frames:
        return pd.Series(np.nan, index=ctx.index)
    sub = ctx.scoped(tf)
    vals = _eval(node.args[1], sub)
    if not isinstance(vals, pd.Series):
        return pd.Series(vals, index=ctx.index)
    base_ts = pd.to_datetime(ctx.df["ts"], utc=True).values
    htf_ts = pd.to_datetime(ctx.frames[tf]["ts"], utc=True).values
    pos = np.searchsorted(htf_ts, base_ts, side="right") - 1
    arr = vals.to_numpy()
    out = np.where(pos >= 0, arr[np.clip(pos, 0, None)], np.nan)
    return pd.Series(out, index=ctx.index)
```

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_dsl.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/dsl.py tests/test_dsl.py
git commit -m "feat(dsl): vectorized evaluator with point-in-time htf() and NaN-is-false semantics"
```

---

### Task 7: Literal extraction — parameters for free

**Files:**
- Modify: `trader/strategy/dsl.py`
- Test: `tests/test_dsl.py` (append)

**Interfaces:**
- Consumes: Task 5's `parse`
- Produces: `Literal(index, value, lo, hi, is_int, source)`, `extract_literals(tree) -> list[Literal]`, `apply_literals(tree, values) -> ast.Expression`, `render(tree) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_dsl.py
from trader.strategy.dsl import apply_literals, extract_literals, render


def test_extracts_arg_and_threshold_literals():
    lits = extract_literals(parse("adx(14) > 25"))
    assert [l.value for l in lits] == [14, 25]


def test_arg_range_comes_from_feature_arg_spec():
    lit = extract_literals(parse("ema(20) > close"))[0]
    assert (lit.lo, lit.hi, lit.is_int) == (3, 300, True)


def test_threshold_range_comes_from_the_other_side_domain():
    """rsi's domain is 0..100, so the 30 in `rsi(14) < 30` is tunable there."""
    lits = extract_literals(parse("rsi(14) < 30"))
    thresh = [l for l in lits if l.value == 30][0]
    assert (thresh.lo, thresh.hi) == (0.0, 100.0)


def test_price_scale_threshold_falls_back_to_relative_range():
    lit = [l for l in extract_literals(parse("close > 50000")) if l.value == 50000][0]
    assert lit.lo < 50000 < lit.hi and lit.source == "fallback"


def test_apply_literals_rewrites_and_is_reparseable():
    tree = parse("adx(14) > 25")
    out = apply_literals(tree, [20, 30])
    assert render(out) == "adx(20) > 30"
    assert parse(render(out)) is not None


def test_apply_literals_preserves_int_ness():
    out = apply_literals(parse("ema(20) > close"), [33.7])
    assert render(out) == "ema(34) > close", "a period must stay an integer"


def test_apply_literals_length_mismatch_raises():
    with pytest.raises(SpecError):
        apply_literals(parse("adx(14) > 25"), [20])
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_dsl.py -k literal -v`
Expected: FAIL — `ImportError: cannot import name 'extract_literals'`

- [ ] **Step 3: Append to `trader/strategy/dsl.py`**

```python
import copy
from dataclasses import dataclass


@dataclass
class Literal:
    """A tunable number inside an expression.

    Every numeric literal is automatically a parameter, with a range inferred
    from the feature registry. This is why there is no FAMILY_GENE_SPECS: the
    Strategist writes structure, an optimizer perturbs these, and the rendered
    text stays the single source of truth for both.
    """
    index: int          # position in document order
    value: float
    lo: float
    hi: float
    is_int: bool
    source: str         # "arg_spec" | "domain" | "fallback"


def _numeric_constants(tree: ast.Expression) -> list[ast.Constant]:
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, (int, float))
            and not isinstance(n.value, bool)]


def extract_literals(tree: ast.Expression) -> list[Literal]:
    ranges = {}          # id(node) -> (lo, hi, is_int, source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            spec = FEATURES[node.func.id].arg_specs
            for arg, aspec in zip(node.args, spec):
                if aspec is SERIES_ARG or not isinstance(arg, ast.Constant):
                    continue
                if not isinstance(arg.value, (int, float)):
                    continue
                typ, lo, hi = aspec
                if lo is not None:
                    ranges[id(arg)] = (float(lo), float(hi), typ is int,
                                       "arg_spec")
        elif isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            for i, operand in enumerate(operands):
                const = _unwrap_const(operand)
                if const is None:
                    continue
                other = operands[i - 1] if i else operands[1]
                dom = _domain_of(other)
                if dom:
                    ranges[id(const)] = (dom[0], dom[1], False, "domain")

    out: list[Literal] = []
    for i, node in enumerate(_numeric_constants(tree)):
        lo, hi, is_int, src = ranges.get(
            id(node), _fallback_range(float(node.value)))
        out.append(Literal(i, node.value, lo, hi, is_int, src))
    return out


def _unwrap_const(node) -> ast.Constant | None:
    """A threshold may be negated: `< -1.5` is UnaryOp(USub, Constant)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)) \
            and isinstance(node.operand, ast.Constant):
        return node.operand
    return None


def _domain_of(node) -> tuple | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return FEATURES[node.func.id].domain
    if isinstance(node, ast.Name):
        return FEATURES[node.id].domain
    return None


def _fallback_range(v: float) -> tuple:
    """No declared domain (a price level, an ATR multiple). Tune relative to
    the author's chosen value rather than refusing to tune it at all."""
    if v == 0:
        return (-1.0, 1.0, False, "fallback")
    lo, hi = sorted((v * 0.5, v * 2.0))
    return (lo, hi, float(v).is_integer() and abs(v) < 1000, "fallback")


def apply_literals(tree: ast.Expression, values) -> ast.Expression:
    """Return a NEW tree with each numeric literal replaced, in document
    order. Integer-typed literals are rounded so a period stays a period."""
    values = list(values)
    out = copy.deepcopy(tree)
    consts = _numeric_constants(out)
    if len(values) != len(consts):
        raise SpecError(f"expected {len(consts)} literal values, "
                        f"got {len(values)}")
    originals = extract_literals(tree)
    for node, v, lit in zip(consts, values, originals):
        node.value = int(round(float(v))) if lit.is_int else round(float(v), 6)
    return ast.fix_missing_locations(out)


def render(tree: ast.Expression) -> str:
    """Back to source. This text is what the Librarian prints, so it must
    always re-parse."""
    return ast.unparse(tree.body)
```

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_dsl.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/dsl.py tests/test_dsl.py
git commit -m "feat(dsl): literal extraction with inferred ranges — every number is a tunable gene"
```

---

### Task 8: The compiler

**Files:**
- Create: `trader/strategy/compile.py`
- Test: `tests/test_compile.py`

**Interfaces:**
- Consumes: `spec.StrategySpec`, `dsl.parse/evaluate_bool/data_requires/render`, `features.FeatureCtx`
- Produces: `compile_spec(spec) -> CompiledStrategy`; `CompiledStrategy.entries(frames, btc=None, derivs=None) -> (long: np.ndarray, short: np.ndarray)`; `.exit_signal(frames, ...) -> np.ndarray | None`; `.to_evaluator() -> Callable`; `.to_markdown() -> str`; `.data_requires: tuple`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_compile.py
import numpy as np
import pandas as pd
import pytest

from trader.core.types import Action, Snapshot
from trader.strategy.compile import compile_spec
from trader.strategy.dsl import SpecError
from trader.strategy.spec import ExitSpec, StrategySpec


def _spec(**kw) -> StrategySpec:
    base = dict(
        id="t1", name="Trend Pullback Probe",
        thesis="Short-horizon trends persist because discretionary entries lag "
               "the impulse; buying a shallow pullback inside an aligned stack "
               "captures the continuation at reduced adverse excursion.",
        invalidation="Retire below profit factor 1.0 over 30 out-of-sample trades.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe="15m", direction="long",
        entry_long="close > ema(20) and ema(20) > ema(50)",
        entry_short="", filters=["adx(14) > 20"],
        exit=ExitSpec(), regime_filter=["TRENDING_UP"], markets=["futures"])
    base.update(kw)
    return StrategySpec(**base)


@pytest.fixture
def frame():
    rng = np.random.default_rng(5)
    n = 600
    close = 100 * np.exp(np.cumsum(rng.normal(0.0002, 0.004, n)))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.002, "low": close * 0.998,
        "close": close, "volume": rng.uniform(50, 500, n)})


def test_entries_are_boolean_arrays_of_frame_length(frame):
    c = compile_spec(_spec())
    lo, sh = c.entries({"15m": frame})
    assert lo.dtype == bool and len(lo) == len(frame)
    assert not sh.any(), "a long-only spec must never emit shorts"


def test_filters_are_anded_with_entry(frame):
    with_f = compile_spec(_spec()).entries({"15m": frame})[0]
    without = compile_spec(_spec(filters=[])).entries({"15m": frame})[0]
    assert with_f.sum() <= without.sum()
    assert np.array_equal(with_f, with_f & without)


def test_data_requires_is_derived(frame):
    assert compile_spec(_spec()).data_requires == ("ohlcv",)


def test_invalid_expression_fails_at_compile_time(frame):
    with pytest.raises(SpecError):
        compile_spec(_spec(entry_long="close > nope(3)"))


def test_to_evaluator_matches_the_last_bar_of_entries(frame):
    c = compile_spec(_spec())
    lo, _ = c.entries({"15m": frame})
    ev = c.to_evaluator()
    snap = Snapshot(symbol="BTC/USDT", ts="", price=float(frame["close"].iloc[-1]),
                    dfs={"15m": frame}, market_type="futures")
    sig = ev(c.spec, snap)
    assert (sig is not None and sig.action == Action.BUY) == bool(lo[-1])


def test_to_evaluator_returns_strategy_signal_shape(frame):
    c = compile_spec(_spec(entry_long="close > ema(2)"))
    ev = c.to_evaluator()
    snap = Snapshot(symbol="ETH/USDT", ts="", price=1.0,
                    dfs={"15m": frame}, market_type="futures")
    sig = ev(c.spec, snap)
    if sig is not None:
        assert sig.strategy_id == "t1" and 0.0 < sig.confidence <= 1.0
        assert sig.symbol == "ETH/USDT" and sig.rationale


def test_markdown_contains_thesis_and_readable_logic():
    md = compile_spec(_spec()).to_markdown()
    assert "Trend Pullback Probe" in md
    assert "close > ema(20)" in md
    assert "adx(14) > 20" in md
    assert "Invalidation" in md
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_compile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trader.strategy.compile'`

- [ ] **Step 3: Implement `trader/strategy/compile.py`**

```python
"""Compile a StrategySpec into the three things the company needs from it:
vectorized entry arrays (Analyst), a live evaluator (Trader), and a readable
card (Librarian). One artifact, three consumers, no drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..core.types import Action, StrategySignal
from . import dsl
from .features import FeatureCtx
from .spec import StrategySpec


@dataclass
class CompiledStrategy:
    spec: StrategySpec
    _long: object | None = None
    _short: object | None = None
    _filters: list = field(default_factory=list)
    _exit: object | None = None
    data_requires: tuple = ("ohlcv",)

    # ── vectorized path (Analyst) ────────────────────────────────────────
    def entries(self, frames: dict, btc: dict | None = None,
                derivs: dict | None = None):
        ctx = self._ctx(frames, btc, derivs)
        n = len(ctx.index)
        keep = np.ones(n, dtype=bool)
        for f in self._filters:
            keep &= dsl.evaluate_bool(f, ctx)
        lo = (dsl.evaluate_bool(self._long, ctx) & keep) if self._long is not None \
            else np.zeros(n, dtype=bool)
        sh = (dsl.evaluate_bool(self._short, ctx) & keep) if self._short is not None \
            else np.zeros(n, dtype=bool)
        # a bar cannot be both; direction conflicts resolve to no trade
        both = lo & sh
        return lo & ~both, sh & ~both

    def exit_signal(self, frames: dict, btc=None, derivs=None):
        if self._exit is None:
            return None
        return dsl.evaluate_bool(self._exit, self._ctx(frames, btc, derivs))

    def _ctx(self, frames, btc, derivs) -> FeatureCtx:
        tf = self.spec.timeframe
        if tf not in frames:
            raise dsl.SpecError(f"spec timeframe '{tf}' not in frames "
                                f"{sorted(frames)}")
        return FeatureCtx(frames=frames, tf=tf, btc=btc, derivs=derivs)

    # ── live path (Trader) ───────────────────────────────────────────────
    def to_evaluator(self):
        """A callable with the EXACT signature library.evaluate() dispatches
        to: (genome_like, Snapshot) -> StrategySignal | None. This is why the
        orchestrator needs no change at cutover."""
        def _evaluate(_genome, snap):
            frames = {k: v for k, v in snap.dfs.items() if v is not None}
            if self.spec.timeframe not in frames:
                return None
            btc = {"15m": snap.dfs["BTC_1h"]} if "BTC_1h" in snap.dfs else None
            try:
                lo, sh = self.entries(frames, btc=btc)
            except Exception:
                return None
            if not len(lo):
                return None
            if lo[-1]:
                action, why = Action.BUY, self.spec.entry_long
            elif sh[-1]:
                action, why = Action.SELL, self.spec.entry_short
            else:
                return None
            return StrategySignal(
                strategy_id=self.spec.id, strategy_name=self.spec.name,
                symbol=snap.symbol, action=action,
                confidence=0.6,
                rationale=f"{self.spec.name}: {why}",
                params={"spec_id": self.spec.id})
        return _evaluate

    # ── librarian path ───────────────────────────────────────────────────
    def to_markdown(self) -> str:
        s = self.spec
        ex = s.exit
        lines = [
            f"# {s.name}", "",
            f"**Thesis** — {s.thesis}", "",
            f"**Invalidation** — {s.invalidation}", "",
            f"- Timeframe: `{s.timeframe}`  ·  Direction: `{s.direction}`",
            f"- Regimes: {', '.join(s.regime_filter) or 'any'}",
            f"- Data: {', '.join(self.data_requires)}",
            f"- Provenance: {s.provenance.get('source_kind', 'unknown')}"
            + (f" — {s.provenance['source_url']}" if s.provenance.get('source_url') else ""),
            "", "## Logic", "```",
        ]
        if s.entry_long:
            lines.append(f"long:   {s.entry_long}")
        if s.entry_short:
            lines.append(f"short:  {s.entry_short}")
        for f in s.filters:
            lines.append(f"filter: {f}")
        lines += [
            f"stop:   {ex.stop}",
            f"target: {ex.target}",
            f"trail:  {ex.trail}",
            f"time:   max {ex.time.get('max_bars')} bars",
        ]
        if ex.signal_exit:
            lines.append(f"exit:   {ex.signal_exit}")
        lines += ["```", ""]
        return "\n".join(lines)


def compile_spec(spec: StrategySpec) -> CompiledStrategy:
    """Parse every expression up front. A spec that cannot compile must never
    reach the gauntlet, let alone the book."""
    errs = StrategySpec.validate(spec)
    if errs:
        raise dsl.SpecError(f"invalid spec '{spec.id}': {errs}")
    long_t = dsl.parse(spec.entry_long) if spec.entry_long.strip() else None
    short_t = dsl.parse(spec.entry_short) if spec.entry_short.strip() else None
    filters = [dsl.parse(f) for f in spec.filters if (f or "").strip()]
    exit_t = dsl.parse(spec.exit.signal_exit) \
        if (spec.exit.signal_exit or "").strip() else None
    trees = [t for t in (long_t, short_t, exit_t, *filters) if t is not None]
    req = dsl.data_requires(*trees)
    spec.data_requires = list(req)
    return CompiledStrategy(spec=spec, _long=long_t, _short=short_t,
                            _filters=filters, _exit=exit_t, data_requires=req)
```

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_compile.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/compile.py tests/test_compile.py
git commit -m "feat(strategy): compiler — vectorized entries, live evaluator, librarian card"
```

---

### Task 9: Vectorized backtester

**Files:**
- Create: `trader/strategy/vector_backtest.py`
- Test: `tests/test_vector_backtest.py`

**Interfaces:**
- Consumes: `compile.CompiledStrategy`, `spec.ExitSpec`, `backtest.BacktestResult`
- Produces: `simulate(long, short, df, exit_spec, risk_cfg, equity=2000.0, genome_id="", symbol="BT") -> BacktestResult`; `vector_backtest(compiled, frames, risk_cfg, btc=None, derivs=None, equity=2000.0) -> BacktestResult`; `vector_walk_forward(compiled, frames, risk_cfg, split=0.7, ...) -> dict` with the same `{train, test, robust, train_fails, test_fails}` shape as `backtest.walk_forward`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_vector_backtest.py
import numpy as np
import pandas as pd
import pytest

from trader.core.config import load_config
from trader.strategy.spec import ExitSpec
from trader.strategy.vector_backtest import simulate


RISK = {"stop_loss_atr_mult": 2.5, "take_profit_atr_mult": 4.5,
        "taker_fee_pct": 0.05, "slippage_atr_frac": 0.06,
        "risk_per_trade_pct": 1.5, "funding_rate_8h": 0.0001,
        "bar_minutes": 15}


def _ramp(n=400, drift=0.004):
    """Deterministic monotonic uptrend: a long must win, a short must lose."""
    close = 100 * np.cumprod(np.full(n, 1 + drift))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 100.0)})


def test_no_entries_means_no_trades():
    df = _ramp()
    n = len(df)
    r = simulate(np.zeros(n, bool), np.zeros(n, bool), df, ExitSpec(), RISK)
    assert r.trades == 0 and r.pnl_usdt == 0.0


def test_long_in_uptrend_hits_target():
    df = _ramp()
    n = len(df)
    lo = np.zeros(n, bool); lo[250] = True
    r = simulate(lo, np.zeros(n, bool), df,
                 ExitSpec(stop={"kind": "atr", "mult": 2.0},
                          target={"kind": "atr", "mult": 3.0},
                          trail={"kind": "none"}, time={"max_bars": 100}),
                 RISK)
    assert r.trades == 1 and r.wins == 1 and r.pnl_usdt > 0


def test_short_in_uptrend_hits_stop():
    df = _ramp()
    n = len(df)
    sh = np.zeros(n, bool); sh[250] = True
    r = simulate(np.zeros(n, bool), sh, df, ExitSpec(time={"max_bars": 100}), RISK)
    assert r.trades == 1 and r.losses == 1 and r.pnl_usdt < 0


def test_only_one_position_at_a_time():
    df = _ramp()
    n = len(df)
    lo = np.zeros(n, bool); lo[250:280] = True
    r = simulate(lo, np.zeros(n, bool), df,
                 ExitSpec(time={"max_bars": 50}), RISK)
    assert r.trades <= 2, "overlapping entry bars must not stack positions"


def test_time_exit_closes_the_trade():
    """Flat market: neither stop nor target is reached, so time must exit."""
    n = 400
    close = np.full(n, 100.0)
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close, "low": close, "close": close,
        "volume": np.full(n, 100.0)})
    lo = np.zeros(n, bool); lo[250] = True
    r = simulate(lo, np.zeros(n, bool), df,
                 ExitSpec(time={"max_bars": 10}), RISK)
    assert r.trades == 1


def test_exit_spec_changes_the_result():
    """The whole point of putting exits in the spec: they must matter."""
    df = _ramp()
    n = len(df)
    lo = np.zeros(n, bool); lo[250] = True
    tight = simulate(lo, np.zeros(n, bool), df,
                     ExitSpec(target={"kind": "atr", "mult": 1.0},
                              time={"max_bars": 100}), RISK)
    wide = simulate(lo, np.zeros(n, bool), df,
                    ExitSpec(target={"kind": "atr", "mult": 8.0},
                             time={"max_bars": 100}), RISK)
    assert tight.pnl_usdt != wide.pnl_usdt


def test_fees_and_funding_are_charged():
    """A zero-drift round trip must lose money to costs, never break even."""
    n = 400
    close = np.full(n, 100.0)
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.0005, "low": close * 0.9995,
        "close": close, "volume": np.full(n, 100.0)})
    lo = np.zeros(n, bool); lo[250] = True
    r = simulate(lo, np.zeros(n, bool), df, ExitSpec(time={"max_bars": 20}), RISK)
    assert r.pnl_usdt < 0
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_vector_backtest.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `trader/strategy/vector_backtest.py`**

The fill and accounting model is copied from `backtest.py:137-208` deliberately
— identical pessimism (stop before target when both are touched intrabar),
identical fee/slippage/funding arithmetic — so the equivalence harness in
Task 10 can attribute any difference to the new exit geometry rather than to
an accounting change.

```python
"""Vectorized backtester.

The old engine called a Python evaluator once per bar: ~1.9 ms/bar/symbol, so
a 5-symbol x 8000-bar gauntlet cost ~76 s and had to run inside a 300 s brain
tick, capped at two candidates. Here the entry signal is a boolean array
computed once, and the simulation touches only the sparse bars where a trade
actually opens.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..agents.indicators import atr_series
from .backtest import BacktestResult
from .spec import ExitSpec

WARMUP = 210


def _stop_distance(exit_spec: ExitSpec, price: float, atr: float,
                   df: pd.DataFrame, i: int, side: str) -> float:
    kind = exit_spec.stop.get("kind", "atr")
    if kind == "atr":
        d = atr * float(exit_spec.stop.get("mult", 2.0))
    elif kind == "pct":
        d = price * float(exit_spec.stop.get("v", 0.01))
    else:                                   # swing
        n = int(exit_spec.stop.get("lookback", 20))
        w = df.iloc[max(0, i - n):i + 1]
        d = (price - float(w["low"].min())) if side == "long" \
            else (float(w["high"].max()) - price)
    return max(d, price * 0.004)            # same floor as the old engine


def _target_distance(exit_spec: ExitSpec, price: float, atr: float,
                     stop_dist: float) -> float | None:
    t = exit_spec.target
    kind = t.get("kind", "rr")
    if kind == "none":
        return None
    if kind == "atr":
        return atr * float(t.get("mult", 3.0))
    if kind == "rr":
        return stop_dist * float(t.get("v", 2.0))
    return price * float(t.get("v", 0.02))          # pct


def simulate(long: np.ndarray, short: np.ndarray, df: pd.DataFrame,
             exit_spec: ExitSpec, risk_cfg: dict, equity: float = 2000.0,
             genome_id: str = "", symbol: str = "BT",
             exit_sig: np.ndarray | None = None) -> BacktestResult:
    res = BacktestResult(genome_id=genome_id, symbol=symbol, bars=len(df))
    fee = float(risk_cfg.get("taker_fee_pct", 0.05)) / 100.0
    slip_frac = float(risk_cfg.get("slippage_atr_frac", 0.06))
    risk_frac = float(risk_cfg["risk_per_trade_pct"]) / 100.0
    funding_8h = float(risk_cfg.get("funding_rate_8h", 0.0001))
    bar_minutes = float(risk_cfg.get("bar_minutes", 15))

    closes = df["close"].to_numpy(float)
    highs = df["high"].to_numpy(float)
    lows = df["low"].to_numpy(float)
    atr = atr_series(df, 14).to_numpy(float)
    n = len(df)
    max_bars = int(exit_spec.time.get("max_bars", 32) or 32)
    trail = exit_spec.trail or {"kind": "none"}
    trail_mult = float(trail.get("mult", 0.0)) if trail.get("kind") == "atr" else 0.0
    arm_at_r = float(trail.get("arm_at_r", 1.0))

    equity_curve = [equity]
    peak = equity
    candidates = np.flatnonzero((long | short))
    cursor = WARMUP

    for i in candidates:
        i = int(i)
        if i < cursor or i >= n - 1 or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        side = "long" if long[i] else "short"
        sign = 1.0 if side == "long" else -1.0
        px = closes[i]
        slip = atr[i] * slip_frac
        entry = px + sign * slip
        sl_dist = _stop_distance(exit_spec, entry, atr[i], df, i, side)
        tp_dist = _target_distance(exit_spec, entry, atr[i], sl_dist)
        sl = entry - sign * sl_dist
        tp = entry + sign * tp_dist if tp_dist is not None else None
        amount = (equity_curve[-1] * risk_frac) / sl_dist
        if amount * px < 10:                          # dust guard
            continue

        end = min(i + max_bars, n - 1)
        exit_i, exit_px = end, closes[end]
        best = entry
        for j in range(i + 1, end + 1):
            if trail_mult:
                best = max(best, highs[j]) if side == "long" \
                    else min(best, lows[j])
                if abs(best - entry) >= arm_at_r * sl_dist:
                    trailed = best - sign * trail_mult * atr[j]
                    sl = max(sl, trailed) if side == "long" else min(sl, trailed)
            hit_sl = lows[j] <= sl if side == "long" else highs[j] >= sl
            hit_tp = tp is not None and (
                highs[j] >= tp if side == "long" else lows[j] <= tp)
            if hit_sl:                                # pessimistic: stop first
                exit_i, exit_px = j, sl
                break
            if hit_tp:
                exit_i, exit_px = j, tp
                break
            if exit_sig is not None and exit_sig[j]:
                exit_i, exit_px = j, closes[j]
                break

        exit_px -= sign * slip                        # exits pay the spread too
        gross = (exit_px - entry) * sign * amount
        fees = fee * (entry + exit_px) * amount
        hours = (exit_i - i) * bar_minutes / 60.0
        funding = abs(funding_8h) * (hours / 8.0) * exit_px * amount
        pnl = gross - fees - funding

        res.trades += 1
        res.pnl_usdt += pnl
        if pnl > 0:
            res.wins += 1
            res.gross_win += pnl
        else:
            res.losses += 1
            res.gross_loss += abs(pnl)
        equity_curve.append(equity_curve[-1] + pnl)
        peak = max(peak, equity_curve[-1])
        if peak > 0:
            res.max_dd_pct = max(res.max_dd_pct,
                                 (peak - equity_curve[-1]) / peak * 100)
        cursor = exit_i + 1                           # one position at a time

    return res


def vector_backtest(compiled, frames: dict, risk_cfg: dict, btc=None,
                    derivs=None, equity: float = 2000.0,
                    symbol: str = "BT") -> BacktestResult:
    lo, sh = compiled.entries(frames, btc=btc, derivs=derivs)
    ex = compiled.exit_signal(frames, btc=btc, derivs=derivs)
    return simulate(lo, sh, frames[compiled.spec.timeframe], compiled.spec.exit,
                    risk_cfg, equity=equity,
                    genome_id=compiled.spec.id, symbol=symbol, exit_sig=ex)


def vector_walk_forward(compiled, frames: dict, risk_cfg: dict,
                        split: float = 0.7, btc=None, derivs=None,
                        symbol: str = "BT") -> dict:
    """Same contract as backtest.walk_forward so evidence.py can call either.

    Signals are computed ONCE over the full frame and then sliced, so an
    indicator near the split boundary is warmed up exactly as it would be
    live — the old engine re-ran the evaluator on a truncated frame, which
    cost the test half its first 210 bars.
    """
    tf = compiled.spec.timeframe
    df = frames[tf]
    lo, sh = compiled.entries(frames, btc=btc, derivs=derivs)
    ex = compiled.exit_signal(frames, btc=btc, derivs=derivs)
    cut = int(len(df) * split)

    def run(a, b):
        return simulate(lo[a:b], sh[a:b], df.iloc[a:b].reset_index(drop=True),
                        compiled.spec.exit, risk_cfg,
                        genome_id=compiled.spec.id, symbol=symbol,
                        exit_sig=None if ex is None else ex[a:b])

    train, test = run(0, cut), run(cut, len(df))
    ok_train, f_train = train.passes()
    ok_test, f_test = test.passes(min_trades=max(3, int(train.trades * 0.2)),
                                  min_pf=1.0)
    return {"train": train, "test": test, "robust": ok_train and ok_test,
            "train_fails": f_train, "test_fails": f_test}
```

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_vector_backtest.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/vector_backtest.py tests/test_vector_backtest.py
git commit -m "feat(strategy): vectorized backtester with spec-owned exit geometry"
```

---

### Task 10: Engine equivalence — same signals in, same trades out

This is the gate that makes the cutover evidence-based rather than
faith-based. It isolates the **engine**: feed both backtesters the identical
entry signals and the identical exit geometry, and require identical trades.
Any difference is an accounting bug, not a strategy difference.

**Files:**
- Create: `scripts/backtest_equivalence.py`
- Test: `tests/test_vector_backtest.py` (append)

**Interfaces:**
- Consumes: `backtest.backtest`, `vector_backtest.simulate`, `library.EVALUATORS`
- Produces: `scripts/backtest_equivalence.py::engine_equivalence(genome, df, risk_cfg) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_vector_backtest.py
def test_engine_equivalence_on_legacy_genome():
    """Old and new engines must produce identical trades from identical
    signals. Config-equivalent ExitSpec: the old engine's global SL/TP ATR
    multiples and its `max_hold_bars` default of 32."""
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
    from scripts.backtest_equivalence import engine_equivalence
    from trader.strategy.library import build_seed_population

    df = _ramp(600, drift=0.0)             # flat-ish; exercise both exits
    rng = np.random.default_rng(9)
    noise = np.cumprod(1 + rng.normal(0, 0.004, len(df)))
    for c in ("open", "high", "low", "close"):
        df[c] = df[c] * noise
    df["high"] = df[["open", "high", "close"]].max(axis=1) * 1.001
    df["low"] = df[["open", "low", "close"]].min(axis=1) * 0.999

    for _st, g in build_seed_population():
        rep = engine_equivalence(g, df, RISK)
        if rep["signals"] == 0:
            continue
        assert rep["match"], f"{g.family}: {rep}"
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_vector_backtest.py -k equivalence -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.backtest_equivalence'`

- [ ] **Step 3: Implement `scripts/backtest_equivalence.py`**

```python
"""Equivalence harness — the gate on the vectorized engine.

Two independent claims, tested separately:

  engine_equivalence  — identical signals + identical exit geometry must
                        produce identical trades in both engines. Isolates
                        the fill and accounting model.
  signal_equivalence  — a ported spec's DSL entry array must match the
                        legacy evaluator's bar-by-bar signals. Isolates the
                        translation. Deltas here are expected for families
                        whose Python logic is not expressible one-to-one;
                        they are REPORTED, not asserted away.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy import library as strat_lib          # noqa: E402
from trader.strategy.backtest import _snap, context_frames  # noqa: E402
from trader.strategy.spec import ExitSpec                  # noqa: E402
from trader.strategy.vector_backtest import WARMUP, simulate  # noqa: E402
from trader.core.types import Action                       # noqa: E402


def legacy_signals(genome, df: pd.DataFrame, ctx: dict | None = None):
    """Replay the legacy evaluator bar-by-bar and record its raw signals.

    This deliberately does NOT skip bars while a position is open — we want
    the unconditioned signal array so the two engines can be fed the same
    input. Position management is the engines' job.
    """
    n = len(df)
    lo = np.zeros(n, dtype=bool)
    sh = np.zeros(n, dtype=bool)
    for i in range(WARMUP, n - 1):
        window = df.iloc[max(0, i - 400):i + 1]
        try:
            sig = strat_lib.evaluate(
                genome, _snap(window, float(df["close"].iloc[i]), ctx))
        except Exception:
            sig = None
        if sig is None:
            continue
        if sig.action == Action.BUY:
            lo[i] = True
        elif sig.action == Action.SELL:
            sh[i] = True
    return lo, sh


def config_equivalent_exit(risk_cfg: dict, max_hold: int = 32) -> ExitSpec:
    """The exit geometry the OLD engine hardcoded for every strategy."""
    return ExitSpec(
        stop={"kind": "atr", "mult": float(risk_cfg["stop_loss_atr_mult"])},
        target={"kind": "atr", "mult": float(risk_cfg["take_profit_atr_mult"])},
        trail={"kind": "none"},
        time={"max_bars": max_hold},
        signal_exit="")


def engine_equivalence(genome, df: pd.DataFrame, risk_cfg: dict) -> dict:
    from trader.strategy.backtest import backtest as legacy_backtest

    lo, sh = legacy_signals(genome, df)
    old = legacy_backtest(genome, df, risk_cfg)
    new = simulate(lo, sh, df, config_equivalent_exit(risk_cfg), risk_cfg,
                   genome_id=genome.strategy_id)
    match = (old.trades == new.trades and old.wins == new.wins
             and abs(old.pnl_usdt - new.pnl_usdt) < max(
                 0.01, abs(old.pnl_usdt) * 0.005))
    return {"family": genome.family, "signals": int(lo.sum() + sh.sum()),
            "old": {"trades": old.trades, "wins": old.wins,
                    "pnl": round(old.pnl_usdt, 4)},
            "new": {"trades": new.trades, "wins": new.wins,
                    "pnl": round(new.pnl_usdt, 4)},
            "match": bool(match)}


def signal_equivalence(compiled, genome, frames: dict, btc=None) -> dict:
    tf = compiled.spec.timeframe
    df = frames[tf]
    ctx = context_frames(df, btc)
    old_lo, old_sh = legacy_signals(genome, df, ctx)
    new_lo, new_sh = compiled.entries(frames, btc={"15m": btc} if btc is not None else None)
    agree = int((old_lo == new_lo).sum() + (old_sh == new_sh).sum())
    total = len(old_lo) * 2
    return {"spec": compiled.spec.id, "family": genome.family,
            "legacy_signals": int(old_lo.sum() + old_sh.sum()),
            "spec_signals": int(new_lo.sum() + new_sh.sum()),
            "bar_agreement": round(agree / total, 4)}


if __name__ == "__main__":
    import json
    from trader.core.config import load_config
    from trader.data.feed import DataFeed
    from trader.strategy import evidence
    from trader.strategy.library import build_seed_population

    cfg = load_config()
    frames = evidence.load_frames(DataFeed(), cfg)
    sym = next(k for k in frames if not k.startswith("_"))
    df = frames[sym]
    print(f"engine equivalence on {sym} ({len(df)} bars)\n")
    ok = True
    for _st, g in build_seed_population():
        rep = engine_equivalence(g, df, cfg["risk"])
        ok &= rep["match"] or rep["signals"] == 0
        print(json.dumps(rep))
    print("\nENGINE EQUIVALENCE:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
```

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_vector_backtest.py -k equivalence -v`
Expected: PASS.

If it fails, the report names the divergence. Likely causes, in order:
(1) the old engine advances `i += step` from the *signal* bar while the new
one resumes at `exit_i + 1` — check `cursor`; (2) the old engine's ATR comes
from a 400-bar window slice (`backtest.py:167,195`) while `atr_series` is
computed over the full frame — these agree once warmed up but not at the
boundary; (3) the old engine closes any open position at the final bar
(`backtest.py:210-211`). Fix the new engine to match the old one exactly —
the old engine is the reference for *this* test even where its behaviour is
imperfect, because the point is to isolate the change.

- [ ] **Step 5: Run against real candles**

Run: `./venv/bin/python scripts/backtest_equivalence.py`
Expected: `ENGINE EQUIVALENCE: PASS`

- [ ] **Step 6: Commit**

```bash
git add scripts/backtest_equivalence.py tests/test_vector_backtest.py
git commit -m "test(strategy): engine equivalence harness — old vs new backtester on identical signals"
```

---

### Task 11: Port the eight families to seed specs

**Files:**
- Create: `data/seed_specs/*.json` (8 files)
- Create: `trader/strategy/seed_specs.py`
- Test: `tests/test_compile.py` (append)

**Interfaces:**
- Consumes: `spec.StrategySpec`, `compile.compile_spec`
- Produces: `seed_specs.load_seed_specs() -> list[StrategySpec]`, `seed_specs.SEED_DIR`

Each ported spec keeps its family's real hypothesis from
`library.build_seed_population()` — not the copy-pasted one
`proposer._hypothesis()` handed to every variant — and now carries its own
exit geometry instead of inheriting the global 2.5/4.5/32.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_compile.py
from trader.strategy.seed_specs import load_seed_specs


def test_all_seed_specs_compile():
    specs = load_seed_specs()
    assert len(specs) >= 8
    for s in specs:
        compile_spec(s)          # raises SpecError on anything malformed


def test_seed_specs_have_distinct_theses():
    theses = [s.thesis for s in load_seed_specs()]
    assert len(set(theses)) == len(theses), \
        "every spec must state its OWN inefficiency — the bug that made every " \
        "ema_trend variant claim the seed's hypothesis"


def test_seed_specs_have_real_names():
    for s in load_seed_specs():
        assert "variant" not in s.name.lower()


def test_seed_specs_emit_signals_on_real_shaped_data(frame):
    fired = []
    for s in load_seed_specs():
        if s.timeframe != "15m":
            continue
        lo, sh = compile_spec(s).entries({"15m": frame})
        fired.append((s.id, int(lo.sum() + sh.sum())))
    assert sum(n for _, n in fired) > 0, f"no seed spec ever fires: {fired}"
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_compile.py -k seed -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trader.strategy.seed_specs'`

- [ ] **Step 3: Write the loader**

```python
# trader/strategy/seed_specs.py
"""Seed specs — the eight legacy families, ported.

These exist so the equivalence harness has something to compare and so the
book is never empty. They are NOT the interesting output of this system: the
authored strategy library (next plan) is.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..core.config import ROOT
from .spec import StrategySpec

SEED_DIR = ROOT / "data" / "seed_specs"


def load_seed_specs() -> list[StrategySpec]:
    out = []
    for p in sorted(SEED_DIR.glob("*.json")):
        out.append(StrategySpec.from_dict(json.loads(p.read_text())))
    return out
```

- [ ] **Step 4: Write the eight spec files**

Write one JSON per family into `data/seed_specs/`. Two worked examples —
follow the same shape for the remaining six (`vwap_fade`, `breakout_retest`,
`sweep_reversal`, `rotation_momo`, `ma_cross`, `bb_fade`), taking each
`thesis` verbatim from that family's `hypothesis` in
`library.build_seed_population()` and each `invalidation` from its
`invalidation` field.

`data/seed_specs/ema_stack_pullback.json`:

```json
{
  "id": "seed_ema_stack_pullback",
  "name": "EMA Stack Pullback",
  "thesis": "Trends persist short-term due to anchoring and delayed discretionary entry; buying shallow pullbacks within an aligned EMA stack captures continuation at reduced risk.",
  "invalidation": "Demote after 6 consecutive losses or profit factor below 0.8 over 20 trades.",
  "provenance": {"source_kind": "seed", "parent": "library.build_seed_population"},
  "universe": {"include": [], "exclude": [], "min_volume_usdt": 0},
  "timeframe": "15m",
  "direction": "both",
  "entry_long": "close > ema(20) and ema(20) > ema(50) and (ema(20) - close) <= 0.8 * max(abs(ema(20) - ema(50)), close * 0.002)",
  "entry_short": "close < ema(20) and ema(20) < ema(50) and (close - ema(20)) <= 0.8 * max(abs(ema(20) - ema(50)), close * 0.002)",
  "filters": ["adx(14) > 22"],
  "exit": {
    "stop": {"kind": "atr", "mult": 2.5},
    "target": {"kind": "atr", "mult": 4.5},
    "trail": {"kind": "none"},
    "time": {"max_bars": 32},
    "signal_exit": ""
  },
  "regime_filter": ["TRENDING_UP", "TRENDING_DOWN"],
  "markets": ["futures"],
  "generation": 0,
  "parent_id": "",
  "data_requires": ["ohlcv"]
}
```

`data/seed_specs/rsi_exhaustion_reclaim.json`:

```json
{
  "id": "seed_rsi_exhaustion_reclaim",
  "name": "RSI Exhaustion Reclaim",
  "thesis": "Momentum extremes overshoot because trend-followers and forced liquidations pile into the same direction; the reclaim of an oversold or overbought threshold marks the point at which that flow is exhausted and reverses.",
  "invalidation": "Demote if win rate falls below 40% over 15 trades or profit factor below 1.0 over 20.",
  "provenance": {"source_kind": "seed", "parent": "library.eval_rsi_extreme"},
  "universe": {"include": [], "exclude": [], "min_volume_usdt": 0},
  "timeframe": "15m",
  "direction": "both",
  "entry_long": "rsi(14) >= 30 and htf('15m', rsi(14)) >= 30",
  "entry_short": "rsi(14) <= 70",
  "filters": ["adx(14) < 30"],
  "exit": {
    "stop": {"kind": "atr", "mult": 1.8},
    "target": {"kind": "rr", "v": 1.8},
    "trail": {"kind": "none"},
    "time": {"max_bars": 24},
    "signal_exit": ""
  },
  "regime_filter": ["RANGING", "VOLATILE"],
  "markets": ["futures"],
  "generation": 0,
  "parent_id": "",
  "data_requires": ["ohlcv"]
}
```

**Note on faithfulness:** the `rsi_exhaustion_reclaim.json` above is shown
with a placeholder `entry_long` for illustration. The legacy
`eval_rsi_extreme` fires on a *crossing* (`prev < os_level <= now`), so the
faithful port uses the `prev()` feature from Task 4:

- `entry_long`: `prev(rsi(14), 1) < 30 and rsi(14) >= 30`
- `entry_short`: `prev(rsi(14), 1) > 70 and rsi(14) <= 70`

Write those into the file rather than the illustrative version.

**The remaining six.** Each takes its `thesis` verbatim from that family's
`hypothesis` in `library.build_seed_population()` (or, for the three families
with no seed, from `harvester.FAMILY_DOCS` expanded into a full sentence) and
its `invalidation` from the seed's `invalidation`. The entry logic, ported
from `library.py`:

| file | direction | entry_long | entry_short | filters | exit stop / target / max_bars |
|---|---|---|---|---|---|
| `vwap_extreme_fade.json` | both | `zscore((close - vwap(96)) / vwap(96), 96) < -2.5` | `zscore((close - vwap(96)) / vwap(96), 96) > 2.5` | — | atr 2.0 / rr 1.5 / 24 |
| `breakout_retest.json` | both | `prev(close, 1) > donchian_hi(48) and low - donchian_hi(48) <= 0.5 * atr(14)` | `prev(close, 1) < donchian_lo(48) and donchian_lo(48) - high <= 0.5 * atr(14)` | see the volume filter below | atr 2.5 / atr 4.5 / 32 |
| `liquidity_sweep_reversal.json` | both | `low < donchian_lo(60) * 0.998 and close > donchian_lo(60)` | `high > donchian_hi(60) * 1.002 and close < donchian_hi(60)` | — | atr 1.5 / rr 2.0 / 16 |
| `btc_rotation_momentum.json` | long | `btc_ret(4) > 0.008 and ret(4) > 0 and rel_strength_btc(4) < 0` | — | `corr_btc(96) > 0.3` | atr 2.0 / rr 2.0 / 24 |
| `ma_crossover.json` | both | `prev(ema(20), 1) <= prev(ema(50), 1) and ema(20) > ema(50)` | `prev(ema(20), 1) >= prev(ema(50), 1) and ema(20) < ema(50)` | — | atr 2.5 / atr 4.5 / 48 |
| `bollinger_band_fade.json` | both | `prev(close, 1) < prev(bb_lower(20, 2.0), 1) and close > bb_lower(20, 2.0)` | `prev(close, 1) > prev(bb_upper(20, 2.0), 1) and close < bb_upper(20, 2.0)` | `adx(14) < 25` | atr 1.8 / rr 1.5 / 20 |

For `breakout_retest`, the legacy volume test compares a 3-bar mean against
1.4x a 48-bar mean ending before the range window (`library.py:85-86`). Add a
`sma_of(expr, n)` feature to express it exactly rather than substituting a
weaker filter:

```python
@register("sma_of", arg_specs=(SERIES_ARG, (int, 2, 500)))
def _sma_of(ctx, s, n):
    return _series(ctx, s).rolling(int(n)).mean()
```

Then the filter is `sma_of(volume, 3) > 1.4 * prev(sma_of(volume, 48), 48)`.

- [ ] **Step 5: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_compile.py tests/test_features.py -v`
Expected: all pass

- [ ] **Step 6: Report signal equivalence (report, do not assert)**

Run:
```bash
./venv/bin/python -c "
from trader.core.config import load_config
from trader.data.feed import DataFeed
from trader.strategy import evidence
from trader.strategy.compile import compile_spec
from trader.strategy.seed_specs import load_seed_specs
from trader.strategy.library import build_seed_population
from scripts.backtest_equivalence import signal_equivalence
import json
cfg=load_config(); frames=evidence.load_frames(DataFeed(), cfg)
sym=next(k for k in frames if not k.startswith('_'))
by_fam={g.family:g for _s,g in build_seed_population()}
for s in load_seed_specs():
    fam=s.provenance.get('family') or s.id.replace('seed_','')
    g=by_fam.get(fam)
    if g: print(json.dumps(signal_equivalence(compile_spec(s), g, {s.timeframe: frames[sym]}, frames.get('_btc_1h'))))
"
```
Record the `bar_agreement` per family in the commit message. Anything below
0.98 needs a one-line explanation of *why* the port differs — that note is
the deliverable, not a passing number.

- [ ] **Step 7: Commit**

```bash
git add data/seed_specs trader/strategy/seed_specs.py trader/strategy/features.py tests/
git commit -m "feat(strategy): port 8 legacy families to seed specs with their own exits

Signal agreement vs legacy evaluators: <paste the per-family numbers>"
```

---

### Task 12: Speed benchmark

**Files:**
- Create: `scripts/bench_vector_backtest.py`

**Interfaces:**
- Consumes: `evidence.load_frames`, `vector_backtest`, `backtest.backtest`
- Produces: a printed speedup factor; exit code 1 below 20x

- [ ] **Step 1: Write the benchmark**

```python
"""Prove the throughput claim: the old engine cost ~1.9 ms/bar/symbol, which
is why gauntlet_max_full_runs is 2. Target >= 20x; the spec's design point
is ~100x.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.config import load_config
from trader.data.feed import DataFeed
from trader.strategy import evidence
from trader.strategy.backtest import backtest as legacy_backtest, context_frames
from trader.strategy.compile import compile_spec
from trader.strategy.library import build_seed_population
from trader.strategy.seed_specs import load_seed_specs
from trader.strategy.vector_backtest import vector_backtest

MIN_SPEEDUP = 20.0

cfg = load_config()
frames = evidence.load_frames(DataFeed(), cfg)
syms = [k for k in frames if not k.startswith("_")]
btc = frames.get("_btc_1h")
print(f"symbols={syms} bars={ {s: len(frames[s]) for s in syms} }")

genomes = [g for _s, g in build_seed_population()]
t0 = time.perf_counter()
for g in genomes:
    for s in syms:
        legacy_backtest(g, frames[s], cfg["risk"],
                        ctx=context_frames(frames[s], btc))
t_old = time.perf_counter() - t0

specs = load_seed_specs()[:len(genomes)]
compiled = [compile_spec(s) for s in specs]
t0 = time.perf_counter()
for c in compiled:
    for s in syms:
        vector_backtest(c, {c.spec.timeframe: frames[s]}, cfg["risk"],
                        btc={"15m": btc} if btc is not None else None, symbol=s)
t_new = time.perf_counter() - t0

speedup = t_old / max(t_new, 1e-9)
print(json.dumps({"runs": len(genomes) * len(syms),
                  "old_s": round(t_old, 2), "new_s": round(t_new, 3),
                  "speedup": round(speedup, 1)}, indent=2))
print("BENCH:", "PASS" if speedup >= MIN_SPEEDUP else "FAIL")
sys.exit(0 if speedup >= MIN_SPEEDUP else 1)
```

- [ ] **Step 2: Run it**

Run: `./venv/bin/python scripts/bench_vector_backtest.py`
Expected: `BENCH: PASS` with a speedup ≥ 20.

If it fails, the hot spot is almost certainly the per-entry `for j in range(...)`
loop in `simulate()`. Replace the inner scan with a vectorized
`np.argmax` over the bounded window for the no-trail case (trailing genuinely
needs the sequential pass, but most specs do not trail).

- [ ] **Step 3: Commit**

```bash
git add scripts/bench_vector_backtest.py
git commit -m "test(strategy): backtest speed benchmark — <paste speedup>x"
```

---

### Task 13: Derivatives and flow feed

Ships **now**, before anything consumes it: `openInterestHist`,
`takerlongshortRatio` and the long/short ratio endpoints retain only ~30 days,
so every day without a recorder is a day of history permanently lost.

**Files:**
- Create: `trader/data/derivatives.py`
- Modify: `config.yaml`
- Test: `tests/test_derivatives.py`

**Interfaces:**
- Consumes: `requests`, sqlite3
- Produces: `DerivFeed(db_path=None)` with `.funding(symbol, limit=1000) -> DataFrame[ts, funding_rate]`, `.open_interest(symbol, period="15m", limit=500) -> DataFrame[ts, oi]`, `.taker_ratio(symbol, period="15m", limit=500) -> DataFrame[ts, taker_ratio]`, `.ls_ratio(symbol, period="15m", limit=500, kind="top") -> DataFrame[ts, ls_ratio]`, `.record_all(symbols) -> dict`, `.load(symbol, series) -> DataFrame | None`

- [ ] **Step 1: Write the failing tests** (no network — the store and the parsing are what matter)

```python
# tests/test_derivatives.py
import pandas as pd
import pytest

from trader.data.derivatives import SERIES, DerivFeed


@pytest.fixture
def feed(tmp_path):
    return DerivFeed(db_path=tmp_path / "derivs.db")


def test_store_roundtrip(feed):
    df = pd.DataFrame({
        "ts": pd.date_range("2026-08-01", periods=10, freq="15min", tz="UTC"),
        "value": range(10)})
    feed.save("BTC/USDT", "funding", df)
    out = feed.load("BTC/USDT", "funding")
    assert len(out) == 10 and out["value"].iloc[-1] == 9


def test_store_is_idempotent(feed):
    df = pd.DataFrame({
        "ts": pd.date_range("2026-08-01", periods=5, freq="15min", tz="UTC"),
        "value": [1.0] * 5})
    feed.save("BTC/USDT", "oi", df)
    feed.save("BTC/USDT", "oi", df)
    assert len(feed.load("BTC/USDT", "oi")) == 5


def test_store_merges_new_observations(feed):
    a = pd.DataFrame({"ts": pd.date_range("2026-08-01", periods=5,
                                          freq="15min", tz="UTC"),
                      "value": [1.0] * 5})
    b = pd.DataFrame({"ts": pd.date_range("2026-08-01 01:00", periods=5,
                                          freq="15min", tz="UTC"),
                      "value": [2.0] * 5})
    feed.save("BTC/USDT", "oi", a)
    feed.save("BTC/USDT", "oi", b)
    assert len(feed.load("BTC/USDT", "oi")) == 9      # one overlapping bar


def test_missing_series_returns_none(feed):
    assert feed.load("BTC/USDT", "funding") is None


def test_known_series_names():
    assert SERIES == ("funding", "oi", "taker_ratio", "ls_ratio", "basis")


def test_parse_funding_payload(feed):
    raw = [{"symbol": "BTCUSDT", "fundingTime": 1756000000000,
            "fundingRate": "0.0001"}]
    df = feed._parse_funding(raw)
    assert list(df.columns) == ["ts", "value"]
    assert df["value"].iloc[0] == pytest.approx(0.0001)


def test_parse_open_interest_payload(feed):
    raw = [{"symbol": "BTCUSDT", "sumOpenInterest": "12345.6",
            "sumOpenInterestValue": "1.0", "timestamp": 1756000000000}]
    df = feed._parse_oi(raw)
    assert df["value"].iloc[0] == pytest.approx(12345.6)


def test_parse_taker_ratio_payload(feed):
    raw = [{"buySellRatio": "1.42", "timestamp": 1756000000000}]
    df = feed._parse_ratio(raw, "buySellRatio")
    assert df["value"].iloc[0] == pytest.approx(1.42)
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_derivatives.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `trader/data/derivatives.py`**

```python
"""Derivatives and flow data — the inputs that make non-obvious strategies
possible.

OHLCV can only yield strategies that have been invented a million times. The
crypto-native mechanisms — funding exhaustion, open-interest divergence,
basis dislocation, aggressor imbalance — need the data below.

Retention is asymmetric and it drives the design:
  funding   /fapi/v1/fundingRate                 years   -> backtestable now
  basis     spot vs perp klines                  years   -> backtestable now
  oi        /futures/data/openInterestHist       ~30 d   -> record forward
  taker     /futures/data/takerlongshortRatio    ~30 d   -> record forward
  ls_ratio  /futures/data/topLongShortPositionRatio ~30 d -> record forward

Because the ~30-day series cannot be backfilled, the recorder runs from day
one and history accumulates past the API window.
"""
from __future__ import annotations

import logging
import threading
import time

import pandas as pd
import requests

log = logging.getLogger(__name__)

FAPI = "https://fapi.binance.com"
SERIES = ("funding", "oi", "taker_ratio", "ls_ratio", "basis")
UA = {"User-Agent": "luffy/1.0"}


def to_binance(symbol: str) -> str:
    return symbol.replace("/", "").replace(":USDT", "")


class DerivFeed:
    def __init__(self, db_path=None, timeout: int = 15):
        self._db_path = str(db_path) if db_path else None
        self._local = threading.local()
        self.timeout = timeout

    # ── store ────────────────────────────────────────────────────────────
    @property
    def db(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            import sqlite3
            if self._db_path is None:
                from ..core.config import ROOT
                self._db_path = str(ROOT / "data" / "derivs.db")
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS derivs ("
                "symbol TEXT NOT NULL, series TEXT NOT NULL, "
                "ts INTEGER NOT NULL, value REAL, "
                "PRIMARY KEY (symbol, series, ts))")
            conn.commit()
            self._local.conn = conn
        return conn

    def save(self, symbol: str, series: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        ms = pd.to_datetime(df["ts"], utc=True).astype("int64") // 10 ** 6
        try:
            self.db.executemany(
                "INSERT OR REPLACE INTO derivs VALUES (?,?,?,?)",
                [(symbol, series, int(t), float(v))
                 for t, v in zip(ms, df["value"])])
            self.db.commit()
        except Exception as e:
            log.warning(f"derivs write {symbol} {series}: {e}")

    def load(self, symbol: str, series: str,
             limit: int = 200000) -> pd.DataFrame | None:
        rows = self.db.execute(
            "SELECT ts, value FROM derivs WHERE symbol=? AND series=? "
            "ORDER BY ts DESC LIMIT ?", (symbol, series, limit)).fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows[::-1], columns=["ts", "value"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df

    # ── fetch ────────────────────────────────────────────────────────────
    def _get(self, path: str, params: dict) -> list:
        try:
            r = requests.get(f"{FAPI}{path}", params=params, headers=UA,
                             timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []
        except Exception as e:
            log.warning(f"derivs fetch {path} {params.get('symbol')}: {e}")
            return []

    @staticmethod
    def _frame(pairs: list) -> pd.DataFrame:
        df = pd.DataFrame(pairs, columns=["ts", "value"])
        df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
        return df.dropna().sort_values("ts").reset_index(drop=True)

    def _parse_funding(self, raw: list) -> pd.DataFrame:
        return self._frame([(int(r["fundingTime"]), float(r["fundingRate"]))
                            for r in raw])

    def _parse_oi(self, raw: list) -> pd.DataFrame:
        return self._frame([(int(r["timestamp"]), float(r["sumOpenInterest"]))
                            for r in raw])

    def _parse_ratio(self, raw: list, key: str) -> pd.DataFrame:
        return self._frame([(int(r["timestamp"]), float(r[key])) for r in raw])

    def funding(self, symbol: str, limit: int = 1000) -> pd.DataFrame:
        return self._parse_funding(self._get(
            "/fapi/v1/fundingRate",
            {"symbol": to_binance(symbol), "limit": min(limit, 1000)}))

    def open_interest(self, symbol: str, period: str = "15m",
                      limit: int = 500) -> pd.DataFrame:
        return self._parse_oi(self._get(
            "/futures/data/openInterestHist",
            {"symbol": to_binance(symbol), "period": period,
             "limit": min(limit, 500)}))

    def taker_ratio(self, symbol: str, period: str = "15m",
                    limit: int = 500) -> pd.DataFrame:
        return self._parse_ratio(self._get(
            "/futures/data/takerlongshortRatio",
            {"symbol": to_binance(symbol), "period": period,
             "limit": min(limit, 500)}), "buySellRatio")

    def ls_ratio(self, symbol: str, period: str = "15m", limit: int = 500,
                 kind: str = "top") -> pd.DataFrame:
        path = ("/futures/data/topLongShortPositionRatio" if kind == "top"
                else "/futures/data/globalLongShortAccountRatio")
        return self._parse_ratio(self._get(
            path, {"symbol": to_binance(symbol), "period": period,
                   "limit": min(limit, 500)}), "longShortRatio")

    # ── recorder ─────────────────────────────────────────────────────────
    def record_all(self, symbols: list[str], delay: float = 0.3) -> dict:
        """One pass over every symbol and series. Rows already stored are
        replaced, so this is safe to run at any cadence."""
        counts: dict[str, int] = {}
        for sym in symbols:
            for series, fn in (("funding", self.funding),
                               ("oi", self.open_interest),
                               ("taker_ratio", self.taker_ratio),
                               ("ls_ratio", self.ls_ratio)):
                try:
                    df = fn(sym)
                except Exception as e:
                    log.warning(f"derivs record {sym}/{series}: {e}")
                    continue
                if df is not None and len(df):
                    self.save(sym, series, df)
                    counts[series] = counts.get(series, 0) + len(df)
                time.sleep(delay)              # public endpoints are rate-limited
        return counts
```

Add to `config.yaml` after the `universe:` block:

```yaml
derivatives:
  enabled: true
  # OI / taker / long-short endpoints retain only ~30 days, so the recorder
  # runs from day one and history accumulates past the API window.
  record_interval_minutes: 15
  symbols: [BTC/USDT, ETH/USDT, SOL/USDT, XRP/USDT, SUI/USDT]
  request_delay_s: 0.3
```

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_derivatives.py -v`
Expected: 8 passed

- [ ] **Step 5: Smoke-test against the live API once (network)**

Run:
```bash
./venv/bin/python -c "
from trader.data.derivatives import DerivFeed
d = DerivFeed()
print('funding', len(d.funding('BTC/USDT')))
print('oi', len(d.open_interest('BTC/USDT')))
print('taker', len(d.taker_ratio('BTC/USDT')))
print('ls', len(d.ls_ratio('BTC/USDT')))
"
```
Expected: four non-zero counts. A zero means the endpoint shape changed —
check the response before proceeding.

- [ ] **Step 6: Commit**

```bash
git add trader/data/derivatives.py tests/test_derivatives.py config.yaml
git commit -m "feat(data): derivatives + flow feed (funding, OI, taker, long/short) with persistent store"
```

---

### Task 14: Derivative features and point-in-time alignment

**Files:**
- Create: `trader/strategy/features_deriv.py`
- Modify: `trader/strategy/features.py` (import the module so registration happens)
- Test: `tests/test_pit_alignment.py`

**Interfaces:**
- Consumes: `features.register`, `FeatureCtx.derivs`
- Produces: `align(series_df, base_ts) -> pd.Series`; features `funding`, `funding_z`, `funding_cum`, `oi`, `oi_ret`, `oi_z`, `taker_ratio`, `taker_ratio_z`, `ls_ratio`, `basis`, `basis_z`, `taker_buy_frac`

`ctx.derivs` holds **raw** `{name: DataFrame[ts, value]}`; alignment happens
inside the feature so the point-in-time rule lives in exactly one place.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pit_alignment.py
import numpy as np
import pandas as pd
import pytest

from trader.strategy.features import FEATURES, FeatureCtx
from trader.strategy.features_deriv import align


@pytest.fixture
def base():
    n = 200
    close = 100 + np.arange(n) * 0.1
    return pd.DataFrame({
        "ts": pd.date_range("2026-08-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close * 1.001, "low": close * 0.999,
        "close": close, "volume": np.full(n, 100.0)})


@pytest.fixture
def funding_df():
    """8-hourly funding — far coarser than the 15m base frame."""
    return pd.DataFrame({
        "ts": pd.date_range("2026-08-01", periods=8, freq="8h", tz="UTC"),
        "value": [0.0001 * i for i in range(8)]})


def test_align_forward_fills_onto_base_index(base, funding_df):
    out = align(funding_df, base["ts"])
    assert len(out) == len(base)
    assert out.notna().sum() > 0


def test_align_has_no_lookahead(base, funding_df):
    """A base bar may only see observations that had ALREADY settled.
    The observation stamped at exactly t is knowable only after t."""
    out = align(funding_df, base["ts"])
    first_obs_ts = funding_df["ts"].iloc[0]
    before = base["ts"] <= first_obs_ts
    assert out[before.values].isna().all()


def test_align_never_fabricates_values(base):
    empty = pd.DataFrame({"ts": pd.to_datetime([], utc=True), "value": []})
    out = align(empty, base["ts"])
    assert out.isna().all(), "missing data must be NaN, never 0.0"


def test_funding_feature_reads_ctx_derivs(base, funding_df):
    ctx = FeatureCtx(frames={"15m": base}, tf="15m",
                     derivs={"funding": funding_df})
    out = FEATURES["funding"].fn(ctx)
    assert len(out) == len(base)


def test_funding_feature_without_data_is_nan(base):
    ctx = FeatureCtx(frames={"15m": base}, tf="15m", derivs=None)
    assert FEATURES["funding"].fn(ctx).isna().all()


def test_deriv_features_declare_their_requirement():
    assert FEATURES["funding"].requires == ("funding",)
    assert FEATURES["oi"].requires == ("open_interest",)


def test_shifting_the_series_changes_the_signal(base, funding_df):
    """The lookahead guard that matters: if shifting the input does not
    change the output, alignment is not actually time-aware."""
    a = align(funding_df, base["ts"])
    shifted = funding_df.copy()
    shifted["ts"] = shifted["ts"] + pd.Timedelta("4h")
    b = align(shifted, base["ts"])
    assert not a.equals(b)


def test_taker_buy_frac_uses_the_candle_store_column(base):
    base = base.copy()
    base["taker_buy"] = base["volume"] * 0.7
    ctx = FeatureCtx(frames={"15m": base}, tf="15m")
    out = FEATURES["taker_buy_frac"].fn(ctx)
    assert out.iloc[-1] == pytest.approx(0.7)
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_pit_alignment.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'trader.strategy.features_deriv'`

- [ ] **Step 3: Implement `trader/strategy/features_deriv.py`**

```python
"""Derivative and flow features.

Point-in-time alignment lives in `align()` and nowhere else. Every series
here is observed at a coarser and irregular cadence than the base frame —
funding every 8 hours, open interest every 15 minutes but with gaps — so the
mapping onto bars is where lookahead would creep in.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..agents import indicators as ind
from .features import SERIES_ARG, FeatureCtx, register


def align(obs: pd.DataFrame | None, base_ts: pd.Series) -> pd.Series:
    """Map an irregular observation series onto the base bar index.

    A bar at time t may only see observations STRICTLY BEFORE t: an
    observation stamped exactly t is knowable at t, not during the bar that
    ends at t. `side='left'` gives that; `side='right'` would leak.

    Missing data is NaN, never a neutral default. A fabricated 0.0 funding
    rate reads as 'funding is flat' and would fire mean-reversion signals on
    data that does not exist.
    """
    idx = pd.RangeIndex(len(base_ts))
    if obs is None or obs.empty:
        return pd.Series(np.nan, index=idx)
    o = obs.dropna(subset=["ts"]).sort_values("ts")
    o_ts = pd.to_datetime(o["ts"], utc=True).values
    b_ts = pd.to_datetime(base_ts, utc=True).values
    pos = np.searchsorted(o_ts, b_ts, side="left") - 1
    vals = o["value"].to_numpy(float)
    out = np.where(pos >= 0, vals[np.clip(pos, 0, None)], np.nan)
    return pd.Series(out, index=idx)


def _deriv(ctx: FeatureCtx, name: str) -> pd.Series:
    src = (ctx.derivs or {}).get(name)
    return align(src, ctx.df["ts"])


# ── funding (years of history — backtestable today) ──────────────────────
register("funding", domain=(-0.01, 0.01), requires=("funding",))(
    lambda ctx: _deriv(ctx, "funding"))


@register("funding_z", arg_specs=((int, 12, 500),), domain=(-4.0, 4.0),
          requires=("funding",))
def _funding_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "funding"), int(n))


@register("funding_cum", arg_specs=((int, 3, 200),), domain=(-0.5, 0.5),
          requires=("funding",))
def _funding_cum(ctx, n):
    """Cumulative funding paid over n bars — the running cost of holding the
    crowded side, which is what actually forces the unwind."""
    return _deriv(ctx, "funding").rolling(int(n)).sum()


# ── open interest (~30d API window; recorded forward) ────────────────────
register("oi", requires=("open_interest",))(lambda ctx: _deriv(ctx, "oi"))


@register("oi_ret", arg_specs=((int, 1, 200),), domain=(-1.0, 1.0),
          requires=("open_interest",))
def _oi_ret(ctx, n):
    s = _deriv(ctx, "oi")
    return s / s.shift(int(n)) - 1.0


@register("oi_z", arg_specs=((int, 12, 500),), domain=(-4.0, 4.0),
          requires=("open_interest",))
def _oi_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "oi"), int(n))


# ── aggressor flow ───────────────────────────────────────────────────────
register("taker_ratio", domain=(0.0, 5.0), requires=("taker_ratio",))(
    lambda ctx: _deriv(ctx, "taker_ratio"))


@register("taker_ratio_z", arg_specs=((int, 12, 500),), domain=(-4.0, 4.0),
          requires=("taker_ratio",))
def _taker_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "taker_ratio"), int(n))


@register("taker_buy_frac", domain=(0.0, 1.0))
def _taker_buy_frac(ctx):
    """Aggressor-buy share estimated from candle position. Unlike
    `taker_ratio` this comes from the candle store's own `taker_buy` column
    (feed.py:99-104), so it has the FULL ~209 days of history rather than the
    exchange endpoint's 30-day window. It is a proxy, not the real print."""
    v = ctx.df["volume"]
    tb = ctx.df.get("taker_buy")
    if tb is None:
        return pd.Series(np.nan, index=ctx.index)
    return tb / v.where(v.abs() > 1e-12)


register("ls_ratio", domain=(0.0, 5.0), requires=("ls_ratio",))(
    lambda ctx: _deriv(ctx, "ls_ratio"))


# ── basis (years of history — backtestable today) ────────────────────────
register("basis", domain=(-0.1, 0.1), requires=("basis",))(
    lambda ctx: _deriv(ctx, "basis"))


@register("basis_z", arg_specs=((int, 12, 500),), domain=(-4.0, 4.0),
          requires=("basis",))
def _basis_z(ctx, n):
    return ind.zscore_series(_deriv(ctx, "basis"), int(n))
```

Then at the bottom of `trader/strategy/features.py`, register the derivative
module so importing `features` is enough to populate the whole registry:

```python
from . import features_deriv          # noqa: E402,F401  (registration side-effect)
```

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_pit_alignment.py tests/test_features.py tests/test_dsl.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/features_deriv.py trader/strategy/features.py tests/test_pit_alignment.py
git commit -m "feat(strategy): derivative + flow features with single-point PIT alignment"
```

---

### Task 15: Kernel recorder thread

**Files:**
- Modify: `trader/kernel.py`
- Test: `tests/test_derivatives.py` (append)

**Interfaces:**
- Consumes: `DerivFeed.record_all`
- Produces: `Kernel._derivatives_recorder()` daemon thread, started in `boot()`

This is the only task that touches the running kernel, and it adds a
read-only background thread. It does not affect the trade loop, the
orchestrator, or the strategy population.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_derivatives.py
def test_record_all_is_safe_when_every_endpoint_fails(feed, monkeypatch):
    """The recorder runs in the live kernel. A dead endpoint must never
    raise into the daemon thread."""
    monkeypatch.setattr(feed, "_get", lambda *a, **k: [])
    assert feed.record_all(["BTC/USDT"], delay=0.0) == {}


def test_record_all_writes_each_series(feed, monkeypatch):
    import pandas as pd
    df = pd.DataFrame({
        "ts": pd.date_range("2026-08-01", periods=3, freq="15min", tz="UTC"),
        "value": [1.0, 2.0, 3.0]})
    for name in ("funding", "open_interest", "taker_ratio", "ls_ratio"):
        monkeypatch.setattr(feed, name, lambda *a, **k: df)
    counts = feed.record_all(["BTC/USDT"], delay=0.0)
    assert set(counts) == {"funding", "oi", "taker_ratio", "ls_ratio"}
    assert feed.load("BTC/USDT", "funding") is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `./venv/bin/python -m pytest tests/test_derivatives.py -k record_all -v`
Expected: FAIL on the first (`record_all` returns non-empty or raises) —
confirm the failure reason before implementing.

- [ ] **Step 3: Add the thread to `trader/kernel.py`**

Add the method to the `Kernel` class:

```python
    def _derivatives_recorder(self) -> None:
        """Record funding / OI / taker / long-short every N minutes.

        Runs from day one even though nothing reads it yet: the OI, taker and
        long-short endpoints retain only ~30 days, so history not recorded now
        is permanently unavailable to any future backtest.
        """
        from .data.derivatives import DerivFeed
        dcfg = self.cfg.get("derivatives", {}) or {}
        if not dcfg.get("enabled", True):
            return
        feed = DerivFeed()
        symbols = dcfg.get("symbols") or self.cfg["universe"]["majors"]
        interval = float(dcfg.get("record_interval_minutes", 15)) * 60
        delay = float(dcfg.get("request_delay_s", 0.3))
        while not self._stopping:
            try:
                counts = feed.record_all(symbols, delay=delay)
                if counts:
                    log.info(f"derivatives recorded: {counts}")
            except Exception as e:
                log.warning(f"derivatives recorder: {e}")
            for _ in range(int(interval)):
                if self._stopping:
                    return
                time.sleep(1)
```

Then start it in `boot()`, beside the existing telegram listener thread:

```python
        threading.Thread(target=self._derivatives_recorder, daemon=True,
                         name="derivs-recorder").start()
```

Check the attribute name the kernel already uses for its shutdown flag
(`grep -n "_stopping\|_shutdown\|_running" trader/kernel.py`) and use that
one rather than introducing a second.

- [ ] **Step 4: Run to verify pass**

Run: `./venv/bin/python -m pytest tests/test_derivatives.py -v`
Expected: all pass

- [ ] **Step 5: Verify the kernel still boots**

Run: `./venv/bin/python -m trader.kernel --status`
Expected: normal status output, no traceback.

- [ ] **Step 6: Full suite**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: no new failures versus baseline.

- [ ] **Step 7: Commit and restart the kernel so recording begins**

```bash
git add trader/kernel.py tests/test_derivatives.py
git commit -m "feat(kernel): derivatives recorder thread — start accumulating 30-day-window history now"
./restart.sh kernel
sleep 60 && grep -c "derivatives recorded" logs/luffy.log
```

---

## Done criteria

- [ ] `./venv/bin/python -m pytest tests/ -q` — no new failures versus the pre-plan baseline
- [ ] `./venv/bin/python scripts/backtest_equivalence.py` — `ENGINE EQUIVALENCE: PASS`
- [ ] `./venv/bin/python scripts/bench_vector_backtest.py` — `BENCH: PASS`, speedup recorded
- [ ] Per-family signal agreement recorded in the Task 11 commit message, with an explanation for anything below 0.98
- [ ] `logs/luffy.log` shows `derivatives recorded:` lines
- [ ] `./venv/bin/python -c "import sqlite3;print(sqlite3.connect('data/derivs.db').execute('select series,count(*) from derivs group by 1').fetchall())"` shows all four series
- [ ] The live kernel is still trading the old `Genome` path — `orchestrator.py` and `library.py` dispatch are unmodified

## What this plan deliberately does not do

**`CompiledStrategy.to_pine()`** — the spec lists PineScript as a fourth
compile target. It is deferred to Plan 2 because nothing in this plan needs
it: the TradingView proxy is already advisory-only after the gauntlet repair,
and the internal walk-forward is the binding gate. When it lands it must set
`tv_testable: false` for specs using features with no Pine analogue (funding,
OI, liquidations) rather than silently substituting a stock strategy.

**The `spec_json` column on the `strategies` table** — no code loads specs
from the journal until the cutover, so the migration belongs with the code
that needs it.

Nothing routes through the spec path yet. The kernel keeps loading `Genome`
rows and calling `library.evaluate()`. Plan 2 covers the Researcher prompt
change, `Strategist.write_spec()`, `brain/analyst.py` with portfolio
keep-or-replace, the Librarian's spec cards, the authored strategy library,
and the cutover — all against the interfaces this plan makes real.
