"""One authorized launch using the corrected prepared execution contract; never retries."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time
from metadata import append
import collect_prepared as collector

P=Path('/mnt/luffy-data/luffy/production')
HERE=Path(__file__).parent
sys.path.insert(0, str(P))
from trader.observability.shutdown import service_state


class Host:
    def __init__(self):
        self.directory=HERE/'run';self.python=str(P/'venv/bin/python')
        self.units=[];self.processes={};self.pids={};self.preflight_record={}
        self.launch_at=None;self.next_identity_check=0
    monotonic=staticmethod(time.monotonic)
    wall=staticmethod(time.time)
    sleep=staticmethod(time.sleep)

    def preflight(self):
        result=subprocess.run([self.python,'-B',str(HERE/'pre_start.py'),'--execute-authorized'],cwd=P,capture_output=True,text=True)
        self.preflight_record=json.loads((self.directory/'preflight.json').read_text())
        if result.returncode or self.preflight_record.get('allow') is not True:
            return {**self.preflight_record,'allow':False}
        return self.preflight_record

    def storage(self,name): collector.storage(self.directory,name)

    def timers_start(self):
        for mode,seconds in [('safety-only',30),('heartbeat-only',10)]:
            unit='luffy-observation-'+HERE.name+'-'+mode
            subprocess.run(['systemd-run','--user','--unit='+unit,'--on-active=2s',
                '--on-unit-inactive='+str(seconds)+'s','--timer-property=AccuracySec=1s',
                '--property=WorkingDirectory='+str(P),'--property=Restart=no',
                '--property=SuccessExitStatus=1','--property=TimeoutStartSec=20s',
                self.python,'-B',str(HERE/'observer_once.py'),'--execute-authorized','--','--'+mode],check=True)
            self.units.append(unit)

    def start(self,role):
        command=([self.python,'-B',str(HERE/'profile_prepared.py'),'--execute-authorized','--output',str(self.directory)]
                 if role=='kernel' else [self.python,'-B','-m','trader.dashboard.server'])
        requested=self.wall();mono=self.monotonic()
        with (self.directory/(role+'.log')).open('ab',buffering=0) as log:
            process=subprocess.Popen(command,cwd=P,stdout=log,stderr=subprocess.STDOUT,
                env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},start_new_session=True)
        self.processes[role]=process
        ticks=Path('/proc',str(process.pid),'stat').read_text().rsplit(')',1)[1].split()[19]
        self.pids[role]=dict(pid=process.pid,start_ticks=ticks,command=command,launch_requested_at=requested,launch_monotonic_s=mono)
        if role=='kernel': self.launch_at=requested
        (self.directory/'pids.json').write_text(json.dumps(dict(launch_requested_at=self.launch_at,**self.pids),indent=2)+'\n')
        append(self.directory/'controller-events.jsonl',dict(event=role+'_launched',at=requested,monotonic_s=mono,pid=process.pid))

    def start_dashboard(self): self.start('dashboard')
    def alive(self,role):
        # UNKNOWN must keep the drain/retention loops and observers alive.
        return self.shutdown_state(role) != 'TERMINATED'

    def shutdown_state(self,role):
        return service_state(self.processes[role], self.pids[role])

    def sample(self):
        sys.path.insert(0,str(P))
        from trader.engine.watchdog import read_heartbeat
        health=json.loads((P/'data/safety_health.json').read_text())
        beat=read_heartbeat(P/'data/heartbeat_luffy.json','luffy',self.wall())
        with sqlite3.connect((P/'data/luffy.db').as_uri()+'?mode=ro',uri=True,timeout=1) as db:
            controls=dict(db.execute("SELECT key,value FROM state_kv WHERE key IN ('control_state','macro_guard_operator_hold')"))
        failure=None
        if hashlib.sha256((P/'data/brain_usage.json').read_bytes()).hexdigest()!=self.preflight_record['provider_ledger_sha']: failure='provider_ledger_changed'
        if self.monotonic()>=self.next_identity_check:
            if hashlib.sha256((P/'config.yaml').read_bytes()).hexdigest()!=self.preflight_record['config_sha256']: failure='configuration_changed'
            if subprocess.check_output(['git','rev-parse','HEAD'],cwd=P,text=True).strip()!=self.preflight_record['head']: failure='source_head_changed'
            changes=subprocess.check_output(['git','--no-optional-locks','diff','--name-only'],cwd=P,text=True).splitlines()
            if any(not name.startswith('graphify-out/') for name in changes): failure='source_worktree_changed'
            self.next_identity_check=self.monotonic()+60
        return dict(health=health,beat=beat,controls=controls,watchdog_off=(P/'data/watchdog.off').exists(),other_failure=failure)

    def resources(self):
        threads=[]
        for task in list(Path('/proc',str(self.pids['kernel']['pid']),'task').glob('*'))[:64]:
            try:
                fields=(task/'stat').read_text().rsplit(')',1)[1].split()
                threads.append(dict(tid=int(task.name),name=(task/'comm').read_text().strip(),cpu_ticks=int(fields[11])+int(fields[12]),schedstat=(task/'schedstat').read_text().strip()))
            except OSError: pass
        append(self.directory/'background-resources.jsonl',dict(at=self.wall(),threads=threads,basis='proc cumulative CPU/scheduler counters, not database lock attribution'))

    def signal_services(self):
        for role in ('dashboard','kernel'):
            if role in self.pids and self.shutdown_state(role) == 'LIVE':
                self.processes[role].send_signal(signal.SIGTERM)

    def timers_stop(self):
        if any(self.alive(role) for role in self.pids):
            raise RuntimeError('shutdown_unconfirmed_observers_retained')
        for unit in self.units:
            # systemd may garbage-collect a completed transient oneshot already.
            subprocess.run(['systemctl','--user','stop',unit+'.timer'],check=True)
            state=subprocess.run(['systemctl','--user','show',unit+'.service','--property=LoadState','--value'],capture_output=True,text=True)
            if state.stdout.strip()!='not-found':
                subprocess.run(['systemctl','--user','stop',unit+'.service'],check=True)

    def final_account(self):
        result=subprocess.run([self.python,'-B',str(HERE/'fresh_protection.py'),'--execute-authorized','final-stopped'],cwd=P,timeout=120,capture_output=True,text=True)
        append(self.directory/'controller-events.jsonl',dict(event='final_read_only_account_check',exit_code=result.returncode,at=self.wall()))
        return result.returncode


def execute(host):
    """Same orchestrator exercised end-to-end by fake host fixtures."""
    result=dict(exact_stop_reason='not_started',still_running=[])
    try:
        preflight=host.preflight()
        if preflight.get('allow') is not True:
            result=dict(exact_stop_reason='preflight_refused',refusal_reasons=preflight.get('reasons'),observation_status='OBSERVATION_NOT_STARTED',still_running=[])
            return result
        host.storage('storage-before-startup')
        host.timers_start()
        host.start('kernel')
        result=collector.observe(host.directory,host)
    except BaseException as exc:
        result=dict(exact_stop_reason='launcher_failure:'+type(exc).__name__,detail=str(exc)[:1024],still_running=[r for r in host.pids if host.alive(r)])
        host.signal_services()
    finally:
        remaining=[role for role in host.pids if host.alive(role)]
        if remaining:
            result['still_running']=remaining
            result['remaining_process_identities']={r:host.pids[r] for r in remaining}
            result['shutdown_states']={r:host.shutdown_state(r) for r in remaining}
            result['observers_retained']=True
            (host.directory/'launch-result.json').write_text(json.dumps(result,indent=2)+'\n')
            # Existing collector remains host-side and alive, timers are NOT cleaned up.
            collector.retain_until_stopped(host.directory,host)
            remaining=[role for role in host.pids if host.alive(role)]
        if not remaining:
            if host.pids:
                host.storage('storage-final-stopped')
                try: result['final_account_exit_code']=host.final_account()
                except Exception as exc: result['final_account_failure']=type(exc).__name__
            host.timers_stop()
            result['observers_stopped']=True
        result['final_still_running']=remaining
        result['final_shutdown_states']={r:host.shutdown_state(r) for r in host.pids}
        (host.directory/'launch-result.json').write_text(json.dumps(result,indent=2)+'\n')
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--execute-authorized',action='store_true');args=ap.parse_args()
    if not args.execute_authorized: ap.error('execution authorization required')
    host=Host();host.directory.mkdir(exist_ok=True)
    # Never reuse a launched run directory or start twice.
    if (host.directory/'pids.json').exists(): raise SystemExit('one attempt already launched; no retry authorized')
    print(json.dumps(execute(host)),flush=True)


if __name__=='__main__': main()
