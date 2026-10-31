"""Same owned-reduction writer for a single versioned entry.

Version labels and legacy-test admission are synthetic. Actual Risk,
reservation, protected entry, reduction and booking code are exercised; this
is not evidence of Factory live admission, qualification or a venue account.
"""
import json
import pytest
from ccxt import RequestTimeout
from tests.test_entry_recovery import setup as recovery_setup
setup = recovery_setup
from trader.engine import coordinated_close as C
from trader.strategy import exit_policy as E
from trader.strategy.spec import ExitSpec


def opened(setup,monkeypatch,*,fraction=1.):
    ex,j,e,d=setup;e.fill_retry_s=0
    p,s=E.initialize(ExitSpec(signal_exit='close < 100'),100.,2.,'long',0,1000)
    identity=dict(status='VERIFIED',strategy_id='strategy',version_id='TEST_ONLY_v',install_id='TEST_ONLY_i',
        spec_hash='TEST_ONLY_s',spec_sha256='TEST_ONLY_s',exit_semantics_id=E.EXIT_SEMANTICS_ID,exit_state=E.encode(p,s))
    create=ex.create_order
    def identify(order):
        # A venue assigns a distinct ID to each child, including zero fills.
        order['id']='versioned-close-'+str(len(ex.sent))
        ex.orders[order['id']]=order
    def submit(symbol,type_,side,amount,params=None):
        reducing=(params or {}).get('reduceOnly') and not (params or {}).get('stopLossPrice') and type_!='STOP_MARKET'
        try:order=create(symbol,type_,side,amount,params)
        except Exception:
            order=ex.orders.get((params or {}).get('newClientOrderId'))
            if order is not None and reducing:
                identify(order)
                order['reduceOnly']=True;order['filled']=amount*fraction
                ex.positions=[] if fraction==1. else [dict(symbol=symbol,side='long',contracts=amount*(1-fraction),entryPrice=100.,markPrice=100.)]
            raise
        if reducing:
            identify(order)
            order['reduceOnly']=True;order['filled']=amount*fraction
            ex.positions=[] if fraction==1. else [dict(symbol=symbol,side='long',contracts=amount*(1-fraction),entryPrice=100.,markPrice=100.)]
        return order
    monkeypatch.setattr(ex,'create_order',submit)
    cancel=ex.cancel_order
    def cancel_(oid,symbol):
        if oid=='stop':ex.stops.clear();return {}
        return cancel(oid,symbol)
    monkeypatch.setattr(ex,'cancel_order',cancel_)
    reference=dict(price=100.)
    permission=e.risk_manager.authorize_entry(d,2.,2.,95.,110.,'strategy',identity=identity,reference=reference)
    trade=e.open(d,2.,2.,95.,110.,'strategy','strategy',entry_identity=identity,reference=reference,risk_permission=permission)
    assert trade is not None,d.skip_reason
    return ex,j,e,trade.id


@pytest.mark.parametrize('lost',[False,True])
def test_single_trade_recovers_original_exact_close_once(setup,monkeypatch,lost):
    ex,j,e,tid=opened(setup,monkeypatch)
    if lost:ex.close_error=RequestTimeout('accepted then lost')
    first=C.close_versioned_once(e,tid,'single-close',reason='signal',exit_decision=None)
    if lost:
        assert first['status']=='ATTEMPTED'
        ex.close_error=None
    count=len(ex.sent)
    assert C.close_versioned_once(e,tid,'single-close',reason='signal',exit_decision=None)['status']=='BOOKED'
    assert len(ex.sent)==count and not j.open_trades()
    receipt=j.query('SELECT * FROM '+C.VERSIONED_TABLE)[0]
    assert receipt['group_id'] is None
    proof=json.loads(receipt['result_json'])['flat_evidence']
    assert proof['schema']=='versioned-terminal-flat.v1' and proof['observation_id']
    assert not j.query("SELECT 1 FROM sqlite_master WHERE name='coordinated_reductions'")


def test_single_close_cannot_adopt_a_grouped_trade(setup,monkeypatch):
    from tests.test_coordinated_close import opened as grouped
    ex,j,e,tid=grouped(setup,monkeypatch);count=len(ex.sent)
    with pytest.raises(ValueError,match='versioned_close_entry_identity_missing'):
        C.close_versioned_once(e,tid,'single-close',reason='signal',exit_decision=None)
    assert len(ex.sent)==count
