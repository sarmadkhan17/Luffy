"""Automatic intake uses real local Risk truth, never synthetic qualification claims."""
from copy import deepcopy
from unittest.mock import Mock
import time
import pytest
from tests.test_entry_recovery import setup  # noqa: F401
from tests.test_coordinated_venue import prepared
from tests.test_strategy_hedge_stops import observed
from trader.engine import coordinated_intake as I, coordinated_authority as CA
from trader.engine.coordinated_live import LiveBooks,run_once
from trader.strategy import factory_handoff as F
from trader import attention_admission as A
from tests.test_hierarchical_admission import observation


def receipt(now,symbol='BTC/USDT'):
    raw=observation(1);raw['selection']='volume'
    raw['source_cut_ms']=now;raw['source_identity']='test-public-volume'
    raw['peer_cohort']=[symbol];raw['strategy_relevance']={symbol:['strategy']}
    row=raw['rows'].pop('S00/USDT');row.update(symbol=symbol,available_at_ms=now)
    raw['rows']={symbol:row}
    return A.admit(raw,A.AdmissionPolicy(max_deep_slots=30,event_reserve=0,exploration_reserve=0,minimum_slots=0),A.initial_state())


def ready(setup,monkeypatch):
    ex,j,e,book,_,identity,ref=prepared(setup,monkeypatch)
    v=F.load_version(j,'v');v['spec']['timeframe']='1h'
    v['lineage']['research']['multi_leg']['hedge_spec']['leverage']=e.risk_manager.leverage
    install=dict(install_id='i',first_bar_ms=0)
    monkeypatch.setattr(F,'verify_install',lambda *a,**kw:install)
    monkeypatch.setattr(F,'live_allocation',lambda *a:dict(version_id='v',fraction='1',ceiling='1'))
    monkeypatch.setattr(I,'candidates',lambda journal:[v])
    from trader.engine import coordinated_trials as inputs
    at=int(time.time()*1000);step=3_600_000;native=at//step-1
    bar=observed(native,p=100.,h=50.,equity=200.)
    bar.update(signal=1,decision_ms=at,native_bar=native,signal_cut_ms=at//step*step,
        quotes={'primary':dict(bid=100.,ask=100.),'hedge':dict(bid=50.,ask=50.)})
    # No source acquisition: only the existing public-observation adapter is
    # replaced; current Risk/account/venue/capability validators stay real.
    reader=Mock(side_effect=lambda *a,**kw:deepcopy(bar))
    monkeypatch.setattr(inputs,'prospective_observation',reader)
    return ex,j,e,v,bar,receipt(at),reader


def test_exact_native_signal_registers_once_then_uses_existing_real_dispatch(setup,monkeypatch):
    ex,j,e,v,bar,r,reader=ready(setup,monkeypatch)
    result=I.run_once(e,None,r)
    assert result[0]['status']=='INTENT_REGISTERED',result
    gid=result[0]['group_id'];state=LiveBooks(e).load(gid)
    assert not ex.sent and not j.open_trades()
    assert state['identity']['coordinated_admission_receipt']==r['receipt_id']
    assert state['book']['pos']=={'primary':0.,'hedge':0.}
    assert len(state['book']['pending'])==1
    assert I.run_once(e,None,r)==[] and reader.call_count==1
    # The later worker obtains its own current quote and exact Risk permission.
    from trader.engine import coordinated_trials as inputs
    ref=I.A.proposal_binding(j,'BTC/USDT')
    import json
    now=int(time.time()*1000)
    monkeypatch.setattr(inputs,'quote',lambda *a:dict(symbol='BTC/USDT',contract='linear',
        instrument_id=json.loads(ref)['instrument_id'],source='fixture-public',
        started_ms=now,received_ms=now,bid=100.,ask=100.))
    sent=run_once(e,None,[])
    assert sent[0]['status']=='TERMINAL',sent
    assert len(j.open_trades())==1
    assert I.run_once(e,None,r)==[] and reader.call_count==1


@pytest.mark.parametrize('case',['stale','tamper','not_shortlisted','no_signal','owner','leverage','no_margin'])
def test_ineligible_intake_never_sends_or_registers(setup,monkeypatch,case):
    ex,j,e,v,bar,r,reader=ready(setup,monkeypatch)
    if case=='stale':r=receipt(int(time.time()*1000)-60001)
    elif case=='tamper':r['admitted_symbols']=['OTHER/USDT']
    elif case=='not_shortlisted':r=receipt(int(time.time()*1000),'OTHER/USDT')
    elif case=='no_signal':bar['signal']=0
    elif case=='owner':monkeypatch.setattr(F,'live_entry_block',lambda *a,**kw:'owner_approval_missing')
    elif case=='leverage':v['lineage']['research']['multi_leg']['hedge_spec']['leverage']=e.risk_manager.leverage+1.
    elif case=='no_margin':j.kv_set('account_margin_observation','null')
    if case in ('stale','tamper'):
        with pytest.raises(ValueError):I.run_once(e,None,r)
    else:
        result=I.run_once(e,None,r)
        assert not result or result[0]['status']=='UNAVAILABLE'
    assert not ex.sent and not j.open_trades()
    tables=j.query("SELECT 1 FROM sqlite_master WHERE name='coordinated_live_books'")
    assert not tables or not j.query('SELECT * FROM coordinated_live_books')


def test_no_supported_active_version_needs_no_schema_quote_or_account(setup,monkeypatch):
    ex,j,e,_=setup
    monkeypatch.setattr(I,'candidates',lambda journal:[])
    assert I.run_once(e,None,None)==[]
    assert not j.query("SELECT 1 FROM sqlite_master WHERE name='coordinated_live_books'")
    assert not CA.RUNTIME_READY


def test_allocated_model_size_cannot_exceed_existing_risk_share(setup,monkeypatch):
    ex,j,e,v,bar,r,reader=ready(setup,monkeypatch)
    full=I.capital(e,v,bar)
    monkeypatch.setattr(F,'live_allocation',lambda *a:dict(version_id='v',fraction='.25',ceiling='1'))
    quarter=I.capital(e,v,bar)
    assert 0<quarter<=full*.25
    result=I.run_once(e,None,r)
    assert result[0]['status']=='INTENT_REGISTERED',result
    state=LiveBooks(e).load(result[0]['group_id'])
    assert state['identity']['coordinated']['allocated_equity_usdt']==quarter
    assert state['book']['management_entry_equity']==quarter
    assert not ex.sent


def test_slow_input_read_cannot_revive_expired_shortlist(setup,monkeypatch):
    ex,j,e,v,bar,r,reader=ready(setup,monkeypatch)
    bar['decision_ms']=r['source_cut_ms']+60001
    result=I.run_once(e,None,r)
    assert result[0]['status']=='UNAVAILABLE'
    assert result[0]['reason']=='coordinated_entry_shortlist_unavailable'
    assert not j.query('SELECT * FROM coordinated_live_books') and not ex.sent


def test_durable_closed_cut_cannot_be_reopened_by_same_signal(setup,monkeypatch):
    ex,j,e,v,bar,r,reader=ready(setup,monkeypatch)
    result=I.run_once(e,None,r);assert result[0]['status']=='INTENT_REGISTERED',result
    live=LiveBooks(e);before=live.load(result[0]['group_id']);after=deepcopy(before);after['status']='ABANDONED'
    live._save(before,after)
    assert I.run_once(e,None,r)==[] and reader.call_count==1 and not ex.sent


@pytest.mark.parametrize('fail',[False,True])
def test_finished_scan_passes_its_exact_receipt_to_intake_without_breaking_scan_count(tmp_path,monkeypatch,fail):
    from types import SimpleNamespace as NS
    from tests.test_split_kernel_runtime import minimal
    k,r=minimal(tmp_path);k.executor=NS();receipt_token={'exact_scan_receipt':'test'}
    view=NS(_model_generation=0,_prepared_at=time.monotonic(),_admission_receipt=receipt_token,
            cycle=lambda:{'decisions':0})
    r.pending_scan=view
    monkeypatch.setattr(I,'run_once',Mock(side_effect=ValueError('entry_unavailable')) if fail else
                        Mock(return_value=[dict(status='INTENT_REGISTERED')]))
    r.jobs['positions'].trigger=Mock(return_value=True)
    r.decisions()
    I.run_once.assert_called_once_with(k.executor,k.feed,receipt_token)
    assert r.scans_completed==1 and not r.scan_in_flight
    assert r.coordinated_intake_results[0]['status']==('UNAVAILABLE' if fail else 'INTENT_REGISTERED')
    r.jobs['positions'].trigger.assert_called_once()
