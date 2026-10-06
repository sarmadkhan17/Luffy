"""OBS-01: local fixtures only, with no real launcher or network capability."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from trader.observability import preflight as P

ROOT = Path(__file__).resolve().parents[1]
NOW = 1800000000.0


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, 'connect', lambda *a, **k: pytest.fail('network forbidden'))
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('network forbidden'))


def good_facts():
    return {key: {'status': 'PASS', 'reasons': []}
            for key in ('storage', 'environment', 'protection', 'control', 'safety')}


@pytest.fixture
def local(tmp_path, monkeypatch):
    data = tmp_path / 'data'; data.mkdir()
    health = dict(schema='luffy-safety-health.v1', revision=1, recovery_required=True,
                  conditions={}, heartbeats={})
    (data / 'safety_health.json').write_text(json.dumps(health))
    (data / 'watchdog.off').touch()
    db = sqlite3.connect(data / 'luffy.db')
    db.executescript('''CREATE TABLE state_kv(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE trades(id TEXT,symbol TEXT,side TEXT,amount REAL,stop_loss REAL,sl_order_id TEXT,status TEXT);
        CREATE TABLE execution_requests(state TEXT);
        CREATE TABLE partial_exit_intents(state TEXT);
        CREATE TABLE protection_evidence(slot INTEGER,boot INTEGER,seq INTEGER,value TEXT);''')
    db.executemany('INSERT INTO state_kv VALUES (?,?)',
                   [('control_state', 'FROZEN'), ('macro_guard_operator_hold', '1')])
    at = datetime.fromtimestamp(NOW - 1, timezone.utc).isoformat()
    snapshot = dict(schema=2, generation=dict(boot=1, seq=1), status='VERIFIED',
                    checked_at=at, completed_at=at, control_state_observed='FROZEN',
                    complete_listing=True, mutations=0, checks=dict.fromkeys(P.CHECKS, True),
                    cleanliness=dict(status='CLEAN', items=[], items_truncated=False, unread_symbols=[]),
                    position_count=0, symbols=[], reasons=[])
    db.execute('INSERT INTO protection_evidence VALUES (1,1,1,?)', (json.dumps(snapshot),))
    db.commit(); db.close()
    from trader.core import config
    monkeypatch.setattr(config, 'load_config', lambda *a: dict(
        brain=dict(enabled=False), research=dict(referee=False, handoff=False)))
    monkeypatch.setattr(config.Env, 'get', lambda *a: 'true')
    monkeypatch.setattr(P, '_filesystem', lambda *a: None)
    return tmp_path, health, snapshot


def collect(local):
    return P.collect_facts(local[0], clock=lambda: NOW)


def update_health(local):
    (local[0] / 'data/safety_health.json').write_text(json.dumps(local[1]))


def update_snapshot(local):
    with sqlite3.connect(local[0] / 'data/luffy.db') as db:
        db.execute('UPDATE protection_evidence SET value=?', (json.dumps(local[2]),))


def test_all_good_real_local_probes_pass_and_keep_safety_latch(local):
    before = (local[0] / 'data/safety_health.json').read_bytes()
    result = P.evaluate(collect(local))
    assert result['allow'] is True and result['result'] == 'PASS'
    assert result['facts']['storage']['components'][0]['committed'] is True
    assert (local[0] / 'data/safety_health.json').read_bytes() == before


@pytest.mark.parametrize('condition', ['journal_write', 'journal_read', 'candles_integrity', 'venue_protection', 'environment'])
def test_active_failure_blocks_without_resolving_or_probing(local, monkeypatch, condition):
    local[1]['conditions'][condition] = dict(status='ACTIVE', reason='COMMIT_FAILED')
    update_health(local)
    monkeypatch.setattr(P, '_probe', lambda *a: pytest.fail('active fault must not be probed away'))
    result = P.evaluate(collect(local))
    assert result['allow'] is False
    assert 'active_condition:' + condition in result['reasons']
    assert json.loads((local[0] / 'data/safety_health.json').read_text()) == local[1]


@pytest.mark.parametrize('failure', ['missing_db', 'invalid_db', 'missing_health', 'invalid_health', 'invalid_condition'])
def test_unreadable_storage_or_required_health_blocks(local, failure):
    path = local[0] / 'data/luffy.db'
    if failure == 'missing_db': path.unlink()
    if failure == 'invalid_db': path.write_text('broken SQLite')
    healthpath = local[0] / 'data/safety_health.json'
    if failure == 'missing_health': healthpath.unlink()
    if failure == 'invalid_health': healthpath.write_text('{')
    if failure == 'invalid_condition':
        local[1]['conditions']['journal_write'] = {'status': 'UNKNOWN'}
        update_health(local)
    assert P.evaluate(collect(local))['allow'] is False
    if failure == 'missing_db': assert not path.exists()  # no newly fabricated store


@pytest.mark.parametrize('variant', ['partial', 'unreadable', 'missing', 'stale', 'future', 'wrong_generation',
    'unknown_check', 'bool_check', 'orders', 'truncated', 'unread_symbols', 'wrong_control', 'missing_symbols',
    'unknown_completion', 'mutations'])
def test_protection_uncertainty_blocks(local, variant):
    s = local[2]
    if variant == 'partial': s['status'] = 'PARTIAL'
    if variant == 'unreadable': s['status'] = 'UNREADABLE'
    if variant == 'stale': s['checked_at'] = datetime.fromtimestamp(NOW-121, timezone.utc).isoformat()
    if variant == 'future': s['checked_at'] = datetime.fromtimestamp(NOW+1, timezone.utc).isoformat()
    if variant == 'wrong_generation': s['generation']['seq'] = 2
    if variant == 'unknown_check': s['checks'].pop('venue_protection')
    if variant == 'bool_check': s['checks']['venue_protection'] = 1
    if variant == 'orders': s['cleanliness']['items'] = [{'class': 'entry_order'}]
    if variant == 'truncated': s['cleanliness']['items_truncated'] = True
    if variant == 'unread_symbols': s['cleanliness']['unread_symbols'] = ['A']
    if variant == 'wrong_control': s['control_state_observed'] = 'ACTIVE'
    if variant == 'missing_symbols': s['position_count'] = 1
    if variant == 'unknown_completion': s.pop('completed_at')
    if variant == 'mutations': s['mutations'] = 1
    update_snapshot(local)
    if variant == 'missing':
        with sqlite3.connect(local[0] / 'data/luffy.db') as db: db.execute('DELETE FROM protection_evidence')
    assert P.evaluate(collect(local))['allow'] is False


@pytest.mark.parametrize('variant', ['live', 'provider', 'referee', 'handoff', 'missing_config', 'filesystem', 'watchdog'])
def test_environment_failures(local, monkeypatch, variant):
    from trader.core import config
    cfg = dict(brain=dict(enabled=False), research=dict(referee=False, handoff=False))
    if variant == 'live': monkeypatch.setattr(config.Env, 'get', lambda *a: 'false')
    if variant == 'provider': cfg['brain']['enabled'] = True
    if variant in ('referee', 'handoff'): cfg['research'][variant] = True
    if variant == 'missing_config': cfg.pop('brain')
    monkeypatch.setattr(config, 'load_config', lambda *a: cfg)
    if variant == 'filesystem': monkeypatch.setattr(P, '_filesystem', lambda *a: (_ for _ in ()).throw(OSError()))
    if variant == 'watchdog': (local[0] / 'data/watchdog.off').unlink()
    assert P.evaluate(collect(local))['allow'] is False


@pytest.mark.parametrize('variant', ['ACTIVE', 'HALTED', 'RECOVERY', 'hold_missing', 'latch_missing', 'pending', 'partial', 'requests_missing', 'request_null', 'partial_null'])
def test_controls_recovery_and_pending_execution_preserved(local, variant):
    with sqlite3.connect(local[0] / 'data/luffy.db') as db:
        if variant in ('ACTIVE', 'HALTED', 'RECOVERY'):
            db.execute("UPDATE state_kv SET value=? WHERE key='control_state'", (variant,))
        if variant == 'hold_missing': db.execute("DELETE FROM state_kv WHERE key='macro_guard_operator_hold'")
        if variant == 'pending': db.execute("INSERT INTO execution_requests VALUES ('SUBMITTED')")
        if variant == 'partial': db.execute("INSERT INTO partial_exit_intents VALUES ('PENDING')")
        if variant == 'request_null': db.execute('INSERT INTO execution_requests VALUES (NULL)')
        if variant == 'partial_null': db.execute('INSERT INTO partial_exit_intents VALUES (NULL)')
        if variant == 'requests_missing': db.execute('DROP TABLE execution_requests')
    if variant == 'latch_missing':
        local[1]['recovery_required'] = False; update_health(local)
    assert P.evaluate(collect(local))['allow'] is False


@pytest.mark.parametrize('value', [None, [], {}, {'storage': {'status': 'PASS'}},
    {key: {'status': 'UNKNOWN'} for key in good_facts()},
    {key: {'status': 'UNREADABLE'} for key in good_facts()}])
def test_missing_malformed_preflight(value):
    assert P.evaluate(value)['allow'] is False


@pytest.mark.parametrize('text', ['{"schema":1,"schema":2}', '{"value":NaN}', ''])
def test_strict_fact_json(text):
    with pytest.raises(ValueError): P.strict_json(text)


def test_safety_race_refuses(local, monkeypatch):
    original = P._probe
    def changed(*a):
        result = original(*a)
        local[1]['conditions']['journal_write'] = dict(status='ACTIVE')
        update_health(local)
        return result
    monkeypatch.setattr(P, '_probe', changed)
    assert P.evaluate(collect(local))['allow'] is False


def test_new_holding_after_snapshot_refuses(local):
    with sqlite3.connect(local[0] / 'data/luffy.db') as db:
        db.execute("INSERT INTO trades VALUES ('t','SOL/USDT','long',1,10,'stop','open')")
    assert P.evaluate(collect(local))['allow'] is False


@pytest.mark.parametrize('value', ['{"SOL/USDT":{}}', 'null', 'broken'])
def test_pending_or_unreadable_rearm_refuses(local, value):
    with sqlite3.connect(local[0] / 'data/luffy.db') as db:
        db.execute("INSERT INTO state_kv VALUES ('reconcile_rearm_submitted',?)", (value,))
    assert P.evaluate(collect(local))['allow'] is False


def test_held_book_exact_match_passes_but_changed_quantity_refuses(local):
    with sqlite3.connect(local[0] / 'data/luffy.db') as db:
        db.execute("INSERT INTO trades VALUES ('t','SOL/USDT','long',1,10,'stop','open')")
    local[2]['position_count'] = 1
    local[2]['symbols'] = [dict(journal_trade_id='t',symbol='SOL/USDT',journal_side='long',
        journal_amount=1,expected_stop=10,journal_stop_id='stop',stop_present=True,quantity_agrees=True,
        precision_status='VALID',rearm_evidence='NONE',reasons=[])]
    update_snapshot(local)
    assert P.evaluate(collect(local))['allow'] is True
    with sqlite3.connect(local[0] / 'data/luffy.db') as db:
        db.execute('UPDATE trades SET amount=2')
    assert P.evaluate(collect(local))['allow'] is False


@pytest.mark.parametrize('variant', ['missing', 'invalid', 'locked'])
def test_configured_critical_store_requires_committed_write(local, monkeypatch, variant):
    from trader.core import config
    path = local[0] / 'data/extra.db'
    monkeypatch.setattr(config, 'load_config', lambda *a: dict(brain=dict(enabled=False),
        research=dict(referee=False,handoff=False),
        safety_monitor=dict(critical_stores=[dict(path='data/extra.db',role='extra')])))
    if variant == 'invalid': path.write_text('broken')
    lock = None
    if variant == 'locked':
        lock = sqlite3.connect(path); lock.execute('CREATE TABLE sample(x)'); lock.commit()
        lock.execute('BEGIN IMMEDIATE')
    try:
        assert P.evaluate(collect(local))['allow'] is False
        if variant == 'missing': assert not path.exists()
    finally:
        if lock is not None: lock.close()


def test_cli_bad_fact_serialization_is_machine_readable_refusal(monkeypatch, capsys, tmp_path):
    facts = good_facts(); facts['storage']['bad'] = float('nan')
    monkeypatch.setattr(P, 'collect_facts', lambda *a: facts)
    assert P.main(['--root',str(tmp_path),'--target','kernel']) == 1
    assert json.loads(capsys.readouterr().out)['allow'] is False


@pytest.mark.parametrize('variant', ['same_volume', 'readonly', 'low_capacity', 'unreadable'])
def test_filesystem_required_envelope(monkeypatch, variant):
    from types import SimpleNamespace as NS
    class Mount:
        def __init__(self, device): self.device = device
        def stat(self): return NS(st_dev=self.device)
    def statvfs(path):
        if variant == 'unreadable': raise OSError()
        return NS(f_flag=os.ST_RDONLY if variant == 'readonly' else 0,
                  f_bavail=1 if variant == 'low_capacity' else 2*P.MIN_FREE_BYTES,f_frsize=1)
    monkeypatch.setattr(P.os, 'statvfs', statvfs)
    with pytest.raises((ValueError, OSError)):
        P._filesystem(Mount(1), Mount(1 if variant == 'same_volume' else 2))


def shell_fixture(tmp_path, facts):
    """Actual restart.sh + actual preflight CLI; every operational command is a stub."""
    (tmp_path / 'restart.sh').write_bytes((ROOT / 'restart.sh').read_bytes())
    binpath = tmp_path / 'bin'; binpath.mkdir()
    venv = tmp_path / 'venv/bin'; venv.mkdir(parents=True)
    fixture = tmp_path / 'facts.json'; fixture.write_text(json.dumps(facts))
    events = tmp_path / 'events'
    shim = '#!' + sys.executable + '\n' + '''import json,os,sys
sys.path.insert(0, os.environ['TEST_REPO'])
if sys.argv[2] == 'trader.observability.dashboard_readiness':
    print(json.dumps(dict(allow=True, result='PASS')))
    raise SystemExit(0)  # OBS-01 isolates its preflight boundary; OBS-02 tests readiness.
if sys.argv[2] == 'trader.runtime_identity':
    raise SystemExit(0)  # RUN-01 stop/verify step, after the OBS-01 gate; proven in test_run01_instance_identity.
from trader.observability import preflight as p
from trader import runtime_identity as ri
ri.inspect=lambda *a: dict(state='NONE', legacy_pids=[])
ri.check_revision=lambda *a: dict(revision='a'*40, dirty_code=False)
p.collect_facts=lambda *a, **k: json.load(open(os.environ['TEST_FACTS']))
raise SystemExit(p.main(sys.argv[3:]))
'''
    (venv / 'python').write_text(shim); (venv / 'python').chmod(0o755)
    for name in ('pgrep', 'sleep', 'mkdir', 'setsid', 'readlink'):
        path = binpath / name
        path.write_text('#!/bin/bash\necho ' + name + ' >> "$TEST_EVENTS"\n')
        path.chmod(0o755)
    env = {**os.environ, 'PATH': str(binpath)+':'+os.environ['PATH'], 'TEST_REPO': str(ROOT),
           'TEST_FACTS': str(fixture), 'TEST_EVENTS': str(events), 'LUFFY_EXPECT_REVISION': 'a'*40}
    # Stub mkdir intentionally does nothing. Existing log destination is /tmp/opencode;
    # substitute fixture logs so no production path is used even on success.
    p = tmp_path / 'restart.sh'; p.write_text(p.read_text().replace('/tmp/opencode', str(tmp_path)))
    return p, events, env


@pytest.mark.parametrize('target', ['kernel', 'dashboard'])
@pytest.mark.parametrize('case', ['good', 'storage_active', 'storage_unreadable', 'protection_unknown', 'environment_fail', 'missing', 'malformed'])
def test_actual_shell_gate_before_every_stop_start(tmp_path, target, case):
    facts = good_facts()
    if case == 'storage_active': facts['storage'] = dict(status='FAIL', reasons=['active_condition:journal_write'])
    if case == 'storage_unreadable': facts['storage']['status'] = 'UNREADABLE'
    if case == 'protection_unknown': facts['protection']['status'] = 'UNKNOWN'
    if case == 'environment_fail': facts['environment']['status'] = 'FAIL'
    if case == 'missing': facts = None
    if case == 'malformed': facts = ['bad']
    path, events, env = shell_fixture(tmp_path, facts)
    result = subprocess.run(['bash', str(path), target], env=env, capture_output=True, text=True, timeout=10)
    first = json.loads(result.stdout.splitlines()[0])
    if case == 'good':
        assert result.returncode == 0 and first['allow'] is True
        assert 'started trader.' in result.stdout
        # The fake setsid records launch without invoking the supplied Python command.
        for _ in range(100):
            if events.exists() and 'setsid' in events.read_text(): break
            import time; time.sleep(.01)
        assert 'setsid' in events.read_text()
    else:
        assert result.returncode != 0 and first['allow'] is False
        assert not events.exists()  # even pgrep/sleep/mkdir is unreachable
        assert 'started' not in result.stdout


@pytest.mark.parametrize('variant', ['missing_helper', 'crashed_helper'])
def test_unavailable_preflight_command_has_zero_side_effects(tmp_path, variant):
    path, events, env = shell_fixture(tmp_path, good_facts())
    python = tmp_path / 'venv/bin/python'
    if variant == 'missing_helper': python.unlink()
    else: python.write_text('#!/bin/bash\nexit 2\n')
    result = subprocess.run(['bash',str(path),'kernel'],env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode != 0 and not events.exists()
    assert json.loads(result.stdout)['reasons'] == ['preflight_command_failed']


def test_terminal_obs01_remains_readable_without_selecting_next_item(tmp_path):
    from trader.dashboard.tracker import read_tracker
    import yaml
    from collections import Counter
    ledger = yaml.safe_load((ROOT / 'docs/tracker/LUFFY_Product_Tracker_v1.yaml').read_text())
    for row in ledger['items']:
        if row['id'] == 'OBS-01': row['status'] = 'CLOSED'
    ledger['metadata']['current_active_item'] = 'OBS-01'
    ledger['metadata']['status_counts'] = dict(Counter(row['status'] for row in ledger['items']))
    path = tmp_path / 'docs/tracker'; path.mkdir(parents=True)
    (path / 'LUFFY_Product_Tracker_v1.yaml').write_text(yaml.safe_dump(ledger))
    (tmp_path / 'NEXT.yaml').write_text(yaml.safe_dump(dict(work_package=dict(
        id='OBS-01', status='CLOSED', mode='ENGINEERING_ONLY', objective='Gate launch.'))))
    result = read_tracker(tmp_path)
    assert result['status'] == 'AVAILABLE'
    assert result['selected']['id'] == 'OBS-01' and result['selected']['status'] == 'CLOSED'
