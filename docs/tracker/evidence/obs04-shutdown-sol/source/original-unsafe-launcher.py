"""Prepared single launch; requires separate authorization, never retries."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--execute-authorized', action='store_true')
    args = ap.parse_args()
    if not args.execute_authorized:
        ap.error('preparation only; later startup authorization required')
    production = Path('/mnt/luffy-data/luffy/production')
    here = Path(__file__).parent
    run = here/'run'
    python = str(production/'venv/bin/python')
    units = []
    processes = {}
    # Signed fresh checks immediately precede this one launch.
    subprocess.run([python,'-B',str(here/'pre_start.py'),'--execute-authorized'],cwd=production,check=True)
    subprocess.run([python,'-B',str(here/'storage.py'),str(run/'storage-before-startup.json')],check=True)
    try:
        for mode, seconds in [('safety-only',30),('heartbeat-only',10)]:
            unit = 'luffy-observation-' + here.name + '-' + mode
            command = ['systemd-run','--user','--unit='+unit,'--on-active=2s',
                '--on-unit-inactive='+str(seconds)+'s','--timer-property=AccuracySec=1s',
                '--property=WorkingDirectory='+str(production),'--property=Restart=no',
                '--property=SuccessExitStatus=1','--property=TimeoutStartSec=20s',
                python,'-B',str(here/'observer_once.py'),'--execute-authorized','--','--'+mode]
            subprocess.run(command,check=True)
            units.append(unit)
        requested = time.time()
        commands = {'kernel':[python,'-B',str(here/'profile_prepared.py'),'--execute-authorized','--output',str(run)],
                    'dashboard':[python,'-B','-m','trader.dashboard.server']}
        pids = dict(launch_requested_at=requested,head='9385f792f32277bb0b826dba83247b8f5ccf1f8a')
        for role, command in commands.items():
            with (run/(role+'.log')).open('ab',buffering=0) as log:
                process = subprocess.Popen(command,cwd=production,stdout=log,stderr=subprocess.STDOUT,
                    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},start_new_session=True)
            processes[role] = process
            ticks = Path('/proc',str(process.pid),'stat').read_text().rsplit(')',1)[1].split()[19]
            pids[role] = dict(pid=process.pid,start_ticks=ticks,command=command)
        (run/'pids.json').write_text(json.dumps(pids,indent=2))
        subprocess.run([python,'-B',str(here/'collect_prepared.py'),'--execute-authorized'],check=True)
    finally:
        # Redundant cleanup covers collector/pre-start measurement failures.
        for role in ('dashboard','kernel'):
            process = processes.get(role)
            if process is not None and process.poll() is None:
                process.send_signal(signal.SIGTERM)
        deadline = time.monotonic()+420
        for process in processes.values():
            try: process.wait(timeout=max(0.01,deadline-time.monotonic()))
            except subprocess.TimeoutExpired: print('STILL RUNNING: manual owner assessment required',process.pid,flush=True)
        stopped=[]
        for unit in units:
            stopped.append(subprocess.run(['systemctl','--user','stop',unit+'.timer',unit+'.service'],check=False).returncode==0)
        subprocess.run([python,'-B',str(here/'storage.py'),str(run/'storage-final-stopped.json')],check=False)
        (run/'launch-result.json').write_text(json.dumps(dict(exits={k:p.poll() for k,p in processes.items()},
            observers_stop_commands_succeeded=all(stopped),automatic_restart=False),indent=2))


if __name__ == '__main__':
    main()
