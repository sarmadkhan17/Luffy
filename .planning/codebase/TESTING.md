# Testing Patterns

**Analysis Date:** 2026-09-03

## Test Framework

**Runner:**
- pytest
- 937 tests in `tests/` directory (collected via `pytest --collect-only`)
- No `conftest.py` — fixtures defined per-test-file

**Run Commands:**
```bash
./venv/bin/python -m pytest tests/                          # Run all tests
./venv/bin/python -m pytest tests/ -v                       # Verbose output
./venv/bin/python -m pytest tests/test_analyst.py           # Single file
./venv/bin/python -m pytest tests/ -k test_name             # Match by name
./venv/bin/python -m pytest tests/ -x                       # Stop on first failure
```

**Assertion Library:**
- Built-in pytest assertions: `assert`, `assert ... ==`, `assert ... is not None`
- `pytest.approx()` for floating-point comparison: `assert value == pytest.approx(expected)`
- `pytest.raises()` for exception testing

## Test File Organization

**Location:**
- All tests in `tests/` directory at repo root
- One test file per module or feature being tested

**Naming:**
- File: `test_<feature_name>.py` or `test_<module_name>.py`
- Function: `test_<what_is_being_tested>()` with descriptive names
- Examples: `test_identical_signals_are_fully_redundant()`, `test_entries_are_boolean_arrays_of_frame_length()`

**Structure:**
```
tests/
├── test_analyst.py          # Tests for Analyst class
├── test_compile.py          # Tests for strategy compiler
├── test_dsl.py              # Tests for DSL parser
├── test_features.py         # Tests for feature registry
├── test_protective_stops.py # Tests for stop management
└── ... (937 tests across ~100 files)
```

## Test Structure

**Test Organization Pattern:**

Most test files follow this structure:

```python
import numpy as np
import pandas as pd
import pytest

from trader.module.under_test import ClassOrFunction

# ── Fixtures (test data factories) ──────────────────────────────────

@pytest.fixture
def df():
    """Create a sample OHLCV DataFrame for testing."""
    rng = np.random.default_rng(11)
    n = 500
    close = 50000 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    return pd.DataFrame({
        "ts": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "close": close,
        "volume": rng.uniform(100, 1000, n),
    })

# ── Helper factories ────────────────────────────────────────────────

def _spec(sid="a1", name="Test Probe", **kw):
    """Factory for StrategySpec test objects."""
    base = dict(id=sid, name=name, thesis="...", ...)
    base.update(kw)
    return StrategySpec(**base)

def _frame(n=4000, drift=0.0, seed=1):
    """Factory for test OHLCV DataFrame."""
    # ... create and return DataFrame

# ── Test cases ──────────────────────────────────────────────────────

def test_feature_basic_assertion(df):
    """Assert that a feature returns expected output."""
    result = compute_feature(df)
    assert result.shape == df.shape
    assert not result.isna().any()

def test_parameter_variations():
    """Test multiple parameter combinations."""
    # ... parametrized test
```

**Patterns:**
- Fixtures defined with `@pytest.fixture` at module level
- Test data factories: helper functions prefixed with `_` that return test objects
- Imports: test dependencies first, then tested module
- Comments: section separators and descriptive docstrings

## Test Structure Details

**Arrange-Act-Assert Pattern:**

```python
def test_signal_overlap_identical_signals():
    """Arrange: two identical signals. Act: compute overlap. Assert: 1.0"""
    lo = np.array([True, False, True, False, True])
    sh = np.zeros(5, bool)
    
    overlap = signal_overlap(lo, sh, lo, sh)
    
    assert overlap == pytest.approx(1.0)
```

**Descriptive Test Names:**
- Name describes what is being tested and the expected result
- Example: `test_identical_signals_are_fully_redundant()`
- Example: `test_opposite_signals_count_as_redundant()`
- Example: `test_unrelated_signals_are_not_redundant()`

## Mocking

**Framework:** `pytest.monkeypatch` (built into pytest)

**Patterns:**
```python
def analyst(monkeypatch):
    """Fixture that patches the Analyst.frames method."""
    a = Analyst(_Journal(), CFG)
    up = _frame(4000, drift=0.004, seed=2)
    monkeypatch.setattr(a, "frames", 
                       lambda tf, extra=(): {"BTC/USDT": up, "_btc_1h": up})
    return a
```

**Usage in tests:**
```python
def test_review_retires_a_decayed_strategy(monkeypatch):
    """Mock frames to return declining performance."""
    down = _frame(4000, drift=-0.001, seed=3)
    monkeypatch.setattr(analyst, "frames",
                       lambda tf, extra=(): {"BTC/USDT": down, "_btc_1h": down})
```

**What to Mock:**
- External data sources: `analyst.frames()`, `journal.query()`
- Exchange APIs: wrapped in `exchange` parameter to avoid network calls
- Heavy computations: can be mocked if test focuses on logic, not result

**What NOT to Mock:**
- Core business logic: test the actual behavior, not stubs
- Strategy evaluation: full implementation tested to ensure correctness
- Backtest results: real numbers compared, not mocked returns

## Fixtures and Factories

**Fixture Definition Pattern:**
```python
@pytest.fixture
def analyst(monkeypatch):
    """Analyst with mocked data frames."""
    a = Analyst(_Journal(), CFG)
    up = _frame(4000, drift=0.004, seed=2)
    monkeypatch.setattr(a, "frames", 
                       lambda tf, extra=(): {"BTC/USDT": up, "_btc_1h": up})
    return a
```

**Test Data Factories:**
```python
def _spec(sid="a1", name="Analyst Probe", entry="close > ema(5)", **kw):
    """Factory for StrategySpec objects."""
    base = dict(
        id=sid, name=name, thesis="A hypothesis...",
        invalidation="Retire below 0.85...",
        provenance={"source_kind": "test"},
        universe={"include": []},
        timeframe="1h", direction="long",
        entry_long=entry, entry_short="",
        filters=[], exit=ExitSpec(),
        regime_filter=["TRENDING_UP"],
        markets=["futures"]
    )
    base.update(kw)
    return StrategySpec(**base)

def _frame(n=4000, drift=0.0, seed=1):
    """Factory for OHLCV test DataFrames."""
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(drift, 0.006, n))
    df = pd.DataFrame({
        "ts": pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"),
        "open": close, "close": close,
        "volume": rng.uniform(50, 500, n)
    })
    df["high"] = df[["open", "close"]].max(axis=1) * 1.003
    df["low"] = df[["open", "close"]].min(axis=1) * 0.997
    return df[["ts", "open", "high", "low", "close", "volume"]]
```

**Location:** Factories defined in the test file, not a shared conftest.py

**Naming:** Prefixed with `_` to indicate test-internal use

## Parametrized Tests

**Framework:** `@pytest.mark.parametrize`

**Usage:**
```python
@pytest.mark.parametrize("expr", GOOD)
def test_valid_expressions_parse(expr):
    assert parse(expr) is not None

@pytest.mark.parametrize("expr", BAD[:-1])
def test_invalid_expressions_rejected(expr):
    with pytest.raises(SpecError):
        parse(expr)

@pytest.mark.parametrize("expr,series", [
    ("funding_pct(96) > 0.9", "funding"),
    ("basis_slope(24) > 0.01", "basis"),
    ("oi_price_div(24) > 1.0", "open_interest"),
])
def test_deriv_features_declare_only_their_series(expr, series):
    assert data_requires(parse(expr)) == (series,)
```

**Patterns:**
- Data-driven tests use module-level lists: `GOOD`, `BAD`, `CASES`
- Multi-value parametrize for complex test data
- Test name includes parameter in output when run with `-v`

## Assertion Patterns

**Floating-Point Comparison:**
```python
assert ind.atr_series(df, 14).iloc[-1] == pytest.approx(ind.atr(df, 14))
assert FEATURES["ema"].fn(ctx, 20).iloc[-1] == pytest.approx(
    ind.ema(df["close"], 20).iloc[-1])
```

**Relative and Absolute Tolerance:**
```python
assert ind.zscore_series(s, 96).iloc[-1] == pytest.approx(
    ind.zscore(s, 96), abs=1e-9)  # absolute tolerance
assert ind.realized_vol_series(df, 48).iloc[-1] == pytest.approx(
    ind.realized_vol(df, 48), rel=1e-6)  # relative tolerance
```

**Exception Testing:**
```python
def test_invalid_expressions_rejected(expr):
    with pytest.raises(SpecError):
        parse(expr)
```

**Array/Series Assertions:**
```python
assert lo.dtype == bool and len(lo) == len(frame)
assert not sh.any(), "a long-only spec must never emit shorts"
assert s.index.equals(df.index)
assert np.array_equal(with_f, with_f & without)
```

## Coverage

**Requirements:** Not enforced; no `.coveragerc` or coverage config observed

**View Coverage:**
```bash
./venv/bin/python -m pytest tests/ --cov=trader --cov-report=html
```

**Target:** 937 tests provide substantial coverage of core modules

## Test Types

**Unit Tests:**
- Scope: Individual functions and classes
- Approach: Isolated test with mocked dependencies
- Examples: `test_signal_overlap_*`, `test_entries_are_boolean_arrays_*`, `test_atr_*`
- Assertion: Direct comparison of output to expected

**Integration Tests:**
- Scope: Multiple modules working together
- Approach: Test actual data flow (e.g., Analyst → Evidence → Results)
- Examples: `test_analyst.py` tests full evaluation pipeline
- Mocking: Light; only external APIs mocked

**Backtest/Vector Tests:**
- Scope: Strategy evaluation over historical data
- Approach: Run compiled strategy on full OHLCV frame
- Examples: `test_vector_backtest.py` tests entry/exit mechanics
- Data: Use real or synthetic OHLCV DataFrames

**End-to-End Tests:**
- Scope: Full strategy lifecycle (create, test, admit, trade)
- Examples: `test_entry_geometry_end_to_end()`, `test_declared_universe_is_traded()`
- Approach: Minimal mocking; real logic paths

## Common Patterns

**Testing Edge Cases:**
```python
def test_overlap_handles_different_lengths():
    a = np.array([True, False, True, False])
    b = np.array([True, False, True])
    assert 0.0 <= signal_overlap(a, np.zeros(4, bool),
                                 b, np.zeros(3, bool)) <= 1.0

def test_constant_signal_has_no_correlation():
    z = np.zeros(5, bool)
    assert signal_overlap(z, z, z, z) == 0.0
```

**Testing Empty/None Cases:**
```python
def test_a_frame_that_does_not_reach_the_horizon_stays_ungraded():
    """Frame too short for the lookback window."""
    frame = make_short_frame()
    result = grade_outcome(frame)
    assert result is None
```

**DataFrame Testing:**
```python
def test_series_are_full_length_and_aligned(df):
    for s in (ind.atr_series(df, 14), ind.adx_series(df, 14)):
        assert len(s) == len(df)
        assert s.index.equals(df.index)
```

**Complex Object Assertions:**
```python
def test_evaluate_picks_a_timeframe_and_reports_all(analyst):
    ok, ev = analyst.evaluate(_spec(), timeframes=("1h",))
    assert "chosen_timeframe" in ev and "by_timeframe" in ev
    assert ok is True
    assert ev["chosen_timeframe"] == "1h"
```

## Code Organization

**Typical test file structure:**

```python
import numpy as np
import pandas as pd
import pytest

from trader.module.under_test import Class, function

# ── Test data and factories ─────────────────────────────────────

def _helper_factory():
    """Create test data."""
    pass

@pytest.fixture
def fixture_name():
    """Provide test data or mocked dependency."""
    pass

# ── Tests by category ──────────────────────────────────────────

def test_basic_functionality():
    """Test 1."""
    pass

def test_error_handling():
    """Test 2."""
    pass

@pytest.mark.parametrize("param", [...])
def test_variations(param):
    """Parametrized test."""
    pass
```

---

*Testing analysis: 2026-09-03*
