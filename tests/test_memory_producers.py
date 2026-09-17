"""Synthetic engineering tests; costs are explicit fixtures, not live assumptions."""
import copy
from dataclasses import asdict, replace
import json
import sqlite3
from types import SimpleNamespace

import pytest

from trader.cognition import investigation as I, outcomes as O, memory as M
from trader.observability import investigation as C, outcomes as S
from scripts.register_nontrade_experiment import register
from tests.test_market_investigation import prefix, targets
from tests.test_investigation_memory import resolved
from tests.test_attention_learning import publish


def archived(prefix, family='volume_anomaly'):
    _, snap, inv = prefix
    if family != inv.primary_trigger:
        state = replace(inv.state,dimensions=tuple(replace(d,value=3.,status='ok') if d.name==family else d for d in inv.state.dimensions))
        inv = I.open_investigation(state,family,snap.bars,inv.registered_ms)
        bars = targets(inv,returns=[.001,-.001,.002,-.002,.001])
        end = inv.measurement.deadline_ms
        update = I.advance(inv,I.measure(inv,bars,end,end+1))
    else:
        bars, update = resolved(inv)
    archive = dict(investigation=asdict(inv),update=asdict(update),inputs=[asdict(b) for b in (*snap.bars,*bars)])
    return json.loads(I.encode(archive)), inv, update


@pytest.mark.parametrize('kind,family',[('false_signal','volume_anomaly'),('regime_transition','volatility_transition')])
def test_producer_replay_retention_and_later_reasoning(prefix,tmp_path,kind,family):
    archive, inv, update = archived(prefix,family)
    now = update.observed_ms+1
    record = O.investigation_case(archive,kind,now)
    assert record['actual_execution'] is None and record['simulation'] is None
    assert {'investigation','cross_market', 'failure' if kind=='false_signal' else 'regime'} <= {l['kind'] for l in record['links']}
    assert O.replay(record)==record
    with C.ledger(tmp_path/'m.db') as db:
        S.put(db,'case',record)
        later = replace(inv,investigation_id='later',registered_ms=now+1)
        ctx = S.register(db,later)
        assert ctx['cases']==[record]
        initial=I.advance(later,I.measure(later,(),now+1,now+1))
        base=M.retrieve(later,[])
        enriched=dict(base,typed_outcomes=ctx,counter_tests=ctx['counter_tests'])
        reasoning=M.reasoning(initial,enriched)
        assert reasoning['changed'] and reasoning['without_memory']==M.reasoning(initial,base)['with_memory']
        assert record['observation']['winner'] in reasoning['with_memory']['test']
        assert not S.register(db,replace(later,investigation_id='early',registered_ms=now))['cases']
        exported=S.export(db)
        with pytest.raises(ValueError,match='terminal_conflict'):
            S.put(db,'case',O.investigation_case(archive,kind,now+1))
    with C.ledger(tmp_path/'restore.db') as db:
        S.restore(db,exported,now+100)
        assert not S.register(db,later)['cases']
        assert S.export(db)==exported


@pytest.mark.parametrize('change',['score','target','baseline','clock'])
def test_case_rejects_forged_source(prefix,change):
    archive, inv, update = archived(prefix)
    if change=='score': archive['update']['evidence']['score']+=1
    if change=='target': archive['inputs'][-1]['version_id']='wrong'
    if change=='baseline': archive['investigation']['measurement']['baseline_scale']+=1
    if change=='clock': archive['update']['evidence']['observed_ms']=update.observed_ms+100
    with pytest.raises(ValueError): O.investigation_case(archive,'false_signal',update.observed_ms+1)


def test_worker_populates_cases_and_journal_links(prefix,tmp_path):
    archive,inv,update=archived(prefix)
    now=update.observed_ms+1
    with C.ledger(tmp_path/'m.db') as db:
        db.execute('INSERT INTO cases VALUES (?,?,?,?,?,?)',(inv.investigation_id,inv.episode_id,inv.state.symbol,inv.registered_ms,update.observed_ms,I.encode(asdict(inv))))
        C._save_inputs(db,inv.investigation_id,[I.InputBar(b['version_id'],I.Candle(**b['candle'])) for b in archive['inputs']],{b['version_id'] for b in archive['inputs']})
        C._append(db,update)
        result=S.ingest(db,tmp_path,now)
        assert not result['refused'] and result['added']==1
        assert S.ingest(db,tmp_path,now+1)['added']==0
        assert S.export(db)['records'][0]['record']['kind']=='false_signal'
    d=dict(id='d',ts='2026-09-16T00:00:00+00:00',symbol='S',executed=False,strategy_ids='["strategy-a"]')
    record=O.linked_journal(d,None,O.timestamp(d['ts'])+100)
    assert O.replay(record)==record
    assert {r['kind'] for r in record['links']}=={'decision','strategy'}
    assert all(r['phase']=='outcome' and r['available_ms']==record['imported_ms'] for r in record['links'])
    assert record['source']['strategy_attributions'][0]['definition_version'] is None
    assert record['actual_execution']['net_pnl'] is None
    bad=copy.deepcopy(record);bad['source']['strategy_attributions'][0]['definition_version']='invented'
    with pytest.raises(ValueError): O.replay(bad)


def fixture_declaration(tmp_path):
    now=1_789_000_000_000//I.TF*I.TF+60000
    publish(tmp_path/'attention.db',now)
    snap=C.adapt(C.source_snapshot(tmp_path/'attention.db'),now)
    from datetime import datetime, timezone
    ts=datetime.fromtimestamp(now/1000,timezone.utc).isoformat()
    symbol=snap.result['selected'][0]
    with sqlite3.connect(tmp_path/'luffy.db') as db:
        db.execute('CREATE TABLE decisions(id,ts,symbol,action,executed,skip_reason,strategy_ids,scan_id)')
        db.execute('INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?)',('d',ts,symbol,'HOLD',0,'risk','[]',snap.scan['scan_id']))
    declaration=dict(schema_version='nontrade-experiment.v1',id='synthetic-only',decision_id='d',direction=1,
                     cost_bps=10,cost_source='explicit synthetic fixture assumption',notional=100,currency='USDT')
    return now,declaration


def test_declared_caller_registers_forward_resolves_and_freezes(tmp_path):
    now,d=fixture_declaration(tmp_path)
    result=register(tmp_path,d,now)
    assert result['status']=='registered'
    assert register(tmp_path,d,now+2*I.TF)['status']=='already_registered'
    with pytest.raises(ValueError,match='conflict'): register(tmp_path,dict(d,cost_bps=11),now)
    with C.ledger(tmp_path/'investigation.db') as db:
        r=json.loads(db.execute('SELECT payload FROM counterfactual_registrations').fetchone()[0])
        end=r['target_open_ms']+I.TF
        assert S.resolve_counterfactuals(db,None,end)['retry']
        b=dict(r['baseline'],open_ms=r['target_open_ms'],close_ms=end,available_ms=end)
        version=b.pop('version_id')
        target=I.InputBar(version+'target',I.Candle(**b))
        assert S.resolve_counterfactuals(db,SimpleNamespace(bars=[target]),end)['added']==1
        frozen=S.export(db)
        record=frozen['records'][0]['record']
        assert record['simulation']['net_pnl']==-0.1 and record['actual_execution'] is None
        assert record['links'][0]['id']=='d'
        assert S.resolve_counterfactuals(db,SimpleNamespace(bars=[target]),end+1)['added']==0
        assert S.export(db)==frozen
        bad=copy.deepcopy(record);bad['source']['registration']['cost_bps']=0
        with pytest.raises(ValueError): O.replay(bad)


@pytest.mark.parametrize('change',['missing_cost','late','executed','wrong_scan','future_decision','negative_cost'])
def test_declared_caller_refuses_unavailable_or_fabricated_inputs(tmp_path,change):
    now,d=fixture_declaration(tmp_path)
    if change=='missing_cost': d.pop('cost_bps')
    if change=='late': now+=I.TF
    if change=='negative_cost': d['cost_bps']=-1
    with sqlite3.connect(tmp_path/'luffy.db') as db:
        if change=='executed': db.execute('UPDATE decisions SET executed=1')
        if change=='wrong_scan': db.execute("UPDATE decisions SET scan_id='other'")
        if change=='future_decision': db.execute("UPDATE decisions SET ts='2099-01-01T00:00:00+00:00'")
    with pytest.raises(ValueError): register(tmp_path,d,now)
    if (tmp_path/'investigation.db').exists():
        with C.ledger(tmp_path/'investigation.db') as db:
            assert db.execute('SELECT COUNT(*) FROM counterfactual_registrations').fetchone()[0]==0



def test_packed_case_integrity_and_bounded_expansion(prefix):
    import base64, hashlib, zlib
    archive, inv, update = archived(prefix)
    packed=O.pack_case(archive)
    assert O.unpack_case(packed)==archive
    bad=dict(packed,sha256='wrong')
    with pytest.raises(ValueError): O.unpack_case(bad)
    raw=b'x'*(O.MAX_CASE_RAW+1)
    oversized=dict(encoding='zlib-base64.v1',sha256=hashlib.sha256(raw).hexdigest(),
                   data=base64.b64encode(zlib.compress(raw)).decode())
    with pytest.raises(ValueError,match='capacity'): O.unpack_case(oversized)


def test_restoration_refuses_changed_terminal_links(tmp_path):
    d=dict(id='d',ts='2026-09-16T00:00:00+00:00',symbol='S',executed=False,strategy_ids=[])
    now=O.timestamp(d['ts'])+100
    original=O.journal_record(d,None,now)
    changed=O.journal_record(d,None,now,[dict(kind='decision',id='d',version=O.digest(d),available_ms=now,phase='outcome')])
    with C.ledger(tmp_path/'m.db') as db:
        S.put(db,'d',original)
        body=dict(schema_version=O.SCHEMA,records=[dict(source_key='d',record=changed)])
        with pytest.raises(ValueError,match='terminal_conflict'):
            S.restore(db,dict(body,sha256=O.digest(body)),now+1)
        assert S.export(db)['records'][0]['record']==original
