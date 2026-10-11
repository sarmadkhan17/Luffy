"""P-1: an ambiguous native-stop placement must not stack a second stop."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.types import Side
from trader.engine.executor import Executor


class Venue:
    def __init__(self, reached=True, listing_ok=True):
        self.reached, self.listing_ok = reached, listing_ok
        self.algo = []
        self.plain = []

    def create_order(self, sym, type_, side, amount, params=None):
        params = params or {}
        if "stopLossPrice" in params:           # protective.place_stop path
            if self.reached:
                self.algo.append({"algoId": "A1", "symbol": "BTCUSDT", "side": side.upper(),
                                  "quantity": str(amount), "triggerPrice": str(params["stopLossPrice"]),
                                  "reduceOnly": "true", "orderType": "STOP_MARKET"})
            raise TimeoutError("ack lost")
        self.plain.append((type_, params))
        return {"id": "P1"}

    def fapiPrivateGetOpenAlgoOrders(self):
        if not self.listing_ok:
            raise RuntimeError("listing down")
        return list(self.algo)


def _ex(venue):
    e = Executor.__new__(Executor)
    e.ex = venue
    return e


def test_reached_stop_is_adopted_not_duplicated():
    v = Venue(reached=True)
    oid = _ex(v)._place_native_stop("BTC/USDT", Side.LONG, 1.0, 98.0)
    assert oid == "A1" and v.plain == []


def test_unreached_stop_still_falls_back():
    v = Venue(reached=False)
    oid = _ex(v)._place_native_stop("BTC/USDT", Side.LONG, 1.0, 98.0)
    assert oid == "P1" and len(v.plain) == 1


def test_unreadable_listing_keeps_the_fallback_protection():
    v = Venue(reached=True, listing_ok=False)
    oid = _ex(v)._place_native_stop("BTC/USDT", Side.LONG, 1.0, 98.0)
    assert oid == "P1"            # a duplicate stop is survivable, naked is not
