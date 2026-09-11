"""Live, a ref() spec reads the same reference frames the backtest did.

Every requirement the live path forgot has meant a spec that sat in paper
forever reading NaN (derivatives, 2026-09-02; the account ratio, 09-11).
"""
import inspect

import pandas as pd

from trader.core.types import Action, Snapshot
from trader.strategy.compile import compile_spec
from trader.strategy.spec import ExitSpec, StrategySpec

H4 = 4 * 3_600_000


def _bars(close0, n=6, start=1_700_000_000_000 - (1_700_000_000_000 % H4)):
    c = [close0 + i for i in range(n)]
    return pd.DataFrame({
        "ts": pd.to_datetime([start + i * H4 for i in range(n)], unit="ms",
                             utc=True),
        "open": c, "high": c, "low": c, "close": c, "volume": [1.0] * n})


def _spec():
    return StrategySpec(
        id="refspec", name="Reference Probe",
        thesis=("BTC dominance above its level marks money rotating out of "
                "alts; a probe that must fire only when that is known live."),
        invalidation="Retire if it fires without its reference frame.",
        provenance={}, universe={"include": []}, timeframe="4h",
        direction="long", entry_long='ref("btcdom", close) > 1000',
        entry_short="", filters=[], exit=ExitSpec(), regime_filter=[],
        markets=["futures"])


def _snap(market):
    return Snapshot(symbol="AAA/USDT", ts="", price=1.0,
                    dfs={"4h": _bars(1.0)}, market=market)


def test_a_ref_spec_fires_when_the_snapshot_carries_its_reference():
    ev = compile_spec(_spec()).to_evaluator()
    sig = ev(None, _snap({"btcdom": _bars(1000.0)}))
    assert sig is not None and sig.action == Action.BUY


def test_without_the_reference_it_cannot_fire():
    ev = compile_spec(_spec()).to_evaluator()
    assert ev(None, _snap(None)) is None


def test_the_kernel_hands_references_to_every_snapshot():
    from trader import kernel as K
    src = inspect.getsource(K)
    assert "market=self._market_for()" in src


def test_market_for_loads_only_what_the_book_needs(monkeypatch):
    from trader import kernel as K
    from trader.strategy import spec_evidence
    calls = []
    monkeypatch.setattr(spec_evidence, "load_refs",
                        lambda req, store=None: calls.append(tuple(req))
                        or {"spx": "F"})
    k = object.__new__(K.Kernel)
    k._spec_requires = ("ohlcv",)
    assert k._market_for() is None and calls == []
    k._spec_requires = ("ohlcv", "ref:spx")
    assert k._market_for() == {"spx": "F"}
    assert k._market_for() == {"spx": "F"} and len(calls) == 1   # cached
