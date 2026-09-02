"""A trade's realized P&L is every leg it closed, not just the last one.

`close_trade` wrote `realized_pnl=?`, overwriting whatever `close_partial`
had accumulated. Every trade that took its 1.5R partial therefore recorded
only the final leg. The 2026-08-30 UNI trade is the measurement: it banked
+24.70 at the partial and +30.91 on the stop, netting +54.31 at the venue —
and the journal says +18.24.

That number is not cosmetic. It is the input to the Analyst's pooled PF, the
30-day decay gate and the judge's book review, so the selection loop was
grading a book on roughly a third of what it earned, biased hardest against
exactly the trend trades that run far enough to take a partial.

The second half of the same error: `close()` priced the exit over the whole
journalled amount rather than what the venue filled. That last UNI leg sent
168 reduce-only against a venue holding 1, and booked P&L on 168.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import MarketType, Position, Side
from trader.engine.executor import Executor


class _Ex:
    """Reduce-only fills only what the venue still holds."""

    def __init__(self, held=337.0, fill=5.304):
        self.held, self.fill = held, fill
        self.orders: list[float] = []

    def amount_to_precision(self, symbol, amount):
        return str(int(float(amount)))

    def create_order(self, symbol, typ, side, amount, params=None):
        want = float(self.amount_to_precision(symbol, amount))
        got = min(want, self.held) if (params or {}).get("reduceOnly") else want
        self.held -= got if (params or {}).get("reduceOnly") else -got
        self.orders.append(got)
        return {"id": "o", "average": self.fill, "price": self.fill,
                "filled": got, "status": "closed"}

    def cancel_order(self, oid, symbol):
        return {}


@pytest.fixture
def setup(tmp_path):
    ex = _Ex()
    j = Journal(tmp_path / "j.db")
    e = Executor(ex, j, load_config(), MarketType.FUTURES)
    j.add_trade(Position(
        id="pos_uni", symbol="UNI/USDT", side=Side.LONG, amount=337.0,
        entry_price=5.157, notional_usdt=round(337 * 5.157, 2),
        stop_loss=5.05, market_type="futures", exec_mode="live",
        strategy_id="s", strategy_name="ema_trend"))
    return ex, j, e


def _row(j):
    return dict(j.query("SELECT * FROM trades WHERE id='pos_uni'")[0])


def test_the_partial_survives_the_close(setup):
    ex, j, e = setup
    e.close_partial(_row(j), 168.0, 5.304, "tp1")
    booked_partial = _row(j)["realized_pnl"]
    assert booked_partial > 0

    ex.fill = 5.10                       # the rest stops out at a loss
    e.close(_row(j), 5.10, "sl_fill")
    leg2 = ((5.10 - 5.157) * 169.0
            - e.taker_fee * (169.0 * 5.157 + 169.0 * 5.10))
    assert leg2 < 0
    r = _row(j)["realized_pnl"]
    assert r == pytest.approx(booked_partial + leg2, abs=0.01), \
        f"a losing final leg overwrote a winning partial: {r}"


def test_the_total_is_the_sum_of_both_legs(setup):
    ex, j, e = setup
    e.close_partial(_row(j), 168.0, 5.304, "tp1")
    leg1 = _row(j)["realized_pnl"]
    ex.fill = 5.341
    e.close(_row(j), 5.341, "sl_fill")
    total = _row(j)["realized_pnl"]

    leg2_gross = (5.341 - 5.157) * 169.0
    leg2_fees = e.taker_fee * (169.0 * 5.157 + 169.0 * 5.341)
    assert total == pytest.approx(leg1 + leg2_gross - leg2_fees, abs=0.01)


def test_a_close_prices_what_the_venue_filled(setup):
    """Reduce-only against a venue holding 1 fills 1. Booking the journal's
    169 fabricates 168 coins of P&L out of a rounding residue."""
    ex, j, e = setup
    e.close_partial(_row(j), 168.0, 5.304, "tp1")
    leg1 = _row(j)["realized_pnl"]
    ex.held = 1.0                       # the rest went out on the venue's stop
    ex.fill = 5.268
    e.close(_row(j), 5.268, "sl_fill")

    leg2_gross = (5.268 - 5.157) * 1.0
    leg2_fees = e.taker_fee * (1.0 * 5.157 + 1.0 * 5.268)
    assert _row(j)["realized_pnl"] == pytest.approx(
        leg1 + leg2_gross - leg2_fees, abs=0.01)


def test_a_trade_without_a_partial_is_unchanged(setup):
    """The common path must book exactly what it always did."""
    ex, j, e = setup
    ex.fill = 5.341
    e.close(_row(j), 5.341, "sl_fill")
    gross = (5.341 - 5.157) * 337.0
    fees = e.taker_fee * (round(337 * 5.157, 2) + 337.0 * 5.341)
    assert _row(j)["realized_pnl"] == pytest.approx(gross - fees, abs=0.01)


def test_exit_price_and_status_still_come_from_the_final_leg(setup):
    ex, j, e = setup
    e.close_partial(_row(j), 168.0, 5.304, "tp1")
    ex.fill = 5.341
    e.close(_row(j), 5.341, "sl_fill")
    r = _row(j)
    assert r["status"] == "closed"
    assert r["exit_price"] == pytest.approx(5.341)
    assert r["close_reason"] == "sl_fill"
