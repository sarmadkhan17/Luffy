"""A spec built on a reference must be scored with it, or refused UNTESTED.

The failure this prevents has happened three times with other series: a
requirement the evidence path does not load evaluates to NaN, takes zero
trades, and reads as "no edge" instead of "could not be tested".
"""
import types

import numpy as np
import pandas as pd

from trader.core.config import load_config
from trader.strategy import rolling, spec_evidence
from trader.strategy.spec import ExitSpec, StrategySpec


class _Store:
    def __init__(self, have):
        self.have = have

    def load(self, key, since_ms=0):
        return self.have.get(key)


def _days(start, n):
    return pd.DataFrame({"ts": pd.date_range(start, periods=n, freq="D",
                                             tz="UTC"),
                         "close": np.arange(n, dtype=float) + 1})


def test_load_refs_returns_only_what_exists():
    out = spec_evidence.load_refs(["ohlcv", "ref:spx", "ref:dxy"],
                                  _Store({"spx": _days("2024-01-01", 3)}))
    assert set(out) == {"spx"}


def test_an_absent_reference_is_untested():
    spec = types.SimpleNamespace(data_requires=["ohlcv", "ref:spx"])
    gaps = spec_evidence.missing_data(spec, ["BTC/USDT"],
                                      frames={"BTC/USDT": _days("2021-01-01", 900)},
                                      ref_store=_Store({}))
    assert gaps == {"BTC/USDT": ["ref:spx: absent"]}


def test_a_short_reference_is_untested_with_its_coverage():
    spec = types.SimpleNamespace(data_requires=["ohlcv", "ref:spx_1h"])
    gaps = spec_evidence.missing_data(
        spec, ["BTC/USDT"], frames={"BTC/USDT": _days("2021-01-01", 1800)},
        ref_store=_Store({"spx_1h": _days("2024-09-01", 700)}))
    assert "covers" in gaps["BTC/USDT"][0]


def test_a_reference_spanning_the_frame_is_testable():
    spec = types.SimpleNamespace(data_requires=["ohlcv", "ref:spx"])
    gaps = spec_evidence.missing_data(
        spec, ["BTC/USDT"], frames={"BTC/USDT": _days("2021-01-01", 900)},
        ref_store=_Store({"spx": _days("2016-01-01", 4000)}))
    assert gaps == {}


def test_rolling_forwards_the_market_frames(monkeypatch):
    seen = {}

    class _C:
        spec = types.SimpleNamespace(id="x", exit=ExitSpec())

        def entries(self, frames, **kw):
            seen.update(kw)
            n = len(next(iter(frames.values())))
            return np.zeros(n, bool), np.zeros(n, bool)

    monkeypatch.setattr(rolling, "funding_for", lambda *a, **k: None)
    monkeypatch.setattr(rolling, "simulate", lambda *a, **k: types.SimpleNamespace(
        trades=0, profit_factor=0.0, pnl_usdt=0.0, winrate=0.0,
        gross_win=0.0, gross_loss=0.0, wins=0))
    df = pd.DataFrame({"ts": pd.date_range("2025-01-01", periods=50,
                                           freq="4h", tz="UTC"),
                       "close": 1.0})
    rolling._score_window(_C(), {"AAA/USDT": df, "_market": {"spx": "F"}},
                          {}, "4h", 1)
    assert seen["market"] == {"spx": "F"}


def _spec(expr):
    return StrategySpec(
        id="p", name="p", thesis="t", invalidation="i", provenance={},
        universe={"include": []}, timeframe="4h", direction="long",
        entry_long=expr, entry_short="", filters=[], exit=ExitSpec(),
        regime_filter=[], markets=["futures"])


def test_the_analyst_loads_a_spec_s_references(monkeypatch):
    from trader.brain.analyst import Analyst
    a = Analyst.__new__(Analyst)
    a.cfg = {"risk": load_config()["risk"], "strategy": {}}
    monkeypatch.setattr(Analyst, "frames",
                        lambda self, tf, extra=(): {"X": None, "_btc_1h": None})
    monkeypatch.setattr(spec_evidence, "load_refs",
                        lambda req, store=None: {"spx": "F"}
                        if "ref:spx" in req else {})
    frames = a._ctx("4h", _spec('ref("spx", close) > 0'))[0]
    assert frames["_market"] == {"spx": "F"}
    assert "_market" not in a._ctx("4h", _spec("close > 0"))[0]
