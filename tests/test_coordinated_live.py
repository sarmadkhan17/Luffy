"""Restart, actual-fill adoption and no invented PnL in the durable caller.

Uses the dispatch tests' synthetic qualification fixture; real admission is not
established by these tests and production runtime readiness stays disabled.
"""
from copy import deepcopy
import time
from unittest.mock import Mock
import pytest
from ccxt import RequestTimeout
from tests.test_entry_recovery import setup  # noqa: F401
from tests.test_coordinated_venue import prepared
from trader.engine.coordinated_live import LiveBooks


def registered(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch)
    live=LiveBooks(e);at=identity['coordinated_instruction_observed_ms']
    live.register(book,identity,prepared_ms=at)
    return ex,j,e,book,ins,identity,ref,live


def test_restart_adopts_exact_actual_fill_once_and_does_not_claim_pnl(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref,live=registered(setup,monkeypatch)
    assert live.register(book,identity,prepared_ms=identity['coordinated_instruction_observed_ms'])==live.load('g')
    result=live.drain('g',references={'primary':ref},atrs={'primary':2.})
    assert result['filled_quantity']==2.
    restart=LiveBooks(e);state=restart.load('g')
    assert state['book']['pos']['primary']==2. and state['book']['pending']=={}
    count=len(ex.sent)
    assert restart.drain('g',references={},atrs={})['status']=='IDLE'
    assert len(ex.sent)==count
    events=j.query('SELECT payload FROM coordinated_live_actions')
    assert len(events)==1 and '"financial_result":null' in events[0]['payload']


def test_crash_after_order_before_model_save_adopts_original_receipt_without_duplicate(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref,live=registered(setup,monkeypatch)
    monkeypatch.setattr(live,'_save',Mock(side_effect=RuntimeError('model save crash')))
    with pytest.raises(RuntimeError,match='model save crash'):
        live.drain('g',references={'primary':ref},atrs={'primary':2.})
    assert j.open_trades()[0]['amount']==2.
    assert LiveBooks(e).load('g')['book']['pos']['primary']==0.
    count=len(ex.sent)
    result=LiveBooks(e).drain('g',references={'primary':ref},atrs={'primary':2.})
    assert result['filled_quantity']==2. and len(ex.sent)==count
    assert LiveBooks(e).load('g')['book']['pos']['primary']==2.


def test_unknown_response_retains_pending_model_until_existing_recovery_finishes(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref,live=registered(setup,monkeypatch)
    ex.entry_error=RequestTimeout('accepted then lost')
    result=live.drain('g',references={'primary':ref},atrs={'primary':2.})
    assert result['filled_quantity'] is None and live.load('g')['book']['pending']
    count=len(ex.sent)
    LiveBooks(e).drain('g',references={'primary':ref},atrs={'primary':2.})
    assert len(ex.sent)==count
    ex.entry_error=None;e.recover_entries();e.recover_entries()
    assert LiveBooks(e).drain('g',references={'primary':ref},atrs={'primary':2.})['filled_quantity']==2.
    assert not LiveBooks(e).load('g')['book']['pending']


def test_expired_unsubmitted_intent_drops_without_request_or_order(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref,live=registered(setup,monkeypatch)
    before=live.load('g');after=deepcopy(before);after['prepared_ms']-=60001
    live._save(before,after)
    result=LiveBooks(e).drain('g',references={},atrs={})
    assert result['status']=='UNSUBMITTED_INTENT_EXPIRED' and not ex.sent
    assert not live.load('g')['book']['pending']
    assert not j.query('SELECT * FROM execution_requests')


def test_registration_does_not_open_missing_runtime_or_owner_gate(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref=prepared(setup,monkeypatch,ready=False)
    with pytest.raises(ValueError,match='multi_leg_requires'):
        LiveBooks(e).register(book,identity,prepared_ms=identity['coordinated_instruction_observed_ms'])
    assert not ex.sent


def test_native_signal_and_input_clock_cannot_be_replayed_in_new_observation(setup,monkeypatch):
    ex,j,e,book,ins,identity,ref,live=registered(setup,monkeypatch)
    live.drain('g',references={'primary':ref},atrs={'primary':2.})
    before=live.load('g')
    at=max(int(time.time()*1000),before['observed_ms']+1)
    monkeypatch.setattr('trader.engine.coordinated_live.time.time',lambda:at/1000.)
    with pytest.raises(ValueError,match='repeated_native_signal'):
        live.prepare('g',0,dict(decision_ms=at,signal=1))
    assert live.load('g')==before


def test_fresh_native_management_step_prepares_only_conditional_hedge_after_main_fill(setup,monkeypatch):
    from tests.test_strategy_hedge_stops import observed
    ex,j,e,book,ins,identity,ref,live=registered(setup,monkeypatch)
    live.drain('g',references={'primary':ref},atrs={'primary':2.})
    before=live.load('g');at=max(int(time.time()*1000),before['observed_ms']+1)
    monkeypatch.setattr('trader.engine.coordinated_live.time.time',lambda:at/1000.)
    bar=observed(1,p=97.,h=50.,equity=194.)
    bar['decision_ms']=at
    after=live.prepare('g',1,bar)
    assert after['book']['pos']['primary']==2. and after['book']['pos']['hedge']==0.
    assert len(after['book']['pending'])==1
    intent=after['book']['log'][0]
    assert intent['leg']=='hedge' and intent['reason']=='risk_management_activation'
    assert after['book']['hedge_exit_state']
    assert LiveBooks(e).load('g')==after
    assert len(j.open_trades())==1  # intent is not a hedge fill


def test_worker_adopts_recovered_fill_without_needing_public_quotes(setup,monkeypatch):
    from trader.engine.coordinated_live import run_once
    from trader.engine import coordinated_trials as inputs
    ex,j,e,book,ins,identity,ref,live=registered(setup,monkeypatch)
    ex.entry_error=RequestTimeout('accepted')
    live.drain('g',references={'primary':ref},atrs={'primary':2.})
    ex.entry_error=None;e.recover_entries();e.recover_entries()
    quote=Mock(side_effect=AssertionError('recovery does not require public market data'))
    reader=Mock(side_effect=AssertionError('recovery does not require candle history'))
    monkeypatch.setattr(inputs,'quote',quote);monkeypatch.setattr(inputs,'prospective_observation',reader)
    result=run_once(e,None,[])
    assert result[0]['status']=='TERMINAL' and result[0]['filled_quantity']==2.
    assert live.load('g')['book']['pos']['primary']==2.
    quote.assert_not_called();reader.assert_not_called()


def test_worker_uses_a_fresh_quote_only_for_an_unsubmitted_entry(setup,monkeypatch):
    from trader.engine.coordinated_live import run_once
    from trader.engine import coordinated_trials as inputs
    ex,j,e,book,ins,identity,ref,live=registered(setup,monkeypatch)
    now=int(time.time()*1000)
    quote=Mock(return_value=dict(symbol='BTC/USDT',contract='linear',instrument_id=ref['instrument_id'],source='fixture-public',
        started_ms=now,received_ms=now,bid=99.99,ask=100.01))
    monkeypatch.setattr(inputs,'quote',quote)
    result=run_once(e,None,[])
    assert result[0]['status']=='TERMINAL' and result[0]['filled_quantity']==2.
    quote.assert_called_once_with(None,'BTC/USDT','linear')
    assert len(j.open_trades())==1


def test_native_only_primary_close_reconciles_flat_without_mark_or_quote_requirement(setup,monkeypatch):
    from trader.engine.coordinated_live import run_once
    from trader.engine import coordinated_trials as inputs
    from tests.test_coordinated_unwind import normalized
    ex,j,e,book,ins,identity,ref,live=registered(setup,monkeypatch)
    live.drain('g',references={'primary':ref},atrs={'primary':2.})
    j.close_trade(j.open_trades()[0]['id'],95.,-10.,'sl_fill')
    ex.positions=[];normalized(ex,monkeypatch)
    quote=Mock(side_effect=AssertionError('native-flat reconciliation needs no price quote'))
    monkeypatch.setattr(inputs,'quote',quote)
    result=run_once(e,None,[])
    assert result[0]['status']=='CLOSED'
    state=live.load('g')
    assert state['status']=='CLOSED' and not any(state['book']['pos'].values())
    assert 'financial_result' not in state
    quote.assert_not_called()
