"""Detecting a naked position is not protecting it.

Reconcile learned to SEE a position with no stop on 2026-09-02, and stopped
there: it reported `naked: N` at ERROR and placed nothing. The reason
recorded for that was wrong. It said the UNI residue "was under the venue's
minimum notional and so could not be re-armed at all" — but UNI's filters
are MIN_NOTIONAL 5 and LOT_SIZE minQty 1 step 1, and the residue was $5.74
on 1.0 coin. Both clear. `min_notional_usdt: 10` in config.yaml is OUR
entry-sizing floor in `risk.check_entry`, not the venue's, and the two were
conflated. Nothing ever attempted a stop; there is no failure in the log,
only the report.

That was survivable while the only naked position was dust from a retired
genome. It is not survivable for a live position after a partial stop fill,
which is exactly how the UNI residue was created.

Two rules the venue forces:

  - Size the stop from the VENUE's position, never the journal's amount. The
    journal said 303.87 while 1.0 remained; a stop for 303.87 is refused,
    and that is the case that produces a naked position in the first place.
  - A stop already through its level cannot be armed. Binance answers -2021
    "order would immediately trigger" for a reduceOnly sell stop above the
    mark. A long whose stop sits above the market should be CLOSED, not
    stopped, and closing is the executor's call — reconcile must not send it.
"""
from trader.engine import reconcile


class _Journal:
    def __init__(self, open_rows):
        self._open = open_rows
        self.events = []
        self.stops_written = []

    def open_trades(self):
        return list(self._open)

    def log_control_event(self, *a, **k):
        self.events.append((a, k))

    def align_trade_amount(self, *a, **k):
        pass

    def close_trade(self, *a, **k):
        pass

    def record_stop_order(self, trade_id, order_id, stop_price):
        self.stops_written.append((trade_id, order_id, stop_price))


class _Ex:
    def __init__(self, positions, last=100.0):
        self._p = positions
        self._last = last
        self.placed = []

    def fetch_positions(self):
        return self._p

    def fetch_ticker(self, sym):
        return {"last": self._last}


def _pos(symbol, contracts, side="long", notional=5000.0):
    return {"symbol": symbol, "contracts": contracts, "notional": notional,
            "side": side, "entryPrice": 100.0}


def _trade(symbol, amount, side="long", stop_loss=95.0, sl_order_id=""):
    return {"id": f"t_{symbol}", "symbol": symbol, "amount": amount,
            "side": side, "entry_price": 100.0, "market_type": "futures",
            "sl_order_id": sl_order_id, "stop_loss": stop_loss,
            "opened_at": "2026-09-02T00:00:00+00:00"}


def _patch(monkeypatch, ex, placed):
    monkeypatch.setattr(reconcile.protective, "sweep_orphans",
                        lambda *a, **k: (0, 0))
    monkeypatch.setattr(reconcile.protective, "open_stops", lambda e: [])

    def _place(e, symbol, close_side, amount, stop_price):
        placed.append((symbol, close_side, amount, stop_price))
        return "algo_new_1"
    monkeypatch.setattr(reconcile.protective, "place_stop", _place)


def test_a_naked_position_gets_its_stop_back(monkeypatch):
    placed = []
    ex = _Ex([_pos("BTC/USDT", 0.5)], last=100.0)
    _patch(monkeypatch, ex, placed)
    j = _Journal([_trade("BTC/USDT", 0.5, stop_loss=95.0)])

    out = reconcile.reconcile_futures(ex, j)

    assert placed, ("a position with no protective order must be re-armed, "
                    "not merely reported")
    sym, side, amount, px = placed[0]
    assert side == "sell" and px == 95.0
    assert out.get("rearmed") == 1


def test_the_stop_is_sized_from_the_venue_not_the_journal(monkeypatch):
    """The partial-fill case that created the fault.

    303.87 was journalled; 1.0 actually remains. A stop for the journalled
    amount is refused, which is how the residue came to hold no protection.
    """
    placed = []
    ex = _Ex([_pos("UNI/USDT", 1.0)], last=100.0)
    _patch(monkeypatch, ex, placed)
    j = _Journal([_trade("UNI/USDT", 303.86680303, stop_loss=95.0)])

    reconcile.reconcile_futures(ex, j)

    assert placed, "the residue must be armed"
    assert placed[0][2] == 1.0, (
        f"stop sized {placed[0][2]} from the journal; the venue holds 1.0 — "
        "ask the venue, not the journal")


def test_a_stop_already_through_its_level_is_not_armed(monkeypatch):
    """Arming this answers -2021; the position needs closing, not a stop."""
    placed = []
    ex = _Ex([_pos("UNI/USDT", 1.0)], last=5.79)
    _patch(monkeypatch, ex, placed)
    j = _Journal([_trade("UNI/USDT", 1.0, stop_loss=6.1884)])

    out = reconcile.reconcile_futures(ex, j)

    assert not placed, ("a long stop above the market would trigger on "
                        "arrival — the venue refuses it with -2021")
    assert out.get("naked") == 1
    assert out.get("unarmable") == 1, (
        "this state must stay visible: it needs a close, which is not "
        "reconcile's to send")


def test_a_short_is_armed_on_the_correct_side(monkeypatch):
    placed = []
    ex = _Ex([_pos("ETH/USDT", 2.0, side="short")], last=100.0)
    _patch(monkeypatch, ex, placed)
    j = _Journal([_trade("ETH/USDT", 2.0, side="short", stop_loss=105.0)])

    reconcile.reconcile_futures(ex, j)

    assert placed, "a naked short must be re-armed too"
    assert placed[0][1] == "buy", "a short is closed by buying"
    assert placed[0][3] == 105.0


def test_a_covered_position_is_left_alone(monkeypatch):
    placed = []
    ex = _Ex([_pos("BTC/USDT", 0.5)], last=100.0)
    monkeypatch.setattr(reconcile.protective, "sweep_orphans",
                        lambda *a, **k: (0, 0))
    monkeypatch.setattr(reconcile.protective, "open_stops",
                        lambda e: [{"symbol": "BTCUSDT"}])
    monkeypatch.setattr(reconcile.protective, "place_stop",
                        lambda *a, **k: placed.append(a) or "x")
    j = _Journal([_trade("BTC/USDT", 0.5, sl_order_id="algo_1")])

    out = reconcile.reconcile_futures(ex, j)

    assert not placed, "never place a second stop on a protected position"
    assert out.get("naked") == 0
