"""Every quantity sent to the venue must be a whole lot, and the journal
must record what the venue actually filled.

UNI/USDT trades in whole coins (precision.amount = 1). Luffy passed raw
floats and let Binance truncate, while the journal kept the untruncated
number. Each leg then rounded down independently against a size the venue
never held:

    entry   607.73360606  -> venue filled 607   (journal kept 607.73360606)
    TP1     303.86680303  -> venue sold   303   (journal kept 303.86680303)
    stop    303.86680303  -> venue sold   303
                             607 - 303 - 303  =  1 UNI stranded

That 1 coin is dust — $6.20 of notional holding $1.24 of isolated margin —
and, because it keeps the symbol present in fetch_positions(), it hid a
completed exit from the kernel for hours. Quantize at the boundary and the
residue cannot accumulate.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import Action, Decision, MarketType, Position, Side
from trader.engine.executor import Executor


class WholeLotEx:
    """A venue whose amount step is 1, like UNI/USDT — it truncates."""

    def __init__(self, fill=5.8933):
        self.orders: list[tuple] = []
        self.stops: list[tuple] = []
        self.fill = fill
        self.position = 0.0        # what the venue actually holds

    def amount_to_precision(self, symbol, amount):
        return str(int(float(amount)))          # ccxt returns a string

    def create_order(self, symbol, typ, side, amount, params=None):
        got = float(self.amount_to_precision(symbol, amount))
        self.position += got if side == "buy" else -got
        self.orders.append((symbol, typ, side, amount))
        return {"id": "o1", "average": self.fill, "price": self.fill,
                "filled": got, "amount": got, "status": "closed"}

    def cancel_order(self, oid, symbol):
        return {}

    def set_leverage(self, lev, symbol):
        return {}


@pytest.fixture
def ex_and_journal(tmp_path):
    ex = WholeLotEx()
    j = Journal(tmp_path / "j.db")
    e = Executor(ex, j, load_config(), MarketType.FUTURES)
    e._place_native_stop = lambda sym, side, amount, px: (
        ex.stops.append((sym, side, amount, px)) or "sl1")
    return ex, j, e


_N = [0]


def _decision(j, sym="UNI/USDT"):
    """A journalled decision — executor.open() schedules an outcome against
    it, and outcomes carry a foreign key back to decisions/cycles."""
    _N[0] += 1
    cid, did = f"cyc_{_N[0]}", f"dec_{_N[0]}"
    d = Decision(id=did, cycle_id=cid, symbol=sym, action=Action.BUY,
                 score=1.0, threshold=0.5, confidence=0.9, votes=[],
                 strategy_signals=[])
    with j._tx() as c:
        c.execute("INSERT INTO cycles(id,ts,symbol,price) VALUES (?,?,?,?)",
                  (cid, d.ts, sym, 100.0))
    j.log_decision(d)
    return d


def test_entry_is_quantized_before_it_reaches_the_venue(ex_and_journal):
    ex, j, e = ex_and_journal
    e.open(_decision(j), 607.73360606, atr=0.1, stop_loss=5.77, take_profit=6.1,
           strategy_id="s", strategy_name="ema_trend")
    sent = ex.orders[0][3]
    assert sent == pytest.approx(607.0), \
        f"venue was handed {sent}, which it silently truncates to 607"


def test_the_journal_records_what_the_venue_filled(ex_and_journal):
    ex, j, e = ex_and_journal
    pos = e.open(_decision(j), 607.73360606, atr=0.1, stop_loss=5.77,
                 take_profit=6.1, strategy_id="s", strategy_name="ema_trend")
    assert pos.amount == pytest.approx(607.0)
    row = j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0]
    assert row["amount"] == pytest.approx(607.0)
    assert row["notional_usdt"] == pytest.approx(round(607 * 5.8933, 2))


def test_the_protective_stop_covers_the_whole_filled_position(ex_and_journal):
    ex, j, e = ex_and_journal
    e.open(_decision(j), 607.73360606, atr=0.1, stop_loss=5.77, take_profit=6.1,
           strategy_id="s", strategy_name="ema_trend")
    assert ex.stops[0][2] == pytest.approx(607.0), \
        "a stop for less than the position leaves the remainder naked"


def test_a_partial_sells_a_whole_number_of_lots(ex_and_journal):
    ex, j, e = ex_and_journal
    pos = e.open(_decision(j), 607.73360606, atr=0.1, stop_loss=5.77,
                 take_profit=6.1, strategy_id="s", strategy_name="ema_trend")
    t = j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0]
    ex.fill = 5.997
    assert e.close_partial(dict(t), float(t["amount"]) * 0.5, 5.997, "tp1")
    assert ex.orders[-1][3] == pytest.approx(303.0)
    row = j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0]
    assert row["amount"] == pytest.approx(304.0), \
        "the remainder is what the venue still holds, not 607.73/2"


def test_the_full_cycle_strands_nothing(ex_and_journal):
    """The regression itself: open, take a half, stop out — venue flat."""
    ex, j, e = ex_and_journal
    pos = e.open(_decision(j), 607.73360606, atr=0.1, stop_loss=5.77,
                 take_profit=6.1, strategy_id="s", strategy_name="ema_trend")

    t = dict(j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0])
    ex.fill = 5.997
    e.close_partial(t, float(t["amount"]) * 0.5, 5.997, "tp1")

    t = dict(j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0])
    ex.fill = 6.187
    e.close(t, 6.187, "sl_fill")

    assert ex.position == pytest.approx(0.0), \
        f"{ex.position} lots left on the venue as unmanaged dust"


def test_a_sub_lot_partial_is_refused_rather_than_sent_as_zero(ex_and_journal):
    """Rounding 0.4 lots down to 0 would be an order the venue rejects —
    and, if it did not, a 'partial' that closes nothing while the journal
    books a fill."""
    ex, j, e = ex_and_journal
    pos = e.open(_decision(j), 607.73360606, atr=0.1, stop_loss=5.77,
                 take_profit=6.1, strategy_id="s", strategy_name="ema_trend")
    t = dict(j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0])
    before = len(ex.orders)
    assert e.close_partial(t, 0.4, 5.99, "tp1") is False
    assert len(ex.orders) == before, "no order may be sent for zero lots"
    row = j.query("SELECT * FROM trades WHERE id=?", (pos.id,))[0]
    assert row["amount"] == pytest.approx(607.0), "nothing was sold"


def test_a_venue_without_precision_helpers_is_left_alone(tmp_path):
    """Spot books, tests and backtests hand in plain fakes; quantization
    must degrade to the requested amount, never crash."""
    class Bare:
        def __init__(self): self.orders = []
        def create_order(self, symbol, typ, side, amount, params=None):
            self.orders.append(amount)
            return {"id": "o", "average": 100.0, "price": 100.0}
    ex = Bare()
    j = Journal(tmp_path / "j.db")
    e = Executor(ex, j, load_config(), MarketType.SPOT)
    e.open(_decision(j, "BTC/USDT"), 1.2345, atr=1.0, stop_loss=98.0,
           take_profit=110.0, strategy_id="s", strategy_name="n")
    assert ex.orders[0] == pytest.approx(1.2345)
