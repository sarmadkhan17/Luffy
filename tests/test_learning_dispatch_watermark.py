"""P1: incremental learning dispatch (Watermark) on its own thread.

The reference is the original full anti-join (`dispatch.pending` without a
watermark). The rule engine is replaced by a receipt-writing fake: this tests
selection, ordering, idempotence and recovery, not learning policy.
"""
import collections
import json
import logging
import random
from types import SimpleNamespace as NS

import pytest

from trader.core.journal import Journal
from trader.learning import capture as C, dispatch as D, foundation as L, runtime as R


class World:
    """Receipt-writing stand-in for the rule engine, with fault injection."""
    def __init__(self, journal):
        self.journal, self.digest, self.fail, self.dups, self.order = journal, 'digest-A', set(), 0, []
        self.now = 0.0

    def clock(self):
        return self.now

    def add_chain(self, outcome_id, gate='REPLAY_COMPLETE'):
        body = dict(replay_gate=gate, evidence=dict(outcome_id=outcome_id), outcome={}, authoritative=True)
        with self.journal._tx() as c:
            C.ensure(c)
            C.insert(c, 'learning_produced_chains', 'outcome_id', outcome_id, (outcome_id, L.canonical(body)))

    def dispatch_evidence(self, journal, cfg, ev, *, at_ms, registry=None, shadow=False):
        if ev.outcome_id in self.fail:
            raise RuntimeError('injected dispatch failure')
        D.ensure(journal)
        dispatch_id = f'{ev.outcome_id}:{self.digest}'
        if journal.query(f'SELECT 1 FROM {D.TABLE} WHERE dispatch_id=?', (dispatch_id,)):
            self.dups += 1
            return dict(outcome_id=ev.outcome_id, results=[], duplicate=True)
        with journal._tx() as c:
            c.execute(f'INSERT INTO {D.TABLE} VALUES(?,?,?,?,?)',
                      (dispatch_id, ev.outcome_id, self.digest, '{}', 'x'))
        self.order.append(ev.outcome_id)
        return dict(outcome_id=ev.outcome_id, results=[dict(result='NO_PROPOSAL', reason='UNREGISTERED')],
                    duplicate=False)

    def receipts(self, digest=None):
        D.ensure(self.journal)
        return {r['outcome_id'] for r in self.journal.query(
            f'SELECT outcome_id FROM {D.TABLE} WHERE registry_digest=?', (digest or self.digest,))}

    def reference(self, limit):
        return [r['outcome_id'] for r in D.pending(self.journal, self.digest, limit)]


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(Journal(tmp_path / 'j.db'))
    monkeypatch.setattr(D, 'dispatch_evidence', w.dispatch_evidence)
    monkeypatch.setattr(D, 'registry_digest', lambda rules: w.digest)
    monkeypatch.setattr(R, 'evidence_from_body', lambda body: NS(outcome_id=body['outcome_id']))
    return w


def step(w, wm, limit):
    return D.dispatch_pending(w.journal, {}, at_ms=1, max_work=limit, watermark=wm)


@pytest.mark.parametrize('seed', range(8))
def test_incremental_dispatch_equals_the_full_scan_under_random_events(world, seed):
    """Inserts, failures, restarts, digest changes and sweeps: the incremental
    step dispatches exactly what the original query would, in the same order."""
    rng, w = random.Random(seed), world
    wm, seen, limit = D.Watermark(sweep_s=3600, clock=w.clock), set(), rng.choice([1, 3, 8])
    for _ in range(260):
        op = rng.choices(['insert', 'dispatch', 'fail', 'restart', 'digest', 'time', 'ineligible'],
                         [4, 8, 1, 1, 1, 2, 1])[0]
        if op in ('insert', 'ineligible'):
            oid = f'o{rng.randrange(10**6):06d}'
            if oid not in seen:
                seen.add(oid)
                w.add_chain(oid, 'REPLAY_COMPLETE' if op == 'insert' else 'REPLAY_INCOMPLETE')
        elif op == 'restart':
            wm = D.Watermark(sweep_s=3600, clock=w.clock)
        elif op == 'digest':
            w.digest = f'digest-{rng.randrange(3)}'
        elif op == 'time':
            w.now += rng.choice([10, 1800, 4000, 30000])
        elif op == 'fail':
            ahead = w.reference(limit)
            if ahead:
                w.fail = {rng.choice(ahead)}
                with pytest.raises(RuntimeError):
                    step(w, wm, limit)
                w.fail = set()
        else:
            expected = w.reference(limit)
            before = len(w.order)
            assert [r['outcome_id'] for r in step(w, wm, limit)] == expected
            assert w.order[before:] == expected
    assert w.dups == 0 and wm.misses == 0
    for _ in range(400):                       # drain: nothing eligible is left undispatched
        if not step(w, wm, limit):
            break
    eligible = {r['outcome_id'] for r in w.journal.query(
        "SELECT outcome_id FROM learning_produced_chains WHERE json_extract(payload,'$.replay_gate')='REPLAY_COMPLETE'")}
    assert w.receipts() == eligible and w.dups == 0 and not w.reference(64)


def test_idle_cycles_skip_the_history_scan(world, monkeypatch):
    w = world
    for i in range(5):
        w.add_chain(f'o{i}')
    wm = D.Watermark(clock=w.clock)
    assert len(step(w, wm, 8)) == 5 and wm.floor == 5
    calls = []
    real = D.pending
    monkeypatch.setattr(D, 'pending', lambda *a, **k: calls.append(1) or real(*a, **k))
    assert [step(w, wm, 8) for _ in range(50)] == [[]] * 50
    assert calls == []                          # one MAX(rowid) read per idle poll, no scan
    w.add_chain('o9')
    assert [r['outcome_id'] for r in step(w, wm, 8)] == ['o9'] and len(calls) == 1


def test_failed_dispatch_keeps_the_floor_and_is_retried_once(world):
    w = world
    for i in range(3):
        w.add_chain(f'o{i}')
    wm = D.Watermark(clock=w.clock)
    w.fail = {'o1'}
    with pytest.raises(RuntimeError):
        step(w, wm, 8)
    assert wm.floor == 0 and w.receipts() == {'o0'}     # o0 landed before the failure
    w.fail = set()
    assert [r['outcome_id'] for r in step(w, wm, 8)] == ['o1', 'o2']
    assert w.receipts() == {'o0', 'o1', 'o2'} and w.dups == 0 and wm.floor == 3


def test_full_batch_does_not_advance_past_unseen_pending_rows(world):
    w = world
    for i in range(5):
        w.add_chain(f'o{i}')
    wm = D.Watermark(clock=w.clock)
    assert len(step(w, wm, 3)) == 3 and wm.floor == 0
    assert len(step(w, wm, 3)) == 2 and wm.floor == 5
    assert step(w, wm, 3) == []


def test_digest_change_and_restart_rescan_from_zero(world):
    w = world
    for i in range(4):
        w.add_chain(f'o{i}')
    wm = D.Watermark(clock=w.clock)
    step(w, wm, 8)
    w.digest = 'digest-B'                        # new registry: everything is pending again
    assert len(step(w, wm, 8)) == 4 and wm.floor == 4
    restarted = D.Watermark(clock=w.clock)       # restart: no state, finds nothing missing
    assert step(w, restarted, 8) == [] and restarted.floor == 4 and w.dups == 0


def test_sweep_detects_and_repairs_a_watermark_miss(world, caplog):
    """A renumbered rowid (e.g. an offline VACUUM) can put a pending chain below
    the floor; the periodic sweep finds it, counts it and dispatches it."""
    w = world
    w.add_chain('o0')
    wm = D.Watermark(sweep_s=100, clock=w.clock)
    step(w, wm, 8)
    w.add_chain('o1')
    wm.floor = 50                                # simulate the corrupted invariant
    assert step(w, wm, 8) == []                  # missed by the fast path ...
    w.now += 101
    with caplog.at_level(logging.ERROR):
        assert [r['outcome_id'] for r in step(w, wm, 8)] == ['o1']   # ... caught by the sweep
    assert wm.misses == 1 and 'watermark miss' in caplog.text and wm.sweeps == 2
    assert w.receipts() == {'o0', 'o1'}


def test_shadow_and_plain_calls_keep_the_original_full_scan(world, monkeypatch):
    w = world
    w.add_chain('o0')
    wm = D.Watermark(clock=w.clock)
    seen = []
    real = D.pending
    monkeypatch.setattr(D, 'pending', lambda *a, **k: seen.append(k.get('after_rowid', 'full')) or real(*a, **k))
    D.dispatch_pending(w.journal, {}, at_ms=1, max_work=8, shadow=True, watermark=wm)
    D.dispatch_pending(w.journal, {}, at_ms=1, max_work=8)
    assert seen == ['full', 'full'] and wm.floor == 0 and wm.digest is None
    w.add_chain('o1')
    assert [sorted(r) for r in real(w.journal, w.digest, 8)] == [['outcome_id', 'payload']]   # legacy row shape
    assert [sorted(r) for r in real(w.journal, w.digest, 8, after_rowid=0)] == [['outcome_id', 'payload', 'rowid']]


# The earlier branch-specific thread tests are replaced by maintenance worker tests.

def test_worker_owns_watermark_and_drains_bounded_batches(monkeypatch):
    from trader.scheduling.maintenance import Maintenance
    m = Maintenance(object(), {}, object())
    calls = []
    def dispatch(*args, **kwargs):
        calls.append(kwargs)
        return [{}] * (8 if len(calls) == 1 else 1)
    monkeypatch.setattr(D, 'dispatch_pending', dispatch)
    w = m.jobs['learning']
    assert w.run_once()
    assert w.snapshot()['pending']
    assert w.run_once()
    assert not w.snapshot()['pending']
    assert all(c['watermark'] is m.watermark and c['max_work'] == 8 for c in calls)


def test_learning_worker_failure_remains_visible_and_retries(monkeypatch):
    from trader.scheduling.maintenance import Maintenance
    m = Maintenance(object(), {}, object())
    def fail(*args, **kwargs):
        raise RuntimeError('db busy')
    monkeypatch.setattr(D, 'dispatch_pending', fail)
    w = m.jobs['learning']
    w.run_once()
    assert w.snapshot()['failures'] == 1
    assert w.snapshot()['last_success_at'] is None
    monkeypatch.setattr(D, 'dispatch_pending', lambda *a, **k: [])
    w.trigger()
    w.run_once()
    assert w.snapshot()['successes'] == 1


def test_trade_close_wakes_outcomes_and_learning():
    from trader.scheduling.maintenance import Maintenance
    m = Maintenance(object(), {}, object())
    for job in m.jobs.values():
        job._pending = None
    m.trade_closed()
    assert m.jobs['learning'].snapshot()['pending']
    assert m.jobs['outcomes'].snapshot()['pending']
    assert not m.jobs['reports'].snapshot()['pending']
