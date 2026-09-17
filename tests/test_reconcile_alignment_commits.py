"""Boot-time alignment must be durable before reconcile goes to the network.

reconcile_futures() pulled a journalled amount back to the venue's through
`journal.query()`, which by the journal's own contract runs with no
transaction wrapper and never commits. The value did survive — but only
because the summary written at the very end of the function happens to go
through `_tx()` on the same thread and sweeps the pending UPDATE along with
it. Until that point the row sat uncommitted, invisible to the dashboard and
holding the write lock across a ticker fetch and the orphan-stop sweep: two
network round-trips with a write transaction open.

That is the hazard, not a hypothetical. This is the backstop under
`_detect_exchange_exits`: if the kernel dies between a venue fill and the
next cycle, boot is the only chance to notice a drifted position.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.engine.reconcile import reconcile_futures


class _Ex:
    def __init__(self, positions): self._p = positions
    def fetch_positions(self): return self._p
    def fetch_ticker(self, sym): return {"last": 6.187}


def _journal(tmp_path, amount=303.86680303):
    j = Journal(tmp_path / "j.db")
    j.add_trade(Position(
        id="pos_uni", symbol="UNI/USDT", side=Side.LONG, amount=amount,
        entry_price=5.8933, notional_usdt=round(amount * 5.8933, 2),
        stop_loss=6.1884, market_type="futures", exec_mode="live",
        strategy_id="s", strategy_name="ema_trend"))
    return j


def _fresh_read(path):
    """A second connection — what every other reader of the db would see."""
    import sqlite3
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    row = c.execute("SELECT * FROM trades WHERE id='pos_uni'").fetchone()
    c.close()
    return dict(row)


def test_the_aligned_amount_is_committed(tmp_path):
    j = _journal(tmp_path)
    ex = _Ex([{"symbol": "UNI/USDT:USDT", "contracts": 1.0,
               "side": "long", "entryPrice": 5.8933, "notional": 6.19}])
    r = reconcile_futures(ex, j)
    assert r["aligned"] == 1
    assert _fresh_read(tmp_path / "j.db")["amount"] == pytest.approx(1.0), \
        "the UPDATE never left the uncommitted transaction"


def test_the_notional_is_committed_with_it(tmp_path):
    j = _journal(tmp_path)
    ex = _Ex([{"symbol": "UNI/USDT:USDT", "contracts": 1.0,
               "side": "long", "entryPrice": 5.8933, "notional": 6.19}])
    reconcile_futures(ex, j)
    assert _fresh_read(tmp_path / "j.db")["notional_usdt"] == pytest.approx(6.19)


def test_the_alignment_is_durable_before_the_network_work(tmp_path):
    """The orphan-stop sweep is REST traffic. Nothing may hold a write
    transaction open across it, and the aligned row must already be visible
    to the dashboard by then."""
    import trader.engine.reconcile as rec
    seen = {}

    def _spy(ex, keep, live, protected):
        seen["amount"] = _fresh_read(tmp_path / "j.db")["amount"]
        seen["in_tx"] = j._conn().in_transaction
        return 0, 0

    j = _journal(tmp_path)
    ex = _Ex([{"symbol": "UNI/USDT:USDT", "contracts": 1.0,
               "side": "long", "entryPrice": 5.8933, "notional": 6.19}])
    old, rec.protective.sweep_orphans = rec.protective.sweep_orphans, _spy
    try:
        reconcile_futures(ex, j)
    finally:
        rec.protective.sweep_orphans = old
    assert seen["in_tx"] is False, "a write transaction is open across REST calls"
    assert seen["amount"] == pytest.approx(1.0)


def test_aligning_down_books_what_the_venue_already_closed(tmp_path):
    """A journal shrinking to meet the venue means the venue closed part of
    the line while we were down. That P&L belongs to the strategy — dropped,
    `ema_trend` reads worse than it traded and the decay gate mismeasures it.
    Priced at the mark, like the ghost path: the fill itself is gone."""
    j = _journal(tmp_path)
    ex = _Ex([{"symbol": "UNI/USDT:USDT", "contracts": 1.0,
               "side": "long", "entryPrice": 5.8933, "notional": 6.19}])
    reconcile_futures(ex, j)                      # fetch_ticker last = 6.187
    sold = 303.86680303 - 1.0
    assert _fresh_read(tmp_path / "j.db")["realized_pnl"] == pytest.approx(
        (6.187 - 5.8933) * sold, abs=0.01)


def test_aligning_up_books_nothing(tmp_path):
    """The venue holding MORE than the journal is an accounting error, not a
    realized gain. Adopt the size; invent no P&L."""
    j = _journal(tmp_path, amount=1.0)
    ex = _Ex([{"symbol": "UNI/USDT:USDT", "contracts": 303.0,
               "side": "long", "entryPrice": 5.8933, "notional": 1785.7}])
    reconcile_futures(ex, j)
    row = _fresh_read(tmp_path / "j.db")
    assert row["amount"] == pytest.approx(303.0)
    assert row["realized_pnl"] == pytest.approx(0.0)


def test_a_matching_position_is_left_alone(tmp_path):
    j = _journal(tmp_path, amount=303.0)
    ex = _Ex([{"symbol": "UNI/USDT:USDT", "contracts": 303.0,
               "side": "long", "entryPrice": 5.8933, "notional": 1785.7}])
    r = reconcile_futures(ex, j)
    assert r["aligned"] == 0
    assert _fresh_read(tmp_path / "j.db")["amount"] == pytest.approx(303.0)


# ── pricing an align-down ────────────────────────────────────────────────
class _ExWithFills(_Ex):
    """A venue that remembers its fills, as Binance does."""

    def __init__(self, positions, fills):
        super().__init__(positions)
        self._fills = fills
        self.since_asked = None

    def fetch_my_trades(self, symbol, since=None, limit=None):
        self.since_asked = since
        return self._fills


def _uni_fills():
    """The real 2026-09-02 UNI legs: entry, TP1, then the trailing stop."""
    return [
        {"id": "entry", "info": {"realizedPnl": "0", "commission": "1.4309", "commissionAsset": "USDT"}},
        {"id": "partial", "info": {"realizedPnl": "31.73912520", "commission": "0.7270", "commissionAsset": "USDT"}},
        {"id": "stop", "info": {"realizedPnl": "89.00612520", "commission": "0.7499", "commissionAsset": "USDT"}},
    ]


def test_an_align_down_is_priced_from_the_venues_fills(tmp_path):
    """The mark is whatever the price happens to be at boot, not what the
    exit filled at. On 2026-09-02 that gap was -2.51 booked against a true
    +117.84 — the sign was wrong, on the one trade of the session."""
    j = _journal(tmp_path)
    ex = _ExWithFills(
        [{"symbol": "UNI/USDT:USDT", "contracts": 1.0, "side": "long",
          "entryPrice": 5.8933, "notional": 5.88}],
        _uni_fills())
    ex.fetch_ticker = lambda s: {"last": 5.885}      # drifted back to entry
    reconcile_futures(ex, j)
    assert _fresh_read(tmp_path / "j.db")["realized_pnl"] == pytest.approx(
        120.74525040 - 2.9078, abs=0.01)


def test_the_venue_total_replaces_the_journals_guess(tmp_path):
    """Realized P&L is set to the venue's number, not added to whatever the
    journal had accumulated — otherwise the partial is counted twice."""
    j = _journal(tmp_path)
    j.align_trade_amount("pos_uni", 303.86680303,
                         round(303.86680303 * 5.8933, 2), pnl_delta=29.70)
    ex = _ExWithFills(
        [{"symbol": "UNI/USDT:USDT", "contracts": 1.0, "side": "long",
          "entryPrice": 5.8933, "notional": 5.88}],
        _uni_fills())
    reconcile_futures(ex, j)
    assert _fresh_read(tmp_path / "j.db")["realized_pnl"] == pytest.approx(
        117.8375, abs=0.01), "the TP1 partial was double-counted"


def test_only_this_trades_fills_are_asked_for(tmp_path):
    j = _journal(tmp_path)
    ex = _ExWithFills(
        [{"symbol": "UNI/USDT:USDT", "contracts": 1.0, "side": "long",
          "entryPrice": 5.8933, "notional": 5.88}],
        _uni_fills())
    reconcile_futures(ex, j)
    assert ex.since_asked is not None, \
        "without a since the sum spans every trade ever taken in the symbol"


def test_a_venue_that_cannot_answer_falls_back_to_the_mark(tmp_path):
    """Degrade, never crash — an unpriced align still beats a journal that
    claims 303 coins the venue does not hold."""
    j = _journal(tmp_path)

    class _Mute(_Ex):
        def fetch_my_trades(self, *a, **k): raise RuntimeError("no history")
    ex = _Mute([{"symbol": "UNI/USDT:USDT", "contracts": 1.0, "side": "long",
                 "entryPrice": 5.8933, "notional": 5.88}])
    reconcile_futures(ex, j)
    row = _fresh_read(tmp_path / "j.db")
    assert row["amount"] == pytest.approx(1.0)
    sold = 303.86680303 - 1.0
    assert row["realized_pnl"] == pytest.approx((6.187 - 5.8933) * sold, abs=0.01)
