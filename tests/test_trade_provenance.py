"""LUFFY-TRADE-PROVENANCE-R1: strategy version → decision → order → fill →
booking, joined only by recorded identifiers; gaps stay explicit."""
import copy
import json
import sqlite3
import time
from pathlib import Path

import pytest
from ccxt import RequestTimeout

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import Action, Decision, MarketType, Position, Side
from trader.dashboard.owner_reads import trade_lineage
from trader.engine import trade_provenance as TP
from trader.engine.executor import Executor
from trader.engine.reconcile import reconcile_futures
from trader.strategy.compile import compile_spec
from tests.test_spec import _valid

SYM = "BTC/USDT"


class Venue:
    """Market orders fill immediately at `px`, split into `split` fills; every
    fill is kept for fetch_my_trades. Stops are acknowledged, never filled."""

    def __init__(self):
        self.px = 100.0
        self.split = 1
        self.fills = []
        self.sent = []
        self.orders = {}
        self.n = 0
        self.commission = "0.04"
        self.order_id_missing = False
        self.oid_format = "{}"
        self.fill_format = "{oid}-{k}"

    def amount_to_precision(self, symbol, amount):
        return str(float(amount))

    def create_order(self, symbol, typ, side, amount, params=None):
        params = dict(params or {})
        self.sent.append((symbol, typ, side, amount, params))
        if "stopLossPrice" in params:
            return {"id": "9001"}
        self.n += 1
        oid = None if self.order_id_missing else self.oid_format.format(1000 + self.n)
        now = int(time.time() * 1000)
        per = amount / self.split
        for k in range(self.split):
            self.fills.append({
                "id": self.fill_format.format(oid=oid, k=k), "order": oid, "symbol": symbol, "side": side,
                "amount": per, "price": self.px, "timestamp": now,
                "info": {"commission": self.commission, "commissionAsset": "USDT",
                         "realizedPnl": "0" if not params.get("reduceOnly") else "1.5"}})
        order = {"id": oid, "status": "closed", "average": self.px, "filled": amount}
        if oid:
            self.orders[oid] = order
            if params.get("newClientOrderId"):
                self.orders[params["newClientOrderId"]] = order
        return order

    def fetch_order(self, oid, symbol, params=None):
        return self.orders[oid or params["origClientOrderId"]]

    def fetch_my_trades(self, symbol, since=None, limit=None):
        return [f for f in self.fills if f["timestamp"] >= (since or 0)]

    def cancel_order(self, oid, symbol):
        return {}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.engine.executor.time.sleep", lambda _: None)
    j = Journal(tmp_path / "journal.db")
    from tests.authority_legacy_fixtures import grant
    j.upsert_spec(_valid())
    grant(j, "seed_test")  # TEST-ONLY exact historical owner authority
    ex = Venue()
    e = Executor(ex, j, load_config(), MarketType.FUTURES)
    e.fill_retry_s = 0
    return ex, j, e


def decision(j, did="d1", symbol=SYM, action=Action.BUY):
    d = Decision(did, "c" + did, symbol, action, 1.0, .5, .8, [], [])
    with j._tx() as db:
        db.execute("INSERT OR IGNORE INTO cycles(id,ts,symbol) VALUES (?,?,?)",
                   ("c" + did, "2026-09-30T00:00:00+00:00", symbol))
    j.log_decision(d)
    return d


def spec_identity(d, spec=None, stamped=True):
    spec = spec or _valid()
    compiled = compile_spec(spec)
    loaded = TP.population_identity(spec.id, kind="spec", family=f"spec:{spec.id}",
                                    name=spec.name, state="paper", generation=spec.generation,
                                    parent_id=spec.parent_id, loaded_at="2026-09-30T00:00:00+00:00",
                                    spec=spec)
    signal = {"strategy_id": spec.id, "action": "BUY",
              "params": {"spec_id": spec.id,
                         "spec_sha256": compiled.spec_sha256 if stamped else None}}
    return TP.entry_identity(spec.id, loaded, d, signal, "2026-09-30T00:00:01+00:00"), compiled


FUT, SPOT = {"market_type": "futures"}, {"market_type": "spot"}
REF = {"price": 99.5, "basis": "decision_snapshot_price", "snapshot_at": "t0", "bar_ts": "b0"}


def open_trade(env, did="d1", identity=None, reference=REF):
    ex, j, e = env
    d = decision(j, did)
    if identity is None:
        identity, _ = spec_identity(d)
    pos = e.open(d, 2.0, 1.0, 95.0, 110.0, "seed_test", "Funding Exhaustion Fade",
                 entry_identity=identity, reference=reference)
    assert pos is not None
    return pos


def row(j, tid):
    return j.query("SELECT * FROM trades WHERE id=?", (tid,))[0]


def prov(j, tid):
    return TP.read(j.query, tid)


def fills(j, **where):
    q = "SELECT * FROM trade_fills"
    if where:
        q += " WHERE " + " AND ".join(f"{k}=?" for k in where)
    return j.query(q + " ORDER BY id", tuple(where.values()))


# ── strategy identity ─────────────────────────────────────────────────────────
def test_entry_binds_exact_strategy_version(env):
    ex, j, e = env
    pos = open_trade(env)
    identity = json.loads(row(j, pos.id)["entry_identity_json"])
    spec = compile_spec(_valid()).spec
    assert identity["status"] == "VERIFIED"
    assert identity["spec_sha256"] == TP.sha256(json.loads(json.dumps(spec.to_dict())))
    assert identity["spec"]["entry_short"] == spec.entry_short
    assert identity["decision"]["decision_id"] == "d1"
    assert identity["generation"] == 0 and identity["evaluator_family"] == "spec:seed_test"
    assert prov(j, pos.id)["strategy_entry_identity"]["status"] == "VERIFIED"


def test_compiled_evaluator_stamps_the_hash_of_what_it_runs():
    spec = _valid()
    compiled = compile_spec(spec)
    assert compiled.spec_sha256 == TP.spec_version(spec)["spec_sha256"]
    mutated = copy.deepcopy(spec)
    mutated.filters = ["adx(14) < 30"]
    assert compile_spec(mutated).spec_sha256 != compiled.spec_sha256


def test_later_strategy_mutation_does_not_change_entry_identity(env):
    ex, j, e = env
    pos = open_trade(env)
    before = row(j, pos.id)["entry_identity_json"]
    spec = _valid()
    spec.filters = ["adx(14) < 40"]
    spec.generation = 7
    j.upsert_spec(spec, state="active")
    assert row(j, pos.id)["entry_identity_json"] == before
    with pytest.raises(sqlite3.IntegrityError, match="entry_identity_immutable"):
        with j._tx() as db:
            db.execute("UPDATE trades SET entry_identity_json='{}' WHERE id=?", (pos.id,))
    assert row(j, pos.id)["entry_identity_json"] == before


def test_signal_hash_disagreeing_with_loaded_spec_is_not_verified(env):
    ex, j, e = env
    d = decision(j)
    other = _valid()
    other.filters = ["adx(14) < 30"]
    loaded = TP.population_identity(other.id, kind="spec", family="spec:x", name=other.name,
                                    state="paper", generation=0, parent_id="", loaded_at="t",
                                    spec=other)
    signal = {"params": {"spec_sha256": compile_spec(_valid()).spec_sha256}}
    ident = TP.entry_identity(other.id, loaded, d, signal, "t")
    assert ident["status"] == "AMBIGUOUS" and ident["spec"] is None
    unstamped, _ = spec_identity(d, stamped=False)
    assert unstamped["status"] == "UNKNOWN"
    assert TP.entry_identity("orchestrator", None, d, None, "t")["status"] == "UNKNOWN"


def test_historical_trade_without_version_stays_unknown_and_cannot_be_backfilled(env):
    ex, j, e = env
    j.add_trade(Position(id="old", symbol=SYM, side=Side.LONG, amount=1, entry_price=100,
                         notional_usdt=100, strategy_id="seed_test"))
    j.upsert_spec(_valid(), state="active")            # a current spec exists
    p = prov(j, "old")
    assert p["strategy_entry_identity"]["status"] == "UNKNOWN"
    assert "strategy_version_at_entry" in p["missing"]
    with pytest.raises(sqlite3.IntegrityError):
        with j._tx() as db:
            db.execute("UPDATE trades SET entry_identity_json=? WHERE id='old'",
                       (json.dumps({"spec": "latest"}),))
    from scripts.backfill_trade_provenance import backfill
    backfill(j, apply=True)
    assert row(j, "old")["entry_identity_json"] is None
    lineage = trade_lineage(j, "old")
    assert any(u["field"] == "strategy_version_at_entry" for u in lineage["unavailable"])


# ── order identity, fills, exits ──────────────────────────────────────────────
def test_entry_order_identity_and_reference_recorded(env):
    ex, j, e = env
    pos = open_trade(env)
    legs = prov(j, pos.id)["legs"]
    assert len(legs) == 1
    leg = legs[0]
    assert leg["purpose"] == "entry" and leg["order"]["status"] == "VERIFIED"
    assert leg["order"]["venue_order_id"] == "1001"
    assert leg["order"]["client_order_id"].startswith("lr_")
    assert leg["reference"]["price"] == 99.5 and leg["reference"]["submitted_ms"] > 0
    assert prov(j, pos.id)["slippage_basis"]["basis"] == "decision_snapshot_price"


def test_multiple_fills_partial_and_final_exit_attributed_by_order(env):
    ex, j, e = env
    ex.split = 2
    pos = open_trade(env)
    assert e.close_partial(row(j, pos.id), 1.0, 104.0)
    ex.split = 3
    assert e.close(row(j, pos.id), 105.0)
    p = prov(j, pos.id)
    by = {l["purpose"]: l for l in p["legs"]}
    assert set(by) == {"entry", "partial_exit", "final_exit"}
    assert len(by["partial_exit"]["fills"]) == 2 and len(by["final_exit"]["fills"]) == 3
    # entry fills come from the final close's venue window, bound by order id
    assert len(by["entry"]["fills"]) == 2 and by["entry"]["fill_coverage"] == "COMPLETE"
    for l in by.values():
        assert l["fill_coverage"] == "COMPLETE"
        assert all(f["attribution"] == "ATTRIBUTED" for f in l["fills"])
        assert l["commission"]["status"] == "VERIFIED"
    assert by["partial_exit"]["exit_attribution"]["status"] == "EXACT_LUFFY_ORDER_LINK"
    assert by["final_exit"]["exit_attribution"]["status"] == "EXACT_LUFFY_ORDER_LINK"
    assert p["terminal_close"]["status"] == "VERIFIED"
    assert p["terminal_close"]["leg_id"] == by["final_exit"]["leg_id"]
    assert by["final_exit"]["terminal_close"] is True
    assert by["partial_exit"]["terminal_close"] is False
    assert p["commission"]["status"] == "VERIFIED"
    assert p["commission"]["verified_by_asset"] == {"USDT": "0.28"}   # 7 fills × 0.04
    assert p["strategy_entry_identity"]["status"] == "VERIFIED"
    assert p["provenance_status"] == "VERIFIED"
    assert p["funding"]["status"] == "UNAVAILABLE"
    assert {f["trade_id"] for f in fills(j)} == {pos.id}


def test_two_sequential_trades_same_symbol_keep_their_own_fills(env):
    ex, j, e = env
    a = open_trade(env, "d1")
    assert e.close(row(j, a.id), 101.0)
    b = open_trade(env, "d2")
    assert e.close(row(j, b.id), 102.0)
    for f in fills(j):
        leg = j.query("SELECT trade_id FROM trade_legs WHERE venue_order_id=?",
                      (f["venue_order_id"],))
        assert f["trade_id"] == leg[0]["trade_id"]
    assert len(fills(j, trade_id=a.id)) == 2 and len(fills(j, trade_id=b.id)) == 2


def test_sibling_on_same_symbol_makes_exit_attribution_ambiguous(env):
    ex, j, e = env
    a = open_trade(env)
    # not reachable through risk (one position per symbol); represented directly
    j.add_trade(Position(id="sibling", symbol=SYM, side=Side.LONG, amount=1,
                         entry_price=100, notional_usdt=100))
    assert e.close(row(j, a.id), 101.0)
    leg = [l for l in prov(j, a.id)["legs"] if l["purpose"] == "final_exit"][0]
    assert leg["exit_attribution"]["status"] == "AMBIGUOUS"
    assert leg["exit_attribution"]["journal_open_siblings_same_symbol"] == ["sibling"]
    assert leg["exit_attribution"]["sibling_evidence_scope"] == "journal_only"
    assert prov(j, a.id)["provenance_status"] != "VERIFIED"


def test_unmatched_fill_same_symbol_and_side_is_never_guessed(env):
    ex, j, e = env
    pos = open_trade(env)
    # a venue-side fill (e.g. a native stop's executing order) Luffy never recorded
    ex.fills.append({"id": "x-1", "order": "777", "symbol": SYM, "side": "sell",
                     "amount": 0.5, "price": 97.0, "timestamp": int(time.time() * 1000),
                     "info": {"commission": "0.02", "commissionAsset": "USDT",
                              "realizedPnl": "-1"}})
    assert e.close(row(j, pos.id), 101.0)
    f = fills(j, venue_fill_id="x-1")[0]
    assert f["attribution"] == "UNATTRIBUTED" and f["trade_id"] is None
    assert f["attribution_reason"] == "order_not_recorded_by_luffy"
    p = prov(j, pos.id)
    assert [o["venue_fill_id"] for o in p["unattributed_observations"]] == ["x-1"]
    assert all(f["venue_fill_id"] != "x-1" for l in p["legs"] for f in l["fills"])


def test_ambiguous_fills(env):
    ex, j, e = env
    pos = open_trade(env)
    with j._tx() as db:
        # a fill with no venue identity cannot be verified, even if its order matches
        assert TP.record_fill(db, {"id": None, "order": "1001", "symbol": SYM, "side": "buy",
                                   "amount": 1, "price": 100, "timestamp": 1},
                              source="t", observed_ms=1, context=FUT) == "inserted"
        # one order id recorded against two trades binds to neither
        db.execute("INSERT INTO trade_legs(trade_id,booking_id,kind,purpose,origin,symbol,side,"
                   "venue_order_id,order_identity,exit_attribution,source,recorded_ms,"
                   "market_type) VALUES "
                   "('other',-1,'entry','entry','luffy_order',?,'buy','5555','VERIFIED',"
                   "'NOT_APPLICABLE','t',1,'futures'),('other2',-2,'entry','entry','luffy_order',"
                   "?,'buy','5555','VERIFIED','NOT_APPLICABLE','t',1,'futures')", (SYM, SYM))
        TP.record_fill(db, {"id": "f-5555", "order": "5555", "symbol": SYM, "side": "buy",
                            "amount": 1, "price": 100, "timestamp": 1},
                       source="t", observed_ms=1, context=FUT)
    no_id = fills(j, fill_key=[f["fill_key"] for f in fills(j)
                                if f["fill_key"].startswith("futures|digest:")][0])[0]
    assert no_id["attribution"] == "AMBIGUOUS" and no_id["trade_id"] is None
    assert no_id["attribution_reason"] == "missing_venue_fill_id"
    shared = fills(j, venue_fill_id="f-5555")[0]
    assert shared["attribution"] == "AMBIGUOUS"
    assert shared["attribution_reason"] == "order_recorded_for_multiple_trades"


def test_missing_venue_ids_are_not_invented(env):
    ex, j, e = env
    pos = open_trade(env)
    ex.order_id_missing = True
    assert e.close(row(j, pos.id), 101.0)      # booked from the order response
    leg = [l for l in prov(j, pos.id)["legs"] if l["purpose"] == "final_exit"][0]
    assert leg["order"]["venue_order_id"] is None and leg["order"]["status"] == "UNAVAILABLE"
    assert leg["exit_attribution"]["status"] == "AMBIGUOUS"
    assert leg["fills"] == [] and leg["fill_coverage"] == "UNAVAILABLE"
    # a native stop booked by size detection: no Luffy order, no fill identity
    j.add_trade(Position(id="n", symbol="ETH/USDT", side=Side.LONG, amount=1,
                         entry_price=100, notional_usdt=100, sl_order_id="4242"))
    j.close_trade("n", 95, -5, "sl_fill", accounting={
        "basis": "exchange_exit_detected_by_size", "purpose": "native_exit",
        "protective_algo_id": "4242", "booked_pnl_basis": "estimated"})
    leg = [l for l in prov(j, "n")["legs"] if l["purpose"] == "native_exit"][0]
    assert leg["order"] == {"venue_order_id": None, "client_order_id": None,
                            "protective_algo_id": "4242", "status": "NOT_APPLICABLE"}
    assert leg["exit_attribution"]["status"] == "VENUE_EVENT_UNVERIFIED"
    assert leg["commission"]["status"] == "ESTIMATED"


def test_opaque_venue_ids_are_stored_and_matched_exactly(env):
    """Venue ids are opaque strings: whitespace and leading zeros are part of
    the identity. Only None or "" is unknown."""
    ex, j, e = env
    ex.oid_format, ex.fill_format = " 00{} ", "\t0{oid}/{k} "
    pos = open_trade(env)
    assert e.close(row(j, pos.id), 101.0)
    legs = {l["purpose"]: l for l in prov(j, pos.id)["legs"]}
    assert legs["entry"]["order"]["venue_order_id"] == " 001001 "
    assert legs["final_exit"]["order"]["venue_order_id"] == " 001002 "
    stored = fills(j)
    assert sorted((f["venue_fill_id"], f["venue_order_id"], f["fill_key"], f["attribution"])
                  for f in stored) == [
        ("\t0 001001 /0 ", " 001001 ", "futures|id:\t0 001001 /0 ", "ATTRIBUTED"),
        ("\t0 001002 /0 ", " 001002 ", "futures|id:\t0 001002 /0 ", "ATTRIBUTED")]
    # a fill naming the trimmed or zero-stripped order id is a different order
    for oid in ("001001", "1001", " 1001 "):
        with j._tx() as db:
            TP.record_fill(db, {"id": "x" + oid, "order": oid, "symbol": SYM, "side": "buy",
                                "amount": 1, "price": 100, "timestamp": 1},
                           source="t", observed_ms=1,
                           context={"market_type": "futures", "symbol": SYM})
        assert fills(j, venue_fill_id="x" + oid)[0]["attribution"] == "UNATTRIBUTED"
    assert TP._oid(None) is None and TP._oid("") is None
    assert TP._oid(" 7 ") == " 7 " and TP._oid("007") == "007" and TP._oid(7) == "7"


def test_exact_and_missing_commission(env):
    ex, j, e = env
    pos = open_trade(env)
    ex.commission = None
    assert e.close(row(j, pos.id), 101.0)
    p = prov(j, pos.id)
    exit_leg = [l for l in p["legs"] if l["purpose"] == "final_exit"][0]
    assert all(f["commission_status"] == "UNAVAILABLE" for f in exit_leg["fills"])
    assert exit_leg["commission"]["status"] != "VERIFIED"
    entry_leg = [l for l in p["legs"] if l["purpose"] == "entry"][0]
    assert entry_leg["commission"] == {"status": "VERIFIED",
                                       "verified_by_asset": {"USDT": "0.04"}, "note": None}
    assert p["commission"]["status"] == "PARTIAL"
    assert "verified_commission" in p["missing"]


def test_duplicate_and_replayed_receipts_are_idempotent(env):
    ex, j, e = env
    pos = open_trade(env)
    assert e.close(row(j, pos.id), 101.0)
    n_legs = len(j.query("SELECT * FROM trade_legs"))
    snapshot = [dict(f) for f in fills(j)]
    receipts = j.query("SELECT id, payload FROM trade_accounting_bookings ORDER BY id")
    with j._tx() as db:
        for r in receipts:
            rec = json.loads(r["payload"])
            assert TP.record_booking(db, rec["trade_id"], rec["kind"], rec["before"],
                                     rec["after"], rec["evidence"], r["id"]) is None
        f = json.loads(snapshot[0]["fill_json"])
        assert TP.record_fill(db, f, source="replay", observed_ms=1, context=FUT) == "duplicate"
    assert len(j.query("SELECT * FROM trade_legs")) == n_legs
    assert [dict(f) for f in fills(j)] == snapshot
    j.close_trade(pos.id, 1, 1, "again")               # duplicate close callback
    assert len(j.query("SELECT * FROM trade_legs")) == n_legs
    with j._tx() as db:
        tampered = dict(f, price=1.0)
        assert TP.record_fill(db, tampered, source="replay", observed_ms=1,
                              context=FUT) == "conflict"
    changed = fills(j, fill_key=snapshot[0]["fill_key"])[0]
    assert changed["attribution"] == "AMBIGUOUS" and changed["trade_id"] is None
    assert json.loads(changed["fill_json"]) == f               # original evidence kept


def test_reconciliation_alignment_preserves_provenance(env):
    ex, j, e = env
    pos = open_trade(env)
    # the venue closed half the line while Luffy was down, via an order it never saw
    ex.fills.append({"id": "venue-1", "order": "8888", "symbol": SYM, "side": "sell",
                     "amount": 1.0, "price": 103.0, "timestamp": int(time.time() * 1000),
                     "info": {"commission": "0.03", "commissionAsset": "USDT",
                              "realizedPnl": "3"}})
    ex.fetch_positions = lambda: [{"symbol": SYM, "side": "long", "contracts": 1.0,
                                   "entryPrice": 100.0, "notional": 100.0}]
    ex.fetch_ticker = lambda s: {"last": 103.0}
    ex.fapiPrivateGetOpenAlgoOrders = lambda: []
    ex.fetch_open_orders = lambda s=None: []
    # opened_at is stamped after the fill; widen the window as a late boot would
    with j._tx() as db:
        db.execute("UPDATE trades SET opened_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                   (pos.id,))
    reconcile_futures(ex, j)
    p = prov(j, pos.id)
    leg = [l for l in p["legs"] if l["purpose"] == "reconcile_align"][0]
    assert leg["origin"] == "venue_observed"
    assert leg["exit_attribution"]["status"] == "VENUE_EVENT_UNVERIFIED"
    venue = fills(j, venue_fill_id="venue-1")[0]
    assert venue["attribution"] == "UNATTRIBUTED" and venue["trade_id"] is None
    assert json.loads(venue["observation_json"])["trade_id"] == pos.id
    # the entry fill in the same window binds to the entry by order id
    entry = [l for l in p["legs"] if l["purpose"] == "entry"][0]
    assert entry["fill_coverage"] == "COMPLETE"


def test_recovered_entry_keeps_identity_and_order_identity(tmp_path, monkeypatch):
    from tests.test_entry_recovery import Venue as RecoveryVenue
    monkeypatch.setattr("trader.engine.executor.time.sleep", lambda _: None)
    j = Journal(tmp_path / "j.db")
    from tests.authority_legacy_fixtures import grant
    j.upsert_spec(_valid())
    grant(j, "seed_test")
    d = decision(j)
    ex = RecoveryVenue()
    e = Executor(ex, j, load_config(), MarketType.FUTURES)
    ident, _ = spec_identity(d)
    ex.entry_error = RequestTimeout("accepted, response lost")
    assert e.open(d, 2., 2., 95., 110., "seed_test", "s", entry_identity=ident) is None
    e.recover_entries()
    e.recover_entries()
    t = j.open_trades()[0]
    assert json.loads(t["entry_identity_json"]) == ident
    leg = prov(j, t["id"])["legs"][0]
    assert leg["purpose"] == "recovered_entry" and leg["order"]["venue_order_id"] == "entry"
    assert leg["order"]["client_order_id"].startswith("lr_")
    assert leg["reference"]["basis"] == "unavailable"


# ── safety, owner read, backfill ──────────────────────────────────────────────
def test_provenance_does_not_change_what_is_sent(tmp_path, monkeypatch):
    monkeypatch.setattr("trader.engine.executor.time.sleep", lambda _: None)
    sent = []
    for n, kwargs in enumerate([{}, {"entry_identity": {"status": "VERIFIED"},
                                     "reference": REF}]):
        j = Journal(tmp_path / f"j{n}.db")
        from tests.authority_legacy_fixtures import seed
        seed(j, "s")
        ex = Venue()
        e = Executor(ex, j, load_config(), MarketType.FUTURES)
        e.fill_retry_s = 0
        d = decision(j)
        pos = e.open(d, 2.0, 1.0, 95.0, 110.0, "s", "s", **kwargs)
        e.close_partial(row(j, pos.id), 1.0, 104.0)
        e.close(row(j, pos.id), 105.0)
        sent.append([(s, t, side, amt, {k: v for k, v in p.items() if k != "newClientOrderId"})
                     for s, t, side, amt, p in ex.sent])
    assert sent[0] == sent[1]


def test_provenance_failure_never_fails_the_booking(env, monkeypatch):
    ex, j, e = env

    def boom(*a, **k):
        raise RuntimeError("provenance down")
    monkeypatch.setattr(TP, "record_booking", boom)
    pos = open_trade(env)
    assert row(j, pos.id)["status"] == "open"
    assert e.close(row(j, pos.id), 101.0)
    assert row(j, pos.id)["status"] == "closed"
    assert j.query("SELECT * FROM trade_legs") == []
    assert len(j.query("SELECT * FROM control_events WHERE event='provenance_record_failed'")) == 2


def test_owner_read_exposes_statuses(env):
    ex, j, e = env
    pos = open_trade(env)
    ex.commission = None
    assert e.close(row(j, pos.id), 101.0)
    lineage = trade_lineage(j, pos.id)
    p = lineage["provenance"]
    assert "entry_identity_json" not in lineage["trade"]
    assert p["strategy_entry_identity"]["status"] == "VERIFIED"
    assert not any(u["field"] == "strategy_version_at_entry" for u in lineage["unavailable"])
    assert p["commission"]["status"] == "PARTIAL"
    assert p["funding"]["status"] == "UNAVAILABLE"
    assert p["provenance_status"] == "PARTIAL"
    json.dumps(lineage)                                 # serializable for the API
    j.add_trade(Position(id="legacy", symbol="X/USDT", side=Side.LONG, amount=1,
                         entry_price=1, notional_usdt=1))
    with j._tx() as db:
        db.execute("DELETE FROM trade_legs WHERE trade_id='legacy'")
    legacy = trade_lineage(j, "legacy")["provenance"]
    assert legacy["provenance_status"] == "UNKNOWN"
    assert legacy["commission"]["status"] == "UNAVAILABLE"
    assert legacy["slippage_basis"]["status"] == "UNAVAILABLE"


def test_backfill_links_only_replayable_receipts(env):
    from scripts.backfill_trade_provenance import backfill
    ex, j, e = env
    pos = open_trade(env)
    assert e.close(row(j, pos.id), 101.0)
    with j._tx() as db:
        db.execute("DELETE FROM trade_legs")
        db.execute("DELETE FROM trade_fills")
        rid, payload = db.execute("SELECT id, payload FROM trade_accounting_bookings "
                                  "ORDER BY id DESC LIMIT 1").fetchone()
    dry = backfill(j, apply=False)
    assert dry["dry_run_rolled_back"] == 1 and j.query("SELECT * FROM trade_legs") == []
    rec = json.loads(payload)
    rec["evidence"]["order_id"] = "forged"
    with j._tx() as db:
        db.execute("INSERT INTO trade_accounting_bookings(trade_id,payload) VALUES (?,?)",
                   (pos.id, json.dumps(rec)))
    report = backfill(j, apply=True)
    assert report["skipped_integrity:booking_integrity"] == 1
    legs = j.query("SELECT * FROM trade_legs ORDER BY id")
    assert [l["purpose"] for l in legs] == ["entry", "final_exit"]
    assert all(l["source"] == "backfill_receipt" for l in legs)
    assert legs[1]["exit_attribution"] == "UNKNOWN_HISTORICAL"
    assert {f["attribution"] for f in fills(j)} == {"ATTRIBUTED"}
    again = backfill(j, apply=True)
    assert again["already_linked"] == 2


def test_kernel_population_identity_matches_the_evaluator_that_signalled(tmp_path):
    from types import SimpleNamespace
    import pandas as pd
    from trader.kernel import Kernel
    j = Journal(tmp_path / "k.db")
    j.upsert_spec(_valid(), state="paper")
    kernel = Kernel.__new__(Kernel)
    kernel.journal = j
    kernel._load_population()
    loaded = kernel._entry_identities["seed_test"]
    stamped = compile_spec(_valid()).spec_sha256
    assert loaded["spec_sha256"] == stamped and loaded["registry_state_at_load"] == "paper"
    d = decision(j)
    d.strategy_signals = [{"strategy_id": "seed_test", "action": "BUY",
                           "params": {"spec_id": "seed_test", "spec_sha256": stamped}}]
    snap = SimpleNamespace(price=101.0, ts="2026-09-30T00:00:00+00:00",
                           df=lambda tf: pd.DataFrame({"ts": ["2026-09-29T23:45:00"],
                                                       "close": [101.0]}))
    identity, reference = kernel._entry_provenance(d, snap, "seed_test", "15m")
    assert identity["status"] == "VERIFIED"
    assert reference == {"price": 101.0, "basis": "decision_snapshot_price",
                         "definition": reference["definition"],
                         "snapshot_at": "2026-09-30T00:00:00+00:00",
                         "bar_ts": "2026-09-29T23:45:00"}
    # the registry changes and the population reloads: the old signal no longer verifies
    mutated = _valid()
    mutated.filters = ["adx(14) < 30"]
    j.upsert_spec(mutated, state="paper")
    kernel._load_population()
    again, _ = kernel._entry_provenance(d, snap, "seed_test", "15m")
    assert again["status"] == "AMBIGUOUS"
    # a snapshot failure never blocks: the reference is simply unavailable
    bad = SimpleNamespace(price="x", ts=None, df=lambda tf: None)
    _, ref = kernel._entry_provenance(d, bad, "seed_test", "15m")
    assert ref["basis"] == "unavailable"


# ── terminal close ────────────────────────────────────────────────────────────
def _bind_entry_fills(ex, j):
    """The entry's own venue fills, bound by order id (as a later read would)."""
    from trader.engine.accounting import safe_fill
    with j._tx() as db:
        for f in ex.fills:
            TP.record_fill(db, safe_fill(f), source="t", observed_ms=1, context=FUT)


def _fail_close_provenance(monkeypatch):
    orig = TP.record_booking

    def flaky(db, tid, kind, *a, **k):
        if kind.startswith("close:"):
            raise RuntimeError("provenance write failed")
        return orig(db, tid, kind, *a, **k)
    monkeypatch.setattr(TP, "record_booking", flaky)


def test_complete_entry_then_failed_terminal_write_is_not_verified(env, monkeypatch):
    ex, j, e = env
    pos = open_trade(env)
    _bind_entry_fills(ex, j)
    before = prov(j, pos.id)
    assert before["provenance_status"] == "VERIFIED"          # open, everything so far exact
    assert before["terminal_close"]["status"] == "NOT_APPLICABLE"
    _fail_close_provenance(monkeypatch)
    assert e.close(row(j, pos.id), 101.0)
    assert row(j, pos.id)["status"] == "closed"                # booking unaffected
    assert len(j.query("SELECT * FROM control_events "
                       "WHERE event='provenance_record_failed'")) == 1
    p = prov(j, pos.id)
    assert p["provenance_status"] == "PARTIAL"
    assert p["terminal_close"]["status"] == "UNAVAILABLE"
    assert "terminal_close" in p["missing"]
    lineage = trade_lineage(j, pos.id)
    gap = [u for u in lineage["unavailable"] if u["field"] == "terminal_close_provenance"]
    assert gap and "UNAVAILABLE" in gap[0]["reason"]


def test_failed_entry_write_then_complete_terminal_close_is_not_verified(env, monkeypatch):
    ex, j, e = env
    orig = TP.record_booking

    def flaky(db, tid, kind, *a, **k):
        if not kind.startswith("close:"):
            raise RuntimeError("provenance write failed")
        return orig(db, tid, kind, *a, **k)
    monkeypatch.setattr(TP, "record_booking", flaky)
    pos = open_trade(env)
    assert row(j, pos.id)["status"] == "open"                  # booking unaffected
    monkeypatch.setattr(TP, "record_booking", orig)
    assert e.close(row(j, pos.id), 101.0)
    assert len(j.query("SELECT * FROM control_events "
                       "WHERE event='provenance_record_failed'")) == 1
    p = prov(j, pos.id)
    assert p["strategy_entry_identity"]["status"] == "VERIFIED"
    assert p["terminal_close"]["status"] == "VERIFIED"
    exit_leg = [l for l in p["legs"] if l["purpose"] == "final_exit"][0]
    assert exit_leg["fill_coverage"] == "COMPLETE"
    assert not any(l["purpose"] in TP.ENTRY_PURPOSES for l in p["legs"])
    assert "entry_order" in p["missing"]
    assert p["provenance_status"] == "PARTIAL"


def test_partial_exit_cannot_substitute_for_terminal_close(env, monkeypatch):
    ex, j, e = env
    pos = open_trade(env)
    _bind_entry_fills(ex, j)
    assert e.close_partial(row(j, pos.id), 1.0, 104.0)
    partial = [l for l in prov(j, pos.id)["legs"] if l["purpose"] == "partial_exit"][0]
    assert partial["exit_attribution"]["status"] == "EXACT_LUFFY_ORDER_LINK"
    assert partial["fill_coverage"] == "COMPLETE" and partial["terminal_close"] is False
    _fail_close_provenance(monkeypatch)
    assert e.close(row(j, pos.id), 105.0)
    p = prov(j, pos.id)
    assert p["provenance_status"] == "PARTIAL"
    assert p["terminal_close"]["status"] == "UNAVAILABLE"
    assert all(l["terminal_close"] is False for l in p["legs"])
    stored = j.query("SELECT status_transition, terminal_close FROM trade_legs ORDER BY id")
    assert [(r["status_transition"], r["terminal_close"]) for r in stored] == [
        ("none->open", 0), ("open->open", 0)]


def test_terminal_marker_is_the_open_to_closed_transition(env):
    ex, j, e = env
    pos = open_trade(env)
    assert e.close(row(j, pos.id), 101.0)
    legs = j.query("SELECT purpose, status_transition, terminal_close FROM trade_legs "
                   "ORDER BY id")
    assert [(r["purpose"], r["status_transition"], r["terminal_close"]) for r in legs] == [
        ("entry", "none->open", 0), ("final_exit", "open->closed", 1)]
    # a native stop closes the trade: terminal, but not a Luffy order link
    j.add_trade(Position(id="n", symbol="ETH/USDT", side=Side.LONG, amount=1,
                         entry_price=100, notional_usdt=100, sl_order_id="4242"))
    j.close_trade("n", 95, -5, "sl_fill", accounting={
        "basis": "exchange_exit_detected_by_size", "purpose": "native_exit"})
    t = prov(j, "n")["terminal_close"]
    assert t["status"] == "PARTIAL" and t["purpose"] == "native_exit"
    assert "exit_attribution_VENUE_EVENT_UNVERIFIED" in t["gaps"]


# ── market-scoped identity ────────────────────────────────────────────────────
def _entry(j, tid, market, oid="1001", fid="1001-0", side=Side.LONG, fills=True):
    fill = {"id": fid, "order": oid, "symbol": SYM, "side": "buy", "amount": 1.0,
            "price": 100.0, "timestamp": 1, "commission": "0.1", "commission_asset": "USDT"}
    j.add_trade(Position(id=tid, symbol=SYM, side=side, amount=1.0, entry_price=100,
                         notional_usdt=100, market_type=market),
                accounting={"basis": "entry_order_confirmation", "purpose": "entry",
                            "order_id": oid, "side": "buy", "confirmed_quantity": 1.0,
                            "confirmed_price": 100.0, "fills": [fill] if fills else []})
    return fill


def test_spot_and_futures_identical_ids_do_not_collide(env):
    ex, j, e = env
    spot = _entry(j, "s1", "spot")
    fut = _entry(j, "f1", "futures")
    rows = fills(j)
    assert len(rows) == 2
    assert {(r["market_type"], r["fill_key"], r["venue_fill_id"], r["trade_id"]) for r in rows} \
        == {("spot", "spot|id:1001-0", "1001-0", "s1"),
            ("futures", "futures|id:1001-0", "1001-0", "f1")}
    assert {r["attribution"] for r in rows} == {"ATTRIBUTED"}
    with j._tx() as db:
        assert TP.record_fill(db, spot, source="r", observed_ms=1, context=SPOT) == "duplicate"
        assert TP.record_fill(db, fut, source="r", observed_ms=1, context=FUT) == "duplicate"
        assert TP.record_fill(db, dict(spot, price=1.0), source="r", observed_ms=1,
                              context=SPOT) == "conflict"
    after = {r["market_type"]: r for r in fills(j)}
    assert after["spot"]["attribution"] == "AMBIGUOUS"
    assert json.loads(after["spot"]["fill_json"]) == TP.clean(spot)    # original kept
    assert after["futures"]["attribution"] == "ATTRIBUTED"             # untouched
    assert after["futures"]["trade_id"] == "f1"
    assert prov(j, "f1")["legs"][0]["fill_coverage"] == "COMPLETE"


def test_no_cross_market_order_attribution(env):
    ex, j, e = env
    _entry(j, "s1", "spot", oid="2002", fills=False)
    with j._tx() as db:
        # a futures fill carrying the spot order's number is not that order
        TP.record_fill(db, {"id": "x", "order": "2002", "symbol": SYM, "side": "sell",
                            "amount": 1, "price": 100, "timestamp": 1},
                       source="t", observed_ms=1, context=FUT)
    f = fills(j, venue_fill_id="x")[0]
    assert f["attribution"] == "UNATTRIBUTED" and f["trade_id"] is None
    assert f["attribution_reason"] == "order_not_recorded_by_luffy"
    # the same order number on a futures trade does not make the spot link ambiguous
    _entry(j, "f1", "futures", oid="3003", fills=False)
    j.close_trade("f1", 101, 1, "manual", accounting={
        "basis": "t", "purpose": "final_exit", "order_id": "2002", "side": "sell",
        "quantity": 1.0})
    j.close_trade("s1", 101, 1, "manual", accounting={
        "basis": "t", "purpose": "final_exit", "order_id": "2002", "side": "sell",
        "quantity": 1.0})
    for tid in ("f1", "s1"):
        leg = [l for l in prov(j, tid)["legs"] if l["purpose"] == "final_exit"][0]
        assert leg["exit_attribution"]["status"] == "EXACT_LUFFY_ORDER_LINK"
    # the futures fill now binds to the futures exit order, never the spot one
    assert fills(j, venue_fill_id="x")[0]["trade_id"] == "f1"


def test_same_order_recorded_for_two_trades_is_ambiguous(env):
    ex, j, e = env
    _entry(j, "a", "futures", oid="10", fills=False)
    j.close_trade("a", 101, 1, "manual", accounting={
        "basis": "t", "purpose": "final_exit", "order_id": "77", "side": "sell"})
    _entry(j, "b", "futures", oid="11", fills=False)
    j.close_trade("b", 101, 1, "manual", accounting={
        "basis": "t", "purpose": "final_exit", "order_id": "77", "side": "sell"})
    leg = [l for l in prov(j, "b")["legs"] if l["purpose"] == "final_exit"][0]
    assert leg["exit_attribution"]["status"] == "AMBIGUOUS"
    assert leg["exit_attribution"]["reason"] == "order_recorded_for_multiple_trades"


@pytest.mark.parametrize("market", [None, "margin"])
def test_unknown_market_type_fails_closed(env, market):
    ex, j, e = env
    _entry(j, "u", market)
    assert fills(j) == []                                   # nothing keyed on a guess
    j.close_trade("u", 101, 1, "manual", accounting={
        "basis": "t", "purpose": "final_exit", "order_id": "55", "side": "sell"})
    stored = j.query("SELECT market_type FROM trade_legs WHERE trade_id='u'")
    assert {r["market_type"] for r in stored} == {None}
    p = prov(j, "u")
    assert all(l["market_scope"] == "UNKNOWN" for l in p["legs"])
    exit_leg = [l for l in p["legs"] if l["purpose"] == "final_exit"][0]
    assert exit_leg["exit_attribution"]["status"] == "AMBIGUOUS"
    assert exit_leg["exit_attribution"]["reason"] == "trade_market_type_unknown"
    assert "market_scope" in p["missing"] and p["provenance_status"] == "PARTIAL"
    assert p["terminal_close"]["status"] == "PARTIAL"
    assert "market_type_unknown" in p["terminal_close"]["gaps"]
    with j._tx() as db:
        assert TP.record_fill(db, {"id": "z", "order": "55", "symbol": SYM},
                              source="t", observed_ms=1, context={}) == "unscoped"
    assert TP.market_scope({"market_type": "spot"}, {"market_type": "futures"}) is None


def test_exit_vocabulary_is_narrowed_and_claims_no_venue_exclusivity(env):
    ex, j, e = env
    pos = open_trade(env)
    assert e.close(row(j, pos.id), 101.0)
    raw = j.query("SELECT exit_attribution FROM trade_legs WHERE purpose='final_exit'")
    assert raw[0]["exit_attribution"] == "EXACT_LUFFY_ORDER_LINK"
    assert not j.query("SELECT 1 FROM trade_legs WHERE exit_attribution='EXACT'")
    p = prov(j, pos.id)
    leg = [l for l in p["legs"] if l["purpose"] == "final_exit"][0]["exit_attribution"]
    assert leg["venue_position_exclusivity"] == "NOT_CLAIMED"
    assert leg["sibling_evidence_scope"] == "journal_only"
    assert leg["journal_open_siblings_same_symbol"] == []
    assert "sole" not in leg["reason"] and "exclusive" not in leg["reason"]
    assert "does not assert exclusive ownership" in p["exit_attribution_meaning"]
    # the R1 draft's 'EXACT' is read conservatively, never as an exact link
    with j._tx() as db:
        db.execute("UPDATE trade_legs SET exit_attribution='EXACT', exit_attribution_json=? "
                   "WHERE purpose='final_exit'",
                   (json.dumps({"open_siblings_same_symbol": [],
                                "reason": "luffy_order_named_this_trade_sole_open_position"}),))
    p = prov(j, pos.id)
    leg = [l for l in p["legs"] if l["purpose"] == "final_exit"][0]["exit_attribution"]
    assert leg["status"] == "LEGACY_EXACT_UNSCOPED"
    assert leg["journal_open_siblings_same_symbol"] == []
    assert leg["venue_position_exclusivity"] == "NOT_CLAIMED"
    assert p["provenance_status"] == "PARTIAL" and p["terminal_close"]["status"] == "PARTIAL"
    assert TP.exit_status("EXACT_LUFFY_ORDER_LINK", None) == "LEGACY_EXACT_UNSCOPED"


# ── additive migration of the R1 draft schema ─────────────────────────────────
R1_DRAFT_TABLES = """
CREATE TABLE trade_legs (id INTEGER PRIMARY KEY AUTOINCREMENT, trade_id TEXT NOT NULL,
    booking_id INTEGER NOT NULL, kind TEXT NOT NULL, purpose TEXT NOT NULL,
    origin TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT, venue_order_id TEXT,
    client_order_id TEXT, protective_algo_id TEXT, order_identity TEXT NOT NULL,
    requested_qty REAL, booked_qty REAL, booked_price REAL, reference_json TEXT,
    exit_attribution TEXT NOT NULL, exit_attribution_json TEXT, fee_basis TEXT,
    source TEXT NOT NULL, recorded_ms INTEGER NOT NULL);
CREATE UNIQUE INDEX trade_legs_booking ON trade_legs(booking_id);
CREATE TABLE trade_fills (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
    fill_key TEXT NOT NULL, venue_fill_id TEXT, venue_order_id TEXT, trade_id TEXT,
    leg_id INTEGER, leg TEXT, side TEXT, qty REAL, price REAL, commission TEXT,
    commission_asset TEXT, realized_pnl TEXT, venue_ts_ms INTEGER, observed_ms INTEGER NOT NULL,
    attribution TEXT NOT NULL, attribution_reason TEXT NOT NULL, source TEXT NOT NULL,
    observation_json TEXT, fill_json TEXT NOT NULL, UNIQUE(symbol, fill_key));
"""


def test_r1_draft_schema_migrates_additively_without_guessing_scope(tmp_path):
    path = tmp_path / "draft.db"
    j = Journal(path)
    j.add_trade(Position(id="old", symbol=SYM, side=Side.LONG, amount=1, entry_price=100,
                         notional_usdt=100))
    j._local.conn.close()
    con = sqlite3.connect(path)
    con.executescript("DROP TABLE trade_legs; DROP TABLE trade_fills;" + R1_DRAFT_TABLES)
    con.execute("INSERT INTO trade_legs(trade_id,booking_id,kind,purpose,origin,symbol,side,"
                "venue_order_id,order_identity,booked_qty,exit_attribution,source,recorded_ms)"
                " VALUES ('old',1,'entry','entry','luffy_order',?,'buy','1001','VERIFIED',1,"
                "'NOT_APPLICABLE','live_booking',1)", (SYM,))
    con.execute("INSERT INTO trade_fills(symbol,fill_key,venue_fill_id,venue_order_id,trade_id,"
                "leg_id,leg,qty,commission,commission_asset,observed_ms,attribution,"
                "attribution_reason,source,fill_json) VALUES (?,'id:1001-0','1001-0','1001',"
                "'old',1,'entry',1,'0.1','USDT',1,'ATTRIBUTED','r','legacy','{}')", (SYM,))
    con.commit()
    con.close()
    j = Journal(path)                                    # additive migration
    legacy = j.query("SELECT * FROM trade_fills")[0]
    assert legacy["fill_key"] == "id:1001-0" and legacy["market_type"] is None
    leg = j.query("SELECT market_type, terminal_close FROM trade_legs")[0]
    assert leg["market_type"] is None and leg["terminal_close"] is None
    # a new futures fill with the same venue id is its own row; the legacy row stays
    _entry(j, "new", "futures")
    rows = fills(j)
    assert [(r["fill_key"], r["market_type"]) for r in rows] == [
        ("id:1001-0", None), ("futures|id:1001-0", "futures")]
    assert rows[1]["trade_id"] == "new"                  # never bound to the legacy leg
    p = prov(j, "old")
    assert [f["venue_fill_id"] for f in p["legacy_unscoped_fills"]] == ["1001-0"]
    assert p["legs"][0]["fills"] == [] and p["legs"][0]["market_scope"] == "UNKNOWN"
    assert "market_scope" in p["missing"] and p["provenance_status"] != "VERIFIED"


# ── backfill CLI: dry run never writes the source ─────────────────────────────
def _legacy_head_db(path: Path, journal_mode: str):
    """A journal at the pre-R1 schema, with replayable receipts."""
    j = Journal(path)
    _entry(j, "h1", "futures")
    j.close_trade("h1", 101, 1, "manual", accounting={
        "basis": "t", "purpose": "final_exit", "order_id": "1002", "side": "sell"})
    j._local.conn.close()
    con = sqlite3.connect(path)
    con.executescript("DROP TRIGGER trades_entry_identity_immutable; DROP TABLE trade_legs;"
                      "DROP TABLE trade_fills; ALTER TABLE trades DROP COLUMN entry_identity_json;")
    con.execute(f"PRAGMA journal_mode={journal_mode}")
    if journal_mode == "wal":
        con.execute("PRAGMA wal_autocheckpoint=0")
        con.execute("INSERT INTO control_events(ts,event,from_state,to_state,actor,detail) "
                    "VALUES ('t','uncheckpointed','','','t','')")
        con.commit()
        return con                                     # held open: the WAL stays on disk
    con.close()
    return None


def _fingerprint(path: Path):
    """Filesystem only — opening the source with SQLite (even mode=ro) could
    itself create or write -shm. Every file in the directory, -shm included."""
    import hashlib
    return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns,
                     p.stat().st_size) for p in sorted(path.parent.iterdir())}


def _contents(path: Path, scratch: Path):
    """Schema, columns and trades, read from a file copy of the source."""
    import shutil
    d = scratch.parent / "inspect"
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir()
    for p in path.parent.iterdir():
        if p.name in (path.name, path.name + "-wal"):
            shutil.copyfile(p, d / p.name)
    con = sqlite3.connect(d / path.name)
    out = {"schema": con.execute("SELECT type, name, tbl_name, sql FROM sqlite_master "
                                 "ORDER BY type, name").fetchall()}
    out["columns"] = {t: con.execute(f"PRAGMA table_info({t})").fetchall()
                      for (t,) in con.execute("SELECT name FROM sqlite_master "
                                              "WHERE type='table'").fetchall()}
    out["trades"] = con.execute("SELECT * FROM trades ORDER BY id").fetchall()
    out["events"] = [r[0] for r in con.execute("SELECT event FROM control_events")]
    con.close()
    shutil.rmtree(d)
    return out


@pytest.fixture
def legacy_source(tmp_path, monkeypatch, request):
    """delete: rollback-journal mode. wal: a writer holds the database open
    with an uncheckpointed commit (-wal and -shm present). wal_no_shm: the
    database and -wal of that state copied into a directory with no -shm."""
    import shutil
    src = tmp_path / "src"
    src.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(scratch))
    path = src / "luffy.db"
    if request.param == "wal_no_shm":
        origin = tmp_path / "origin"
        origin.mkdir()
        holder = _legacy_head_db(origin / "luffy.db", "wal")
        for name in ("luffy.db", "luffy.db-wal"):
            shutil.copyfile(origin / name, src / name)
        holder.close()
        holder = None
        assert sorted(p.name for p in src.iterdir()) == ["luffy.db", "luffy.db-wal"]
    else:
        holder = _legacy_head_db(path, request.param)
    if request.param != "delete":
        assert (src / "luffy.db-wal").stat().st_size > 0      # committed, not checkpointed
    yield path, scratch
    if holder is not None:
        holder.close()


SOURCES = ["delete", "wal", "wal_no_shm"]


@pytest.mark.parametrize("legacy_source", SOURCES, indirect=True)
def test_cli_dry_run_leaves_legacy_source_untouched(legacy_source, capsys, monkeypatch):
    from scripts import backfill_trade_provenance as B
    path, scratch = legacy_source
    before = _fingerprint(path)
    content = _contents(path, scratch)
    assert "trade_legs" not in content["columns"]
    seen = {}
    real = B.backfill

    def spy(journal, apply):
        seen["events"] = [r["event"] for r in journal.query("SELECT event FROM control_events")]
        return real(journal, apply)
    monkeypatch.setattr(B, "backfill", spy)
    B.main(["--db", str(path)])
    report = json.loads(capsys.readouterr().out)
    assert report["dry_run_rolled_back"] == 1 and report["receipts"] == 2
    assert report["leg:entry:VERIFIED"] == 1 and report["leg:final_exit:VERIFIED"] == 1
    assert report["leg_market:futures"] == 2 and report["terminal_close_legs"] == 1
    assert report["source"]["opened"] == "file snapshot copy; sqlite opened only the copy"
    wal = "luffy.db-wal" in before
    assert report["source"]["files_copied"] == (["luffy.db", "luffy.db-wal"] if wal
                                                else ["luffy.db"])
    if wal:                                               # committed WAL pages are in the copy
        assert "uncheckpointed" in seen["events"]
    assert _fingerprint(path) == before                   # every file, -shm included
    if "luffy.db-shm" not in before:                     # no -shm is ever created
        assert not (path.parent / "luffy.db-shm").exists()
    assert _contents(path, scratch) == content
    assert list(scratch.iterdir()) == []                  # the copy is discarded


@pytest.mark.parametrize("legacy_source", SOURCES, indirect=True)
@pytest.mark.parametrize("where, exc", [("backfill", RuntimeError),
                                        ("Journal", KeyboardInterrupt)])
def test_failed_or_interrupted_dry_run_leaves_source_untouched(legacy_source, monkeypatch,
                                                               where, exc):
    from scripts import backfill_trade_provenance as B
    path, scratch = legacy_source
    before = _fingerprint(path)

    def boom(*a, **k):
        raise exc("stopped")
    monkeypatch.setattr(B, where, boom)
    with pytest.raises(exc):
        B.main(["--db", str(path)])
    assert _fingerprint(path) == before
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("legacy_source", ["delete", "wal_no_shm"], indirect=True)
def test_dry_run_refuses_a_source_that_changes_during_the_copy(legacy_source, monkeypatch):
    from scripts import backfill_trade_provenance as B
    path, scratch = legacy_source
    real, calls = B._state, []

    def moving(source):
        calls.append(1)
        state = real(source)
        if len(calls) == 1:                               # a writer appends mid-copy
            with open(Path(str(path) + "-wal"), "ab") as fh:
                fh.write(b"\0" * 32)
        return state
    monkeypatch.setattr(B, "_state", moving)
    monkeypatch.setattr(B, "Journal", lambda *a: pytest.fail("copy must not be used"))
    with pytest.raises(B.SnapshotRefused, match="source_changed_during_copy"):
        B.main(["--db", str(path)])
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("legacy_source", ["delete"], indirect=True)
def test_dry_run_refuses_an_unreadable_source(legacy_source):
    import os
    from scripts import backfill_trade_provenance as B
    if os.geteuid() == 0:
        pytest.skip("root reads mode-000 files")
    path, scratch = legacy_source
    path.chmod(0)
    try:
        with pytest.raises(B.SnapshotRefused, match="source_unreadable"):
            B.dry_run(path)
    finally:
        path.chmod(0o644)
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("legacy_source", ["delete"], indirect=True)
def test_cli_apply_migrates_and_backfills(legacy_source, capsys):
    from scripts import backfill_trade_provenance as B
    path, _ = legacy_source
    trades_before = _contents(path, path.parent)["trades"]
    B.main(["--db", str(path), "--apply"])
    report = json.loads(capsys.readouterr().out)
    assert "dry_run_rolled_back" not in report and report["receipts"] == 2
    j = Journal(path)
    legs = j.query("SELECT purpose, market_type, terminal_close, exit_attribution, source "
                   "FROM trade_legs ORDER BY id")
    assert [tuple(l.values()) for l in legs] == [
        ("entry", "futures", 0, "NOT_APPLICABLE", "backfill_receipt"),
        ("final_exit", "futures", 1, "UNKNOWN_HISTORICAL", "backfill_receipt")]
    assert fills(j)[0]["attribution"] == "ATTRIBUTED"
    assert row(j, "h1")["entry_identity_json"] is None         # never backfilled
    assert [r[:-1] if len(r) > len(trades_before[0]) else r
            for r in _contents(path, path.parent)["trades"]] == trades_before
    p = prov(j, "h1")
    assert p["terminal_close"]["status"] == "PARTIAL"           # UNKNOWN_HISTORICAL link
    B.main(["--db", str(path), "--apply"])
    assert json.loads(capsys.readouterr().out)["already_linked"] == 2
