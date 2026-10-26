"""Two-leg paper execution failure/recovery; no venue capability."""
import pytest
from trader.core.journal import Journal
from trader.engine.coordinated_paper import CoordinatedPaper
from trader.research.compose import hedge as H


def setup(tmp_path,**overrides):
    j=Journal(tmp_path/'paper.db')
    spec=H.make_spec(purpose='protective_beta',primary={'symbol':'A/USDT'},hedge={'symbol':'B/USDT'},ratio={'method':'fixed','value':1.,'lookback':20,'max_ratio':2.},**overrides)
    runner=CoordinatedPaper(j)
    ident=dict(exec_mode='paper',version_id='v',spec_hash='s',install_id='i',admission_id='a')
    key=runner.start(spec=spec,identity=ident)
    return j,runner,key


def obs(**kw):
    return dict(dict(p=100.,h=100.,po=100.,ho=100.,signal=1,exit=False,ratio=1.,corr=1.,
                     funding={'primary':.0001,'hedge':.0001}),**kw)


def test_both_legs_fill_next_open_once_and_survive_restart(tmp_path):
    j,r,k=setup(tmp_path)
    first=r.advance(k,0,obs(),allow_entry=True)
    assert len(first['intents'])==2 and not any(first['positions'].values())
    assert r.advance(k,0,obs(),allow_entry=True)==first
    next_=r.advance(k,1,obs(p=102.,h=101.,po=101.,ho=100.5),allow_entry=True)
    assert next_['positions']['primary']>0 and next_['positions']['hedge']<0
    assert next_['costs']>0 and next_['real_order_submissions']==0
    restart=CoordinatedPaper(Journal(j.db_path))
    assert restart.state(k)==r.state(k)
    assert restart.advance(k,1,obs(p=102.,h=101.,po=101.,ho=100.5),allow_entry=True)==next_
    with pytest.raises(ValueError,match='cut_conflict'):restart.advance(k,1,obs(),allow_entry=True)
    assert not j.query('SELECT * FROM trades')


def test_freeze_cancels_queued_increases(tmp_path):
    _,r,k=setup(tmp_path)
    r.advance(k,0,obs(),allow_entry=True)
    row=r.advance(k,1,obs(),allow_entry=False)
    assert not any(row['fills'].values()) and not any(row['positions'].values())


def test_partial_hedge_reduces_naked_primary_and_rejected_unwind_retries(tmp_path):
    _,r,k=setup(tmp_path)
    issued=r.advance(k,0,obs(),allow_entry=True)
    fill={i['oid']:i['qty'] if i['leg']=='primary' else 0.0 for i in issued['intents']}
    row=r.advance(k,1,obs(),fills=fill,allow_entry=True)
    assert row['positions']['primary']>0 and row['positions']['hedge']==0
    # Force an exit; a rejected close is retained and reissued, never reported flat.
    row=r.advance(k,2,obs(exit=True),fills={},allow_entry=False)
    assert any(i['kind']=='UNWIND' and i['leg']=='primary' for i in row['intents'])
    row=r.advance(k,3,obs(exit=True),fills={},allow_entry=False)
    assert row['held'] and row['intents'][0]['reason']=='unwind_remainder'
    row=r.advance(k,4,obs(exit=True),allow_entry=False)
    assert not any(row['positions'].values())


def test_late_and_excess_fills_and_clock_gap_are_rejected(tmp_path):
    _,r,k=setup(tmp_path)
    first=r.advance(k,0,obs(),allow_entry=True)
    pending=first['intents'][0]
    for fills in ({'late-order':1},{pending['oid']:pending['qty']+1}):
        with pytest.raises(ValueError,match='fill'):r.advance(k,1,obs(),fills=fills,allow_entry=True)
    assert r.state(k)['bar']==0
    with pytest.raises(ValueError,match='clock_gap'):r.advance(k,2,obs(),allow_entry=True)


def test_missing_marks_are_unknown_and_funding_not_assumed_zero(tmp_path):
    _,r,k=setup(tmp_path)
    r.advance(k,0,obs(),allow_entry=True);r.advance(k,1,obs(),allow_entry=True)
    row=r.advance(k,2,obs(p=None,h=None,po=None,ho=None,funding=None),allow_entry=False)
    assert row['equity'] is None and row['incomplete_marks']
    assert row['funding_basis']=='UNAVAILABLE' and row['net_economics']=='UNAVAILABLE'


def test_hedge_account_paper_mode_keeps_both_leg_contracts(tmp_path):
    j,r,k=setup(tmp_path,account_mode='hedge')
    r.advance(k,0,obs(),allow_entry=True)
    row=r.advance(k,1,obs(),allow_entry=True)
    assert len(row['positions'])==2
    import json
    event=json.loads(j.query('SELECT payload FROM coordinated_paper_events WHERE bar=0')[0]['payload'])
    assert {i['position_side'] for i in event['intents']}=={'LONG','SHORT'}


def test_unknown_ratio_prevents_new_paper_entry_without_becoming_zero(tmp_path):
    _,runner,key=setup(tmp_path)
    row=runner.advance(key,0,obs(ratio=None),allow_entry=True)
    assert row['intents']==[] and not any(row['positions'].values())
    assert runner.state(key)['book']['ratio_unknown']==1


def test_declared_exit_prevents_simultaneous_entry(tmp_path):
    _,runner,key=setup(tmp_path)
    row=runner.advance(key,0,obs(exit=True),allow_entry=True)
    assert row['intents']==[] and not any(row['positions'].values())


def test_incremental_paper_matches_research_cashflow_on_the_same_known_path(tmp_path):
    import numpy as np
    _,runner,key=setup(tmp_path,fee_bps=5.,slip_bps=2.)
    p=np.array([100.,101.,100.,102.,101.,101.]);h=np.array([100.,100.5,100.,101.,100.,100.])
    po=p-.1;ho=h-.05;signal=np.array([1,0,0,0,0,0]);exit_=np.array([False,False,False,True,False,False])
    funding=(np.full(6,.0001),np.full(6,.0002))
    research=H.replay(runner.state(key)['spec'],p,h,signal,exit_,opens=(po,ho),funding=funding)
    actual=[]
    for bar in range(6):
        row=runner.advance(key,bar,obs(p=float(p[bar]),h=float(h[bar]),po=float(po[bar]),ho=float(ho[bar]),
            signal=int(signal[bar]),exit=bool(exit_[bar]),funding={'primary':float(funding[0][bar]),'hedge':float(funding[1][bar])}),allow_entry=True)
        actual.append(row['equity'])
    assert np.allclose(actual,research['equity'])


def test_missing_funding_never_becomes_complete_after_later_good_data(tmp_path):
    _,runner,key=setup(tmp_path)
    runner.advance(key,0,obs(),allow_entry=True)
    runner.advance(key,1,obs(funding=None),allow_entry=True)
    result=runner.advance(key,2,obs(),allow_entry=True)
    assert result['net_economics']=='UNAVAILABLE'


def test_margin_breach_keeps_unfilled_exposure_visible_and_prevents_reentry(tmp_path):
    _,r,key=setup(tmp_path,maint_margin=.9)
    r.advance(key,0,obs(),allow_entry=True)
    result=r.advance(key,1,obs(),allow_entry=True)
    assert result['liquidated'] and result['held']
    assert all(i['kind']=='UNWIND' for i in result['intents'])
    # A rejected liquidation/unwind remains exposed, not falsely flat.
    result=r.advance(key,2,obs(),fills={},allow_entry=True)
    assert result['held'] and result['liquidated']
    result=r.advance(key,3,obs(),allow_entry=True)
    assert not result['held'] and result['liquidated']
