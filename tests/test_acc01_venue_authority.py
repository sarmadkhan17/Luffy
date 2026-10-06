"""ACC-01: venue receipts stay authoritative; local estimates stay labelled; unknown stays unknown."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_reconcile as T
from trader.engine import booking
from trader.engine.reconcile import flatten_all, reconcile_futures, venue_realized_pnl


def receipts(j, kind=None):
    rows = [json.loads(r["payload"]) for r in
            j.query("SELECT payload FROM trade_accounting_bookings ORDER BY id")]
    return [r for r in rows if kind is None or r["kind"] == kind]


class NoQuote(T.FakeExchange):
    def fetch_ticker(self, sym):
        return {}


class DeadTicker(T.FakeExchange):
    def fetch_ticker(self, sym):
        raise RuntimeError("down")


def test_ghost_without_quote_is_unknown_not_total_loss(tmp_path):
    for n, ex in enumerate((NoQuote(), DeadTicker())):
        (tmp_path / str(n)).mkdir()
        j = T.make_journal(tmp_path / str(n), [T._pos(entry=100.0)])
        reconcile_futures(ex, j)
        row = j.query("SELECT * FROM trades WHERE status='closed'")[0]
        assert row["exit_price"] == 100.0 and row["realized_pnl"] == 0.0   # never a price of 0
        ev = receipts(j, "close:reconciled_ghost")[-1]["evidence"]
        assert ev["pnl_status"] == "UNKNOWN" and ev["estimated_pnl_usdt"] is None
        assert ev["exit_price_source"] == "journal_entry_not_a_fill"
        assert ev["exposure_source"] == "venue_position_snapshot"
        assert ev["exit_authority"] == "RECOVERY"


def test_ghost_with_quote_is_labelled_estimate_differing_from_venue(tmp_path):
    j = T.make_journal(tmp_path, [T._pos(entry=100.0)])
    reconcile_futures(T.FakeExchange(tickers={"BTC/USDT": 110.0}), j)
    rec = receipts(j, "close:reconciled_ghost")[-1]
    assert rec["evidence"]["pnl_status"] == "ESTIMATE"
    assert rec["evidence"]["exit_price_source"] == "ticker_last_not_a_fill"
    assert rec["evidence"]["pnl_value_class"] == "DERIVED_ESTIMATE"
    # an estimate can never be promoted to verified venue accounting
    assert rec["assessment"]["status"] == "unverified"
    assert rec["assessment"]["net_economic_pnl_usdt"] is None
    assert rec["assessment"]["learning_eligible"] is False


def test_panic_without_any_price_is_unknown(tmp_path):
    class NoFill(DeadTicker):
        def create_order(self, *a, **k):
            return {"id": "o1"}
    j = T.make_journal(tmp_path, [T._pos(entry=100.0)])
    assert flatten_all(NoFill(), j) == 1
    row = j.query("SELECT * FROM trades WHERE status='closed'")[0]
    assert row["exit_price"] == 100.0 and row["realized_pnl"] == 0.0
    ev = receipts(j, "close:panic")[-1]["evidence"]
    assert ev["pnl_status"] == "UNKNOWN" and ev["exit_price_source"] == "journal_entry_not_a_fill"
    assert ev["pnl_value_class"] == "UNKNOWN"
    assert ev["order_id"] == "o1"


def test_adoption_without_venue_entry_price_records_unknown(tmp_path):
    pos = T._ex_position("ETH/USDT", 2.0)
    pos.pop("entryPrice")
    pos.pop("notional")
    j = T.make_journal(tmp_path)
    reconcile_futures(T.FakeExchange(positions={"ETH/USDT": pos}), j)
    ev = receipts(j, "entry")[-1]["evidence"]
    assert ev["entry_price_status"] == "UNKNOWN" and ev["venue_entry_price"] is None
    assert ev["exposure_source"] == "venue_position_snapshot"
    assert float(j.open_trades()[0]["amount"]) == 2.0           # exposure is the venue's


def test_adoption_with_venue_price_records_venue_source(tmp_path):
    j = T.make_journal(tmp_path)
    reconcile_futures(T.FakeExchange(positions={"ETH/USDT": T._ex_position("ETH/USDT", 2.0, entry=50.0)}), j)
    ev = receipts(j, "entry")[-1]["evidence"]
    assert ev["entry_price_status"] == "VENUE_REPORTED" and ev["venue_entry_price"] == 50.0


def test_alignment_estimate_is_labelled_and_history_is_append_only(tmp_path):
    j = T.make_journal(tmp_path, [T._pos(amount=2.0, entry=100.0)])
    before = receipts(j)
    reconcile_futures(T.FakeExchange(positions={"BTC/USDT": T._ex_position("BTC/USDT", 1.0)},
                                     tickers={"BTC/USDT": 110.0}), j)
    after = receipts(j)
    assert after[: len(before)] == before                        # prior record untouched
    corr = after[-1]
    assert corr["kind"] == "align_delta"
    assert corr["before"]["amount"] == 2.0 and corr["after"]["amount"] == 1.0
    assert corr["evidence"]["pnl_status"] == "ESTIMATE"
    assert corr["evidence"]["pnl_value_class"] == "DERIVED_ESTIMATE"
    assert corr["evidence"]["pnl_price_source"] == "ticker_last_not_a_fill"
    assert corr["evidence"]["estimated_pnl_usdt"] == 10.0
    booking.replay(corr)                                          # integrity holds
    assert float(j.open_trades()[0]["amount"]) == 1.0            # exposure aligned to venue


def test_alignment_with_dead_quote_is_unknown(tmp_path):
    j = T.make_journal(tmp_path, [T._pos(amount=2.0, entry=100.0)])
    reconcile_futures(NoQuote(positions={"BTC/USDT": T._ex_position("BTC/USDT", 1.0)}), j)
    ev = receipts(j, "align_delta")[-1]["evidence"]
    assert ev["pnl_status"] == "UNKNOWN" and ev["pnl_price_source"] == "journal_entry_not_a_fill"


def test_unreadable_positions_leave_journal_and_report_unknown(tmp_path):
    class Dead(T.FakeExchange):
        def fetch_positions(self):
            raise RuntimeError("down")
    j = T.make_journal(tmp_path, [T._pos()])
    r = reconcile_futures(Dead(), j)
    assert r["positions_readable"] is False and r["ghosts"] == 0
    assert len(j.open_trades()) == 1                              # no ghost invented from silence


def test_journal_side_vs_venue_side_mismatch_is_a_safety_issue(tmp_path):
    j = T.make_journal(tmp_path, [T._pos()])
    r = reconcile_futures(T.FakeExchange(positions={"BTC/USDT": T._ex_position("BTC/USDT", 1.0, side="short")}),
                          j, verify=True)
    assert "contradictory_position_side:BTC/USDT" in r["safety_issues"]
    assert r["aligned"] == r["ghosts"] == r["adopted"] == 0       # nothing silently rewritten


def test_missing_and_failed_receipt_history_is_not_a_number(tmp_path):
    class NoHistory(T.FakeExchange):
        def fetch_my_trades(self, *a, **k):
            raise RuntimeError("down")
    obs = {}
    assert venue_realized_pnl(NoHistory(), "BTC/USDT", "2026-01-01T00:00:00+00:00", obs) is None
    assert obs["reason"].startswith("fill_history_unavailable") and obs["learning_eligible"] is False

    class Empty(T.FakeExchange):
        def fetch_my_trades(self, *a, **k):
            return []
    obs = {}
    assert venue_realized_pnl(Empty(), "BTC/USDT", "2026-01-01T00:00:00+00:00", obs) is None


def _evidence(**over):
    fill = {"id": "f1", "order": "o1", "symbol": "BTC/USDT", "side": "sell", "amount": 1.0,
            "price": 100.0, "timestamp": 1000, "realized_pnl": 5.0, "commission": 0.1,
            "commission_asset": "USDT"}
    ev = {"basis": "venue_order_fills", "order_id": "o1", "symbol": "BTC/USDT", "side": "sell",
          "quantity": 1.0, "since_ms": 500, "observed_ms": 2000, "fills": [fill]}
    ev.update(over)
    return ev


def test_stale_wrong_instrument_and_wrong_currency_receipts_are_not_verified():
    assert booking.assess(_evidence())["status"] == "verified_leg_fills_only"
    stale = _evidence(since_ms=1500)                                # fill predates the booking window
    assert booking.assess(stale)["status"] == "unverified"
    other = _evidence()
    other["fills"][0]["symbol"] = "ETH/USDT"                         # another instrument's fill
    assert booking.assess(other)["status"] == "unverified"
    bound = _evidence(execution_binding={"capability": {"record": {"instrument_id": {"venue_symbol": "BTCUSDT"}}}})
    bound["fills"][0]["venue_symbol"] = "BTCUSDC"                    # same raw text, other canonical instrument
    assert booking.assess(bound)["status"] == "unverified"
    fee = _evidence()
    fee["fills"][0]["commission_asset"] = "BNB"
    a = booking.assess(fee)
    assert a["status"] == "unverified" and a["leg_fill_net_excluding_funding_usdt"] is None


def test_local_estimate_cannot_be_replayed_as_venue_actual():
    receipt = {"schema_version": "trade-booking.v1", "trade_id": "t", "kind": "close:x",
               "observed_ms": 1, "before": None, "after": {"id": "t"},
               "evidence": {"basis": "reconcile_ghost_mark_estimate"},
               "assessment": {"status": "verified_leg_fills_only"}}
    receipt["sha256"] = booking.digest({k: v for k, v in receipt.items()})
    try:
        booking.replay(receipt)
    except ValueError as e:
        assert str(e) == "booking_assessment_mismatch"
    else:
        raise AssertionError("rewritten assessment accepted")
