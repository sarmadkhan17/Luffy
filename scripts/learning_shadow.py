#!/usr/bin/env python3
"""Bounded read-only historical learning inventory. No live constructors/writes.

Journal outcomes without exact retained decision context remain non-authoritative.
The as-of clock is explicit, so rerunning the same snapshot produces the same
artifacts. Database reads use one SQLite snapshot and URI mode=ro.
"""
import argparse
from dataclasses import asdict
from pathlib import Path
import json
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from trader.learning import foundation as L
from trader.cognition import outcomes as O


def run(journal_path, output, as_of_ms, limit):
    if type(limit) is not int or limit <= 0:
        raise ValueError('explicit_positive_inventory_bound_required')
    db = sqlite3.connect(Path(journal_path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA query_only=ON')
    db.execute('BEGIN')
    counts = dict(realized=0, counterfactual=0, unresolved=0, unassessable=0,
        replay_complete=0, replay_incomplete=0, learning_evidence_objects=0,
        applicable_updates=0, insufficient_evidence=0, unregistered_rules=0,
        incomplete_replay_proposals=0)
    records = []
    try:
        total = db.execute('SELECT COUNT(*) FROM decisions').fetchone()[0]
        recent = db.execute('SELECT * FROM decisions ORDER BY ts DESC,id LIMIT ?', (limit,)).fetchall()
        executed = db.execute('SELECT * FROM decisions WHERE executed=1 ORDER BY ts DESC,id LIMIT ?', (limit,)).fetchall()
        resolved = db.execute('SELECT d.* FROM outcomes o JOIN decisions d ON d.id=o.decision_id WHERE o.resolved_at IS NOT NULL ORDER BY o.ts DESC,o.decision_id LIMIT ?', (limit,)).fetchall()
        selected = {r['id']: r for r in recent + executed + resolved}
        rows = sorted(selected.values(), key=lambda r: (r['ts'], r['id']))
        for row in rows:
            d = dict(row)
            ts = O.timestamp(d['ts'])
            if ts > as_of_ms:
                continue
            trades = [dict(t) for t in db.execute("SELECT * FROM trades WHERE decision_id=? ORDER BY id", (d['id'],))]
            fwd = db.execute('SELECT * FROM outcomes WHERE decision_id=?', (d['id'],)).fetchone()
            fwd = dict(fwd) if fwd else None
            if trades:
                kind, boundary, label = L.Kind.EXECUTED, L.Boundary.UNASSESSABLE, 'UNASSESSABLE'
                if any(t['status'] != 'closed' or not t.get('closed_at') or O.timestamp(t['closed_at']) > as_of_ms for t in trades):
                    boundary, label = L.Boundary.UNRESOLVED, 'UNRESOLVED'
                obs = dict(reason='legacy_journal_not_verified_venue_accounting', trade_ids=[t['id'] for t in trades])
            elif fwd and fwd.get('resolved_at') and ts < O.timestamp(fwd['resolved_at']) <= as_of_ms:
                kind = L.Kind.CASH if d.get('action') == 'HOLD' else L.Kind.REJECTED
                boundary, label = L.Boundary.COUNTERFACTUAL, 'SIMULATED / UNREALIZED'
                obs = dict(measurement='legacy_forward_return_not_money_or_verified_replay',
                    future_returns={k: fwd.get(k) for k in ('fwd_ret_1h', 'fwd_ret_4h', 'fwd_ret_24h')})
            else:
                kind = L.Kind.CASH if d.get('action') == 'HOLD' else L.Kind.REJECTED
                boundary, label = L.Boundary.UNRESOLVED, 'UNRESOLVED'
                obs = dict(reason='no_exact_resolved_future_measurement')
            measurement_ms = ts
            if boundary == L.Boundary.COUNTERFACTUAL:
                measurement_ms = O.timestamp(fwd['resolved_at'])
            elif trades:
                measurement_ms = max([ts] + [O.timestamp(t['closed_at']) for t in trades if t.get('closed_at') and O.timestamp(t['closed_at']) <= as_of_ms])
            frozen = dict(observation=obs, legacy_trades=trades, legacy_forward_outcome=fwd)
            snapshots = dict(decision=d, outcome=frozen)
            if trades:
                snapshots['trade'] = dict(trade_ids=[t['id'] for t in trades], records=trades)
            sources = tuple(L.Source(role, role + ':' + d['id'], L.digest(v), L.digest(v), None)
                            for role, v in snapshots.items())
            retained = {(s.source_id, s.version): snapshots[s.role] for s in sources}
            lineage = L.Lineage(d.get('cycle_id'), d['id'], None,
                trade_ids=tuple(t['id'] for t in trades))
            o = L.Outcome(kind, boundary, lineage, ts, measurement_ms, sources, L.canonical(obs), label)
            a, r = L.attribute(o), L.replay(o, retained)
            e = L.evidence(o, a, r)
            p = L.propose(e, L.Target.LIFECYCLE, dict(state='UNKNOWN'), rule=L.DECAY_RULE)
            counts[boundary.value.lower()] += 1
            counts['replay_complete' if r.status == 'COMPLETE' else 'replay_incomplete'] += 1
            counts['learning_evidence_objects'] += 1
            key = {L.Status.APPLICABLE: 'applicable_updates', L.Status.INSUFFICIENT: 'insufficient_evidence',
                   L.Status.UNREGISTERED: 'unregistered_rules', L.Status.INCOMPLETE: 'incomplete_replay_proposals'}.get(p.status)
            if key:
                counts[key] += 1
            records.append(dict(outcome=asdict(o), attribution=asdict(a), replay=asdict(r),
                                evidence=asdict(e), proposal=asdict(p)))
        summary = dict(status='PASS', as_of_ms=as_of_ms, source=str(Path(journal_path).resolve()),
            selection='union of latest decisions, executed decisions and resolved outcome decisions; per-stratum bound', total_decisions=total,
            selected_decisions=len(rows), processed=len(records), inventory_limit_per_stratum=limit,
            truncated=total > limit, counts=counts, events_sha256=L.digest(records), registered_rules=[L.DECAY_RULE],
            applicable_authority='NONE_IN_SHADOW', production_mutations=0,
            trading_behavior_changed=False,
            limitation='bounded legacy inventory; monetary truth, complete history and missing context not inferred')
        output.mkdir(parents=True, exist_ok=True)
        events = output / (L.digest(records) + '.json')
        payload = L.canonical(records) + '\n'
        try:
            with events.open('x') as f:
                f.write(payload)
        except FileExistsError:
            if events.read_text() != payload:
                raise ValueError('immutable_shadow_collision')
        summary['events_path'] = str(events)
        (output / 'summary.json').write_text(L.canonical(summary) + '\n')
        return summary
    finally:
        db.rollback()
        db.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--journal', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--as-of-ms', type=int, required=True)
    p.add_argument('--limit', type=int, required=True)
    a = p.parse_args()
    result = run(a.journal, a.output, a.as_of_ms, a.limit)
    print(L.canonical({k: v for k, v in result.items() if k != 'records'}))


if __name__ == '__main__':
    main()
