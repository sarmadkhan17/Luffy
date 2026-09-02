"""A spec signals on a CLOSED bar, the way it was validated.

`to_evaluator` read `lo[-1]` — the last row of the live frame — and the live
frame's last row is the bar currently forming. So the live rule was "price
is beyond the level right now" while the rule that earned the statistics is
"the bar CLOSED beyond the level":

    vector_backtest.py:115   px = closes[i]      # signal bar's close

For a breakout mechanism those are not the same rule. Every intrabar poke
that retraces before the close is a live entry the backtest never took —
the textbook false breakout, and precisely the population Donchian's
measured edge excludes.

Observed 2026-09-02 on UNI/USDT 4h: the bar traded up to 6.373, above the
100-bar high, and closed at 5.742, well below it. Under the old rule that is
a long entry into a bar that fell 9%; under the validated rule it is
nothing at all.
"""
import numpy as np
import pandas as pd
import pytest

from trader.core.types import Snapshot
from trader.strategy.compile import compile_spec
from trader.strategy.spec import ExitSpec, StrategySpec

TF_MS = 14_400_000          # 4h


def _breakout_spec() -> StrategySpec:
    return StrategySpec(
        id="brk", name="Breakout Probe",
        thesis="A close beyond the twenty-bar high marks the point where "
               "supply above the market has been exhausted and continuation "
               "is more likely than reversion.",
        invalidation="Retire below profit factor 1.0 over 30 trades.",
        provenance={"source_kind": "test"}, universe={"include": []},
        timeframe="4h", direction="long",
        entry_long="close > donchian_hi(20)", entry_short="",
        filters=[], exit=ExitSpec(), regime_filter=[], markets=["futures"])


def _frame(n: int, last_open: float, last_high: float, last_close: float,
           end_ts: pd.Timestamp) -> pd.DataFrame:
    """Flat history under a ceiling, then one bar that poked above it."""
    close = np.full(n, 100.0)
    high = np.full(n, 100.5)
    low = np.full(n, 99.5)
    open_ = np.full(n, 100.0)
    close[-1], high[-1], open_[-1] = last_close, last_high, last_open
    low[-1] = min(last_close, last_open) - 0.5
    return pd.DataFrame({
        "ts": pd.date_range(end=end_ts, periods=n, freq="4h", tz="UTC"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": np.full(n, 1000.0)})


def _snap(df: pd.DataFrame) -> Snapshot:
    return Snapshot(symbol="UNI/USDT",
                    ts=pd.Timestamp.utcnow().isoformat(),
                    price=float(df["close"].iloc[-1]),
                    dfs={"4h": df}, market_type="futures")


def _forming_end() -> pd.Timestamp:
    """Open time of the 4h bar that is forming right now."""
    now = pd.Timestamp.utcnow().tz_localize(None).tz_localize("UTC")
    ms = int(now.timestamp() * 1000)
    return pd.Timestamp(ms - ms % TF_MS, unit="ms", tz="UTC")


def _closed_end() -> pd.Timestamp:
    return _forming_end() - pd.Timedelta(milliseconds=TF_MS)


def test_a_forming_bar_above_the_level_is_not_yet_a_signal():
    """The live failure: price is beyond the channel, but the bar is open.

    `close` on a forming bar is just the last trade. It is above the
    channel now and may be back inside by the close — which is the case the
    backtest scores as a non-event, because it only ever saw the close.
    """
    ev = compile_spec(_breakout_spec()).to_evaluator()
    df = _frame(60, last_open=100.0, last_high=110.0, last_close=105.0,
                end_ts=_forming_end())
    assert ev(None, _snap(df)) is None, (
        "the bar has not closed yet — entering on a price that may retrace "
        "before the close is the false breakout the backtest never took")


def test_a_closed_bar_above_the_level_is_a_signal():
    """The fix must not silence the real thing."""
    ev = compile_spec(_breakout_spec()).to_evaluator()
    df = _frame(60, last_open=100.0, last_high=110.0, last_close=105.0,
                end_ts=_closed_end())
    sig = ev(None, _snap(df))
    assert sig is not None and sig.action.name == "BUY", (
        "a bar that closed above the channel is exactly the validated entry")


def test_a_forming_bar_does_not_mask_a_closed_signal_beneath_it():
    """The closed bar below the tail still decides.

    A frame ending in a forming bar must be judged on its last CLOSED bar,
    not simply dropped — otherwise a real signal is lost for four hours.
    """
    ev = compile_spec(_breakout_spec()).to_evaluator()
    df = _frame(60, last_open=100.0, last_high=110.0, last_close=105.0,
                end_ts=_closed_end())
    # append a forming bar that is back inside the channel
    forming = df.iloc[[-1]].copy()
    forming["ts"] = _forming_end()
    forming[["open", "high", "low", "close"]] = [105.0, 105.2, 99.0, 99.5]
    df2 = pd.concat([df, forming], ignore_index=True)

    sig = ev(None, _snap(df2))
    assert sig is not None and sig.action.name == "BUY", (
        "the last closed bar broke out; a forming bar behind it must not "
        "erase that signal")
