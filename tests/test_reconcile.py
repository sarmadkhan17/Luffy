"""Reconciliation + flatten tests against a scripted fake exchange."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.engine.reconcile import flatten_all, reconcile_futures


class FakeExchange:
    def __init__(self, positions=None, tickers=None):
        self._positions = positions or {}
        self.tickers = tickers or {}
        self.orders = []
        self.cancelled = []

    def fetch_positions(self):
        return list(self._positions.values())

    def fetch_ticker(self, sym):
        return {"last": self.tickers.get(sym, 100.0)}

    def cancel_order(self, oid, sym):
        self.cancelled.append((oid, sym))

    def create_order(self, sym, type_, side, amount, params=None):
        self.orders.append((sym, side, amount, params))
        return {"id": f"o{len(self.orders)}", "average": self.tickers.get(sym, 100.0)}


def make_journal(tmp_path, open_trades=()):
    j = Journal(tmp_path / "j.db")
    for t in open_trades:
        j.add_trade(t)
    return j


def _pos(symbol="BTC/USDT", amount=1.0, entry=100.0, sl=98.0, sl_order_id="sl_001"):
    p = Position(id=f"x_{symbol}", symbol=symbol, side=Side.LONG,
                 amount=amount, entry_price=entry, notional_usdt=amount * entry,
                 leverage=5, stop_loss=sl, sl_order_id=sl_order_id, market_type="futures")
    return p


def _ex_position(sym, contracts, side="long", entry=100.0):
    return {"symbol": sym, "contracts": contracts, "side": side,
            "entryPrice": entry, "notional": contracts * entry, "leverage": 5}


def test_adopts_orphan(tmp_path):
    ex = FakeExchange(positions={"ETH/USDT": _ex_position("ETH/USDT", 2.0)})
    j = make_journal(tmp_path)
    r = reconcile_futures(ex, j)
    assert r["adopted"] == 1
    opens = j.open_trades()
    assert len(opens) == 1 and opens[0]["strategy_id"] == "adopted"


def test_closes_ghost_at_market(tmp_path):
    ex = FakeExchange(tickers={"BTC/USDT": 110.0})
    j = make_journal(tmp_path, [_pos(entry=100.0)])
    r = reconcile_futures(ex, j)
    assert r["ghosts"] == 1 and not j.open_trades()
    closed = j.query("SELECT * FROM trades WHERE status='closed'")[0]
    # long from 100 → closed at 110 → pnl = +10 * amount(=notional/entry... )
    assert closed["close_reason"] == "reconciled_ghost"
    assert closed["exit_price"] == 110.0
    assert closed["realized_pnl"] > 0


def test_aligns_drifted_amount(tmp_path):
    ex = FakeExchange(positions={"BTC/USDT": _ex_position("BTC/USDT", 1.05)})
    j = make_journal(tmp_path, [_pos(amount=1.0)])
    r = reconcile_futures(ex, j)
    assert r["aligned"] == 1
    assert float(j.open_trades()[0]["amount"]) == 1.05


def test_flatten_cancels_sl_then_closes(tmp_path):
    ex = FakeExchange(tickers={"BTC/USDT": 105.0})
    j = make_journal(tmp_path, [_pos(sl=98.0)])
    n = flatten_all(ex, j)
    assert n == 1 and not j.open_trades()
    assert ex.cancelled and ex.orders[0][1] == "sell"      # long → sell reduceOnly
    assert ex.orders[0][3] == {"reduceOnly": True}
    row = j.query("SELECT * FROM trades WHERE status='closed'")[0]
    assert row["close_reason"] == "panic"


def test_flatten_survives_single_failure(tmp_path, monkeypatch):
    ex = FakeExchange(tickers={"A/USDT": 100.0, "B/USDT": 100.0})
    calls = {"n": 0}
    real_create = ex.create_order

    def flaky(sym, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("exchange hiccup")
        return real_create(sym, *a, **k)

    monkeypatch.setattr(ex, "create_order", flaky)
    j = make_journal(tmp_path, [_pos("A/USDT"), _pos("B/USDT")])
    n = flatten_all(ex, j)
    assert n == 1                       # B closed despite A failing
    assert len(j.open_trades()) == 1    # A still open → retried next cycle
