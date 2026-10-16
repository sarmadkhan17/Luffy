"""DA-10 bounded storage regressions; real SQLite, synthetic data, no providers."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from trader.learning import capture as C, foundation as L, consumers
from trader.observability import prospective_execution as P
from tests.test_stage1_recovery_monitoring import backup  # fixture, offline store guard


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    def deny(*a, **kw):raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket,'connect',deny)
    monkeypatch.setattr(socket,'create_connection',deny)


@pytest.mark.parametrize('failure',[False,True])
def test_retained_snapshot_reader_is_closed_at_scope_exit(tmp_path,failure):
    path=tmp_path/'j.db'
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE evidence(value)');db.commit()
    reader=None
    try:
        with consumers.retained(path) as reader:
            assert reader.query('SELECT * FROM evidence')==[]
            if failure:raise RuntimeError('injected')
    except RuntimeError:pass
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        reader.query('SELECT 1')


def test_learning_snapshot_serializes_once_with_exact_original_identity(tmp_path,monkeypatch):
    value={'nan_is_not_defaulted':None,'values':[1,.12345678901234568,'quoted \\ bytes']}
    expected=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
    canonical=L.canonical;calls=[]
    def counted(x):calls.append(x);return canonical(x)
    monkeypatch.setattr(L,'canonical',counted)
    with closing(sqlite3.connect(tmp_path/'j.db')) as db:
        C.ensure(db)
        dep=C.snapshot(db,'data',value,source_id='exact',version='v1',available_ms=1,producer='test')
        assert len(calls)==1
        assert dep['sha256']==hashlib.sha256(expected.encode()).hexdigest()
        assert db.execute('SELECT payload FROM learning_source_blobs').fetchone()[0]==expected
        assert C.resolve(db,dep)==value
        db.commit()


def test_duplicate_execution_capture_does_not_rewrite_sequence(tmp_path):
    cap=P.Capture(tmp_path/'p.db')
    identity=cap.write('note',{'value':1})
    with closing(sqlite3.connect(cap.path)) as db:
        before=db.execute('SELECT * FROM observations').fetchall()
        seq=db.execute('SELECT * FROM sqlite_sequence').fetchall()
    for _ in range(4):assert cap.write('note',{'value':1})==identity
    with closing(sqlite3.connect(cap.path)) as db:
        assert db.execute('SELECT * FROM observations').fetchall()==before
        assert db.execute('SELECT * FROM sqlite_sequence').fetchall()==seq
    assert cap.write('note',{'value':2})!=identity
    with closing(sqlite3.connect(cap.path)) as db:
        assert db.execute('SELECT count(*) FROM observations').fetchone()[0]==2


def track_connections(monkeypatch):
    original=sqlite3.connect;connections=[]
    def tracked(*args,**kw):
        db=original(*args,**kw);connections.append(db);return db
    monkeypatch.setattr(sqlite3,'connect',tracked)
    return connections


def assert_closed(connections):
    assert connections
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError,match='closed'):db.execute('SELECT 1')


def test_execution_capture_and_replay_close_owned_connections(tmp_path,monkeypatch):
    connections=track_connections(monkeypatch)
    cap=P.Capture(tmp_path/'p.db');assert cap.write('note',{'value':1})
    assert P.replay_observations(cap.path)==[]
    assert_closed(connections)


def test_empty_accounting_queue_closes_without_venue_access(tmp_path,monkeypatch):
    from trader.observability.accounting import step
    path=tmp_path/'j.db'
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE trade_accounting_bookings(id INTEGER,trade_id TEXT)');db.commit()
    connections=track_connections(monkeypatch)
    def never():pytest.fail('empty queue constructed provider')
    health=step(path,tmp_path/'accounting',never,now_ms=1)
    assert health['attempted']==0
    assert_closed(connections)


@pytest.mark.parametrize('failure',[False,True])
def test_accounting_delivery_closes_and_commits_or_refuses(tmp_path,monkeypatch,failure):
    from trader.learning import producers
    from trader.engine import trade_accounting
    path=tmp_path/'j.db'
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE learning_registrations(event_key TEXT)')
        db.execute('CREATE TABLE trades(id TEXT,entry_identity_json TEXT)')
        db.execute('CREATE TABLE delivered(value TEXT)');db.commit()
    monkeypatch.setattr(trade_accounting,'verified_outcome',lambda *a:{})
    monkeypatch.setattr(C,'trade_event',lambda *a:'event')
    def registration(*a):
        if failure:raise ValueError('injected_validation')
        return None,{}
    monkeypatch.setattr(C,'registration',registration)
    monkeypatch.setattr(producers,'verify_trade_binding',lambda *a:None)
    def delivered(db,*a,**kw):
        db.execute("INSERT INTO delivered VALUES ('exact')");return 'outcome'
    monkeypatch.setattr(C,'safely',delivered)
    connections=track_connections(monkeypatch)
    artifact={'captured_ms':1,'sha256':'test','bookings':{'trade':{'id':'t','decision_id':'d'}}}
    if failure:
        with pytest.raises(ValueError,match='injected_validation'):producers.deliver_accounting(path,artifact)
    else:assert producers.deliver_accounting(path,artifact)=='outcome'
    assert_closed(connections)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT * FROM delivered').fetchall()==([] if failure else [('exact',)])


def test_portfolio_delivery_closes_its_owned_journal_connection(tmp_path,monkeypatch):
    from trader.portfolio import current,runtime
    path=tmp_path/'j.db'
    with closing(sqlite3.connect(path)) as db:
        db.execute('CREATE TABLE delivered(value TEXT)');db.commit()
    monkeypatch.setattr(current,'freeze',lambda *a:({},{}))
    monkeypatch.setattr(runtime.Consumer,'consume',lambda *a:{'portfolio_cut_id':'cut','proposal':None})
    def delivered(db,*a,**kw):
        db.execute("INSERT INTO delivered VALUES ('exact')");return 'outcome'
    monkeypatch.setattr(C,'safely',delivered)
    connections=track_connections(monkeypatch)
    result,detail=current.checkpoint(path,{})
    assert detail['learning_source_delivery']=='outcome'
    assert_closed(connections)


def test_backup_and_restore_never_materialize_whole_databases(backup,tmp_path,monkeypatch):
    _,_,sets=backup;original=Path.read_bytes
    def bounded(self):
        if self.suffix=='.db':pytest.fail('whole database read_bytes')
        return original(self)
    monkeypatch.setattr(Path,'read_bytes',bounded)
    generation=sets.capture();sets.verify(generation)
    receipt=sets.restore(generation,tmp_path/'restored')
    assert receipt['restore']=='PUBLISHED'


def test_restored_store_integrity_is_checked_once_per_asset(backup,tmp_path,monkeypatch):
    from trader.persistence import backup as B
    _,_,sets=backup;generation=sets.capture();original=B.metadata;counts=[]
    def counted(path):
        if '.restore-' in str(path):counts.append(path)
        return original(path)
    monkeypatch.setattr(B,'metadata',counted)
    assert sets.restore(generation,tmp_path/'restored')['restore']=='PUBLISHED'
    sqlite_assets=[a for a in sets.inventory.assets if a.kind=='sqlite']
    assert len(counts)==len(sqlite_assets)


def fast_portfolio_connections(monkeypatch):
    from trader.portfolio import runtime as R
    original=R.Consumer._db
    def fast(self):
        db=original(self);db.execute('PRAGMA busy_timeout=25');return db
    monkeypatch.setattr(R.Consumer,'_db',fast)


def test_portfolio_duplicate_replay_needs_no_writer_lock(tmp_path,monkeypatch):
    from trader.portfolio import runtime as R
    from tests.test_portfolio_allocator import inputs
    fast_portfolio_connections(monkeypatch)
    book=inputs();consumer=R.Consumer(tmp_path/'p.db');first=consumer.consume(book,processed_at=book.as_of_ms)
    with closing(sqlite3.connect(consumer.path)) as other:
        other.execute('BEGIN IMMEDIATE')
        try:assert consumer.consume(book,processed_at=book.as_of_ms)==dict(first,duplicate=True)
        finally:other.rollback()


def test_portfolio_pure_preparation_does_not_own_writer(tmp_path,monkeypatch):
    from trader.portfolio import runtime as R
    from tests.test_portfolio_allocator import inputs
    book=inputs();consumer=R.Consumer(tmp_path/'p.db');original=R.gate.reoptimize
    def probe(*a,**kw):
        with closing(sqlite3.connect(consumer.path,timeout=.025)) as other:
            other.execute('BEGIN IMMEDIATE');other.rollback()
        return original(*a,**kw)
    monkeypatch.setattr(R.gate,'reoptimize',probe)
    result=consumer.consume(book,processed_at=book.as_of_ms)
    assert R.replay(result)==result


def test_portfolio_concurrent_change_preserves_serialized_results(tmp_path,monkeypatch):
    from dataclasses import replace
    from trader.portfolio import runtime as R
    from tests.test_portfolio_allocator import inputs
    fast_portfolio_connections(monkeypatch)
    older=replace(inputs(),as_of_ms=1501);book=replace(inputs(),as_of_ms=1502)
    # The existing serialized order is the oracle for a concurrent state change.
    serial=R.Consumer(tmp_path/'serial.db')
    serial.consume(older,processed_at=older.as_of_ms)
    expected=serial.consume(book,processed_at=book.as_of_ms)
    consumer=R.Consumer(tmp_path/'p.db');original=R.gate.reoptimize;once=[]
    def race(*a,**kw):
        if not once:
            once.append(True)
            R.Consumer(consumer.path).consume(older,processed_at=older.as_of_ms)
        return original(*a,**kw)
    monkeypatch.setattr(R.gate,'reoptimize',race)
    assert consumer.consume(book,processed_at=book.as_of_ms)==expected
    with closing(sqlite3.connect(consumer.path)) as db:
        assert db.execute('select count(*) from evaluations').fetchone()[0]==2
        marker=db.execute('select inputs_json from state').fetchone()[0]
        from trader.core import journal_evidence as E
        assert json.loads(E.resolve(E.install(db),marker))['as_of_ms']==book.as_of_ms


def test_snapshot_refuses_known_insufficient_space_before_copying(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from trader.persistence import storage_cleanup as S
    source=tmp_path/'source';source.mkdir();(source/'j.db').write_bytes(b'bounded fixture')
    monkeypatch.setattr(S.shutil,'disk_usage',lambda *a:SimpleNamespace(free=0))
    monkeypatch.setattr(S.shutil,'copyfile',lambda *a:pytest.fail('known impossible copy started'))
    with pytest.raises(ValueError,match='SNAPSHOT_SPACE_INSUFFICIENT'):
        S.snapshot(source,tmp_path/'scratch')
    assert not list((tmp_path/'scratch/copies').iterdir())


def test_copy_rehearsal_closes_reader_when_inventory_fails(tmp_path,monkeypatch):
    from trader.persistence import storage_cleanup as S
    from tests.test_storage_cleanup import fixture_db
    db,*_=fixture_db(tmp_path);path=Path(db.execute('PRAGMA database_list').fetchone()[2]);db.close()
    original=S.read_normalized_copy;connections=[]
    def tracked(*a):
        db=original(*a);connections.append(db);return db
    monkeypatch.setattr(S,'read_normalized_copy',tracked)
    def failure(*a):raise ValueError('injected_inventory')
    monkeypatch.setattr(S,'inventory',failure)
    with pytest.raises(ValueError,match='injected_inventory'):S.rehearse(path.parent,tmp_path/'report')
    assert_closed(connections)


def test_changed_generation_during_streamed_restore_is_never_published(backup,tmp_path,monkeypatch):
    from trader.persistence import backup as B
    _,_,sets=backup;generation=sets.capture();original=B.durable_copy;changed=[]
    def corrupt(dest,source,size,sha):
        if source.suffix=='.db' and not changed:
            changed.append(True)
            with source.open('ab') as f:f.write(b'injected')
        return original(dest,source,size,sha)
    monkeypatch.setattr(B,'durable_copy',corrupt)
    target=tmp_path/'restored'
    with pytest.raises(B.Refused,match='asset_changed_during_restore'):sets.restore(generation,target)
    assert not target.exists() and changed


def test_concurrent_same_portfolio_cut_returns_exact_duplicate(tmp_path,monkeypatch):
    from trader.portfolio import runtime as R
    from tests.test_portfolio_allocator import inputs
    fast_portfolio_connections(monkeypatch)
    book=inputs();consumer=R.Consumer(tmp_path/'p.db');original=R.gate.reoptimize;other=[]
    def race(*a,**kw):
        if not other:
            other.append(None)
            other[0]=R.Consumer(consumer.path).consume(book,processed_at=book.as_of_ms)
        return original(*a,**kw)
    monkeypatch.setattr(R.gate,'reoptimize',race)
    assert consumer.consume(book,processed_at=book.as_of_ms)==dict(other[0],duplicate=True)
    with closing(sqlite3.connect(consumer.path)) as db:
        assert db.execute('select count(*) from evaluations').fetchone()[0]==1


@pytest.mark.parametrize('fault',[KeyboardInterrupt,SystemExit,GeneratorExit,RuntimeError])
def test_journal_interrupted_write_releases_writer_and_preserves_exception(tmp_path,fault):
    import threading
    from trader.core.journal import Journal
    journal=object.__new__(Journal);journal.db_path=tmp_path/'j.db'
    journal._local=threading.local();journal._write_lock=threading.Lock()
    db=sqlite3.connect(journal.db_path);db.execute('create table evidence(value)');db.commit()
    journal._local.conn=db;original=fault('injected')
    try:
        with pytest.raises(fault) as caught:
            with journal._tx() as tx:
                tx.execute('insert into evidence values (1)');raise original
        assert caught.value is original
        with closing(sqlite3.connect(journal.db_path,timeout=.025)) as other:
            other.execute('BEGIN IMMEDIATE');other.rollback()
            assert other.execute('select * from evidence').fetchall()==[]
    finally:db.rollback();db.close()


@pytest.mark.parametrize('rollback_fault',[sqlite3.OperationalError,KeyboardInterrupt,SystemExit,None])
def test_journal_failed_rollback_discards_connection_without_masking_commit_error(tmp_path,rollback_fault):
    import threading
    from trader.core.journal import Journal
    original=sqlite3.OperationalError('database full')
    class Failing(sqlite3.Connection):
        fail=False
        def commit(self):
            if self.fail:raise original
            return super().commit()
        def rollback(self):
            if self.fail:
                if rollback_fault is not None:raise rollback_fault('injected rollback failure')
                return  # A rollback which returns while still in transaction.
            return super().rollback()
    journal=object.__new__(Journal);journal.db_path=tmp_path/'j.db'
    journal._local=threading.local();journal._write_lock=threading.Lock()
    db=sqlite3.connect(journal.db_path,factory=Failing);db.execute('create table evidence(value)');db.commit()
    journal._local.conn=db;db.fail=True
    try:
        with pytest.raises(BaseException) as caught:
            with journal._tx() as tx:tx.execute('insert into evidence values (1)')
        assert caught.value is original
        assert journal._safety_failed
        assert journal._local.conn is None
        with closing(sqlite3.connect(journal.db_path,timeout=.025)) as other:
            other.execute('BEGIN IMMEDIATE');other.rollback()
            assert other.execute('select * from evidence').fetchall()==[]
    finally:
        db.fail=False
        try:db.rollback()
        except sqlite3.ProgrammingError:pass  # the failed connection was discarded
        db.close()


@pytest.mark.parametrize('failure',[False,True])
def test_research_numeric_binding_scope_closes_and_commits_without_running_research(tmp_path,monkeypatch,failure):
    # Execute the actual small creation scope from cycle(). Numerical/referee
    # computation is outside this connection-lifetime regression's scope.
    import ast,inspect
    from trader.research import predictive_bridge as B
    tree=ast.parse(inspect.getsource(B.cycle))
    scopes=(n for n in ast.walk(tree) if isinstance(n,ast.With) and any(
        isinstance(v,ast.Constant) and isinstance(v.value,str) and
        v.value.startswith('CREATE TABLE predictive_experiment_binding') for v in ast.walk(n)))
    scope=min(scopes,key=lambda n:sum(1 for _ in ast.walk(n)))
    function=ast.parse('def create_binding(numerical_path,ex):\n    pass').body[0]
    function.body=[scope]
    module=ast.fix_missing_locations(ast.Module(body=[function],type_ignores=[]))
    namespace=dict(B.__dict__);exec(compile(module,'<real-numerical-binding-scope>','exec'),namespace)
    original=sqlite3.connect;connections=[]
    class Fault(sqlite3.Connection):
        def execute(self,sql,*a,**kw):
            if failure and sql.startswith('INSERT INTO predictive_experiment_binding'):
                raise ValueError('injected_binding')
            return super().execute(sql,*a,**kw)
    def tracked(*a,**kw):
        db=original(*a,factory=Fault,**kw);connections.append(db);return db
    monkeypatch.setattr(sqlite3,'connect',tracked)
    path=tmp_path/'numerical.db'
    if failure:
        with pytest.raises(ValueError,match='injected_binding'):namespace['create_binding'](path,{'experiment_id':'exact'})
    else:namespace['create_binding'](path,{'experiment_id':'exact'})
    assert_closed(connections)
    with closing(original(path)) as db:
        assert db.execute('select * from predictive_experiment_binding').fetchall()==([] if failure else [('exact',)])
