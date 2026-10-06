"""OBS-03 metadata-only fixtures; no Kernel, Dashboard, providers or subprocesses."""
import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'docs/tracker/evidence/obs03-first-cycle-sol/source'

@pytest.fixture
def collector(monkeypatch):
    monkeypatch.syspath_prepend(str(SOURCE))
    spec = importlib.util.spec_from_file_location('obs03_collector', SOURCE / 'collect_prepared.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **k: pytest.fail('process effect'))
    return module

def row(stage, status, mono, pid=71, **extra):
    return dict(pid=pid, stage=stage, status=status, monotonic_s=mono, **extra)

def boot(first, pid=71):
    return [row('kernel.__init__', 'COMPLETE', first-20, pid,
                heartbeat_instance_id='fixture', heartbeat_started_at=123),
            row('kernel.boot', 'COMPLETE', first-1, pid)]

def read(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

@pytest.mark.parametrize('first', [0., 100.125, 102990.302153612, 10000000.75])
@pytest.mark.parametrize('pid', [71, 921])
def test_single_boundary_cold_and_shutdown_labels(collector, tmp_path, first, pid):
    s = collector.State(pid)
    s.ingest([row('process.launch', 'BEGIN', first-5000, pid)] + boot(first, pid), tmp_path)
    assert s.first is s.deadline is None
    s.ingest([row('kernel.cycle', 'BEGIN', first, pid),
              row('kernel.cycle', 'COMPLETE', first+200, pid, elapsed_s=200, cold=True)], tmp_path)
    s.ingest([row('kernel.cycle', 'BEGIN', first+230, pid)], tmp_path)
    s.stop = first+240
    s.ingest([row('kernel.cycle', 'COMPLETE', first+270, pid, elapsed_s=40),
              row('cycle.summary', 'COMPLETE', first+271, pid)], tmp_path, 'SHUTDOWN')
    events = read(tmp_path/'controller-events.jsonl')
    records = read(tmp_path/'cycle-measurements.jsonl')
    assert len(events) == 1 and events[0]['monotonic_s'] == first
    assert s.first == first and s.deadline == first+3600
    assert [r['sequence'] for r in records] == [1, 2]
    assert [r['phase'] for r in records] == ['FIRST', 'SHUTDOWN_OVERLAP']
    assert records[0]['record']['cold'] is True
    assert records[1]['cycle_kind'] == 'RECURRING'
    assert (s.completed_window, s.shutdown_cycles) == (1, 1)

@pytest.mark.parametrize('status', ['COMPLETE', 'FAIL'])
def test_first_shutdown_cycle_keeps_first_identity(collector, tmp_path, status):
    s = collector.State(71); s.ingest(boot(100)+[row('kernel.cycle', 'BEGIN', 100)], tmp_path)
    s.stop=340.033
    s.ingest([row('kernel.cycle', status, 359.776, elapsed_s=259.776, cold=True)], tmp_path, 'SHUTDOWN')
    result=read(tmp_path/'cycle-measurements.jsonl')[0]
    assert result['phase']=='SHUTDOWN_OVERLAP' and result['cycle_kind']=='FIRST' and result['sequence']==1
    assert result['record']['status']==status and result['record']['cold']
    assert s.completed_window==0 and s.shutdown_cycles==1


def test_delayed_delivery_does_not_relabel_pre_shutdown_completion(collector, tmp_path):
    s=collector.State(71); s.ingest(boot(100)+[row('kernel.cycle','BEGIN',100)],tmp_path)
    s.stop=150
    s.ingest([row('kernel.cycle','COMPLETE',140,elapsed_s=40)],tmp_path,'SHUTDOWN')
    assert read(tmp_path/'cycle-measurements.jsonl')[0]['phase']=='FIRST'

@pytest.mark.parametrize('bad', [None, True, float('nan'), float('inf'), '100'])
def test_invalid_boundary_fails_explicitly(collector,tmp_path,bad):
    s=collector.State(71); s.ingest(boot(100),tmp_path)
    with pytest.raises(ValueError,match='unbound_cycle_start'):
        s.ingest([row('kernel.cycle','BEGIN',bad)],tmp_path)
    assert s.first is None


def test_duplicate_begin_and_unpaired_completion_fail_explicitly(collector,tmp_path):
    s=collector.State(71); s.ingest(boot(100)+[row('kernel.cycle','BEGIN',100)],tmp_path)
    with pytest.raises(ValueError,match='overlapping_cycle_start'):
        s.ingest([row('kernel.cycle','BEGIN',101)],tmp_path)
    assert s.first==100 and s.cycles==1
    s.ingest([row('kernel.cycle','COMPLETE',110)],tmp_path)
    with pytest.raises(ValueError,match='unbound_cycle_completion'):
        s.ingest([row('kernel.cycle','COMPLETE',111)],tmp_path)

@pytest.mark.parametrize('delay', [10, 5000])
def test_delayed_boot_does_not_consume_hour(collector,tmp_path,delay):
    first=1000+delay
    backend=SimpleNamespace(pids={'kernel':{'pid':71}},preflight_record={},clock=1000,live=True)
    backend.monotonic=lambda: backend.clock; backend.wall=lambda: 10000+backend.clock
    backend.storage=lambda *a: None; backend.resources=lambda:None
    backend.alive=lambda role:backend.live
    backend.start_dashboard=lambda:pytest.fail('no operational launch')
    backend.sample=lambda:dict(controls={'control_state':'FROZEN','macro_guard_operator_hold':'1'},
        health={'schema':'luffy-safety-health.v1','conditions':{},'recovery_required':True},watchdog_off=True)
    def sleep(seconds):
        if backend.clock<first:
            backend.clock=first
            for r in boot(first)+[row('kernel.cycle','BEGIN',first),row('kernel.cycle','COMPLETE',first+.5,elapsed_s=.5)]:
                collector.append(tmp_path/'stages.jsonl',r)
        else: backend.clock+=seconds
    backend.sleep=sleep
    backend.signal_services=lambda:setattr(backend,'live',False)
    result=collector.observe(tmp_path,backend)
    assert result['first_cycle_monotonic_s']==first and first!=1000
    assert result['duration_s']==3600 and result['stop_monotonic_s']==first+3600
    assert result['completed_window_cycles']==1 and result['reason']=='bounded_hour_elapsed'
    (tmp_path/'fixture-result.json').write_text(json.dumps(result,indent=2))

@pytest.mark.parametrize('restart', [False, True])
def test_retention_keeps_late_final_flush_without_duplicates(collector,tmp_path,restart):
    rows=boot(100)+[row('kernel.cycle','BEGIN',100), row('kernel.cycle','COMPLETE',110,elapsed_s=10),
                    row('cycle.summary','COMPLETE',111), row('kernel.cycle','BEGIN',120)]
    s=collector.State(71);s.ingest(rows,tmp_path)
    for r in rows:collector.append(tmp_path/'stages.jsonl',r)
    collector.append(tmp_path/'controller-events.jsonl',{'event':'stop_requested','monotonic_s':130})
    backend=SimpleNamespace(pids={'kernel':{'pid':71}},clock=600,live=True)
    backend.monotonic=lambda:backend.clock;backend.wall=lambda:10000+backend.clock
    backend.storage=lambda *a:None;backend.resources=lambda:None;backend.alive=lambda role:backend.live
    def sleep(seconds):
        backend.clock+=seconds
        # Flush after the last alive poll, so only the final read sees these.
        for r in [row('kernel.cycle','FAIL',601,elapsed_s=481),row('cycle.summary','COMPLETE',602)]:
            collector.append(tmp_path/'stages.jsonl',r)
        backend.live=False
    backend.sleep=sleep
    collector.retain_until_stopped(tmp_path,backend)
    if restart:collector.retain_until_stopped(tmp_path,backend)
    records=read(tmp_path/'cycle-measurements.jsonl')
    assert len(records)==2 and [r['sequence'] for r in records]==[1,2]
    assert records[1]['phase']=='SHUTDOWN_OVERLAP' and records[1]['record']['status']=='FAIL'
    assert len(read(tmp_path/'cycle-summaries.jsonl'))==2
    assert len([r for r in read(tmp_path/'controller-events.jsonl') if r['event']=='window_started'])==1
    final=json.loads((tmp_path/'delayed-observation-result.json').read_text())
    assert final['first_cycle_monotonic_s']==100 and final['deadline_monotonic_s']==3700
    assert final['completed_window_cycles']==1 and final['shutdown_cycles']==1


def test_prior_pid_cannot_start_window(collector,tmp_path):
    s=collector.State(71);s.ingest(boot(100,72)+[row('kernel.cycle','BEGIN',100,72)],tmp_path)
    assert s.first is None and s.cycles==0


def test_prepared_profiler_emits_monotonic_begin_without_running_main(tmp_path):
    """Execute only retained emit() against an inert FD; never import Kernel."""
    import os, threading
    tree=ast.parse((SOURCE/'profile_prepared.py').read_text())
    main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    emit=next(n for n in main.body if isinstance(n,ast.FunctionDef) and n.name=='emit')
    fd=os.open(tmp_path/'stages.jsonl',os.O_CREAT|os.O_WRONLY,0o600)
    namespace=dict(os=os,json=json,time=SimpleNamespace(time=lambda:9999,monotonic=lambda:123.125),
                   threading=threading,lock=threading.RLock(),fd=fd,OUT=tmp_path)
    try:
        exec(compile(ast.Module(body=[emit],type_ignores=[]),'<retained emit>','exec'),namespace)
        namespace['emit']({'stage':'kernel.cycle','status':'BEGIN'})
    finally:os.close(fd)
    result=read(tmp_path/'stages.jsonl')[0]
    assert result['monotonic_s']==123.125 and result['at']==9999
    wrap=next(n for n in main.body if isinstance(n,ast.FunctionDef) and n.name=='wrap')
    run=next(n for n in wrap.body if isinstance(n,ast.FunctionDef) and n.name=='run')
    code=ast.unparse(run)
    assert code.index("status='BEGIN'")<code.index('result = fn(')
    assert 'completed_cycles += 1' in code and "stage='cycle.summary'" in code


def test_exact_retained_predecessor_regressions(collector,tmp_path):
    spec=importlib.util.spec_from_file_location('obs03_before',SOURCE/'before-collect_prepared.py')
    before=importlib.util.module_from_spec(spec);spec.loader.exec_module(before)
    s=before.State(71);s.ingest(boot(100)+[row('kernel.cycle','BEGIN',100)],tmp_path)
    s.stop=150;s.ingest([row('kernel.cycle','COMPLETE',140)],tmp_path,'SHUTDOWN')
    assert read(tmp_path/'cycle-measurements.jsonl')[0]['phase']=='SHUTDOWN_OVERLAP'
    # Original timeout retention reads/counts the late row, but never retains its measurement.
    collector.append(tmp_path/'stages.jsonl',row('kernel.cycle','COMPLETE',600))
    backend=SimpleNamespace(pids={'kernel':{'pid':71}},live=True,monotonic=lambda:600,wall=lambda:10000,
        storage=lambda *a:None,resources=lambda:None)
    backend.alive=lambda role:backend.live
    backend.sleep=lambda seconds:setattr(backend,'live',False)
    before.retain_until_stopped(tmp_path,backend)
    assert len(read(tmp_path/'cycle-measurements.jsonl'))==1


def test_existing_240_second_boundary_unchanged(collector,tmp_path):
    s=collector.State(71);s.ingest(boot(100)+[row('kernel.cycle','BEGIN',100)],tmp_path)
    facts=dict(controls={'control_state':'FROZEN','macro_guard_operator_hold':'1'},
        health={'schema':'luffy-safety-health.v1','conditions':{},'recovery_required':True},watchdog_off=True)
    s.check(facts,None,340,10000);assert s.reason is None
    s.check(facts,None,340.0001,10000)
    assert s.reason=='inflight_cycle_exceeded_existing_240_second_limit'


def test_exact_retained_run_boundary(collector,tmp_path):
    packet=json.loads((SOURCE.parent/'historical-boundary.json').read_text())
    s=collector.State(packet['pid'])
    s.ingest(packet['stages_before_stop'],tmp_path)
    s.stop=packet['stop']['monotonic_s'];s.ingest(packet['stages_after_stop'],tmp_path,'SHUTDOWN')
    assert s.first==packet['window']['monotonic_s']!=packet['launch']['monotonic_s']
    assert s.deadline==packet['window']['deadline_monotonic_s']==s.first+3600
    assert s.stop-s.first==packet['result']['duration_s']
    record=read(tmp_path/'cycle-measurements.jsonl')[0]
    assert record['sequence']==1 and record['cycle_kind']=='FIRST' and record['phase']=='SHUTDOWN_OVERLAP'
    assert s.completed_window==0 and s.shutdown_cycles==1


def test_original_launch_clock_consumes_delayed_boot_without_importing_it():
    tree=ast.parse((SOURCE/'original-launch-clock.py').read_text())
    assignment=next(n for n in tree.body if isinstance(n,ast.Assign)
        and any(isinstance(t,ast.Name) and t.id=='deadline' for t in n.targets))
    namespace={'start_at':1000}
    exec(compile(ast.Module(body=[assignment],type_ignores=[]),'<original deadline>','exec'),namespace)
    assert namespace['deadline']==4600
    assert namespace['deadline']<6000  # boot delay has exhausted the old launch clock
