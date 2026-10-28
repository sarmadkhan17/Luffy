"""Frequent management ticks preserve frozen native-bar holding units."""
from dataclasses import asdict
import pytest
from tests.test_managed_hedge import policy,bar
from tests.test_compose_hedge import spec
from trader.research.compose import hedge as H
from trader.research.closure import ClosureRefused


def primary():
    book=H.HedgeBook(spec(management=policy(),ratio=dict(method='fixed',value=.5),
                          rebalance=dict(min_interval_bars=0)))
    orders=book.step(0,bar(signal=1))
    book.apply_fills({o.oid:o.qty for o in orders})
    return book


def test_intrabar_loss_can_trigger_conditional_protection():
    book=primary()
    orders=book.step(0,bar(p=97.,equity=970.))
    assert len(orders)==1 and orders[0].reason=='risk_management_activation'
    assert orders[0].leg=='hedge'


def test_many_ticks_do_not_turn_minutes_into_native_bar_timeouts():
    book=primary();seen=[]
    for _ in range(120):
        orders=book.step(0,bar(p=97.,equity=970.))
        assert not any(o.kind in ('UNWIND','REDUCE_PRIMARY') for o in orders)
        seen.extend(o.oid for o in orders)
        book.apply_fills({o.oid:0. for o in orders})
    assert len(seen)==len(set(seen)) and book.leg_wait==1
    for native_bar in range(1,4):
        orders=book.step(native_bar,bar(p=97.,equity=970.))
        if native_bar<3:assert not any(o.kind=='REDUCE_PRIMARY' for o in orders)
        else:assert any(o.kind=='REDUCE_PRIMARY' for o in orders)
        book.apply_fills({o.oid:0. for o in orders})


def test_native_clock_survives_restore_and_refuses_backwards_input():
    book=primary();book.step(2,bar())
    restored=H.HedgeBook(**asdict(book));before=asdict(restored)
    with pytest.raises(ClosureRefused,match='native_clock_invalid'):
        restored.step(1,bar())
    assert asdict(restored)==before
