from trader.engine.watchdog import Heartbeat
from trader.observability.safety import SafetyHealth, SafetyObserver, HeartbeatPolicy


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
