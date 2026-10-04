"""Bounded safety health artifact independent of the operational Journal.

Incident transitions (one active and one last resolved per condition) survive
observer reopen. No repeats, cadence, trading or automatic release authority.
A recovery latch clears only after existing Supervisor verification succeeds.
"""
from __future__ import annotations

from contextlib import contextmanager, closing
from dataclasses import dataclass
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import threading
from uuid import uuid4

log = logging.getLogger(__name__)
SCHEMA = 'luffy-safety-health.v1'
_LOCAL_LOCKS = {}
_LOCKS_GUARD = threading.Lock()
_LOCK_DEPTH = threading.local()


def publish(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(json.dumps(value, sort_keys=True, allow_nan=False))
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def health_path(journal):
    return Path(journal.db_path).with_name('safety_health.json')


class SafetyHealth:
    def __init__(self, path, *, clock=time.time, sink=None):
        self.path = Path(path)
        self.clock = clock
        # Logging/stdout observer is independent even if both SQLite and artifact
        # destination fail. Injected notifier receives transitions only.
        self.sink = sink or (lambda event: log.critical('SAFETY %s', json.dumps(event)))

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = str(self.path.absolute())
        with _LOCKS_GUARD:
            local = _LOCAL_LOCKS.setdefault(key, threading.RLock())
        with local:
            depths = getattr(_LOCK_DEPTH, 'paths', None)
            if depths is None:
                depths = _LOCK_DEPTH.paths = set()
            if key in depths:
                yield  # reentrant Journal failure reporting during final guard
                return
            with self.path.with_suffix('.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                depths.add(key)
                try:
                    yield
                finally:
                    depths.remove(key)

    def read(self):
        try:
            if not self.path.exists():
                return dict(schema=SCHEMA, revision=0, recovery_required=False, conditions={}, heartbeats={})
            v = json.loads(self.path.read_text())
            if (v['schema'] != SCHEMA or type(v['revision']) is not int
                    or type(v['recovery_required']) is not bool or not isinstance(v['conditions'], dict)
                    or not isinstance(v['heartbeats'], dict)
                    or any(not isinstance(c, dict) or c.get('status') not in ('ACTIVE', 'RESOLVED')
                           for c in v['conditions'].values())):
                raise ValueError('health_contract')
            return v
        except Exception:
            return dict(schema=SCHEMA, revision=-1, recovery_required=True,
                        conditions={'safety_health_invalid': dict(status='ACTIVE', reason='artifact_invalid')}, heartbeats={})

    def observe(self, condition, reason=None, *, context=None):
        event = None
        with self.locked():
            v = self.read()
            previous = v['conditions'].get(condition, {})
            active = previous.get('status') == 'ACTIVE'
            now = self.clock()
            if reason:
                v['recovery_required'] = True
                if not active:
                    event = dict(condition=condition, incident_id=uuid4().hex, status='ACTIVE',
                                 detected_at=now, reason=reason)
                    previous = dict(event)
                previous.update(reason=reason, context=context or {})
            elif active:
                event = dict(previous, status='RESOLVED', resolved_at=now)
                previous = dict(event)
            else:
                return v
            v['conditions'][condition] = previous
            v.update(revision=v['revision'] + 1, observed_at=now)
            # Immediate publication to independent notifier precedes filesystem
            # persistence. Journal is never consulted by this path.
            if event:
                try:
                    self.sink(event)
                except Exception as exc:
                    previous['alert_sink_error'] = type(exc).__name__
                    log.critical('SAFETY notifier unavailable: %s', json.dumps(event))
            publish(self.path, v)
            if event:
                try:
                    publish(self.path.parent / 'safety-incidents' / (event['incident_id'] + '-' + event['status'] + '.json'), event)
                except Exception:
                    log.critical('SAFETY incident receipt unavailable: %s', json.dumps(event))
        return v

    def entry_block(self):
        v = self.read()
        return 'critical_safety_requires_recovery' if v['recovery_required'] or any(
            c.get('status') == 'ACTIVE' for c in v['conditions'].values()) else None

    def clear_after_supervisor(self, revision):
        with self.locked():
            v = self.read()
            if v['revision'] != revision or any(c.get('status') == 'ACTIVE' for c in v['conditions'].values()):
                return False
            v.update(recovery_required=False, revision=revision + 1, observed_at=self.clock())
            publish(self.path, v)
            return True

    def active_reasons(self):
        return [key + ':' + str(c.get('reason')) for key, c in self.read()['conditions'].items()
                if c.get('status') == 'ACTIVE']


def journal_failure(journal, mode, exc):
    journal._safety_failed = True
    try:
        SafetyHealth(health_path(journal)).observe('journal_' + mode, type(exc).__name__)
    except Exception:
        log.critical('SAFETY journal_%s unavailable; health publication unavailable', mode)


def check_journal(journal):
    """Readable AND committed writable. Called before new-entry/recovery work,
    never inside an existing Risk-held transaction."""
    health = SafetyHealth(health_path(journal))
    try:
        journal.query('SELECT key,value FROM state_kv LIMIT 1')
        health.observe('journal_read', None)
    except Exception as exc:
        journal_failure(journal, 'read', exc)
        return False
    try:
        journal.kv_set('__critical_storage_probe__', uuid4().hex)
        health.observe('journal_write', None)
        journal._safety_failed = False
        for path, role in getattr(journal, '_critical_store_paths', []):
            result = SafetyObserver(health).store(path, role)
            if result['status'] != 'AVAILABLE':
                return False
        return True
    except Exception as exc:
        journal_failure(journal, 'write', exc)
        return False


def entry_refusal(journal, *, probe=False):
    if not hasattr(journal, 'db_path'):
        return None  # existing in-process test doubles
    if probe and not check_journal(journal):
        return 'critical_journal_unavailable'
    if getattr(journal, '_safety_failed', False):
        return 'critical_journal_unavailable'
    return SafetyHealth(health_path(journal)).entry_block()


@dataclass(frozen=True)
class HeartbeatPolicy:
    stale_after_s: float | None
    basis: str = 'POLICY_NOT_CONFIGURED'

    def __post_init__(self):
        if self.stale_after_s is not None and (type(self.stale_after_s) not in (float, int)
                or not math.isfinite(self.stale_after_s) or self.stale_after_s <= 0):
            raise ValueError('invalid_heartbeat_policy')

    @classmethod
    def configured(cls, cfg):
        # Existing kernel boot registers this exact stall policy. Do not add a
        # default or reuse the unrelated legacy watchdog restart threshold.
        seconds = (cfg.get('timeframes') or {}).get('scan_interval_seconds')
        return cls(float(seconds) * 4, 'existing_kernel_scan_interval_times_four') if seconds is not None else cls(None)


class SafetyObserver:
    """Independent observer invoked by scripts.monitor/watchdog. No venue calls."""
    def __init__(self, health, *, clock=time.time):
        self.health = health
        self.clock = clock

    def heartbeat(self, path, producer, policy):
        now = self.clock()
        reason, record = None, None
        try:
            from trader.engine.watchdog import read_heartbeat
            record = read_heartbeat(path, producer, now)
            if policy.stale_after_s is None:
                reason = 'POLICY_NOT_CONFIGURED'
            elif max(now - record['timestamp'],
                     now - record['context']['last_successful_cycle_at']) > policy.stale_after_s:
                reason = 'STALE'
            with self.health.locked():
                v = self.health.read()
                prior = v['heartbeats'].get(producer)
                if prior and prior['instance_id'] == record['instance_id']:
                    if record['sequence'] < prior['sequence'] or record['timestamp'] < prior['timestamp']:
                        reason = 'CHRONOLOGY_REGRESSION'
                    elif record['sequence'] == prior['sequence'] and record != prior:
                        reason = 'UNCHANGED_SEQUENCE_RESTAMPED'
                elif prior:
                    reason = 'PRODUCER_RESTART_REQUIRES_RECOVERY'
                if reason not in ('CHRONOLOGY_REGRESSION', 'UNCHANGED_SEQUENCE_RESTAMPED'):
                    v['heartbeats'][producer] = record
                v.update(revision=v['revision'] + 1, observed_at=now)
                publish(self.health.path, v)
        except FileNotFoundError:
            reason = 'MISSING'
        except Exception:
            reason = 'UNAVAILABLE'
        self.health.observe('heartbeat:' + producer, reason, context=dict(record=record, policy=policy.basis))
        return dict(component=producer, status=reason or 'FRESH', record=record)

    def store(self, path, role, *, journal=False):
        reason = None
        try:
            with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, timeout=0)) as db:
                if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                    raise sqlite3.DatabaseError('integrity')
                if journal:
                    db.execute('SELECT key,value FROM state_kv LIMIT 1').fetchall()
                self.health.observe(role + '_read', None)
                try:
                    if journal:
                        db.execute('INSERT OR REPLACE INTO state_kv(key,value) VALUES (?,?)',
                                   ('__critical_storage_probe__', uuid4().hex))
                    else:
                        # Write the existing version header without schema/data
                        # changes. Successful commit proves writable durability.
                        version = int(db.execute('PRAGMA user_version').fetchone()[0])
                        db.execute('PRAGMA user_version=' + str(version))
                    db.commit()
                    self.health.observe(role + '_write', None)
                except sqlite3.Error as exc:
                    reason = 'WRITE_UNAVAILABLE'
                    self.health.observe(role + '_write', type(exc).__name__)
        except (sqlite3.Error, OSError) as exc:
            reason = 'READ_UNAVAILABLE'
            self.health.observe(role + '_read', type(exc).__name__)
        return dict(component=role, status=reason or 'AVAILABLE')


def is_storage_error(exc):
    if isinstance(exc, OSError):
        return True
    return isinstance(exc, sqlite3.DatabaseError) and not isinstance(exc, (sqlite3.IntegrityError, sqlite3.ProgrammingError)) and not any(
        text in str(exc).lower() for text in ('no such table', 'no such column', 'syntax error'))
