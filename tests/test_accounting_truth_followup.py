"""Accounting consumer regressions for 8867794 metadata; fixtures only."""
import json
from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient
from tests.owner_frontend_fixture import make_app, TOKEN
from trader.dashboard.economics import read, journal_pnl, booking_receipts
from trader.dashboard.owner_api import read_realized_today

@pytest.mark.parametrize('kind,source', [('reconciled_ghost','journal_entry_not_a_fill'),('panic','journal_entry_not_a_fill'),('align_delta','journal_entry_not_a_fill')])
def test_unknown_placeholder_is_suppressed_in_economics_and_overview(tmp_path,monkeypatch,kind,source):
    app,j,_=make_app(tmp_path,monkeypatch)
    ev=dict(basis='reconcile_ghost_unpriced',pnl_status='UNKNOWN',pnl_value_class='UNKNOWN',exit_price_source=source)
    if kind=='align_delta':
        j.align_trade_amount('t-btc', .005, 300, pnl_delta=0, accounting=ev)
        j.close_trade('t-btc',60000,1,'final')
    else:j.close_trade('t-btc',60000,0,kind,accounting=ev)
    t=dict(j.query("SELECT * FROM trades WHERE id='t-btc'")[0])
    r=read(t,{},booking_receipts(j,'t-btc'))['journal_booked'][0]
    assert r['value'] is None and r['status']=='UNKNOWN'
    assert source in json.dumps(r['coverage'])
    o,_=read_realized_today(j,datetime.now(timezone.utc))
    assert o['value'] is None and o['quality']=='PARTIAL_UNKNOWN'
    c=TestClient(app)
    d=c.get('/owner-api/v1/trades/t-btc/lineage',headers={'x-luffy-token':TOKEN}).json()
    assert d['trade']['realized_pnl'] is None
    assert d['economics']['journal_booked'][0]['status']=='UNKNOWN'
    activity=c.get('/owner-api/v1/overview/activity',headers={'x-luffy-token':TOKEN}).json()
    econ=next(s['journal_economics'] for s in activity['strategies'] if s['id']==t['strategy_id'])
    assert econ['realized_pnl'] is None and econ['pnl_value_class']=='UNKNOWN'

@pytest.mark.parametrize('price_source',['ticker_last_not_a_fill','order_response_price'])
def test_derived_estimate_remains_estimated_and_separate(tmp_path,monkeypatch,price_source):
    _,j,_=make_app(tmp_path,monkeypatch)
    ev=dict(basis='panic_order_unconfirmed',pnl_status='ESTIMATE',pnl_value_class='DERIVED_ESTIMATE',exit_price_source=price_source)
    j.close_trade('t-btc',61000,10,'panic',accounting=ev)
    t=dict(j.query("SELECT * FROM trades WHERE id='t-btc'")[0]);r=read(t,{},booking_receipts(j,'t-btc'))
    assert r['journal_booked'][0]['value']==10 and r['journal_booked'][0]['status']=='DERIVED_ESTIMATE'
    assert price_source in json.dumps(r['journal_booked'][0]['coverage'])
    assert all(v['value'] is None for v in r['venue_monetary'])
    o,_=read_realized_today(j,datetime.now(timezone.utc));assert o['value']==12 and o['quality']=='DERIVED_ESTIMATE'

@pytest.mark.parametrize('metadata',[{'pnl_status':'UNKNOWN'}, {'pnl_value_class':'UNKNOWN'}, {'exit_price_source':'journal_entry_not_a_fill'}, {'basis':'reconcile_ghost_unpriced'}])
def test_each_unknown_marker_overrides_numeric_zero(metadata):
    p=journal_pnl(dict(id='t',realized_pnl=0,**metadata));assert p['value'] is None and p['status']=='UNKNOWN'

def test_tampered_early_receipt_cannot_disappear_after_fifty_later_legs(tmp_path,monkeypatch):
    _,j,_=make_app(tmp_path,monkeypatch)
    for _ in range(55):j.align_trade_amount('t-btc',.01,600,accounting={'basis':'estimated_order_booking'})
    with j._tx() as c:c.execute("UPDATE trade_accounting_bookings SET payload='{}' WHERE id=(SELECT MIN(id) FROM trade_accounting_bookings WHERE trade_id='t-btc')")
    t=dict(j.query("SELECT * FROM trades WHERE id='t-btc'")[0])
    assert journal_pnl(t,booking_receipts(j,'t-btc'))['value'] is None
