"""The leverage the sizing math assumes must be the leverage the venue uses.

History: `risk.leverage` drove the margin figure (`notional / leverage`) while
`set_leverage` was never called, so the venue traded at whatever the account
happened to be set to. An interim fix applied it per symbol before every
order (`Executor._ensure_leverage`).

Stage 1 entry authority (commit 8d49c69) replaced *applying* with *verifying*:
the executor no longer mutates the venue before the final fence, and a
capability is only usable when the venue's own observed leverage and bracket
equal the leverage Risk sized for. The two properties below guard that
contract; the unknown / bracket / stale variants live in
tests/test_stage1_entry_authority.py and tests/test_risk02_exposure_feasibility.py.
"""
import pytest

from tests.entry_authority_fixtures import permission
from tests.test_entry_recovery import setup  # noqa: F401  (real Risk/capability harness)


def test_entry_never_mutates_venue_leverage(setup):
    ex, j, e, d = setup
    calls = []
    ex.set_leverage = lambda *a, **k: calls.append((a, k))
    kw = permission(e, d)
    assert e.open(d, 2., 2., 95., 110., 'strategy', 'strategy', **kw) is not None
    assert calls == [], "entry must verify leverage, not apply it"
    assert not hasattr(e, "_ensure_leverage")


def test_sizing_leverage_must_equal_the_venue_observation(setup):
    ex, j, e, d = setup
    e.risk_manager.leverage += 1     # Risk would now size for a leverage the venue does not hold
    with pytest.raises(ValueError, match="leverage_unverified"):
        permission(e, d)
    assert not ex.sent, "no order may be sent against unverified leverage"
