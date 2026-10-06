"""RISK-01: new exposure needs a current, exactly bound Risk permission.

Offline. Each refusal check asserts nothing reached the venue; each mutant
removes one Risk-side guard and must be caught by the matching check.
"""
import ast
import inspect
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from ccxt import InsufficientFunds, RequestTimeout

from tests.test_entry_recovery import setup  # noqa: F401
from tests.entry_authority_fixtures import permission
from trader.core.config import load_config
from trader.core.types import Action, ControlState
from trader.engine import entry_authority as A
from trader.engine.risk import RiskManager


def go(e, d, kw, amount=2., atr=2., stop=95., target=110.):
    return e.open(d, amount, atr, stop, target, 'strategy', 'strategy', **kw)


def _entries(ex):
    return [s for s in ex.sent if not s[4].get('reduceOnly') and 'stopLossPrice' not in s[4]]


# ── checks (shared by refusal tests and mutant detection) ────────────────
def check_not_issued_by_this_risk(setup):
    """Permission minted by another Risk instance (restart / foreign issuer)."""
    ex, j, e, d = setup
    other = RiskManager(load_config(), j)
    kw = dict(reference=dict(price=100.),
              risk_permission=other.authorize_entry(d, 2., 2., 95., 110., 'strategy', reference=dict(price=100.)))
    assert go(e, d, kw) is None and not ex.sent, 'foreign Risk permission submitted'


def check_stale_permission(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    e.risk_manager.release_max_age_s = -1.0  # proof older than its freshness bound
    assert go(e, d, kw) is None and not ex.sent, 'stale permission submitted'
    assert d.skip_reason == 'risk_proof_stale'


def check_expired_capability(setup, monkeypatch):
    ex, j, e, d = setup
    kw = permission(e, d)
    real = A.time.time
    monkeypatch.setattr(A, 'time', SimpleNamespace(time=lambda: real() + 3600, monotonic=A.time.monotonic))
    assert go(e, d, kw) is None and not ex.sent, 'expired capability submitted'


def check_account_changed(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    obs = json.loads(j.kv_get('account_observation'))
    obs['account_scope'] = 'other-account'
    j.kv_set('account_observation', json.dumps(obs))
    assert go(e, d, kw) is None and not ex.sent, 'permission survived account change'


def mutated(monkeypatch, fn_name, replacements):
    fn = getattr(A, fn_name)
    code = inspect.getsource(fn)
    for old, new in replacements:
        assert old in code, old
        code = code.replace(old, new)
    scope = dict(A.__dict__)
    exec(compile(code, '<risk01-mutant>', 'exec'), scope)
    monkeypatch.setattr(A, fn_name, scope[fn_name])


# ── refusals ─────────────────────────────────────────────────────────────
def test_no_permission_refused_even_when_active(setup):
    ex, j, e, d = setup
    assert j.kv_get('control_state') == ControlState.ACTIVE.value
    assert go(e, d, {}) is None
    assert d.skip_reason == 'exact_risk_permission_required' and not ex.sent


def test_forged_and_non_permission_objects_refused(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    for forged in (None, {'allowed': '2'}, kw['risk_permission'].payload_json, object()):
        assert go(e, d, dict(kw, risk_permission=forged)) is None
    assert not ex.sent


def test_permission_from_another_risk_instance_refused(setup):
    check_not_issued_by_this_risk(setup)


def test_stale_permission_refused(setup):
    check_stale_permission(setup)


def test_expired_capability_refused(setup, monkeypatch):
    check_expired_capability(setup, monkeypatch)


def test_account_change_after_permission_refused(setup):
    check_account_changed(setup)


def test_other_instrument_refused(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    assert go(e, replace(d, symbol='ETH/USDT'), kw) is None
    assert not ex.sent


@pytest.mark.parametrize('field,value', [('atr', 3.), ('stop', 96.), ('target', 120.)])
def test_changed_geometry_refused(setup, field, value):
    ex, j, e, d = setup
    kw = permission(e, d)
    assert go(e, d, kw, **{field: value}) is None
    assert d.skip_reason == 'risk_intent_or_capability_mismatch' and not ex.sent


def test_other_decision_refused(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    assert go(e, replace(d, id='other'), kw) is None
    assert not ex.sent


def test_direction_and_quantity_are_bound(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    assert go(e, replace(d, action=Action.SELL), kw, stop=105., target=90.) is None
    assert go(e, d, kw, amount=2.5) is None
    assert d.skip_reason == 'risk_size_exceeded' and not ex.sent


# ── retry / restart cannot reuse a spent or failed permission ────────────
def test_retry_after_definite_rejection_cannot_reuse_permission(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    ex.entry_error = InsufficientFunds('definite')
    assert go(e, d, kw) is None
    ex.entry_error = None
    assert go(e, d, kw) is None
    assert len(_entries(ex)) == 1 and d.skip_reason == 'logical_action_already_reserved'


def test_retry_after_ambiguous_submission_never_resubmits(setup):
    ex, j, e, d = setup
    kw = permission(e, d)
    ex.entry_error = RequestTimeout('ambiguous')
    go(e, d, kw)
    ex.entry_error = None
    assert go(e, d, kw) is None
    assert len(_entries(ex)) == 1


# ── mutants: removing a Risk-side guard must be detected ─────────────────
def test_mutant_issuer_registry_removed_is_detected(setup, monkeypatch):
    mutated(monkeypatch, 'validate', [
        ("if getattr(manager, '_entry_permissions', {}).get(digest(raw)) is not permission:",
         "if False:"),
        ("if raw['risk_issuer'] != manager._identity or manager.journal.db_path != executor.journal.db_path:",
         "if False:"),
        # defence in depth: the release proof also carries the issuer identity
        ("reason = manager._revalidate(permission.release, conn)", "reason = None")])
    with pytest.raises(AssertionError):
        check_not_issued_by_this_risk(setup)


def test_mutant_freshness_revalidation_removed_is_detected(setup, monkeypatch):
    mutated(monkeypatch, 'validate', [("reason = manager._revalidate(permission.release, conn)", "reason = None")])
    with pytest.raises(AssertionError):
        check_stale_permission(setup)


def test_mutant_capability_freshness_removed_is_detected(setup, monkeypatch):
    # Neutralise every time bound on the capability (registry and leverage
    # receipt) and the independently timed account/venue receipts.
    mutated(monkeypatch, 'capability', [
        ("or not raw['observed_at_ms'] <= now_ms <= min(raw['valid_until_ms'], raw['observed_at_ms'] + MAX_SNAPSHOT_AGE_MS)", ""),
        ("or not lev['observed_at_ms'] <= now_ms <= min(lev['valid_until_ms'], lev['observed_at_ms'] + int(ACCOUNT_STALE_S*1000))", "")])
    ex, j, e, d = setup
    frozen = A.authoritative_inputs(j, json.loads(j.kv_get(A.CAP_KEY + d.symbol)))
    monkeypatch.setattr(A, 'authoritative_inputs', lambda journal, cap: frozen)
    with pytest.raises(AssertionError):
        check_expired_capability(setup, monkeypatch)


def test_mutant_gate_removed_in_submit_is_detected(setup, monkeypatch):
    monkeypatch.setattr(A, 'validate', lambda *a, **k: None)
    mutated(monkeypatch, 'submit', [("raw = validate(executor, permission, decision, amount, atr, stop, target,\n"
                                     "                           strategy_id, identity, reference, conn)",
                                     "raw = permission.payload()")])
    with pytest.raises(AssertionError):
        check_stale_permission(setup)


# ── static: nothing but the Kernel reaches Executor.open; only the executor reaches submit ──
def test_entry_callers_are_closed_set():
    open_callers, submit_callers = {}, set()
    for path in Path('trader').rglob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(a.name == 'submit' for a in node.names) \
                    and (node.module or '').endswith('entry_authority'):
                submit_callers.add(path.as_posix())
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ('open', '_open_serialized', '_open_locked')
                    and 'executor' in ast.unparse(node.func.value).lower()):
                open_callers.setdefault(path.as_posix(), []).append(node)
    assert submit_callers == {'trader/engine/executor.py'}
    assert set(open_callers) == {'trader/kernel.py'}
    for call in open_callers['trader/kernel.py']:
        assert any(k.arg == 'risk_permission' for k in call.keywords)
