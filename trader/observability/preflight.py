"""OBS-01 local launch gate. No process identity, venue or provider capability.

Only the existing controlled FROZEN/demo scope is admitted. A cached protection
receipt must still be current; missing venue evidence never triggers acquisition.
Committed storage probes reuse SafetyObserver without resolving persisted faults.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import math
import os
from pathlib import Path
import sqlite3
import time

SCHEMA = 'luffy-launch-preflight.v1'
CHECKS = ('venue_positions', 'observation_consistent', 'reconciliation',
          'venue_protection', 'precision_known')
RECOVERY_ROOT = Path('/mnt/luffy-recovery/recovery')  # retained observation environment
MIN_FREE_BYTES = 1024 ** 3  # unchanged retained preflight envelope


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('nonfinite_json')
    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def evaluate(facts):
    """All required facts are explicit; only literal PASS admits launch."""
    reasons = []
    if not isinstance(facts, dict):
        facts = {}
        reasons.append('preflight_facts_malformed')
    for name in ('storage', 'environment', 'protection', 'control', 'safety'):
        fact = facts.get(name)
        if not isinstance(fact, dict) or fact.get('status') != 'PASS':
            reasons.append(name + '_not_pass')
            if isinstance(fact, dict):
                reasons.extend(fact.get('reasons', []) if isinstance(fact.get('reasons'), list) else [])
    reasons = list(dict.fromkeys(r for r in reasons if isinstance(r, str)))
    return dict(schema=SCHEMA, allow=not reasons, result='PASS' if not reasons else 'FAIL',
                reasons=reasons, facts=facts)


def _fact(reasons):
    return dict(status='FAIL' if reasons else 'PASS', reasons=reasons)


def _health(path):
    value = strict_json(path.read_text())
    if (not isinstance(value, dict) or value.get('schema') != 'luffy-safety-health.v1'
            or type(value.get('revision')) is not int or value['revision'] < 0
            or type(value.get('recovery_required')) is not bool
            or not isinstance(value.get('conditions'), dict)):
        raise ValueError('safety_health_malformed')
    for condition in value['conditions'].values():
        if not isinstance(condition, dict) or condition.get('status') not in ('ACTIVE', 'RESOLVED'):
            raise ValueError('safety_condition_malformed')
    return value


def _filesystem(root, recovery_root):
    mounts = [root, recovery_root]
    if len({p.stat().st_dev for p in mounts}) != 2:
        raise ValueError('recovery_filesystem_not_separate')
    for path in mounts:
        fs = os.statvfs(path)
        if fs.f_flag & os.ST_RDONLY or fs.f_bavail * fs.f_frsize < MIN_FREE_BYTES:
            raise ValueError('filesystem_readonly_or_capacity_low')


def _probe(path, role, journal=False):
    from .safety import SafetyObserver
    class PreserveHealth:
        def observe(self, *args, **kwargs):
            # Preflight is not the authority to resolve existing incidents.
            pass
    return SafetyObserver(PreserveHealth()).store(path, role, journal=journal)


def _protection(db, now):
    # Reuse the persisted monitor contract, not a fresh venue reader.
    from trader.engine.protection_snapshot import STALE_AFTER_S
    row = db.execute('SELECT boot,seq,value FROM protection_evidence WHERE slot=1').fetchone()
    if row is None:
        raise ValueError('protection_missing')
    s = strict_json(row['value'])
    if (s.get('schema') != 2 or type(row['boot']) is not int or type(row['seq']) is not int
            or s.get('generation') != dict(boot=row['boot'], seq=row['seq'])
            or s.get('status') != 'VERIFIED' or s.get('control_state_observed') != 'FROZEN'
            or s.get('complete_listing') is not True or type(s.get('mutations')) is not int or s['mutations'] != 0
            or not isinstance(s.get('checks'), dict)
            or any(s['checks'].get(key) is not True for key in CHECKS)):
        raise ValueError('protection_unverified')
    def stamp(value):
        result = datetime.fromisoformat(value)
        if result.tzinfo is None:
            raise ValueError('protection_clock_unknown')
        return result.timestamp()
    checked, completed = stamp(s['checked_at']), stamp(s['completed_at'])
    if (not math.isfinite(checked) or not math.isfinite(completed)
            or not 0 <= now - checked <= STALE_AFTER_S or not checked <= completed <= now):
        raise ValueError('protection_stale_or_future')
    cl = s.get('cleanliness')
    if (not isinstance(cl, dict) or cl.get('status') != 'CLEAN' or cl.get('items') != []
            or cl.get('items_truncated') is not False or cl.get('unread_symbols') != []):
        raise ValueError('protection_orders_or_coverage_uncertain')
    if (s.get('reasons') != [] or type(s.get('position_count')) is not int
            or s['position_count'] < 0 or not isinstance(s.get('symbols'), list)
            or len(s['symbols']) != s['position_count']):
        raise ValueError('protection_coverage_malformed')
    for item in s['symbols']:
        if (not isinstance(item, dict) or item.get('stop_present') is not True
                or item.get('quantity_agrees') is not True or item.get('precision_status') != 'VALID'
                or item.get('rearm_evidence') != 'NONE' or item.get('reasons') != []):
            raise ValueError('protection_position_uncertain')
    # Freshness alone cannot certify a book changed since publication. Bind
    # every current journal holding to the monitor's exact verified journal
    # fields; neither journal stop IDs nor an empty snapshot prove protection.
    trades = [dict(r) for r in db.execute(
        "SELECT id,symbol,side,amount,stop_loss,sl_order_id FROM trades WHERE status='open'")]
    expected = [(r['journal_trade_id'], r['symbol'], r['journal_side'], r['journal_amount'],
                 r['expected_stop'], r['journal_stop_id']) for r in s['symbols']]
    actual = [(r['id'], r['symbol'], r['side'], r['amount'], r['stop_loss'],
               str(r['sl_order_id']) if r['sl_order_id'] else None) for r in trades]
    if (len({r['symbol'] for r in trades}) != len(trades)
            or sorted(actual) != sorted(expected)):
        raise ValueError('protection_current_book_changed')
    return dict(status='PASS', reasons=[], generation=s['generation'], checked_at=s['checked_at'])


def collect_facts(root, *, recovery_root=RECOVERY_ROOT, clock=time.time):
    root, recovery_root = Path(root).resolve(), Path(recovery_root)
    facts = {}
    try:
        health = _health(root / 'data/safety_health.json')
        reasons = ['active_condition:' + name for name, c in health['conditions'].items()
                   if c['status'] == 'ACTIVE']
        if health['recovery_required'] is not True:
            reasons.append('recovery_latch_not_retained')
        facts['safety'] = _fact(reasons)
    except Exception:
        facts['safety'] = _fact(['safety_health_unreadable'])
    try:
        from trader.core.config import load_config, Env
        cfg = load_config(str(root / 'config.yaml'))
        reasons = []
        if Env.get('BINANCE_DEMO').lower() not in ('1', 'true', 'yes'):
            reasons.append('environment_not_demo')
        if cfg['brain']['enabled'] is not False:
            reasons.append('providers_not_disabled')
        if cfg['research']['referee'] is not False or cfg['research']['handoff'] is not False:
            reasons.append('research_gates_not_disabled')
        if not (root / 'data/watchdog.off').is_file():
            reasons.append('watchdog_hold_missing')
        _filesystem(root, recovery_root)
        facts['environment'] = _fact(reasons)
        stores = [(root / 'data/luffy.db', 'journal', True)]
        for store in cfg.get('safety_monitor', {}).get('critical_stores', []):
            path = (root / store['path']).resolve()
            if not path.is_relative_to(root) or not isinstance(store['role'], str):
                raise ValueError('critical_store_contract')
            stores.append((path, store['role'], False))
    except Exception:
        facts['environment'] = _fact(['environment_unreadable_or_failed'])
        stores = None
    # Refuse known faults before probing; a passing probe cannot erase an ACTIVE fault.
    if facts['safety']['status'] != 'PASS' or stores is None:
        facts['storage'] = _fact(['storage_not_probed_due_to_required_fact_failure'])
    else:
        try:
            results = [_probe(path, role, journal) for path, role, journal in stores]
            reasons = ['storage_unavailable:' + r['component'] for r in results
                       if r.get('status') != 'AVAILABLE' or r.get('committed') is not True]
            facts['storage'] = {**_fact(reasons), 'components': results}
        except Exception:
            facts['storage'] = _fact(['storage_unreadable_or_probe_failed'])
    try:
        from trader.engine.protection_snapshot import ro_connect
        with ro_connect(root / 'data/luffy.db', timeout_s=1) as db:
            controls = dict(db.execute("SELECT key,value FROM state_kv WHERE key IN "
                                       "('control_state','macro_guard_operator_hold','execution_recovery')"))
            reasons = []
            if controls.get('control_state') != 'FROZEN' or controls.get('macro_guard_operator_hold') != '1':
                reasons.append('frozen_operator_hold_required')
            # Absent recovery key is the existing ledger's no-pending-request state.
            if controls.get('execution_recovery') not in (None, '', 'null'):
                reasons.append('pending_execution_recovery')
            from trader.engine.reconcile import REARM_KEY
            rearm = db.execute('SELECT value FROM state_kv WHERE key=?', (REARM_KEY,)).fetchone()
            if rearm is not None and strict_json(rearm[0]) != {}:
                reasons.append('pending_or_unreadable_protection_rearm')
            if db.execute("SELECT count(*) FROM execution_requests WHERE state IS NULL OR state NOT IN ('TERMINAL','REFUSED')").fetchone()[0] != 0:
                reasons.append('pending_execution_requests')
            # Optional legacy partial ledger is explicitly inventoried, as in retained preflight.
            partial_exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='partial_exit_intents'").fetchone() is not None
            if partial_exists and db.execute("SELECT count(*) FROM partial_exit_intents WHERE state IS NULL OR state!='CONSUMED'").fetchone()[0] != 0:
                reasons.append('pending_partial_exits')
            facts['control'] = {**_fact(reasons), 'partial_ledger_present': partial_exists}
            facts['protection'] = _protection(db, clock())
        # Probe/publication races cannot waive a new persisted safety fault.
        health_after = _health(root / 'data/safety_health.json')
        if health_after != health:
            facts['safety'] = _fact(['safety_health_changed_during_preflight'])
    except Exception:
        facts.setdefault('control', _fact(['control_unreadable']))
        facts['protection'] = _fact(['protection_unreadable_or_unverified'])
    return facts


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--target', choices=('kernel', 'dashboard'), required=True)
    args = parser.parse_args(argv)
    try:
        result = evaluate(collect_facts(args.root))
        result['target'] = args.target
        encoded = json.dumps(result, sort_keys=True, allow_nan=False)
    except Exception:
        result = {**evaluate(None), 'target': args.target}
        encoded = json.dumps(result, sort_keys=True, allow_nan=False)
    print(encoded)
    return 0 if result['allow'] is True and result['result'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
