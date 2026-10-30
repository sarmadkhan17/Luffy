"""Real Risk/Executor/recovery for a strategy's hedge-only reduction choice."""
import pytest
from ccxt import RequestTimeout
from tests.test_entry_recovery import setup  # noqa: F401
from tests.test_coordinated_close import opened
from trader.engine.coordinated_close import close_hedge_once


def test_strategy_hedge_close_recovers_lost_response_without_new_order(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch,leg='hedge')
    ex.close_error=RequestTimeout('accepted response lost')
    assert close_hedge_once(e,tid,'group-fixture','stop-owned')['status']=='ATTEMPTED'
    count=len(ex.sent);ex.close_error=None
    assert close_hedge_once(e,tid,'group-fixture','stop-owned')['status']=='CLOSED'
    assert len(ex.sent)==count
    assert close_hedge_once(e,tid,'group-fixture','stop-owned')['status']=='CLOSED'
    assert len(ex.sent)==count
    assert not j.query("SELECT 1 FROM sqlite_master WHERE name='coordinated_group_unwinds'")


def test_partial_hedge_stop_reduces_remaining_only_on_later_tick(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch,leg='hedge',fraction=.5)
    first=close_hedge_once(e,tid,'group-fixture','stop-owned')
    assert first['status']=='REDUCING' and j.open_trades()[0]['amount']==1.
    second=close_hedge_once(e,tid,'group-fixture','stop-owned')
    assert second['status']=='REDUCING' and j.open_trades()[0]['amount']==.5
    assert first['reduction']['client_order_id']!=second['reduction']['client_order_id']
    assert ex.stops


def test_main_cannot_use_hedge_only_route_to_bypass_whole_group_exit(setup,monkeypatch):
    ex,j,e,tid=opened(setup,monkeypatch);count=len(ex.sent)
    with pytest.raises(ValueError,match='hedge_only_close_required'):
        close_hedge_once(e,tid,'group-fixture','not-hedge')
    assert len(ex.sent)==count
