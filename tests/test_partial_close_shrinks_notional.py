"""A half-closed position must count as half a position.

close_partial() shrank `amount` but left `notional_usdt` at its original
value. Two things ran on the stale number:

  * RiskManager's heat cap sums risk_frac x notional_usdt over open trades.
    A book at TP1 therefore charged twice the size it actually held against
    the 15% cap — throttling exactly the breadth a trend book earns its
    Sharpe from (0.5%/8 beats 0.75%/4 on both return and drawdown).
  * close() bills entry-leg fees as taker_fee * notional_usdt, so the final
    close of any trade that took a partial overstated its costs.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import MarketType, Position, Side
from trader.engine.executor import Executor


class FakeEx:
    def create_order(self, symbol, typ, side, amount, params=None):
        return {"id": "x", "average": 110.0, "price": 110.0}

    def cancel_order(self, oid, symbol):
        return {}


@pytest.fixture
def ex_and_journal(tmp_path):
    j = Journal(tmp_path / "j.db")
    e = Executor(FakeEx(), j, load_config(), MarketType.FUTURES)
    pos = Position(id="pos_1", symbol="BTC/USDT", side=Side.LONG,
                   amount=10.0, entry_price=100.0, notional_usdt=1000.0,
                   stop_loss=98.0, market_type="futures", exec_mode="live")
    j.add_trade(pos)
    return e, j


def _row(j):
    return j.query("SELECT * FROM trades WHERE id='pos_1'")[0]


def test_half_the_position_is_half_the_notional(ex_and_journal):
    e, j = ex_and_journal
    assert e.close_partial(dict(_row(j)), 5.0, 110.0, "tp1") is True
    r = _row(j)
    assert r["amount"] == pytest.approx(5.0)
    assert r["notional_usdt"] == pytest.approx(500.0)   # 5 x entry 100


def test_notional_tracks_entry_price_not_the_exit_fill(ex_and_journal):
    """Notional is what the remaining position COST, so heat is measured
    against the capital actually committed — not marked to the exit."""
    e, j = ex_and_journal
    e.close_partial(dict(_row(j)), 2.0, 110.0, "tp1")
    r = _row(j)
    assert r["amount"] == pytest.approx(8.0)
    assert r["notional_usdt"] == pytest.approx(800.0)   # 8 x 100, not x 110


def test_the_callers_dict_is_updated_too(ex_and_journal):
    """ExitEngine keeps working the same dict after a partial; a stale
    notional there re-enters the next heat check and the fee maths."""
    e, j = ex_and_journal
    t = dict(_row(j))
    e.close_partial(t, 5.0, 110.0, "tp1")
    assert t["amount"] == pytest.approx(5.0)
    assert t["notional_usdt"] == pytest.approx(500.0)


def test_heat_halves_with_the_position(ex_and_journal):
    """The behaviour that actually matters: the cap sees what is held."""
    from trader.engine.risk import RiskManager
    e, j = ex_and_journal
    rm = RiskManager(load_config(), j)
    before = float(_row(j)["notional_usdt"])
    e.close_partial(dict(_row(j)), 5.0, 110.0, "tp1")
    after = float(_row(j)["notional_usdt"])
    assert after == pytest.approx(before / 2)
    assert rm is not None
