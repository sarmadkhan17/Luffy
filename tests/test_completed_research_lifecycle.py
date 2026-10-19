"""Synthetic protocol only: real Bank/referee/Factory and exact retained budgets."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest
from trader.persistence import storage_cleanup as S
from trader.research import predictive_bridge as B, predictive_receipt as M, factory_transfer as T
from tests.test_rp03_research_factory_transfer import _build
from tests.test_predictive_research_bridge import CFG


@pytest.fixture(scope='module',params=['SUPPORTED','UNSUPPORTED'])
def completed(request,tmp_path_factory):
    root=tmp_path_factory.mktemp('completed-'+request.param.lower())
    # First retain the completed work, providing an untouched offline baseline
    # against which the automatic restart hook and failure recovery are tested.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(S,'retire_research_work',lambda *a,**k:dict(status='RETAINED',reason='test_baseline'))
        built=_build(root,request.param)
    bank=root/'bridge.db'
    with closing(B.store(bank)) as db:
        result=next(r for r in B.records(db,'bridge_bank_results') if r['classification']==request.param)
        ex=next(x for x in B.records(db,'bridge_experiments') if x['experiment_id']==result['experiment_id'])
    return dict(root=root,bank=bank,result=result,ex=ex,later=built['later'],disposition=request.param)


@pytest.fixture
def copy(completed,tmp_path):
    dst=tmp_path/'copy';shutil.copytree(completed['root'],dst)
    # Nothing in the offline copy aliases original writable files.
    return dict(completed,root=dst,bank=dst/'bridge.db',source_root=completed['root'])


def state(f):
    with closing(B.store(f['bank'])) as db:
        return {t:list(db.execute('SELECT * FROM '+t+' ORDER BY id')) for t in ('bridge_hypotheses','bridge_bank_results','bridge_tests')},db.execute("SELECT payload FROM bridge_runtime_bindings WHERE id='registered-error-budget'").fetchone()[0]


def work(f): return f['root']/'bridge-experiments'/f['ex']['experiment_id']


def cleanup(f):
    # Production calls this under cycle's existing flock; copies have one writer.
    with closing(B.store(f['bank'])) as db:
        return S.retire_research_work(db,f['bank'],f['ex'],f['result'])


def test_terminal_automatic_restart_cleanup_replay_budget_and_factory(copy):
    f=copy;before=state(f)
    with closing(B.store(f['bank'])) as db: evidence=M.snapshot(db,f['ex'])
    raw_hash=S.file_hash(work(f)/'candles.db')
    calls=[]
    def forbidden(*a,**k): calls.append(a);pytest.fail('completed research must not retest')
    # The real next cycle is the restart point; terminal history prevents new looks.
    for _ in range(3):
        out=B.cycle(f['source_root']/'investigation.db',f['bank'],f['root']/'candles.db',CFG,
                    now_ms=f['later'],run=forbidden,submit=False)
        if not work(f).exists(): break
    assert not work(f).exists(),out['lifecycle']
    assert not calls and state(f)==before
    with closing(B.store(f['bank'])) as db: assert M.snapshot(db,f['ex'])==evidence
    with closing(S.completed_reader(f['bank'],f['ex']['experiment_id'],dataset=True)) as db:
        assert hashlib.sha256(db.serialize()).hexdigest()==raw_hash
        assert db.execute('SELECT count(*) FROM candles').fetchone()[0]>0
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    if f['disposition']=='SUPPORTED':
        packet=T.authenticate(f['bank'],f['result']['result_id'])
        assert packet['look_seq']==1 and packet['tests']==evidence['spent_looks']
    assert cleanup(f)['status']=='ALREADY_RETIRED'


def test_active_unknown_financial_wal_and_changed_evidence_refuse(copy):
    f=copy;p=work(f)
    (p/'unknown-investigation-evidence').write_text('keep')
    assert cleanup(f)['reason']=='unknown_work_dependency'
    (p/'unknown-investigation-evidence').unlink()
    with closing(sqlite3.connect(p/'quantitative.db')) as db, db:
        db.execute("INSERT INTO research_batches(tf,geo,started) VALUES ('4h','fixed','active')")
    assert cleanup(f)['reason']=='unfinished_research_batch'
    with closing(sqlite3.connect(p/'quantitative.db')) as db, db:
        db.execute("DELETE FROM research_batches WHERE started='active'")
        db.execute("INSERT INTO state_kv(key,value) VALUES ('recovery.dependency','required')")
    assert cleanup(f)['reason']=='recovery_state_dependency'
    with closing(sqlite3.connect(p/'quantitative.db')) as db, db:
        db.execute("DELETE FROM state_kv WHERE key='recovery.dependency'")
    (p/'quantitative.db-wal').write_bytes(b'unresolved')
    assert cleanup(f)['reason']=='unresolved_WAL_or_writer'
    assert p.is_dir()


def test_archive_commit_precedes_unlink_and_restart_is_idempotent(copy,monkeypatch):
    f=copy;original=Path.unlink;failed=[]
    def crash(path,*a,**k):
        if path.parent==work(f) and path.name=='quantitative.db' and not failed:
            failed.append(True);raise OSError('injected_crash_after_archive_commit')
        return original(path,*a,**k)
    with monkeypatch.context() as mp:
        mp.setattr(Path,'unlink',crash)
        assert cleanup(f)['status']=='REFUSED'
    assert failed
    with closing(B.store(f['bank'])) as db:
        assert db.execute('SELECT 1 FROM bridge_runtime_bindings WHERE id=?',(S.RESEARCH_CAPSULE_KEY+f['ex']['experiment_id'],)).fetchone()
    assert cleanup(f)['status']=='RETIRED'
    assert cleanup(f)['status']=='ALREADY_RETIRED'


def test_uncommitted_capsule_never_deletes_work(copy,monkeypatch):
    f=copy;original=B.append
    def fail(db,table,key,body):
        if key.startswith(S.RESEARCH_CAPSULE_KEY): raise sqlite3.OperationalError('injected_archive_commit_failure')
        return original(db,table,key,body)
    with monkeypatch.context() as mp:
        mp.setattr(B,'append',fail)
        assert cleanup(f)['status']=='REFUSED'
    assert (work(f)/'candles.db').is_file() and (work(f)/'quantitative.db').is_file()
    assert cleanup(f)['status']=='RETIRED'


def test_incomplete_and_explicit_cancel_abandon_terminal_predicate():
    assert not S.terminal_research(dict(classification='INCONCLUSIVE',referee_disposition={'state':'queued'}))
    for disposition in ('CANCELLED','ABANDONED'):
        assert S.terminal_research(dict(classification='INCONCLUSIVE',referee_disposition={'state':disposition}))


@pytest.mark.parametrize('reason',['CANCELLED','ABANDONED'])
def test_explicit_terminal_disposition_keeps_statistical_expenditure(copy,reason):
    f=copy;before=state(f)
    with closing(B.store(f['bank'])) as db:
        row=db.execute('SELECT payload FROM bridge_hypotheses WHERE id=?',(f['result']['hypothesis_id'],)).fetchone()
        h=B.P.hypothesis_from_dict(json.loads(row[0])['hypothesis'])
        with db: B.feedback(db,h,f['ex']['experiment_id'],'INCONCLUSIVE',None,dict(state=reason,reason='test explicit owner completion'),f['later']+1)
        f['result']=B.records(db,'bridge_bank_results')[-1]
    assert cleanup(f)['status']=='RETIRED'
    after=state(f)
    assert after[0]['bridge_tests']==before[0]['bridge_tests'] and after[1]==before[1]
    assert not work(f).exists()


def test_existing_factory_consumer_and_tamper_contracts(copy,tmp_path):
    if copy['disposition']!='SUPPORTED': return
    from tests import test_rp03_research_factory_transfer as original
    from trader.core.journal import Journal
    from trader.research.ledger import Ledger
    population=(copy['bank'],copy['later'])
    target=Journal(tmp_path/'factory-journal.db');Ledger(target).ensure()
    # Exercise the existing tests against the retained-evidence layout, using
    # this module's already-built genuine protocol fixture.
    assert cleanup(copy)['status']=='RETIRED'
    original.test_baseline_factory_cannot_see_the_experiment_evidence(population,target)
    original.test_supported_result_transfers_and_factory_validates(population,target)
    untouched=Journal(tmp_path/'untouched-target.db');Ledger(untouched).ensure()
    original.test_forged_experiment_combination_row_is_refused(population,target=untouched)
    original.test_missing_or_foreign_experiment_ledger_is_refused(population,tmp_path)
