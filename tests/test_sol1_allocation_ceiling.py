"""The approved per-version allocation bounds every live entry (SOL1 closure).

Isolated simulation only: a TEST-ONLY venue double, a temporary journal, no
network, no production database. The allocation is a share of the size Risk
itself computes for the entry; Risk's authorization, the Kernel request and the
Executor each derive it independently, so none can exceed it - through
resizing, lot quantization or revalidation after the Governor lowers it.
"""
import copy
import json
import time
from decimal import Decimal

import pytest

from trader.core.types import Action, Decision, MarketType
from trader.engine import entry_authority as A
from trader.engine.executor import Executor
from trader.strategy import factory_handoff as F
from tests.authority_capacity_fixtures import NOW
from tests.authority_factory_fixtures import INPUTS, cfg  # noqa: F401
from tests.entry_authority_fixtures import bind
from tests.test_entry_recovery import Venue
from tests.test_sol1_strategy_lifecycle import _deploy

REF = dict(price=100.)


class RoundUpVenue(Venue):
    """TEST-ONLY venue whose lot helper rounds UP to a whole contract."""
    def amount_to_precision(self, symbol, amount):
        import math
        return str(math.ceil(amount))


class TruncVenue(Venue):
    def amount_to_precision(self, symbol, amount):
        return str(int(Decimal(str(amount)) * 1000) / 1000)


def live(tmp_path, cfg, monkeypatch, venue=None, real_capacity=False):
    """An approved, Governor-ACTIVE, owner-deployed version, then the venue
    double's account/margin/capability observations.

    Those observations change the state a capacity receipt was recorded
    against, so - exactly as in production - the REAL capacity check then holds
    the version until a fresh ESTABLISHED receipt is recorded
    (`test_new_account_observations_hold_the_version_until_capacity_is_refreshed`).
    Unless `real_capacity`, this fixture holds capacity currency constant so the
    other tests isolate allocation; capacity itself is proven by that test and
    the lifecycle HOLD tests, not waived."""
    from tests.test_sol1_strategy_lifecycle import _activated
    from trader.strategy import capacity as cap
    j, v, risk, rid = _activated(tmp_path, cfg, monkeypatch)
    _deploy(j, cfg, v)
    assert F.live_entry_block(j, v["strategy_id"], cfg=cfg) is None
    ex = venue or Venue()
    manager, _cap = bind(j, ex, cfg)
    if not real_capacity:
        monkeypatch.setattr(cap, "check_current", lambda *a, **k: {
            "receipt_id": rid, "current": True, "reasons": [], "status": cap.ESTABLISHED,
            "effective": None, "contract": cap.SCHEMA})
    d = Decision("d", "c", "BTC/USDT", Action.BUY, 1., .5, .8, [], [])
    with j._tx() as db:
        db.execute("INSERT INTO cycles(id,ts,symbol) VALUES ('c','2026-09-16','BTC/USDT')")
    d.instrument_binding_json = A.proposal_binding(j, d.symbol)
    j.log_decision(d)
    e = Executor(ex, j, cfg, MarketType.FUTURES, risk_manager=manager)
    return dict(j=j, v=v, sid=v["strategy_id"], vid=v["version_id"], ex=ex, e=e,
                d=d, manager=manager, rid=rid, risk=risk)


def authorize(w, amount, **kw):
    return w["manager"].authorize_entry(w["d"], amount, 2., 95., 110., w["sid"],
                                        reference=REF, **kw)


def open_(w, amount, permission):
    return w["e"].open(w["d"], amount, 2., 95., 110., w["sid"], "version",
                       reference=REF, risk_permission=permission)


def base_size(w):
    """Risk's own approved size for this entry, with no allocation applied."""
    permission = authorize(w, 1000.)
    return Decimal(permission.payload()["allocation"]["base"])


def entries(ex):
    return [s for s in ex.sent if not s[4].get("reduceOnly") and "stopLossPrice" not in s[4]
            and s[1] != "STOP_MARKET"]


def test_new_account_observations_hold_the_version_until_capacity_is_refreshed(
        tmp_path, cfg, monkeypatch):
    """No stub: the real capacity check supersedes the receipt the moment the
    account state it was recorded against changes, and the entry is refused."""
    w = live(tmp_path, cfg, monkeypatch, real_capacity=True)
    hold = F.live_entry_block(w["j"], w["sid"], cfg=cfg)
    assert hold and "capacity_inputs_superseded" in hold
    with pytest.raises(ValueError, match="version_not_live_authorized"):
        authorize(w, 1.)
    assert open_(w, 1., None) is None and entries(w["ex"]) == []


def test_allocated_is_exact_decimal_rounded_down_and_never_enlarges():
    assert A.allocated("10", "0.1") == Decimal("1.00000000")
    assert A.allocated("3.33333333", "0.1") == Decimal("0.33333333")      # down, not nearest
    assert A.allocated("0.00000019", "0.5") == Decimal("0.00000009")
    for base in ("0.7", "1234.56789012", "5"):
        for f in ("0.01", "0.3333", "0.9999", "1"):
            assert A.allocated(base, f) <= Decimal(base)
    for bad in ("0", "-0.1", "1.0000001", "nan", "inf", True, None):
        with pytest.raises(ValueError):
            A.allocated("1", bad)


def test_risk_authorization_shrinks_the_entry_to_the_approved_allocation(tmp_path, cfg, monkeypatch):
    w = live(tmp_path, cfg, monkeypatch)
    p = authorize(w, 1000.)                       # a request far above any limit
    raw = p.payload()
    base = Decimal(raw["allocation"]["base"])
    assert base > 0
    assert Decimal(raw["allowed"]) == A.allocated(base, "0.1") == Decimal(raw["allocation"]["limit"])
    assert Decimal(raw["allowed"]) <= base / 10
    assert raw["risk_result"] == "RESIZE"
    assert raw["allocation"]["version_id"] == w["vid"]
    assert raw["allocation"]["fraction"] == "0.1" == raw["allocation"]["ceiling"]
    assert raw["allocation"]["governor_event_sha256"]


def test_a_request_inside_the_allocation_is_allowed_and_submitted_exactly(tmp_path, cfg, monkeypatch):
    w = live(tmp_path, cfg, monkeypatch)
    allowed = Decimal(authorize(w, 1000.).payload()["allowed"])
    amount = float(allowed)
    p = authorize(w, amount)
    assert p.payload()["risk_result"] == "ALLOW"
    pos = open_(w, amount, p)
    assert pos is not None
    sent = entries(w["ex"])
    assert len(sent) == 1 and Decimal(str(sent[0][3])) == allowed
    assert Decimal(str(sent[0][3])) <= base_size_of(p) / 10


def base_size_of(permission):
    return Decimal(permission.payload()["allocation"]["base"])


def test_execution_cannot_submit_more_than_the_authorized_allocation(tmp_path, cfg, monkeypatch):
    w = live(tmp_path, cfg, monkeypatch)
    p = authorize(w, 1000.)
    allowed = Decimal(p.payload()["allowed"])
    for amount in (float(allowed) + 1e-6, float(base_size_of(p)), float(base_size_of(p)) * 2):
        w["d"].skip_reason = None
        assert open_(w, amount, p) is None
        assert w["d"].skip_reason == "risk_size_exceeded"
    assert entries(w["ex"]) == []


def test_lot_quantization_can_only_shrink_never_enlarge(tmp_path, cfg, monkeypatch):
    w = live(tmp_path, cfg, monkeypatch, venue=RoundUpVenue())
    allowed = Decimal(authorize(w, 1000.).payload()["allowed"])
    amount = float(allowed)
    p = authorize(w, amount)
    assert open_(w, amount, p) is None
    assert w["d"].skip_reason == "entry_quantization_enlarged_size"
    assert entries(w["ex"]) == []


def test_truncating_quantization_stays_inside_the_allocation(tmp_path, cfg, monkeypatch):
    w = live(tmp_path, cfg, monkeypatch, venue=TruncVenue())
    allowed = Decimal(authorize(w, 1000.).payload()["allowed"])
    amount = float(allowed) + 0.0004 if False else float(allowed)
    p = authorize(w, amount)
    pos = open_(w, amount, p)
    sent = entries(w["ex"])
    assert pos is not None and len(sent) == 1
    assert Decimal(str(sent[0][3])) <= allowed
    # the lot step is a multiple of 0.001, so the sent size is a whole number of lots
    assert (Decimal(str(sent[0][3])) / Decimal("0.001")) % 1 == 0


def _reallocate(w, cfg, fraction, at):
    return F.govern_version(
        w["j"], cfg, w["vid"], "ACTIVE", actor="operator",
        reason_code="TEST-ONLY reduce allocation", at_ms=at, allocation=fraction,
        available_inputs=INPUTS, capacity_receipt_id=w["rid"], risk_manager=w["risk"],
        risk_release=w["risk"].release_check(10000))


def test_a_reduction_after_authorization_binds_at_submission(tmp_path, cfg, monkeypatch):
    w = live(tmp_path, cfg, monkeypatch)
    p = authorize(w, 1000.)
    allowed = Decimal(p.payload()["allowed"])
    _reallocate(w, cfg, 0.05, NOW + 1000)
    assert F.live_allocation(w["j"], w["sid"], cfg)["fraction"] == "0.05"
    # the permission was issued under 0.1; it can no longer be used for that size
    assert open_(w, float(allowed), p) is None
    assert w["d"].skip_reason == "allocation_ceiling_exceeded"
    assert entries(w["ex"]) == []
    # but a size inside the reduced allocation still goes out
    reduced = float(A.allocated(base_size_of(p), "0.05"))
    w["d"].skip_reason = None
    assert open_(w, reduced, p) is not None
    assert Decimal(str(entries(w["ex"])[0][3])) <= base_size_of(p) / 20


def test_the_allocation_can_never_be_raised_above_the_approved_ceiling(tmp_path, cfg, monkeypatch):
    w = live(tmp_path, cfg, monkeypatch)
    with pytest.raises(F.HandoffRefused, match="governor_owner_boundaries_changed"):
        _reallocate(w, cfg, 0.2, NOW + 1000)
    with pytest.raises(F.HandoffRefused, match="governor_allocation_invalid"):
        _reallocate(w, cfg, 1.5, NOW + 2000)
    assert F.live_allocation(w["j"], w["sid"], cfg)["fraction"] == "0.1"


def test_a_tiny_allocation_below_min_notional_is_refused(tmp_path, cfg, monkeypatch):
    w = live(tmp_path, cfg, monkeypatch)
    base_margin = float(base_size(w)) * REF["price"] / w["manager"].leverage
    # Risk's own size clears the floor; ten percent of it does not
    monkeypatch.setattr(w["manager"], "min_notional", base_margin * 0.5)
    with pytest.raises(ValueError, match="allocation_below_min_notional"):
        authorize(w, 1000.)


def test_allocation_is_unavailable_unless_the_version_is_live_authorized(tmp_path, cfg, monkeypatch):
    w = live(tmp_path, cfg, monkeypatch)
    assert F.live_allocation(w["j"], w["sid"], cfg)["version_id"] == w["vid"]
    with pytest.raises(F.HandoffRefused, match="version_first_live_configuration_unavailable"):
        F.live_allocation(w["j"], w["sid"], None)
    changed = copy.deepcopy(cfg)
    changed["risk"]["leverage"] += 1
    with pytest.raises(F.HandoffRefused, match="version_not_live_authorized"):
        F.live_allocation(w["j"], w["sid"], changed)


def test_a_strategy_outside_the_factory_has_no_per_version_allocation(tmp_path, monkeypatch):
    from tests.test_entry_recovery import setup  # noqa: F401
    import tests.test_entry_recovery as T
    from trader.core.config import load_config
    from trader.core.journal import Journal
    monkeypatch.setattr("trader.engine.executor.time.sleep", lambda _: None)
    j = Journal(tmp_path / "legacy.db")
    from tests.authority_legacy_fixtures import seed
    seed(j)
    ex = Venue()
    manager, _ = bind(j, ex, load_config())
    d = Decision("d", "c", "BTC/USDT", Action.BUY, 1., .5, .8, [], [])
    with j._tx() as db:
        db.execute("INSERT INTO cycles(id,ts,symbol) VALUES ('c','2026-09-16','BTC/USDT')")
    d.instrument_binding_json = A.proposal_binding(j, d.symbol)
    j.log_decision(d)
    p = manager.authorize_entry(d, 2., 2., 95., 110., "strategy", reference=REF)
    assert p.payload()["allocation"] is None
    assert A.current_allocation(j, load_config(), "strategy") is None


# ── the Kernel request is already inside the allocation ─────────────────
from tests import test_entry_geometry_end_to_end as G  # noqa: E402
from tests.test_entry_geometry_end_to_end import _journal as _geometry_journal  # noqa: E402,F401


class _SizedExec(G._Exec):
    def open(self, d, amount, *a, **kw):
        self.amount = amount
        return super().open(d, amount, *a, **kw)


def _kernel(min_notional):
    k = G._kernel({"donchian": G._DONCHIAN}, min_notional=min_notional)
    k.risk.leverage = 5
    k.executor = _SizedExec()
    return k


def _snap():
    return G._Snap({"15m": G._bars(60, 0.4), "4h": G._bars(60, 8.0)})


def test_the_kernel_hands_the_executor_only_the_allocated_share(monkeypatch):
    monkeypatch.setattr(A, "current_allocation", lambda *a: {"fraction": "0.1"})
    k = _kernel(min_notional=0.1)
    assert k._try_enter(G._D(Action.BUY, G._SIG), _snap(), 5000.0, 0) is True
    assert k.executor.amount == pytest.approx(0.1)            # Risk sized 1.0
    assert k.executor.amount <= 1.0 * 0.1


def test_an_allocation_share_below_min_notional_is_not_traded(monkeypatch):
    monkeypatch.setattr(A, "current_allocation", lambda *a: {"fraction": "0.1"})
    k = _kernel(min_notional=10)                              # 0.1 * 100 / 5 = 2 < 10
    d = G._D(Action.BUY, G._SIG)
    assert k._try_enter(d, _snap(), 5000.0, 0) is False
    assert "allocation-sized below min notional" in d.skip_reason
    assert k.executor.opened is None


def test_a_version_that_is_not_live_authorized_never_reaches_the_executor(monkeypatch):
    def refuse(*a):
        raise ValueError("version_not_live_authorized")
    monkeypatch.setattr(A, "current_allocation", refuse)
    k = _kernel(min_notional=0.1)
    d = G._D(Action.BUY, G._SIG)
    assert k._try_enter(d, _snap(), 5000.0, 0) is False
    assert "version_not_live_authorized" in d.skip_reason and k.executor.opened is None
