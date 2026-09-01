# Feature layer: vocabulary + cross-sectional context — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen what a Luffy strategy can express, from 48 single-symbol features to a vocabulary that includes bar shape, sequence, classic indicators, derivative structure, and — the important one — where this coin sits against the rest of the universe.

**Architecture:** Group A is drop-in: pure functions on the existing frame, registered in `trader/strategy/features.py`. Group B is structural: `FeatureCtx` currently sees one symbol plus BTC, so no expression can ask a cross-sectional question. It gains a `universe` map, threaded through every `entries()` call site, and `xs_rank(expr)` is special-cased in the DSL exactly as `htf(tf, expr)` already is.

**Tech Stack:** Python 3.12, pandas, numpy, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-01-researcher-and-strategy-first-core-design.md` — the "Feature vocabulary" section.

## Why this matters (do not skip)

The Researcher's edge map can only find edge in what the vocabulary can express. Measured on this repo's own cached data on 2026-09-01, the strongest single predictor of the next 8 hours was `rel_strength_btc(24)`, at IC **−0.15 to −0.17 in every regime** — and it is the *only* cross-asset feature that exists. Its natural generalisation, "how does this coin rank against all the others", is currently inexpressible. Group B is that generalisation.

## Global Constraints

- Python 3.12; run everything through `./venv/bin/python`. Baseline: **554 passing**.
- No new third-party dependencies.
- **Missing data is NaN, never a fabricated default.** `features_deriv.align()` documents why: a fabricated `0.0` funding rate reads as "funding is flat" and fires signals on data that does not exist. Every feature here obeys that rule.
- **Point-in-time discipline.** A bar may only see information that had closed by its own close. `dsl._eval_htf` is the reference: `np.searchsorted(..., side="right") - 1`. Any alignment across frames or symbols uses the same rule.
- Every feature carries `arg_specs` and, where bounded, `domain` — these drive automatic parameter-range inference for the optimizer, they are not documentation.
- `market` (CoinGecko market structure) is added as a field in this plan but stays `None`. It is deliberately NOT called `globals`, which would shadow the builtin wherever it appears as a keyword argument. It is populated in the Harvester plan. Do not build features against it here.
- Every task ends with a commit.

## File Structure

| file | responsibility | change |
|---|---|---|
| `trader/strategy/features.py` | the registry and single-symbol features | +group A, +`universe`/`market` on `FeatureCtx` |
| `trader/strategy/features_xs.py` | NEW — cross-sectional features | create |
| `trader/strategy/dsl.py` | expression evaluation | special-case `xs_rank` |
| `trader/strategy/compile.py` | builds the ctx for backtest and live | thread `universe`/`market` |
| `trader/strategy/rolling.py` | window scoring | supply `universe` |
| `trader/strategy/vector_backtest.py` | walk-forward | thread `universe` |
| `trader/brain/analyst.py` | redundancy check | supply `universe` |
| `trader/core/types.py` | `Snapshot` | carry the live universe |
| `trader/kernel.py` | the trade loop | build the live universe map |

---

### Task 1: Bar shape and participation

**Files:**
- Modify: `trader/strategy/features.py`
- Test: `tests/test_features_shape.py` (create)

**Interfaces:**
- Produces: `efficiency_ratio(n)`, `volume_z(n)`, `rel_volume(n)`, `body_frac`, `upper_wick`, `lower_wick`, `dd_from_high(n)`, `runup_from_low(n)` in `FEATURES`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_features_shape.py
"""Bar shape and participation — what a candle says beyond its close."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy.features import FEATURES, FeatureCtx


def _ctx(**cols):
    n = len(next(iter(cols.values())))
    base = {"ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
            "open": np.ones(n), "high": np.ones(n), "low": np.ones(n),
            "close": np.ones(n), "volume": np.ones(n)}
    base.update(cols)
    return FeatureCtx(frames={"15m": pd.DataFrame(base)}, tf="15m")


def _f(name, ctx, *args):
    return FEATURES[name].fn(ctx, *args)


def test_efficiency_ratio_is_one_for_a_straight_line():
    ctx = _ctx(close=np.arange(1.0, 21.0))
    assert _f("efficiency_ratio", ctx, 10).iloc[-1] == pytest.approx(1.0)


def test_efficiency_ratio_is_near_zero_for_a_round_trip():
    up = np.arange(1.0, 11.0)
    close = np.concatenate([up, up[::-1][1:]])
    ctx = _ctx(close=close)
    assert _f("efficiency_ratio", ctx, 18).iloc[-1] < 0.1


def test_body_frac_is_one_for_a_marubozu_and_zero_for_a_doji():
    ctx = _ctx(open=np.array([1.0, 2.0]), close=np.array([2.0, 2.0]),
               high=np.array([2.0, 3.0]), low=np.array([1.0, 1.0]))
    out = _f("body_frac", ctx)
    assert out.iloc[0] == pytest.approx(1.0)
    assert out.iloc[1] == pytest.approx(0.0)


def test_wicks_split_the_range():
    ctx = _ctx(open=np.array([2.0]), close=np.array([3.0]),
               high=np.array([4.0]), low=np.array([1.0]))
    assert _f("upper_wick", ctx).iloc[0] == pytest.approx(1 / 3)
    assert _f("lower_wick", ctx).iloc[0] == pytest.approx(1 / 3)


def test_rel_volume_reports_a_multiple_of_the_average():
    """Nine bars at 1.0 then one at 3.0: mean over the last ten is 1.2,
    so the multiple is exactly 3 / 1.2."""
    v = np.concatenate([np.ones(10), np.array([3.0])])
    ctx = _ctx(volume=v)
    assert _f("rel_volume", ctx, 10).iloc[-1] == pytest.approx(2.5)


def test_dd_from_high_is_zero_at_a_new_high_and_negative_below():
    h = np.array([1.0, 2.0, 3.0, 3.0])
    ctx = _ctx(high=h, close=np.array([1.0, 2.0, 3.0, 1.5]))
    out = _f("dd_from_high", ctx, 3)
    assert out.iloc[2] == pytest.approx(0.0)
    assert out.iloc[3] < -0.4


def test_a_flat_range_gives_nan_not_a_fabricated_zero():
    """Missing information is NaN. A fabricated 0.0 reads as a real reading."""
    ctx = _ctx(open=np.array([1.0]), close=np.array([1.0]),
               high=np.array([1.0]), low=np.array([1.0]))
    assert np.isnan(_f("body_frac", ctx).iloc[0])
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_features_shape.py -q`
Expected: FAIL — `KeyError: 'efficiency_ratio'`.

- [ ] **Step 3: Implement**

Append to `trader/strategy/features.py`, before the `features_deriv` import at the bottom:

```python
# ── bar shape and participation ──────────────────────────────────────────
@register("efficiency_ratio", arg_specs=((int, 5, 200),), domain=(0.0, 1.0))
def _efficiency_ratio(ctx, n):
    """Kaufman: distance travelled over path walked. 1.0 = a straight line,
    near 0 = chop. The cleanest trend-versus-noise discriminator available
    from price alone."""
    n = int(n)
    c = ctx.df["close"]
    direction = (c - c.shift(n)).abs()
    path = c.diff().abs().rolling(n).sum()
    return direction / path.where(path > 1e-12)


@register("volume_z", arg_specs=((int, 12, 500),), domain=(-5.0, 5.0))
def _volume_z(ctx, n):
    return ind.zscore_series(ctx.df["volume"], int(n))


@register("rel_volume", arg_specs=((int, 5, 200),), domain=(0.0, 10.0))
def _rel_volume(ctx, n):
    v = ctx.df["volume"]
    m = v.rolling(int(n)).mean()
    return v / m.where(m > 1e-12)


def _range(ctx):
    r = ctx.df["high"] - ctx.df["low"]
    return r.where(r > 1e-12)          # a flat bar carries no shape: NaN


@register("body_frac", domain=(0.0, 1.0))
def _body_frac(ctx):
    return (ctx.df["close"] - ctx.df["open"]).abs() / _range(ctx)


@register("upper_wick", domain=(0.0, 1.0))
def _upper_wick(ctx):
    top = ctx.df[["open", "close"]].max(axis=1)
    return (ctx.df["high"] - top) / _range(ctx)


@register("lower_wick", domain=(0.0, 1.0))
def _lower_wick(ctx):
    bot = ctx.df[["open", "close"]].min(axis=1)
    return (bot - ctx.df["low"]) / _range(ctx)


@register("dd_from_high", arg_specs=((int, 5, 500),), domain=(-1.0, 0.0))
def _dd_from_high(ctx, n):
    hi = ctx.df["high"].rolling(int(n)).max()
    return ctx.df["close"] / hi.where(hi > 1e-12) - 1.0


@register("runup_from_low", arg_specs=((int, 5, 500),), domain=(0.0, 5.0))
def _runup_from_low(ctx, n):
    lo = ctx.df["low"].rolling(int(n)).min()
    return ctx.df["close"] / lo.where(lo > 1e-12) - 1.0
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_features_shape.py tests/test_features.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/features.py tests/test_features_shape.py
git commit -m "feat(features): bar shape and participation"
```

---

### Task 2: Sequence — setups are events, not levels

**Files:**
- Modify: `trader/strategy/features.py`
- Test: `tests/test_features_sequence.py` (create)

**Interfaces:**
- Consumes: `_series` and `SERIES_ARG` from Task 1's file.
- Produces: `bars_since(expr)`, `streak(expr)`, `swing_high(n)`, `swing_low(n)`.

**Why:** "three bars since the sweep" is currently inexpressible. Every existing feature answers "what is true now", never "how long since". Nested boolean expressions arrive here as a pandas boolean Series (comparisons in `dsl._eval` return Series), so `_series()` passes them through unchanged.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_features_sequence.py
"""Sequence features — a setup is an event followed by a condition."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy import dsl
from trader.strategy.features import FEATURES, FeatureCtx


def _ctx(close):
    n = len(close)
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close + 1.0, "low": close - 1.0,
        "close": close, "volume": np.ones(n)})
    return FeatureCtx(frames={"15m": df}, tf="15m")


def test_bars_since_counts_from_the_last_true_bar():
    ctx = _ctx(np.array([1.0, 5.0, 1.0, 1.0, 1.0]))
    out = dsl.evaluate(dsl.parse("bars_since(close > 3)"), ctx)
    assert list(out.iloc[1:]) == [0.0, 1.0, 2.0, 3.0]


def test_bars_since_is_nan_before_the_condition_has_ever_been_true():
    """Never-true is unknown, not zero."""
    ctx = _ctx(np.array([1.0, 1.0, 1.0]))
    out = dsl.evaluate(dsl.parse("bars_since(close > 99)"), ctx)
    assert out.isna().all()


def test_streak_counts_consecutive_true_bars_and_resets():
    ctx = _ctx(np.array([5.0, 5.0, 1.0, 5.0]))
    out = dsl.evaluate(dsl.parse("streak(close > 3)"), ctx)
    assert list(out) == [1.0, 2.0, 0.0, 1.0]


def test_swing_high_only_reports_a_pivot_after_it_is_confirmed():
    """A pivot at bar i needs n later bars to confirm, so it cannot be
    visible at i — that would be lookahead."""
    close = np.array([1.0, 2.0, 9.0, 2.0, 1.0, 1.0, 1.0])
    ctx = _ctx(close)
    out = FEATURES["swing_high"].fn(ctx, 2)
    assert np.isnan(out.iloc[2]), "pivot visible on its own bar = lookahead"
    assert out.iloc[4] == 10.0        # high = close + 1
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_features_sequence.py -q`
Expected: FAIL — `SpecError` on the unknown feature `bars_since`.

- [ ] **Step 3: Implement**

Append to `trader/strategy/features.py`:

```python
# ── sequence ─────────────────────────────────────────────────────────────
@register("bars_since", arg_specs=(SERIES_ARG,), domain=(0.0, 500.0))
def _bars_since(ctx, s):
    """Bars elapsed since the expression was last true. NaN until it has
    been true at least once — never-happened is unknown, not zero."""
    b = _series(ctx, s).astype(bool)
    idx = pd.Series(np.arange(len(b), dtype=float), index=b.index)
    return idx - idx.where(b).ffill()


@register("streak", arg_specs=(SERIES_ARG,), domain=(0.0, 500.0))
def _streak(ctx, s):
    """Length of the current run of consecutive true bars; 0 when false."""
    b = _series(ctx, s).astype(bool)
    return b.groupby((~b).cumsum()).cumsum().astype(float)


@register("swing_high", arg_specs=((int, 2, 50),))
def _swing_high(ctx, n):
    """Most recent CONFIRMED swing high. A pivot at bar i is only knowable
    n bars later, so the level is shifted forward by n before it is used."""
    n = int(n)
    h = ctx.df["high"]
    piv = h == h.rolling(2 * n + 1, center=True).max()
    return h.where(piv).shift(n).ffill()


@register("swing_low", arg_specs=((int, 2, 50),))
def _swing_low(ctx, n):
    n = int(n)
    lo = ctx.df["low"]
    piv = lo == lo.rolling(2 * n + 1, center=True).min()
    return lo.where(piv).shift(n).ffill()
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_features_sequence.py tests/test_pit_alignment.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/features.py tests/test_features_sequence.py
git commit -m "feat(features): sequence — bars_since, streak, confirmed swings"
```

---

### Task 3: Classic indicators

**Files:**
- Modify: `trader/strategy/features.py`
- Test: `tests/test_features_classic.py` (create)

**Interfaces:**
- Produces: `macd(fast, slow, signal)`, `keltner_upper(n, k)`, `keltner_lower(n, k)`, `atr_pct_rank(n)`, `vol_of_vol(n)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_features_classic.py
"""Standard vocabulary that was simply absent."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy.features import FEATURES, FeatureCtx


def _ctx(close):
    n = len(close)
    df = pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": close, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": np.ones(n)})
    return FeatureCtx(frames={"15m": df}, tf="15m")


def test_macd_histogram_is_positive_in_an_uptrend():
    ctx = _ctx(np.arange(1.0, 121.0))
    assert FEATURES["macd"].fn(ctx, 12, 26, 9).iloc[-1] > 0


def test_macd_histogram_is_negative_in_a_downtrend():
    ctx = _ctx(np.arange(120.0, 0.0, -1.0))
    assert FEATURES["macd"].fn(ctx, 12, 26, 9).iloc[-1] < 0


def test_keltner_brackets_the_price():
    ctx = _ctx(np.concatenate([np.ones(60) * 100, np.array([101.0])]))
    up = FEATURES["keltner_upper"].fn(ctx, 20, 2.0).iloc[-1]
    dn = FEATURES["keltner_lower"].fn(ctx, 20, 2.0).iloc[-1]
    assert dn < 100.0 < up


def test_atr_pct_rank_is_high_when_volatility_expands():
    calm = np.ones(80) * 100
    wild = 100 + np.arange(1, 21) * 3.0
    ctx = _ctx(np.concatenate([calm, wild]))
    assert FEATURES["atr_pct_rank"].fn(ctx, 60).iloc[-1] > 0.8
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_features_classic.py -q`
Expected: FAIL — `KeyError: 'macd'`.

- [ ] **Step 3: Implement**

Append to `trader/strategy/features.py`:

```python
# ── classic indicator vocabulary ─────────────────────────────────────────
@register("macd", arg_specs=((int, 3, 50), (int, 10, 200), (int, 2, 50)))
def _macd(ctx, fast, slow, signal):
    """The histogram — line minus signal. One number, which is what a DSL
    comparison needs."""
    c = ctx.df["close"]
    line = ind.ema(c, int(fast)) - ind.ema(c, int(slow))
    return line - ind.ema(line, int(signal))


@register("keltner_upper", arg_specs=((int, 5, 200), (float, 0.5, 4.0)))
def _keltner_upper(ctx, n, k):
    return ind.ema(ctx.df["close"], int(n)) \
        + float(k) * ind.atr_series(ctx.df, int(n))


@register("keltner_lower", arg_specs=((int, 5, 200), (float, 0.5, 4.0)))
def _keltner_lower(ctx, n, k):
    return ind.ema(ctx.df["close"], int(n)) \
        - float(k) * ind.atr_series(ctx.df, int(n))


@register("atr_pct_rank", arg_specs=((int, 20, 500),), domain=(0.0, 1.0))
def _atr_pct_rank(ctx, n):
    """Where current volatility sits in its own recent distribution —
    a regime reading as a number rather than a label."""
    return ind.atr_series(ctx.df, 14).rolling(int(n)).rank(pct=True)


@register("vol_of_vol", arg_specs=((int, 20, 500),), domain=(0.0, 2.0))
def _vol_of_vol(ctx, n):
    n = int(n)
    return ind.realized_vol_series(ctx.df, max(2, n // 4)).rolling(n).std()
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_features_classic.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/features.py tests/test_features_classic.py
git commit -m "feat(features): macd, keltner, volatility rank"
```

---

### Task 4: Derivative structure

**Files:**
- Modify: `trader/strategy/features_deriv.py`
- Test: `tests/test_features_deriv_extra.py` (create)

**Interfaces:**
- Produces: `funding_pct(n)` requiring `funding`, `basis_slope(n)` requiring `basis`, `oi_price_div(n)` requiring `open_interest`.

**Note:** only z-scores of these series are exposed today. Read the existing `features_deriv.py` for the `_deriv(ctx, name)` accessor and the `requires=` convention before writing — a feature that reads a series must declare it, or `spec_evidence.missing_data()` cannot refuse a spec whose data is absent.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_features_deriv_extra.py
"""Derivative structure beyond a z-score."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy.features import FEATURES, FeatureCtx


def _ctx(close, series_name=None, values=None):
    n = len(close)
    ts = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    df = pd.DataFrame({"ts": ts, "open": close, "high": close + 0.5,
                       "low": close - 0.5, "close": close,
                       "volume": np.ones(n)})
    derivs = None
    if series_name is not None:
        derivs = {series_name: pd.DataFrame({"ts": ts, "value": values})}
    return FeatureCtx(frames={"1h": df}, tf="1h", derivs=derivs)


def test_funding_pct_is_high_when_funding_is_at_its_extreme():
    """The spike sits in the last few observations, NOT only on the final
    timestamp: align() deliberately shows a bar only the observations that
    closed strictly before it, so a value stamped on the last bar's own ts
    is never visible to any bar."""
    n = 80
    vals = np.concatenate([np.zeros(n - 5), np.full(5, 0.01)])
    ctx = _ctx(np.ones(n) * 100, "funding", vals)
    assert FEATURES["funding_pct"].fn(ctx, 60).iloc[-1] > 0.9


def test_missing_series_is_nan_not_zero():
    ctx = _ctx(np.ones(50) * 100)          # no derivs at all
    assert FEATURES["funding_pct"].fn(ctx, 20).isna().all()


def test_oi_price_div_is_positive_when_oi_rises_as_price_falls():
    n = 60
    price = np.linspace(100.0, 90.0, n)
    oi = np.linspace(1000.0, 2000.0, n)
    ctx = _ctx(price, "oi", oi)
    assert FEATURES["oi_price_div"].fn(ctx, 24).iloc[-1] > 0
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_features_deriv_extra.py -q`
Expected: FAIL — `KeyError: 'funding_pct'`.

- [ ] **Step 3: Implement**

Append to `trader/strategy/features_deriv.py`, matching the file's existing `_deriv` accessor and `requires=` idiom:

```python
@register("funding_pct", arg_specs=((int, 24, 2000),), domain=(0.0, 1.0),
          requires=("ohlcv", "funding"))
def _funding_pct(ctx, n):
    """Where funding sits in its own recent distribution. A percentile
    survives regime shifts in the level that a z-score does not."""
    return _deriv(ctx, "funding").rolling(int(n)).rank(pct=True)


@register("basis_slope", arg_specs=((int, 6, 500),), domain=(-1.0, 1.0),
          requires=("ohlcv", "basis"))
def _basis_slope(ctx, n):
    b = _deriv(ctx, "basis")
    return b - b.shift(int(n))


@register("oi_price_div", arg_specs=((int, 6, 500),), domain=(-2.0, 2.0),
          requires=("ohlcv", "open_interest"))
def _oi_price_div(ctx, n):
    """Open interest building against the price move — positioning growing
    into a decline is a different animal from one growing into a rally."""
    n = int(n)
    oi = _deriv(ctx, "oi")
    oi_ret = oi / oi.shift(n) - 1.0
    px_ret = ctx.df["close"] / ctx.df["close"].shift(n) - 1.0
    return oi_ret - px_ret
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_features_deriv_extra.py tests/test_pit_alignment.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/features_deriv.py tests/test_features_deriv_extra.py
git commit -m "feat(features): funding percentile, basis slope, OI-price divergence"
```

---

### Task 5: Widen FeatureCtx — plumbing only, no behaviour change

**Files:**
- Modify: `trader/strategy/features.py`, `trader/strategy/compile.py`, `trader/strategy/vector_backtest.py`
- Test: `tests/test_feature_ctx_universe.py` (create)

**Interfaces:**
- Produces: `FeatureCtx(..., universe=None, market=None)`, `FeatureCtx.for_symbol(sym)`, and `universe=` / `market=` keyword arguments on `Compiled.entries()`, `Compiled.exit_signal()`, and `vector_walk_forward()`.

**This task adds no features and changes no results.** Every existing test must still pass unchanged — that is the check that the threading is transparent.

`universe` shape: `{symbol: {timeframe: DataFrame}}`. `market` shape: `{series_name: DataFrame}` with `ts`/`value` columns, and it stays `None` throughout this plan.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_feature_ctx_universe.py
"""The context can see the rest of the book, not just this symbol."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy.features import FeatureCtx


def _df(v):
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=len(v), freq="15min",
                            tz="UTC"),
        "open": v, "high": v, "low": v, "close": v,
        "volume": np.ones(len(v))})


def test_ctx_carries_a_universe_and_market():
    uni = {"ETH/USDT": {"15m": _df(np.arange(1.0, 6.0))}}
    ctx = FeatureCtx(frames={"15m": _df(np.ones(5))}, tf="15m", universe=uni)
    assert "ETH/USDT" in ctx.universe
    assert ctx.market is None


def test_for_symbol_returns_a_context_scoped_to_another_member():
    uni = {"ETH/USDT": {"15m": _df(np.arange(1.0, 6.0))}}
    ctx = FeatureCtx(frames={"15m": _df(np.ones(5))}, tf="15m", universe=uni)
    sub = ctx.for_symbol("ETH/USDT")
    assert sub is not None
    assert float(sub.df["close"].iloc[-1]) == 5.0
    assert sub.tf == ctx.tf


def test_for_symbol_returns_none_when_the_member_is_absent():
    """Absent is None, so callers produce NaN rather than a fabricated value."""
    ctx = FeatureCtx(frames={"15m": _df(np.ones(5))}, tf="15m", universe={})
    assert ctx.for_symbol("SOL/USDT") is None


def test_scoped_preserves_universe_and_market():
    uni = {"ETH/USDT": {"15m": _df(np.ones(5)), "1h": _df(np.ones(5))}}
    ctx = FeatureCtx(frames={"15m": _df(np.ones(5)), "1h": _df(np.ones(5))},
                     tf="15m", universe=uni, market={"btc_dominance": _df(np.ones(5))})
    assert ctx.scoped("1h").universe is ctx.universe
    assert ctx.scoped("1h").market is ctx.market
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_feature_ctx_universe.py -q`
Expected: FAIL — `TypeError: FeatureCtx.__init__() got an unexpected keyword argument 'universe'`.

- [ ] **Step 3: Implement**

In `trader/strategy/features.py`, add the two fields to `FeatureCtx` (after `derivs`, before `_cache`):

```python
    universe: dict | None = None      # {symbol: {tf: df}} — the rest of the book
    market: dict | None = None        # market-wide series; populated later
```

Update `scoped` to carry them, and add `for_symbol`:

```python
    def scoped(self, tf: str) -> "FeatureCtx":
        """A view on a different timeframe, sharing the cache."""
        return FeatureCtx(self.frames, tf, self.btc, self.derivs,
                          self.universe, self.market, self._cache)

    def for_symbol(self, symbol: str) -> "FeatureCtx | None":
        """A view on another member of the universe at the same timeframe.

        None when the member is absent — the caller then produces NaN rather
        than a fabricated value.
        """
        frames = (self.universe or {}).get(symbol)
        if not frames or self.tf not in frames:
            return None
        return FeatureCtx(frames, self.tf, self.btc, self.derivs,
                          self.universe, self.market, {})
```

**Note the fresh `{}` cache in `for_symbol`** — the memo key is `(name, tf, args)` with no symbol component, so sharing the parent's cache would return this symbol's values for every other symbol.

In `trader/strategy/compile.py`, thread the arguments:

```python
    def entries(self, frames: dict, btc: dict | None = None,
                derivs: dict | None = None, universe: dict | None = None,
                market: dict | None = None):
        ctx = self._ctx(frames, btc, derivs, universe, market)
```

```python
    def exit_signal(self, frames: dict, btc=None, derivs=None,
                    universe=None, market=None):
        if self._exit is None:
            return None
        return dsl.evaluate_bool(
            self._exit, self._ctx(frames, btc, derivs, universe, market))

    def _ctx(self, frames, btc, derivs, universe=None,
             market=None) -> FeatureCtx:
        tf = self.spec.timeframe
        if tf not in frames:
            raise dsl.SpecError(f"spec timeframe '{tf}' not in frames "
                                f"{sorted(frames)}")
        return FeatureCtx(frames=frames, tf=tf, btc=btc, derivs=derivs,
                          universe=universe, market=market)
```

In `trader/strategy/vector_backtest.py`, add `universe=None, market=None` to `vector_walk_forward`'s signature and pass them through to both `compiled.entries(...)` and `compiled.exit_signal(...)` calls (lines ~163-164 and ~182-183).

- [ ] **Step 4: Run the full suite**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: PASS, and the count is the previous total plus the 4 new tests. Any existing failure means the threading is not transparent.

- [ ] **Step 5: Commit**

```bash
git add trader/strategy/features.py trader/strategy/compile.py trader/strategy/vector_backtest.py tests/test_feature_ctx_universe.py
git commit -m "feat(features): FeatureCtx can see the universe and global series"
```

---

### Task 6: Supply the universe at every call site

**Files:**
- Modify: `trader/strategy/rolling.py`, `trader/strategy/spec_evidence.py`, `trader/brain/analyst.py`, `trader/core/types.py`, `trader/kernel.py`, `trader/strategy/compile.py`
- Test: `tests/test_universe_wiring.py` (create)

**Interfaces:**
- Consumes: `universe=` from Task 5.
- Produces: `Snapshot.universe` (a `{symbol: {tf: df}}` map), populated by the kernel.

**The two paths differ.** In the backtest, `spec_evidence.load_frames(cfg, tf)` already returns `{symbol: df}` for every symbol — the universe is in the caller's hand and just needs reshaping to `{symbol: {tf: df}}`. Live, the kernel fetches one symbol at a time inside its cycle loop, so it must build the map BEFORE the loop. `DataFeed` is cache-first, so a pre-pass over symbols it is about to fetch anyway is nearly free.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_universe_wiring.py
"""The universe has to actually arrive, or cross-sectional features are NaN."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.types import Snapshot
from trader.strategy.spec import ExitSpec


def _df(v):
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=len(v), freq="15min",
                            tz="UTC"),
        "open": v, "high": v, "low": v, "close": v,
        "volume": np.ones(len(v))})


def test_snapshot_carries_a_universe_and_defaults_to_empty():
    snap = Snapshot(symbol="BTC/USDT", ts="2026-01-01T00:00:00+00:00",
                    price=100.0, dfs={"15m": _df(np.ones(5))})
    assert snap.universe in (None, {})


def test_rolling_passes_the_universe_into_entries(monkeypatch):
    """recent_verdict must hand every symbol's frame to the evaluator."""
    from trader.strategy import rolling

    seen = {}

    class _Spec:
        exit = ExitSpec()

    class _Compiled:
        spec = _Spec()

        def entries(self, frames, btc=None, derivs=None, universe=None,
                    market=None):
            seen["universe"] = universe
            n = len(next(iter(frames.values())))
            return np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)

    risk = {"risk_per_trade_pct": 1.0, "taker_fee_pct": 0.05,
            "slippage_atr_frac": 0.06, "funding_rate_8h": 0.0001,
            "bar_minutes": 15, "stop_loss_atr_mult": 2.5,
            "take_profit_atr_mult": 4.5}
    frames = {"BTC/USDT": _df(np.ones(400)), "ETH/USDT": _df(np.ones(400))}
    rolling.recent_verdict(_Compiled(), frames, risk, "15m",
                           recent_days=1, min_trades=0)

    assert seen["universe"] is not None
    assert set(seen["universe"]) == {"BTC/USDT", "ETH/USDT"}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_universe_wiring.py -q`
Expected: FAIL — `Snapshot` has no `universe`, and `recent_verdict` passes no `universe`.

- [ ] **Step 3: Implement**

**a. `trader/core/types.py`** — add to `Snapshot`:

```python
    universe: dict | None = None      # {symbol: {tf: df}} for cross-sectional
```

**b. `trader/strategy/rolling.py`** — at the top of `rolling_windows`, `recent_verdict` and `regime_windows`, build the map once and pass it to every `entries` call:

```python
    universe = {s: {timeframe: f} for s, f in frames.items()
                if not s.startswith("_") and f is not None}
```

then `compiled.entries({timeframe: df}, btc=btc, derivs=derivs, universe=universe)` at each of the three sites. In `recent_verdict`, note the per-symbol frame is the sliced `recent`, but the universe should be the FULL frames — cross-sectional rank needs the other symbols over the same span, and `xs_rank` aligns by timestamp, so a longer universe frame is correct and a shorter one would produce NaN.

**c. `trader/strategy/spec_evidence.py`** — in `run_gauntlet`, build the same map from `frames` and pass `universe=` into `vector_walk_forward`.

**d. `trader/brain/analyst.py`** — in `redundancy`, pass `universe=` built from `frames` into both `cand.entries(...)` and `oc.entries(...)`.

**e. `trader/kernel.py`** — in `cycle()`, before the `for symbol in self.universe.symbols():` loop:

```python
        exec_tf = self.cfg["timeframes"]["execution"]
        universe_frames = {}
        for sym in self.universe.symbols():
            df = self.feed.fetch_ohlcv(sym, exec_tf)
            if df is not None and len(df):
                universe_frames[sym] = {exec_tf: df}
```

and pass it into `_snapshot_for` so the returned `Snapshot` carries it:

```python
    def _snapshot_for(self, symbol: str, universe: dict | None = None) -> Snapshot | None:
```
...
```python
        return Snapshot(symbol=symbol, ..., btc_ctx=self._btc_ctx,
                        universe=universe)
```

**f. `trader/strategy/compile.py`** — in `to_evaluator`'s `_evaluate`, pass the snapshot's universe through:

```python
                lo, sh = self.entries(frames, btc=btc,
                                      universe=getattr(snap, "universe", None))
```

- [ ] **Step 4: Run the full suite**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trader/ tests/test_universe_wiring.py
git commit -m "feat(strategy): supply the universe to the backtest and live paths"
```

---

### Task 7: Cross-sectional features

**Files:**
- Create: `trader/strategy/features_xs.py`
- Modify: `trader/strategy/dsl.py`, `trader/strategy/features.py` (import for registration side-effect)
- Test: `tests/test_features_xs.py` (create)

**Interfaces:**
- Consumes: `FeatureCtx.for_symbol` (Task 5), the populated `universe` (Task 6).
- Produces: `xs_rank(expr)`, `breadth(expr)`, `dispersion(expr)` in `FEATURES`, and `_eval_xs` in `dsl.py`.

**The design point that makes this possible.** A normal feature receives its argument ALREADY EVALUATED for this symbol, so it cannot re-evaluate the expression across other symbols. `htf(tf, expr)` has exactly this problem and solves it by being a **marker**: registered so `parse()` knows its name and arity, then special-cased in `dsl._eval` so the inner expression is evaluated in a different scope. `xs_rank`, `breadth` and `dispersion` follow that precedent — read `_eval_htf` in `trader/strategy/dsl.py` before writing `_eval_xs`, and reuse its point-in-time alignment (`searchsorted(..., side="right") - 1`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_features_xs.py
"""Where this coin sits against the rest of the book."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy import dsl
from trader.strategy.features import FeatureCtx


def _df(v):
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=len(v), freq="15min",
                            tz="UTC"),
        "open": v, "high": v, "low": v, "close": v,
        "volume": np.ones(len(v))})


def _ctx(mine, others):
    uni = {"ME": {"15m": _df(mine)}}
    for name, v in others.items():
        uni[name] = {"15m": _df(v)}
    return FeatureCtx(frames={"15m": _df(mine)}, tf="15m", universe=uni)


def test_xs_rank_is_one_for_the_strongest_member():
    ctx = _ctx(np.array([1.0, 4.0]), {"A": np.array([1.0, 2.0]),
                                      "B": np.array([1.0, 3.0])})
    out = dsl.evaluate(dsl.parse("xs_rank(close)"), ctx)
    assert out.iloc[-1] == 1.0


def test_xs_rank_is_zero_for_the_weakest_member():
    ctx = _ctx(np.array([1.0, 1.0]), {"A": np.array([1.0, 2.0]),
                                      "B": np.array([1.0, 3.0])})
    out = dsl.evaluate(dsl.parse("xs_rank(close)"), ctx)
    assert out.iloc[-1] == 0.0


def test_xs_rank_is_nan_without_a_universe():
    """No peers means no cross-sectional answer — not a fabricated 0.5."""
    ctx = FeatureCtx(frames={"15m": _df(np.ones(4))}, tf="15m")
    out = dsl.evaluate(dsl.parse("xs_rank(close)"), ctx)
    assert out.isna().all()


def test_breadth_is_the_fraction_of_the_book_satisfying_the_condition():
    ctx = _ctx(np.array([5.0, 5.0]), {"A": np.array([5.0, 5.0]),
                                      "B": np.array([1.0, 1.0])})
    out = dsl.evaluate(dsl.parse("breadth(close > 3)"), ctx)
    assert out.iloc[-1] == pytest.approx(2 / 3)


def test_dispersion_rises_when_members_diverge():
    tight = _ctx(np.array([1.0, 1.0]), {"A": np.array([1.0, 1.0])})
    wide = _ctx(np.array([1.0, 1.0]), {"A": np.array([1.0, 50.0])})
    t = dsl.evaluate(dsl.parse("dispersion(close)"), tight).iloc[-1]
    w = dsl.evaluate(dsl.parse("dispersion(close)"), wide).iloc[-1]
    assert w > t
```

- [ ] **Step 2: Run it and watch it fail**

Run: `./venv/bin/python -m pytest tests/test_features_xs.py -q`
Expected: FAIL — `SpecError` on the unknown feature `xs_rank`.

- [ ] **Step 3: Implement**

Create `trader/strategy/features_xs.py`:

```python
"""Cross-sectional features — this coin against the rest of the book.

Every other feature in the registry answers a question about one symbol.
Measured on this repo's cached data, the strongest single predictor of the
next eight hours was `rel_strength_btc`, at IC -0.15 to -0.17 in every
regime. That is a cross-asset effect narrowed to a single reference asset.
These generalise it to the whole universe.

Each is a MARKER registered only so `dsl.parse()` knows the name and arity;
the real evaluation happens in `dsl._eval_xs`, because the inner expression
must be evaluated once per universe member rather than once for this symbol.
`htf(tf, expr)` uses the same mechanism for the same reason.
"""
from __future__ import annotations

from .features import SERIES_ARG, register

register("xs_rank", arg_specs=(SERIES_ARG,), domain=(0.0, 1.0))(
    lambda ctx, expr: expr)
register("breadth", arg_specs=(SERIES_ARG,), domain=(0.0, 1.0))(
    lambda ctx, expr: expr)
register("dispersion", arg_specs=(SERIES_ARG,), domain=(0.0, 10.0))(
    lambda ctx, expr: expr)
```

Import it for its registration side-effect at the bottom of `trader/strategy/features.py`, beside the existing `features_deriv` import:

```python
from . import features_xs             # noqa: E402,F401  (registration side-effect)
```

In `trader/strategy/dsl.py`, add the dispatch beside the existing `htf` case in `_eval`:

```python
        if name in ("xs_rank", "breadth", "dispersion"):
            return _eval_xs(name, node, ctx)
```

and implement `_eval_xs` next to `_eval_htf`:

```python
def _eval_xs(name: str, node: ast.Call, ctx):
    """Evaluate the inner expression for every universe member, align each
    onto this symbol's bars point-in-time, then reduce across members.

    Same discipline as _eval_htf: searchsorted side='right' minus one, so a
    bar can only see peer bars that had already closed.
    """
    universe = ctx.universe or {}
    if not universe:
        return pd.Series(np.nan, index=ctx.index)

    base_ts = pd.to_datetime(ctx.df["ts"], utc=True).values
    cols = {}
    for sym in universe:
        sub = ctx.for_symbol(sym)
        if sub is None:
            continue
        vals = _eval(node.args[0], sub)
        if not isinstance(vals, pd.Series):
            continue
        peer_ts = pd.to_datetime(sub.df["ts"], utc=True).values
        pos = np.searchsorted(peer_ts, base_ts, side="right") - 1
        arr = vals.to_numpy(dtype=float)
        cols[sym] = np.where(pos >= 0, arr[np.clip(pos, 0, None)], np.nan)

    if not cols:
        return pd.Series(np.nan, index=ctx.index)

    panel = pd.DataFrame(cols, index=ctx.index)
    mine = _eval(node.args[0], ctx)
    if not isinstance(mine, pd.Series):
        mine = pd.Series(mine, index=ctx.index)

    if name == "breadth":
        return panel.astype(float).mean(axis=1)
    if name == "dispersion":
        return panel.std(axis=1)
    # xs_rank: this symbol's position within the cross-section, in [0, 1]
    below = panel.lt(mine, axis=0).sum(axis=1)
    valid = panel.notna().sum(axis=1)
    denom = (valid - 1).where(valid > 1)
    return (below / denom).clip(0.0, 1.0)
```

- [ ] **Step 4: Run the tests**

Run: `./venv/bin/python -m pytest tests/test_features_xs.py -q`
Expected: PASS, 5 tests.

- [ ] **Step 5: Run the full suite**

Run: `./venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add trader/strategy/features_xs.py trader/strategy/features.py trader/strategy/dsl.py tests/test_features_xs.py
git commit -m "feat(features): cross-sectional rank, breadth and dispersion"
```

---

## Done when

- `./venv/bin/python -c "from trader.strategy.features import FEATURES; print(len(FEATURES))"` reports roughly 68, up from 48.
- `xs_rank(close)` evaluates to a real number in a backtest and to NaN when no universe is supplied.
- The full suite is green.
- A spec using `funding_pct` is still refused by `spec_evidence.missing_data()` when funding history does not span the frame.

## Deliberately NOT in this plan

- **Group C** — recording order-book depth, liquidations and the spot/perp volume split. That is Harvester work and belongs with the CoinGecko plan.
- **Populating `market`** — the field is added here and stays `None` until market-structure series exist.
- Using any of this in the Researcher's edge map — that is the Researcher plan.
