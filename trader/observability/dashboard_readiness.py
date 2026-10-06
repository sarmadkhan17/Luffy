"""OBS-02: read-only current-Kernel readiness before Dashboard attachment.

RUN-01 owns process identity. This gate consumes its lock/record verification,
the existing heartbeat contract and independently observed safety receipts.
It never repairs health, clears containment, or launches a process.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import time

from trader import runtime_identity
from trader.engine.watchdog import read_heartbeat, validate_heartbeat
from .preflight import _health
from .safety import HeartbeatPolicy

SCHEMA = 'luffy-dashboard-readiness.v1'


def _time(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def check(root, cfg, *, expected_identity=None, clock=time.time):
    root = Path(root).resolve()
    facts, reasons = {}, []
    def require(condition, reason):
        if not condition:
            reasons.append(reason)
    try:
        # Do not create a runtime record or lock as a side effect of a refusal.
        require((root / 'data/kernel_instance.json').is_file()
                and (root / 'data/kernel.lock').is_file(), 'kernel_identity_missing')
        if reasons:
            raise ValueError('identity unavailable')
        identity = runtime_identity.inspect(root)
        facts['identity'] = identity
        record = identity.get('record') or {}
        require(identity.get('state') == 'RUNNING' and identity.get('verified') is True,
                'kernel_identity_unverified')
        require(identity.get('heartbeat_belongs_to_instance') is True, 'heartbeat_instance_unbound')
        revision = runtime_identity.code_revision(root)
        require(revision['revision'] is not None and revision['dirty_code'] is False
                and record.get('revision') == revision['revision'] and record.get('dirty_code') is False,
                'kernel_revision_not_intended')
        if expected_identity is not None:
            require(record.get('pid') == expected_identity.get('pid')
                    and str(record.get('start_ticks')) == str(expected_identity.get('start_ticks')),
                    'kernel_not_intended_process')
        now = clock()
        policy = HeartbeatPolicy.configured(cfg)
        require(policy.stale_after_s is not None, 'heartbeat_policy_unknown')
        beat = read_heartbeat(root / 'data/heartbeat_luffy.json', 'luffy', now)
        facts['heartbeat'] = beat
        require(bool(record.get('instance_id')) and beat['instance_id'] == record.get('heartbeat_instance_id'),
                'heartbeat_prior_instance')
        require(_time(record.get('started_at')) and record['started_at'] <= beat['started_at'],
                'heartbeat_predates_kernel')
        if policy.stale_after_s is not None:
            require(max(now - beat['timestamp'], now - beat['context']['last_successful_cycle_at'])
                    <= policy.stale_after_s, 'heartbeat_stale')
        require(beat['context'].get('boot_complete') is True, 'kernel_boot_incomplete')
        require(beat['context'].get('stopping') is False, 'kernel_stopping_or_unknown')
        require(beat['context'].get('state') == 'FROZEN', 'heartbeat_control_not_frozen')
        health = _health(root / 'data/safety_health.json')
        facts['health'] = health
        for name, condition in health['conditions'].items():
            require(condition['status'] != 'ACTIVE', 'blocking_health:' + name)
        observed_at = health.get('observed_at')
        require(_time(observed_at) and beat['started_at'] <= observed_at <= now,
                'health_observation_time_unknown')
        if policy.stale_after_s is not None and _time(observed_at):
            require(now - observed_at <= policy.stale_after_s, 'health_observation_stale')
        observed = validate_heartbeat(health['heartbeats']['luffy'], 'luffy', now)
        if policy.stale_after_s is not None:
            require(max(now - observed['timestamp'], now - observed['context']['last_successful_cycle_at'])
                    <= policy.stale_after_s, 'independent_heartbeat_observation_stale')
        require(observed['instance_id'] == beat['instance_id'] and observed['started_at'] == beat['started_at'],
                'health_prior_instance')
        require(observed['context'].get('boot_complete') is True
                and observed['context'].get('state') == 'FROZEN'
                and observed['context'].get('stopping') is False, 'health_boot_control_unconfirmed')
        require(beat['sequence'] >= observed['sequence'] and beat['timestamp'] >= observed['timestamp']
                and beat['context']['last_successful_cycle_at'] >= observed['context']['last_successful_cycle_at'],
                'heartbeat_chronology_regression')
        require(beat['sequence'] != observed['sequence'] or beat == observed, 'heartbeat_sequence_restamped')
        require(observed['timestamp'] <= observed_at if _time(observed_at) else False,
                'health_receipt_predates_heartbeat')
        with closing(sqlite3.connect((root / 'data/luffy.db').as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            controls = dict(db.execute("SELECT key,value FROM state_kv WHERE key IN "
                                       "('control_state','macro_guard_operator_hold')"))
        facts['controls'] = controls
        require(controls == {'control_state': 'FROZEN', 'macro_guard_operator_hold': '1'},
                'current_control_not_frozen_held')
        # A process/record replacement during the read cannot inherit acceptance.
        final = runtime_identity.inspect(root)
        require(final.get('state') == 'RUNNING' and final.get('verified') is True
                and final.get('record') == record and final.get('heartbeat_belongs_to_instance') is True,
                'kernel_changed_during_check')
        require(_health(root / 'data/safety_health.json') == health, 'health_changed_during_check')
    except Exception as exc:
        # No exception text or unknown fact is promoted to readiness.
        reasons.append('required_readiness_unreadable:' + type(exc).__name__)
    return dict(schema=SCHEMA, allow=not reasons, result='FAIL' if reasons else 'PASS',
                reasons=list(dict.fromkeys(reasons)), facts=facts)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        from trader.core.config import load_config
        result = check(args.root, load_config(str(args.root / 'config.yaml')))
    except Exception as exc:
        result = dict(schema=SCHEMA, allow=False, result='FAIL',
                      reasons=['readiness_command_failed:' + type(exc).__name__])
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result['allow'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
