"""Benign observer publication must not undo an otherwise SAFE owner activation."""
import copy,time
import pytest
from trader.observability.safety import SafetyHealth,publish,health_path
from trader.core.journal import Journal
from trader.core.types import ControlState
from trader.engine.state import ControlStateMachine
from tests.test_owner_recovery import HookVenue,OWNER
from tests.test_kernel_boot_recovery import _kernel,_protected


def seeded(path):
 h=SafetyHealth(path,sink=lambda _:None)
 h.observe('journal_read','TEST-ONLY old fault');h.observe('journal_read',None)
 v=h.read();now=time.time()
 v['heartbeats']={'luffy':dict(instance_id='TEST-ONLY',started_at=now-100,sequence=1,timestamp=now,
   context=dict(last_successful_cycle_at=now))}
 v['observed_at']=now;publish(path,v)
 return h


def advance(h):
 v=h.read();v['heartbeats']['luffy']['sequence']+=1;v['heartbeats']['luffy']['timestamp']+=.001
 v['revision']+=1;v['observed_at']+=.001;publish(h.path,v)


def test_owner_resume_heartbeat_during_venue_verification_does_not_recontain(tmp_path,monkeypatch):
 j=Journal(tmp_path/'j.db');v=HookVenue();_protected(j,v)
 ControlStateMachine(j).set(ControlState.FROZEN,'operator','TEST-ONLY owner hold')
 k,_,_=_kernel(j,v,monkeypatch);h=seeded(health_path(j))
 v.on_positions=lambda:advance(h)
 result=k.supervisor.request_owner_recovery(OWNER)
 assert result.status=='ACTIVATED'
 assert h.read()['recovery_required'] is False
 k.supervisor.cycle()
 assert j.kv_get('control_state')=='ACTIVE'


def test_only_benign_heartbeat_revision_can_clear(tmp_path):
 h=seeded(tmp_path/'health.json');before=h.read();advance(h)
 assert h.clear_after_supervisor(before['revision']) is False
 assert h.clear_after_supervisor(before['revision'],baseline=before) is True
 assert h.read()['revision']>before['revision']+1


@pytest.mark.parametrize('change',['active_fault','resolved_new_fault','producer_restart','restamp','clock_rewind','bootstrap_changed'])
def test_real_or_unproven_changes_keep_recovery_latched(tmp_path,change):
 h=seeded(tmp_path/'health.json');before=h.read();advance(h)
 if change in ('active_fault','resolved_new_fault'):
  h.observe('journal_write','TEST-ONLY newer real fault')
  if change=='resolved_new_fault':h.observe('journal_write',None)
 else:
  v=h.read()
  if change=='producer_restart':v['heartbeats']['luffy']['instance_id']='DIFFERENT'
  if change=='restamp':v['heartbeats']['luffy']['sequence']=before['heartbeats']['luffy']['sequence']
  if change=='clock_rewind':v['heartbeats']['luffy']['timestamp']=before['heartbeats']['luffy']['timestamp']-1
  if change=='bootstrap_changed':v['bootstrap']={'status':'PENDING','token':'different'}
  publish(h.path,v)
 assert h.clear_after_supervisor(before['revision'],baseline=before) is False
 assert h.read()['recovery_required'] is True
