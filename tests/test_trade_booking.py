"""Booking receipts are durable evidence, not inferred complete trade outcomes."""
import json
import sqlite3
import subprocess

import pytest

from trader.core.journal import Journal
from trader.core.types import Position, Side
from trader.engine.accounting import digest
from trader.engine.booking import assess, export, monetary_total, replay, replay_export
from trader.engine.reconcile import venue_realized_pnl
from tests.test_exit_books_the_fill import book, _fill, _row


def receipts(j):
    return [json.loads(r['payload']) for r in j.query('SELECT payload FROM trade_accounting_bookings ORDER BY id')]


def test_partial_and_final_receipts_survive_restart_with_exact_exit_evidence(book):
    ex,j,e=book
    ex.next_fills=[_fill('buy',7.3,136,.3971,20.808)]
    assert e.close_partial(_row(j),136)
    ex.next_fills=[_fill('buy',7.4,137,.4055,7.261)]
    assert e.close(_row(j))
    restored=Journal(j.db_path)
    rs=receipts(restored)
    assert [r['kind'] for r in rs]==['entry','align_delta','close:signal_exit']
    assert rs[1]['assessment']['status']=='verified_leg_fills_only'
    assert rs[2]['assessment']['status']=='verified_leg_fills_only'
    assert rs[1]['after']['amount']==137
    assert rs[2]['before']['realized_pnl']==pytest.approx(20.808-.3971)
    assert len(rs[2]['evidence']['venue_window_observation']['fills'])==3
    assert rs[2]['evidence']['booked_pnl_basis']=='symbol_time_window_subtotal_unattributed'
    for r in rs:
        assert replay(r)['learning_eligible'] is False
        assert replay(r)['net_economic_pnl_usdt'] is None


def test_missing_fee_no_longer_silently_means_zero(book):
    ex,j,e=book
    f=_fill('buy',7.3,136,.3971,20.808);f['info'].pop('commission')
    ex.next_fills=[f]
    assert e.close_partial(_row(j),136)
    assert _row(j)['realized_pnl'] != pytest.approx(20.808)
    r=receipts(j)[-1]
    assert r['assessment']['status']=='unverified'
    assert 'missing_or_invalid_pnl_fee_currency' in r['assessment']['reasons']
    assert j.query("SELECT * FROM brain_events WHERE kind='pnl_estimated'")


def test_final_close_partial_uses_actual_leg_net_and_keeps_position(book):
    ex,j,e=book
    ex.next_fills=[_fill('buy',7.3,100,.7,15.3)]
    assert not e.close(_row(j))
    r=_row(j)
    assert r['amount']==173
    assert r['status']=='open'
    assert r['realized_pnl']==pytest.approx(14.6)
    assert receipts(j)[-1]['assessment']['status']=='verified_leg_fills_only'


def test_native_ghost_and_panic_labels_cannot_make_estimates_actual(tmp_path):
    j=Journal(tmp_path/'j.db')
    for kind in ['sl_fill','reconciled_ghost','panic']:
        j.add_trade(Position(id=kind,symbol='BTC/USDT',side=Side.LONG,amount=1,entry_price=100,notional_usdt=100))
        j.close_trade(kind,200,100,kind)
        r=receipts(j)[-1]
        assert r['kind']=='close:'+kind
        assert r['assessment']['status']=='unverified'
        assert r['assessment']['net_economic_pnl_usdt'] is None


def test_receipt_failure_rolls_back_booking_and_tp1_flag(book,monkeypatch):
    ex,j,e=book
    ex.next_fills=[_fill('buy',7.3,136,.3971,20.808)]
    def fail(*a,**k):raise OSError('storage failure')
    monkeypatch.setattr('trader.engine.booking.persist',fail)
    assert not e.close_partial(_row(j),136)
    row=_row(j)
    assert row['amount']==273 and row['tp1_done']==0 and row['realized_pnl']==0
    assert len(receipts(j))==1


def test_duplicate_final_journal_callback_does_not_double_count(book):
    ex,j,e=book
    j.close_trade('pos_avax',7.3,10,'test')
    j.close_trade('pos_avax',7.3,10,'test')
    assert _row(j)['realized_pnl']==10
    assert len(receipts(j))==2


def test_replay_refuses_rehashed_claim_and_export_promotion(book):
    ex,j,e=book
    r=receipts(j)[0];r['assessment']['status']='verified_actual'
    r['sha256']=digest({k:v for k,v in r.items() if k!='sha256'})
    with pytest.raises(ValueError,match='assessment_mismatch'):replay(r)
    with sqlite3.connect(j.db_path) as db:a=export(db,'pos_avax')
    assert replay_export(a)['receipts']==1
    a['learning_eligible']=True;a['sha256']=digest({k:v for k,v in a.items() if k!='sha256'})
    with pytest.raises(ValueError,match='incomplete_promoted'):replay_export(a)


def test_cli_source_free_replay_and_read_only_legacy_export(tmp_path):
    path=tmp_path/'legacy.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE trades(id TEXT, realized_pnl REAL, status TEXT)')
        db.execute("INSERT INTO trades VALUES ('old', 12345, 'closed')")
    original=path.read_bytes();out=tmp_path/'receipt.json'
    subprocess.run(['./venv/bin/python','-m','scripts.export_trade_accounting','--journal',str(path),'--trade-id','old','--output',str(out)],check=True,capture_output=True)
    assert path.read_bytes()==original
    a=json.loads(out.read_text())
    assert 'legacy_booking_receipts_absent' in a['reasons']
    path.unlink()
    subprocess.run(['./venv/bin/python','-m','scripts.export_trade_accounting','--replay',str(out)],check=True,capture_output=True)


@pytest.mark.parametrize('field,value',[('commission',None),('realized_pnl',None),('commission_asset','BNB'),('id',None),('commission','nan')])
def test_window_total_refuses_missing_or_unconvertible_fields(field,value):
    fill={'id':'f','commission':'1','realized_pnl':'10','commission_asset':'USDT'}
    fill[field]=value
    assert monetary_total([fill]) is None


def test_numeric_subtotal_deduplicates_but_conflicting_identity_refuses():
    f={'id':'f','commission':'1','realized_pnl':'10','commission_asset':'USDT'}
    assert monetary_total([f,f])==9
    assert monetary_total([f,dict(f,realized_pnl='20')]) is None


def test_history_error_and_full_page_remain_explicit():
    class Bad:
        def fetch_my_trades(self,*a,**kw):raise RuntimeError('SECRET')
    obs={}
    assert venue_realized_pnl(Bad(),'BTC/USDT','2026-09-16T00:00:00+00:00',obs) is None
    assert 'SECRET' not in json.dumps(obs)
    class Full:
        def fetch_my_trades(self,*a,**kw):return [_fill('buy',100,1,1,10)]*1000
    assert venue_realized_pnl(Full(),'BTC/USDT','2026-09-16T00:00:00+00:00',obs) is None
    assert obs['reason']=='history_page_full_retry_required'


def test_partial_unconfirmed_response_cannot_book_hint_or_requested_amount(book):
    ex,j,e=book
    ex.fetch_raises=True
    assert not e.close_partial(_row(j),136,price_hint=7.3)
    assert _row(j)['amount']==273
    assert _row(j)['realized_pnl']==0
    assert j.query("SELECT * FROM control_events WHERE event='partial_fill_unconfirmed'")


def test_duplicate_fill_delivery_cannot_double_reduce_position(book):
    ex,j,e=book
    f=_fill('buy',7.3,100,.7,15.3)
    ex.next_fills=[f,f]
    assert not e.close(_row(j))
    assert _row(j)['amount']==173
    assert _row(j)['realized_pnl']==pytest.approx(14.6)
