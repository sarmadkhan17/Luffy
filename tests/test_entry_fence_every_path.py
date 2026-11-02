"""General order path: a blocked persisted control state refuses an entry on EVERY entry method, sending nothing.

The permission is issued while ACTIVE, then the control state is persisted as FROZEN / HALTED / RECOVERY; the
executor must refuse (at its own boundary for `open`, at submit's fence for `increase`) even though a currently
valid Risk permission is in hand. This pins the behaviour noted as unpinned in docs/guard-review-20261102.md.
It applies to all strategies equally and says nothing about any particular strategy.
"""
import pytest

from tests.test_coordinated_increase import increase, prepared  # noqa: F401
from tests.entry_authority_fixtures import permission
from tests.test_entry_recovery import setup  # noqa: F401  (shared pytest fixture)

BLOCKED = ["FROZEN", "HALTED", "RECOVERY"]


@pytest.mark.parametrize("state", BLOCKED)
def test_increase_is_refused_in_a_blocked_control_state_and_sends_nothing(setup, monkeypatch, state):
    ex, j, e, tid, identity, d, permission = prepared(setup, monkeypatch)
    before_orders, before_amount = len(ex.sent), j.open_trades()[0]["amount"]
    j.kv_set("control_state", state)
    with pytest.raises(ValueError, match=f"state={state}"):
        increase(e, tid, identity, d, permission)
    assert len(ex.sent) == before_orders, "no order of any kind may reach the venue"
    assert j.open_trades()[0]["amount"] == before_amount, "the position must be unchanged"


@pytest.mark.parametrize("state", BLOCKED)
def test_open_is_refused_in_a_blocked_control_state_and_sends_nothing(setup, state):
    ex, j, e, d = setup
    kw = permission(e, d)                                  # a valid permission, issued while ACTIVE
    j.kv_set("control_state", state)
    assert e.open(d, 2., 2., 95., 110., "strategy", "strategy", **kw) is None
    assert d.skip_reason.startswith(f"state={state}"), d.skip_reason
    assert not ex.sent, "no order of any kind may reach the venue"


def test_open_is_not_refused_while_active(setup):
    """Control: the refusals above come from the control state, not from the fixture."""
    ex, j, e, d = setup
    kw = permission(e, d)
    assert j.kv_get("control_state", "ACTIVE") == "ACTIVE"
    assert e.open(d, 2., 2., 95., 110., "strategy", "strategy", **kw) is not None or ex.sent


def test_the_same_entry_is_not_refused_while_active(setup, monkeypatch):
    """Control: the refusals above come from the control state, not from the fixture."""
    ex, j, e, tid, identity, d, permission = prepared(setup, monkeypatch)
    from tests.test_coordinated_increase import submit_fixture
    submit_fixture(ex, monkeypatch)
    assert j.kv_get("control_state", "ACTIVE") == "ACTIVE"
    assert increase(e, tid, identity, d, permission)["status"] == "RECOVERY_PENDING"
