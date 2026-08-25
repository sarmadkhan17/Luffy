"""ExitEngine ladder tests — deterministic scenarios, fake exchange."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.engine.exits import ExitEngine, ExitConfig


class FakeEx:
    def __init__(self):
        self.cancelled = []
        self.stops = []

    def cancel_order(self, oid, sym):
        self.cancelled.append(oid)

    def create_order(self, sym, type_, side, amount, params=None):
        self.stops.append((sym, params))
        return {"id": f"s{len(self.stops)}"}


class FakeExec:
    def __init__(self, journal=None):
        self.journal = journal
        self.closed = None
        self.partials = []

    def close(self, trade, exit_price_hint=0, reason=""):
        self.closed = reason
        if self.journal:
            self.journal.close_trade(trade["id"], exit_price_hint, 0, reason)
        return True

    def close_partial(self, trade, amount, price, reason):
        if self.journal:
            with self.journal._tx() as c:
                c.execute("UPDATE trades SET amount=amount-?, tp1_done=1 "
                          "WHERE id=?", (amount, trade["id"]))
        self.partials.append((amount, reason))
        return True


def make_trade(**kw):
    base = dict(id="t1", symbol="X/USDT", side="long", amount=2.0,
                entry_price=100.0, stop_loss=98.0, take_profit=110.0,
                opened_at="2026-08-25T00:00:00+00:00", strategy_id="s1",
                sl_order_id="sl1")
    base.update(kw)
    t = dict(base)
    return t


def make_engine(tmp_path):
    j = Journal(tmp_path / "j.db")
    j.add_trade(Position(id="t1", symbol="X/USDT", side=Side.LONG, amount=2,
                         entry_price=100, notional_usdt=200, stop_loss=98,
                         take_profit=110, strategy_id="s1"))
    ex = FakeEx()
    exex = FakeExec(j)
    eng = ExitEngine(ex, j, exex, {"risk": {}})
    return j, ex, exex, eng


def test_tp1_then_breakeven(tmp_path):
    j, ex, exex, eng = make_engine(tmp_path)
    t = make_trade()
    # +2R → TP1 fires (tp1_r=1.5), stop moves to breakeven
    reason = eng.manage(t, mark=104.0, atr=1.0, current_score=None)
    assert reason == "tp1"
    assert exex.partials and exex.partials[0][1].startswith("tp1")
    row = j.query("SELECT * FROM trades WHERE id='t1'")[0]
    assert abs(row["stop_loss"] - 100.06) < 0.01      # BE + buffer
    assert row["tp1_done"] == 1
    assert abs(row["amount"] - 1.0) < 1e-6            # half closed


def test_trail_ratchets_after_tp1(tmp_path):
    j, ex, exex, eng = make_engine(tmp_path)
    j.query("UPDATE trades SET tp1_done=1, amount=1.0, stop_loss=100.06 "
            "WHERE id='t1'")                       # TP1 already banked
    t = make_trade(tp1_done=1, amount=1.0)
    # trail_atr=2.2 → trail = 106 - 2.2 = 103.8 > BE
    eng.manage(t, mark=106.0, atr=1.0, current_score=None)
    row = j.query("SELECT * FROM trades WHERE id='t1'")[0]
    assert abs(row["stop_loss"] - 103.8) < 0.01
    # price rises → trail follows up
    eng.manage(t, mark=108.0, atr=1.0, current_score=None)
    row = j.query("SELECT * FROM trades WHERE id='t1'")[0]
    assert row["stop_loss"] > 103.8
    # price falls → trail never loosens
    eng.manage(t, mark=104.0, atr=1.0, current_score=None)
    row = j.query("SELECT * FROM trades WHERE id='t1'")[0]
    assert row["stop_loss"] > 103.8


def test_time_stop_closes_stale_loser(tmp_path):
    j, ex, exex, eng = make_engine(tmp_path)
    j.query("UPDATE trades SET opened_at='2026-08-20T00:00:00+00:00' "
            "WHERE id='t1'")                        # 5 days old in journal
    t = make_trade(opened_at="2026-08-20T00:00:00+00:00")
    reason = eng.manage(t, mark=99.5, atr=1.0, current_score=None)
    assert reason and reason.startswith("time_stop")
    assert exex.closed.startswith("time_stop")


def test_flip_exit_on_hard_opposite_score(tmp_path):
    j, ex, exex, eng = make_engine(tmp_path)
    t = make_trade()                                        # long
    reason = eng.manage(t, mark=100.5, atr=1.0, current_score=-0.30)
    assert reason and reason.startswith("flip_exit")
    # but NOT when in good profit (r>1 protects)
    t2 = make_trade(id="t2")
    reason2 = eng.manage(t2, mark=103.5, atr=1.0, current_score=-0.30)
    assert reason2 is None or reason2.startswith("tp1") or reason2 is None


def test_no_premature_action(tmp_path):
    j, ex, exex, eng = make_engine(tmp_path)
    t = make_trade()
    reason = eng.manage(t, mark=100.5, atr=1.0, current_score=0.1)
    assert reason is None
    row = j.query("SELECT stop_loss FROM trades WHERE id='t1'")[0]
    assert row["stop_loss"] == 98.0
