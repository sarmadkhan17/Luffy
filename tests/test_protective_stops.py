"""Binance books a reduceOnly stop as an ALGO order, not an ordinary one.

Consequences, all of which were live on the account:

  * `cancel_order(id, symbol)` answers -2011 "Unknown order sent" every
    time, so the trail ratchet — which places the replacement, then cancels
    the incumbent — leaked one live stop on EVERY ratchet.
  * `fetch_open_orders()` returns nothing, so reconcile and the dashboard
    saw zero protective orders on positions that were in fact stopped.
  * `/panic` closes the position and leaks its stop too.

24 stops were live at discovery, including nine BUY stops on SUI from a
short closed days earlier. A leaked stop is reduceOnly, so it is inert while
the symbol is flat — and stops being inert the moment a new position opens
on the opposite side, where it is exactly the closing order and fires at a
trigger belonging to a trade that no longer exists.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from trader.engine import protective as P


class FakeEx:
    """A venue that books stops as algo orders, like Binance USDM."""

    def __init__(self, algo=None, plain=None, algo_delete_ok=True):
        self.algo = list(algo or [])
        self.plain = list(plain or [])
        self.algo_delete_ok = algo_delete_ok
        self.cancelled_plain: list = []
        self.created: list = []

    # ── algo endpoints ──
    def fapiPrivateGetOpenAlgoOrders(self):
        return {"orders": list(self.algo)}

    def fapiPrivateDeleteAlgoOrder(self, params):
        aid = str(params["algoId"])
        if not self.algo_delete_ok:
            raise Exception('binanceusdm {"code":-2011,"msg":"Unknown order"}')
        before = len(self.algo)
        self.algo = [o for o in self.algo if str(o["algoId"]) != aid]
        if len(self.algo) == before:
            raise Exception('{"code":-2011,"msg":"Unknown order sent."}')
        return {"algoId": aid, "code": "200"}

    # ── ordinary endpoints ──
    def fetch_open_orders(self, symbol=None):
        return list(self.plain)

    def cancel_order(self, oid, symbol):
        self.cancelled_plain.append((oid, symbol))
        return {"id": oid}

    def create_order(self, symbol, typ, side, amount, params=None):
        self.created.append((symbol, side, amount, (params or {})))
        return {"id": None, "info": {"algoId": 999}}


def _algo(aid, sym, side="SELL", qty=10.0, trig=1.0):
    return {"algoId": aid, "symbol": sym, "side": side,
            "quantity": qty, "triggerPrice": trig, "algoStatus": "NEW"}


# ── the id a stop actually gets ─────────────────────────────────────────
def test_place_stop_reads_the_algo_id_when_there_is_no_order_id():
    """ccxt returns id=None for an algo order; the id lives in info.algoId.
    Losing it means the stop can never be cancelled or recognised again."""
    ex = FakeEx()
    assert P.place_stop(ex, "UNI/USDT", "sell", 304.0, 5.9) == "999"
    sym, side, amt, params = ex.created[0]
    assert params["reduceOnly"] is True and params["stopLossPrice"] == 5.9


# ── cancel ───────────────────────────────────────────────────────────────
def test_cancel_goes_to_the_algo_endpoint_first():
    ex = FakeEx(algo=[_algo(1, "SUIUSDT")])
    assert P.cancel_stop(ex, "1", "SUI/USDT") is True
    assert ex.algo == [] and ex.cancelled_plain == []


def test_cancel_falls_back_for_a_venue_with_ordinary_stops():
    ex = FakeEx(algo=[], algo_delete_ok=False)
    assert P.cancel_stop(ex, "77", "BTC/USDT") is True
    assert ex.cancelled_plain == [("77", "BTC/USDT")]


def test_an_empty_id_is_already_cancelled():
    ex = FakeEx()
    assert P.cancel_stop(ex, "", "BTC/USDT") is True
    assert ex.cancelled_plain == []


# ── enumeration ──────────────────────────────────────────────────────────
def test_open_stops_sees_algo_orders_fetch_open_orders_cannot():
    ex = FakeEx(algo=[_algo(1, "UNIUSDT"), _algo(2, "FILUSDT")])
    assert ex.fetch_open_orders("UNI/USDT") == []      # the blind spot
    ids = {o["id"] for o in P.open_stops(ex)}
    assert ids == {"1", "2"}


@pytest.mark.parametrize("spelling", ["SUIUSDT", "SUI/USDT", "SUI/USDT:USDT"])
def test_one_market_one_key(spelling):
    assert P.venue_key(spelling) == "SUIUSDT"


# ── the sweep ────────────────────────────────────────────────────────────
def test_stops_of_a_closed_position_are_cancelled():
    """The nine SUI stops: position long gone, stops still armed."""
    ex = FakeEx(algo=[_algo(i, "SUIUSDT", "BUY") for i in range(1, 10)])
    cancelled, failed = P.sweep_orphans(ex, keep_ids=set(),
                                        live_symbols=set(), protected=set())
    assert (cancelled, failed) == (9, 0) and ex.algo == []


def test_the_journals_own_stop_survives():
    ex = FakeEx(algo=[_algo(1, "UNIUSDT"), _algo(2, "UNIUSDT")])
    cancelled, _ = P.sweep_orphans(ex, keep_ids={"1"},
                                   live_symbols={"UNI/USDT"},
                                   protected={"UNI/USDT"})
    assert cancelled == 1
    assert [str(o["algoId"]) for o in ex.algo] == ["1"]


def test_ratchet_residue_on_a_live_position_is_cleared():
    """Stale stops from earlier ratchets sit below the live one. They are
    still armed, and they are not what the journal thinks protects this
    position."""
    ex = FakeEx(algo=[_algo(1, "FILUSDT", trig=0.755),
                      _algo(2, "FILUSDT", trig=0.7745),
                      _algo(3, "FILUSDT", trig=0.7794)])
    cancelled, _ = P.sweep_orphans(ex, keep_ids={"3"},
                                   live_symbols={"FIL/USDT"},
                                   protected={"FIL/USDT"})
    assert cancelled == 2
    assert [str(o["algoId"]) for o in ex.algo] == ["3"]


def test_a_live_position_with_no_known_stop_is_never_stripped():
    """If the journal lost the id, the stop on the venue is the only thing
    standing between the position and an unbounded loss. Leave it."""
    ex = FakeEx(algo=[_algo(1, "BTCUSDT"), _algo(2, "ETHUSDT")])
    cancelled, failed = P.sweep_orphans(
        ex, keep_ids={"2"}, live_symbols={"BTC/USDT", "ETH/USDT"},
        protected={"ETH/USDT"})           # BTC's stop id is unknown
    assert (cancelled, failed) == (0, 0)
    assert len(ex.algo) == 2


def test_a_stop_that_will_not_cancel_is_reported_not_swallowed():
    ex = FakeEx(algo=[_algo(1, "MOVRUSDT")], algo_delete_ok=False)
    ex.cancel_order = lambda oid, sym: (_ for _ in ()).throw(Exception("nope"))
    cancelled, failed = P.sweep_orphans(ex, set(), set(), set())
    assert (cancelled, failed) == (0, 1)
