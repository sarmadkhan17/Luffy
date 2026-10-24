"""P3b — freeze a finalist and qualify it independently on protected data.

Contract (separate from, and never relabelled as, post-generation prospective
evidence): the frozen candidate (exact rendered thresholds fitted on the
development window only) is evaluated ONCE by the EXISTING referee
(`referee.examine` via `job.referee_job`: dependence-corrected consistency on
held-out A and B, common-rotation null, gate 3) against a sealed copy of the
protected window, priced by the EXISTING LORD++ ledger (`Ledger.next_alpha` /
`record_test` / `complete_look`). One look per envelope: any later candidate
on the same window is BLOCKED, so qualification feedback cannot return to
development while an untouched-test claim is kept.

Statuses: QUALIFIED | NOT_QUALIFIED | INCONCLUSIVE | BLOCKED. Only QUALIFIED
carries a registered rejecting gate-1 look, passing gate 3 and a proven
UNEXPOSED protected window. Gate 3 uses an EMPTY historical book (no book
existed in that era): it asserts standalone compounded return, not
diversification — recorded in the packet as `gate3_book`.
"""
from __future__ import annotations

import json
from pathlib import Path

from ...strategy.portfolio_evidence import RESEARCH_CLOCK, accounting_provenance
from .. import fdr, job, portfolio_null as pn, referee
from ..combo import Combination, evaluated_record
from ..ledger import Ledger
from . import ClosureRefused, digest, exposure, families, manifest
from .development import require_current, research_cfg

FREEZE_SCHEMA = 'closure-freeze.v1'
QUAL_SCHEMA = 'closure-qualification.v1'
STATUSES = ('QUALIFIED', 'NOT_QUALIFIED', 'INCONCLUSIVE', 'BLOCKED')
BOOK = 'EMPTY_HISTORICAL_BOOK'          # standalone only; never sufficient for CONFIGURED authority


def freeze_finalist(store, env: dict, results: list):
    """Deterministically freeze the best DEVELOPMENT_POSITIVE result of this
    envelope. Accounts for every trial so the search history is on record."""
    manifest.verify_envelope(env)
    mine = [r for r in results if r.get('stage') == 'DEVELOPMENT' and r.get('envelope_id') == env['envelope_id']]
    pos = [r for r in mine if r['outcome'] == 'DEVELOPMENT_POSITIVE']
    if not pos:
        return None
    best = max(pos, key=lambda r: (r['metrics']['total_pct'], -(r['metrics']['consistency_p'] or 1.0), r['candidate_id']))
    body = dict(schema=FREEZE_SCHEMA, envelope_id=env['envelope_id'],
                candidate=store.get_once('candidates', best['candidate_id']), development_result_id=best['result_id'],
                combo=best['combo'], combo_hash=best['combo_hash'], threshold_fit=best['threshold_fit'],
                development_metrics=best['metrics'],
                search_history=dict(trials=len(mine), outcomes={o: sum(1 for r in mine if r['outcome'] == o)
                                                                 for o in sorted({r['outcome'] for r in mine})},
                                    result_ids=sorted(r['result_id'] for r in mine)))
    freeze = dict(body, freeze_id=digest(body))
    store.put_once('freezes', freeze['freeze_id'], freeze)
    return freeze


def verify_freeze(freeze: dict, env: dict) -> Combination:
    if freeze.get('schema') != FREEZE_SCHEMA or freeze.get('freeze_id') != digest(
            {k: v for k, v in freeze.items() if k != 'freeze_id'}):
        raise ClosureRefused('freeze_integrity')
    if freeze['envelope_id'] != env['envelope_id']:
        raise ClosureRefused('freeze_envelope_mismatch')
    families.verify_candidate(freeze['candidate'])
    c = Combination.from_dict(freeze['combo'])
    if (c.hash != freeze['combo_hash'] or c.evaluation_scope != env['envelope_id']
            or c.trigger != freeze['candidate']['candidate_id']):
        raise ClosureRefused('freeze_combination_mismatch')
    return c


def _row(db_rows):
    return [dict(r) for r in db_rows]


def _packet(status, reason, env, freeze, ledger=None, extra=None, book=None, sequence_id=None):
    body = dict(schema=QUAL_SCHEMA, qualification_id=digest(['qualification', freeze['freeze_id']]), status=status,
                reason=reason, envelope=env, freeze=freeze, authority_class=env['policy_class'],
                gate3_book=dict(kind='DECLARED_INCUMBENT', book_id=digest(book), n=len(book)) if book else BOOK,
                book=book or [], source_sequence_id=sequence_id, ledger=ledger,
                protected_fingerprint=env.get('protected_fingerprint'), exposure_proofs=env['exposure_proofs'],
                **(extra or {}))
    return dict(body, evidence_id=digest(body))


def calibration_approved(cfg, env) -> bool:
    """A retained FINAL calibration report, run under a CONFIGURED policy and
    the CURRENT evaluator, that the configuration explicitly approves."""
    a = (((cfg or {}).get('research') or {}).get('closure') or {}).get('approved_calibration')
    if not isinstance(a, dict):
        return False
    try:
        from . import calibration
        rep = json.loads(Path(a['report_path']).read_text())
        return calibration.report_authorizes(rep, a, env)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def authority_blocks(env, cfg, book, source_journal, budget_journal) -> list:
    """Why a CONFIGURED-class look must not be taken yet. Closed production
    gates are honoured, not routed around; TEST_ONLY probes are unaffected."""
    if env['policy_class'] != 'CONFIGURED':
        return []
    out = []
    if not ((cfg or {}).get('research') or {}).get('referee', False):
        out.append('production_referee_gate_closed')
    if not calibration_approved(cfg, env):
        out.append('calibration_not_approved')
    if not book:
        out.append('incumbent_book_not_declared')
    if not source_journal:
        out.append('cumulative_sequence_source_not_supplied')
    if not budget_journal:
        out.append('canonical_budget_journal_not_supplied')
    elif not source_journal or Path(budget_journal).resolve() != Path(source_journal).resolve():
        # The only reservation that also binds the existing runner is the research ledger's own
        # BEGIN IMMEDIATE on the journal both spend in. A separate copy can be spent twice in
        # parallel, so authoritative qualification must spend in THE source journal itself.
        out.append('shared_budget_reservation_unavailable')
    if manifest.rederive_proofs(env) != env['exposure_proofs'] or set(env['exposure_proofs'].values()) != {'FORWARD_SEAL'}:
        out.append('exposure_proof_not_forward_seal')
    return out


def import_sequence(journal, source_path) -> str:
    """Copy the source journal's registered budget and spent looks into the
    canonical sequence VERBATIM; any divergence is a refusal, never a reset."""
    from contextlib import closing
    from ..predictive_experiment import readonly
    with closing(readonly(source_path)) as src:
        src.row_factory = __import__('sqlite3').Row
        budget = [dict(r) for r in src.execute('SELECT * FROM research_budget')]
        tests = [dict(r) for r in src.execute('SELECT * FROM research_tests ORDER BY seq')]
    with journal._tx() as c:
        if not c.in_transaction:
            c.execute('BEGIN IMMEDIATE')
        have = [dict(zip(('id', 'alpha', 'w0', 'registered_at'), r)) for r in c.execute('SELECT * FROM research_budget')]
        if have and budget and (have[0]['alpha'], have[0]['w0']) != (budget[0]['alpha'], budget[0]['w0']):
            raise ClosureRefused('error_budget_differs')
        if budget and not have:
            c.execute('INSERT INTO research_budget VALUES (?,?,?,?)', tuple(budget[0][k] for k in ('id', 'alpha', 'w0', 'registered_at')))
        cur = c.execute('SELECT * FROM research_tests')
        names = [d[0] for d in cur.description]
        mine = {r[0]: dict(zip(names, r)) for r in cur.fetchall()}
        for t in tests:
            if t['seq'] in mine:
                if mine[t['seq']] != t:
                    raise ClosureRefused('budget_sequence_diverged')
            else:
                if mine and t['seq'] < max(mine):
                    raise ClosureRefused('budget_sequence_diverged')
                cols = list(t)
                c.execute(f"INSERT INTO research_tests({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
                          tuple(t[k] for k in cols))
    return digest(dict(budget=budget, tests=tests))


def qualify(store, freeze: dict, env: dict, snapshot_path, workdir, *, now_ms: int, run=None, cfg=None, book=None,
            source_journal=None, budget_journal=None, derivs_path=None, world_path=None) -> dict:
    """Run (or recall) the independent qualification. Idempotent: a recorded
    qualification is returned unchanged and a spent look is never retaken."""
    c = verify_freeze(freeze, env)
    require_current(env)
    qid = digest(['qualification', freeze['freeze_id']])
    done = store.get_once('qualifications', qid)
    if done is not None:
        return done

    def record(p):
        store.put_once('qualifications', qid, p)
        return p

    book = list(book or [])
    gate = authority_blocks(env, cfg, book, source_journal, budget_journal)
    if gate:
        # not recorded: gates may open later, and nothing was spent
        return _packet('BLOCKED', 'authority_blocked: ' + ','.join(gate) + ' (no look spent)', env, freeze)
    if env['protected_status'] != 'ELIGIBLE':
        return record(_packet('BLOCKED', 'protected_unavailable: ' + ';'.join(env['protected_reasons'])[:300], env, freeze))
    prior = store.get_once('looks', env['envelope_id'])
    if prior is not None and prior['freeze_id'] != freeze['freeze_id']:
        return record(_packet('BLOCKED', 'protected_window_already_looked_by_another_candidate', env, freeze))
    if prior is None:
        # exposure is re-derived NOW from the controlled registry, not the envelope
        events = exposure.events(store)
        s = env['split']
        for sym in env['discovery_symbols'] + env['heldout_symbols']:
            state, _ = manifest.exposure_state(events, sym, s['start_ms'], s['end_ms'],
                                               env['protected_first_available'].get(sym),
                                               allow_synthetic=env['policy_class'] == 'TEST_ONLY')
            if state != 'UNEXPOSED':
                return record(_packet('BLOCKED', f'protected_exposure_{state.lower()}:{sym}', env, freeze))
    s = env['split']

    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    sealed = workdir / 'protected.db'
    try:
        fp = manifest.freeze_window(snapshot_path, sealed, env)
    except ClosureRefused as exc:
        return record(_packet('BLOCKED', f'snapshot_unverifiable:{exc.code}', env, freeze))
    if fp != env['protected_fingerprint']:
        return record(_packet('BLOCKED', 'protected_data_changed_since_envelope', env, freeze))
    ctx_paths, ctx_id = {}, None
    external = [r for r in c.requires if r not in ('ohlcv', 'world')]
    if external:
        # dependency-complete BEFORE any look: a candidate whose context cannot be served in the sealed window
        # would otherwise read NaN, take no trades and burn the look as "no edge"
        sealed_derivs = workdir / 'protected-derivs.db'
        if sealed_derivs.exists():
            sealed_derivs.unlink()
        try:
            ctx = manifest.freeze_context(snapshot_path, derivs_path, sealed, sealed_derivs, env, external)
        except ClosureRefused as exc:
            return record(_packet('BLOCKED', f'context_unverifiable:{exc.code}', env, freeze))
        if ctx['gaps']:
            return _packet('BLOCKED', 'context_missing_in_protected_window (no look spent): ' +
                           ';'.join(f"{g['requirement']}: {g['reason']}" for g in ctx['gaps'])[:300], env, freeze)
        ctx_id = ctx['fingerprint']
        ctx_paths = dict(derivs=str(sealed_derivs)) if sealed_derivs.exists() else {}

    if 'world' in c.requires:
        from ..compose.world_snapshot import seal_world
        try:
            world_receipt = seal_world(world_path, workdir / 'protected-world.db', env)
        except ClosureRefused as exc:
            return _packet('BLOCKED', f'world_context_unavailable:{exc.code} (no look spent)', env, freeze)
        ctx_paths['world'] = str(workdir / 'protected-world.db')
        ctx_id = digest([ctx_id, world_receipt])

    from ...core.journal import Journal
    journal = Journal(budget_journal or workdir / 'qualification.db')
    led = Ledger(journal)
    led.ensure()
    seq_id = import_sequence(journal, source_journal) if (source_journal and Path(source_journal).resolve() != Path(
        budget_journal or workdir / 'qualification.db').resolve()) else (
        digest(_row(journal.query('SELECT * FROM research_tests ORDER BY seq'))) if source_journal else None)
    h, tf, geo = c.hash, c.tf, c.geo
    pol = env['policy']
    spent = led.spent(h)
    lock = open(str(budget_journal or workdir / 'qualification.db') + '.reserve', 'a')
    try:
        import fcntl
        fcntl.flock(lock, fcntl.LOCK_EX)          # serialise next_alpha -> record_test across stores/processes
        return _look(**dict(locals(), ctx_paths=ctx_paths, ctx_id=ctx_id))
    finally:
        lock.close()


def _look(*, store, freeze, env, c, journal, led, h, tf, geo, pol, spent, workdir, sealed, snapshot_path, now_ms, run,
          record, book, seq_id, qid, ctx_paths=None, ctx_id=None, **_):
    s = env['split']
    if spent is None:
        reg_a, reg_w = led.register_budget(pol['fdr_target'], pol['lord_w0'])
        if abs(reg_a - pol['fdr_target']) > 1e-12 or abs(reg_w - pol['lord_w0']) > 1e-12:
            return record(_packet('BLOCKED', 'registered_error_budget_differs_from_policy', env, freeze))
        t, alpha = led.next_alpha(pol['fdr_target'], pol['lord_w0'])
        draws = pn.draws_for(alpha, int(pol['referee_max_draws']))
        cfg = research_cfg(env)
        cfg['research'].update(predictive_split=s, horizons=[tf], geometries=[geo], referee=False, handoff=False)
        paths = dict(candles=str(sealed), derivs=str(workdir / 'unused-derivs.db'))
        paths.update(ctx_paths or {})
        capacity = referee.rotation_capacity(tf, s['cut_ms'], env['heldout_symbols'],
                                             env['discovery_symbols'] + env['heldout_symbols'], cfg, paths)
        if draws is not None and capacity is not None and capacity['b']:
            draws = pn.draws_for(alpha, min(int(pol['referee_max_draws']), pn.rotation_offsets(min(capacity['b'].values()))))
        reason = ('alpha_below_resolvable_level' if draws is None else referee.rotation_deferral(capacity, draws))
        if reason:
            return record(_packet('INCONCLUSIVE', f'deferred_before_look: {reason} (alpha_{t}={alpha:.2e}); no look spent',
                                  env, freeze))
        m = freeze['development_metrics']
        led.record_result(dict(hash=h, tf=tf, geo=geo, k=c.k, round='closure', parent='', trigger=c.trigger,
                               window=c.window, parts=list(c.keys), entry_long=c.long, entry_short=c.short,
                               verdict='scored', trades=m['trades'], consistency_p=m['consistency_p'],
                               median_pf=m['median_pf'], scored_symbols=m['scored_symbols'], testable=True,
                               portfolio=dict(total_pct=m['total_pct'], max_dd_pct=m['max_dd_pct'], accounting=
                                              accounting_provenance(2000.0, float(env['risk'].get('risk_per_trade_pct', .5)),
                                                                    int(env['risk'].get('max_open_trades', 8)), RESEARCH_CLOCK))),
                          'survivor', 'frozen closure candidate', {})
        led.set_candidate(h, tf, geo, 'queued', rank=float(m['total_pct'] or 0.0))
        evaluated = evaluated_record(c)
        # the look is registered BEFORE any protected price is read
        store.put_once('looks', env['envelope_id'], dict(freeze_id=freeze['freeze_id'], candidate_hash=h, alpha_t=alpha, t=t))
        exposure.mark(store, 'PROTECTED_EXPOSED', s['start_ms'], s['end_ms'], now_ms, f'qualification look {h}')
        led.record_test(h, tf, geo, 'gate1', 1.0, alpha, False, dict(
            look_state='pending', cut_ms=int(s['cut_ms']), evaluated=evaluated, envelope_id=env['envelope_id'],
            freeze_id=freeze['freeze_id'], reason='dispatched_or_interrupted; no retry permitted'))
        payload = dict(combo=c.as_dict(), cut_ms=int(s['cut_ms']), cfg=cfg, spent_cut_ms=int(s['cut_ms']), paths=paths,
                       discovery_symbols=list(env['discovery_symbols']), heldout_symbols=list(env['heldout_symbols']),
                       draws=int(draws), seed=int(pol['seed']), alpha=float(alpha),
                       max_draws=int(pol['referee_max_draws']), book=book, book_exit_bindings={})
        if run is None:
            v, run_error = job.referee_job(payload), None
        else:
            res = run(job.referee_job, payload)
            v, run_error = (res.value or {}), (None if res.ok else f'worker failed: {res.error}')
        error = run_error or v.get('error') or (None if v.get('hash') == h and v.get('looked') is True
                                                 else 'protected result identity unavailable')
        if error:
            led.complete_look(h, 1.0, dict(error=error, cut_ms=int(s['cut_ms']), evaluated=evaluated), 'gate1_fail',
                              gate1=dict(p=1.0, reason='untestable: ' + str(error)), reason='failed look: ' + str(error))
        else:
            g1, g3 = v['gate1'], v.get('gate3') or dict(passed=False, reason='no gate3 result')
            if float(g1['p']) <= alpha and g3.get('passed'):
                state, why = 'referee_passed', f"{g1['reason']} <= alpha_{t}={alpha:.2e}; {g3.get('reason')}"
            elif float(g1['p']) > alpha:
                state, why = 'gate1_fail', f"{g1['reason']} > alpha_{t}={alpha:.2e}"
            else:
                state, why = 'gate3_fail', g3.get('reason', '')
            led.complete_look(h, float(g1['p']), dict(
                evaluated=evaluated, cut_ms=int(s['cut_ms']), a=v.get('a'), b=v.get('b'), rotation=v.get('rotation'),
                draws=(v.get('rotation') or {}).get('draws', draws), t=t, envelope_id=env['envelope_id'],
                freeze_id=freeze['freeze_id']), state, gate1={**g1, 'alpha': alpha, 't': t, 'a': v.get('a'),
                'b': v.get('b'), 'rotation': v.get('rotation')}, gate3=g3, reason=why)
    ctx_extra = dict(context_fingerprint=ctx_id, required_inputs=list(c.requires))
    cand = led.candidate(h)
    pending = json.loads(led.spent(h)['detail']).get('look_state') == 'pending'
    if pending:
        return record(_packet('INCONCLUSIVE', 'look_spent_but_not_completed (interrupted); p=1 charged, no retry', env, freeze, extra=ctx_extra))
    ledger = dict(budget=_row(journal.query('SELECT * FROM research_budget WHERE id=1'))[0],
                  tests=_row(journal.query('SELECT * FROM research_tests ORDER BY seq')),
                  candidate=dict(cand), combo=_row(journal.query('SELECT * FROM research_combos WHERE hash=?', (h,)))[0])
    if cand['state'] == 'referee_passed':
        return record(_packet('QUALIFIED', cand['reason'], env, freeze, ledger, book=book, sequence_id=seq_id, extra=ctx_extra))
    g1 = json.loads(cand['gate1'] or '{}')
    untestable = str(g1.get('reason', '')).startswith('untestable') or 'failed look' in (cand['reason'] or '')
    return record(_packet('INCONCLUSIVE' if untestable else 'NOT_QUALIFIED', cand['reason'], env, freeze, ledger,
                          book=book, sequence_id=seq_id, extra=ctx_extra))


def verify_qualification(q: dict) -> dict:
    """Pure re-derivation of a QUALIFIED packet from its own recorded rows.
    Raises ClosureRefused on any inconsistency; never trusts `status`."""
    body = {k: v for k, v in q.items() if k not in ('evidence_id',)}
    if q.get('schema') != QUAL_SCHEMA or q.get('evidence_id') != digest(body):
        raise ClosureRefused('qualification_integrity')
    env, freeze = q['envelope'], q['freeze']
    manifest.verify_envelope(env)
    c = verify_freeze(freeze, env)
    if q['qualification_id'] != digest(['qualification', freeze['freeze_id']]):
        raise ClosureRefused('qualification_id_mismatch')
    if q['status'] != 'QUALIFIED':
        raise ClosureRefused('qualification_not_qualified', str(q['status']))
    if env['protected_status'] != 'ELIGIBLE' or not env.get('protected_fingerprint'):
        raise ClosureRefused('protected_window_not_eligible')
    gb = q['gate3_book']
    if gb != BOOK and (not isinstance(gb, dict) or gb.get('book_id') != digest(q['book']) or not q['book']):
        raise ClosureRefused('gate3_book_unbound')
    if manifest.rederive_proofs(env) != env['exposure_proofs'] or env['exposure_proofs'] != q['exposure_proofs']:
        raise ClosureRefused('exposure_proof_not_rederivable')
    if env['policy_class'] == 'CONFIGURED' and (
            set(env['exposure_proofs'].values()) != {'FORWARD_SEAL'} or gb == BOOK or not q.get('source_sequence_id')):
        raise ClosureRefused('configured_authority_requirements_unmet')
    if q['protected_fingerprint'] != env['protected_fingerprint'] or q['authority_class'] != env['policy_class']:
        raise ClosureRefused('qualification_binding_mismatch')
    if any(p is None for p in env['exposure_proofs'].values()):
        raise ClosureRefused('exposure_not_proven')
    led = q['ledger']
    h = c.hash
    looks = [t for t in led['tests'] if t['hash'] == h and t['gate'] == 'gate1']
    if len(looks) != 1:
        raise ClosureRefused('gate1_look_missing_or_ambiguous')
    look = looks[0]
    d = json.loads(look['detail'])
    if (d.get('envelope_id') != env['envelope_id'] or d.get('freeze_id') != freeze['freeze_id']
            or d.get('cut_ms') != env['split']['cut_ms'] or d['evaluated']['hash'] != h
            or d['evaluated']['combo'] != c.as_dict()):
        raise ClosureRefused('look_not_bound_to_envelope_freeze')
    actual = referee.gate1(d['a'], d['b'], d['rotation'])
    if abs(actual['p'] - look['p']) > 1e-12 or not look['rejected'] or look['p'] > look['alpha_t']:
        raise ClosureRefused('gate1_not_rederivable')
    rows = sorted(led['tests'], key=lambda t: t['seq'])
    pos = [t['seq'] for t in rows].index(look['seq']) + 1
    rej = [i for i, t in enumerate(rows, 1) if t['rejected'] and i < pos]
    b = led['budget']
    pol = env['policy']
    if (abs(b['alpha'] - pol['fdr_target']) > 1e-12 or abs(b['w0'] - pol['lord_w0']) > 1e-12
            or look['alpha_t'] > fdr.alpha_at(pos, rej, b['alpha'], b['w0']) + 1e-12):
        raise ClosureRefused('alpha_not_registered')
    cand = led['candidate']
    g3 = json.loads(cand['gate3'] or 'null')
    if cand['state'] != 'referee_passed' or cand['hash'] != h or not isinstance(g3, dict) or g3.get('passed') is not True:
        raise ClosureRefused('candidate_gates_not_passed')
    if json.loads(cand['gate1'])['p'] != look['p'] or led['combo']['hash'] != h:
        raise ClosureRefused('candidate_look_mismatch')
    return q
