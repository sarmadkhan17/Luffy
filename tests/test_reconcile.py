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


def test_flatten_closes_then_cancels_sl(tmp_path):
    ex = FakeExchange(tickers={"BTC/USDT": 105.0})
    j = make_journal(tmp_path, [_pos(sl=98.0)])
    n = flatten_all(ex, j)
    assert n == 1 and n.complete and not j.open_trades()
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


class LiveBook(FakeExchange):
    """Stateful fake: create_order changes the venue position like a venue."""

    def __init__(self, sizes, fills=None, readable=True):
        super().__init__(tickers={k: 100.0 for k in sizes})
        self.sizes = dict(sizes)
        self.script = list(fills or [])      # per create_order: 'fill'|'raise'|'raise_filled'|'partial'
        self.readable = readable
        self.events = []

    def fetch_positions(self):
        if not self.readable:
            raise RuntimeError("positions unavailable")
        return [_ex_position(s, n) for s, n in self.sizes.items() if n > 0]

    def cancel_order(self, oid, sym):
        self.events.append(("cancel", oid))
        super().cancel_order(oid, sym)

    def create_order(self, sym, type_, side, amount, params=None):
        self.events.append(("order", amount))
        step = self.script.pop(0) if self.script else "fill"
        if step == "raise":
            raise RuntimeError("rejected")
        if step == "raise_filled":                # timeout after the venue filled
            self.sizes[sym] = 0.0
            raise TimeoutError("ack lost")
        if step == "partial":
            self.sizes[sym] = self.sizes[sym] / 2
        else:
            self.sizes[sym] = 0.0
        return super().create_order(sym, type_, side, amount, params)


def test_flatten_refused_close_keeps_stop_and_reports_remaining(tmp_path):
    ex = LiveBook({"A/USDT": 1.0}, fills=["raise"] * 5)
    j = make_journal(tmp_path, [_pos("A/USDT", sl_order_id="sl_A")])
    n = flatten_all(ex, j)
    assert n == 0 and not n.complete and n.remaining == ("A/USDT",)
    assert len(j.open_trades()) == 1
    assert ex.cancelled == [], "position still open: its venue stop must stand"
    ev = j.query("SELECT * FROM control_events WHERE event='panic_flatten_incomplete'")
    assert len(ev) == 1


def test_flatten_stop_is_released_only_after_confirmed_flat(tmp_path):
    ex = LiveBook({"A/USDT": 1.0})
    j = make_journal(tmp_path, [_pos("A/USDT", sl_order_id="sl_A")])
    n = flatten_all(ex, j)
    assert n == 1 and n.complete and not j.open_trades()
    kinds = [e[0] for e in ex.events]
    assert kinds == ["order", "cancel"]


def test_flatten_retries_a_partial_close_with_the_venue_remainder(tmp_path):
    ex = LiveBook({"A/USDT": 1.0}, fills=["partial", "fill"])
    j = make_journal(tmp_path, [_pos("A/USDT", sl_order_id="sl_A")])
    n = flatten_all(ex, j)
    assert n.complete and n == 1
    sizes = [e[1] for e in ex.events if e[0] == "order"]
    assert sizes == [1.0, 0.5]                 # second order sized from the venue
    assert ex.events.index(("cancel", "sl_A")) > max(
        i for i, e in enumerate(ex.events) if e[0] == "order")


def test_flatten_persistent_partial_leaves_stop_and_is_incomplete(tmp_path):
    ex = LiveBook({"A/USDT": 1.0}, fills=["partial"] * 5)
    j = make_journal(tmp_path, [_pos("A/USDT", sl_order_id="sl_A")])
    n = flatten_all(ex, j)
    assert not n.complete and n.remaining == ("A/USDT",)
    assert ex.cancelled == [] and len(j.open_trades()) == 1


def test_flatten_ambiguous_timeout_that_filled_is_not_resent(tmp_path):
    ex = LiveBook({"A/USDT": 1.0}, fills=["raise_filled"])
    j = make_journal(tmp_path, [_pos("A/USDT", sl_order_id="sl_A")])
    n = flatten_all(ex, j)
    assert sum(1 for e in ex.events if e[0] == "order") == 1
    assert n.complete                           # venue is flat: no exposure
    # no order response, so nothing is fabricated: reconcile books the close
    assert len(j.open_trades()) == 1


def test_flatten_unreadable_venue_never_assumes_success(tmp_path):
    ex = LiveBook({"A/USDT": 1.0}, readable=False)
    j = make_journal(tmp_path, [_pos("A/USDT", sl_order_id="sl_A")])
    n = flatten_all(ex, j)
    assert sum(1 for e in ex.events if e[0] == "order") == 1   # not resent blind
    assert not n.complete and ex.cancelled == [] and len(j.open_trades()) == 1


def test_flatten_reports_but_never_closes_unjournaled_venue_positions(tmp_path):
    ex = LiveBook({"A/USDT": 1.0, "ORPHAN/USDT": 3.0})
    j = make_journal(tmp_path, [_pos("A/USDT", sl_order_id="sl_A")])
    n = flatten_all(ex, j)
    assert n == 1 and not n.complete and n.unjournaled == ("ORPHAN/USDT",)
    assert ex.sizes["ORPHAN/USDT"] == 3.0


class _StopVenue(FakeExchange):
    """Venue with one UNDERSIZED stop on a 1.0 long (inadequate coverage)."""

    def fetch_open_orders(self, sym=None):
        return []

    def fapiPrivateGetOpenAlgoOrders(self):
        return [{"algoId": "99", "symbol": "BTCUSDT", "side": "SELL", "quantity": "0.4",
                 "triggerPrice": "98", "reduceOnly": "true", "orderType": "STOP_MARKET"}]


def test_p4_rearm_criterion_is_identical_in_verify_and_non_verify(tmp_path):
    """Characterisation (P-4, deferred): re-arm treats ANY stop on a symbol as
    cover in both modes — they agree. Verify additionally *reports* an
    inadequate stop (protection_match); placing a second stop against it would
    change protection behaviour and is left to the owner."""
    outcomes = {}
    for verify in (False, True):
        ex = _StopVenue(positions={"BTC/USDT": _ex_position("BTC/USDT", 1.0)})
        j = make_journal(tmp_path / str(verify), [_pos(sl_order_id="99")]) \
            if (tmp_path / str(verify)).mkdir() is None else None
        r = reconcile_futures(ex, j, verify=verify)
        outcomes[verify] = (len(ex.orders), r.get("naked"), r.get("rearmed"), r)
    assert outcomes[False][:3] == outcomes[True][:3]      # same placement decision
    assert outcomes[True][0] == 0                          # no new stop either way
    assert any(i.startswith("position_unprotected") or i == "protection_cannot_restore"
               for i in outcomes[True][3]["safety_issues"])
