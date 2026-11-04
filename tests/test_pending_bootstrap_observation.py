"""Only a proved, fenced managed child may occupy the brief pre-lock interval."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts import monitor as M
from trader.observability.safety import SafetyHealth, SafetyObserver, publish
from trader.observability import managed

TOKEN='a'*32
REV='b'*40
PID=5151
TICKS=777

@pytest.fixture
def pending(tmp_path,monkeypatch):
    root=tmp_path/'installation';root.mkdir();(root/'data').mkdir()
    (root/'data/watchdog.off').touch()
    directory=root/'data/bootstrap'/TOKEN;directory.mkdir(parents=True)
    manifest=directory/'observers.json'
    manifest.write_text(json.dumps(dict(status='COVERED',cleanup='OWNER_ONLY',root=str(root),revision=REV,token=TOKEN,units=['luffy-bootstrap-'+TOKEN+'-'+m for m in ('heartbeat-only','safety-only')])))
    manifest.chmod(0o600)
    fence=dict(schema='luffy-safety-health.v1',revision=0,recovery_required=True,conditions={},heartbeats={},bootstrap=dict(status='PENDING',token=TOKEN,intended_revision=REV))
    health=SafetyHealth(root/'data/safety_health.json');publish(health.path,fence)
    proc=tmp_path/'proc';p=proc/str(PID);p.mkdir(parents=True)
    argv=[str(root/'venv/bin/python'),'-B','-m','trader.kernel','--expect-revision',REV,'--bootstrap-token',TOKEN]
    (p/'cmdline').write_bytes(b'\0'.join(a.encode() for a in argv)+b'\0')
    (p/'stat').write_text(f'{PID} (python) S '+' '.join(['0']*18)+f' {TICKS}')
    group='/user.slice/app.slice/luffy-kernel-'+TOKEN+'.service'
    (p/'cgroup').write_text('0::'+group+'\n');(p/'cwd').symlink_to(root,target_is_directory=True)
    props=dict(ActiveState='active',MainPID=str(PID),ControlGroup=group)
    class Manager:
        def show(self,*args):return props
    manager=Manager()
    identity=dict(state='UNLOCKED_PROCESS',reasons=[],holder_pids=[],record=None,processes=[dict(pid=PID,start_ticks=TICKS,revision=REV,token=TOKEN,flags=['-B'])])
    monkeypatch.setattr(managed,'UserManager',lambda:pytest.fail('real manager forbidden'))
    return SimpleNamespace(root=root,health=health,proc=proc,p=p,manifest=manifest,identity=identity,manager=manager,props=props,fence=fence)

def test_verified_pending_child_is_recognized_without_filesystem_writes(pending):
    p=pending
    before={str(f):f.read_bytes() for f in p.root.rglob('*') if f.is_file()}
    assert M._pending_managed_kernel(p.root,p.identity,p.health,manager=p.manager,proc_root=p.proc)
    assert before=={str(f):f.read_bytes() for f in p.root.rglob('*') if f.is_file()}
    assert p.identity['state']=='UNLOCKED_PROCESS' and not p.identity.get('verified')

@pytest.mark.parametrize('variant',['ready','failed','no_latch','wrong_token','wrong_revision','multiple','holder','ambiguous','unknown','pid_reuse','wrong_main_pid','foreign_cgroup','wrong_argv','wrong_cwd','inactive_unit','stale_manifest','bad_manifest','writable_manifest','symlink_manifest','manager_unavailable','live_old_record'])
def test_unproved_or_stale_child_still_refuses(pending,variant):
    p=pending;i=copy.deepcopy(p.identity)
    if variant in ('ready','failed'):
        p.fence['bootstrap']['status']=variant.upper();publish(p.health.path,p.fence)
    elif variant=='no_latch':p.fence['recovery_required']=False;publish(p.health.path,p.fence)
    elif variant=='wrong_token':i['processes'][0]['token']='c'*32
    elif variant=='wrong_revision':i['processes'][0]['revision']='c'*40
    elif variant=='multiple':i['processes'].append(dict(i['processes'][0],pid=5152))
    elif variant=='holder':i['holder_pids']=[PID]
    elif variant=='ambiguous':i['reasons']=['process_table_ambiguous']
    elif variant=='unknown':i['state']='UNKNOWN'
    elif variant=='pid_reuse':i['processes'][0]['start_ticks']=TICKS+1
    elif variant=='wrong_main_pid':p.props['MainPID']='9999'
    elif variant=='foreign_cgroup':(p.p/'cgroup').write_text('0::/other.service\n')
    elif variant=='wrong_argv':(p.p/'cmdline').write_bytes(b'python\0-m\0other.module\0')
    elif variant=='wrong_cwd':(p.p/'cwd').unlink();(p.p/'cwd').symlink_to(p.root.parent,target_is_directory=True)
    elif variant=='inactive_unit':p.props['ActiveState']='inactive'
    elif variant=='stale_manifest':os.utime(p.manifest,(1,1))
    elif variant=='bad_manifest':p.manifest.write_text('{}')
    elif variant=='writable_manifest':p.manifest.chmod(0o666)
    elif variant=='symlink_manifest':
        target=p.manifest.with_name('target');p.manifest.rename(target);p.manifest.symlink_to(target)
    elif variant=='manager_unavailable':
        def unavailable(*a):raise managed.ManagedUnavailable('unavailable')
        p.manager.show=unavailable
    elif variant=='live_old_record':i['record']=dict(pid=PID,start_ticks=TICKS)
    assert not M._pending_managed_kernel(p.root,i,p.health,manager=p.manager,proc_root=p.proc)

def test_pending_child_does_not_hide_missing_heartbeat(pending,monkeypatch):
    p=pending;original=M._pending_managed_kernel;calls=[]
    monkeypatch.setattr(M,'_pending_managed_kernel',lambda root,identity,health:original(root,identity,health,manager=p.manager,proc_root=p.proc))
    monkeypatch.setattr(SafetyObserver,'heartbeat',lambda *a,**k:calls.append('heartbeat') or dict(status='STALE'))
    result=M.safety_check(p.root,{},heartbeat_only=True,identity_probe=lambda _:p.identity)
    assert calls==['heartbeat'] and result['components'][0]['status']=='STALE'
    assert result['health']['recovery_required'] is True
    assert result['health']['conditions'].get('paused_kernel_identity',{}).get('status')!='ACTIVE'
