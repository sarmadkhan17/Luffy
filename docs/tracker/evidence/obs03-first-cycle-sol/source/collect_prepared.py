"""Existing bounded collector: new-process boot gate, monotonic cycle window and drain."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
from metadata import Tail, append, blocking_conditions

PRODUCTION=Path('/mnt/luffy-data/luffy/production')
HERE=Path(__file__).parent


def same_process(item):
    try:
        fields=Path('/proc',str(item['pid']),'stat').read_text().rsplit(')',1)[1].split()
        return fields[19]==item['start_ticks'] and fields[0]!='Z'
    except FileNotFoundError: return False


def storage(directory,name):
    subprocess.run([sys.executable,'-B',str(HERE/'storage.py'),str(directory/(name+'.json'))],check=True,timeout=30)


class State:
    def __init__(self,pid):
        self.pid=pid;self.boot=False;self.instance=None;self.started_at=None
        self.first=None;self.deadline=None;self.cycle_start=None;self.cycles=0
        self.last_beat=None;self.gaps=[];self.stop=None;self.reason=None
        self.completed_window=0;self.shutdown_cycles=0

    def ingest(self,rows,directory,phase='OBSERVATION',recorded=None):
        def emit(name,value):
            key=(name,json.dumps(value.get('record',value),sort_keys=True))
            if recorded is None or key not in recorded:
                append(directory/name,value)
                if recorded is not None: recorded.add(key)
        for row in rows:
            if row.get('pid')!=self.pid: continue
            if row.get('status') in ('FAIL','METADATA_BOUND'):
                self.reason=self.reason or 'stage_failure:'+str(row.get('stage'))
            stage=row.get('stage');status=row.get('status');mono=row.get('monotonic_s')
            if stage=='kernel.__init__' and status=='COMPLETE':
                self.instance=row['heartbeat_instance_id'];self.started_at=row['heartbeat_started_at']
            if stage=='kernel.boot' and status=='COMPLETE': self.boot=True
            if stage=='kernel.cycle' and status=='BEGIN':
                if not self.boot or type(mono) not in (int,float) or not math.isfinite(mono): raise ValueError('unbound_cycle_start')
                if self.cycle_start is not None: raise ValueError('overlapping_cycle_start')
                self.cycles+=1;self.cycle_start=mono
                if self.first is None:
                    self.first=mono;self.deadline=mono+3600
                    emit('controller-events.jsonl',dict(event='window_started',pid=self.pid,monotonic_s=mono,deadline_monotonic_s=self.deadline))
            if stage=='kernel.cycle' and status in ('COMPLETE','FAIL'):
                if self.cycle_start is None or type(mono) not in (int,float) or not math.isfinite(mono) or mono<self.cycle_start:
                    raise ValueError('unbound_cycle_completion')
                overlap=(self.stop is not None and mono>=self.stop) or (self.deadline is not None and mono>self.deadline)
                kind='SHUTDOWN_OVERLAP' if overlap else ('FIRST' if self.cycles==1 else 'RECURRING')
                if overlap: self.shutdown_cycles+=1
                elif status=='COMPLETE': self.completed_window+=1
                emit('cycle-measurements.jsonl',dict(phase=kind,cycle_kind='FIRST' if self.cycles==1 else 'RECURRING',sequence=self.cycles,record=row))
                if row.get('elapsed_s',0)>240: self.reason=self.reason or 'cycle_exceeded_existing_240_second_limit'
                self.cycle_start=None
            if stage=='cycle.summary':
                emit('cycle-summaries.jsonl',dict(phase=phase,sequence=self.cycles,record=row))

    def check(self,facts,prior,now,wall):
        if facts['controls']!={'control_state':'FROZEN','macro_guard_operator_hold':'1'} or facts['health'].get('recovery_required') is not True or not facts['watchdog_off']:
            self.reason=self.reason or 'required_controls_changed'
        failures=blocking_conditions(facts['health'],prior)
        if failures: self.reason=self.reason or 'active_safety_failure:'+','.join(failures)
        if facts.get('other_failure'): self.reason=self.reason or facts['other_failure']
        if self.cycle_start is not None and now-self.cycle_start>240:
            self.reason=self.reason or 'inflight_cycle_exceeded_existing_240_second_limit'
        beat=facts.get('beat')
        genuine=bool(beat and self.instance and beat.get('instance_id')==self.instance and beat.get('started_at')==self.started_at)
        if genuine:
            completed=beat['context']['last_successful_cycle_at']
            if self.last_beat is not None and completed!=self.last_beat:
                self.gaps.append(completed-self.last_beat)
                if self.gaps[-1]>240: self.reason=self.reason or 'successful_heartbeat_gap_exceeded_240'
            self.last_beat=completed
            if max(wall-beat['timestamp'],wall-completed)>240:
                self.reason=self.reason or 'current_successful_heartbeat_stale'
        return self.boot and genuine and not self.reason


def observe(directory,backend):
    baseline=backend.preflight_record
    state=State(backend.pids['kernel']['pid']);tail=Tail()
    next_storage=next_threads=backend.monotonic()
    backend.storage('storage-observer-start')
    try:
        while True:
            state.ingest(tail.read(directory/'stages.jsonl'),directory)
            facts=backend.sample()
            if state.check(facts,baseline.get('prior_instance'),backend.monotonic(),backend.wall()) and 'dashboard' not in backend.pids:
                backend.start_dashboard()
                append(directory/'controller-events.jsonl',dict(event='dashboard_boot_gate_passed',at=backend.wall(),monotonic_s=backend.monotonic(),kernel_instance=state.instance))
            if (directory/'STOP').exists(): state.reason=state.reason or 'operator_stop'
            if (directory/'EVIDENCE_LIMIT').exists(): state.reason=state.reason or 'evidence_capacity_reached'
            if not all(backend.alive(role) for role in backend.pids): state.reason=state.reason or 'service_exited'
            now=backend.monotonic()
            if now>=next_storage:
                backend.storage('storage-during');next_storage=now+60
            if now>=next_threads:
                backend.resources();next_threads=now+15
            append(directory/'samples.jsonl',dict(at=backend.wall(),monotonic_s=now,controls=facts['controls'],
                recovery_required=facts['health']['recovery_required'],heartbeat=facts.get('beat'),
                active_conditions={k:{f:v.get(f) for f in ('status','reason','incident_id')} for k,v in facts['health']['conditions'].items() if v.get('status')=='ACTIVE'},stop_reason=state.reason))
            if state.reason: break
            if state.deadline is not None and now>=state.deadline:
                state.reason='bounded_hour_elapsed';break
            backend.sleep(1)
    except BaseException as exc:
        state.reason=state.reason or 'collector_failure:'+type(exc).__name__
        append(directory/'controller-events.jsonl',dict(event='collector_exception',error_class=type(exc).__name__,detail=str(exc)[:1024]))
    state.stop=backend.monotonic()
    append(directory/'controller-events.jsonl',dict(event='stop_requested',reason=state.reason,monotonic_s=state.stop))
    backend.signal_services()
    def drain_storage(name):
        try: backend.storage(name)
        except Exception as exc: append(directory/'controller-events.jsonl',dict(event='storage_measurement_failed',phase='SHUTDOWN',name=name,error_class=type(exc).__name__))
    drain_storage('storage-stop-request')
    until=backend.monotonic()+420
    next_storage=backend.monotonic()
    while any(backend.alive(role) for role in backend.pids) and backend.monotonic()<until:
        state.ingest(tail.read(directory/'stages.jsonl'),directory,'SHUTDOWN')
        if backend.monotonic()>=next_storage:
            drain_storage('storage-during-shutdown');next_storage=backend.monotonic()+60
        backend.sleep(1)
    state.ingest(tail.read(directory/'stages.jsonl'),directory,'SHUTDOWN')
    remaining=[role for role in backend.pids if backend.alive(role)]
    drain_storage('storage-stop-timeout' if remaining else 'storage-after-graceful-stop')
    result=dict(reason=state.reason,observation_status='OBSERVATION_NOT_STARTED' if state.first is None else 'STARTED',
        first_cycle_monotonic_s=state.first,deadline_monotonic_s=state.deadline,stop_monotonic_s=state.stop,
        duration_s=0 if state.first is None else max(0,state.stop-state.first),completed_window_cycles=state.completed_window,
        shutdown_cycles=state.shutdown_cycles,successful_heartbeat_gaps=state.gaps,still_running=remaining,
        exact_stop_reason='graceful_shutdown_timeout' if remaining else state.reason)
    append(directory/'controller-events.jsonl',dict(event='drain_finished',**result))
    (directory/'observation-result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def retain_until_stopped(directory,backend):
    """Continue actual host-side observations after a drain timeout; no repair/restart."""
    tail=Tail();next_storage=backend.monotonic()
    state=State(backend.pids['kernel']['pid']);recorded=set()
    for name in ('controller-events.jsonl','cycle-measurements.jsonl','cycle-summaries.jsonl'):
        path=directory/name
        if not path.exists(): continue
        for line in path.read_text().splitlines():
            value=json.loads(line)
            recorded.add((name,json.dumps(value.get('record',value),sort_keys=True)))
            if name=='controller-events.jsonl' and value.get('event')=='stop_requested':
                state.stop=value['monotonic_s']
    def ingest_remaining():
        # Reconstruct from the original BEGIN, deduplicate already retained records,
        # then keep the same incremental cursor through delayed process death.
        while True:
            offset=tail.offset
            rows=tail.read(directory/'stages.jsonl')
            state.ingest(rows,directory,'SHUTDOWN',recorded)
            if rows: append(directory/'controller-events.jsonl',dict(event='timeout_continuing',stage_lines=len(rows),at=backend.wall()))
            if tail.offset==offset: break
    while any(backend.alive(role) for role in backend.pids):
        ingest_remaining()
        if backend.monotonic()>=next_storage:
            try:
                backend.storage('storage-timeout-continuing');backend.resources()
            except Exception as exc:
                append(directory/'controller-events.jsonl',dict(event='timeout_measurement_failed',error_class=type(exc).__name__,at=backend.wall()))
            next_storage=backend.monotonic()+60
        backend.sleep(1)
    ingest_remaining()  # includes a final record flushed immediately before death
    (directory/'delayed-observation-result.json').write_text(json.dumps(dict(
        first_cycle_monotonic_s=state.first,deadline_monotonic_s=state.deadline,
        stop_monotonic_s=state.stop,completed_window_cycles=state.completed_window,
        shutdown_cycles=state.shutdown_cycles,cycle_count=state.cycles),indent=2)+'\n')
    backend.storage('storage-after-delayed-termination')
    append(directory/'controller-events.jsonl',dict(event='delayed_termination_confirmed',at=backend.wall()))
