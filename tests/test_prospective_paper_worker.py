"""Current Factory -> paper worker -> fresh public quotes, without venue orders."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from tests.test_coordinated_trial_lifecycle import packet  # noqa: F401
from trader.core.journal import Journal
from trader.research.closure import coordinated_trial as T
from trader.strategy import factory_handoff as F
from trader.engine import coordinated_trials as R
from trader.engine.coordinated_paper import CoordinatedPaper,QUOTE_PROTOCOL_ID


def test_new_factory_paper_uses_quotes_and_calculates_only_on_native_candle(tmp_path,packet,monkeypatch):
    j=Journal(tmp_path/'j.db');cfg={'research':{'closure':{'enabled':True,'paper_intake':True}}}
    issued=F.create_version(j,cfg,dict(kind=T.KIND,packet=packet),at_ms=1)
    installed=F.record_coordinated_paper_install(j,issued['version_id'],at_ms=1)
    runner=CoordinatedPaper(j);key=installed['book_id']
    assert runner.state(key)['paper_execution_protocol']==QUOTE_PROTOCOL_ID
    step=14_400_000;now=[installed['install']['first_bar_ms']+step+1000]
    monkeypatch.setattr(R.time,'time',lambda:now[0]/1000.)
    observed=[]
    def read(feed,journal,version,stamp,*,decision_ms=None):
        observed.append((stamp,decision_ms))
        return dict(p=100.,h=100.,po=100.,ho=100.,signal=1,exit=False,ratio=1.,corr=1.,
                    funding={'primary':.0001,'hedge':.0001},signal_cut_ms=stamp+step,
                    primary_exit_observation=dict(bar_ms=stamp,step_ms=step,open=100.,high=100.,low=100.,close=100.,atr=1.))
    monkeypatch.setattr(R,'observation',read)
    ex=NS(id='binanceusdm',urls={'api':{'fapiPublic':'https://public.test/fapi/v1','fapiPrivate':'https://private.test'}},
        market=lambda symbol:dict(id=symbol.replace('/',''),contract=True,linear=True,inverse=False),
        fetch_order_book=Mock(return_value={'bids':[[99.,1000.]],'asks':[[101.,1000.]]}),
        create_order=Mock(side_effect=AssertionError('paper has no venue-order authority')))
    feed=NS(ex=ex)
    result=R.run_once(j,cfg,feed,at_ms=now[0],allow_entry=True)
    assert result[0]['status']=='PAPER_OBSERVATION' and not any(runner.state(key)['book']['pos'].values())
    now[0]+=30_000
    R.run_once(j,cfg,feed,at_ms=now[0],allow_entry=True)
    state=runner.state(key)
    assert state['book']['pos']['primary']>0 and state['book']['pos']['hedge']==0
    assert state['bar']==1 and state['native_bar']==0
    assert len(observed)==1 and ex.fetch_order_book.call_count==4
    import json
    event=json.loads(j.query('SELECT payload FROM coordinated_paper_events ORDER BY bar DESC LIMIT 1')[0]['payload'])
    assert 'private.test' not in event['request']['observation']['quotes']['primary']['source']
    ex.create_order.assert_not_called()
    assert not j.query('SELECT * FROM trades')


def test_unavailable_quote_is_a_gap_and_cannot_create_a_fake_observation(tmp_path,packet,monkeypatch):
    j=Journal(tmp_path/'j.db');cfg={'research':{'closure':{'enabled':True,'paper_intake':True}}}
    issued=F.create_version(j,cfg,dict(kind=T.KIND,packet=packet),at_ms=1)
    installed=F.record_coordinated_paper_install(j,issued['version_id'],at_ms=1)
    now=installed['install']['first_bar_ms']+14_400_000+1000
    monkeypatch.setattr(R,'observation',lambda *a,**kw:dict(signal_cut_ms=now-1000,funding=None))
    def unavailable(*a):raise ConnectionError('synthetic quote outage')
    result=R.run_once(j,cfg,None,at_ms=now,quote_reader=unavailable,allow_entry=True)
    assert result[0]['status']=='PAPER_INPUT_GAP'
    assert CoordinatedPaper(j).state(installed['book_id'])['bar']==-1


def test_conflicting_projection_rolls_back_install_and_probation_start(tmp_path,packet):
    from trader.strategy.spec import StrategySpec
    j=Journal(tmp_path/'j.db')
    issued=F.create_version(j,{},dict(kind=T.KIND,packet=packet),at_ms=1)
    version=F.load_version(j,issued['version_id'])
    j.upsert_spec(StrategySpec.from_dict(version['spec']))
    with pytest.raises(F.HandoffRefused,match='coordinated_strategy_projection_conflict'):
        F.record_coordinated_paper_install(j,version['version_id'],at_ms=2)
    assert F.state_of(j,version['version_id'])==F.VALIDATED
    assert not j.query('SELECT * FROM strategy_version_installs')
    assert j.query('SELECT kind FROM strategies')==[{'kind':'spec'}]


def test_changed_projection_cannot_masquerade_as_current_coordinated_install(tmp_path,packet):
    j=Journal(tmp_path/'j.db')
    issued=F.create_version(j,{},dict(kind=T.KIND,packet=packet),at_ms=1)
    installed=F.record_coordinated_paper_install(j,issued['version_id'],at_ms=2)
    version=F.load_version(j,issued['version_id'])
    with j._tx() as db:
        db.execute("UPDATE strategies SET kind='spec'")
    with pytest.raises(F.HandoffRefused,match='coordinated_strategy_projection_conflict'):
        F.verify_install(j,version)
    # Historical paper evidence remains bound to the original immutable install.
    assert F.verify_install(j,version,current=False)['install_id']==installed['install']['install_id']
