"""R is measured against the risk the trade was SIZED on, not today's stop.

ExitEngine computed `initial_risk = abs(entry - stop_loss)` from the trade's
CURRENT stop. The trail ratchet moves that stop toward and past entry, so
the denominator collapses and R explodes — a live UNI position up 2.1%
logged `TRAIL UNI/USDT: stop -> 6.022 (R=83.06)`.

Three exits gate on R:
  * the time stop fires only when `r_now < 0.5`
  * the flip exit only when `r_now < 1.0`
  * TP1 at `r_now >= 1.5`

With R inflated, none of them can fire again once trailing has begun. For
Donchian Breakout Trail that silently removes the 500-bar (83-day) time cap
the strategy was validated with, so the engine stops running the geometry
the backtest measured.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import Position, Side


@pytest.fixture
def journal(tmp_path):
    return Journal(tmp_path / "j.db")


def _add(j, entry=100.0, sl=98.0):
    j.add_trade(Position(id="pos_1", symbol="BTC/USDT", side=Side.LONG,
                         amount=10.0, entry_price=entry, notional_usdt=1000.0,
                         stop_loss=sl, market_type="futures",
                         exec_mode="live"))
    return j.query("SELECT * FROM trades WHERE id='pos_1'")[0]


def test_initial_risk_is_written_at_entry(journal):
    assert _add(journal)["initial_risk"] == pytest.approx(2.0)


def test_it_survives_the_stop_moving_to_breakeven(journal):
    """The exact live case: stop ratcheted to entry+fees, R must not explode."""
    _add(journal)
    journal.update_position_protection("pos_1", 100.06, None)
    t = dict(journal.query("SELECT * FROM trades WHERE id='pos_1'")[0])
    assert t["stop_loss"] == pytest.approx(100.06)
    assert t["initial_risk"] == pytest.approx(2.0)     # unchanged

    entry = float(t["entry_price"])
    risk = float(t["initial_risk"])
    mark = 102.1                                       # +2.1%, as UNI was
    assert (mark - entry) / risk == pytest.approx(1.05, abs=0.01)
    # what the old code computed instead:
    stale = (mark - entry) / abs(entry - float(t["stop_loss"]))
    assert stale > 30                                  # the R=83 class of lie


def test_a_trade_with_no_recorded_risk_falls_back_to_the_stop(journal):
    """Rows written before the column existed must keep working."""
    _add(journal)
    with journal._tx() as c:
        c.execute("UPDATE trades SET initial_risk=NULL WHERE id='pos_1'")
    t = dict(journal.query("SELECT * FROM trades WHERE id='pos_1'")[0])
    risk = float(t.get("initial_risk") or 0) or abs(
        float(t["entry_price"]) - float(t["stop_loss"]))
    assert risk == pytest.approx(2.0)


def test_a_short_records_a_positive_risk(journal):
    journal.add_trade(Position(id="pos_s", symbol="BTC/USDT", side=Side.SHORT,
                               amount=10.0, entry_price=100.0,
                               notional_usdt=1000.0, stop_loss=103.0,
                               market_type="futures", exec_mode="live"))
    r = journal.query("SELECT * FROM trades WHERE id='pos_s'")[0]
    assert r["initial_risk"] == pytest.approx(3.0)


def test_the_time_stop_can_still_fire_on_a_trailed_loser(journal):
    """The behaviour the bug removed: a position that trailed up, came back,
    and is now flat on entry must still be recyclable by the time stop."""
    _add(journal)
    journal.update_position_protection("pos_1", 100.06, None)   # breakeven
    t = dict(journal.query("SELECT * FROM trades WHERE id='pos_1'")[0])
    mark, entry = 100.2, float(t["entry_price"])
    r_now = (mark - entry) / float(t["initial_risk"])
    assert r_now < 0.5                       # time stop is reachable
    stale_r = (mark - entry) / abs(entry - float(t["stop_loss"]))
    assert stale_r >= 0.5                    # the old maths blocked it
