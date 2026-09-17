"""An exit is booked at the price it FILLED at, and a trade's P&L is the venue's.

On 2026-09-11 the journal booked 8 closes at +$46.33; the venue's ledger
says +$11.83. Entries matched to the tick. Every exit was booked better than
it filled (AVAX short: 7.447 booked, 7.484 filled), because a demo market
order returns neither `average` nor `price` and the hint was booked instead.
"""
import time
from datetime import datetime, timedelta

import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import MarketType, Position, Side
from trader.engine.executor import Executor

OPENED_AT = "2026-09-11T01:03:01.754364+00:00"
OPEN_MS = int(datetime.fromisoformat(OPENED_AT).timestamp() * 1000)


def _fill(side, price, amount, commission, realized, order=None, t=0):
    return {"id": f"fill-{side}-{price}-{amount}-{commission}-{realized}-{order}-{t}",
            "symbol": "AVAX/USDT", "order": order, "side": side, "price": price, "amount": amount,
            "timestamp": t,
            "info": {"commission": str(commission), "commissionAsset": "USDT", "realizedPnl": str(realized)}}


class DemoEx:
    """Binance Demo's shape: a market order returns no average and no price."""

    def __init__(self, fills):
        self.fills = list(fills)
        self.next_fills = []          # fills the NEXT create_order produces
        self.fetch_raises = False

    def amount_to_precision(self, symbol, amount):
        return str(float(amount))

    def create_order(self, symbol, typ, side, amount, params=None):
        oid = f"c{len(self.fills)}"
        now = int(time.time() * 1000)
        self.fills += [{**f, "order": oid, "timestamp": now} for f in self.next_fills]
        self.next_fills = []
        return {"id": oid, "average": None, "price": None,
                "filled": None, "status": "NEW"}

    def fetch_my_trades(self, symbol, since=None, limit=None):
        if self.fetch_raises:
            raise RuntimeError("venue down")
        return [f for f in self.fills if f["timestamp"] >= (since or 0)]

    def cancel_order(self, oid, symbol):
        return {}


ENTRY = _fill("sell", 7.453, 273.0, 0.81387, 0.0, order="o_entry",
              t=OPEN_MS - 2000)       # lands 2s before opened_at is stamped


@pytest.fixture
def book(tmp_path):
    j = Journal(tmp_path / "j.db")
    ex = DemoEx([ENTRY])
    e = Executor(ex, j, load_config(), MarketType.FUTURES)
    e.fill_retry_s = 0
    j.add_trade(Position(
        id="pos_avax", symbol="AVAX/USDT", side=Side.SHORT, amount=273.0,
        entry_price=7.453, notional_usdt=round(273 * 7.453, 2),
        stop_loss=7.56964, market_type="futures", exec_mode="live",
        strategy_id="s", strategy_name="t", opened_at=OPENED_AT))
    return ex, j, e


def _row(j, tid="pos_avax"):
    return dict(j.query("SELECT * FROM trades WHERE id=?", (tid,))[0])


def test_the_exit_is_booked_at_the_fill_not_the_hint(book):
    ex, j, e = book
    ex.next_fills = [_fill("buy", 7.484, 273.0, 0.81725, -8.463)]
    assert e.close(_row(j), exit_price_hint=7.447, reason="manual")
    r = _row(j)
    assert r["exit_price"] == pytest.approx(7.484)
    assert r["realized_pnl"] == pytest.approx(-8.463 - 0.81387 - 0.81725, abs=1e-6)


def test_a_partial_then_a_close_totals_to_the_venue(book):
    ex, j, e = book
    ex.next_fills = [_fill("buy", 7.30, 136.0, 0.3971, 20.808)]
    assert e.close_partial(_row(j), 136.0, 7.20, "tp1")
    r = _row(j)
    assert r["realized_pnl"] == pytest.approx(20.808 - 0.3971, abs=1e-6)
    assert r["amount"] == pytest.approx(137.0)

    ex.next_fills = [_fill("buy", 7.40, 137.0, 0.4055, 7.261)]
    assert e.close(_row(j), 7.35, "sl_fill")
    venue_total = -0.81387 + (20.808 - 0.3971) + (7.261 - 0.4055)
    r = _row(j)
    assert r["realized_pnl"] == pytest.approx(venue_total, abs=1e-6)
    assert r["exit_price"] == pytest.approx(7.40)


def test_the_previous_trade_on_the_symbol_is_not_counted(book):
    ex, j, e = book
    prev_closed = (datetime.fromisoformat(OPENED_AT)
                   - timedelta(seconds=10)).isoformat()
    j.add_trade(Position(
        id="pos_prev", symbol="AVAX/USDT", side=Side.LONG, amount=100.0,
        entry_price=7.0, notional_usdt=700.0, stop_loss=6.9,
        market_type="futures", exec_mode="live", strategy_id="s",
        strategy_name="t", opened_at="2026-09-11T00:00:00+00:00"))
    j.close_trade("pos_prev", 7.2, 19.0, "tp", closed_at=prev_closed)
    ex.fills.append(_fill("sell", 7.2, 100.0, 1.0, 20.0, order="o_prev",
                          t=OPEN_MS - 12000))   # inside 30s, before prev close
    ex.next_fills = [_fill("buy", 7.484, 273.0, 0.81725, -8.463)]
    assert e.close(_row(j), exit_price_hint=7.447, reason="manual")
    assert _row(j)["realized_pnl"] == pytest.approx(
        -8.463 - 0.81387 - 0.81725, abs=1e-6)


def test_an_unreadable_venue_leaves_the_trade_open_and_says_so(book):
    ex, j, e = book
    ex.fetch_raises = True
    assert not e.close(_row(j), exit_price_hint=7.447, reason="manual")
    row = _row(j)
    assert row["status"] == "open"
    assert row["exit_price"] is None
    ev = j.query("SELECT event FROM control_events "
                 "WHERE event='close_fill_unconfirmed'")
    assert [x["event"] for x in ev] == ["close_fill_unconfirmed"]
