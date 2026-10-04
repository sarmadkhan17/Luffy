"""Kernel.boot()/run() integration with the real Supervisor recovery pass.

Regression for the boot notification reading a field RecoveryResult does not
have: any boot whose recovery pass reported a reason raised AttributeError
after RECOVERY was persisted, before workers started, so every watchdog
restart crashed the same way. Each case drives the real Kernel.boot() with a
real Journal, ControlStateMachine, Executor and Supervisor against a
recording venue; only threads, signals and the stall monitor are recorded
instead of started.
"""
import json
from types import SimpleNamespace

import ccxt
import pytest

from trader.core.journal import Journal
from trader.core.types import (Action, ControlState, Decision, MarketType,
                               Position, Side)
from trader.engine.executor import Executor
from trader.engine.risk import RiskManager
from trader.engine.state import ControlStateMachine
from trader.engine.supervisor import KEY, Supervisor

WORKERS = {"tg-listener", "derivs-recorder", "strategy-mechanism",
           "agent-validator"}


class RecordingVenue:
    """Replays venue truth; records every call that could change it."""

    def __init__(self):
        self.positions = []
        self.stops = []
        self.unreadable = False
        self.order_status_unreadable = False
        self.algo_listing_invalid = False
        self.mutations = []
        self.markets = {}
        self._next = 900
        self.equity = 1000.0      # venue-reported equity; None = read fails

    def load_markets(self):
        return self.markets

    def fetch_positions(self):
        if self.unreadable:
            raise ccxt.RequestTimeout("venue timeout")
        return [dict(p) for p in self.positions]

    def fapiPrivateGetOpenAlgoOrders(self):
        if self.unreadable:
            raise ccxt.RequestTimeout("venue timeout")
        if self.algo_listing_invalid:
            return {"code": -1, "msg": "partial"}   # neither a list nor {orders: [...]}
        return [dict(s) for s in self.stops]

    def fetch_open_orders(self, symbol):
        assert symbol is not None
        return []

    def fetch_order(self, order_id, symbol, params=None):
        if self.order_status_unreadable:
            raise ccxt.RequestTimeout("order status timeout")
        return {"id": order_id, "status": "closed", "filled": 0}

    # -- mutations -----------------------------------------------------------
    def create_order(self, symbol, type, side, amount, price=None, params=None):
        self._next += 1
        self.mutations.append(("create_order", symbol, side, amount, dict(params or {})))
        if params and "stopLossPrice" in params:
            self.stops.append({"algoId": str(self._next), "symbol": symbol.replace("/", ""),
                               "side": side.upper(), "reduceOnly": True,
                               "orderType": "STOP_MARKET", "quantity": str(amount),
                               "triggerPrice": str(params["stopLossPrice"])})
        return {"id": str(self._next)}

    def cancel_order(self, order_id, symbol=None):
        self.mutations.append(("cancel_order", order_id, symbol))
        return {}

    def fapiPrivateDeleteAlgoOrder(self, params):
        self.mutations.append(("cancel_algo", str(params["algoId"])))
        self.stops = [s for s in self.stops if str(s["algoId"]) != str(params["algoId"])]
        return {}

    def set_leverage(self, leverage, symbol):
        self.mutations.append(("set_leverage", leverage, symbol))


def _trade(sl=95.0, sl_order_id="stop-1"):
    return Position(id="pos_btc", symbol="BTC/USDT", side=Side.LONG, amount=1.0,
                    entry_price=100.0, notional_usdt=100.0, leverage=5,
                    stop_loss=sl, sl_order_id=sl_order_id, market_type="futures")


def _stop(side="SELL", trigger="95", algo_id="stop-1"):
    return {"algoId": algo_id, "symbol": "BTCUSDT", "side": side,
            "reduceOnly": True, "orderType": "STOP_MARKET",
            "quantity": "1", "triggerPrice": trigger}


def _protected(journal, venue, *, stop=None):
    journal.add_trade(_trade())
    venue.positions = [{"symbol": "BTC/USDT", "contracts": 1, "side": "long",
                        "entryPrice": 100, "markPrice": 100}]
    venue.stops = [stop or _stop()]


RISK_CFG = {"risk": {"risk_per_trade_pct": 1.0, "portfolio_heat_cap_pct": 15,
                     "per_symbol_risk_cap_pct": 8, "max_open_positions": 8,
                     "max_daily_loss_pct": 6, "halt_drawdown_pct": 20,
                     "leverage": 5, "stop_loss_atr_mult": 2.0,
                     "min_notional_usdt": 5}}


def _kernel(journal, venue, monkeypatch):
    from trader.kernel import Kernel
    state = ControlStateMachine(journal)
    executor = Executor(venue, journal,
                        {"risk": {"leverage": 5, "take_profit_atr_mult": 3.0}},
                        MarketType.FUTURES)
    k = object.__new__(Kernel)
    k.cfg = {"timeframes": {"scan_interval_seconds": 60},
             "references": {"enabled": False}}
    k.market_type = MarketType.FUTURES
    k.journal = journal
    k.exchange = venue
    from trader.data.feed import DataFeed
    k.feed = DataFeed(exchange=RecordingVenue(), db_path=journal.db_path.with_name("candles.db"))
    assert k.feed.ex is not venue  # boot initializes independent public data identity
    k.state_machine = state
    k.executor = executor
    # Real Risk wiring: RiskManager + Kernel._risk_release; only the venue
    # equity read is replaced (no credentials, no network).
    k.risk = RiskManager(RISK_CFG, journal)
    if journal.kv_get("risk_state") is None and venue.equity:
        k.risk.update_equity(venue.equity)        # the first kernel cycle's baseline
    monkeypatch.setattr(k, "_fetch_balance_fresh", lambda: venue.equity, raising=False)
    k.supervisor = Supervisor(journal, state, executor, venue, interval_s=0,
                              risk_release=k._risk_release)
    k.population = []
    k.universe = SimpleNamespace(symbols=lambda: [], _alts=[], majors=[])
    k.heartbeat = object()
    k._stop = False
    sent = []
    k.notifier = SimpleNamespace(send=sent.append)
    started = []
    monkeypatch.setattr("trader.kernel.start_stall_monitor",
                        lambda *a, **kw: started.append("stall-monitor"))
    monkeypatch.setattr("trader.kernel.signal.signal", lambda *a, **kw: None)
    monkeypatch.setattr(
        "trader.kernel.threading.Thread",
        lambda *a, **kw: SimpleNamespace(start=lambda: started.append(kw.get("name"))))
    return k, sent, started


def _transitions(journal, after=0):
    return [(r["from_state"], r["to_state"], r["actor"]) for r in journal.query(
        "SELECT from_state, to_state, actor FROM control_events "
        "WHERE event='state_change' AND id>? ORDER BY id", (after,))]


def _last_event(journal):
    return int(journal.query("SELECT COALESCE(MAX(id),0) AS id FROM control_events")[0]["id"])


def _status(journal):
    return json.loads(journal.kv_get(KEY))


def _entry_probe(k):
    """Offer a real entry to the real Executor; only the fence may stop it."""
    from tests.authority_legacy_fixtures import seed
    if not k.journal.query("SELECT 1 FROM strategies WHERE id='s'"):
        seed(k.journal, 's')
    else:
        from trader.strategy import legacy_authority as L
        from tests.authority_legacy_fixtures import grant
        if not k.journal.query(f"SELECT 1 FROM {L.TABLE} WHERE strategy_id='s'"):
            grant(k.journal, 's')
    d = Decision("d-probe", "c-probe", "ETH/USDT", Action.BUY, .7, .2, .8, [], [])
    before = len([m for m in k.exchange.mutations if m[0] == "create_order"])
    pos = k.executor.open(d, 1.0, 1.0, 90.0, 110.0, "s", "s")
    after = len([m for m in k.exchange.mutations if m[0] == "create_order"])
    return pos, d.skip_reason, after - before


@pytest.fixture
def world(tmp_path):
    return Journal(tmp_path / "j.db"), RecordingVenue()


# ── A. clean boot ──────────────────────────────────────────────────────────
def test_a_clean_boot_proves_and_activates_without_venue_mutation(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    k, sent, started = _kernel(journal, venue, monkeypatch)
    k._stop = True                       # run() = boot() then no cycles
    k.run()
    assert [t[:2] for t in _transitions(journal)] == [
        ("ACTIVE", "FROZEN"), ("FROZEN", "RECOVERY"), ("RECOVERY", "ACTIVE")]
    assert k.state_machine.refresh() == ControlState.ACTIVE
    st = _status(journal)
    assert st["outcome"] == "SAFE" and st["reasons"] == [] and st["safe_to_activate"]
    assert venue.mutations == []
    assert not any("recovery" in m for m in sent)
    assert WORKERS <= set(started) and "stall-monitor" in started


# ── B. venue unreadable ────────────────────────────────────────────────────
def test_b_venue_timeout_contains_notifies_and_boots(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    venue.unreadable = True
    k, sent, started = _kernel(journal, venue, monkeypatch)
    k._stop = True
    k.run()                              # formerly AttributeError here
    assert k.state_machine.refresh() == ControlState.RECOVERY
    st = _status(journal)
    assert st["outcome"] == "NEEDS_OWNER" and st["needs_owner"]
    assert "venue_state_unreadable" in st["reasons"]
    assert sent == ["🔒 recovery NEEDS_OWNER: " + ", ".join(st["reasons"])]
    assert WORKERS <= set(started) and "stall-monitor" in started
    assert _entry_probe(k)[1:] == ("state=RECOVERY: entries blocked", 0)
    assert not [m for m in venue.mutations if m[0] != "set_leverage"]


# ── C. persisted entry intent, order status unreadable ─────────────────────
def test_c_pending_entry_with_unreadable_order_status(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    k, sent, started = _kernel(journal, venue, monkeypatch)
    intent = k.executor.recovery.begin(Position(
        id="pos_eth", symbol="ETH/USDT", side=Side.LONG, amount=1.0, entry_price=0,
        notional_usdt=0, leverage=5, stop_loss=90.0, take_profit=110.0,
        strategy_id="s", strategy_name="s", decision_id="d-crash",
        market_type="futures", exec_mode="live"))
    intent["order_id"] = "entry-1"
    k.executor.recovery.save(intent, "entry_submitted")
    venue.order_status_unreadable = True
    k.boot()
    assert k.state_machine.refresh() == ControlState.RECOVERY
    st = _status(journal)
    assert st["outcome"] == "NEEDS_OWNER"
    assert {"entry_recovery_pending", "entry_recovery_owner_required",
            "entry_owned_exposure_pending"} <= set(st["reasons"])
    assert st["actions"]["entry_reason"] == "venue_or_recovery_unavailable"
    assert sent and sent[-1].startswith("🔒 recovery NEEDS_OWNER: ")
    assert WORKERS <= set(started)
    assert k.executor.recovery.pending()["order_id"] == "entry-1"  # never resubmitted
    assert venue.mutations == []
    pos, reason, orders = _entry_probe(k)
    assert pos is None and reason == "state=RECOVERY: entries blocked" and orders == 0


# ── D. wrong-side stop ─────────────────────────────────────────────────────
def test_d_wrong_side_stop_is_not_protection(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue, stop=_stop(side="BUY"))
    k, sent, started = _kernel(journal, venue, monkeypatch)
    k.boot()
    assert k.state_machine.refresh() == ControlState.RECOVERY
    st = _status(journal)
    assert st["outcome"] == "NEEDS_OWNER"
    assert "position_unprotected:BTC/USDT" in st["reasons"]
    assert sent == ["🔒 recovery NEEDS_OWNER: position_unprotected:BTC/USDT"]
    assert venue.mutations == []         # reported, not replaced or cancelled
    assert WORKERS <= set(started)
    assert _entry_probe(k)[1:] == ("state=RECOVERY: entries blocked", 0)


# ── E. loosened stop ───────────────────────────────────────────────────────
def test_e_loosened_stop_requires_owner_without_mutation(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue, stop=_stop(trigger="80"))
    k, sent, started = _kernel(journal, venue, monkeypatch)
    k.boot()
    assert k.state_machine.refresh() == ControlState.RECOVERY
    st = _status(journal)
    assert st["outcome"] == "NEEDS_OWNER"
    assert st["reasons"] == ["position_unprotected:BTC/USDT"]
    assert venue.mutations == []
    assert WORKERS <= set(started)
    assert _entry_probe(k)[1:] == ("state=RECOVERY: entries blocked", 0)


# ── F. owner-held FROZEN ───────────────────────────────────────────────────
def test_f_owner_frozen_is_never_appropriated(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    ControlStateMachine(journal).set(ControlState.FROZEN, "operator", "owner hold")
    mark = _last_event(journal)
    k, sent, started = _kernel(journal, venue, monkeypatch)
    k.boot()
    assert k.state_machine.refresh() == ControlState.FROZEN
    assert _transitions(journal, mark) == []
    st = _status(journal)
    assert st["outcome"] == "DEGRADED" and not st["needs_owner"]
    assert st["reasons"] == ["control_state_not_supervisor_owned"]
    assert st["checks"]["reconciliation"] and st["checks"]["venue_protection"]
    assert sent == ["🔒 recovery DEGRADED: control_state_not_supervisor_owned"]
    assert venue.mutations == []
    assert WORKERS <= set(started)
    assert _entry_probe(k)[1:] == ("state=FROZEN: entries blocked", 0)


# ── G. repeated boot after RECOVERY persisted, then owner recovery ─────────
def test_g_repeated_boot_after_persisted_recovery_then_owner_resume(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    venue.unreadable = True
    k1, _, _ = _kernel(journal, venue, monkeypatch)
    k1.boot()
    assert journal.kv_get("control_state") == "RECOVERY"

    # Watchdog restart with the fault already gone: the persisted owner hold
    # survives, the kernel boots (workers start) and stays contained.
    venue.unreadable = False
    mark = _last_event(journal)
    k2, sent2, started2 = _kernel(journal, venue, monkeypatch)
    k2.boot()
    assert k2.state_machine.refresh() == ControlState.RECOVERY
    assert _transitions(journal, mark) == []
    st = _status(journal)
    assert st["outcome"] == "NEEDS_OWNER"
    assert st["reasons"] == ["owner_resume_required"]
    assert st["checks"]["entries_safe"] is True        # venue proof is clean
    assert sent2 == ["🔒 recovery NEEDS_OWNER: owner_resume_required"]
    assert WORKERS <= set(started2)
    assert venue.mutations == []

    # Owner recovery through the running kernel's own interface (Telegram
    # /resume, actor=operator): a guarded fresh recheck activates, then a
    # later boot re-verifies. No reply leaves the process.
    replies = []
    k2.notifier.chat_id = "1"
    monkeypatch.setattr("requests.post", lambda *a, **kw: replies.append(kw["json"]["text"]))
    from tests.test_owner_recovery_risk_guard import tg_update
    k2._handle_tg_command("/resume", "https://telegram.invalid", update=tg_update())
    assert journal.kv_get("control_state") == "ACTIVE"
    assert _transitions(journal, mark)[-1] == ("RECOVERY", "ACTIVE", "supervisor")
    assert replies == ["🙂 ACTIVE — fresh recovery check proved safe."]
    mark = _last_event(journal)
    k3, sent3, _ = _kernel(journal, venue, monkeypatch)
    k3.boot()
    assert k3.state_machine.refresh() == ControlState.ACTIVE
    st = _status(journal)
    assert st["outcome"] == "SAFE" and not st["needs_owner"]
    assert [t[:2] for t in _transitions(journal, mark)] == [
        ("ACTIVE", "FROZEN"), ("FROZEN", "RECOVERY"), ("RECOVERY", "ACTIVE")]
    assert sent3 == []
    assert venue.mutations == []


def test_g_repeated_boot_with_fault_persisting_stays_contained(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue, stop=_stop(trigger="80"))
    for _ in range(3):
        k, sent, started = _kernel(journal, venue, monkeypatch)
        k.boot()
        assert k.state_machine.refresh() == ControlState.RECOVERY
        assert _status(journal)["outcome"] == "NEEDS_OWNER"
        assert WORKERS <= set(started)
    assert [t[:2] for t in _transitions(journal)] == [
        ("ACTIVE", "FROZEN"), ("FROZEN", "RECOVERY")]   # contained once, held
    assert venue.mutations == []


# ── protective / reconciliation matrix at boot ─────────────────────────────
# The first three are the mutation-recording negative controls: reconcile
# MUST act, and the recording venue must show exactly that action.
def test_matrix_missing_stop_rearms_then_proves(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    venue.stops = []
    k, sent, _ = _kernel(journal, venue, monkeypatch)
    k.boot()
    assert venue.mutations == [("create_order", "BTC/USDT", "sell", 1.0,
                                {"stopLossPrice": 95.0, "reduceOnly": True})]
    assert journal.open_trades()[0]["sl_order_id"] == "901"
    assert k.state_machine.refresh() == ControlState.ACTIVE
    assert _status(journal)["outcome"] == "SAFE" and sent == []


def test_matrix_orphan_stop_is_cancelled(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    venue.stops.append(dict(_stop(algo_id="7009"), symbol="ETHUSDT"))
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.boot()
    assert venue.mutations == [("cancel_algo", "7009")]
    assert k.state_machine.refresh() == ControlState.ACTIVE


def test_matrix_journal_venue_id_mismatch_cancels_and_rearms(world, monkeypatch):
    journal, venue = world
    journal.add_trade(_trade(sl_order_id="6999"))
    venue.positions = [{"symbol": "BTC/USDT", "contracts": 1, "side": "long",
                        "entryPrice": 100, "markPrice": 100}]
    venue.stops = [_stop(algo_id="7010")]
    k, _, _ = _kernel(journal, venue, monkeypatch)
    k.boot()
    assert [m[0] for m in venue.mutations] == ["cancel_algo", "create_order"]
    assert venue.mutations[0] == ("cancel_algo", "7010")
    assert journal.open_trades()[0]["sl_order_id"] == "901"
    assert k.state_machine.refresh() == ControlState.ACTIVE


def test_matrix_incomplete_listing_never_sweeps(world, monkeypatch):
    journal, venue = world
    _protected(journal, venue)
    venue.stops.append(dict(_stop(algo_id="7009"), symbol="ETHUSDT"))
    venue.algo_listing_invalid = True
    k, sent, started = _kernel(journal, venue, monkeypatch)
    k.boot()
    assert venue.mutations == []          # no cancel on a partial snapshot
    assert k.state_machine.refresh() == ControlState.RECOVERY
    st = _status(journal)
    assert st["outcome"] == "NEEDS_OWNER"
    assert {"orphan_stop_sweep_unresolved",
            "protection_snapshot_unreadable"} <= set(st["reasons"])
    assert sent and sent[0].startswith("🔒 recovery NEEDS_OWNER: ")
    assert WORKERS <= set(started)
