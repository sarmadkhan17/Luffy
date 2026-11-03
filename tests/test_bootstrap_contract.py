"""OBS-05: temporary local stores/lock holders and inert launch seams only."""
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import sqlite3
from types import SimpleNamespace

import pytest

from trader import runtime_identity as ri
from trader.observability import bootstrap as B, preflight as P
from trader.observability.safety import SafetyHealth, SafetyObserver, HeartbeatPolicy
from tests.test_launch_preflight import local, NOW, update_health, update_snapshot
from tests.test_dashboard_readiness import ready, mutate, CFG, REV


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *a: pytest.fail('network forbidden'))
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('network forbidden'))


def phase_a(local, monkeypatch):
    monkeypatch.setattr(ri, 'code_revision', lambda *a: dict(revision=REV, dirty_code=False))
    monkeypatch.setattr(ri, 'legacy_kernel_pids', lambda *a, **k: [])
    return P.prelaunch(local[0], REV, clock=lambda: NOW)


def test_original_cold_deadlock_is_reproduced_then_contained(local, monkeypatch):
    local[1]['conditions'] = {name: dict(status='ACTIVE', reason='MISSING')
                             for name in ['heartbeat:luffy', 'observed_cycle:luffy']}
    update_health(local)
    old = datetime.fromtimestamp(NOW - 86400, timezone.utc).isoformat()
    local[2].update(checked_at=old, completed_at=old); update_snapshot(local)
    health_before = (local[0] / 'data/safety_health.json').read_bytes()
    assert not P.evaluate(P.collect_facts(local[0], clock=lambda: NOW))['allow']
    result = phase_a(local, monkeypatch)
    assert result['allow'] and not result['trading_authority']
    assert result['facts']['protection']['freshness'] == 'STALE'
    assert result['facts']['protection']['age_seconds'] == 86400
    assert result['facts']['protection']['evidence_status'] == 'VERIFIED'
    assert not (local[0] / 'data/heartbeat_luffy.json').exists()
    assert (local[0] / 'data/safety_health.json').read_bytes() == health_before


@pytest.mark.parametrize('bad', ['health_missing', 'health_unreadable', 'health_unknown',
    'storage_missing', 'storage_unreadable', 'static_active', 'protection_missing',
    'protection_partial', 'book_changed', 'hold_missing', 'active_control', 'latch_cleared',
    'watchdog_missing', 'config_missing', 'pending_entry', 'environment_invalid'])
def test_phase_a_static_required_facts_remain_fail_closed(local, monkeypatch, bad):
    root = local[0]
    if bad == 'health_missing': (root / 'data/safety_health.json').unlink()
    if bad == 'health_unreadable': (root / 'data/safety_health.json').write_text('{')
    if bad == 'health_unknown':
        local[1]['conditions']['journal_write'] = dict(status='UNKNOWN'); update_health(local)
    if bad == 'static_active':
        local[1]['conditions']['protection'] = dict(status='ACTIVE'); update_health(local)
    if bad == 'latch_cleared':
        local[1]['recovery_required'] = False; update_health(local)
    if bad == 'storage_missing': (root / 'data/luffy.db').unlink()
    if bad == 'storage_unreadable': (root / 'data/luffy.db').write_text('broken')
    if bad == 'watchdog_missing': (root / 'data/watchdog.off').unlink()
    if bad == 'config_missing':
        from trader.core import config
        monkeypatch.setattr(config, 'load_config', lambda *a: {})
    if bad == 'environment_invalid': monkeypatch.setattr(P, '_filesystem', lambda *a: (_ for _ in ()).throw(OSError()))
    if bad == 'protection_missing':
        # first-boot read needs credentials; none configured here, so it must refuse before any network
        from trader.core import config
        class NoKeys:
            def get(self, key, default=''): return 'true' if key == 'BINANCE_DEMO' else default
            def binance_keys(self): return ('', '')
        monkeypatch.setattr(config, 'Env', NoKeys())
    if bad in ['protection_missing', 'protection_partial', 'book_changed', 'hold_missing', 'active_control', 'pending_entry']:
        with sqlite3.connect(root / 'data/luffy.db') as db:
            if bad == 'protection_missing': db.execute('DELETE FROM protection_evidence')
            if bad == 'protection_partial':
                local[2]['status'] = 'PARTIAL'; db.execute('UPDATE protection_evidence SET value=?', (json.dumps(local[2]),))
            if bad == 'book_changed': db.execute("INSERT INTO trades VALUES ('t','SOL/USDT','long',1,10,'s','open')")
            if bad == 'hold_missing': db.execute("DELETE FROM state_kv WHERE key='macro_guard_operator_hold'")
            if bad == 'active_control': db.execute("UPDATE state_kv SET value='ACTIVE' WHERE key='control_state'")
            if bad == 'pending_entry': db.execute("INSERT INTO execution_requests VALUES ('SUBMITTED')")
    assert not phase_a(local, monkeypatch)['allow']


@pytest.mark.parametrize('revision', [None, '', 'abc', 'b'*40])
def test_phase_a_requires_exact_full_revision(local, monkeypatch, revision):
    phase_a(local, monkeypatch)
    assert not P.prelaunch(local[0], revision, clock=lambda: NOW)['allow']


def test_dirty_revision_and_conflicting_kernel_refuse(local, monkeypatch):
    phase_a(local, monkeypatch)
    monkeypatch.setattr(ri, 'code_revision', lambda *a: dict(revision=REV, dirty_code=True))
    assert not P.prelaunch(local[0], REV, clock=lambda: NOW)['allow']
    monkeypatch.setattr(ri, 'code_revision', lambda *a: dict(revision=REV, dirty_code=False))
    instance = ri.KernelInstance(local[0]); instance.acquire()
    try:
        assert not P.prelaunch(local[0], REV, clock=lambda: NOW)['allow']
    finally: instance.release()


@pytest.mark.parametrize('status', ['PENDING', 'FAILED', 'UNKNOWN', None])
def test_bootstrap_fence_blocks_entry_and_recovery_even_without_active_health(local, status):
    root = local[0]; health = SafetyHealth(root / 'data/safety_health.json')
    B.record_fence(root, 'attempt', 'PENDING', REV)
    body = health.read(); body['bootstrap']['status'] = status
    (root / 'data/safety_health.json').write_text(json.dumps(body))
    assert health.entry_block() == 'contained_bootstrap_not_ready'
    assert 'contained_bootstrap_not_ready' in health.active_reasons()
    assert not health.clear_after_supervisor(body['revision'])
    # The final real Executor reservation refuses before any Risk/order seam.
    from trader.engine.entry_authority import submit
    executor = SimpleNamespace(journal=SimpleNamespace(db_path=root / 'data/luffy.db'))
    with pytest.raises(ValueError, match='contained_bootstrap_not_ready'):
        submit(executor, None, None, 0, 0, 0, 0, None, None, None, None)
    assert health.read()['recovery_required'] is True


def test_ready_fence_preserves_recovery_latch_and_hold(local):
    root=local[0]; B.record_fence(root, 'attempt', 'PENDING', REV)
    B.record_fence(root, 'attempt', 'READY', REV)
    health = SafetyHealth(root / 'data/safety_health.json')
    assert health.entry_block() == 'critical_safety_requires_recovery'
    with sqlite3.connect(root / 'data/luffy.db') as db:
        assert dict(db.execute('SELECT key,value FROM state_kv'))['control_state'] == 'FROZEN'
        assert dict(db.execute('SELECT key,value FROM state_kv'))['macro_guard_operator_hold'] == '1'


def protection(ready):
    at = datetime.fromtimestamp(ready.now - .1, timezone.utc).isoformat()
    snap = dict(schema=2, generation=dict(boot=2, seq=1), status='VERIFIED', checked_at=at,
                completed_at=at, control_state_observed='FROZEN', complete_listing=True, mutations=0,
                checks=dict.fromkeys(P.CHECKS, True), cleanliness=dict(status='CLEAN', items=[],
                items_truncated=False, unread_symbols=[]), position_count=0, symbols=[], reasons=[])
    with sqlite3.connect(ready.root / 'data/luffy.db') as db:
        db.executescript('CREATE TABLE trades(id,symbol,side,amount,stop_loss,sl_order_id,status);'
                         'CREATE TABLE protection_evidence(slot,boot,seq,value);')
        db.execute('INSERT INTO protection_evidence VALUES (1,2,1,?)',(json.dumps(snap),))
    return snap


def phase_b(ready):
    return B.readiness(ready.root, CFG, ready.instance.record, dict(generation=dict(boot=1, seq=1)),
                       clock=lambda: ready.now)


def test_current_boot_can_be_contained_ready_with_fresh_protection(ready):
    protection(ready); result=phase_b(ready)
    assert result['allow'] and not result['trading_authority'], result['reasons']
    assert ready.health.read()['recovery_required'] is True


@pytest.mark.parametrize('bad', ['prior_instance', 'stale', 'stale_work', 'blocking_health',
    'health_prior_instance', 'health_missing_receipt', 'current_control_active', 'hold_missing',
    'latch_cleared', 'boot_incomplete', 'stopping'])
def test_phase_b_existing_instance_health_controls_still_required(ready, bad):
    protection(ready); mutate(ready, bad)
    assert not phase_b(ready)['allow']


@pytest.mark.parametrize('bad', ['missing', 'stale', 'prior_boot', 'before_launch', 'reconciliation',
    'protection', 'malformed', 'pending_orders'])
def test_phase_b_fresh_protection_and_reconciliation_are_required(ready, bad):
    snap=protection(ready)
    if bad == 'stale': snap['checked_at']=datetime.fromtimestamp(ready.now-121,timezone.utc).isoformat()
    if bad == 'before_launch': snap['checked_at']=datetime.fromtimestamp(ready.instance.record['started_at']-1,timezone.utc).isoformat()
    if bad == 'prior_boot': snap['generation']['boot']=1
    if bad in ('reconciliation', 'protection'): snap['checks']['reconciliation' if bad == 'reconciliation' else 'venue_protection']=False
    if bad == 'pending_orders': snap['cleanliness']['items']=[{'class':'entry_order'}]
    with sqlite3.connect(ready.root / 'data/luffy.db') as db:
        db.execute('UPDATE protection_evidence SET boot=?, value=?',(snap['generation']['boot'], '{' if bad=='malformed' else json.dumps(snap)))
        if bad == 'missing': db.execute('DELETE FROM protection_evidence')
    assert not phase_b(ready)['allow']


def initialized_without_cycle(ready):
    protection(ready)
    ready.health.clock = lambda: ready.now
    with sqlite3.connect(ready.root / 'data/luffy.db') as db:
        db.execute('CREATE TABLE execution_requests(state TEXT)')
    ready.beat.beat(dict(startup_state='STARTED_FROZEN', last_successful_cycle_at=None,
                         state='FROZEN', boot_complete=True, stopping=False))
    ready.health.observe('observed_cycle:luffy', 'CYCLE_PENDING')
    SafetyObserver(ready.health, clock=lambda: ready.now).heartbeat(
        ready.beat.path, 'luffy', HeartbeatPolicy.configured(CFG))


def started_check(ready):
    return B.readiness(ready.root, dict(CFG, mode={'execution': 'paper'}),
                       ready.instance.record, dict(generation=dict(boot=1, seq=1)),
                       clock=lambda: ready.now, require_cycle=False)


def refresh_protection(ready, age=.1):
    # isoformat() rounds to the nearest microsecond, which can land a fraction of a microsecond
    # AFTER the mocked clock and be refused as 'future'. Stamp a deterministic, strictly earlier instant.
    with sqlite3.connect(ready.root / 'data/luffy.db') as db:
        value = json.loads(db.execute('SELECT value FROM protection_evidence').fetchone()[0])
        value['checked_at'] = value['completed_at'] = datetime.fromtimestamp(
            ready.now - age, timezone.utc).isoformat()
        db.execute('UPDATE protection_evidence SET value=?', (json.dumps(value),))


@pytest.mark.parametrize('age,accepted', [(.1, True), (119., True), (-60., False), (121., False), (3600., False)])
def test_protection_timestamp_current_accepted_future_and_stale_refused(ready, age, accepted):
    # The production rule (P._protection) is exercised unchanged; only the fixture stamp varies.
    initialized_without_cycle(ready)
    refresh_protection(ready, age)
    with sqlite3.connect((ready.root / 'data/luffy.db').as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        if accepted:
            assert P._protection(db, ready.now)['checked_at']
        else:
            with pytest.raises(ValueError, match='protection_stale_or_future'):
                P._protection(db, ready.now)


def test_real_startup_and_cycle_receipts_stay_distinct_through_a_slow_first_cycle(ready):
    initialized_without_cycle(ready)
    B.record_fence(ready.root, 'attempt', 'PENDING', REV)
    B.record_fence(ready.root, 'attempt', 'STARTED_FROZEN', REV)
    result = started_check(ready)
    assert result['allow'], result['reasons']
    assert not phase_b(ready)['allow']
    ready.now += 301
    ready.beat.clock = lambda: ready.now
    ready.beat.pulse(state='FROZEN', boot_complete=True, stopping=False)
    SafetyObserver(ready.health, clock=lambda: ready.now).heartbeat(
        ready.beat.path, 'luffy', HeartbeatPolicy.configured(CFG))
    refresh_protection(ready)
    result = started_check(ready)
    assert result['allow'], result['reasons']
    assert not phase_b(ready)['allow']
    assert json.loads(ready.beat.path.read_text())['context']['last_successful_cycle_at'] is None
    assert ready.health.entry_block() == 'contained_bootstrap_not_ready'
    assert ready.health.read()['recovery_required'] is True
    ready.beat.beat(dict(startup_state='CYCLE_READY', last_successful_cycle_at=ready.now,
                         state='FROZEN', boot_complete=True, stopping=False))
    SafetyObserver(ready.health, clock=lambda: ready.now).heartbeat(
        ready.beat.path, 'luffy', HeartbeatPolicy.configured(CFG))
    assert phase_b(ready)['allow']
    assert ready.health.read()['recovery_required'] is True


@pytest.mark.parametrize('failure', ['failed_initialization', 'missing_protection', 'stale_heartbeat',
                                   'process_death', 'reconciliation', 'unfrozen', 'journal_fault'])
def test_initialized_frozen_acceptance_does_not_hide_required_failures(ready, failure):
    initialized_without_cycle(ready)
    if failure == 'failed_initialization':
        ready.beat.beat(dict(startup_state='STARTED_FROZEN', last_successful_cycle_at=None,
                             state='FROZEN', boot_complete=False, stopping=False))
    if failure == 'missing_protection':
        with sqlite3.connect(ready.root / 'data/luffy.db') as db:
            db.execute('DELETE FROM protection_evidence')
    if failure == 'stale_heartbeat':
        ready.now += 241
        refresh_protection(ready)
    if failure == 'process_death': ready.instance.release()
    if failure == 'unfrozen': mutate(ready, 'current_control_active')
    if failure == 'journal_fault': ready.health.observe('journal_write', 'FAILED')
    if failure == 'reconciliation':
        with sqlite3.connect(ready.root / 'data/luffy.db') as db:
            value = json.loads(db.execute('SELECT value FROM protection_evidence').fetchone()[0])
            value['checks']['reconciliation'] = False
            db.execute('UPDATE protection_evidence SET value=?', (json.dumps(value),))
    assert not started_check(ready)['allow']


def test_independent_observer_resolves_cold_cycle_condition_only_for_current_boot(ready):
    ready.health.observe('observed_cycle:luffy', 'MISSING')
    original = ready.beat.path.read_text()
    beat = json.loads(original); beat['instance_id']='prior'; ready.beat.path.write_text(json.dumps(beat))
    obs=SafetyObserver(ready.health,clock=lambda: ready.now)
    obs.heartbeat(ready.beat.path,'luffy',HeartbeatPolicy.configured(CFG))
    assert ready.health.read()['conditions']['observed_cycle:luffy']['status']=='ACTIVE'
    ready.beat.path.write_text(original)
    obs.heartbeat(ready.beat.path,'luffy',HeartbeatPolicy.configured(CFG))
    obs.heartbeat(ready.beat.path,'luffy',HeartbeatPolicy.configured(CFG))
    assert ready.health.read()['conditions']['observed_cycle:luffy']['status']=='RESOLVED'
    assert ready.health.read()['recovery_required'] is True


class InertHost:
    def __init__(self, failure=None):
        self.events=[]; self.now=0.; self.failure=failure; self.launches=0
    def monotonic(self): return self.now
    def sleep(self,n): self.now+=n
    def prelaunch(self):
        return dict(allow=self.failure!='prelaunch', facts={'protection':{'generation':{'boot':1}}})
    def fence(self,status): self.events.append(status)
    def ready(self,proof): self.events.append('READY')
    def started_frozen(self,proof): self.events.append('STARTED_FROZEN')
    def observers_start(self):
        self.events.append('observers')
        if self.failure=='observers': raise RuntimeError()
    def start(self):
        self.launches+=1; self.events.append('launch')
        if self.failure=='start': raise RuntimeError()
    def sample(self,baseline):
        if self.failure=='sample': raise RuntimeError()
        if self.failure=='late': self.now+=5
        return dict(allow=self.failure is None or self.failure=='late', cycle_ready=True,
                    cycle_readiness={})
    def contain_stop(self):
        self.events.append('SIGTERM'); return dict(stop='UNKNOWN',observers_retained=True)
    def save(self,result): self.result=result


@pytest.mark.parametrize('failure', ['prelaunch','observers','start','sample','timeout','late'])
def test_failure_retains_observers_never_retries_or_awards_readiness(failure):
    host=InertHost(failure); result=B.execute(host,timeout=2)
    assert not result['allow'] and not result['trading_authority']
    assert host.launches<=1 and 'READY' not in host.events
    if failure!='prelaunch':
        assert host.events[:2]==['PENDING','observers'] and 'FAILED' in host.events
        assert result['observers_retained']
    if host.launches: assert result['containment']['observers_retained']


def test_success_keeps_containment_no_active_transition():
    host=InertHost(); result=B.execute(host,timeout=2)
    assert result['allow'] and not result['trading_authority'] and host.launches==1
    assert host.events==['PENDING','observers','launch','STARTED_FROZEN','READY']


def test_slow_first_cycle_can_finish_after_startup_deadline_without_a_second_launch():
    class SlowHost(InertHost):
        def sample(self, baseline):
            return dict(allow=True, cycle_ready=self.now >= 300, cycle_readiness={})
        def started_frozen(self, proof):
            super().started_frozen(proof)
            self.initialized_at = self.now
    host = SlowHost(); result = B.execute(host, timeout=240)
    assert result['allow'] and result['phase'] == 'CYCLE_READY'
    assert host.initialized_at < 240 and host.now >= 300
    assert host.launches == 1 and 'SIGTERM' not in host.events
    assert not result['trading_authority']


@pytest.mark.parametrize('failure', ['stale_heartbeat', 'missing_protection', 'process_death'])
def test_started_kernel_is_stopped_for_real_health_failure_while_cycle_pending(failure):
    class FailedHost(InertHost):
        def sample(self, baseline):
            if 'STARTED_FROZEN' in self.events:
                return dict(allow=False, reasons=[failure], fatal=failure == 'process_death')
            return dict(allow=True, cycle_ready=False)
    host = FailedHost(); result = B.execute(host, timeout=240)
    assert not result['allow'] and host.launches == 1
    assert 'FAILED' in host.events and 'SIGTERM' in host.events and 'READY' not in host.events
    assert result['reason'] == 'contained_startup_health_failed'


def test_owned_child_unknown_identity_never_signalled_or_cleaned(tmp_path, monkeypatch):
    host=B.Host(tmp_path,REV)
    host.process=SimpleNamespace(pid=77,poll=lambda:None,
                                 send_signal=lambda sig:pytest.fail('uncertain signal'))
    host.expected=dict(pid=77,start_ticks='wrong')
    ticks=iter([0,301]); host.monotonic=lambda:next(ticks)
    monkeypatch.setattr(B,'service_state',lambda *a:'UNKNOWN')
    result=host.contain_stop()
    assert result['stop']=='UNKNOWN' and result['observers_retained']


def test_ready_publication_cannot_accept_changed_health(local):
    root=local[0]; B.record_fence(root,'attempt','PENDING',REV)
    health=SafetyHealth(root/'data/safety_health.json'); before=health.read()
    health.observe('journal_write','FAILED')
    with pytest.raises(ValueError,match='health_changed'):
        B.record_fence(root,'attempt','READY',REV,expected_health=before)
    assert health.bootstrap_block(health.read())


@pytest.mark.parametrize('change', ['observer', 'fault', 'recovery', 'bootstrap'])
def test_ready_publication_revalidates_progress_but_refuses_changed_containment(ready, monkeypatch, change):
    from trader.observability import dashboard_readiness as gate
    ready.health.clock = lambda: ready.now
    B.record_fence(ready.root, 'attempt', 'PENDING', REV)
    before = ready.health.read()
    ready.now += 1
    SafetyObserver(ready.health, clock=lambda: ready.now).heartbeat(
        ready.beat.path, 'luffy', HeartbeatPolicy.configured(CFG))
    if change == 'fault': ready.health.observe('journal_write', 'FAILED')
    if change in ('recovery', 'bootstrap'):
        value = ready.health.read()
        if change == 'recovery': value['recovery_required'] = False
        else: value['bootstrap']['token'] = 'other'
        B.publish(ready.health.path, value)
    original = gate.check
    monkeypatch.setattr(gate, 'check', lambda *a, **kw: original(*a, **kw, clock=lambda: ready.now))
    if change == 'observer':
        B.record_fence(ready.root, 'attempt', 'READY', REV, expected_health=before)
        assert ready.health.read()['bootstrap']['status'] == 'READY'
        assert ready.health.read()['recovery_required'] is True
    else:
        with pytest.raises(ValueError, match='health_changed'):
            B.record_fence(ready.root, 'attempt', 'READY', REV, expected_health=before)


def test_latest_protection_failure_is_not_hidden_by_benign_observer_progress(ready, monkeypatch):
    from trader.observability import dashboard_readiness as gate
    initialized_without_cycle(ready)
    original = gate._health
    reads = []
    def concurrent_read(path):
        if reads == [1]:
            with sqlite3.connect(ready.root / 'data/luffy.db') as db:
                value = json.loads(db.execute('SELECT value FROM protection_evidence').fetchone()[0])
                value['checks']['venue_protection'] = False
                db.execute('UPDATE protection_evidence SET value=?', (json.dumps(value),))
        reads.append(1)
        return original(path)
    monkeypatch.setattr(gate, '_health', concurrent_read)
    assert not started_check(ready)['allow']


def test_child_admission_requires_persisted_attempt_and_rechecks_static_facts(local, monkeypatch):
    root=local[0]; phase_a(local,monkeypatch)
    original=P.prelaunch
    monkeypatch.setattr(P,'prelaunch',lambda root, revision: original(root,revision,clock=lambda:NOW))
    with pytest.raises(ValueError,match='fence_missing'):
        B.child_admission(root,'attempt',REV)
    B.record_fence(root,'attempt','PENDING',REV)
    B.child_admission(root,'attempt',REV)
    with pytest.raises(ValueError,match='fence_missing'):
        B.child_admission(root,'wrong-attempt',REV)
    (root/'data/watchdog.off').unlink()
    with pytest.raises(ValueError,match='prelaunch_refused'):
        B.child_admission(root,'attempt',REV)


@pytest.mark.parametrize('contained_cold', [True, False])
def test_observer_refresh_during_preflight_only_defers_cold_producer_conditions(
        local, monkeypatch, contained_cold):
    root = local[0]
    local[1]['conditions']['observed_cycle:luffy'] = dict(status='RESOLVED', reason='STALE')
    update_health(local)
    original = P._probe
    def concurrent_observer(*args):
        result = original(*args)
        observer = SafetyObserver(SafetyHealth(root / 'data/safety_health.json',
                                               clock=lambda: NOW), clock=lambda: NOW)
        observer.heartbeat(root / 'data/heartbeat_luffy.json', 'luffy', HeartbeatPolicy(960))
        return result
    monkeypatch.setattr(P, '_probe', concurrent_observer)
    result = P.evaluate(P.collect_facts(root, clock=lambda: NOW, contained_cold=contained_cold))
    assert result['allow'] is contained_cold
    if contained_cold:
        assert set(result['facts']['safety']['deferred_to_contained_readiness']) == {
            'heartbeat:luffy', 'observed_cycle:luffy'}
        assert SafetyHealth(root / 'data/safety_health.json').entry_block()
    else:
        assert 'safety_health_changed_during_preflight' in result['reasons']


@pytest.mark.parametrize('change', ['journal_fault', 'protection_fault', 'latch',
                                  'bootstrap', 'heartbeat_record', 'unknown_field', 'revision_rollback'])
def test_cold_admission_still_refuses_changed_non_deferred_safety(local, monkeypatch, change):
    root = local[0]
    original = P._probe
    def concurrent_change(*args):
        result = original(*args)
        health = SafetyHealth(root / 'data/safety_health.json')
        value = health.read()
        if change in ('journal_fault', 'protection_fault'):
            name = 'journal_write' if change == 'journal_fault' else 'protection'
            health.observe(name, 'FAILED')
        else:
            if change == 'latch': value['recovery_required'] = False
            if change == 'bootstrap': value['bootstrap'] = {'status': 'FAILED'}
            if change == 'heartbeat_record': value['heartbeats']['luffy'] = {'instance_id': 'changed'}
            if change == 'unknown_field': value['unexpected_authority'] = True
            if change == 'revision_rollback': value['revision'] = 0
            B.publish(health.path, value)
        return result
    monkeypatch.setattr(P, '_probe', concurrent_change)
    result = P.evaluate(P.collect_facts(root, clock=lambda: NOW, contained_cold=True))
    assert not result['allow']
    assert 'safety_health_changed_during_preflight' in result['reasons']


def test_child_refusal_logs_only_existing_sanitized_codes(monkeypatch, caplog):
    import sys
    from trader import kernel
    reasons = ['safety_not_pass', 'safety_health_changed_during_preflight']
    monkeypatch.setattr(sys, 'argv', ['fixture-kernel', '--bootstrap-token', 'attempt',
                                    '--expect-revision', REV])
    monkeypatch.setattr(kernel, 'load_config', lambda *a: {})
    monkeypatch.setattr(kernel, 'setup_logging', lambda *a: None)
    monkeypatch.setattr(P, 'prelaunch', lambda *a: {
        'allow': False, 'reasons': reasons, 'facts': {'private': 'NEVER_LOG_THIS'}})
    monkeypatch.setattr(kernel, 'Kernel', lambda *a: pytest.fail('refused Kernel constructed'))
    with pytest.raises(SystemExit) as exc:
        kernel.main()
    assert exc.value.code == ri.EXIT_WRONG_REVISION
    assert 'child_prelaunch_refused reasons=' + json.dumps(reasons) in caplog.text
    assert 'NEVER_LOG_THIS' not in caplog.text


def test_native_kernel_without_controller_refuses_before_construction(monkeypatch):
    import sys
    from trader import kernel
    monkeypatch.setattr(sys,'argv',['fixture-kernel'])
    monkeypatch.setattr(kernel,'load_config',lambda *a:{})
    monkeypatch.setattr(kernel,'setup_logging',lambda *a:None)
    monkeypatch.setattr(kernel,'Kernel',lambda *a:pytest.fail('Kernel constructed without admission'))
    with pytest.raises(SystemExit) as exc:
        kernel.main()
    assert exc.value.code==ri.EXIT_WRONG_REVISION


def test_admitted_run_wires_bounded_protection_producer_after_boot(monkeypatch):
    from trader import kernel
    from trader.engine import protection_snapshot
    events=[]
    lock=SimpleNamespace(locked=lambda:False)
    obj=SimpleNamespace(boot=lambda:events.append('boot'),_bootstrap_token='attempt',_stop=True,
                        cfg={'timeframes':{'scan_interval_seconds':60}},exchange=object(),
                        journal=SimpleNamespace(db_path='fixture.db'),supervisor=SimpleNamespace(_pass_lock=lock))
    obj._boot_complete = True
    obj.state_machine = SimpleNamespace(state=SimpleNamespace(value='FROZEN'))
    obj.heartbeat = SimpleNamespace(beat=lambda value: events.append(
        ('heartbeat', value['last_successful_cycle_at'], value['stopping'])))
    monitor=SimpleNamespace(loop=lambda **k:events.append(('loop',k['stopped']())))
    def make(exchange,path,**kwargs):
        assert exchange is obj.exchange and path=='fixture.db'
        assert not kwargs['supervisor_busy']()
        events.append('producer'); return monitor
    monkeypatch.setattr(protection_snapshot,'make_monitor',make)
    class Thread:
        def __init__(self,target,kwargs,**other): self.target=target;self.kwargs=kwargs
        def start(self): self.target(**self.kwargs)
    monkeypatch.setattr(B.threading,'Thread',Thread)
    kernel.Kernel.run(obj)
    assert events==['boot',('heartbeat',None,True),'producer',('loop',True)]
    assert obj._bootstrap_protection_monitor is monitor


def test_failed_attempt_real_lock_still_refuses_second_holder(tmp_path, monkeypatch):
    monkeypatch.setattr(ri,'code_revision',lambda *a:dict(revision=REV,dirty_code=False))
    monkeypatch.setattr(ri,'legacy_kernel_pids',lambda *a,**k:[])
    holder=ri.KernelInstance(tmp_path); holder.acquire()
    try:
        host=InertHost('timeout'); result=B.execute(host,timeout=2)
        assert result['containment']['stop']=='UNKNOWN' and result['observers_retained']
        with pytest.raises(ri.AlreadyRunning): ri.KernelInstance(tmp_path).acquire()
    finally: holder.release()


@pytest.mark.parametrize('condition',['heartbeat:luffy','observed_cycle:luffy'])
def test_absent_live_heartbeat_alone_is_deferred_not_waived(local,monkeypatch,condition):
    local[1]['conditions'][condition]=dict(status='ACTIVE',reason='MISSING')
    update_health(local)
    assert not P.evaluate(P.collect_facts(local[0],clock=lambda:NOW))['allow']
    result=phase_a(local,monkeypatch)
    assert result['allow'] and not result['trading_authority']
    assert result['facts']['safety']['deferred_to_contained_readiness']==[condition]


def test_stale_venue_receipt_while_paused_is_deferred_only_for_exact_reason(local,monkeypatch):
    name='paused_venue_protection'
    local[1]['conditions'][name]=dict(status='ACTIVE',reason='VENUE_STATE_UNCONFIRMED')
    update_health(local)
    monkeypatch.setattr(P,'_probe',lambda path,role,journal:dict(component=role,status='AVAILABLE',committed=True))
    assert not P.evaluate(P.collect_facts(local[0],clock=lambda:NOW))['allow']
    cold=P.evaluate(P.collect_facts(local[0],clock=lambda:NOW,contained_cold=True))
    assert cold['facts']['safety']['deferred_to_contained_readiness']==[name]
    assert 'active_condition:'+name not in cold['reasons']
    assert SafetyHealth(local[0]/'data/safety_health.json').read()['recovery_required'] is True
    local[1]['conditions'][name]=dict(status='ACTIVE',reason='SOMETHING_ELSE')
    update_health(local)
    refused=P.evaluate(P.collect_facts(local[0],clock=lambda:NOW,contained_cold=True))
    assert not refused['allow'] and 'active_condition:'+name in refused['reasons']


def test_native_containment_signals_only_sigterm_and_retains_on_unconfirmed_exit(tmp_path,monkeypatch):
    host=B.Host(tmp_path,REV); signals=[]
    host.process=SimpleNamespace(pid=77,send_signal=signals.append)
    host.expected=dict(pid=77,start_ticks=123)
    times=iter([0,301]); host.monotonic=lambda:next(times)
    states=iter(['LIVE','UNKNOWN'])
    monkeypatch.setattr(B,'service_state',lambda *a:next(states))
    result=host.contain_stop()
    assert signals==[signal.SIGTERM] and result['stop']=='UNKNOWN' and result['observers_retained']


def test_no_generic_force_option(tmp_path):
    with pytest.raises(SystemExit) as exc:
        B.main(['--root',str(tmp_path),'--expect-revision',REV,'--force'])
    assert exc.value.code==2 and not (tmp_path/'data').exists()


def test_native_token_does_not_admit_unchecked_alternate_config(monkeypatch):
    import sys
    from trader import kernel
    monkeypatch.setattr(sys,'argv',['fixture-kernel','--bootstrap-token','attempt',
                                   '--expect-revision',REV,'--config','/tmp/unchecked.yaml'])
    monkeypatch.setattr(kernel,'load_config',lambda *a:{})
    monkeypatch.setattr(kernel,'setup_logging',lambda *a:None)
    monkeypatch.setattr(B,'child_admission',lambda *a:pytest.fail('unchecked config admitted'))
    monkeypatch.setattr(kernel,'Kernel',lambda *a:pytest.fail('unchecked config constructed'))
    with pytest.raises(SystemExit) as exc:
        kernel.main()
    assert exc.value.code==ri.EXIT_WRONG_REVISION


def test_revision_bound_root_config_reaches_child_admission(monkeypatch):
    import sys
    from trader import kernel
    calls=[]
    monkeypatch.setattr(sys,'argv',['fixture-kernel','--bootstrap-token','attempt',
        '--expect-revision',REV,'--config',str(kernel.ROOT/'config.yaml')])
    monkeypatch.setattr(kernel,'load_config',lambda *a:{})
    monkeypatch.setattr(kernel,'setup_logging',lambda *a:None)
    def admission(*args):
        calls.append(args); raise ValueError('fixture refusal before construction')
    monkeypatch.setattr(B,'child_admission',admission)
    monkeypatch.setattr(kernel,'Kernel',lambda *a:pytest.fail('fixture constructed'))
    with pytest.raises(SystemExit): kernel.main()
    assert calls==[(kernel.ROOT,'attempt',REV)]
