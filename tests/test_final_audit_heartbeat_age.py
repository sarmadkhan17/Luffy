from trader.engine.watchdog import Heartbeat
from trader.observability.safety import SafetyHealth, SafetyObserver, HeartbeatPolicy
import pytest
from trader.engine.watchdog import read_heartbeat


def test_fresh_publication_cannot_hide_stale_successful_cycles(tmp_path):
    now=[1.]
    beat=Heartbeat(path=tmp_path/'heartbeat.json',clock=lambda:now[0])
    beat.beat({'last_successful_cycle_at':1.})
    health=SafetyHealth(tmp_path/'health.json',clock=lambda:now[0],sink=lambda _:None)
    observer=SafetyObserver(health,clock=lambda:now[0])
    policy=HeartbeatPolicy(10,'injected-policy')
    assert observer.heartbeat(beat.path,'luffy',policy)['status']=='FRESH'
    now[0]=1000.
    for _ in range(3):
        beat.beat({'last_successful_cycle_at':1.})
        assert observer.heartbeat(beat.path,'luffy',policy)['status']=='STALE'
        assert health.entry_block()=='critical_safety_requires_recovery'
        now[0]+=1
    assert beat.age_seconds()>=999
    beat.beat({'last_successful_cycle_at':now[0]})
    assert observer.heartbeat(beat.path,'luffy',policy)['status']=='FRESH'
    assert health.entry_block()  # freshness alone cannot release recovery containment


def test_exact_age_cut_and_unconfigured_policy(tmp_path):
    now=[1.]
    beat=Heartbeat(path=tmp_path/'heartbeat.json',clock=lambda:now[0])
    now[0]=11.;beat.beat({'last_successful_cycle_at':1.})
    health=SafetyHealth(tmp_path/'health.json',clock=lambda:now[0],sink=lambda _:None)
    observer=SafetyObserver(health,clock=lambda:now[0])
    assert observer.heartbeat(beat.path,'luffy',HeartbeatPolicy(10))['status']=='FRESH'
    now[0]+=0.001;beat.beat({'last_successful_cycle_at':1.})
    assert observer.heartbeat(beat.path,'luffy',HeartbeatPolicy(10))['status']=='STALE'
    assert observer.heartbeat(beat.path,'luffy',HeartbeatPolicy(None))['status']=='POLICY_NOT_CONFIGURED'


def test_liveness_pulse_cannot_invent_or_advance_successful_cycle_evidence(tmp_path):
    now = [1.]
    beat = Heartbeat(path=tmp_path / 'heartbeat.json', clock=lambda: now[0])
    beat.beat(dict(startup_state='STARTED_FROZEN', last_successful_cycle_at=None,
                   state='FROZEN', boot_complete=True, stopping=False))
    now[0] = 400.
    beat.pulse(state='FROZEN', boot_complete=True, stopping=False)
    assert read_heartbeat(beat.path, 'luffy', now[0], require_cycle=False)['context']['last_successful_cycle_at'] is None
    with pytest.raises(ValueError, match='cycle_context'):
        read_heartbeat(beat.path, 'luffy', now[0])
    beat.beat(dict(startup_state='CYCLE_READY', last_successful_cycle_at=400.,
                   state='FROZEN', boot_complete=True, stopping=False, decisions=3))
    now[0] = 800.
    beat.pulse(state='FROZEN', boot_complete=True, stopping=False)
    record = read_heartbeat(beat.path, 'luffy', now[0])
    assert record['timestamp'] == 800 and record['context']['last_successful_cycle_at'] == 400
    assert record['context']['decisions'] == 3 and beat.age_seconds() == 400
