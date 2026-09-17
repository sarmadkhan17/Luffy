"""Synthetic chronological tests; no profitability or predictive validation."""
import json
import math
import sqlite3
from dataclasses import asdict, replace

import pytest

from trader.cognition import investigation as I, memory as M
from trader.observability import investigation as C
from trader.observability import memory as S
from tests.test_market_investigation import prefix, targets
from tests.test_attention_learning import publish


def resolved(inv):
    bars = targets(inv, volume=math.expm1(inv.measurement.baseline_mean))
    end = inv.measurement.deadline_ms
    update = I.advance(inv, I.measure(inv, bars, end, end+1))
    return bars, update


def later(prefix, offset=2):
    _, snap, inv = prefix
    bars, update = resolved(inv)
    now = update.observed_ms+offset
    state = replace(inv.state, observed_ms=now)
    new = I.open_investigation(state, inv.primary_trigger, snap.bars, now)
    return new, bars, update


def test_verified_replay_and_concrete_paired_reasoning(prefix):
    _, snap, inv = prefix
    new, bars, update = later(prefix)
    case = M.verified_case(inv, update, (*snap.bars,*bars), update.observed_ms)
    assert case.winner == 'normalization'
    assert case.outcome_type == 'non_economic_observation'
    context = M.retrieve(new, [case])
    initial = I.advance(new, I.measure(new, (), new.registered_ms, new.registered_ms))
    baseline = M.reasoning(initial, M.retrieve(new, []))
    enriched = M.reasoning(initial, context)
    assert not baseline['changed'] and enriched['changed']
    assert enriched['without_memory'] == baseline['with_memory']
    assert enriched['with_memory']['kind'] == 'WAIT'
    assert 'normalization' in enriched['with_memory']['test']
    assert enriched['case_ids'] == [case.case_id]
    assert all(a.probability is None for a in new.alternatives)
    assert M.case_from_dict(json.loads(I.encode(asdict(case)))) == case
    assert M.retrieve(new, [M.case_from_dict(json.loads(I.encode(asdict(case))))]) == context


@pytest.mark.parametrize('change', ['future_resolution','future_availability','late_import','catalog','config','type','sign','family','regime'])
def test_exclusion_no_hindsight_or_incompatible_context(prefix, change):
    _, snap, inv = prefix
    new, bars, update = later(prefix)
    case = M.verified_case(inv, update, (*snap.bars,*bars), update.observed_ms)
    edits = {'future_resolution':{'resolved_ms':new.registered_ms},
             'future_availability':{'available_ms':new.registered_ms+1},
             'late_import':{'recorded_ms':new.registered_ms+1}, 'catalog':{'catalog_id':'other'},
             'config':{'config_id':'other'}, 'type':{'outcome_type':'actual_pnl'},
             'sign':{'sign':-case.sign}, 'family':{'family':'other'}, 'regime':{'contradictions':('other',)}}
    context = M.retrieve(new,[replace(case,**edits[change])])
    assert not context['cases'] and context['audit'][0]['reason'] != 'compatible_prior_observable_path'


@pytest.mark.parametrize('change', ['score','target_version','baseline','registration','assessment'])
def test_forged_source_refused(prefix,change):
    _, snap, inv = prefix
    bars, update = resolved(inv)
    if change=='score': update=replace(update,evidence=replace(update.evidence,score=999))
    if change=='target_version': bars=tuple(replace(b,version_id=b.version_id+'bad') for b in bars)
    if change=='baseline': inv=replace(inv,measurement=replace(inv.measurement,baseline_scale=999))
    if change=='registration': inv=replace(inv,registered_ms=inv.registered_ms+1)
    if change=='assessment': update=replace(update,assessment=(('same_direction','compatible'),))
    with pytest.raises(ValueError): M.verified_case(inv,update,(*snap.bars,*bars),update.observed_ms)


def test_delayed_arrival_and_retrieval_capacity(prefix):
    _, snap, inv = prefix
    new, bars, update = later(prefix)
    case = M.verified_case(inv,update,(*snap.bars,*bars),update.observed_ms)
    cases=[replace(case,case_id=f'case{i}',source_id=f'source{i}') for i in range(8)]
    ctx=M.retrieve(new,list(reversed(cases)))
    assert ctx==M.retrieve(new,cases)
    assert len(ctx['cases'])==M.MAX_RETRIEVED
    assert sum(a['reason']=='retrieval_capacity' for a in ctx['audit'])==5
    delayed=replace(case,recorded_ms=new.registered_ms+I.TF)
    assert M.retrieve(new,[delayed])['cases']==[]


def test_consumer_chronology_restart_export_and_retention(tmp_path):
    import time
    now=int(time.time()*1000)//I.TF*I.TF+60_000
    src,dst=tmp_path/'attention.db',tmp_path/'investigation.db'
    publish(src,now); C.step(src,dst,now)
    first=C.dossiers(dst)
    assert first and all(not d['memory']['context']['cases'] for d in first)
    end=first[0]['investigation']['measurement']['deadline_ms']
    publish(src,end+1,'resolve'); C.step(src,dst,end+1)
    # Actual first consumer availability is later than the outcome's bar clock.
    C.step(src,dst,end+2)
    with sqlite3.connect(dst) as db:
        assert db.execute('SELECT COUNT(*) FROM memory_cases').fetchone()[0]>0
    current=[d for d in C.dossiers(dst) if d['updates'][-1]['evidence']['status']=='unresolved']
    end2=max(d['investigation']['measurement']['deadline_ms'] for d in current)
    publish(src,end2+1,'next'); C.step(src,dst,end2+1)
    enriched=[d for d in C.dossiers(dst) if any(r['changed'] for r in d['memory']['reasoning'])]
    assert enriched
    frozen=C.dossiers(dst)
    C.step(src,dst,end2+2)
    assert C.dossiers(dst)==frozen
    for d in enriched:
        ctx=d['memory']['context']
        assert all(c['recorded_ms'] < ctx['cutoff_ms'] for c in ctx['cases'])
        assert all(r['with_memory']['kind']==r['without_memory']['kind'] for r in d['memory']['reasoning'])
    # Exported context contains complete matched source measurements even if
    # the older source is later removed by bounded retention.
    assert json.loads(I.encode(enriched))==enriched
    with C.ledger(dst) as db:
        db.execute('DELETE FROM cases'); S.retain(db)
        for table in ('memory_cases','memory_contexts','memory_reasoning'):
            assert db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]==0


def test_full_raw_source_archive_survives_source_retention(tmp_path):
    import time
    now=int(time.time()*1000)//I.TF*I.TF+60_000
    src,dst=tmp_path/'attention.db',tmp_path/'investigation.db'
    publish(src,now);C.step(src,dst,now)
    end=C.dossiers(dst)[0]['investigation']['measurement']['deadline_ms']
    publish(src,end+1,'resolve');C.step(src,dst,end+1);C.step(src,dst,end+2)
    end2=max(d['investigation']['measurement']['deadline_ms'] for d in C.dossiers(dst)
             if d['updates'][-1]['evidence']['status']=='unresolved')
    publish(src,end2+1,'later');C.step(src,dst,end2+1)
    d=next(d for d in C.dossiers(dst) if any(r['changed'] for r in d['memory']['reasoning']))
    iid=d['investigation']['investigation_id']
    with C.ledger(dst) as db:
        archive=S.export_case(db,iid)
        S.replay_export(json.loads(I.encode(archive)))
        for case in d['memory']['context']['cases']:
            db.execute('DELETE FROM cases WHERE id=?',(case['source_id'],))
        db.execute('DELETE FROM case_inputs WHERE case_id NOT IN (SELECT id FROM cases)')
        db.execute('DELETE FROM inputs WHERE id NOT IN (SELECT input_id FROM case_inputs)')
        S.retain(db)
        assert S.export_case(db,iid)==archive
        S.replay_export(archive)
        assert all(raw['inputs'] for raw in archive['memory']['context']['source_archives'])
