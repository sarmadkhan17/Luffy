"""Real Factory bindings, synthetic adapter submission; no live admission claim.

Ownership/recovery transport has its separate real Risk/Executor regressions.
These cases isolate the exit adapter's exact-version and leg routing.
"""
import json
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pandas as pd
import pytest

from tests.test_coordinated_trial_lifecycle import packet  # noqa: F401
from trader.core.journal import Journal
from trader.core.types import Snapshot,TF_MS
from trader.engine.versioned_exits import LiveExitAdapter
from trader.engine import coordinated_close as C,coordinated_unwind as U
from trader.research.compose import hedge as H
from trader.strategy import factory_handoff as F,exit_policy as E
from trader.strategy.spec import ExitSpec


def fixture(tmp_path,packet,monkeypatch,leg='primary'):
    j=Journal(tmp_path/'journal.db');cfg={}
    created=F.create_version(j,cfg,dict(kind='coordinated_paper_research',packet=packet),at_ms=1)
    v=F.load_version(j,created['version_id'])
    installed=F.record_coordinated_paper_install(j,v['version_id'],at_ms=1)
    install=installed['install'];step=TF_MS[v['spec']['timeframe']];start=install['first_bar_ms']
    policy,state=E.initialize(ExitSpec(**v['spec']['exit']),100.,1.,'long',start,step)
    identity=dict(status='VERIFIED',version_id=v['version_id'],strategy_id=v['strategy_id'],
        spec_sha256=v['spec_hash'],install_id=install['install_id'],exit_semantics_id=H.BOOK_EXIT_SEMANTICS_ID,
        exit_state=E.encode(policy,state),coordinated=dict(group_id='g',leg=leg))
    trade=dict(id='main' if leg=='primary' else 'hedge',strategy_id=v['strategy_id'],symbol='A/USDT',
        amount=2.,side='long' if leg=='primary' else 'short',entry_identity_json=json.dumps(identity))
    monkeypatch.setattr(C,'ownership',lambda executor,t,g:json.loads(t['entry_identity_json'])['coordinated'])
    monkeypatch.setattr(U,'active',lambda *a:False)
    executor=NS(close=Mock(return_value=False))
    return j,v,trade,identity,policy,state,executor,start,step


@pytest.mark.parametrize('reason',['stop','target','time'])
def test_main_exit_routes_whole_group_and_persists_retry(tmp_path,packet,monkeypatch,reason):
    j,v,t,identity,policy,state,executor,start,step=fixture(tmp_path,packet,monkeypatch)
    if reason=='target' and not policy.target:pytest.skip('fixture declares no target')
    price=policy.stop-1. if reason=='stop' else policy.target+1. if reason=='target' else 100.
    # Time intent uses the exact frozen policy; no changed test-only policy.
    count=policy.max_bars if reason=='time' else 1
    rows=[dict(ts=pd.Timestamp(start+i*step,unit='ms',tz='UTC'),open=100.,
        high=max(100.,price),low=min(100.,price),close=price,volume=10.) for i in range(1,count+1)]
    frame=pd.DataFrame(rows)
    snap=Snapshot(t['symbol'],pd.Timestamp(start+(count+1)*step,unit='ms',tz='UTC').isoformat(),price,
        {v['spec']['timeframe']:frame},market_type='futures')
    adapter=LiveExitAdapter(j,executor)
    assert adapter.manage(t,snap) is None  # fake executor deliberately leaves it pending
    request=executor.close.call_args
    evidence=request.args[0]['_strategy_exit_evidence']
    assert request.kwargs['reason']==reason
    assert evidence['scope']=='WHOLE_POSITION' and evidence['group_id']=='g'
    assert evidence['group_exit_semantics_id']==H.BOOK_EXIT_SEMANTICS_ID
    assert evidence['exit_semantics_id']==E.EXIT_SEMANTICS_ID
    saved=j.query('SELECT * FROM versioned_live_exit_states')[0]['state_json']
    executor.close.return_value=True
    assert LiveExitAdapter(Journal(j.db_path),executor).manage(t,snap)==reason
    assert j.query('SELECT * FROM versioned_live_exit_states')[0]['state_json']==saved
    assert not j.query('SELECT * FROM trades')  # no manufactured fills or live approval


def test_opposite_leg_never_uses_main_geometry(tmp_path,packet,monkeypatch):
    j,v,t,identity,policy,state,executor,start,step=fixture(tmp_path,packet,monkeypatch,'hedge')
    # No exit frame needed for the protective leg, even at the main stop.
    snap=Snapshot(t['symbol'],pd.Timestamp(start+step,unit='ms',tz='UTC').isoformat(),1.,{},market_type='futures')
    assert LiveExitAdapter(j,executor).manage(t,snap) is None
    executor.close.assert_not_called()
    assert not j.query('SELECT * FROM versioned_live_exit_states')


def test_group_exit_cannot_accept_primary_only_semantics(tmp_path,packet,monkeypatch):
    j,v,t,identity,policy,state,executor,start,step=fixture(tmp_path,packet,monkeypatch)
    identity['exit_semantics_id']=E.EXIT_SEMANTICS_ID;t['entry_identity_json']=json.dumps(identity)
    snap=NS(price=100.,ts='2026-10-29T00:00:00+00:00')
    with pytest.raises(ValueError,match='live_exit_identity_unbound'):LiveExitAdapter(j,executor).manage(t,snap)
    executor.close.assert_not_called()
