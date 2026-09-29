"""LUFFY-PROTECTION-SNAPSHOT-R2: read-only protection evidence in every state.

Covers Astra's R1 blockers: capability-safe dependencies, a coherent
observation (no mixed-moment VERIFIED), verification semantics, protection vs
reconciliation cleanliness, generation-ordered persistence, fail-closed
freshness, bounded publication under journal contention, fixed-start cadence,
single flight and bounded shutdown. Every evidence case proves zero venue
mutations and zero journal/control changes outside the evidence tables.
"""
import functools
import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import ccxt
import pytest

from tests.test_kernel_boot_recovery import RecordingVenue, _stop, _trade
from trader.core.journal import Journal
from trader.core.types import ControlState, Position, Side
from trader.dashboard import owner_api
from trader.engine import protection_snapshot as ps
from trader.engine.reconcile import REARM_KEY, reconcile_futures
from trader.engine.recovery import KEY as RECOVERY_KEY
from trader.engine.state import ControlStateMachine
from trader.engine.venue_reads import TickPrecision

EVIDENCE_TABLES = (ps.TABLE, ps.BOOTS, "sqlite_sequence")


class Venue(RecordingVenue):
    """The venue itself (the kernel's exchange view). Supervisor/reconcile use it."""

    def __init__(self):
        super().__init__()
        self.slow = 0.0

    def fetch_positions(self):
        if self.slow:
            time.sleep(self.slow)
        return super().fetch_positions()


class Reads:
    """The snapshot's dedicated read client over the same venue state.

    ``before``/``after`` hooks run around the n-th call of a read, to change
    the venue (or run a Supervisor pass) in the middle of an observation.
    """

    def __init__(self, venue, ticks=None):
        self.venue = venue
        self.precision = TickPrecision({"BTCUSDT": "0.1"} if ticks is None else ticks)
        self.calls = []
        self.before, self.after = {}, {}
        self.ordinary = {}
        self.hang = None
        self.lock = threading.Lock()
        self.inflight = self.max_inflight = 0

    def _call(self, name, produce):
        with self.lock:
            self.calls.append(name)
            n = self.calls.count(name)
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            if self.hang is not None:
                self.hang.wait(10)
            if (name, n) in self.before:
                self.before[(name, n)]()
            if self.venue.unreadable:
                raise ccxt.RequestTimeout("venue timeout")
            data = produce()
            if (name, n) in self.after:
                self.after[(name, n)]()
            return data
        finally:
            with self.lock:
                self.inflight -= 1

    def positions(self):
        return self._call("positions", lambda: [
            {**{k: p.get(k) for k in ("symbol", "side", "contracts", "entryPrice")},
             "updateTime": p["updateTime"] if "updateTime" in p else "1790000000000"}
            for p in self.venue.positions])

    def algo_orders(self):
        def rows():
            if self.venue.algo_listing_invalid:
                return {"code": -1, "msg": "partial"}
            return [dict(s) for s in self.venue.stops]
        return self._call("algo_orders", rows)

    def open_orders(self, symbol):
        return self._call("open_orders", lambda: [dict(o) for o in self.ordinary.get(symbol, [])])

    def rate_state(self):
        return {"used_weight_1m": None}


def _db_state(journal):
    """Everything a snapshot must not change: all rows except its own tables."""
    out = {}
    for table in [r["name"] for r in journal.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]:
        if table in EVIDENCE_TABLES:
            continue
        rows = journal.query(f"SELECT * FROM {table}")
        out[table] = sorted(json.dumps(dict(r), sort_keys=True, default=str) for r in rows)
    return out


@pytest.fixture
def world(tmp_path):
    journal = Journal(tmp_path / "j.db")
    venue = Venue()
    return journal, venue, Reads(venue), ControlStateMachine(journal)


def _protected(journal, venue, *, stop=None):
    journal.add_trade(_trade())
    venue.positions = [{"symbol": "BTC/USDT:USDT", "contracts": 1, "side": "long",
                        "entryPrice": 100, "markPrice": 100}]
    venue.stops = [stop or _stop()]


def _observer(journal, reads, busy=None):
    return ps.Observer(reads, ps.JournalReads(journal.db_path), busy or (lambda: False))


def _obs(journal, reads, busy=None, budget_s=10.0):
    return ps.observe(_observer(journal, reads, busy),
                      budget=ps.Budget(time.monotonic() + budget_s))


def _snap(journal, venue, reads, *, busy=None, check_db=True):
    before = _db_state(journal)
    s = ps.evaluate(_obs(journal, reads, busy), reads.precision, boot=1, seq=1)
    assert venue.mutations == []                       # zero venue mutations
    if check_db:
        assert _db_state(journal) == before            # zero journal/control change
    assert s["mutations"] == 0
    return s


def _sym(snap, symbol="BTC/USDT"):
    return next(r for r in snap["symbols"] if r["symbol"] == symbol)


def _store(journal, timeout_s=0.3):
    return ps.SnapshotStore(journal.db_path, timeout_s=timeout_s)


def _monitor(journal, reads, busy=None, **kw):
    kw.setdefault("timeout_s", 5)
    store = kw.pop("store", None) or _store(journal)
    return ps.ProtectionMonitor(_observer(journal, reads, busy), store, **kw)


def _stored(journal):
    return _store(journal).read()


# ── evidence cases ───────────────────────────────────────────────────────────
def test_a_fully_protected_is_verified(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    s = _snap(journal, venue, reads)
    assert s["status"] == "VERIFIED" and s["reasons"] == []
    assert s["checks"] == {"venue_positions": True, "observation_consistent": True,
                           "reconciliation": True, "venue_protection": True,
                           "precision_known": True}
    assert s["complete_listing"] is True and s["position_count"] == 1
    r = _sym(s)
    assert r == {**r, "side": "long", "quantity": 1.0, "journal_amount": 1.0,
                 "quantity_agrees": True, "stop_present": True, "stop_id": "stop-1",
                 "stop_side": "sell", "reduce_only": True, "order_type": "STOP_MARKET",
                 "covered_quantity": 1.0, "trigger_price": 95.0, "tick_size": "0.1",
                 "precision_status": "VALID", "precision_valid": True,
                 "stop_id_matches_journal": True, "match_reason": "matched",
                 "rearm_evidence": "NONE", "verified": True, "journal_trade_id": "pos_btc"}
    assert s["venue_request_names"] == ["positions", "algo_orders", "positions",
                                        "algo_orders", "open_orders"]
    assert s["observation"] == {**s["observation"], "positions_stable": True,
                                "stops_stable": True, "journal_stable": True,
                                "supervisor_pass_overlap": False}
    assert s["cleanliness"]["status"] == "CLEAN"


def test_b_missing_stop_is_partial_and_never_rearmed(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    venue.stops = []
    s = _snap(journal, venue, reads)
    assert s["status"] == "PARTIAL" and not s["checks"]["venue_protection"]
    assert "position_unprotected:BTC/USDT" in s["reasons"]
    assert _sym(s)["stop_present"] is False and venue.stops == []
    assert journal.kv_get(REARM_KEY) is None


def test_c_orphan_stop_is_cleanliness_not_protection(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    venue.stops.append(_stop(algo_id="old-sui") | {"symbol": "SUIUSDT", "side": "BUY"})
    s = _snap(journal, venue, reads)
    assert s["status"] == "VERIFIED"                  # current exposure is protected
    items = s["cleanliness"]["items"]
    assert s["cleanliness"]["status"] == "ATTENTION"
    assert [(i["class"], i["id"]) for i in items] == [("orphan_stop", "old-sui")]
    assert any(st["algoId"] == "old-sui" for st in venue.stops)       # never cancelled


def test_d_stop_id_mismatch_is_cleanliness(world):
    journal, venue, reads, _ = world
    _protected(journal, venue, stop=_stop(algo_id="other-id"))
    s = _snap(journal, venue, reads)
    assert s["status"] == "VERIFIED" and _sym(s)["stop_id_matches_journal"] is False
    assert [i["class"] for i in s["cleanliness"]["items"]] == ["stop_id_mismatch"]


def test_d2_unrecognised_extra_stop_is_cleanliness(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    venue.stops.append(_stop(algo_id="leak-2", trigger="90"))
    s = _snap(journal, venue, reads)
    assert s["status"] == "VERIFIED"
    assert [(i["class"], i["id"]) for i in s["cleanliness"]["items"]] == \
        [("unrecognised_stop", "leak-2")]


def test_e_loosened_stop_is_partial(world):
    journal, venue, reads, _ = world
    _protected(journal, venue, stop=_stop(trigger="90"))
    s = _snap(journal, venue, reads)
    assert s["status"] == "PARTIAL"
    assert "stop_inadequate:BTC/USDT:trigger_below_expected" in s["reasons"]


@pytest.mark.parametrize("fault", ["algo_invalid", "venue_timeout"])
def test_f_unreadable_listing_or_venue(world, fault):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    if fault == "algo_invalid":
        venue.algo_listing_invalid = True
    else:
        venue.unreadable = True
    s = _snap(journal, venue, reads)
    assert s["status"] == "UNREADABLE"
    assert s["reasons"][0] == ("protection_snapshot_unreadable:algo_listing_invalid"
                               if fault == "algo_invalid" else "venue_timeout")
    assert s["cleanliness"]["status"] == "UNKNOWN"


def test_g_read_budget_stops_further_calls(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    obs = _obs(journal, reads, budget_s=-1)
    s = ps.evaluate(obs, reads.precision, boot=1, seq=1)
    assert s["status"] == "UNREADABLE" and s["reasons"] == ["venue_timeout"]
    assert reads.calls == []


def test_h_ordinary_orders_are_classified_not_protection(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    sym = "BTC/USDT:USDT"

    def order(oid, side, typ, ro, stop=None):
        return {"id": oid, "symbol": sym, "side": side, "type": typ.lower(), "amount": 1.0,
                "price": 110.0, "reduceOnly": ro, "stopPrice": stop,
                "info": {"origType": typ, "reduceOnly": str(ro).lower(),
                         **({"stopPrice": str(stop)} if stop else {})}}
    reads.ordinary[sym] = [order("tp", "sell", "LIMIT", True),
                           order("add", "buy", "LIMIT", False),
                           order("flip", "sell", "LIMIT", False),
                           order("ostop", "sell", "STOP_MARKET", True, 94.0)]
    s = _snap(journal, venue, reads)
    assert s["status"] == "VERIFIED"                  # none of these touch protection
    got = {i["id"]: (i["class"], i["severity"]) for i in s["cleanliness"]["items"]}
    assert got == {"tp": ("reduce_only_close", "info"), "add": ("entry_order", "attention"),
                   "flip": ("reversal_capable_order", "attention"),
                   "ostop": ("ordinary_stop_order", "attention")}


def test_harmless_tp_does_not_lower_protection(world):
    """An algo reduce-only TAKE_PROFIT and an ordinary reduce-only limit close."""
    journal, venue, reads, _ = world
    _protected(journal, venue)
    venue.stops.append({"algoId": "tp-1", "symbol": "BTCUSDT", "side": "SELL",
                        "reduceOnly": True, "orderType": "TAKE_PROFIT_MARKET",
                        "quantity": "1", "triggerPrice": "120"})
    reads.ordinary["BTC/USDT:USDT"] = [{"id": "lim", "side": "sell", "type": "limit",
                                        "amount": 1.0, "price": 125.0, "reduceOnly": True,
                                        "info": {"origType": "LIMIT"}}]
    s = _snap(journal, venue, reads)
    assert s["status"] == "VERIFIED" and _sym(s)["verified"] is True
    assert s["cleanliness"]["status"] == "INFO"
    assert {i["class"] for i in s["cleanliness"]["items"]} == {"reduce_only_close"}


def test_tp_only_is_unprotected(world):
    journal, venue, reads, _ = world
    _protected(journal, venue, stop={"algoId": "tp-1", "symbol": "BTCUSDT", "side": "SELL",
                                     "reduceOnly": True, "orderType": "TAKE_PROFIT_MARKET",
                                     "quantity": "1", "triggerPrice": "120"})
    s = _snap(journal, venue, reads)
    assert s["status"] == "PARTIAL" and "position_unprotected:BTC/USDT" in s["reasons"]


def test_reconciliation_equivalence_is_reported_not_applied(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    venue.positions.append({"symbol": "ETH/USDT:USDT", "contracts": 2, "side": "short",
                            "entryPrice": 3000})
    journal.add_trade(Position(id="pos_sol", symbol="SOL/USDT", side=Side.LONG, amount=3.0,
                               entry_price=150.0, notional_usdt=450.0, leverage=5,
                               stop_loss=140.0, sl_order_id="s-sol", market_type="futures"))
    s = _snap(journal, venue, reads)
    assert s["status"] == "PARTIAL" and not s["checks"]["reconciliation"]
    assert "untracked_venue_position:ETH/USDT" in s["reasons"]
    assert "journal_position_absent_on_venue:SOL/USDT" in s["reasons"]
    assert _sym(s, "ETH/USDT")["verified"] is False


# ── verification semantics (blocker 4) ──────────────────────────────────────
def test_missing_journal_quantity_never_verifies(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    with journal._tx() as c:
        c.execute("UPDATE trades SET amount = 0 WHERE id = 'pos_btc'")
    s = _snap(journal, venue, reads)
    assert s["status"] == "PARTIAL" and _sym(s)["verified"] is False
    assert "journal_quantity_unknown:BTC/USDT" in s["reasons"]


def test_journal_drift_never_verifies(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    venue.positions[0]["contracts"] = 1.5
    venue.stops = [_stop() | {"quantity": "1.5"}]
    s = _snap(journal, venue, reads)
    assert s["status"] == "PARTIAL" and "journal_size_drift:BTC/USDT" in s["reasons"]


@pytest.mark.parametrize("raw", [
    "[]", '{"BTC/USDT": []}', '{"BTC/USDT": {"order_id": "x"}}',
    '{"BTC/USDT": {"trade_id": 7}}', "not json"])
def test_malformed_rearm_evidence_never_verifies(world, raw):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    journal.kv_set(REARM_KEY, raw)
    s = _snap(journal, venue, reads)
    assert s["status"] == "PARTIAL" and _sym(s)["rearm_evidence"] == "UNREADABLE"
    assert "protection_rearm_evidence_unreadable" in s["reasons"]


def test_rearm_evidence_is_per_symbol(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    journal.kv_set(REARM_KEY, json.dumps({"BTC/USDT": {"trade_id": "other", "order_id": "9"}}))
    s = _snap(journal, venue, reads)
    assert {"protection_rearm_pending:BTC/USDT",
            "protection_rearm_evidence_mismatch:BTC/USDT"} <= set(s["reasons"])
    journal.kv_set(REARM_KEY, json.dumps({"SUI/USDT": {"trade_id": "t", "order_id": "9"}}))
    s = _snap(journal, venue, reads)
    assert s["status"] == "VERIFIED"                  # another symbol's record: cleanliness
    assert [i["class"] for i in s["cleanliness"]["items"]] == ["stale_rearm_evidence"]


def test_entry_recovery_pending_or_unreadable_never_verifies(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    journal.kv_set(RECOVERY_KEY, json.dumps({"symbol": "BTC/USDT", "phase": "submitted"}))
    s = _snap(journal, venue, reads)
    assert s["status"] == "PARTIAL" and "entry_recovery_pending" in s["reasons"]
    assert "entry_owned_exposure_pending:BTC/USDT" in _sym(s)["reasons"]
    journal.kv_set(RECOVERY_KEY, '{"no_symbol": 1}')
    s = _snap(journal, venue, reads)
    assert "critical_recovery_state_unreadable" in s["reasons"]


def test_precision_unknown_is_never_inferred(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    reads.precision = TickPrecision({})               # no exchangeInfo tick
    s = _snap(journal, venue, reads)
    r = _sym(s)
    assert s["status"] == "PARTIAL" and not s["checks"]["precision_known"]
    assert r["precision_status"] == "UNKNOWN" and r["precision_valid"] is None
    assert "price_precision_unknown:BTC/USDT" in s["reasons"]


def test_off_tick_trigger_is_invalid(world):
    journal, venue, reads, _ = world
    _protected(journal, venue, stop=_stop(trigger="95.05"))
    s = _snap(journal, venue, reads)
    assert _sym(s)["precision_status"] == "INVALID" and s["status"] == "PARTIAL"
    assert "stop_trigger_precision_invalid:BTC/USDT" in s["reasons"]


def test_no_positions_and_no_stops_is_verified(world):
    journal, venue, reads, _ = world
    s = _snap(journal, venue, reads)
    assert s["status"] == "VERIFIED" and s["position_count"] == 0


# ── coherent observation (blocker 3): A–F ───────────────────────────────────
def test_e_position_changes_between_reads_is_never_verified(world):
    """Astra's case: q=1 with no stop, then q=2 with a q=1 stop."""
    journal, venue, reads, _ = world
    _protected(journal, venue)
    venue.stops = []

    def grow():
        venue.positions = [dict(venue.positions[0], contracts=2)]
        venue.stops = [_stop()]                       # q=1 stop
    reads.after[("positions", 1)] = grow
    obs = _obs(journal, reads)
    s = ps.evaluate(obs, reads.precision, boot=1, seq=1)
    assert s["status"] == "PARTIAL" and s["checks"]["observation_consistent"] is False
    assert "observation_inconsistent:venue_positions_changed" in s["reasons"]
    # negative control: without the second position read this was R1's false VERIFIED
    mixed = dict(obs, positions_after=obs["positions_before"],
                 algo_after=obs["algo"])
    assert ps.evaluate(mixed, reads.precision, boot=1, seq=1)["status"] == "VERIFIED"


def test_f_stop_changes_between_reads_is_never_verified(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    reads.after[("algo_orders", 1)] = lambda: setattr(venue, "stops", [])
    s = _snap(journal, venue, reads)
    assert s["status"] == "PARTIAL"
    assert "observation_inconsistent:stop_listing_changed" in s["reasons"]


def test_contradictory_second_position_read_never_verifies(world):
    """Astra R2 repro: P1 one BTC long q=1; P2 two BTC rows (q=2, q=1)."""
    journal, venue, reads, _ = world
    _protected(journal, venue)

    def duplicate():
        row = venue.positions[0]
        venue.positions = [dict(row, contracts=2), dict(row)]
    reads.before[("positions", 2)] = duplicate
    s = _snap(journal, venue, reads)
    assert s["status"] != "VERIFIED"
    assert "contradictory_venue_ownership" in s["reasons"]
    assert "observation_inconsistent:venue_positions_changed" in s["reasons"]


def test_quantity_aba_between_samples_is_never_verified(world):
    """Astra R2 repro: 1 → 2 → 1 with unchanged entry price, no close/reopen."""
    journal, venue, reads, _ = world
    _protected(journal, venue)
    venue.positions[0]["updateTime"] = "1790000000000"

    def grow():
        venue.positions = [dict(venue.positions[0], contracts=2, updateTime="1790000000001")]

    def shrink():
        venue.positions = [dict(venue.positions[0], contracts=1, updateTime="1790000000002")]
    reads.after[("positions", 1)] = grow
    reads.before[("positions", 2)] = shrink
    s = _snap(journal, venue, reads)
    assert s["status"] == "PARTIAL"
    assert "observation_inconsistent:venue_positions_changed" in s["reasons"]


@pytest.mark.parametrize("raw", [
    "not-a-timestamp", -1, True, False, {}, [], "NaN", "nan", 1.79e12, "1.79e12",
    "", "0", 0, " 1790000000000", "+1790000000000", "１７９００００００００００",
    "17900000000000000", 1_000, None])
def test_malformed_update_time_never_establishes_continuity(world, raw):
    """Astra R3 repro, through the real ``_plain_position`` normalization."""
    from trader.engine.venue_reads import _plain_position
    journal, venue, reads, _ = world
    _protected(journal, venue)
    reads.positions = lambda: reads._call("positions", lambda: [_plain_position(
        {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 1.0, "entryPrice": 100.0,
         "info": {"updateTime": raw}})])
    s = _snap(journal, venue, reads)
    assert s["status"] != "VERIFIED" and s["observation"]["position_continuity"] is False
    assert any(r.startswith("observation_continuity_unproven:position_update_time_")
               for r in s["reasons"])


@pytest.mark.parametrize("raw", [1790000000000, "1790000000000"])
def test_valid_update_time_establishes_continuity(world, raw):
    from trader.engine.venue_reads import _plain_position
    journal, venue, reads, _ = world
    _protected(journal, venue)
    reads.positions = lambda: reads._call("positions", lambda: [_plain_position(
        {"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 1.0, "entryPrice": 100.0,
         "info": {"updateTime": raw}})])
    assert _snap(journal, venue, reads)["status"] == "VERIFIED"


def test_missing_update_time_means_continuity_unproven(world):
    """Negative control for the ABA case: without venue updateTime the equal
    samples of 1 → 2 → 1 are indistinguishable, so VERIFIED is withheld."""
    journal, venue, reads, _ = world
    _protected(journal, venue)
    venue.positions[0]["updateTime"] = None
    reads.after[("positions", 1)] = lambda: venue.positions.__setitem__(
        0, dict(venue.positions[0], contracts=2))
    reads.before[("positions", 2)] = lambda: venue.positions.__setitem__(
        0, dict(venue.positions[0], contracts=1))
    s = _snap(journal, venue, reads)
    assert s["status"] == "PARTIAL" and s["observation"]["positions_stable"] is True
    assert "observation_continuity_unproven:position_update_time_missing" in s["reasons"]


def _supervisor(journal, state, venue):
    from tests.test_supervisor import RISK_OK
    from trader.engine.supervisor import Supervisor
    executor = SimpleNamespace(recovery=SimpleNamespace(pending=lambda: None),
                               recover_entries=lambda: None)
    return Supervisor(journal, state, executor, venue, interval_s=0, risk_release=RISK_OK)


def test_a_snapshot_starts_then_supervisor_starts(world):
    journal, venue, reads, state = world
    _protected(journal, venue)
    sup = _supervisor(journal, state, venue)
    venue.slow = 0.4
    passes = []

    def start_pass():
        t = threading.Thread(target=lambda: passes.append(sup.pass_once()))
        t.start()
        deadline = time.monotonic() + 2
        while not sup._pass_lock.locked() and time.monotonic() < deadline:
            time.sleep(0.005)
        passes.append(t)
    reads.before[("algo_orders", 1)] = start_pass
    s = ps.evaluate(_obs(journal, reads, sup._pass_lock.locked), reads.precision,
                    boot=1, seq=1)
    passes[0].join(10)
    assert s["status"] != "VERIFIED"
    assert "observation_inconsistent:supervisor_pass_overlap" in s["reasons"]


def test_supervisor_pass_during_ordinary_reads_is_detected(world):
    """Astra R2 repro: ordinary-order reads are inside the bracket now."""
    journal, venue, reads, state = world
    _protected(journal, venue)
    sup = _supervisor(journal, state, venue)
    reads.before[("open_orders", 1)] = sup.pass_once
    s = ps.evaluate(_obs(journal, reads, sup._pass_lock.locked), reads.precision,
                    boot=1, seq=1)
    assert s["status"] != "VERIFIED" and s["checks"]["observation_consistent"] is False
    assert "observation_inconsistent:journal_changed" in s["reasons"]


def test_b_supervisor_starts_then_snapshot_defers(world):
    journal, venue, reads, state = world
    _protected(journal, venue)
    sup = _supervisor(journal, state, venue)
    monitor = _monitor(journal, reads, sup._pass_lock.locked)
    with sup._pass_lock:                              # a pass in progress
        assert monitor.run_once() is None
    assert monitor.stats["deferred_supervisor"] == 1 and reads.calls == []
    assert monitor.run_once()["status"] == "VERIFIED"


def test_c_owner_recovery_mid_snapshot_is_never_verified(world):
    from trader.engine.supervisor import OwnerContext
    journal, venue, reads, state = world
    _protected(journal, venue)
    state.set(ControlState.FROZEN, "operator", "hold")
    sup = _supervisor(journal, state, venue)
    ctx = OwnerContext(actor="operator", channel="test")
    reads.before[("algo_orders", 1)] = lambda: sup.request_owner_recovery(ctx)
    s = ps.evaluate(_obs(journal, reads, sup._pass_lock.locked), reads.precision,
                    boot=1, seq=1)
    assert s["status"] != "VERIFIED"
    assert "observation_inconsistent:journal_changed" in s["reasons"]


def test_d_reconciliation_mid_snapshot_is_never_verified(world):
    """A mid-snapshot re-arm: without the journal bracket this reads VERIFIED."""
    journal, venue, reads, _ = world
    _protected(journal, venue)
    venue.stops = []
    reads.before[("algo_orders", 1)] = lambda: reconcile_futures(venue, journal, verify=True)
    obs = _obs(journal, reads)
    s = ps.evaluate(obs, reads.precision, boot=1, seq=1)
    assert venue.mutations and venue.mutations[0][0] == "create_order"   # the reconcile, not us
    assert s["status"] != "VERIFIED"
    assert "observation_inconsistent:journal_changed" in s["reasons"]
    mixed = dict(obs, journal_after=obs["journal_before"])            # negative control
    assert ps.evaluate(mixed, reads.precision, boot=1, seq=1)["status"] == "VERIFIED"


# ── control state unchanged in every state ──────────────────────────────────
@pytest.mark.parametrize("target,actor", [
    (ControlState.FROZEN, "operator"), (ControlState.HALTED, "operator"),
    (ControlState.RECOVERY, "supervisor"), (ControlState.ACTIVE, None)])
def test_i_to_l_every_state_unchanged(world, target, actor):
    journal, venue, reads, state = world
    _protected(journal, venue)
    venue.stops = []                                  # a fault a repair path would act on
    if target == ControlState.RECOVERY:
        state.set(ControlState.FROZEN, "supervisor", "contain")
    if target == ControlState.ACTIVE:
        state.set(ControlState.FROZEN, "operator", "hold")
        state.set(ControlState.ACTIVE, "operator", "resume")
    elif actor:
        state.set(target, actor, "hold under test")
    before = _db_state(journal)
    monitor = _monitor(journal, reads)
    snap = monitor.run_once()
    assert snap["control_state_observed"] == target.value and snap["status"] == "PARTIAL"
    assert _db_state(journal) == before and venue.mutations == []
    assert state.refresh() == target
    assert _stored(journal)["value"]["generation"] == snap["generation"]


# ── capability structure (blocker 1) ────────────────────────────────────────
FORBIDDEN_NAMES = ("Journal", "Supervisor", "ControlStateMachine", "Executor", "EntryRecovery",
                   "Kernel", "ExitEngine", "RiskManager", "RecordingVenue", "Venue")
MUTATOR_NAMES = ("create_order", "cancel_order", "edit_order", "cancel_all_orders",
                 "fapiPrivateDeleteAlgoOrder", "set_leverage", "kv_set", "close_trade",
                 "pass_once", "request_owner_recovery", "set", "apply", "trigger")


def reachable(root, limit=400_000):
    """Every object reachable from ``root`` through attributes, slots, containers,
    closures, defaults, bound-method ``__self__``/``__func__``, partials and
    ``__wrapped__`` — the escapes Astra used. Modules, classes and function
    globals are not traversed (they hold no instance authority)."""
    import types
    seen, stack, out = set(), [root], []
    while stack and len(seen) < limit:
        o = stack.pop()
        if id(o) in seen or isinstance(o, (types.ModuleType, type, str, bytes, int, float,
                                           bool, type(None))):
            continue
        seen.add(id(o))
        out.append(o)
        if isinstance(o, dict):
            stack.extend(o.keys())
            stack.extend(o.values())
        elif isinstance(o, (list, tuple, set, frozenset)):
            stack.extend(o)
        if isinstance(o, types.MethodType):
            stack.extend((o.__self__, o.__func__))
        elif isinstance(o, types.BuiltinMethodType) and getattr(o, "__self__", None) is not None:
            stack.append(o.__self__)
        if isinstance(o, types.FunctionType):
            stack.extend(c.cell_contents for c in (o.__closure__ or ())
                         if _cell_ok(c))
            stack.extend(o.__defaults__ or ())
            stack.extend((o.__kwdefaults__ or {}).values())
        if isinstance(o, functools.partial):
            stack.extend((o.func, o.args, o.keywords))
        wrapped = getattr(o, "__wrapped__", None) if not isinstance(o, dict) else None
        if wrapped is not None:
            stack.append(wrapped)
        d = getattr(o, "__dict__", None)
        if isinstance(d, dict):
            stack.append(d)
        for klass in type(o).__mro__:
            for slot in getattr(klass, "__slots__", ()) or ():
                if isinstance(slot, str) and hasattr(o, slot):
                    try:
                        stack.append(getattr(o, slot))
                    except Exception:
                        pass
    return out


def _cell_ok(cell):
    try:
        cell.cell_contents
        return True
    except ValueError:
        return False


TRADING_KEY, TRADING_SECRET = "tk", "ts"          # tests.test_venue_reads.Env


def violations(root):
    from trader.engine.venue_reads import GetOnlySession
    bad = []
    for o in reachable(root):
        name = type(o).__name__
        if name in FORBIDDEN_NAMES or isinstance(o, sqlite3.Connection):
            bad.append(name)
        elif name in ("SnapshotStore", "ProtectionMonitor"):
            bad.append("publisher:" + name)
        elif isinstance(o, ccxt.Exchange) and (getattr(o, "secret", None) == TRADING_SECRET
                                               or getattr(o, "apiKey", None) == TRADING_KEY):
            bad.append("trading_credentials_reachable")
        elif any(callable(getattr(type(o), m, None)) for m in MUTATOR_NAMES[:6]):
            # an exchange-like object: only the dedicated GET-only client may exist
            if not (isinstance(o, ccxt.Exchange) and isinstance(o.session, GetOnlySession)):
                bad.append("unguarded_exchange:" + name)
    return bad


def _real_observer(journal, busy):
    from tests.test_venue_reads import Env, Kernel
    from trader.engine.venue_reads import make_venue_reads
    reads = make_venue_reads(Kernel(), env=Env(read_key=True))
    return ps.Observer(reads, ps.JournalReads(journal.db_path), busy)


def test_snapshot_dependencies_have_no_mutation_authority(world):
    journal, venue, _, state = world
    sup = _supervisor(journal, state, venue)
    observer = _real_observer(journal, sup._pass_lock.locked)
    assert violations(observer) == []
    assert not hasattr(observer.reads, "_ex") and not hasattr(observer, "exchange")
    for attr in ("reads", "journal", "supervisor_busy"):
        with pytest.raises(AttributeError):
            setattr(observer, attr, None)
    # the kernel-built monitor holds the same observer; the store is its only write path
    monitor = ps.ProtectionMonitor(observer, _store(journal))
    assert violations(monitor.observer) == []


def test_journal_reads_cannot_write(world):
    journal, *_ = world
    conn = ps.ro_connect(journal.db_path)
    for sql in ("INSERT INTO state_kv(key, value) VALUES ('x', 'y')",
                "UPDATE trades SET status = 'closed'", "DELETE FROM state_kv",
                "PRAGMA query_only = OFF", f"ATTACH DATABASE '{journal.db_path}' AS rw",
                "CREATE TABLE evil(x)"):
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(sql)
    conn.close()


# negative controls: the structural detector catches each escape Astra used
def test_nc_exchange_object_exposed_through_snapshot(world):
    journal, venue, _, _ = world
    assert violations(ps.Observer(venue, ps.JournalReads(journal.db_path)))


def test_nc_bound_method_self_escape(world):
    journal, venue, _, state = world
    sup = _supervisor(journal, state, venue)
    assert "Supervisor" in violations(
        ps.Observer(None, ps.JournalReads(journal.db_path), sup.pass_once))
    reads = SimpleNamespace(positions=venue.fetch_positions)    # read bound to the venue
    assert violations(ps.Observer(reads, ps.JournalReads(journal.db_path)))


def test_nc_read_alias_points_to_order_creation(world):
    journal, venue, _, _ = world
    reads = SimpleNamespace(positions=venue.create_order)
    assert violations(ps.Observer(reads, ps.JournalReads(journal.db_path)))


def test_nc_mutation_capable_callback_and_wrapped_read(world):
    journal, venue, _, _ = world
    cb = lambda: journal.kv_set("control_state", "ACTIVE") or False     # noqa: E731
    assert "Journal" in violations(ps.Observer(None, ps.JournalReads(journal.db_path), cb))

    def read():
        return []

    @functools.wraps(read)
    def wrapped():
        venue.cancel_order("1", "BTC/USDT")
        return read()
    assert violations(ps.Observer(SimpleNamespace(positions=wrapped),
                                  ps.JournalReads(journal.db_path)))


# ── persistence identity / ordering (blocker 6) ─────────────────────────────
def test_generation_order_newest_wins_equal_rejected(world):
    journal, *_ = world
    store = _store(journal)
    boot = store.allocate_boot()
    s = lambda seq: ps.unreadable(boot, seq, "x")            # noqa: E731
    assert store.publish(s(2), boot=boot, seq=2, max_seq=3)
    assert not store.publish(s(2), boot=boot, seq=2, max_seq=3)      # equal
    assert not store.publish(s(1), boot=boot, seq=1, max_seq=3)      # older
    assert store.publish(s(3), boot=boot, seq=3, max_seq=3)
    boot2 = store.allocate_boot()
    assert boot2 > boot
    assert store.publish(ps.unreadable(boot2, 1, "x"), boot=boot2, seq=1, max_seq=1)
    assert not store.publish(s(9), boot=boot, seq=9, max_seq=9)      # older boot


def test_malformed_stored_identity_never_blocks(world):
    journal, *_ = world
    store = _store(journal)
    boot = store.allocate_boot()
    # (a) a row claiming a boot that was never allocated
    with journal._tx() as c:
        c.execute(f"INSERT INTO {ps.TABLE}(slot, boot, seq, value) VALUES (1, ?, 5, '{{}}')",
                  (10 ** 15,))
    assert store.publish(ps.unreadable(boot, 1, "x"), boot=boot, seq=1, max_seq=1)
    # (b) this boot, but a seq this process never issued
    with journal._tx() as c:
        c.execute(f"UPDATE {ps.TABLE} SET seq = ?", (10 ** 15,))
    assert store.publish(ps.unreadable(boot, 2, "x"), boot=boot, seq=2, max_seq=2)
    # (c) a legacy/malformed table without type checks: text identity, bad JSON
    with journal._tx() as c:
        c.execute(f"DROP TABLE {ps.TABLE}")
        c.execute(f"CREATE TABLE {ps.TABLE}(slot INTEGER PRIMARY KEY, boot, seq, value)")
        c.execute(f"INSERT INTO {ps.TABLE} VALUES (1, 'zzz', 'zzz', 'not json')")
    assert store.publish(ps.unreadable(boot, 3, "x"), boot=boot, seq=3, max_seq=3)
    assert store.read()["seq"] == 3


def test_wall_clock_rollback_does_not_block_new_evidence(world, monkeypatch):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    first = _monitor(journal, reads).run_once()
    assert first["status"] == "VERIFIED"
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() - 3600)       # clock moved back 1 h
    second_monitor = _monitor(journal, reads)                      # a restart
    second = second_monitor.run_once()
    stored = _stored(journal)
    assert stored["value"]["generation"] == second["generation"]
    assert second["generation"]["boot"] > first["generation"]["boot"]
    snap, _ = owner_api.read_protection_snapshot(
        journal, datetime.fromtimestamp(time.time(), timezone.utc))
    assert snap["freshness"] == "fresh" and snap["status"] == "VERIFIED"


# ── publication failure / late workers / contention (blockers 5, 7, 8) ──────
def _hold_write_lock(path, seconds, ready):
    conn = sqlite3.connect(str(path), timeout=5, isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    ready.set()
    time.sleep(seconds)
    conn.execute("COMMIT")
    conn.close()


def test_failed_timeout_publication_fails_closed_and_late_worker_is_rejected(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    monitor = _monitor(journal, reads, timeout_s=0.3)
    ok = monitor.run_once()
    assert ok["status"] == "VERIFIED"
    reads.hang = threading.Event()                    # the next check hangs in the venue
    ready = threading.Event()
    holder = threading.Thread(target=_hold_write_lock, args=(journal.db_path, 1.5, ready))
    holder.start()
    ready.wait(2)
    t0 = time.monotonic()
    timed_out = monitor.run_once()                   # UNREADABLE cannot be published
    elapsed = time.monotonic() - t0
    assert timed_out["reasons"] == ["venue_timeout"]
    assert elapsed < 0.3 + 0.3 + 1.0                 # timeout + publish bound + slack
    assert monitor.stats["publish_failed"] >= 1
    holder.join(5)
    stored = _stored(journal)["value"]
    assert stored["generation"] == ok["generation"]     # the old evidence stands …
    at = datetime.fromisoformat(stored["checked_at"])
    snap, _ = owner_api.read_protection_snapshot(journal, at + timedelta(seconds=121))
    assert snap["freshness"] == "stale"                # … and ages to STALE on its own time
    reads.hang.set()                                   # the hung worker finally returns
    deadline = time.monotonic() + 5
    while monitor._worker.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.3)                                    # give any stray commit a chance
    monitor.collect_late()
    assert monitor.stats["late_discarded"] == 1
    assert _stored(journal)["value"]["generation"] == ok["generation"]


def test_astra_race_timeout_during_publication_cannot_commit_old_generation(world):
    """Astra R2 repro: a result must never commit after its timeout decision.

    The worker cannot publish at all (no store), so 'worker waits on SQLite,
    times out, then commits' has no path. A result finished in time is
    published by the monitor thread; if the DB is locked that publication
    fails boundedly and nothing commits later."""
    journal, venue, reads, _ = world
    _protected(journal, venue)
    monitor = _monitor(journal, reads, timeout_s=0.5)
    first = monitor.run_once()
    ready = threading.Event()
    holder = threading.Thread(target=_hold_write_lock, args=(journal.db_path, 1.5, ready))
    holder.start()
    ready.wait(2)
    snap = monitor.run_once()                          # finished in time; publication blocked
    assert snap["status"] == "VERIFIED" and monitor.stats["publish_failed"] == 1
    holder.join(5)
    time.sleep(0.3)
    assert _stored(journal)["value"]["generation"] == first["generation"]
    # a result finishing *after* the deadline is discarded even with a free DB
    reads.after[("algo_orders", 6)] = lambda: time.sleep(0.8)   # 3rd check, 2nd listing
    timed_out = monitor.run_once()
    assert timed_out["reasons"] == ["venue_timeout"]
    deadline = time.monotonic() + 5
    while monitor._worker.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)
    monitor.collect_late()
    stored = _stored(journal)["value"]
    assert stored["generation"] == timed_out["generation"] and monitor.stats["late_discarded"] == 1


def test_delayed_monitor_never_accepts_a_result_completed_after_the_deadline(world):
    """Astra R3 repro, deterministic: the final venue read completes after the
    deadline while the monitor is (in effect) descheduled. The worker's own
    completion time decides — the result is discarded and timeout evidence
    is published instead."""
    journal, venue, reads, _ = world
    _protected(journal, venue)
    clock = FakeClock()
    monitor = _monitor(journal, reads, timeout_s=0.06, clock=clock)
    first = monitor.run_once()                         # a normal in-time check
    assert first["status"] == "VERIFIED"
    reads.after[("open_orders", 2)] = lambda: clock.advance(0.065)   # completes at ~T+65 ms
    snap = monitor.run_once()
    assert snap["reasons"] == ["venue_timeout"] and monitor.stats["timeouts"] == 1
    assert _stored(journal)["value"]["generation"] == snap["generation"]
    assert _stored(journal)["value"]["status"] == "UNREADABLE"
    monitor.collect_late()
    assert monitor.stats["late_discarded"] == 1
    # a result completed inside the deadline is still accepted
    reads.after[("open_orders", 3)] = lambda: clock.advance(0.05)
    assert monitor.run_once()["status"] == "VERIFIED"


def test_worker_has_no_path_to_the_publisher(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    monitor = _monitor(journal, reads)
    reads.hang = threading.Event()
    t = threading.Thread(target=monitor.run_once)
    t.start()
    deadline = time.monotonic() + 2
    while (monitor._worker is None or not monitor._worker.is_alive()) and time.monotonic() < deadline:
        time.sleep(0.01)
    w = monitor._worker
    assert [v for v in violations((w._target, w._args, w._kwargs)) if v.startswith("publisher")] == []
    # negative control: R2's worker (a bound monitor method) reaches the store
    assert "publisher:ProtectionMonitor" in violations(monitor.run_once)
    monitor.stop()
    reads.hang.set()
    t.join(5)


def test_journal_write_lock_never_blocks_the_kernel_beyond_bound(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    monitor = _monitor(journal, reads, store=_store(journal, timeout_s=0.25))
    ready, release = threading.Event(), threading.Event()

    def kernel_writer():                              # production-like: Journal._tx held
        with journal._tx() as c:
            c.execute("INSERT OR REPLACE INTO state_kv(key, value) VALUES ('cycle', '1')")
            ready.set()
            release.wait(5)
    w = threading.Thread(target=kernel_writer)
    w.start()
    ready.wait(2)
    t0 = time.monotonic()
    snap = monitor.run_once()
    elapsed = time.monotonic() - t0
    release.set()
    w.join(5)
    assert snap["status"] == "VERIFIED" and elapsed < 1.5
    assert monitor.stats["publish_failed"] == 1 and _stored(journal) is None
    # the monitor never holds the Journal's in-process write lock, so a kernel
    # cycle write proceeds while the monitor publishes continuously
    stop = threading.Event()

    def spin():
        while not stop.is_set():
            monitor.run_once()
    spinner = threading.Thread(target=spin)
    spinner.start()
    worst = 0.0
    for i in range(30):
        t = time.monotonic()
        journal.kv_set("cycle", str(i))
        worst = max(worst, time.monotonic() - t)
    stop.set()
    spinner.join(10)
    assert worst < 0.5 and not spinner.is_alive()


# ── cadence, single flight, timeout, shutdown (blockers 9, 13) ──────────────
class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            return self.t

    def advance(self, dt):
        with self.lock:
            self.t += dt


def _cadence(check_s, n_starts, interval=60.0):
    tmp = Path(__import__("tempfile").mkdtemp())
    journal = Journal(tmp / "j.db")
    venue = Venue()
    reads = Reads(venue)
    clock = FakeClock()
    reads.before[("positions", 1)] = lambda: clock.advance(check_s)
    monitor = _monitor(journal, reads, interval_s=interval, timeout_s=1e6, clock=clock,
                       wait=lambda dt: clock.advance(max(dt, 0.001)))

    def spy(orig=monitor._run):
        reads.calls.clear()
        return orig()
    monitor._run = spy
    monitor.loop(stopped=lambda: len(monitor.starts) >= n_starts, tick_s=1.0)
    return list(monitor.starts), monitor.stats


def test_cadence_is_fixed_start_not_completion_plus_interval():
    starts, _ = _cadence(check_s=20.0, n_starts=5)
    assert starts == [0.0, 60.0, 120.0, 180.0, 240.0]            # not 0, 80, 160 …


def test_long_check_skips_slots_without_a_burst():
    starts, stats = _cadence(check_s=130.0, n_starts=3)
    assert starts == [0.0, 180.0, 360.0] and stats["skipped_slots"] == 6   # 2 per check
    assert all(b - a >= 60.0 for a, b in zip(starts, starts[1:]))


def test_nc_completion_plus_interval_would_drift(monkeypatch):
    """The detector: R1's completion + interval schedule fails the cadence test."""
    def completion_plus_interval(self):
        self._slot = self.clock() + self.interval_s
        self._retry_at = 0.0
    monkeypatch.setattr(ps.ProtectionMonitor, "_advance", completion_plus_interval)
    starts, _ = _cadence(check_s=20.0, n_starts=5)
    assert starts == [0.0, 80.0, 160.0, 240.0, 320.0]
    assert starts != [0.0, 60.0, 120.0, 180.0, 240.0]


def test_hung_venue_single_flight_no_accumulation_bounded_shutdown(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    reads.hang = threading.Event()
    monitor = _monitor(journal, reads, interval_s=0.05, timeout_s=0.1, defer_s=0.01)
    before = threading.active_count()
    loop = threading.Thread(target=monitor.loop, kwargs={"tick_s": 0.01})
    loop.start()
    time.sleep(1.0)
    alive = sum(t.name == "protection-snapshot-check" and t.is_alive()
                for t in threading.enumerate())
    t0 = time.monotonic()
    monitor.stop()
    loop.join(2)
    assert not loop.is_alive() and time.monotonic() - t0 < 0.5      # bounded shutdown
    assert alive <= 1 and reads.max_inflight == 1 and len(reads.calls) == 1
    assert monitor.stats["timeouts"] >= 1 and monitor.stats["blocked_by_running_check"] >= 1
    assert _stored(journal)["value"]["status"] == "UNREADABLE"
    reads.hang.set()
    time.sleep(0.2)
    assert threading.active_count() <= before + 1
    monitor.collect_late()
    assert monitor.stats["late_discarded"] == 1


def test_shutdown_interrupts_a_running_check(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    reads.hang = threading.Event()
    monitor = _monitor(journal, reads, timeout_s=30)
    t = threading.Thread(target=monitor.run_once)
    t.start()
    time.sleep(0.1)
    t0 = time.monotonic()
    monitor.stop()
    t.join(2)
    assert not t.is_alive() and time.monotonic() - t0 < 0.5
    assert monitor.stats["stopped_mid_check"] == 1
    reads.hang.set()
    time.sleep(0.1)
    monitor.collect_late()
    assert monitor.stats["late_discarded"] == 1 and _stored(journal) is None


def test_concurrent_run_once_is_single_flight(world):
    journal, venue, reads, _ = world
    _protected(journal, venue)
    monitor = _monitor(journal, reads)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(lambda _: monitor.run_once(), range(8)))
    assert reads.max_inflight == 1


def test_owner_api_never_reaches_the_venue(tmp_path, monkeypatch):
    """Booby-trap every venue path; owner routes still answer from the journal."""
    from fastapi.testclient import TestClient
    from tests.owner_frontend_fixture import TOKEN, make_app
    from trader.engine import protective
    import ccxt as _ccxt

    def boom(*a, **k):
        raise AssertionError("venue reached from the owner API")
    for mod, name in ((ps, "observe"), (ps, "evaluate"), (ps.ProtectionMonitor, "run_once"),
                      (protective, "open_stops"),
                      (_ccxt.binanceusdm, "fetch_positions"),
                      (_ccxt.binanceusdm, "fetch_open_orders")):
        monkeypatch.setattr(mod, name, boom)
    app, *_ = make_app(tmp_path, monkeypatch)
    c = TestClient(app)
    h = {"x-luffy-token": TOKEN}
    for route in ("overview", "protection", "system", "trades", "bootstrap"):
        r = c.get(f"/owner-api/v1/{route}", headers=h)
        assert r.status_code == 200, (route, r.text[:200])
    assert c.get("/owner-api/v1/protection", headers=h).json()["snapshot"]["status"] == "VERIFIED"


# ── historical compatibility (captured evidence, no venue call) ─────────────
EVIDENCE = Path(__file__).resolve().parent.parent / (
    "docs/superpowers/reports/2026-09-28-candle-store-transaction-safety-deployment-v1-"
    "evidence/venue_active_final.json")


def _decimals(x: float) -> int:
    text = repr(float(x))
    return len(text.split(".")[1].rstrip("0")) if "." in text else 0


@pytest.mark.skipif(not EVIDENCE.exists(), reason="captured evidence absent")
def test_captured_2026_09_28_state_is_compatible_but_not_live_status(tmp_path):
    """Compatibility only: the 2026-09-28 capture (8 longs, 8 native algo stops).

    Without exchangeInfo ticks precision is UNKNOWN and nothing verifies — the
    snapshot no longer infers precision. With ticks *inferred from the echoed
    triggers* (labelled; not authoritative) the rows are accepted. The capture
    has no position updateTime either: the continuity timestamps here are
    *synthetic*, supplied by the test's Reads fixture, so this replay does not
    show that the historical capture satisfies the continuity requirement.
    This is not current live status (NOT_ESTABLISHED)."""
    cap = json.loads(EVIDENCE.read_text())
    journal = Journal(tmp_path / "j.db")
    venue = Venue()
    ticks = {}
    for i, r in enumerate(cap["rows"]):
        sym = f"{r['sym'][:-4]}/USDT"
        journal.add_trade(Position(id=f"cap_{i}", symbol=sym, side=Side.LONG,
                                   amount=r["journal_amt"], entry_price=1.0,
                                   notional_usdt=r["journal_amt"], leverage=5,
                                   stop_loss=r["journal_sl"], sl_order_id=r["journal_sl_id"],
                                   market_type="futures"))
        venue.positions.append({"symbol": sym + ":USDT", "contracts": r["amt"],
                                "side": r["side"], "entryPrice": 1.0})
        for sid, trig in zip(r["venue_stop_ids"], r["venue_triggers"]):
            venue.stops.append({"algoId": sid, "symbol": r["sym"], "side": "SELL",
                                "reduceOnly": "true", "orderType": "STOP_MARKET",
                                "quantity": str(r["amt"]), "triggerPrice": str(trig)})
            ticks[r["sym"]] = format(10 ** -_decimals(trig), "f")
    s = _snap(journal, venue, Reads(venue, ticks={}))
    assert s["status"] == "PARTIAL" and not s["checks"]["precision_known"]
    s = _snap(journal, venue, Reads(venue, ticks=ticks))
    assert s["status"] == "VERIFIED", s["reasons"]
    assert s["position_count"] == cap["venue_positions"] == 8
    assert s["venue_requests"] == 4 + 8


def test_kernel_wiring_builds_a_dedicated_read_only_observer(tmp_path, monkeypatch):
    """Kernel._start_protection_monitor: dedicated GET-only client, read-only
    journal, the pass lock's ``locked`` — never the kernel's exchange. The loop
    is stubbed so no request (let alone a live one) is made."""
    from tests.test_venue_reads import Env, Kernel as KernelExchange
    from trader import kernel as kmod
    from trader.core import config
    from trader.core.types import MarketType
    from trader.engine.venue_reads import VenueReads
    monkeypatch.setattr(config, "Env", Env(read_key=True))
    started = []
    monkeypatch.setattr(ps.ProtectionMonitor, "loop", lambda self, **kw: started.append(kw))
    journal = Journal(tmp_path / "j.db")
    exchange = KernelExchange()
    lock = threading.Lock()
    k = SimpleNamespace(market_type=MarketType.FUTURES, cfg={}, exchange=exchange,
                        journal=journal, supervisor=SimpleNamespace(_pass_lock=lock),
                        _stop=False)
    kmod.Kernel._start_protection_monitor(k)
    time.sleep(0.05)
    m = k.protection_monitor
    assert isinstance(m.observer.reads, VenueReads) and m.observer.reads._client is not exchange
    assert m.observer.supervisor_busy.__self__ is lock
    assert (m.interval_s, m.timeout_s) == (60.0, 20.0) and started
    assert violations(m.observer) == [] and exchange.mutations == []
    off = SimpleNamespace(market_type=MarketType.FUTURES,
                          cfg={"protection_snapshot": {"enabled": False}})
    kmod.Kernel._start_protection_monitor(off)
    assert not hasattr(off, "protection_monitor")


def test_without_read_only_key_the_monitor_publishes_unreadable(tmp_path, monkeypatch):
    """The trading key is never used for the snapshot. No read key → the
    reader is not built and every check publishes the reason."""
    from tests.test_venue_reads import Env, Kernel as KernelExchange
    journal = Journal(tmp_path / "j.db")
    m = ps.make_monitor(KernelExchange(), journal.db_path, supervisor_busy=lambda: False,
                        env=Env())
    assert m.observer.reads is None
    snap = m.run_once()
    assert snap["status"] == "UNREADABLE"
    assert snap["reasons"] == ["venue_reader_unavailable:read_only_api_key_not_configured"]
    assert _stored(journal)["value"]["generation"] == snap["generation"]
