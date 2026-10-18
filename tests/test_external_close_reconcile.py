"""Externally closed futures trades are booked from the venue's own fills."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.engine.reconcile import (_funding_settlements, external_close_attribution,
                                     reconcile_external_closes, reconcile_futures)

T0 = int(datetime(2026, 9, 18, 16, 0, 54, tzinfo=timezone.utc).timestamp() * 1000)
HOUR = 3_600_000


def fill(i, order, side, amount, price, ts, rp, fee, asset="USDT", symbol="SOL/USDT"):
    return {"id": str(i), "order": str(order), "symbol": symbol, "side": side, "amount": amount,
            "price": price, "timestamp": ts,
            "info": {"realizedPnl": str(rp), "commission": str(fee), "commissionAsset": asset,
                     "symbol": symbol.replace("/", ""), "orderId": str(order)}}


class Venue:
    id = "binanceusdm"

    def __init__(self, fills, funding=(), positions=(), open_orders=()):
        self.fills, self.funding = list(fills), list(funding)
        self.positions, self.orders = list(positions), list(open_orders)
        self.writes = []

    def fetch_positions(self):
        return list(self.positions)

    def fetch_open_orders(self, symbol=None):
        return list(self.orders)

    def fetch_my_trades(self, symbol, since=None, limit=None, params=None):
        until = (params or {}).get("until", 1 << 62)
        return [f for f in self.fills if since <= f["timestamp"] <= until][:limit]

    def fetch_funding_history(self, symbol, since=None, limit=None, params=None):
        until = (params or {}).get("until", 1 << 62)
        return [f for f in self.funding if since <= f["timestamp"] <= until][:limit]

    def fetch_ticker(self, sym):
        raise AssertionError("a mark price must never price an attributed close")

    def create_order(self, *a, **k):
        self.writes.append(("create", a)); raise AssertionError("no orders")

    def cancel_order(self, *a, **k):
        self.writes.append(("cancel", a)); raise AssertionError("no cancels")


def journal_with_open(tmp_path, amount=5.77, entry_order="E1", symbol="SOL/USDT", tid="pos_a"):
    j = Journal(tmp_path / "j.db")
    p = Position(id=tid, symbol=symbol, side=Side.LONG, amount=amount, entry_price=111.35,
                 notional_usdt=amount * 111.35, leverage=5, stop_loss=100.0, sl_order_id="",
                 market_type="futures",
                 opened_at=datetime.fromtimestamp((T0 + 2000) / 1000, timezone.utc).isoformat())
    j.add_trade(p, accounting={"basis": "entry_order_confirmation", "order_id": entry_order,
                               "confirmed_price": 111.35, "confirmed_quantity": amount})
    return j


def entry_and_exit(exit_parts=((5.77, 116.15, 27.696, 0.268),)):
    out = [fill(1, "E1", "buy", 5.77, 111.35, T0, 0, 0.257)]
    for n, (q, px, rp, fee) in enumerate(exit_parts):
        out.append(fill(10 + n, "X1", "sell", q, px, T0 + 19 * 24 * HOUR, rp, fee))
    return out


def funding_rows(n, amount=-0.5):
    first = (T0 // (8 * HOUR) + 1) * 8 * HOUR
    return [{"id": f"f{k}", "timestamp": first + k * 8 * HOUR, "amount": amount,
             "info": {"incomeType": "FUNDING_FEE", "asset": "USDT"}} for k in range(n)]


def venue_for(fills, **kw):
    ex = Venue(fills, **kw)
    ex.funding = kw.get("funding") or funding_rows(_funding_settlements(T0, T0 + 19 * 24 * HOUR))
    return ex


def test_books_exact_fill_pnl_fees_and_funding_separately(tmp_path):
    j = journal_with_open(tmp_path)
    ex = venue_for(entry_and_exit())
    report = reconcile_external_closes(ex, j, apply=True)
    assert [r["status"] for r in report] == ["CLOSED"]
    t = j.query("SELECT * FROM trades WHERE id='pos_a'")[0]
    assert t["status"] == "closed" and t["close_reason"] == "external_close"
    assert t["realized_pnl"] == pytest.approx(27.696 - 0.257 - 0.268)
    assert t["exit_price"] == pytest.approx(116.15)
    receipt = json.loads(j.query("SELECT payload FROM trade_accounting_bookings WHERE trade_id='pos_a' "
                                 "ORDER BY id DESC")[0]["payload"])
    assert receipt["assessment"]["status"] == "verified_leg_fills_only"
    assert receipt["assessment"]["learning_eligible"] is False
    funding = receipt["evidence"]["funding"]
    assert funding["status"] == "COMPLETE" and funding["funding_usdt"] == pytest.approx(-0.5 * len(funding["rows"]))
    assert t["realized_pnl"] != pytest.approx(27.696 - 0.525 + funding["funding_usdt"])  # funding not folded in
    assert ex.writes == []


def test_split_exit_fills_sum_to_one_order(tmp_path):
    j = journal_with_open(tmp_path)
    parts = ((3.0, 116.15, 14.4, 0.14), (2.77, 116.15, 13.296, 0.128))
    reconcile_external_closes(venue_for(entry_and_exit(parts)), j, apply=True)
    t = j.query("SELECT * FROM trades WHERE id='pos_a'")[0]
    assert t["realized_pnl"] == pytest.approx(27.696 - 0.257 - 0.268)
    rows = j.query("SELECT venue_order_id, attribution FROM trade_fills ORDER BY venue_fill_id")
    assert [(r["venue_order_id"], r["attribution"]) for r in rows] == [("X1", "UNATTRIBUTED")] * 2


def test_duplicate_reconciliation_is_a_no_op(tmp_path):
    j = journal_with_open(tmp_path)
    ex = venue_for(entry_and_exit())
    reconcile_external_closes(ex, j, apply=True)
    before = j.query("SELECT count(*) n FROM trade_accounting_bookings")[0]["n"]
    assert reconcile_external_closes(ex, j, apply=True) == []     # nothing open any more
    assert j.query("SELECT count(*) n FROM trade_accounting_bookings")[0]["n"] == before
    assert j.query("SELECT realized_pnl FROM trades WHERE id='pos_a'")[0]["realized_pnl"] == pytest.approx(27.171)


def test_dry_run_writes_nothing(tmp_path):
    j = journal_with_open(tmp_path)
    report = reconcile_external_closes(venue_for(entry_and_exit()), j)
    assert report[0]["status"] == "WOULD_CLOSE"
    assert j.query("SELECT status FROM trades WHERE id='pos_a'")[0]["status"] == "open"


@pytest.mark.parametrize("mutate,reason", [
    (lambda f: [x for x in f if x["order"] != "X1"], "position_not_closed_exactly_by_fills"),
    (lambda f: f[:1] + [fill(10, "X1", "sell", 2.0, 116, T0 + HOUR, 1, .1)], "position_not_closed_exactly_by_fills"),
    (lambda f: f[:1] + [fill(9, "ADD", "buy", 1.0, 112, T0 + HOUR, 0, .1)] + f[1:], "additional_same_side_fill_after_entry"),
    (lambda f: f[:1] + [fill(10, "X1", "sell", 3.0, 116, T0 + HOUR, 1, .1), fill(11, "X2", "sell", 2.77, 116, T0 + 2 * HOUR, 1, .1)],
     "multiple_exit_orders_unsupported"),
    (lambda f: [fill(1, "E1", "buy", 5.0, 111.35, T0, 0, .2)] + f[1:], "entry_fills_do_not_match_journal"),
    (lambda f: f[:1] + [fill(10, "X1", "sell", 5.77, 116.15, T0 + HOUR, 27.696, .268, asset="BNB")], "missing_or_invalid_pnl_fee_currency"),
])
def test_ambiguous_or_incomplete_history_is_refused(tmp_path, mutate, reason):
    j = journal_with_open(tmp_path)
    ex = venue_for(mutate(entry_and_exit()))
    t = j.open_trades()[0]
    assert external_close_attribution(ex, j, t) == (None, reason)
    assert reconcile_external_closes(ex, j, apply=True)[0]["status"] == "SKIPPED"
    assert j.query("SELECT status, realized_pnl FROM trades")[0]["status"] == "open"


def test_missing_entry_receipt_order_is_refused(tmp_path):
    j = journal_with_open(tmp_path, entry_order="")
    assert external_close_attribution(venue_for(entry_and_exit()), j, j.open_trades()[0]) == (
        None, "entry_order_id_not_recorded")


def test_overlapping_journal_trade_on_symbol_is_refused(tmp_path):
    j = journal_with_open(tmp_path)
    other = Position(id="pos_b", symbol="SOL/USDT", side=Side.LONG, amount=1, entry_price=100,
                     notional_usdt=100, leverage=1, stop_loss=90, sl_order_id="", market_type="futures",
                     opened_at=datetime.fromtimestamp((T0 + 5 * HOUR) / 1000, timezone.utc).isoformat())
    j.add_trade(other)
    assert external_close_attribution(venue_for(entry_and_exit()), j, j.open_trades()[0])[1] == \
        "overlapping_journal_trade_on_symbol"


def test_truncated_history_page_is_refused(tmp_path):
    j = journal_with_open(tmp_path)
    ex = venue_for(entry_and_exit())
    ex.fetch_my_trades = lambda symbol, since=None, limit=None, params=None: [fills_dummy] * 1000
    fills_dummy = fill(1, "E1", "buy", 5.77, 111.35, T0, 0, .2)
    assert external_close_attribution(ex, j, j.open_trades()[0])[1] == "fill_history_unavailable_or_truncated"


def test_incomplete_funding_is_unknown_not_zero(tmp_path):
    j = journal_with_open(tmp_path)
    ex = venue_for(entry_and_exit(), funding=funding_rows(3))
    a, _ = external_close_attribution(ex, j, j.open_trades()[0])
    assert a["evidence"]["funding"]["status"] == "UNKNOWN" and a["evidence"]["funding"]["funding_usdt"] is None


def test_venue_exposure_blocks_the_close(tmp_path):
    j = journal_with_open(tmp_path)
    held = [{"symbol": "SOL/USDT", "contracts": 1.0, "side": "long"}]
    assert reconcile_external_closes(venue_for(entry_and_exit(), positions=held), j, apply=True)[0]["reason"] == \
        "venue_position_present"
    assert reconcile_external_closes(venue_for(entry_and_exit(), open_orders=[{"id": "1"}]), j, apply=True)[0]["reason"] == \
        "venue_orders_or_stops_present"
    assert j.open_trades()


def test_other_open_positions_are_untouched(tmp_path):
    j = journal_with_open(tmp_path)
    p = Position(id="pos_eth", symbol="ETH/USDT", side=Side.LONG, amount=1, entry_price=100, notional_usdt=100,
                 leverage=1, stop_loss=90, sl_order_id="", market_type="futures")
    j.add_trade(p)
    ex = venue_for(entry_and_exit())
    reconcile_external_closes(ex, j, apply=True, trade_ids={"pos_a"})
    assert [t["id"] for t in j.open_trades()] == ["pos_eth"]


def test_boot_reconcile_prefers_venue_fills_over_a_mark(tmp_path):
    j = journal_with_open(tmp_path)
    ex = venue_for(entry_and_exit())
    ex.fapiPrivateGetOpenAlgoOrders = lambda: []
    result = reconcile_futures(ex, j)         # fetch_ticker would raise if a mark were used
    assert result["ghosts"] == 1
    t = j.query("SELECT * FROM trades WHERE id='pos_a'")[0]
    assert t["close_reason"] == "external_close" and t["realized_pnl"] == pytest.approx(27.171)


def test_funding_settlement_count():
    start = int(datetime(2026, 9, 18, 16, 0, 54, tzinfo=timezone.utc).timestamp() * 1000)
    end = int(datetime(2026, 9, 19, 16, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert _funding_settlements(start, end) == 3        # 00:00, 08:00, 16:00 on the 19th
