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
from contextlib import contextmanager, closing
from pathlib import Path

from ..compose import catalog, graph, search as csearch
from . import ClosureRefused, canonical, consumer, data_access, digest, exposure, families, hedge_qualification, manifest, qualification
from .development import develop
from .store import Store
from ...core.types import TF_MS

MAX_UNIT_ATTEMPTS = 3
TERMINAL_UNIT_STATUSES = frozenset(('DONE','FAILED','POSTPONED'))
SPEC_KEYS = ('snapshot_path', 'tf', 'discovery_symbols', 'heldout_symbols', 'fit_start_ms', 'dev_end_ms', 'embargo_bars',
             'protected_start_ms', 'protected_end_ms', 'policy', 'risk', 'development_mode', 'seed_families',
             'bank_path', 'max_candidates', 'max_revisions', 'workdir', 'journal_path', 'budget_journal_path',
             'source_journal_path', 'book', 'unit_timeout_s', 'derivs_path', 'world_path', 'seed_graphs', 'search', 'proposal_mode',
             'references_path', 'origin', 'protected_derivs_path','universe_path','universe_environment','independent_window_assigned')


def develop_job(payload: dict) -> dict:
    """Child-process entry for one candidate's development (pure: returns the
    result, the parent writes it). Module level so `spawn` can import it."""
    return develop(payload['candidate'], payload['snapshot_path'], payload['env'], derivs_path=payload.get('derivs_path'), world_path=payload.get('world_path'),
                   references_path=payload.get('references_path'), cache_path=payload.get('store_path'),
                   universe_path=payload.get('universe_path'),universe_environment=payload.get('universe_environment','demo'))


def intake_job(payload: dict) -> dict:
    """Child entry for question intake; opens its own Store connection (WAL) and writes through it."""
    store = Store(payload['store_path'])
    try:
        source_intake = None
        if payload.get('brain_source_path'):
            source_intake = refresh_question_bank(payload['brain_source_path'], payload['bank_path'],
                                                  now_ms=payload['now_ms'], forbidden_paths=payload.get('source_paths', ()))
        proposer = None
        if payload.get('proposal_mode') == 'compositional':
            from ...brain.llm import BrainLLM
            from ..compose.proposer import GraphProposer
            learning_tasks = []
            if payload.get('brain_source_path'):
                from ...engine.protection_snapshot import ro_connect
                from ...learning.research_policy import tasks
                with closing(ro_connect(payload['brain_source_path'])) as db:
                    class Reads:
                        def query(self, sql, args=()):
                            return [dict(r) for r in db.execute(sql, args)]
                    learning_tasks = tasks(Reads(), at_ms=payload['now_ms'])
            from copy import deepcopy
            provider_cfg=deepcopy(payload['cfg'])
            closure=(provider_cfg.get('research') or {}).get('closure') or {}
            if closure.get('proposal_provider_enabled',False):
                provider_cfg.setdefault('brain',{})['enabled']=True
            proposer = GraphProposer(BrainLLM(provider_cfg), store, tf=payload['tf'], study_id=payload.get('study_id'),
                                     learning_tasks=learning_tasks)
        result = consumer.consume(store, payload['bank_path'], now_ms=payload['now_ms'], proposer=proposer, study_id=payload.get('study_id'))
        if source_intake is not None:
            result['source_intake'] = source_intake
        return result
    finally:
        store.close()


def refresh_question_bank(source_path, bank_path, *, now_ms, forbidden_paths=()):
    """Use the maintained question-only Brain intake, with Bank-owned consumption.

    No bridge experiment or qualification is run, and the source Journal is
    never written. Called only in the deadline-bounded intake child.
    """
    from .. import brain_intake, predictive_bridge
    destination = Path(bank_path).resolve()
    for value in (source_path, *forbidden_paths):
        if not value:
            continue
        source = Path(value).resolve()
        if destination == source or (destination.exists() and source.exists() and destination.samefile(source)):
            raise ClosureRefused('source_destination_alias')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with open(str(destination) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with closing(predictive_bridge.store(destination)) as bank:
            return brain_intake.intake(source_path, bank, predictive_bridge.append, now_ms=now_ms, limit=8)


def search_job(payload: dict) -> dict:
    """Child entry for the budgeted search plan (measure coverage, admit candidates)."""
    store = Store(payload['store_path'])
    try:
        study = payload['study']
        measured = catalog.measure(study['snapshot_path'], study.get('derivs_path'), study.get('references_path'),
                                   study.get('world_path'))
        inv = catalog.inventory(measured)
        status = {r['key']: r['status'] for d in inv['domains'].values() for r in d['factors']}
        budget = csearch.register_budget(store, payload['study_id'],
                                         dict(study['search'], tf=study['tf'], symbols=max(len(study.get('discovery_symbols') or []), 1)))
        out = csearch.plan(store, payload['study_id'], budget, status, units=store.units(payload['study_id']),
                           now_ms=payload['now_ms'])
        return dict(admitted=len(out['admitted']), stopped=out['stopped'], spent=out['spent'], inventory_id=digest(inv),
                    requirements=len(csearch.requirements_for(status)), budget_memory_mb=budget['memory_mb'])
    finally:
        store.close()


def _limits(study) -> dict:
    """Resource ceilings enforced on every child (OS rlimits), from the study's registered search budget."""
    s = study.get('search') or {}
    return dict(memory_mb=int(s.get('memory_mb', csearch.DEFAULT_BUDGET['memory_mb'])))


def _call(fn, payload, timeout_s, child, limits=None, should_stop=None):
    if not child:
        from ...core.child import ChildResult
        t0 = time.monotonic()
        try:
            return ChildResult(ok=True, value=fn(payload), elapsed_s=time.monotonic() - t0)
        except ClosureRefused as exc:
            return ChildResult(ok=False, error='refused:' + exc.code, elapsed_s=time.monotonic() - t0)
    from ...core.child import run_child
    return run_child(fn, payload, timeout_s=timeout_s, nice=19, **(limits or {}),
                     stop_requested=should_stop,
                     **({'cpu_seconds': timeout_s * 1.5} if limits else {}))


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
        if not reconcile_child(store):
            yield False
            return
        yield True
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def fence_child(store, result, **attempt):
    body = dict(pid=getattr(result, 'pid', None), start_ticks=getattr(result, 'start_ticks', None),
                artifact_dir=getattr(result, 'artifact_dir', None),
                pending_paths=list(getattr(result, 'owned_pending', ())))
    body.update(attempt)
    if body.get('study_id') and body.get('unit_id'):
        row = store._conn().execute('SELECT seq FROM resource_ledger WHERE study_id=? AND unit_id=? '
                                     "AND outcome='RUNNING' ORDER BY seq DESC LIMIT 1",
                                     (body['study_id'], body['unit_id'])).fetchone()
        body['resource_seq'] = row[0] if row else None
    path = Path(str(store.path) + '.child-unreaped')
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(canonical(body))
    pending.replace(path)


def reconcile_child(store):
    """Clear only proven cessation, retaining attempt/commit/look history."""
    path = Path(str(store.path) + '.child-unreaped')
    if not path.exists():
        return True
    from ...core.child import process_state, completed_artifact
    try:
        body = json.loads(path.read_text())
        if not isinstance(body.get('pid'), int) or body.get('start_ticks') is None:
            return False
        state = process_state(body['pid'])
        if state is not None and state[1] == body['start_ticks'] and state[0] not in ('Z', 'X'):
            return False
    except (OSError, ValueError, KeyError, TypeError):
        return False
    if str(body.get('unit_id', '')).startswith('develop:') and body.get('artifact_dir'):
        try:
            result = completed_artifact(body['artifact_dir'])
            spec, env = _load(store, body['study_id'])
            semantic = {k: v for k, v in result.items() if k not in ('result_id', 'cached')}
            if (result.get('stage') == 'DEVELOPMENT' and result.get('envelope_id') == env['envelope_id']
                    and result.get('candidate_id') == body['unit_id'].split(':', 1)[1]
                    and result.get('result_id') == digest(semantic)):
                if store.get_run(result['cache_key']) is None:
                    store.put_run(result['cache_key'], {k: v for k, v in result.items() if k != 'cached'})
        except (OSError, ValueError, KeyError, TypeError, ClosureRefused):
            pass  # no invented commit; existing interrupted-unit recovery applies
    store.log_schedule(Store.now_ms(), 'CHILD_CESSATION_VERIFIED', study_id=body.get('study_id'), detail=body)
    import shutil
    for value in body.get('pending_paths') or ():
        pending = Path(value)
        if not pending.is_symlink():
            if pending.name.startswith('.preparing-') and pending.is_dir():
                shutil.rmtree(pending)
            elif pending.name.startswith('world-') and pending.suffix == '.pending':
                pending.unlink(missing_ok=True)
    directory = Path(body['artifact_dir']) if body.get('artifact_dir') else None
    if directory is not None and directory.name.startswith('luffy-child-') and not directory.is_symlink():
        shutil.rmtree(directory, ignore_errors=True)
    path.unlink()
    return True


def create_study(store: Store, spec: dict, *, now_ms: int, prepared_manifest=None) -> str:
    """Build manifest + envelope from the registry as it stands, record them,
    mark the development window exposed, and return the study id."""
    extra = set(spec) - set(SPEC_KEYS)
    if extra:
        raise ClosureRefused('study_spec_unknown_keys', ','.join(sorted(extra)))
    spec = dict(dict(bank_path=None, journal_path=None, seed_families=[], max_candidates=8, max_revisions=4,
                     development_mode='REPLAY', budget_journal_path=None, source_journal_path=None, book=[],
                     unit_timeout_s=900, derivs_path=None, world_path=None, seed_graphs=[], search=None, proposal_mode='compositional'), **spec)
    if spec.get('references_path') is not None and spec['development_mode'] != 'RETROSPECTIVE':
        raise ClosureRefused('references_path_requires_retrospective_development')
    spec_key = digest(spec)          # references_path has no default: it joins the identity only when supplied
    for row in store.studies():
        if json.loads(row['payload']).get('spec_key') == spec_key:
            return row['study_id']
    syms = spec['discovery_symbols'] + spec['heldout_symbols']
    events = exposure.events(store)
    if prepared_manifest is None:
        m = manifest.build_manifest(spec['snapshot_path'], spec['tf'], syms, events)
    else:
        manifest.verify_manifest(prepared_manifest)
        manifest.require_unchanged(spec['snapshot_path'], prepared_manifest['snapshot'])
        if (prepared_manifest['tf'] != spec['tf'] or
                prepared_manifest['exposure_events'] != sorted(events, key=canonical) or
                not set(syms) <= set(prepared_manifest['symbols'])):
            raise ClosureRefused('prepared_manifest_scope_mismatch')
        body = {k: v for k, v in prepared_manifest.items() if k != 'manifest_id'}
        body['symbols'] = {s: prepared_manifest['symbols'][s] for s in sorted(set(syms))}
        m = dict(body, manifest_id=digest(body))
    from .calibration import search_contract
    contract = search_contract(search=spec['search'],max_candidates=spec['max_candidates'],
                               max_revisions=spec['max_revisions'],tf=spec['tf'],intake=bool(spec.get('bank_path')),
                               development_bars=(spec['dev_end_ms']-spec['fit_start_ms'])//TF_MS[spec['tf']])
    env = manifest.make_envelope(
        m, spec['snapshot_path'], policy=spec['policy'], discovery_symbols=spec['discovery_symbols'],
        heldout_symbols=spec['heldout_symbols'], fit_start_ms=spec['fit_start_ms'], dev_end_ms=spec['dev_end_ms'],
        embargo_bars=spec['embargo_bars'], protected_start_ms=spec['protected_start_ms'],
        protected_end_ms=spec['protected_end_ms'], risk=spec['risk'], development_mode=spec['development_mode'],search_contract_id=digest(contract),
        independent_window_assigned=spec.get('independent_window_assigned',True))
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


def _owned(store, study_id, item) -> bool:
    """A compiled question/revision belongs to the study that consumed it. Items recorded before ownership existed are
    attributed only when the store holds exactly ONE study, so a second study can never inherit another's development
    lineage (or its feedback) by accident."""
    owner = json.loads(item['payload']).get('study_id')
    return owner == study_id if owner else len(store.studies()) == 1


def _plan(store, study_id, study, env, now_ms):
    """Add the units the current state calls for (idempotent)."""
    units = store.units(study_id)
    ids = {u['unit_id'] for u in units}
    if study['bank_path'] and 'intake' not in ids:
        _ensure_unit(store, study_id, 'intake', 'intake')
    queues = {'seeds': [], 'retained': []}
    for fid in study['seed_families']:
        c = families.make_candidate(fid)
        store.put_once('candidates', c['candidate_id'], c)
        queues['seeds'].append(c['candidate_id'])
    for gs in study.get('seed_graphs') or []:
        g = graph.make_candidate(gs)
        store.put_once('candidates', g['candidate_id'], g)
        queues['seeds'].append(g['candidate_id'])
    if study.get('search') is not None:
        if 'search' not in ids:
            _ensure_unit(store, study_id, 'search', 'search', ['intake'] if study['bank_path'] else [])
        if any(u['unit_id'] == 'search' and u['status'] == 'DONE' for u in units):
            for attempt in store.attempts_for(study_id):
                track = str(attempt.get('track') or attempt.get('domain', 'search'))
                queues.setdefault('search:' + track, []).append(attempt['candidate_id'])
    owned = store.owned_items(study_id)
    queues['retained'] = [i['candidate_id'] for i in owned if i['kind'] != 'revision'
                          and i['status'] == 'COMPILED' and i['candidate_id']]
    seen = []
    # Round-robin source/track queues before applying the evaluation cap. A
    # large generated stream must not starve retained questions or model tracks.
    order = sorted(queues, key=lambda k: 0 if k in ('seeds', 'retained') else
                   1 if k == 'search:interaction' else 2 if k.startswith('search:learned-') or k == 'search:equal-weight' else 3)
    while any(queues.values()):
        for key in order:
            queue = queues[key]
            if queue:
                cid = queue.pop(0)
                if cid not in seen:
                    seen.append(cid)
    intake_done = not study['bank_path'] or any(u['unit_id'] == 'intake' and u['status'] == 'DONE' for u in units)
    search_done = study.get('search') is None or any(u['unit_id'] == 'search' and u['status'] in ('DONE', 'FAILED') for u in units)
    if intake_done and search_done:
        for cid in seen[:study['max_candidates']]:
            _ensure_unit(store, study_id, f'develop:{cid}', 'develop', ['intake'] if study['bank_path'] else [])
        # Revisions have their own bounded per-root count; they must not be
        # stranded behind the already selected roots' evaluation cap.
        for item in owned:
            if item['kind'] == 'revision' and item['status'] == 'COMPILED' and item['candidate_id']:
                _ensure_unit(store, study_id, f"develop:{item['candidate_id']}", 'develop')
    units = store.units(study_id)
    for u in units:
        if u['kind'] == 'develop' and u['status'] == 'DONE':
            r = json.loads(u['result'])
            if r['outcome'] in ('NEGATIVE_ECONOMICS', 'CODE_FAILURE', 'INCONCLUSIVE_POWER', 'BLOCKED_DATA'):
                _ensure_unit(store, study_id, f'revise:{r["result_id"]}', 'revise', [u['unit_id']])
    units = store.units(study_id)
    work = [u for u in units if u['kind'] in ('develop', 'revise')]
    if work and all(u['status'] in TERMINAL_UNIT_STATUSES for u in work) and intake_done:
        _ensure_unit(store, study_id, 'freeze', 'freeze', [u['unit_id'] for u in work])
    freeze = next((u for u in units if u['unit_id'] == 'freeze' and u['status'] == 'DONE'), None)
    if freeze is not None and json.loads(freeze['result']).get('freeze_id'):
        _ensure_unit(store, study_id, 'qualify', 'qualify', ['freeze'])
    q = next((u for u in units if u['unit_id'] == 'qualify' and u['status'] == 'DONE'), None)
    if q is not None and json.loads(q['result']).get('status') == 'QUALIFIED' and study['journal_path']:
        _ensure_unit(store, study_id, 'handoff', 'handoff', ['qualify'])


def _run_unit(store, study_id, study, env, unit, now_ms, run, *, timeout_s, child, cfg, should_stop=None):
    kind, uid = unit['kind'], unit['unit_id']
    cap = float(study['unit_timeout_s'])
    hit_cap = timeout_s >= cap
    timeout_s = min(timeout_s, cap)
    if kind in ('intake', 'search'):
        job, body = (intake_job, dict(store_path=str(store.path), bank_path=study['bank_path'], now_ms=now_ms, tf=study['tf'],
                                      proposal_mode=study.get('proposal_mode'), cfg=cfg, study_id=study_id,
                                      brain_source_path=study.get('source_journal_path'),
                                      source_paths=[str(store.path), study['snapshot_path'], study.get('derivs_path')])) if kind == 'intake' else \
            (search_job, dict(store_path=str(store.path), study=study, study_id=study_id, now_ms=now_ms))
        res = _call(job, body, timeout_s, child, _limits(study), should_stop)
        if not getattr(res, 'terminated', True):
            fence_child(store, res, study_id=study_id, unit_id=uid)
            _set_unit(store, study_id, uid, 'RUNNING', now_ms, note='CHILD_NOT_REAPED')
            raise RuntimeError('research_child_not_reaped: ownership unresolved')
        if res.timed_out or getattr(res, 'cancelled', False):
            raise UnitTimeout(timeout_s, hit_cap)
        if not res.ok:
            raise ClosureRefused(f'{kind}_worker_failed', str(res.error)[-200:])
        return res.value
    if kind == 'develop':
        cand = store.get_once('candidates', uid.split(':', 1)[1])
        hit = None if graph.is_graph(cand) or env['risk'].get('real_funding', False) or study.get('universe_path') else next((r for r in store.runs_for(candidate_id=cand['candidate_id']) if r.get('candidate_id') == cand['candidate_id']
                    and r.get('envelope_id') == env['envelope_id'] and r.get('evaluator_id') == env['evaluator_id']
                    and r.get('data_id') == env.get('development_data_id')), None)
        if hit is None and env['development_status'] != 'ELIGIBLE':
            hit = develop(cand, study['snapshot_path'], env, store=store,       # records the BLOCKED result, no heavy work
                          universe_path=study.get('universe_path'),universe_environment=study.get('universe_environment','demo'),
                          references_path=study.get('references_path'))
        if hit is None:
            res = _call(develop_job, dict(candidate=cand, snapshot_path=study['snapshot_path'], env=env,
                                          store_path=str(store.path),
                                          derivs_path=study.get('derivs_path'), world_path=study.get('world_path'),
                                          universe_path=study.get('universe_path'),universe_environment=study.get('universe_environment','demo'),
                          references_path=study.get('references_path')), timeout_s, child, _limits(study), should_stop)
            if not getattr(res, 'terminated', True):
                fence_child(store, res, study_id=study_id, unit_id=uid)
                _set_unit(store, study_id, uid, 'RUNNING', now_ms, note='CHILD_NOT_REAPED')
                raise RuntimeError('research_child_not_reaped: ownership unresolved')
            if res.timed_out or getattr(res, 'cancelled', False):
                raise UnitTimeout(timeout_s, hit_cap)
            if not res.ok:
                raise ClosureRefused('develop_worker_failed', str(res.error)[-200:])
            hit = res.value
            if not isinstance(hit, dict) or 'cache_key' not in hit or 'result_id' not in hit:
                raise ClosureRefused('develop_worker_result_malformed')
            if not hit.get('cached'):
                store.put_run(hit['cache_key'], hit)
        awaited, required = data_access.record(store, study_id, study, env, cand, hit, now_ms)
        out = dict({k: hit.get(k) for k in ('result_id', 'candidate_id', 'outcome', 'reason', 'cached', 'metrics',
                                            'requires', 'context_gaps', 'context_provenance', 'evidence_scope',
                                            'validation_readiness')},
                   diagnosis=csearch.DIAGNOSIS.get(hit.get('outcome')))
        if awaited:
            out['waiting_data'] = awaited          # checkpoint: this candidate waits for the owner; others proceed
            out['waiting_requirements'] = required
        return out
    if kind == 'revise':
        rid = uid.split(':', 1)[1]
        res = next(iter(store.runs_for(result_id=rid)))
        item = consumer.revise(store, res['candidate_id'], res, now_ms=now_ms, max_revisions=study['max_revisions'], study_id=study_id)
        return dict(item_id=item['item_id'], status=item['status'], reason=item['reason'], child=item['candidate_id'])
    if kind == 'freeze':
        mine = store.runs_for(envelope_id=env['envelope_id'])
        f, kind_ = qualification.freeze_finalist(store, env, mine), 'alpha'
        if f is None:                      # a protective hedge is frozen on ITS objective, never as alpha
            f, kind_ = hedge_qualification.freeze_hedge_finalist(store, env, mine), 'hedge_protective'
        return dict(freeze_id=f['freeze_id'] if f else None, finalist=f['candidate']['candidate_id'] if f else None,
                    freeze_kind=kind_ if f else None,
                    reason=None if f else 'NO_FINALIST: no DEVELOPMENT_POSITIVE or HEDGE_SUPPORTED candidate')
    if kind == 'qualify':
        fz = store.get_once('freezes', json.loads(next(u for u in store.units(study_id)
                                                       if u['unit_id'] == 'freeze')['result'])['freeze_id'])
        timed = []

        def bounded(fn, payload, **_):
            from ...core.child import ChildResult
            r = run(fn, payload) if run is not None else _call(fn, payload, timeout_s, child,
                limits=_limits(study),should_stop=should_stop)
            if not getattr(r, 'terminated', True):
                fence_child(store, r, study_id=study_id, unit_id=uid)
                _set_unit(store, study_id, uid, 'RUNNING', now_ms, note='CHILD_NOT_REAPED')
                raise RuntimeError('research_child_not_reaped: protected look remains spent')
            if getattr(r, 'timed_out', False):
                timed.append(1)
            return r if isinstance(r, ChildResult) or hasattr(r, 'ok') else ChildResult(ok=True, value=r)
        if fz.get('schema') == hedge_qualification.FREEZE_SCHEMA:
            q = hedge_qualification.qualify_hedge(
                store, fz, env, study['snapshot_path'], Path(study['workdir']) / 'qualification', now_ms=now_ms, cfg=cfg,
                run=bounded, source_journal=study.get('source_journal_path'),budget_journal=study.get('budget_journal_path'), derivs_path=study.get('protected_derivs_path') or study.get('derivs_path'), world_path=study.get('world_path'))
            return dict(status=q['status'], reason=q['reason'], qualification_id=q['qualification_id'],
                        evidence_id=q['evidence_id'], qualification_kind='hedge_protective', worker_timed_out=bool(timed))
        q = qualification.qualify(
            store, fz, env, study['snapshot_path'], Path(study['workdir']) / 'qualification', now_ms=now_ms,
            run=bounded, cfg=cfg, book=study['book'], source_journal=study['source_journal_path'],
            budget_journal=study['budget_journal_path'], derivs_path=study.get('protected_derivs_path') or study.get('derivs_path'), world_path=study.get('world_path'))
        return dict(status=q['status'], reason=q['reason'], qualification_id=q['qualification_id'],
                    evidence_id=q['evidence_id'], worker_timed_out=bool(timed))
    if kind == 'handoff':
        from ...core.journal import Journal
        from . import handoff
        q = store.get_once('qualifications', json.loads(next(u for u in store.units(study_id)
                                                             if u['unit_id'] == 'qualify')['result'])['qualification_id'])
        if q.get('schema') == hedge_qualification.QUAL_SCHEMA:
            return handoff.hand_off_hedge(Journal(study['journal_path']), cfg, q)
        return handoff.hand_off(Journal(study['journal_path']), cfg, q)
    raise ClosureRefused('unit_kind_unknown', kind)


def step(store: Store, study_id: str, *, budget_s: float, now_ms: int | None = None, clock=time.monotonic,
         cycle_seconds: float | None = None, skip_if_cycle_s: float = 45.0, run=None, child: bool = True,
         should_stop=lambda: False, cfg: dict | None = None, allow_heavy: bool = True, extra_kinds=()) -> dict:
    """Run units until the budget, the work, a stop request or a timeout ends
    the step. Heavy units execute in a killable child bounded by
    min(unit_timeout_s, time left), so even the FIRST unit cannot overrun."""
    with single_flight(store) as free:
        if not free:
            return dict(study_status(store, study_id), step='ALREADY_RUNNING')
        return _step(store, study_id, budget_s, now_ms, clock, cycle_seconds, skip_if_cycle_s, run, child,
                     should_stop, cfg, allow_heavy, extra_kinds)


#: Units whose cost is bounded inside this process or by a killable child. Everything
#: else (bank/source verification, snapshot sealing, rotation capacity, the look)
#: needs an operator step: it must not run inside the live kernel callback.
KERNEL_SAFE = frozenset(('develop', 'revise', 'freeze'))


def _step(store, study_id, budget_s, now_ms, clock, cycle_seconds, skip_if_cycle_s, run, child, should_stop, cfg,
          allow_heavy=True, extra_kinds=()):
    study, env = _load(store, study_id)
    if cfg is None:
        from ...core.config import load_config
        cfg = load_config()
    now_ms = Store.now_ms() if now_ms is None else now_ms
    if cycle_seconds is not None and cycle_seconds > skip_if_cycle_s:
        _record(store, study_id, 'DEFERRED', 'busy_market_cycle', now_ms, progress=False)
        return dict(study_status(store, study_id), step='DEFERRED')
    deadline = clock() + float(budget_s)
    ran, postponed, stop = 0, 0, 'COMPLETE'
    while True:
        if should_stop():
            stop = 'STOP_REQUESTED'
            break
        _resume_waiting(store, study_id, study, env, now_ms)
        _plan(store, study_id, study, env, now_ms)
        units = store.units(study_id)
        done = {u['unit_id'] for u in units if u['status'] in TERMINAL_UNIT_STATUSES}
        todo = [u for u in units if u['status'] in ('PENDING', 'RUNNING') and all(d in done for d in json.loads(u['deps']))]
        if not todo:
            stop = ('WAITING_FOR_DATA' if any(u['status'] == 'WAITING_DATA' for u in units) else
                    'COMPLETE' if all(u['status'] in TERMINAL_UNIT_STATUSES for u in units) else 'BLOCKED_ON_DEPENDENCY')
            break
        remaining = deadline - clock()
        if remaining <= 1.0:
            stop = 'DEADLINE'
            break
        u = todo[0]
        if not allow_heavy and u['kind'] not in KERNEL_SAFE and u['kind'] not in extra_kinds:
            stop = 'REQUIRES_OPERATOR_STEP'
            break
        prior = json.loads(u['result']) if u['result'] and u['status'] == 'PENDING' else {}
        if prior.get('timed_out') and remaining <= prior['timeout_s'] * 1.001 and u['attempts'] < MAX_UNIT_ATTEMPTS:
            stop = 'BUDGET_INSUFFICIENT'      # known to need more than this budget: do not relaunch and burn CPU
            break
        if u['attempts'] >= MAX_UNIT_ATTEMPTS:
            _set_unit(store, study_id, u['unit_id'], 'FAILED', now_ms, note='attempt budget exhausted')
            continue
        fits = reads = 0
        budget = csearch.budget_for(store, study_id, study)
        if u['kind'] == 'develop':
            cand = store.get_once('candidates', u['unit_id'].split(':', 1)[1])
            if cand is not None:
                fits, reads = csearch._fits(cand), csearch.reads_of(cand, budget['symbols'])
        # Search ceilings bound experiment work, not the bounded recording /
        # independent assessment of results already obtained. A later cheap
        # candidate can still fit when the next fitted candidate cannot.
        finalizing = u['kind'] in ('freeze','qualify','handoff')
        stop_why = None if finalizing else csearch.dispatch_gate(store, study_id, budget, fits=fits, reads=reads)
        if stop_why:
            if u['kind'] in ('develop','revise'):
                _set_unit(store,study_id,u['unit_id'],'POSTPONED',now_ms,
                    result=dict(postponed=True,evidence='NOT_EVALUATED',reason=stop_why),note=stop_why)
                postponed += 1
                continue
            stop = stop_why                     # cumulative registered totals spent: stop here, never widen, never skip silently
            break
        if not finalizing:
            remaining = min(remaining, csearch.cpu_left(store, study_id, budget))
        if remaining <= 1.0:
            if u['kind'] in ('develop','revise'):
                _set_unit(store,study_id,u['unit_id'],'POSTPONED',now_ms,
                    result=dict(postponed=True,evidence='NOT_EVALUATED',reason='BUDGET_EXHAUSTED:cpu_seconds'),
                    note='BUDGET_EXHAUSTED:cpu_seconds')
                postponed += 1
                continue
            stop = 'BUDGET_EXHAUSTED:cpu_seconds'
            break
        if u['status'] == 'RUNNING':                 # a previous process died mid-unit: charge that attempt, then retry
            store.close_interrupted(study_id, u['unit_id'], int(time.time() * 1000), float(study['unit_timeout_s']))
        _set_unit(store, study_id, u['unit_id'], 'RUNNING', now_ms, bump=True)   # checkpoint BEFORE running
        t0, wall0 = clock(), int(time.time() * 1000)
        ledger = store.start_resource(study_id, u['unit_id'], u['kind'], wall0, fits=fits, reads=reads)
        try:
            extra = {'should_stop': should_stop} if child and run is None else {}
            res = _run_unit(store, study_id, study, env, u, now_ms, run, timeout_s=remaining, child=child, cfg=cfg, **extra)
        except UnitTimeout as exc:
            store.finish_resource(ledger, int(time.time() * 1000), clock() - t0, 'TIMEOUT')
            partial = dict(timed_out=True, timeout_s=round(exc.timeout_s, 1), hit_unit_cap=exc.hit_cap)
            # EVERY timeout counts as an attempt, so retries are bounded whatever the budget/cap relation
            _set_unit(store, study_id, u['unit_id'], 'PENDING', now_ms, result=partial,
                      note='TIMEOUT at unit cap' if exc.hit_cap else 'TIMEOUT at step budget')
            stop = 'UNIT_TIMEOUT' if exc.hit_cap else 'DEADLINE'
            ran += 1
            break
        except ClosureRefused as exc:
            store.finish_resource(ledger, int(time.time() * 1000), clock() - t0, 'REFUSED:' + exc.code)
            _set_unit(store, study_id, u['unit_id'], 'FAILED', now_ms, result=dict(refused=exc.code), note=str(exc)[:200])
            ran += 1
            continue
        result = dict(res, elapsed_s=round(clock() - t0, 2), started_ms=wall0, ended_ms=int(time.time() * 1000))
        store.finish_resource(ledger, result['ended_ms'], clock() - t0, 'WAITING_DATA' if res.get('waiting_data') else 'DONE')
        _set_unit(store, study_id, u['unit_id'], 'WAITING_DATA' if res.get('waiting_data') else 'DONE', now_ms, result=result,
                  note='waiting for owner-supplied data: ' + ','.join(r[:12] for r in res['waiting_data'])
                  if res.get('waiting_data') else None)
        ran += 1
    nxt = next((u['unit_id'] for u in store.units(study_id) if u['status'] in ('PENDING', 'RUNNING')), None)
    _record(store, study_id, 'COMPLETED' if stop == 'COMPLETE' else 'IN_PROGRESS', stop, now_ms, progress=ran > 0 or postponed > 0, nxt=nxt)
    return dict(study_status(store, study_id), step=stop, units_run=ran, units_postponed=postponed)


def _resume_waiting(store, study_id, study, env, now_ms):
    """Re-queue a candidate waiting for data ONLY when the loader now serves every awaited series. Its
    original blocked run stays in `runs`; the re-run reads the new data, so its cache key is new. The request
    is resolved by that re-evaluation (data_access.record), never by acknowledgement."""
    for u in store.units(study_id):
        if u['status'] != 'WAITING_DATA':
            continue
        need = json.loads(u['result']).get('waiting_requirements') or []
        cand = store.get_once('candidates', u['unit_id'].split(':', 1)[1])
        if cand is not None and data_access.eligible_now(store, study, env, cand, need):
            _set_unit(store, study_id, u['unit_id'], 'PENDING', now_ms, note='awaited data now eligible; re-evaluating')


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
    development = [json.loads(u['result']) for u in units
                   if u['kind'] == 'develop' and u['status'] == 'DONE' and u.get('result')]
    development_outcomes = {}
    requirements = set()
    for result in development:
        label = result.get('outcome', 'UNKNOWN')
        development_outcomes[label] = development_outcomes.get(label, 0) + 1
        if label == 'BLOCKED_DATA':
            requirements.update(g['requirement'] for g in (result.get('context_gaps') or [])
                                if isinstance(g, dict) and isinstance(g.get('requirement'), str))
    reason = (qual or {}).get('reason') or (frz or {}).get('reason')
    blocked = development_outcomes.get('BLOCKED_DATA', 0)
    failed = [dict(unit_id=u['unit_id'], reason=str(u.get('note') or 'worker failure')[:160])
              for u in units if u['kind']=='develop' and u['status']=='FAILED']
    postponed = [dict(unit_id=u['unit_id'],reason=u['note'],evidence='NOT_EVALUATED')
                 for u in units if u['kind']=='develop' and u['status']=='POSTPONED']
    if outcome == 'NO_FINALIST' and blocked:
        reason = (f'NO_FINALIST: required data unavailable for {blocked}/{len(development)} evaluated candidates; '
                  'these candidates have no economic verdict')
    if outcome == 'NO_FINALIST' and failed:
        reason = (f'NO_FINALIST: {len(failed)} development units failed to run; '
                  f'{blocked} evaluated candidates lacked required data')
    if outcome == 'NO_FINALIST' and postponed:
        reason = (reason or 'NO_FINALIST') + f'; {len(postponed)} experiments not evaluated within the fixed budget'
    return dict(study_id=study_id, process_status=row['status'], stop_reason=row['stop_reason'],
                last_progress_ms=row['last_progress_ms'], next_work=row['next_work'], steps=row['steps'],
                units=counts, research_outcome=outcome,
                waiting_for_data=[dict(unit_id=u['unit_id'], request_ids=json.loads(u['result']).get('waiting_data'))
                                  for u in units if u['status'] == 'WAITING_DATA'],
                research_reason=reason, development_outcomes=development_outcomes,
                failed_development_units=failed[:16], failed_development_units_omitted=max(0,len(failed)-16),
                postponed_development_units=postponed[:16],postponed_development_units_omitted=max(0,len(postponed)-16),
                blocked_requirements=sorted(requirements)[:32],
                blocked_requirements_omitted=max(0,len(requirements)-32),
                note='process_status says whether the job ran; research_outcome is the only evidence statement')


def scheduled(cfg: dict, cycle_seconds: float | None = None, *, slot_remaining_s: float | None = None,
              should_stop=lambda: False) -> dict:
    """Opt-in hook for the research slot (research.closure.enabled, default off).

    With auto enabled, preparation, measured-coverage study creation and research
    units run in deadline-bounded children under one remaining slot budget.
    Otherwise this resumes an existing manually created study. Qualification
    and handoff are excluded from this callback and retain their existing gates.
    Parent bookkeeping is bounded separately; no hard operating-system I/O
    deadline is claimed."""
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
    from . import auto
    store = Store(c['store_path'])
    try:
        now_ms = Store.now_ms()
        try:
            settings = auto.settings(cfg)
        except ClosureRefused as exc:
            store.log_schedule(now_ms, 'CONFIG_INVALID', detail=dict(error=exc.code))
            return dict(status='CONFIG_INVALID', reason=exc.code)
        if settings is not None:
            try:
                return auto.tick(cfg, store, settings, now_ms=now_ms, budget_s=budget, cycle_seconds=cycle_seconds,
                                 should_stop=should_stop)
            except Exception as exc:                       # noqa: BLE001 - a research fault must never reach the trade loop
                store.log_schedule(now_ms, 'ERROR', detail=dict(error=f'{type(exc).__name__}: {exc}'[:300]))
                return dict(status='ERROR', reason=type(exc).__name__)
        if not c.get('study_id') or store.study(c['study_id']) is None:
            return dict(status='NOT_INITIALISED', reason='run: python -m trader.research.closure create (operator step), '
                                                         'or configure research.closure.auto')
        return step(store, c['study_id'], budget_s=budget, cycle_seconds=cycle_seconds, should_stop=should_stop,
                    cfg=cfg, allow_heavy=False)
    finally:
        store.close()
