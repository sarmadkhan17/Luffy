"""No unapproved version trades, and no lifecycle check bypasses the controls.

Isolated simulation: a TEST-ONLY venue double and a temporary journal. Every
refusal asserts that nothing reached the venue. Real live execution stays off:
nothing here enables it in any configuration or database.
"""
import inspect
import json
import time

import pytest
from ccxt import RequestTimeout

from trader.core.types import Action, ControlState, Decision, MarketType
from trader.engine import entry_authority as A
from trader.engine.executor import Executor
from trader.strategy import factory_handoff as F
from tests.authority_capacity_fixtures import NOW
from tests.authority_factory_fixtures import _install, _trades, T0, DAY, cfg  # noqa: F401
from tests.entry_authority_fixtures import bind
from tests.test_entry_recovery import Venue
from tests.test_sol1_allocation_ceiling import live, entries, REF
from tests.test_sol1_strategy_lifecycle import (
    _approved, _deploy, _journal, _validated)

STAGES = ["validated_not_installed", "shadow_probation", "approval_required",
          "owner_approved_not_activated", "active_without_deployment",
          "deployed_then_paused", "deployed_then_retired"]


def _stage(tmp_path, cfg, monkeypatch, stage):
    j = _journal(tmp_path)
    if stage == "validated_not_installed":
        out, _q = _validated(j, cfg)
        return j, F.load_version(j, out["version_id"])
    if stage in ("shadow_probation", "approval_required"):
        out, _q = _validated(j, cfg)
        rec = _install(j, out["version_id"])
        if stage == "approval_required":
            _trades(j, rec["strategy_id"], [10.0] * 7 + [-5.0] * 3, T0,
                    spec_hash=rec["spec_hash"])
            p = F.evaluate_probation(j, cfg, out["version_id"], at_ms=T0 + 30 * DAY)
            assert p["status"] == F.P_SATISFIED
        return j, F.load_version(j, out["version_id"])
    v, _p, _d = _approved(j, cfg)
    if stage == "owner_approved_not_activated":
        return j, v
    from tests.test_sol1_strategy_lifecycle import _activated  # noqa: F401
    raise AssertionError(stage)


def _attempt(j, v, cfg, venue):
    """Everything a caller could try for this version, all of which must fail."""
    manager, _ = bind(j, venue, cfg)
    e = Executor(venue, j, cfg, MarketType.FUTURES, risk_manager=manager)
    d = Decision("d", "c", "BTC/USDT", Action.BUY, 1., .5, .8, [], [])
    with j._tx() as db:
        db.execute("INSERT OR IGNORE INTO cycles(id,ts,symbol) VALUES ('c','2026-09-16','BTC/USDT')")
    d.instrument_binding_json = A.proposal_binding(j, d.symbol)
    j.log_decision(d)
    sid = v["strategy_id"]
    assert F.live_entry_block(j, sid, cfg=cfg)
    with pytest.raises(ValueError):
        manager.authorize_entry(d, 1., 2., 95., 110., sid, reference=REF)
    assert e.open(d, 1., 2., 95., 110., sid, "version", reference=REF,
                  risk_permission=None) is None
    assert venue.sent == [] and j.open_trades() == []


@pytest.mark.parametrize("stage", STAGES[:4])
def test_a_version_short_of_live_authorization_cannot_trade(tmp_path, cfg, monkeypatch, stage):
    j, v = _stage(tmp_path, cfg, monkeypatch, stage)
    _attempt(j, v, cfg, Venue())


def test_an_active_version_without_the_owners_deployment_cannot_trade(tmp_path, cfg, monkeypatch):
    from tests.test_sol1_strategy_lifecycle import _activated
    j, v, _risk, _rid = _activated(tmp_path, cfg, monkeypatch)
    assert F.live_entry_block(j, v["strategy_id"], cfg=cfg) == "version_first_live_execution_not_enabled"
    _attempt(j, v, cfg, Venue())


@pytest.mark.parametrize("end", ["PAUSED", "RETIRED"])
def test_a_paused_or_retired_version_cannot_trade_even_when_deployed(tmp_path, cfg, monkeypatch, end):
    w = live(tmp_path, cfg, monkeypatch)
    assert F.live_entry_block(w["j"], w["sid"], cfg=cfg) is None
    if end == "PAUSED":
        F.govern_version(w["j"], cfg, w["vid"], "PAUSED", actor="strategy_governor",
                         reason_code="TEST-ONLY", at_ms=NOW + 1000)
    else:
        F.retire_version(w["j"], w["vid"], F.RETIRED, reason_code="TEST-ONLY",
                         actor="strategy_governor", at_ms=NOW + 1000)
    assert F.live_entry_block(w["j"], w["sid"], cfg=cfg)
    with pytest.raises(ValueError):
        w["manager"].authorize_entry(w["d"], 1., 2., 95., 110., w["sid"], reference=REF)
    assert w["e"].open(w["d"], 1., 2., 95., 110., w["sid"], "version",
                       reference=REF, risk_permission=None) is None
    assert w["ex"].sent == []


def test_a_sibling_strategy_cannot_use_the_approved_versions_authority(tmp_path, cfg, monkeypatch):
    """The approval and allocation belong to one version; a different strategy
    id, or a version derived from it, inherits nothing."""
    w = live(tmp_path, cfg, monkeypatch)
    p = w["manager"].authorize_entry(w["d"], 1000., 2., 95., 110., w["sid"], reference=REF)
    other = "spec_some_other_strategy"
    assert F.live_entry_block(w["j"], other, cfg=cfg) == "VERSIONED_AUTHORITY_REQUIRED"
    assert w["e"].open(w["d"], 0.001, 2., 95., 110., other, "version",
                       reference=REF, risk_permission=p) is None
    assert entries(w["ex"]) == []


@pytest.mark.parametrize("state", ["FROZEN", "HALTED"])
def test_frozen_and_halted_stop_an_eligible_version_before_and_after_authorization(
        tmp_path, cfg, monkeypatch, state):
    w = live(tmp_path, cfg, monkeypatch)
    # authorized while ACTIVE ...
    p = w["manager"].authorize_entry(w["d"], 1000., 2., 95., 110., w["sid"], reference=REF)
    allowed = float(p.payload()["allowed"])
    # ... then the owner (or Risk) freezes/halts before submission
    w["j"].kv_set("control_state", state)
    assert w["e"].open(w["d"], allowed, 2., 95., 110., w["sid"], "version",
                       reference=REF, risk_permission=p) is None
    assert w["ex"].sent == []
    # and no new permission can be minted while it stands
    with pytest.raises(ValueError):
        w["manager"].authorize_entry(w["d"], 1., 2., 95., 110., w["sid"], reference=REF)
    # lifecycle operations do not lift it
    _deploy(w["j"], cfg, w["v"])
    assert w["j"].kv_get("control_state") == state


def test_lifecycle_code_never_writes_the_control_state():
    import re
    from trader.strategy import qualification, promotion, capacity
    for module in (F, qualification, promotion, capacity):
        src = inspect.getsource(module)
        assert not re.search(r"kv_set\(\s*['\"]control_state", src), module.__name__
        assert "ControlStateMachine" not in src and "INTO state_kv" not in src
        assert "log_control_event" not in src and "set_control" not in src


def test_a_missing_or_wrong_side_stop_is_refused_for_an_eligible_version(tmp_path, cfg, monkeypatch):
    w = live(tmp_path, cfg, monkeypatch)
    for stop in (0., -1., 100., 105.):                      # none, negative, at/above a long's price
        with pytest.raises(ValueError):
            w["manager"].authorize_entry(w["d"], 1000., 2., stop, 110., w["sid"], reference=REF)
    assert w["ex"].sent == []


def test_a_successful_eligible_entry_is_protected_by_a_native_stop(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr("trader.engine.executor.time.sleep", lambda _: None)
    w = live(tmp_path, cfg, monkeypatch)
    p = w["manager"].authorize_entry(w["d"], 1000., 2., 95., 110., w["sid"], reference=REF)
    amount = float(p.payload()["allowed"])
    pos = w["e"].open(w["d"], amount, 2., 95., 110., w["sid"], "version",
                      reference=REF, risk_permission=p)
    assert pos is not None and len(entries(w["ex"])) == 1
    stops = [s for s in w["ex"].sent if s[4].get("reduceOnly") or s[1] == "STOP_MARKET"
             or "stopLossPrice" in s[4]]
    assert stops, "an entry must leave a reduce-only protective stop on the venue"
    assert all(float(s[3]) <= amount for s in stops)


def test_pending_execution_recovery_blocks_further_entries_of_an_eligible_version(
        tmp_path, cfg, monkeypatch):
    monkeypatch.setattr("trader.engine.executor.time.sleep", lambda _: None)
    w = live(tmp_path, cfg, monkeypatch)
    w["ex"].entry_error = RequestTimeout("accepted, response lost")
    p = w["manager"].authorize_entry(w["d"], 1000., 2., 95., 110., w["sid"], reference=REF)
    amount = float(p.payload()["allowed"])
    assert w["e"].open(w["d"], amount, 2., 95., 110., w["sid"], "version",
                       reference=REF, risk_permission=p) is None
    assert w["e"].recovery_pending()
    sent = len(w["ex"].sent)
    # lifecycle activity does not clear recovery and no second order goes out
    _deploy(w["j"], cfg, w["v"])
    assert F.live_entry_block(w["j"], w["sid"], cfg=cfg) is None
    d2 = Decision("d2", "c", "BTC/USDT", Action.BUY, 1., .5, .8, [], [])
    assert w["e"].open(d2, amount, 2., 95., 110., w["sid"], "version",
                       reference=REF, risk_permission=p) is None
    assert d2.skip_reason == "execution_recovery_pending"
    assert len(w["ex"].sent) == sent and w["e"].recovery_pending()
