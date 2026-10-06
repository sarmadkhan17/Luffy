"""WRLD-01: actual enabled producers → Decision → journal/restart. Offline only."""
import ast
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

from trader.agents.measurement import Measurement
from trader.agents.momentum import MomentumAnalyst, ValueAnalyst, RotationAnalyst
from trader.agents.structure import StructureAnalyst
from trader.agents.flow import FlowAnalyst
from trader.agents.positioning import PositioningAnalyst
from trader.agents.orderbook_depth import DepthScout
from trader.core.types import Snapshot, Vote, Side, Action
from trader.core.journal import Journal
from trader.engine.orchestrator import Orchestrator

CLASSES = (StructureAnalyst, MomentumAnalyst, FlowAnalyst, ValueAnalyst,
           RotationAnalyst, PositioningAnalyst, DepthScout)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    def denied(*args, **kwargs):
        pytest.fail('WRLD-01 attempted network/provider access')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'create_connection', denied)


def snapshot():
    def frame(tf):
        c = 100 + np.arange(300)*.03 + np.sin(np.arange(300)/7)
        return pd.DataFrame(dict(ts=pd.date_range(end='2026-10-01', periods=300, freq=tf, tz='UTC'),
            open=c, high=c+1, low=c-1, close=c, volume=np.full(300,100.), taker_buy=np.full(300,60.)))
    return Snapshot('ETH/USDT', '2026-10-01T01:00:00+00:00', 110,
        {'15m':frame('15min'),'1h':frame('1h'),'BTC_1h':frame('1h')})


def analysts():
    values = [cls() for cls in CLASSES]
    values[-2].set_context('ETH/USDT', .0008, {'now':1e6,'chg_24h':.1})
    values[-1].set_context('ETH/USDT', {'bids':[[109.9,50],[109.8,4]],'asks':[[110.1,3],[110.2,4]]})
    return values


def test_exact_enabled_roster_without_kernel_import_or_boot():
    tree = ast.parse(Path('trader/kernel.py').read_text())
    roster = next(n.value for n in tree.body if isinstance(n,ast.Assign)
                  and any(isinstance(t,ast.Name) and t.id=='AGENTS' for t in n.targets))
    assert set(ast.literal_eval(k) for k in roster.keys) == {c.name for c in CLASSES}


@pytest.mark.parametrize('index',range(7))
def test_all_enabled_analysts_declare_typed_immutable_measurements(index):
    snap=snapshot(); a=analysts()[index]; v=a.evaluate(snap); p=v.measurement
    assert isinstance(p,Measurement)
    assert (p.analyst,p.instrument,p.observed_at)==(a.name,snap.symbol,snap.ts)
    assert p.horizon==a.evidence_timeframe  # Depth explicitly has no bar horizon.
    assert p.inputs and p.limitations and p.uncertainty['basis']
    assert p.strength is not None and p.uncertainty['confidence'] is not None
    assert p.quality.value=='SUSPECT'  # No synthetic fixture establishes provider clocks.
    assert all(i['source_ref'] for i in p.inputs if i['quality']!='MISSING')
    assert Measurement.from_dict(json.loads(json.dumps(p.to_dict()))).record_id==p.record_id
    with pytest.raises((FrozenInstanceError,AttributeError)): p.strength=.9
    with pytest.raises(TypeError): p.inputs[0]['source']='fabricated'
    before=p.record_id; snap.dfs['15m'].loc[299,'close']=999
    assert p.record_id==before


@pytest.mark.parametrize('cls',CLASSES)
def test_missing_inputs_remain_null_and_analysts_have_no_execution_capability(cls):
    a=cls(); snap=Snapshot('ETH/USDT','2026-10-01T01:00:00+00:00',110,{})
    p=a.evaluate(snap).measurement
    assert p.strength is None and p.uncertainty['confidence'] is None
    assert p.quality.value=='MISSING'
    assert all(i['source_ref'] is None for i in p.inputs)
    assert not any(hasattr(a,k) for k in ('exchange','executor','journal','risk','place_order','size_order','activate_strategy'))
    assert not any(k in p.to_dict() for k in ('action','side','size','size_usdt','strategy_state'))


def test_positioning_discards_execution_object():
    class Execution:
        def __getattribute__(self,key): pytest.fail('execution capability accessed')
    a=PositioningAnalyst(Execution())
    assert 'exchange' not in vars(a)


def test_enabled_analyst_code_cannot_order_size_or_activate():
    import inspect
    paths={Path(inspect.getfile(cls)) for cls in CLASSES}
    paths.update(Path('trader/agents')/name for name in ('base.py','measurement.py','indicators.py'))
    banned_modules=('executor','engine.risk','engine.exits','ccxt','requests','urllib','strategy.governor')
    banned_calls={'create_order','place_order','submit_order','cancel_order','check_entry','size_order',
                  'upsert_spec','activate','install_version','approve_version','govern_version','set_state'}
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node,(ast.Import,ast.ImportFrom)):
                names=[a.name for a in node.names]+([node.module] if isinstance(node,ast.ImportFrom) and node.module else [])
                assert not any(b in name for name in names for b in banned_modules),(path,names)
            if isinstance(node,ast.Call):
                called=node.func.attr if isinstance(node.func,ast.Attribute) else getattr(node.func,'id','')
                assert called not in banned_calls,(path,called)


@pytest.mark.parametrize('index',[0,1,2,3,4])
def test_nonfinite_required_inputs_never_become_measurements(index):
    snap=snapshot(); a=analysts()[index]
    key,cols,_=a.frame_inputs[0]
    snap.dfs[key].loc[299,cols[0]]=np.nan
    p=a.evaluate(snap).measurement
    assert p.strength is None and p.uncertainty['confidence'] is None
    assert p.quality.value=='INVALID'


def test_degenerate_value_window_does_not_fabricate_zero_zscore():
    snap=snapshot(); snap.dfs['15m'][['open','high','low','close']]=100.
    p=ValueAnalyst().evaluate(snap).measurement
    assert p.strength is None and 'undefined' in p.rationale


def test_nonfinite_context_is_missing_and_input_identity_tracks_revision():
    snap=snapshot(); a=PositioningAnalyst(); a.set_context(snap.symbol,float('nan'),None)
    p=a.evaluate(snap).measurement
    assert p.strength is None
    assert next(i for i in p.inputs if i['name']=='funding')['quality']=='MISSING'
    a=MomentumAnalyst(); before=a.evaluate(snap).measurement
    snap.dfs['15m'].loc[299,'close']+=1e-12
    after=a.evaluate(snap).measurement
    assert before.inputs[0]['source_ref']!=after.inputs[0]['source_ref']
    assert before.record_id!=after.record_id


@pytest.mark.parametrize('index',[5,6])
def test_wrong_instrument_context_is_explicitly_missing(index):
    snap=snapshot(); snap.symbol='OTHER/USDT'; p=analysts()[index].evaluate(snap).measurement
    assert p.strength is None
    assert any('wrong instrument' in i['reason'] for i in p.inputs)


def test_optional_missing_and_unsupported_inputs_are_not_defaulted():
    snap=snapshot(); a=PositioningAnalyst(); a.set_context(snap.symbol,.001,None)
    p=a.evaluate(snap).measurement
    oi=next(i for i in p.inputs if i['name']=='open_interest')
    assert oi['quality']=='MISSING' and oi['source_ref'] is None
    assert p.strength is not None  # Funding remains a real partial measurement.
    from trader.agents.base import Analyst
    class Undeclared(Analyst):
        def evaluate(self,snap): pytest.fail('unsupported analyst evaluated')
    p=Undeclared().evaluate(snap).measurement
    assert p.quality.value=='UNSUPPORTED' and p.strength is None


def test_decision_consumes_packets_and_rejects_untyped_output(tmp_path):
    snap=snapshot(); values=analysts(); expected=values[1].evaluate(snap).measurement
    original=values[1].evaluate
    def forged_projection(snap):
        v=original(snap); v.conviction=-1; v.confidence=1; v.side=Side.SHORT
        return v
    values[1].evaluate=forged_projection
    class Legacy:
        name='legacy'
        def evaluate(self,snap): return Vote(self.name,snap.symbol,Side.LONG,1,1,'untyped')
    j=Journal(tmp_path/'decision.db'); o=Orchestrator(values+[Legacy()],j)
    d=o.decide(snap,[])
    assert d.action==Action.HOLD and d.size_usdt==0 and len(d.votes)==7
    v=next(v for v in d.votes if v['agent']=='momentum')
    assert v['conviction']==round(expected.strength,4)
    assert v['meta']['measurement']==expected.to_dict()
    o.journalize(snap,d,'futures','paper')
    assert not j.query('SELECT * FROM trades') and not j.query('SELECT * FROM strategies')


def test_packets_survive_exact_decision_capture_replay_and_process_restart(tmp_path):
    from trader.learning import capture as C
    snap=snapshot(); j=Journal(tmp_path/'replay.db'); o=Orchestrator(analysts(),j)
    d=o.decide(snap,[]); o.journalize(snap,d,'futures','paper')
    rows=j.query('SELECT meta FROM votes WHERE cycle_id=? ORDER BY rowid',(d.cycle_id,))
    original=[Measurement.from_dict(json.loads(r['meta'])['measurement']).to_dict() for r in rows]
    assert len(original)==7
    # Exact capture retains original vote metadata in its frozen reasons dependency.
    _,reg=C.registration(j._conn(),'decision:'+d.id)
    reasons=next(x for x in reg['dependencies'] if x['role']=='reasons')
    body=C.resolve(j._conn(),reasons)
    assert [json.loads(v['meta'])['measurement'] for v in body['votes']]==original
    j.kv_set('world_model','LATEST'); snap.dfs.clear()
    code='''import sqlite3,json,sys
from trader.agents.measurement import Measurement
db=sqlite3.connect(sys.argv[1]); rows=db.execute("SELECT meta FROM votes WHERE cycle_id=? ORDER BY rowid",(sys.argv[2],))
print(json.dumps([Measurement.from_dict(json.loads(r[0])["measurement"]).to_dict() for r in rows],sort_keys=True))'''
    actual=json.loads(subprocess.check_output([sys.executable,'-c',code,str(j.db_path),d.cycle_id],text=True))
    assert actual==original


def test_replay_refuses_missing_declarations_and_missing_strength_defaults():
    record=analysts()[0].evaluate(snapshot()).measurement.to_dict()
    for key in record:
        broken=dict(record); del broken[key]
        with pytest.raises((ValueError,TypeError,KeyError)): Measurement.from_dict(broken)
    broken=dict(record); broken['inputs']=[dict(record['inputs'][0],quality='MISSING',source_ref=None)]
    with pytest.raises(ValueError): Measurement.from_dict(broken)
    legacy=Vote('old','ETH/USDT',Side.FLAT,0,0,'historical')
    assert legacy.measurement is None and legacy.meta=={}
