"""SDD-STAGE-3-DECISION-REJECTION-REASON-CODES-V1.

Every existing refusal branch writes a stable code alongside its unchanged
skip_reason text (vocabulary decision-rejection-reason.v1). Codes are set at
the branch, never parsed from text; overwrites replace text and codes
together; the journal keeps NULL for rows written without codes.

NULL = reason codes not recorded / legacy unknown. Explicit [] = the field was
recorded but no coded text-producing refusal was present — never "no refusal",
"all checks passed" or "executed" (see test_empty_codes_do_not_imply_execution).
"""
import ast
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from trader.core import reason_codes as rc
from trader.core.journal import Journal
from trader.core.types import (Action, ControlState, Decision, MarketType,
                               Position, RiskError, Side, StrategySignal)
from trader.engine import orchestrator as om
from trader.engine.control_fence import entry_block, entry_block_reason
from trader.engine.risk import RiskManager, SizingResult
from trader.engine.state import ControlStateMachine
from tests.test_entry_recovery import enter, setup  # noqa: F401
from tests.test_scout_upgrades import Stub, _snap

ROOT = Path(__file__).resolve().parents[1]
SEEN: set = set()          # every code a scenario below actually produced


def _seen(codes):
    SEEN.update(codes)
    return codes


def test_vocabulary_is_stable_snake_case_and_versioned():
    assert rc.VERSION == "decision-rejection-reason.v1"
    assert len(rc.VOCABULARY) == 26
    for code in rc.VOCABULARY:
        assert code == code.lower() and code.replace("_", "").isalpha(), code
        assert not any(ch.isdigit() for ch in code)


# ── orchestrator ─────────────────────────────────────────────────────────
def _orch(conv, **scouts):
    import tempfile
    j = Journal(Path(tempfile.mkdtemp()) / "o.db")
    return om.Orchestrator([Stub(conv)], j, cfg={"scouts": scouts})


def _decide(o, **kw):
    return o.decide(_snap(), population=[], **kw)


def test_plain_hold_has_explicit_empty_codes():
    d = _decide(_orch(0.0))
    assert d.action == Action.HOLD
    assert d.skip_reason == "" and d.reason_codes == []


def test_directional_unrefused_decision_has_empty_codes():
    d = _decide(_orch(0.9, require_strategy_signal=False), entry_allowed=True)
    assert d.action == Action.BUY
    assert d.skip_reason == "" and d.reason_codes == []


def test_no_strategy_signal():
    d = _decide(_orch(0.9))
    assert d.action == Action.HOLD
    assert d.skip_reason == "no strategy signal"
    assert _seen(d.reason_codes) == [rc.STRATEGY_NO_SIGNAL]


def _eligible_population(monkeypatch, sig_action):
    monkeypatch.setattr(om, "regime_allows", lambda g, r: True)
    monkeypatch.setattr(om, "symbol_allows", lambda g, s: True)
    monkeypatch.setattr(om.strat_lib, "evaluate", lambda g, snap, **kw: StrategySignal(
        "s1", "spec one", snap.symbol, sig_action, 0.6, "test"))
    return [(NS(id="s1", is_trade_eligible=True), NS(markets={"futures"}))]


def test_no_strategy_agrees(monkeypatch):
    pop = _eligible_population(monkeypatch, Action.SELL)
    monkeypatch.setattr(om, "net_score", lambda *a: 0.9)
    o = _orch(0.9, strategy_leads=False)
    d = o.decide(_snap(), population=pop)
    assert d.action == Action.HOLD
    assert d.skip_reason == "no strategy agrees with BUY (1 signalled the other way)"
    assert _seen(d.reason_codes) == [rc.STRATEGY_NO_AGREEMENT]


def test_strategy_gate_keeps_its_three_tuple_and_matches_the_coded_gate():
    sig = [NS(action=Action.SELL)]
    for action, sigs in ((Action.HOLD, []), (Action.BUY, []),
                         (Action.BUY, sig), (Action.SELL, sig)):
        a, veto, code, lean = om.strategy_gate_coded(action, sigs)
        assert om.strategy_gate(action, sigs) == (a, veto, lean)
        assert bool(veto) == bool(code)


def test_htf_hard_veto(monkeypatch):
    monkeypatch.setattr(om, "htf_trend_score", lambda df: 0.9)
    d = _decide(_orch(-0.9), entry_allowed=True)
    assert d.action == Action.HOLD
    assert d.skip_reason == "4h trend UP s=+0.90 — hard veto"
    assert _seen(d.reason_codes) == [rc.HTF_TREND_HARD_VETO]


@pytest.fixture
def meta_p(monkeypatch):
    from trader.brain import meta_label
    monkeypatch.setattr(meta_label, "judge", lambda feat: 0.2)


def test_meta_veto(meta_p):
    d = _decide(_orch(0.9, require_strategy_signal=False))
    assert d.action == Action.BUY
    assert d.skip_reason == "meta: p=0.20 < 0.45"
    assert _seen(d.reason_codes) == [rc.META_VETO]


def test_combined_refusals_keep_text_order(meta_p):
    d = _decide(_orch(0.9, require_strategy_signal=False), entry_allowed=False,
                blocked_reason="state=FROZEN",
                blocked_reason_code=rc.CONTROL_STATE_NOT_ACTIVE)
    assert d.skip_reason == "meta: p=0.20 < 0.45; state=FROZEN"
    assert d.reason_codes == [rc.META_VETO, rc.CONTROL_STATE_NOT_ACTIVE]


def test_entries_not_allowed_fallback_and_uncoded_caller():
    o = _orch(0.9, require_strategy_signal=False)
    d = _decide(o, entry_allowed=False)
    assert d.skip_reason == "entries not allowed"
    assert _seen(d.reason_codes) == [rc.ENTRIES_NOT_ALLOWED]
    d = _decide(o, entry_allowed=False, blocked_reason="state=FROZEN")
    assert d.skip_reason == "state=FROZEN"
    assert d.reason_codes == [rc.ENTRIES_NOT_ALLOWED]
    # a code never travels without its own text
    d = _decide(o, entry_allowed=False, blocked_reason_code=rc.DAILY_LOSS_BREAKER)
    assert d.skip_reason == "entries not allowed"
    assert d.reason_codes == [rc.ENTRIES_NOT_ALLOWED]


def test_hold_with_entries_blocked_is_not_a_refusal():
    d = _decide(_orch(0.0), entry_allowed=False, blocked_reason="state=FROZEN",
                blocked_reason_code=rc.CONTROL_STATE_NOT_ACTIVE)
    assert d.action == Action.HOLD and d.skip_reason == "" and d.reason_codes == []


# ── risk.check_entry ─────────────────────────────────────────────────────
RISK_CFG = {"risk": {
    "risk_per_trade_pct": 1, "portfolio_heat_cap_pct": 5,
    "per_symbol_risk_cap_pct": 2, "max_open_positions": 3,
    "max_daily_loss_pct": 3, "halt_drawdown_pct": 20, "leverage": 5,
    "stop_loss_atr_mult": 2, "min_notional_usdt": 10,
    "max_position_margin_pct": 20, "max_total_margin_pct": 70}}


def _pos(symbol, notional=100.0, stop=95.0):
    return Position(id=symbol, symbol=symbol, side=Side.LONG, amount=1,
                    entry_price=100.0, notional_usdt=notional, stop_loss=stop)


def _check(state=ControlState.ACTIVE, positions=(), equity=1000.0, prime=None):
    rm = RiskManager(RISK_CFG, None)
    if prime:
        rm.update_equity(prime)
    return rm.check_entry(state, "BTC/USDT", 100.0, 1.0, 0.02, list(positions),
                          equity, 100, "futures")


@pytest.mark.parametrize("kw,reason,code", [
    ({"state": ControlState.FROZEN}, "state=FROZEN: entries blocked", rc.RISK_STATE_FROZEN),
    ({"state": ControlState.HALTED}, "state=HALTED", rc.RISK_STATE_HALTED),
    ({"state": ControlState.RECOVERY}, "state=RECOVERY: entries blocked",
     rc.RISK_STATE_NOT_ACTIVE),
    ({"equity": 960.0, "prime": 1000.0}, "daily breaker -4.0%", rc.RISK_DAILY_LOSS_BREAKER),
    ({"positions": [_pos("A"), _pos("B"), _pos("C")]}, "max positions reached",
     rc.RISK_MAX_POSITIONS),
    ({"positions": [_pos("BTC/USDT")]}, "already exposed here", rc.RISK_ALREADY_EXPOSED),
    ({"positions": [_pos("A", notional=2000.0, stop=0.0)]},
     "risk budget exhausted (heat 10.0%/5%)", rc.RISK_BUDGET_EXHAUSTED),
    ({"positions": [_pos("A", notional=4000.0, stop=99.9)]},
     "margin cap reached (80%/70% of equity committed)", rc.RISK_MARGIN_CAP),
    ({"equity": 20.0}, "below min notional", rc.RISK_BELOW_MIN_NOTIONAL),
])
def test_each_risk_refusal_carries_its_branch_code(kw, reason, code):
    r = _check(**kw)
    assert not r.ok and r.reason == reason and r.code == code
    assert (r.size_usdt, r.amount, r.risk_usdt, r.size_mult) == (0, 0, 0, 0)
    SEEN.add(r.code)


def test_risk_ok_has_no_code():
    r = _check()
    assert r.ok and r.reason == "ok" and r.code == ""


def test_risk_error_still_raised():
    with pytest.raises(RiskError):
        _check(equity=700.0, prime=1000.0)


# ── kernel._try_enter ────────────────────────────────────────────────────
from tests.test_entry_geometry_end_to_end import (_DONCHIAN, _SIG, _bars,  # noqa: E402
                                                  _D, _Snap, _kernel)


def test_stop_atr_unavailable_when_spec_frame_bars_missing():
    k = _kernel({"donchian": _DONCHIAN})
    d = _D(Action.BUY, _SIG)
    assert k._try_enter(d, _Snap({"15m": _bars(60, 0.4)}), 5000.0, 0) is False
    assert d.skip_reason == "no 4h bars to size the stop on"
    assert _seen(d.reason_codes) == [rc.STOP_ATR_UNAVAILABLE]


def test_stop_atr_unavailable_when_bars_present_but_atr_not_positive():
    # same predicate (a <= 0) reached with 4h bars present: the code names
    # the predicate, not only the missing-bars case the legacy text describes
    k = _kernel({"donchian": _DONCHIAN})
    d = _D(Action.BUY, _SIG)
    snap = _Snap({"15m": _bars(60, 0.4), "4h": _bars(60, 0.0)})
    assert k._try_enter(d, snap, 5000.0, 0) is False
    assert d.skip_reason == "no 4h bars to size the stop on"   # text unchanged
    assert _seen(d.reason_codes) == [rc.STOP_ATR_UNAVAILABLE]
    assert k.executor.opened is None


def test_risk_refusal_code_comes_from_the_risk_branch():
    k = _kernel({"donchian": _DONCHIAN})
    k.risk.check_entry = lambda *a, **kw: SizingResult(
        False, "already exposed here", 0, 0, 0, 0, code=rc.RISK_ALREADY_EXPOSED)
    d = _D(Action.BUY, _SIG)
    snap = _Snap({"15m": _bars(60, 0.4), "4h": _bars(60, 8.0)})
    assert k._try_enter(d, snap, 5000.0, 0) is False
    assert d.skip_reason == "risk: already exposed here"
    assert d.reason_codes == [rc.RISK_ALREADY_EXPOSED]
    assert k.executor.opened is None


def test_meta_sized_below_min_notional():
    k = _kernel({"donchian": _DONCHIAN}, min_notional=10)
    d = _D(Action.BUY, _SIG)
    d.meta_size = 0.05                       # 1.0 × 100 × 0.05 = 5 < 10
    snap = _Snap({"15m": _bars(60, 0.4), "4h": _bars(60, 8.0)})
    assert k._try_enter(d, snap, 5000.0, 0) is False
    assert d.skip_reason == "risk: meta-sized below min notional"
    assert _seen(d.reason_codes) == [rc.META_SIZE_BELOW_MIN_NOTIONAL]
    assert k.executor.opened is None


# ── kernel.cycle ─────────────────────────────────────────────────────────
class _KV:
    def __init__(self, **kv):
        self.kv = dict(kv)
        self.update_decision_outcome = Mock()
        self.log_control_event = Mock()
        self.log_equity = Mock()

    def kv_get(self, k, d=None): return self.kv.get(k, d)
    def kv_set(self, k, v): self.kv[k] = v
    def query(self, *a): return [{"n": 0}]
    def open_trades(self): return []


def _cycle_kernel(state="ACTIVE", symbols=("S0/USDT",), recovery=False,
                  daily_pnl=0.0, try_enter=None):
    from trader.kernel import Kernel
    j = _KV(control_state=state)
    k = object.__new__(Kernel)
    k.cfg = {"timeframes": {"execution": "15m"}}
    k.population = []
    k.journal = j
    k.state_machine = ControlStateMachine(j)
    k._fetch_balance = lambda: 1000
    k.risk = NS(update_equity=lambda _: {"equity": 1000, "drawdown_pct": 0,
                                         "daily_pnl_pct": daily_pnl},
                daily_loss_block=.03)
    k._drain_close_requests = lambda: 0
    k.macro_guard = NS(check=lambda: {"active": False})
    k.news_guard = NS(check=lambda: {"active": False})
    k.market_type = MarketType.FUTURES
    k.executor = NS(recovery_pending=lambda: recovery, recover_entries=Mock())
    k.supervisor = None
    k._funding_map = k._oi_map = lambda: {}
    k._refresh_btc_context = lambda: None
    k._scan_symbols = lambda: list(symbols)
    k._universe_frames = lambda _: {}
    k._snapshot_for = lambda s, **kw: NS(symbol=s, price=100)
    k.positioning_agent = k.depth_agent = NS(set_context=lambda *a: None)
    k._order_book = lambda _: {}

    def decide(snap, population, entry_allowed=True, blocked_reason="",
               blocked_reason_code=""):
        return Decision("d-" + snap.symbol, "c", snap.symbol, Action.BUY,
                        .7, .2, .8, [], [])
    k.orchestrator = NS(decide=Mock(side_effect=decide), journalize=Mock())
    k._try_enter = Mock(side_effect=try_enter or (lambda *a: True))
    k._detect_exchange_exits = Mock(return_value=0)
    k._manage_one = Mock(return_value=None)
    k._manage_orphan_positions = Mock(return_value=0)
    k._maybe_resolve_outcomes = Mock()
    k._record_excursions = Mock()
    k._attention_call = lambda *a, **kw: None
    k.heartbeat = NS(beat=Mock())
    k.notifier = NS(send=Mock())
    return k


@pytest.mark.parametrize("kw,reason,code", [
    ({"state": "FROZEN"}, "state=FROZEN", rc.CONTROL_STATE_NOT_ACTIVE),
    ({"recovery": True}, "execution_recovery_pending", rc.EXECUTION_RECOVERY_PENDING),
    ({"daily_pnl": -4.0}, "daily breaker -4.0%", rc.DAILY_LOSS_BREAKER),
])
def test_kernel_blocked_reason_travels_with_its_code(kw, reason, code):
    k = _cycle_kernel(**kw)
    k.cycle()
    call = k.orchestrator.decide.call_args.kwargs
    assert call["entry_allowed"] is False
    assert call["blocked_reason"] == reason
    assert call["blocked_reason_code"] == code
    SEEN.add(code)


def test_kernel_active_passes_no_code():
    k = _cycle_kernel()
    k.cycle()
    call = k.orchestrator.decide.call_args.kwargs
    assert call["blocked_reason"] == "" and call["blocked_reason_code"] == ""


def test_risk_halt_codes_the_decision_and_the_rest_of_the_cycle():
    def boom(*a):
        raise RiskError("drawdown 25.0% ≥ halt 20% — flip HALTED")
    k = _cycle_kernel(symbols=("S0/USDT", "S1/USDT"), try_enter=boom)
    k.cycle()
    k.journal.update_decision_outcome.assert_called_once_with(
        "d-S0/USDT", False, 0.0,
        "risk halt: drawdown 25.0% ≥ halt 20% — flip HALTED",
        reason_codes=[rc.RISK_HALT])
    second = k.orchestrator.decide.call_args_list[1].kwargs
    assert second["blocked_reason"] == "state=HALTED"
    assert second["blocked_reason_code"] == rc.RISK_HALT_IN_CYCLE
    SEEN.update({rc.RISK_HALT, rc.RISK_HALT_IN_CYCLE})


def test_kernel_overwrite_replaces_orchestrator_codes():
    """_try_enter's refusal replaces whatever decide() recorded, text and codes."""
    def refuse(d, *a):
        d.skip_reason = "risk: max positions reached"
        d.reason_codes = [rc.RISK_MAX_POSITIONS]
        return False
    k = _cycle_kernel(try_enter=refuse)
    k.cycle()
    k.journal.update_decision_outcome.assert_called_once_with(
        "d-S0/USDT", False, 0.0, "risk: max positions reached",
        reason_codes=[rc.RISK_MAX_POSITIONS])


def test_kernel_executed_entry_records_empty_codes():
    def ok(d, *a):
        d.executed, d.size_usdt = True, 50.0
        return True
    k = _cycle_kernel(try_enter=ok)
    k.cycle()
    k.journal.update_decision_outcome.assert_called_once_with(
        "d-S0/USDT", True, 50.0, "", reason_codes=[])


# ── executor ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("persisted,reason,code", [
    ("FROZEN", "state=FROZEN: entries blocked", rc.FENCE_STATE_FROZEN),
    ("HALTED", "state=HALTED", rc.FENCE_STATE_HALTED),
    ("RECOVERY", "state=RECOVERY: entries blocked", rc.FENCE_STATE_NOT_ACTIVE),
    ("BOGUS", "state=UNREADABLE: entries blocked", rc.FENCE_STATE_UNREADABLE),
])
def test_submission_fence_codes(setup, persisted, reason, code):
    ex, j, e, d = setup
    j.kv_set("control_state", persisted)
    assert enter(e, d) is None
    assert ex.sent == []
    assert d.skip_reason == reason
    assert _seen(d.reason_codes) == [code]


def test_entry_block_reason_text_unchanged():
    for s in (ControlState.ACTIVE, ControlState.FROZEN, ControlState.HALTED,
              ControlState.RECOVERY, None):
        assert entry_block_reason(s) == entry_block(s)[0]
    assert entry_block(ControlState.ACTIVE) == (None, None)


def test_executor_recovery_pending(setup):
    from ccxt import RequestTimeout
    ex, j, e, d = setup
    ex.entry_error = RequestTimeout("accepted, response lost")
    assert enter(e, d) is None and e.recovery_pending()
    assert d.skip_reason == "" and d.reason_codes == []   # unchanged: no text set
    assert enter(e, d) is None
    assert d.skip_reason == "execution_recovery_pending"
    assert _seen(d.reason_codes) == [rc.SUBMISSION_RECOVERY_PENDING]


def test_executor_success_leaves_empty_codes(setup):
    ex, j, e, d = setup
    assert enter(e, d) is not None
    assert d.skip_reason == "" and d.reason_codes == []


@pytest.mark.parametrize("path", ["zero_lot", "submission_rejected"])
def test_empty_codes_do_not_imply_execution(setup, monkeypatch, path):
    # [] means "no coded text-producing refusal", not success: these paths
    # fail without skip_reason text and are deliberately left uncoded in v1
    from ccxt import InsufficientFunds
    ex, j, e, d = setup
    if path == "zero_lot":
        monkeypatch.setattr("trader.engine.executor.quantize", lambda *a: 0.0)
    else:
        ex.entry_error = InsufficientFunds("rejected")
    assert enter(e, d) is None
    assert not d.executed
    if path == "zero_lot":
        assert ex.sent == []                 # nothing reached the venue
    assert d.skip_reason == "" and d.reason_codes == []


# ── journal persistence ──────────────────────────────────────────────────
LEGACY_DECISIONS = """
CREATE TABLE cycles (id TEXT PRIMARY KEY, ts TEXT NOT NULL, symbol TEXT NOT NULL);
CREATE TABLE decisions (
    id TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, ts TEXT NOT NULL,
    symbol TEXT NOT NULL, action TEXT NOT NULL, score REAL NOT NULL,
    threshold REAL NOT NULL, confidence REAL NOT NULL,
    executed INTEGER NOT NULL DEFAULT 0, skip_reason TEXT,
    size_usdt REAL DEFAULT 0, entry_price REAL, strategy_ids TEXT);
INSERT INTO cycles VALUES ('c', '2026-09-01', 'BTC/USDT');
INSERT INTO decisions(id,cycle_id,ts,symbol,action,score,threshold,confidence,
    executed,skip_reason) VALUES ('old','c','2026-09-01','BTC/USDT','BUY',
    .5,.2,.6,0,'risk: max positions reached');
"""


def _row(j, did):
    return j.query("SELECT skip_reason, reason_codes, reason_codes_version "
                   "FROM decisions WHERE id=?", (did,))[0]


def test_migration_idempotent_and_legacy_rows_stay_null(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as c:
        c.executescript(LEGACY_DECISIONS)
    Journal(db)
    j = Journal(db)                                     # second run is a no-op
    cols = [r["name"] for r in j.query("PRAGMA table_info(decisions)")]
    assert cols.count("reason_codes") == 1 and cols.count("reason_codes_version") == 1
    old = _row(j, "old")
    assert old["skip_reason"] == "risk: max positions reached"
    assert old["reason_codes"] is None and old["reason_codes_version"] is None
    assert rc.decode(old["reason_codes"]) is None       # unknown, not []


def _logged(tmp_path, **kw):
    j = Journal(tmp_path / "j.db")
    with j._tx() as c:
        c.execute("INSERT INTO cycles(id,ts,symbol) VALUES ('c','t','BTC/USDT')")
    d = Decision("d", "c", "BTC/USDT", Action.BUY, .5, .2, .6, [], [], **kw)
    j.log_decision(d)
    return j, d


def test_new_rows_round_trip_ordered_codes(tmp_path):
    codes = [rc.META_VETO, rc.CONTROL_STATE_NOT_ACTIVE]
    j, _ = _logged(tmp_path, skip_reason="meta: p=0.20 < 0.45; state=FROZEN",
                   reason_codes=codes)
    row = _row(j, "d")
    assert json.loads(row["reason_codes"]) == codes
    assert rc.decode(row["reason_codes"]) == codes
    assert row["reason_codes_version"] == rc.VERSION


def test_uncoded_decision_persists_explicit_empty_list(tmp_path):
    j, _ = _logged(tmp_path)
    row = _row(j, "d")
    assert row["reason_codes"] == "[]" and rc.decode(row["reason_codes"]) == []


def test_update_outcome_overwrites_text_and_codes_together(tmp_path):
    j, _ = _logged(tmp_path)
    j.update_decision_outcome("d", False, 0.0, "risk: below min notional",
                              reason_codes=[rc.RISK_BELOW_MIN_NOTIONAL])
    row = _row(j, "d")
    assert row["skip_reason"] == "risk: below min notional"
    assert rc.decode(row["reason_codes"]) == [rc.RISK_BELOW_MIN_NOTIONAL]
    j.update_decision_outcome("d", True, 50.0, "", reason_codes=[])
    assert rc.decode(_row(j, "d")["reason_codes"]) == []
    # a writer that does not classify records "not recorded", never stale codes
    j.update_decision_outcome("d", True, 50.0, "recovered_confirmed_exposure")
    row = _row(j, "d")
    assert row["reason_codes"] is None and row["reason_codes_version"] is None


def test_decode_tolerates_null_and_garbage():
    assert rc.decode(None) is None
    assert rc.decode("not json") is None
    assert rc.decode('{"a": 1}') is None
    assert rc.encode(None) is None and rc.encode([]) == "[]"


def test_old_readers_still_work_on_new_rows(tmp_path):
    j, _ = _logged(tmp_path, skip_reason="no strategy signal",
                   reason_codes=[rc.STRATEGY_NO_SIGNAL])
    rows = j.query("SELECT ts,symbol,action,score,executed,skip_reason "
                   "FROM decisions")
    assert rows[0]["skip_reason"] == "no strategy signal"


# ── exhaustiveness ───────────────────────────────────────────────────────
def _trader_sources():
    return [p for p in (ROOT / "trader").rglob("*.py")]


def _attr_target(node, attr):
    return [t for t in getattr(node, "targets", [])
            if isinstance(t, ast.Attribute) and t.attr == attr]


def test_every_skip_reason_assignment_sets_codes_in_the_same_block():
    sites = 0
    for path in _trader_sources():
        tree = ast.parse(path.read_text())
        for block in ast.walk(tree):
            for field in ("body", "orelse", "handlers", "finalbody"):
                stmts = getattr(block, field, None)
                if not isinstance(stmts, list):
                    continue
                for i, st in enumerate(stmts):
                    for t in _attr_target(st, "skip_reason"):
                        sites += 1
                        owner = ast.unparse(t.value)
                        assert any(ast.unparse(u.value) == owner
                                   for later in stmts[i + 1:i + 3]
                                   for u in _attr_target(later, "reason_codes")), \
                            f"{path.relative_to(ROOT)}:{st.lineno} skip_reason without reason_codes"
    assert sites == 7    # orchestrator 1, kernel 4, executor 2


def test_every_update_decision_outcome_call_classifies():
    for path in _trader_sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "update_decision_outcome"):
                assert any(k.arg == "reason_codes" for k in node.keywords), \
                    f"{path.relative_to(ROOT)}:{node.lineno}"


def test_every_code_referenced_exists_and_every_code_is_used():
    used = set()
    for path in _trader_sources():
        if path.name == "reason_codes.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                    and node.value.id == "rc" and node.attr.isupper()):
                assert hasattr(rc, node.attr), f"{path}:{node.lineno} rc.{node.attr}"
                used.add(getattr(rc, node.attr))
    assert used == set(rc.VOCABULARY)


def test_zz_every_code_was_produced_by_a_scenario():
    """Runs last: each vocabulary code came out of its real branch above."""
    assert SEEN == set(rc.VOCABULARY), sorted(set(rc.VOCABULARY) - SEEN)
