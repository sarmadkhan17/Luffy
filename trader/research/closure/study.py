"""P5 — bounded, resumable study orchestration.

A study is a durable list of units (intake -> develop* -> revise* -> freeze ->
qualify -> optional handoff) in the closure store. `step()` runs as many units
as fit inside a wall-clock budget, checkpointing every unit before and after
it runs; a crash re-runs only idempotent units, and the protected look is
guarded by the `looks` table and the spent-look ledger so it can never be
taken twice. Process status (did the job finish?) and research outcome (what
did qualification find?) are separate fields: completion never implies a pass.

It performs no market-cycle work, installs nothing, and writes no Journal
unless the spec names an isolated `journal_path` for the optional handoff.
"""
from __future__ import annotations

import fcntl
import json
import time
from contextlib import contextmanager
from pathlib import Path

from . import ClosureRefused, canonical, consumer, digest, exposure, families, manifest, qualification
from .development import develop
from .store import Store

MAX_UNIT_ATTEMPTS = 3
SPEC_KEYS = ('snapshot_path', 'tf', 'discovery_symbols', 'heldout_symbols', 'fit_start_ms', 'dev_end_ms', 'embargo_bars',
             'protected_start_ms', 'protected_end_ms', 'policy', 'risk', 'development_mode', 'seed_families',
             'bank_path', 'max_candidates', 'max_revisions', 'workdir', 'journal_path', 'budget_journal_path',
             'source_journal_path', 'book', 'unit_timeout_s')


def develop_job(payload: dict) -> dict:
    """Child-process entry for one candidate's development (pure: returns the
    result, the parent writes it). Module level so `spawn` can import it."""
    return develop(payload['candidate'], payload['snapshot_path'], payload['env'])


def _call(fn, payload, timeout_s, child):
    if not child:
        from ...core.child import ChildResult
        t0 = time.monotonic()
        try:
            return ChildResult(ok=True, value=fn(payload), elapsed_s=time.monotonic() - t0)
        except ClosureRefused as exc:
            return ChildResult(ok=False, error='refused:' + exc.code, elapsed_s=time.monotonic() - t0)
    from ...core.child import run_child
    return run_child(fn, payload, timeout_s=timeout_s, nice=19)


class UnitTimeout(Exception):
    def __init__(self, timeout_s, hit_cap):
        super().__init__(f'timed out after {timeout_s:.1f}s')
        self.timeout_s, self.hit_cap = timeout_s, hit_cap


@contextmanager
def single_flight(store):
    """One step per store at a time (scheduler re-entry, CLI + kernel)."""
    fh = open(str(store.path) + '.lock', 'a')
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def create_study(store: Store, spec: dict, *, now_ms: int) -> str:
    """Build manifest + envelope from the registry as it stands, record them,
    mark the development window exposed, and return the study id."""
    extra = set(spec) - set(SPEC_KEYS)
    if extra:
        raise ClosureRefused('study_spec_unknown_keys', ','.join(sorted(extra)))
    spec = dict(dict(bank_path=None, journal_path=None, seed_families=[], max_candidates=8, max_revisions=4,
                     development_mode='REPLAY', budget_journal_path=None, source_journal_path=None, book=[],
                     unit_timeout_s=900), **spec)
    spec_key = digest(spec)
    for row in store.studies():
        if json.loads(row['payload']).get('spec_key') == spec_key:
            return row['study_id']
    syms = spec['discovery_symbols'] + spec['heldout_symbols']
    m = manifest.build_manifest(spec['snapshot_path'], spec['tf'], syms, exposure.events(store))
    env = manifest.make_envelope(
        m, spec['snapshot_path'], policy=spec['policy'], discovery_symbols=spec['discovery_symbols'],
        heldout_symbols=spec['heldout_symbols'], fit_start_ms=spec['fit_start_ms'], dev_end_ms=spec['dev_end_ms'],
        embargo_bars=spec['embargo_bars'], protected_start_ms=spec['protected_start_ms'],
        protected_end_ms=spec['protected_end_ms'], risk=spec['risk'], development_mode=spec['development_mode'])
    store.put_once('manifests', m['manifest_id'], m)
    store.put_once('envelopes', env['envelope_id'], env)
    study = dict(spec, spec_key=spec_key, envelope_id=env['envelope_id'], manifest_id=m['manifest_id'])
    study_id = digest(study)
    with store._tx() as c:
        if not c.execute('SELECT 1 FROM studies WHERE study_id=?', (study_id,)).fetchone():
            c.execute('INSERT INTO studies(study_id,payload,status,last_progress_ms) VALUES (?,?,?,?)',
                      (study_id, canonical(study), 'CREATED', now_ms))
    exposure.mark(store, 'DEVELOPMENT_EXPOSED', spec['fit_start_ms'], spec['dev_end_ms'], now_ms,
                  f'development study {study_id[:12]}', symbol='*')
    return study_id


def _load(store, study_id):
    row = store.study(study_id)
    if row is None:
        raise ClosureRefused('study_unknown')
    study = json.loads(row['payload'])
    return study, store.get_once('envelopes', study['envelope_id'])


def _ensure_unit(store, study_id, unit_id, kind, deps=()):
    with store._tx() as c:
        if c.execute('SELECT 1 FROM units WHERE study_id=? AND unit_id=?', (study_id, unit_id)).fetchone():
            return False
        seq = c.execute('SELECT COALESCE(MAX(seq),0)+1 FROM units WHERE study_id=?', (study_id,)).fetchone()[0]
        c.execute('INSERT INTO units(study_id,unit_id,seq,kind,deps,status) VALUES (?,?,?,?,?,?)',
                  (study_id, unit_id, seq, kind, canonical(list(deps)), 'PENDING'))
        return True


def _set_unit(store, study_id, unit_id, status, now_ms, result=None, note=None, bump=False):
    with store._tx() as c:
        c.execute('UPDATE units SET status=?,result=COALESCE(?,result),note=?,attempts=attempts+?,updated_ms=? '
                  'WHERE study_id=? AND unit_id=?',
                  (status, canonical(result) if result is not None else None, note, 1 if bump else 0, now_ms,
                   study_id, unit_id))


def _results(store, units, kind):
    return [json.loads(u['result']) for u in units if u['kind'] == kind and u['status'] == 'DONE' and u['result']]


def _plan(store, study_id, study, env, now_ms):
    """Add the units the current state calls for (idempotent)."""
    units = store.units(study_id)
    ids = {u['unit_id'] for u in units}
    if study['bank_path'] and 'intake' not in ids:
        _ensure_unit(store, study_id, 'intake', 'intake')
    cands = []
    for fid in study['seed_families']:
        c = families.make_candidate(fid)
        store.put_once('candidates', c['candidate_id'], c)
        cands.append(c['candidate_id'])
    cands += [i['candidate_id'] for i in store.items() if i['status'] == 'COMPILED' and i['candidate_id']]
    seen = []
    for cid in cands:
        if cid not in seen:
            seen.append(cid)
    intake_done = not study['bank_path'] or any(u['unit_id'] == 'intake' and u['status'] == 'DONE' for u in units)
    if intake_done:
        for cid in seen[:study['max_candidates']]:
            _ensure_unit(store, study_id, f'develop:{cid}', 'develop', ['intake'] if study['bank_path'] else [])
    units = store.units(study_id)
    for u in units:
        if u['kind'] == 'develop' and u['status'] == 'DONE':
            r = json.loads(u['result'])
            if r['outcome'] in ('NEGATIVE_ECONOMICS', 'CODE_FAILURE', 'INCONCLUSIVE_POWER', 'BLOCKED_DATA'):
                _ensure_unit(store, study_id, f'revise:{r["result_id"]}', 'revise', [u['unit_id']])
    units = store.units(study_id)
    work = [u for u in units if u['kind'] in ('develop', 'revise')]
    if work and all(u['status'] in ('DONE', 'FAILED') for u in work) and intake_done:
        _ensure_unit(store, study_id, 'freeze', 'freeze', [u['unit_id'] for u in work])
    freeze = next((u for u in units if u['unit_id'] == 'freeze' and u['status'] == 'DONE'), None)
    if freeze is not None and json.loads(freeze['result']).get('freeze_id'):
        _ensure_unit(store, study_id, 'qualify', 'qualify', ['freeze'])
    q = next((u for u in units if u['unit_id'] == 'qualify' and u['status'] == 'DONE'), None)
    if q is not None and json.loads(q['result']).get('status') == 'QUALIFIED' and study['journal_path']:
        _ensure_unit(store, study_id, 'handoff', 'handoff', ['qualify'])


def _run_unit(store, study_id, study, env, unit, now_ms, run, *, timeout_s, child, cfg):
    kind, uid = unit['kind'], unit['unit_id']
    cap = float(study['unit_timeout_s'])
    hit_cap = timeout_s >= cap
    timeout_s = min(timeout_s, cap)
    if kind == 'intake':
        return consumer.consume(store, study['bank_path'], now_ms=now_ms)
    if kind == 'develop':
        cand = store.get_once('candidates', uid.split(':', 1)[1])
        hit = next((r for r in store.all_once('runs') if r.get('candidate_id') == cand['candidate_id']
                    and r.get('envelope_id') == env['envelope_id'] and r.get('evaluator_id') == env['evaluator_id']
                    and r.get('data_id') == env.get('development_data_id')), None)
        if hit is None and env['development_status'] != 'ELIGIBLE':
            hit = develop(cand, study['snapshot_path'], env, store=store)       # records the BLOCKED result, no heavy work
        if hit is None:
            res = _call(develop_job, dict(candidate=cand, snapshot_path=study['snapshot_path'], env=env), timeout_s, child)
            if res.timed_out:
                raise UnitTimeout(timeout_s, hit_cap)
            if not res.ok:
                raise ClosureRefused('develop_worker_failed', str(res.error)[-200:])
            hit = res.value
            if not isinstance(hit, dict) or 'cache_key' not in hit or 'result_id' not in hit:
                raise ClosureRefused('develop_worker_result_malformed')
            store.put_run(hit['cache_key'], hit)
        return {k: hit.get(k) for k in ('result_id', 'candidate_id', 'outcome', 'reason', 'cached', 'metrics')}
    if kind == 'revise':
        rid = uid.split(':', 1)[1]
        res = next(x for x in store.all_once('runs') if x.get('result_id') == rid)
        item = consumer.revise(store, res['candidate_id'], res, now_ms=now_ms, max_revisions=study['max_revisions'])
        return dict(item_id=item['item_id'], status=item['status'], reason=item['reason'], child=item['candidate_id'])
    if kind == 'freeze':
        f = qualification.freeze_finalist(store, env, store.all_once('runs'))
        return dict(freeze_id=f['freeze_id'] if f else None, finalist=f['candidate']['candidate_id'] if f else None,
                    reason=None if f else 'NO_FINALIST: no DEVELOPMENT_POSITIVE candidate')
    if kind == 'qualify':
        fz = store.get_once('freezes', json.loads(next(u for u in store.units(study_id)
                                                       if u['unit_id'] == 'freeze')['result'])['freeze_id'])
        timed = []

        def bounded(fn, payload, **_):
            from ...core.child import ChildResult
            r = run(fn, payload) if run is not None else _call(fn, payload, timeout_s, child)
            if getattr(r, 'timed_out', False):
                timed.append(1)
            return r if isinstance(r, ChildResult) or hasattr(r, 'ok') else ChildResult(ok=True, value=r)
        q = qualification.qualify(
            store, fz, env, study['snapshot_path'], Path(study['workdir']) / 'qualification', now_ms=now_ms,
            run=bounded, cfg=cfg, book=study['book'], source_journal=study['source_journal_path'],
            budget_journal=study['budget_journal_path'])
        return dict(status=q['status'], reason=q['reason'], qualification_id=q['qualification_id'],
                    evidence_id=q['evidence_id'], worker_timed_out=bool(timed))
    if kind == 'handoff':
        from ...core.journal import Journal
        from . import handoff
        q = store.get_once('qualifications', json.loads(next(u for u in store.units(study_id)
                                                             if u['unit_id'] == 'qualify')['result'])['qualification_id'])
        return handoff.hand_off(Journal(study['journal_path']), cfg, q)
    raise ClosureRefused('unit_kind_unknown', kind)


def step(store: Store, study_id: str, *, budget_s: float, now_ms: int | None = None, clock=time.monotonic,
         cycle_seconds: float | None = None, skip_if_cycle_s: float = 45.0, run=None, child: bool = True,
         should_stop=lambda: False, cfg: dict | None = None, allow_heavy: bool = True) -> dict:
    """Run units until the budget, the work, a stop request or a timeout ends
    the step. Heavy units execute in a killable child bounded by
    min(unit_timeout_s, time left), so even the FIRST unit cannot overrun."""
    with single_flight(store) as free:
        if not free:
            return dict(study_status(store, study_id), step='ALREADY_RUNNING')
        return _step(store, study_id, budget_s, now_ms, clock, cycle_seconds, skip_if_cycle_s, run, child,
                     should_stop, cfg, allow_heavy)


#: Units whose cost is bounded inside this process or by a killable child. Everything
#: else (bank/source verification, snapshot sealing, rotation capacity, the look)
#: needs an operator step: it must not run inside the live kernel callback.
KERNEL_SAFE = frozenset(('develop', 'revise', 'freeze'))


def _step(store, study_id, budget_s, now_ms, clock, cycle_seconds, skip_if_cycle_s, run, child, should_stop, cfg,
          allow_heavy=True):
    study, env = _load(store, study_id)
    if cfg is None:
        from ...core.config import load_config
        cfg = load_config()
    now_ms = Store.now_ms() if now_ms is None else now_ms
    if cycle_seconds is not None and cycle_seconds > skip_if_cycle_s:
        _record(store, study_id, 'DEFERRED', 'busy_market_cycle', now_ms, progress=False)
        return dict(study_status(store, study_id), step='DEFERRED')
    deadline = clock() + float(budget_s)
    ran, stop = 0, 'COMPLETE'
    while True:
        if should_stop():
            stop = 'STOP_REQUESTED'
            break
        _plan(store, study_id, study, env, now_ms)
        units = store.units(study_id)
        done = {u['unit_id'] for u in units if u['status'] in ('DONE', 'FAILED')}
        todo = [u for u in units if u['status'] in ('PENDING', 'RUNNING') and all(d in done for d in json.loads(u['deps']))]
        if not todo:
            stop = 'COMPLETE' if all(u['status'] in ('DONE', 'FAILED') for u in units) else 'BLOCKED_ON_DEPENDENCY'
            break
        remaining = deadline - clock()
        if remaining <= 1.0:
            stop = 'DEADLINE'
            break
        u = todo[0]
        if not allow_heavy and u['kind'] not in KERNEL_SAFE:
            stop = 'REQUIRES_OPERATOR_STEP'
            break
        prior = json.loads(u['result']) if u['result'] and u['status'] == 'PENDING' else {}
        if prior.get('timed_out') and remaining <= prior['timeout_s'] * 1.001 and u['attempts'] < MAX_UNIT_ATTEMPTS:
            stop = 'BUDGET_INSUFFICIENT'      # known to need more than this budget: do not relaunch and burn CPU
            break
        if u['attempts'] >= MAX_UNIT_ATTEMPTS:
            _set_unit(store, study_id, u['unit_id'], 'FAILED', now_ms, note='attempt budget exhausted')
            continue
        _set_unit(store, study_id, u['unit_id'], 'RUNNING', now_ms, bump=True)   # checkpoint BEFORE running
        t0 = clock()
        try:
            res = _run_unit(store, study_id, study, env, u, now_ms, run, timeout_s=remaining, child=child, cfg=cfg)
        except UnitTimeout as exc:
            partial = dict(timed_out=True, timeout_s=round(exc.timeout_s, 1), hit_unit_cap=exc.hit_cap)
            # EVERY timeout counts as an attempt, so retries are bounded whatever the budget/cap relation
            _set_unit(store, study_id, u['unit_id'], 'PENDING', now_ms, result=partial,
                      note='TIMEOUT at unit cap' if exc.hit_cap else 'TIMEOUT at step budget')
            stop = 'UNIT_TIMEOUT' if exc.hit_cap else 'DEADLINE'
            ran += 1
            break
        except ClosureRefused as exc:
            _set_unit(store, study_id, u['unit_id'], 'FAILED', now_ms, result=dict(refused=exc.code), note=str(exc)[:200])
            ran += 1
            continue
        _set_unit(store, study_id, u['unit_id'], 'DONE', now_ms, result=dict(res, elapsed_s=round(clock() - t0, 2)))
        ran += 1
    nxt = next((u['unit_id'] for u in store.units(study_id) if u['status'] in ('PENDING', 'RUNNING')), None)
    _record(store, study_id, 'COMPLETED' if stop == 'COMPLETE' else 'IN_PROGRESS', stop, now_ms, progress=ran > 0, nxt=nxt)
    return dict(study_status(store, study_id), step=stop, units_run=ran)


def _record(store, study_id, status, stop, now_ms, *, progress, nxt=None):
    with store._tx() as c:
        c.execute('UPDATE studies SET status=?,stop_reason=?,next_work=?,steps=steps+1,'
                  'last_progress_ms=CASE WHEN ? THEN ? ELSE last_progress_ms END WHERE study_id=?',
                  (status, stop, nxt, int(progress), now_ms, study_id))


def study_status(store: Store, study_id: str) -> dict:
    row = store.study(study_id)
    if row is None:
        return dict(study_id=study_id, process_status='UNKNOWN')
    units = store.units(study_id)
    counts = {}
    for u in units:
        counts[u['status']] = counts.get(u['status'], 0) + 1
    qual = next((json.loads(u['result']) for u in units if u['unit_id'] == 'qualify' and u['status'] == 'DONE'), None)
    frz = next((json.loads(u['result']) for u in units if u['unit_id'] == 'freeze' and u['status'] == 'DONE'), None)
    outcome = (qual or {}).get('status') or ('NO_FINALIST' if frz and not frz.get('freeze_id') else 'NOT_REACHED')
    return dict(study_id=study_id, process_status=row['status'], stop_reason=row['stop_reason'],
                last_progress_ms=row['last_progress_ms'], next_work=row['next_work'], steps=row['steps'],
                units=counts, research_outcome=outcome,
                research_reason=(qual or {}).get('reason') or (frz or {}).get('reason'),
                note='process_status says whether the job ran; research_outcome is the only evidence statement')


def scheduled(cfg: dict, cycle_seconds: float | None = None, *, slot_remaining_s: float | None = None,
              should_stop=lambda: False) -> dict:
    """Opt-in hook for the research slot (research.closure.enabled, default off).

    Deliberately small: it never builds a manifest or envelope (full-snapshot
    hashing/inventory) and never runs sealing, source verification, rotation
    capacity or a protected look inside the live callback. It only resumes an
    EXISTING study (created by `python -m trader.research.closure create`) with
    the bounded units (develop in a killable child, revise, freeze). The aggregate
    budget is therefore soft: the legacy step is bounded by its own child deadline,
    this slice by min(unit cap, remaining); no hard end-to-end deadline is claimed.
    It honours (never opens) the research gates; qualification is an operator step."""
    if should_stop():
        return dict(status='STOP_REQUESTED')
    c = ((cfg or {}).get('research') or {}).get('closure') or {}
    if not c.get('enabled', False):
        return dict(status='NOT_ENABLED')
    budget = float(c.get('budget_s', 600))
    if slot_remaining_s is not None:
        budget = min(budget, float(slot_remaining_s))
    if budget <= 1.0:
        return dict(status='DEFERRED', reason='aggregate_slot_budget_exhausted')
    store = Store(c['store_path'])
    try:
        if not c.get('study_id') or store.study(c['study_id']) is None:
            return dict(status='NOT_INITIALISED', reason='run: python -m trader.research.closure create (operator step)')
        return step(store, c['study_id'], budget_s=budget, cycle_seconds=cycle_seconds, should_stop=should_stop,
                    cfg=cfg, allow_heavy=False)
    finally:
        store.close()
