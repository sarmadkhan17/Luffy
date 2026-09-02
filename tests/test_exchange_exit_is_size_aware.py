"""A stop that fills is a stop that filled, even if a coin is left behind.

`_detect_exchange_exits` asked one question: is the symbol still in
fetch_positions() with contracts > 0? On 2026-09-02 UNI/USDT's trailing stop
sold 303 of the 304 coins the venue held. One coin remained — $6.20 of
notional on $1.24 of margin — so the symbol stayed in the set and the exit
was invisible. For the rest of the session the journal carried a 303.87-coin,
$1,791 position that no longer existed: the +$89 was never attributed to
`ema_trend`, a trade slot and the heat budget stayed spent, and `sl_order_id`
pointed at an order the venue had already consumed.

Presence is not size. The venue's number is the position.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from trader.kernel import Kernel


class _Journal:
    def __init__(self, trades):
        self._t = {t["id"]: dict(t) for t in trades}
        self.closed: list[tuple] = []
    def open_trades(self):
        return [dict(t) for t in self._t.values() if t.get("status", "open") == "open"]
    def close_trade(self, tid, px, pnl, reason):
        self._t[tid]["status"] = "closed"
        self.closed.append((tid, px, round(pnl, 2), reason))
    def align_trade_amount(self, tid, amount, notional, pnl_delta=0.0):
        t = self._t[tid]                       # rounds as the real journal does
        t["amount"], t["notional_usdt"] = round(amount, 8), round(notional, 2)
        t["realized_pnl"] = float(t.get("realized_pnl") or 0) + pnl_delta


class _Ex:
    def __init__(self, positions): self._p = positions
    def fetch_positions(self):
        return [{"symbol": s, "contracts": c} for s, c in self._p.items()]


class _Feed:
    def __init__(self, px): self._px = px
    def price(self, sym): return self._px


class _Notifier:
    def __init__(self): self.sent = []
    def send(self, msg): self.sent.append(msg)


class _Executor:
    taker_fee = 0.0005


def _kernel(venue, journal_amount=303.86680303, px=6.187):
    k = object.__new__(Kernel)
    k.journal = _Journal([{
        "id": "pos_uni", "symbol": "UNI/USDT", "side": "long",
        "market_type": "futures", "entry_price": 5.8933,
        "amount": journal_amount,
        "notional_usdt": round(journal_amount * 5.8933, 2),
        "stop_loss": 6.1884, "take_profit": 0.0, "realized_pnl": 29.70,
        "status": "open"}])
    k.exchange = _Ex(venue)
    k.feed = _Feed(px)
    k.notifier = _Notifier()
    k.executor = _Executor()
    return k


def test_a_vanished_position_still_closes_the_trade():
    """The behaviour that already worked must keep working."""
    k = _kernel(venue={})
    assert k._detect_exchange_exits("UNI/USDT") == 1
    assert k.journal.closed and k.journal.closed[0][3] == "sl_fill"


def test_a_dust_remainder_does_not_hide_the_fill():
    """303 of 304 sold. One coin left. The exit happened."""
    k = _kernel(venue={"UNI/USDT": 1.0})
    assert k._detect_exchange_exits("UNI/USDT") == 1, \
        "the stop filled; a 1-coin residue is not an open 303-coin position"


def test_the_journal_is_aligned_to_what_the_venue_holds():
    k = _kernel(venue={"UNI/USDT": 1.0})
    k._detect_exchange_exits("UNI/USDT")
    t = k.journal.open_trades()[0]
    assert t["amount"] == pytest.approx(1.0)
    assert t["notional_usdt"] == pytest.approx(round(1.0 * 5.8933, 2)), \
        "heat and the position cap must charge $6, not $1,791"


def test_the_closed_portion_is_booked_as_realized_pnl():
    """+$89 gross on 302.87 coins from 5.8933 to 6.187, less taker fees
    both legs. Unbooked, `ema_trend` reads worse than it traded."""
    k = _kernel(venue={"UNI/USDT": 1.0})
    k._detect_exchange_exits("UNI/USDT")
    t = k.journal.open_trades()[0]
    sold = 303.86680303 - 1.0
    gross = (6.187 - 5.8933) * sold
    fees = 0.0005 * (sold * 5.8933 + sold * 6.187)
    assert t["realized_pnl"] == pytest.approx(29.70 + gross - fees, abs=0.01)


def test_a_position_the_venue_still_fully_holds_is_untouched():
    k = _kernel(venue={"UNI/USDT": 303.86680303})
    assert k._detect_exchange_exits("UNI/USDT") == 0
    assert k.journal.open_trades()[0]["amount"] == pytest.approx(303.86680303)


def test_lot_rounding_alone_is_not_read_as_an_exit():
    """A venue holding 607 against a journalled 607.73 is the same position;
    booking an exit on sub-lot noise would shred every trade."""
    k = _kernel(venue={"UNI/USDT": 607.0}, journal_amount=607.73360606)
    assert k._detect_exchange_exits("UNI/USDT") == 0
    assert k.journal.closed == []


def test_other_symbols_are_not_reconciled_by_this_call():
    k = _kernel(venue={"BTC/USDT": 1.0})
    assert k._detect_exchange_exits("BTC/USDT") == 0
    assert k.journal.open_trades()[0]["amount"] == pytest.approx(303.86680303)


def test_a_venue_that_cannot_be_reached_changes_nothing():
    class Dead:
        def fetch_positions(self): raise RuntimeError("network")
    k = _kernel(venue={})
    k.exchange = Dead()
    assert k._detect_exchange_exits("UNI/USDT") == 0
    assert k.journal.closed == []


def test_the_partial_exit_is_announced():
    k = _kernel(venue={"UNI/USDT": 1.0})
    k._detect_exchange_exits("UNI/USDT")
    assert any("UNI" in m for m in k.notifier.sent)
