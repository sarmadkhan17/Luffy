"""Exact recovery accounting: synthetic venue evidence, no exchange access."""
import json

import pytest

from trader.engine.accounting import capture, digest, reconcile, replay
from trader.engine.executor import Executor
from trader.core.config import load_config
from trader.core.journal import Journal
from trader.core.types import MarketType
from tests.test_entry_recovery import setup, enter


def record():
    return {'id': 'intent', 'symbol': 'BTC/USDT', 'created_ms': 100,
            'flat_verified_ms': 900, 'position': {'side': 'long', 'decision_id': 'd'},
            'entry_observation': {'id': 'entry', 'filled': 2, 'status': 'closed'},
            'close_orders': [{'id': 'exit1', 'filled': .5, 'status': 'canceled'},
                             {'id': 'exit2', 'filled': 1.5, 'status': 'closed'}]}


def fills():
    return [dict(id=str(i), symbol='BTC/USDT:USDT', timestamp=200+i,
                 side=side, amount=qty, price=price, order=oid,
                 realized_pnl=pnl, commission=fee, commission_asset='USDT')
            for i, (oid, side, qty, price, pnl, fee) in enumerate([
                ('entry', 'buy', 2, 100, 0, .1),
                ('exit1', 'sell', .5, 90, -5, .025),
                ('exit2', 'sell', 1.5, 110, 15, .075)])]


def test_exact_partial_round_trip_deduplicates_and_preserves_funding_unknown():
    result = reconcile(record(), fills()+[fills()[0]])
    assert result['status'] == 'fills_verified_funding_unknown'
    assert result['gross_realized_usdt'] == 10
    assert result['commission_usdt'] == pytest.approx(.2)
    assert result['fill_net_excluding_funding_usdt'] == pytest.approx(9.8)
    assert result['net_economic_pnl_usdt'] is None
    assert result['funding_usdt'] is None
    assert result['learning_eligible'] is False
    assert len(result['fills']) == 3


@pytest.mark.parametrize('field,value', [('commission',None), ('realized_pnl',None),
    ('commission_asset','BNB'), ('amount', float('nan')), ('price',0),
    ('timestamp',99), ('symbol','ETH/USDT'), ('side','sell'), ('id',None)])
def test_bad_or_missing_exact_fields_cannot_become_actual_pnl(field,value):
    fs=fills();fs[0][field]=value
    result=reconcile(record(),fs)
    assert result['status']=='incomplete'
    assert result['fill_net_excluding_funding_usdt'] is None


def test_missing_close_history_or_fill_keeps_retry_explicit():
    r=record();r['close_orders'].pop(0)
    assert 'entry_exit_quantity_mismatch' in reconcile(r,fills())['reasons']
    assert 'order_fill_quantity_missing_or_mismatched' in reconcile(record(),fills()[:-1])['reasons']


def test_unrelated_orders_do_not_contaminate_and_conflicting_ids_refuse():
    unrelated=dict(fills()[0],id='other',order='unrelated',realized_pnl=999)
    assert reconcile(record(),fills()+[unrelated])['gross_realized_usdt']==10
    conflict=dict(fills()[0],price=101)
    assert 'conflicting_fill_identity' in reconcile(record(),fills()+[conflict])['reasons']


def test_short_and_multi_fill_entry():
    r=record();r['position']['side']='short'
    fs=fills()
    for f in fs:f['side']='sell' if f['side']=='buy' else 'buy'
    fs[0]['amount']=1
    fs.append(dict(fs[0],id='extra'))
    assert reconcile(r,fs)['status']=='fills_verified_funding_unknown'


class History:
    def __init__(self, rows):self.rows=rows
    def fetch_my_trades(self,symbol,since,limit):
        assert (symbol,since,limit)==('BTC/USDT',100,1000)
        return self.rows


def raw_fills():
    return [dict(f,info={'realizedPnl':f['realized_pnl'],'commission':f['commission'],
                         'commissionAsset':f['commission_asset']}) for f in fills()]


def test_capture_and_source_free_replay_reject_rehashed_assessment():
    artifact=capture(record(),History(raw_fills()))
    assert replay(json.loads(json.dumps(artifact)))['gross_realized_usdt']==10
    artifact['assessment']['gross_realized_usdt']=999
    artifact['sha256']=digest({k:v for k,v in artifact.items() if k!='sha256'})
    with pytest.raises(ValueError,match='replay_mismatch'):replay(artifact)


def test_fetch_error_is_redacted_and_later_capture_recovers():
    class Offline:
        def fetch_my_trades(self,*a,**kw):raise RuntimeError('SECRET')
    failed=capture(record(),Offline())
    assert 'SECRET' not in json.dumps(failed)
    assert replay(failed)['status']=='incomplete'
    assert replay(capture(record(),History(raw_fills())))['status']=='fills_verified_funding_unknown'


def test_full_page_cannot_claim_complete_history():
    result=capture(record(),History(raw_fills()+[raw_fills()[0]]*997))
    assert 'history_page_full_retry_required' in result['assessment']['reasons']
    assert result['assessment']['fill_net_excluding_funding_usdt'] is None


def test_recovery_archives_all_closes_across_restart_before_release(setup):
    ex,j,e,d=setup
    ex.stop_error=True
    enter(e,d)
    ex.orders['close']['filled']=.5
    ex.positions[0]['contracts']=1.5
    original=ex.create_order
    def next_close(symbol,typ,side,amount,params=None):
        order=original(symbol,typ,side,amount,params)
        order['id']='close2';ex.orders['close2']=order
        return order
    ex.create_order=next_close
    e.recover_entries()
    assert e.recovery.pending()['close_orders'][0]['id']=='close'
    ex.positions=[]
    restarted=Executor(ex,Journal(j.db_path),load_config(),MarketType.FUTURES)
    restarted.recover_entries()
    assert not restarted.recovery_pending()
    rows=j.query('SELECT * FROM execution_accounting')
    assert len(rows)==1
    archived=json.loads(rows[0]['payload'])
    assert [x['id'] for x in archived['close_orders']]==['close','close2']
    assert archived['position']['decision_id']==d.id
    assert archived['status']=='fills_pending_funding_unknown'
    restarted.recover_entries()
    assert len(j.query('SELECT * FROM execution_accounting'))==1
    assert not j.open_trades()


def test_archive_failure_preserves_recovery_barrier(setup,monkeypatch):
    ex,j,e,d=setup
    ex.stop_error=True;enter(e,d);ex.positions=[]
    def fail(db,intent):raise OSError('disk full')
    monkeypatch.setattr('trader.engine.accounting.archive_flat',fail)
    e.recover_entries()
    assert e.recovery_pending()
    assert not j.query('SELECT * FROM execution_accounting')


def test_nonfinite_venue_response_becomes_explicit_missing_evidence():
    rows=raw_fills();rows[0]['amount']=float('nan')
    artifact=capture(record(),History(rows))
    assert replay(artifact)['status']=='incomplete'
    assert artifact['fills'][0]['amount'] is None


def test_archive_and_release_are_one_transaction(setup, monkeypatch):
    from trader.engine.accounting import archive_flat
    ex,j,e,d=setup
    ex.stop_error=True;enter(e,d);ex.positions=[]
    def fail_after_insert(db,intent):
        archive_flat(db,intent)
        raise OSError('crash after insert')
    monkeypatch.setattr('trader.engine.accounting.archive_flat',fail_after_insert)
    e.recover_entries()
    assert e.recovery_pending()
    assert not j.query('SELECT * FROM execution_accounting')
    monkeypatch.setattr('trader.engine.accounting.archive_flat',archive_flat)
    e.recover_entries()
    assert not e.recovery_pending()
    assert len(j.query('SELECT * FROM execution_accounting'))==1
