"""Reconcile must notice a position with no stop, not just a stop with no position.

`sweep_orphans` handles one direction — a protective order left behind by a
position that is gone. The reverse was never checked, and it is the dangerous
one: a STOP_MARKET that partially fills leaves a residue holding no
protection at all, and nothing in the system looks.

Observed live on 2026-09-02. A trailing stop on UNI/USDT filled 302.87 of
303.87 coins; reconcile aligned the journal's amount down to the 1.0 that
remained and reported success. `scripts/monitor.py` — which asks the venue —
saw "UNIUSDT is NAKED"; the kernel saw a clean reconcile. The residue was
also below the venue's minimum notional, so it could not be re-armed even in
principle, and it blocked its own symbol through the one-position-per-symbol
guard for thirteen hours while that symbol sat past its entry trigger.

Detection is the fix here. Placing orders is the executor's job and arming a
stop that the venue will refuse for size is not protection.
"""
from trader.engine import reconcile


class _Journal:
    def __init__(self, open_rows):
        self._open = open_rows
        self.events = []

    def open_trades(self):
        return list(self._open)

    def log_control_event(self, *a, **k):
        self.events.append((a, k))

    def align_trade_amount(self, *a, **k):
        pass

    def close_trade(self, *a, **k):
        pass


def _pos(symbol, contracts, notional=5000.0):
    return {"symbol": symbol, "contracts": contracts, "notional": notional,
            "side": "long", "entryPrice": 100.0}


def _trade(symbol, amount, sl_order_id=""):
    return {"id": f"t_{symbol}", "symbol": symbol, "amount": amount,
            "side": "long", "entry_price": 100.0, "market_type": "futures",
            "sl_order_id": sl_order_id, "stop_loss": 95.0}


def test_a_position_with_no_protective_order_is_reported(monkeypatch):
    monkeypatch.setattr(reconcile.protective, "sweep_orphans",
                        lambda *a, **k: (0, 0))
    monkeypatch.setattr(reconcile.protective, "open_stops", lambda ex: [])
    j = _Journal([_trade("UNI/USDT", 1.0)])
    ex = type("E", (), {"fetch_positions": lambda s: [_pos("UNI/USDT", 1.0)]})()
    out = reconcile.reconcile_futures(ex, j)
    assert out.get("naked") == 1


def test_a_protected_position_is_not_reported(monkeypatch):
    monkeypatch.setattr(reconcile.protective, "sweep_orphans",
                        lambda *a, **k: (0, 0))
    monkeypatch.setattr(reconcile.protective, "open_stops", lambda ex: [
        {"id": "9", "symbol": "UNIUSDT", "side": "sell", "amount": 1.0,
         "stop_price": 95.0, "kind": "algo"}])
    j = _Journal([_trade("UNI/USDT", 1.0, sl_order_id="9")])
    ex = type("E", (), {"fetch_positions": lambda s: [_pos("UNI/USDT", 1.0)]})()
    out = reconcile.reconcile_futures(ex, j)
    assert out.get("naked") == 0


def test_the_venue_spelling_does_not_hide_a_stop(monkeypatch):
    """Algo rows come back as UNIUSDT while positions are UNI/USDT:USDT. A
    naive comparison reports every protected position as naked and every
    naked one as fine, depending which way it fails."""
    monkeypatch.setattr(reconcile.protective, "sweep_orphans",
                        lambda *a, **k: (0, 0))
    monkeypatch.setattr(reconcile.protective, "open_stops", lambda ex: [
        {"id": "9", "symbol": "UNIUSDT", "side": "sell", "amount": 1.0,
         "stop_price": 95.0, "kind": "algo"}])
    j = _Journal([_trade("UNI/USDT", 1.0, sl_order_id="9")])
    ex = type("E", (), {
        "fetch_positions": lambda s: [_pos("UNI/USDT:USDT", 1.0)]})()
    out = reconcile.reconcile_futures(ex, j)
    assert out.get("naked") == 0


def test_a_failure_to_read_stops_is_not_read_as_protection(monkeypatch):
    """If the venue will not say, the answer is unknown — never 'protected'."""
    def boom(ex):
        raise RuntimeError("venue down")
    monkeypatch.setattr(reconcile.protective, "sweep_orphans",
                        lambda *a, **k: (0, 0))
    monkeypatch.setattr(reconcile.protective, "open_stops", boom)
    j = _Journal([_trade("UNI/USDT", 1.0)])
    ex = type("E", (), {"fetch_positions": lambda s: [_pos("UNI/USDT", 1.0)]})()
    out = reconcile.reconcile_futures(ex, j)
    assert out.get("naked") is None
