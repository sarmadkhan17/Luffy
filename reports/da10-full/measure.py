"""Bounded offline SQLite/storage measurements on recovery scratch only."""
import gc,json,sqlite3,time,tracemalloc,threading,socket,statistics
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch
from trader.learning import capture as C,foundation as L,consumers
from trader.observability.prospective_execution import Capture
from trader.persistence.backup import BackupSets,critical_inventory
from trader.core.journal import Journal
from trader.portfolio import runtime as R
from tests.test_portfolio_allocator import inputs

P=Path(__file__).parent
SCRATCH=Path('/mnt/luffy-recovery/recovery/sol1-da10-full-tmp-20261016')
def deny(*a,**k):raise AssertionError('offline network denied')
socket.socket.connect=deny;socket.create_connection=deny

def size(path):return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
def wal_size(path):
    wal=Path(str(path)+'-wal')
    return wal.stat().st_size if wal.exists() else 0
def fds(path):
    prefix=str(path.resolve());count=0
    for p in Path('/proc/self/fd').iterdir():
        try:count+=str(p.readlink()).startswith(prefix)
        except OSError:pass
    return count

def run(label):
    import shutil
    root=SCRATCH/('measure-'+label);root.mkdir()
    result={}
    db=sqlite3.connect(root/'learning.db');C.ensure(db);db.commit()
    value={'records':[{'i':i,'text':str(i)+'x'*8192} for i in range(256)]}
    times=[];calls=[]
    original=L.canonical
    def canonical(v):calls.append(1);return original(v)
    for _ in range(5):
        with patch.object(L,'canonical',canonical):
            start=time.perf_counter();C.snapshot(db,'data',value,source_id='source',version='v1',available_ms=1,producer='test');db.commit();times.append((time.perf_counter()-start)*1000)
    result['snapshot']=dict(median_ms=statistics.median(times),runs_ms=times,canonical_calls_per_call=len(calls)//5,rows=db.execute('select count(*) from learning_source_blobs').fetchone()[0])
    db.close()
    ledger=root/'observation.db';cap=Capture(ledger);identity=cap.write('note',{'value':1});gc.collect()
    anchor=sqlite3.connect(ledger);anchor.execute('PRAGMA journal_mode=WAL');anchor.commit()
    baseline_wal=wal_size(ledger)
    gc.disable()
    before_fd=fds(root)
    for _ in range(32):assert cap.write('note',{'value':1})==identity
    result['duplicate_capture']=dict(retries=32,open_fd_growth=fds(root)-before_fd,wal_growth_bytes=wal_size(ledger)-baseline_wal,sequence=anchor.execute('select seq from sqlite_sequence').fetchone()[0],rows=anchor.execute('select count(*) from observations').fetchone()[0])
    anchor.close();gc.enable();gc.collect()
    with closing(sqlite3.connect(root/'reader.db')) as rd:rd.execute('create table evidence(value)');rd.commit()
    gc.disable();before_fd=fds(root)
    for _ in range(32):
        with consumers.retained(root/'reader.db') as reader:assert reader.query('select * from evidence')==[]
    result['reader_fd_growth']=fds(root)-before_fd
    gc.enable();gc.collect()
    # Use existing backup fixture contracts with a bounded 8 MiB database.
    source=root/'source';source.mkdir()
    for name in ('SDD.md','STATE.yaml','NEXT.yaml','config.yaml'):(source/name).write_text('test: version1\n')
    journal=Journal(source/'data/luffy.db');journal.kv_set('control_state','FROZEN');journal.kv_set('execution_recovery','null')
    (source/'data/ewa_state.json').write_text('{"agents":{},"processed":[],"updated":0}')
    with journal._tx() as connection:
        connection.execute('create table retained_storage_probe(id integer primary key,payload blob)')
        for i in range(128):connection.execute('insert into retained_storage_probe values (?,?)',(i,bytes([i])*65536))
    journal._conn().close()
    sets=BackupSets(source,root/'generations',critical_inventory(source),repository_version='offline',clock=lambda:1234.)
    measures={}
    for operation,fn in [('capture',sets.capture)]:
        tracemalloc.start();start=time.perf_counter();generation=fn();current,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
        measures[operation]=dict(seconds=time.perf_counter()-start,peak_python_bytes=peak)
    tracemalloc.start();start=time.perf_counter();manifest=sets.verify(generation);_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    measures['verify']=dict(seconds=time.perf_counter()-start,peak_python_bytes=peak)
    tracemalloc.start();start=time.perf_counter();receipt=sets.restore(generation,root/'restored');_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    measures['restore']=dict(seconds=time.perf_counter()-start,peak_python_bytes=peak)
    result['backup']=dict(measurements=measures,asset_identity={e['path']:{k:e[k] for k in ('size','sha256')} for e in manifest['assets']},restore=receipt['restore'])
    # Existing runtime portfolio fixture, default SQLite durability.
    path=root/'portfolio.db';consumer=R.Consumer(path);book=inputs();first=consumer.consume(book,processed_at=book.as_of_ms)
    probe={};ready=threading.Event()
    def blocker():
        with closing(sqlite3.connect(path)) as other:
            other.execute('BEGIN IMMEDIATE');ready.set();time.sleep(.12);other.rollback()
    thread=threading.Thread(target=blocker);thread.start();assert ready.wait(1)
    start=time.perf_counter();again=consumer.consume(book,processed_at=book.as_of_ms);probe['duplicate_during_other_writer_ms']=(time.perf_counter()-start)*1000
    thread.join();assert again==dict(first,duplicate=True)
    # Measure actual writer ownership during expensive pure preparation.
    waits=[];held=[];bodies=[]
    for index in range(4):
        current=replace(book,as_of_ms=book.as_of_ms+index+1)
        started=threading.Event();finished=threading.Event();original_opt=R.gate.reoptimize
        def compete():
            assert started.wait(2)
            with closing(sqlite3.connect(path,timeout=2)) as other:
                begin=time.perf_counter();other.execute('BEGIN IMMEDIATE');waits.append((time.perf_counter()-begin)*1000);other.rollback()
            finished.set()
        def slow(*a,**kw):
            started.set();time.sleep(.04)
            return original_opt(*a,**kw)
        thread=threading.Thread(target=compete);thread.start()
        with patch.object(R.gate,'reoptimize',slow):body=consumer.consume(current,processed_at=current.as_of_ms)
        thread.join();assert R.replay(body)==body;bodies.append(body)
    result['contention']=dict(**probe,pure_prepare_probe_wait_ms=waits,max_prepare_probe_wait_ms=max(waits),fixture_delay_ms=40)
    result['replay_bodies']=bodies
    result['scratch_peak_logical_bytes']=size(root)
    result['limits']='Synthetic bounded fixtures, default durability; fixed 40ms pure-preparation injection for lock ownership and 120ms external writer. Memory is Python tracemalloc, not total RSS. No production stores or providers.'
    (P/(label+'-measurements.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('replay_bodies','backup')},indent=2))
    shutil.rmtree(root)
if __name__=='__main__':
    import sys;run(sys.argv[1])
