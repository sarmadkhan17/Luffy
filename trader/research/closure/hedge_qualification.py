"""Protective-hedge freeze, independent protected assessment and pure verification.

A typed extension of the closure workflow, not a second strategy table and not alpha metrics. A protective hedge is judged
on its PREDECLARED risk objective (`spec.objective.metric`: max drawdown, CVaR5, worst bar or stress loss) of the combined
two-leg book against unhedged, exposure-matched reduced and cash, with fees, slippage, carry/funding, open-position marks,
the next-open fill convention and the stress cases already in `compose.hedge`, on SEALED independent protected data.

Frozen inputs never refit on protected values: the hedge ratio is the development-only estimate carried in the freeze
(`ratio.method == 'fixed'`), the entry rule is the frozen combination, and `refit_on_protected` is False. A protected look is
shared with the envelope's single look (`looks`), registered BEFORE any protected price is read; an interruption leaves the
look spent and the result INCONCLUSIVE, never retried.

Statistical admission adds a frozen episode-reliability comparison against each
control, using the same shared error-budget ledger. Its conditional-null
assumption and limited claim are explicit in objective_admission.PROTOCOL.
Full-window economics and cash dominance remain separate required checks.
The numerical component calibration does not replace full-search calibration;
CONFIGURED authority requires both current reports and genuine forward seals.

"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ...core.types import TF_MS
from ..compose import hedge as H
from ..combo import Combination
from . import ClosureRefused, digest, exposure, families, manifest
from . import development as D
from .development import require_current
from . import objective_admission as OA

FREEZE_SCHEMA = 'closure-hedge-freeze.v1'
QUAL_SCHEMA = 'closure-hedge-qualification.v1'
RULE_ID = 'protective-risk-objective-assessment.v1'
RETURN_RULE_ID = 'relative-value-net-return-assessment.v1'
PROTECTIVE = ('protective_beta', 'protective_directional', 'stress_loss')
STATUSES = ('QUALIFIED', 'ASSESSED_SUPPORTED', 'ASSESSED_NOT_SUPPORTED', 'DOMINATED_BY_CASH', 'INCONCLUSIVE', 'BLOCKED')
MIN_ENTRIES = 3

#: Registration alone grants no authority; current approved calibration is required.
ADMISSION_METHODS: dict = {OA.METHOD_ID: OA.evaluate}


def assessment_rule(spec):
    return RETURN_RULE_ID if spec['purpose'] == 'relative_value' else RULE_ID


def registered_method(cfg):
    """The registered, calibrated statistical admission method named by the configuration, else None."""
    r = (((cfg or {}).get('research') or {}).get('hedge_qualification'))
    if not isinstance(r, dict) or not r.get('approved') or not r.get('calibration_id'):
        return None
    if r.get('method_id')!=OA.METHOD_ID or r.get('version')!=OA.PROTOCOL['version']:
        return None
    try:
        report=json.loads(Path(r['calibration_report_path']).read_text())
        if not OA.report_valid(report,r['calibration_id']):return None
    except (OSError,ValueError,KeyError,TypeError):return None
    return dict(r)


# ── frozen finalist ─────────────────────────────────────────────────────────
def _margin(result) -> float:
    per = next(iter((result.get('hedge') or {}).get('per_symbol', {}).values()), None) or {}
    c, m = per.get('comparators') or {}, (result.get('hedge') or {}).get('objective', {}).get('metric')
    try:
        if (result.get('hedge') or {}).get('purpose') == 'relative_value':
            return float(c['hedged'][m]) - max(0.0, float(c['unhedged'][m]), float(c['reduced'][m]))
        return float(c['unhedged'][m]) - float(c['hedged'][m])
    except (KeyError, TypeError):
        return float('-inf')


def freeze_hedge_finalist(store, env: dict, results: list):
    """Deterministically freeze the best HEDGE_SUPPORTED development result of this envelope (largest objective
    improvement over unhedged, ties by candidate id). Carries the DEVELOPMENT-ONLY ratio, spec and combination."""
    manifest.verify_envelope(env)
    mine = [r for r in results if r.get('stage') == 'DEVELOPMENT' and r.get('envelope_id') == env['envelope_id']
            and (r.get('hedge') or {}).get('per_symbol')]
    ok = [r for r in mine if r['outcome'] == 'HEDGE_SUPPORTED' and r.get('combo')]
    if not ok:
        return None
    best = max(ok, key=lambda r: (_margin(r), r['candidate_id']))
    sym, per = next(iter(best['hedge']['per_symbol'].items()))
    spec = per['effective_spec']
    if H.capital_enabled(spec) and per.get('capital_policy_id')!=H.K.POLICY_ID:
        raise ClosureRefused('hedge_development_capital_policy_differs')
    body = dict(schema=FREEZE_SCHEMA, envelope_id=env['envelope_id'], candidate=store.get_once('candidates', best['candidate_id']),
                capital_policy_id=H.K.POLICY_ID if H.capital_enabled(spec) else None,
                development_result_id=best['result_id'], combo=best['combo'], combo_hash=best['combo_hash'],
                hedge_spec=spec, primary_symbol=sym,
                # a fixed/ols ratio is frozen as ONE development-estimated number; rolling_beta freezes the estimation PROCEDURE
                # (lookback, bounds, causal timing) which is then applied to protected bars without choosing or refitting anything
                ratio_frozen=float(per['ratio']) if spec['ratio']['method'] == 'fixed' else None,
                ratio_procedure=dict(spec['ratio']) if spec['ratio']['method'] != 'fixed' else None, purpose=spec['purpose'],
                objective=spec['objective'], funding_basis=per.get('funding_basis'), cost_model=per.get('cost_model'),
                development_comparators=per['comparators'], refit_on_protected=False,
                search_history=dict(trials=len(mine), outcomes={o: sum(1 for r in mine if r['outcome'] == o)
                                                                 for o in sorted({r['outcome'] for r in mine})},
                                    result_ids=sorted(r['result_id'] for r in mine)),
                authority='RESEARCH_ONLY',admission_protocol=dict(OA.PROTOCOL))
    freeze = dict(body, freeze_id=digest(body))
    store.put_once('freezes', freeze['freeze_id'], freeze)
    return freeze


def verify_hedge_freeze(freeze: dict, env: dict) -> Combination:
    if freeze.get('schema') != FREEZE_SCHEMA or freeze.get('freeze_id') != digest({k: v for k, v in freeze.items() if k != 'freeze_id'}):
        raise ClosureRefused('hedge_freeze_integrity')
    if freeze['envelope_id'] != env['envelope_id']:
        raise ClosureRefused('freeze_envelope_mismatch')
    families.verify_candidate(freeze['candidate'])
    spec = H.validate(freeze['hedge_spec'])
    if H.capital_enabled(spec) and freeze.get('capital_policy_id')!=H.K.POLICY_ID:
        raise ClosureRefused('hedge_frozen_capital_policy_differs')
    if env.get('policy_class')=='CONFIGURED':
        from ..compose.managed_hedge import require_current
        require_current(spec)
    r = spec['ratio']
    if freeze['refit_on_protected'] is not False:
        raise ClosureRefused('hedge_ratio_not_frozen')
    if r['method'] == 'fixed':
        if freeze.get('ratio_frozen') is None or float(r['value']) != float(freeze['ratio_frozen']) or freeze.get('ratio_procedure') is not None:
            raise ClosureRefused('hedge_ratio_not_frozen')
    elif r['method'] == 'rolling_beta':
        if freeze.get('ratio_frozen') is not None or freeze.get('ratio_procedure') != r or 'lookback' not in r or 'max_ratio' not in r:
            raise ClosureRefused('hedge_ratio_procedure_not_frozen')
    else:                                           # an un-frozen estimator (e.g. ols_beta with a fit_end) would choose on outcomes
        raise ClosureRefused('hedge_ratio_not_frozen', str(r['method']))
    if freeze['purpose'] != spec['purpose'] or freeze['objective'] != spec['objective']:
        raise ClosureRefused('hedge_objective_not_frozen')
    c = Combination.from_dict(freeze['combo'])
    if c.hash != freeze['combo_hash'] or c.evaluation_scope != env['envelope_id'] or c.trigger != freeze['candidate']['candidate_id']:
        raise ClosureRefused('freeze_combination_mismatch')
    return c


# ── the predeclared assessment rule (pure) ──────────────────────────────────
def rederive_verdict(res: dict, spec: dict) -> tuple:
    """compose.hedge.evaluate's verdict rule, re-derived from its own recorded comparators and folds, so a packet cannot
    carry a verdict its numbers do not support."""
    m = res['objective']
    relative = spec.get('purpose') == 'relative_value'
    expected = (spec.get('objective') or {}).get('metric')
    if (expected is not None and m != expected) or (relative and m != 'net_return_pct'):
        raise ClosureRefused('hedge_objective_mismatch')
    mh, mu, mr = res['comparators']['hedged'], res['comparators']['unhedged'], res['comparators']['reduced']
    folds = res['folds']
    if relative:
        wins = sum(1 for f in folds if all(f.get(k) is not None and np.isfinite(f[k]) for k in ('hedged', 'unhedged', 'reduced'))
                   and f['hedged'] > max(0.0, f['unhedged'], f['reduced']))
    else:
        wins = sum(1 for f in folds if f['hedged'] < f['unhedged'])
    majority = bool(folds) and wins > len(folds) / 2
    if not all(x['risk_complete'] and x.get(m) is not None and np.isfinite(x[m]) for x in (mh, mu, mr)):
        return 'INSUFFICIENT_EVIDENCE'
    if mh['entries'] < MIN_ENTRIES:
        return 'INSUFFICIENT_EVIDENCE'
    if mh['liquidated']:
        return 'ECONOMIC_UNSUPPORTED'
    improvement = mh[m] > max(0.0, mu[m], mr[m]) if relative else mh[m] < mu[m] and mh[m] < mr[m]
    if improvement and majority:
        return 'HEDGE_SUPPORTED'
    return 'ECONOMIC_UNSUPPORTED'


def assess(res: dict, spec: dict) -> tuple:
    """(status, reason) from one protected combined-book result under the frozen spec. Deterministic; no tuning."""
    if H.capital_enabled(spec) and res.get('capital_policy_id')!=H.K.POLICY_ID:
        raise ClosureRefused('hedge_assessment_capital_policy_differs')
    if spec['purpose'] not in (*PROTECTIVE, 'relative_value'):
        raise ClosureRefused('hedge_purpose')
    if spec['purpose'] == 'relative_value' and spec['objective']['metric'] != 'net_return_pct':
        return 'BLOCKED', 'relative_value_requires_return_objective'
    verdict = rederive_verdict(res, spec)
    if verdict != res['verdict']:
        raise ClosureRefused('hedge_verdict_not_rederivable')
    mh = res['comparators']['hedged']
    m = res['objective']
    if verdict == 'INSUFFICIENT_EVIDENCE':
        return 'INCONCLUSIVE', res['reason']
    if verdict != 'HEDGE_SUPPORTED':
        return 'ASSESSED_NOT_SUPPORTED', res['reason']
    cash_dominates = spec['purpose'] in PROTECTIVE and mh['net_return_pct'] is not None and mh['net_return_pct'] < 0.0 and mh[m] >= 0.0   # cash: return 0, risk 0
    constraint=spec.get('primary_exposure_constraint')
    measured=mh.get('minimum_retained_net_fraction')
    minimum=constraint.get('min_net_exposure') if isinstance(constraint,dict) else None
    constrained=(isinstance(constraint,dict) and set(constraint)=={'min_net_exposure'}
        and type(minimum) in (int,float) and np.isfinite(minimum) and 0<minimum<=1
        and res.get('spec_id')==spec.get('spec_id') and bool(spec.get('spec_id'))
        and type(measured) in (int,float) and np.isfinite(measured) and measured+1e-12>=minimum
        and type(mh.get('primary_active_bars')) is int and mh['primary_active_bars']>0
        and mh.get('retained_exposure_basis')=='PRIMARY_DIRECTION_NET_NOTIONAL_OVER_CURRENT_COMBINED_EQUITY')
    if cash_dominates and not constrained:
        return 'DOMINATED_BY_CASH', (f'cash has higher return (0 > {mh["net_return_pct"]:.3f}) and no greater {m}; '
                                     'no verified feasible exposure constraint in the frozen spec and measured path')
    return 'ASSESSED_SUPPORTED', res['reason']


def _packet(status, reason, env, freeze, *, result=None, method=None, admission=None, context_id=None, gaps=()):
    body = dict(schema=QUAL_SCHEMA, qualification_id=digest(['hedge-qualification', freeze['freeze_id']]), status=status,
                reason=reason, envelope=env, freeze=freeze, authority_class=env['policy_class'], rule_id=assessment_rule(freeze['hedge_spec']),
                statistical_admission=dict(status='ADMITTED' if status == 'QUALIFIED' else
                                           ('NONE_REGISTERED' if method is None else 'NOT_ADMITTED'),
                                           method=None if method is None else {k: method.get(k) for k in ('method_id', 'version', 'calibration_id')},
                                           evidence=admission),
                protected_result=result, protected_fingerprint=env.get('protected_fingerprint'),
                exposure_proofs=env['exposure_proofs'], context_fingerprint=context_id, gaps=list(gaps),
                frozen=dict(ratio=freeze.get('ratio_frozen'), ratio_procedure=freeze.get('ratio_procedure'), refit_on_protected=False,
                            combo_hash=freeze['combo_hash']))
    return dict(body, evidence_id=digest(body))


def _frame(feed_path, env, sym, window):
    from ...strategy import spec_evidence                                   # noqa: F401  (frames_for used by caller)
    feed = manifest._feed(feed_path, window[1] + TF_MS[env['tf']])
    try:
        df = feed.replay_ohlcv(sym, env['tf'], 200000)
    finally:
        if getattr(feed._local, 'conn', None) is not None:
            feed.db.close()
    if df is None or not len(df):
        return None
    ms = manifest._ms(df['ts'])
    return df[(ms >= window[0]) & (ms + TF_MS[env['tf']] <= window[1] + TF_MS[env['tf']])].reset_index(drop=True)


def protected_evaluation(freeze: dict, env: dict, c: Combination, sealed: Path, external: dict | None, derivs_path=None) -> dict:
    """The frozen hedge on the sealed protected window: entries from the FROZEN combination, the frozen ratio (a number, or the
    frozen rolling procedure applied causally), next-open fills on ONE full UTC clock of both legs (a missing bar stays an
    unknown mark and an entry cannot fill after a gap), signed funding bound to the sealed derivative file for BOTH legs.
    Runs inside the bounded child. Raises ClosureRefused on any missing frame/funding (never substitutes)."""
    from ...strategy import spec_evidence
    from ...strategy.compile import compile_spec
    from ...strategy.vector_backtest import funding_for
    spec = freeze['hedge_spec']
    s = env['split']
    psym, hsym = spec['primary']['symbol'], spec['hedge']['symbol']
    window = (s['start_ms'], s['end_ms'])
    # every envelope symbol, exactly the universe/BTC context development evaluated the frozen rule against
    allf = {sym: _frame(sealed, env, sym, window) for sym in env['discovery_symbols'] + env['heldout_symbols']}
    missing = [k for k in (psym, hsym) if allf.get(k) is None]
    if missing:
        raise ClosureRefused('hedge_protected_frames_missing', ','.join(missing))
    frames = {k: v for k, v in allf.items() if v is not None}
    df, hdf = frames[psym], frames[hsym]
    risk = dict(env['risk'])
    if risk.get('real_funding', False):
        ext_f = (external or {}).get('funding_context')
        if ext_f is None:
            raise ClosureRefused('hedge_funding_binding_missing', 'real funding requires the sealed derivative file; no global fallback')
        # bind exactly as development does (development.bind_funding): signed funding aligned to each leg's own frame, no fallback
        D.bind_funding({psym: df, hsym: hdf}, ext_f, env, 'protected_sealed_window')
        if ext_f['gaps']:
            raise ClosureRefused('hedge_funding_incomplete', ';'.join(g['reason'] for g in ext_f['gaps'])[:160])
    compiled = compile_spec(c.to_spec())
    sf = spec_evidence.frames_for(df, env['tf'])
    ext = external or {}
    btc = {env['tf']: frames['BTC/USDT']} if 'BTC/USDT' in frames else None
    lo, sh = compiled.entries(sf, btc=btc, derivs=(ext.get('derivs') or {}).get(psym),
                              universe={k: {env['tf']: v} for k, v in frames.items()},
                              market=ext.get('market'), symbol=psym, world=ext.get('world'))
    from ...strategy import fill_timing
    need = fill_timing.history_requirements(compiled.spec)
    rr = spec['ratio']
    al = H.align_pair(df, hdf, lo, sh, TF_MS[env['tf']], p_window=need['base'],
                      h_window=(int(rr['lookback']) + 1) if rr['method'] == 'rolling_beta' else 1)
    n = al['bars']
    cost = TF_MS[env['tf']] / 28_800_000
    if risk.get('real_funding', False):
        pf, hf = funding_for(psym, df, risk), funding_for(hsym, hdf, risk)
        if pf is None or hf is None or not (np.isfinite(pf).all() and np.isfinite(hf).all()):
            raise ClosureRefused('hedge_funding_incomplete', 'signed funding must cover every protected bar of both legs')
        funding, flat = (H.place(al['idx_p'], pf, n) * cost, H.place(al['idx_h'], hf, n) * cost), 0.0
    else:
        funding, flat = None, abs(float(risk.get('funding_rate_8h', 0.0))) * cost
    exits = H.aligned_exits(compiled, sf, al, btc=btc, derivs=(ext.get('derivs') or {}).get(psym),
                            universe={k: {env['tf']: v} for k, v in frames.items()},
                            market=ext.get('market'), symbol=psym, world=ext.get('world'))
    primary_exit,decision_bars=H.primary_exit_inputs(compiled,df,al,hedge_frame=hdf)
    res = H.evaluate(spec, al['p'], al['h'], al['sig'], exits, opens=(al['po'], al['ho']), funding=funding,
                     flat_funding_rate=flat,assessment_episode_bars=(freeze.get('admission_protocol') or {}).get('episode_bars'),
                     reduced_fraction=freeze['development_comparators']['reduced']['fraction'],primary_exit=primary_exit,decision_bars=decision_bars)
    res['funding_basis'] = 'RETAINED_SIGNED_HISTORY' if funding is not None else 'CONSERVATIVE_FLAT_EIGHT_HOUR'
    res['alignment'] = {k: al[k] for k in ('refused', 'bars', 'missing_primary', 'missing_hedge')}
    res['window_bars'] = int(n)
    return res


def protected_job(payload: dict) -> dict:
    """Child entry: the whole protected evaluation, including loading the sealed context, in the bounded worker."""
    from ..compose import data as cdata
    freeze, env = payload['freeze'], payload['env']
    c = Combination.from_dict(freeze['combo'])
    s = env['split']
    psym = freeze['hedge_spec']['primary']['symbol']
    external = None
    cut = int(s['end_ms']) + TF_MS[env['tf']]
    if payload['requires']:
        external = cdata.load_external(list(payload['requires']), payload['sealed'], payload.get('sealed_derivs'), [psym],
                                       cut, window_start_ms=int(s['start_ms']), world_path=payload.get('sealed_world'))
        if external['gaps']:
            raise ClosureRefused('hedge_context_unservable', ';'.join(f"{g['requirement']}: {g['reason']}" for g in external['gaps'])[:200])
    if (env['risk'] or {}).get('real_funding', False):
        legs = [freeze['hedge_spec']['primary']['symbol'], freeze['hedge_spec']['hedge']['symbol']]
        funding = cdata.load_external(['funding'], payload['sealed'], payload.get('sealed_derivs'), legs, cut,
                                      window_start_ms=int(s['start_ms']))
        external = dict(external or {}, funding_context=funding)
    return protected_evaluation(freeze, env, c, Path(payload['sealed']), external, payload.get('sealed_derivs'))


def authority_blocks(env, cfg, source_journal=None,budget_journal=None) -> list:
    """Why a CONFIGURED hedge look must not be taken yet (closed gates honoured, never routed around)."""
    from . import qualification as Q
    if env['policy_class'] != 'CONFIGURED':
        return []
    out = []
    if not ((cfg or {}).get('research') or {}).get('referee', False):
        out.append('production_referee_gate_closed')
    if not Q.calibration_approved(cfg, env):
        out.append('calibration_not_approved')
    if registered_method(cfg) is None:
        out.append('hedge_admission_method_not_registered')
    if not source_journal or not budget_journal or Path(source_journal).resolve()!=Path(budget_journal).resolve():
        out.append('canonical_shared_error_budget_required')
    if manifest.rederive_proofs(env) != env['exposure_proofs'] or set(env['exposure_proofs'].values()) != {'FORWARD_SEAL'}:
        out.append('exposure_proof_not_forward_seal')
    return out


def _funding_ready(sealed_derivs, symbols, window, step) -> list:
    """Metadata only (counts and first/last stamps, no values): both legs must have retained signed funding spanning the protected
    window, else the hedge is BLOCKED before a look is spent."""
    import sqlite3
    from contextlib import closing
    from ..predictive_experiment import readonly
    gaps = []
    if sealed_derivs is None or not Path(sealed_derivs).is_file():
        return [f'{s}: sealed derivative file absent' for s in symbols]
    with closing(readonly(sealed_derivs)) as db:
        for sym in symbols:
            try:
                n, lo, hi = db.execute("SELECT COUNT(*),MIN(ts),MAX(ts) FROM derivs WHERE symbol=? AND series='funding' AND ts>=? AND ts<=?",
                                       (sym, window[0] - 8 * 3_600_000, window[1] + step)).fetchone()
            except sqlite3.Error:
                n, lo, hi = 0, None, None
            if not n or lo > window[0] + 8 * 3_600_000 or hi < window[1] - 8 * 3_600_000:
                gaps.append(f'{sym}: funding does not span the protected window ({n} rows)')
    return gaps


def qualify_hedge(store, freeze: dict, env: dict, snapshot_path, workdir, *, now_ms: int, cfg=None, run=None,
                  derivs_path=None, world_path=None, timeout_s: float = 900.0,source_journal=None,budget_journal=None) -> dict:
    """Run (or recall) the independent protected assessment. Idempotent: a recorded packet is returned unchanged and a
    spent look is never retaken, interrupted or not. The evaluator executes in the provided bounded `run` (or a killable
    child) AFTER the look is registered; a timeout or crash is a spent, INCONCLUSIVE look with no retry."""
    c = verify_hedge_freeze(freeze, env)
    require_current(env)
    qid = digest(['hedge-qualification', freeze['freeze_id']])
    done = store.get_once('qualifications', qid)
    if done is not None:
        return done

    def record(p):
        store.put_once('qualifications', qid, p)
        return p

    spec = freeze['hedge_spec']
    method = registered_method(cfg)
    if spec['purpose'] not in PROTECTIVE and method is None:
        return record(_packet('BLOCKED', 'relative_value_return_method_not_registered (no look spent)', env, freeze,
                              gaps=['return evidence method for relative-value pairs'], method=method))
    gate = authority_blocks(env, cfg,source_journal,budget_journal)
    if gate:
        return _packet('BLOCKED', 'authority_blocked: ' + ','.join(gate) + ' (no look spent)', env, freeze)   # not recorded: may open later
    if env['protected_status'] != 'ELIGIBLE':
        return record(_packet('BLOCKED', 'protected_unavailable: ' + ';'.join(env['protected_reasons'])[:300], env, freeze))
    deployment = H.deployment_contract(spec)
    prior = store.get_once('looks', env['envelope_id'])
    if prior is not None and prior['freeze_id'] != freeze['freeze_id']:
        return record(_packet('BLOCKED', 'protected_window_already_looked_by_another_candidate', env, freeze))
    if prior is not None:        # same freeze, no recorded result: the look was spent and interrupted
        return record(_packet('INCONCLUSIVE', 'look_spent_but_not_completed (interrupted); no retry permitted', env, freeze, method=method))
    events = exposure.events(store)
    s = env['split']
    step = TF_MS[env['tf']]
    universe = env['discovery_symbols'] + env['heldout_symbols']
    for sym in universe:
        state, _ = manifest.exposure_state(events, sym, s['start_ms'], s['end_ms'], env['protected_first_available'].get(sym),
                                           allow_synthetic=env['policy_class'] == 'TEST_ONLY')
        if state != 'UNEXPOSED':
            return record(_packet('BLOCKED', f'protected_exposure_{state.lower()}:{sym}', env, freeze))
    legs = [spec['primary']['symbol'], spec['hedge']['symbol']]
    for sym in legs:
        if sym not in universe:
            return record(_packet('BLOCKED', f'hedge_leg_not_in_envelope_universe:{sym}', env, freeze))
    # frozen input context: the protected window carries only its own bars, so the rule's warmup must fit INSIDE it. A window too
    # short would silently evaluate a different procedure; it is refused, before any look.
    from ...strategy import fill_timing
    from ...strategy.compile import compile_spec
    try:
        need = fill_timing.history_requirements(compile_spec(c.to_spec()).spec)       # the rule's ACTUAL dependency incl. fitted inputs
    except Exception as exc:                                                       # noqa: BLE001 - unknown dependency: refuse, never assume
        return record(_packet('BLOCKED', f'history_requirement_unknown:{type(exc).__name__} (no look spent)', env, freeze,
                              gaps=['rule dependency window']))
    ratio = spec['ratio']
    warm = max(need['base'], (int(ratio['lookback']) + 1) if ratio['method'] == 'rolling_beta' else 1,
               *[-(-b * TF_MS[t] // step) for t, b in need['htf'].items()])
    bars = (s['end_ms'] - s['start_ms']) // step + 1
    if need['exceeds_bound'] or bars < warm + 3 * MIN_ENTRIES:
        return record(_packet('BLOCKED', f'insufficient_protected_warmup: {bars} bars < {warm} bars the frozen rule/ratio procedure consumes '
                                         f'+ usable window (no look spent)', env, freeze, gaps=['protected warmup']))
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    sealed = workdir / 'protected-hedge.db'
    try:
        fp = manifest.freeze_window(snapshot_path, sealed, env)
    except ClosureRefused as exc:
        return record(_packet('BLOCKED', f'snapshot_unverifiable:{exc.code}', env, freeze))
    if fp != env['protected_fingerprint']:
        return record(_packet('BLOCKED', 'protected_data_changed_since_envelope', env, freeze))
    needs = [r for r in c.requires if r not in ('ohlcv', 'world')]
    real_funding = bool((env['risk'] or {}).get('real_funding', False))
    ctx_ids, sealed_derivs, sealed_world = [], workdir / 'protected-hedge-derivs.db', workdir / 'protected-hedge-world.db'
    sealed_derivs.unlink(missing_ok=True)
    if needs or real_funding:
        # dependency-complete BEFORE any look: missing context/funding would read NaN and burn the look as "no edge"
        try:
            ctx = manifest.freeze_context(snapshot_path, derivs_path, sealed, sealed_derivs, env,
                                          needs + (['funding'] if real_funding and 'funding' not in needs else []))
        except ClosureRefused as exc:
            return record(_packet('BLOCKED', f'context_unverifiable:{exc.code}', env, freeze))
        gaps = [g for g in ctx['gaps'] if not (real_funding and g['requirement'] == 'funding' and 'funding' not in needs)]
        if gaps:
            return _packet('BLOCKED', 'context_missing_in_protected_window (no look spent): ' +
                           ';'.join(f"{g['requirement']}: {g['reason']}" for g in gaps)[:300], env, freeze)
        ctx_ids.append(ctx['fingerprint'])
    if real_funding:
        fgaps = _funding_ready(sealed_derivs if sealed_derivs.exists() else None, legs, (s['start_ms'], s['end_ms']), step)
        if fgaps:
            return _packet('BLOCKED', 'funding_dependency_incomplete (no look spent): ' + '; '.join(fgaps)[:300], env, freeze)
    if 'world' in c.requires:
        from ..compose.world_snapshot import seal_world
        try:
            ctx_ids.append(seal_world(world_path, sealed_world, env))      # the alpha qualifier's exact-cut protected seal
        except ClosureRefused as exc:
            return _packet('BLOCKED', f'world_context_unavailable:{exc.code} (no look spent)', env, freeze)
    ctx_id = digest(ctx_ids) if ctx_ids else None
    if method is not None:
        if freeze.get('admission_protocol')!=OA.PROTOCOL:
            return record(_packet('BLOCKED','admission_protocol_not_frozen (no look spent)',env,freeze,method=method))
        payload=dict(freeze=freeze,env=env,sealed=str(sealed),requires=needs+(['world'] if 'world' in c.requires else []),
                     sealed_derivs=str(sealed_derivs) if sealed_derivs.exists() else None,
                     sealed_world=str(sealed_world) if sealed_world.exists() else None)
        return _reserved_assessment(store,freeze,env,c,payload,method,record,run,timeout_s,now_ms,ctx_id,deployment,
                                    Path(budget_journal) if budget_journal else workdir/'hedge-qualification.db')
    # the look is registered BEFORE any protected price is read or the child is dispatched, and is charged whatever follows
    store.put_once('looks', env['envelope_id'], dict(freeze_id=freeze['freeze_id'], kind='hedge_relative_value' if spec['purpose'] == 'relative_value' else 'hedge_protective', rule_id=assessment_rule(spec),
                                                    method=None if method is None else method.get('method_id')))
    exposure.mark(store, 'PROTECTED_EXPOSED', s['start_ms'], s['end_ms'], now_ms, f'hedge qualification look {c.hash}')
    payload = dict(freeze=freeze, env=env, sealed=str(sealed), requires=needs + (['world'] if 'world' in c.requires else []),
                   sealed_derivs=str(sealed_derivs) if sealed_derivs.exists() else None,
                   sealed_world=str(sealed_world) if sealed_world.exists() else None)
    try:
        if run is None:
            from ...core.child import run_child
            out = run_child(protected_job, payload, timeout_s=timeout_s, nice=19, memory_mb=3072)
        else:
            out = run(protected_job, payload)
    except Exception as exc:                                                       # noqa: BLE001
        return record(_packet('INCONCLUSIVE', f'look_spent_dispatch_failed:{getattr(exc, "code", type(exc).__name__)}:{str(exc)[:120]}; '
                                              'no retry permitted', env, freeze, method=method))
    if getattr(out, 'timed_out', False):
        return record(_packet('INCONCLUSIVE', 'look_spent_evaluation_timed_out; no retry permitted', env, freeze, method=method))
    if not getattr(out, 'ok', False) or not isinstance(getattr(out, 'value', None), dict):
        return record(_packet('INCONCLUSIVE', 'look_spent_evaluation_failed: ' + str(getattr(out, 'error', ''))[-160:] +
                              '; no retry permitted', env, freeze, method=method))
    res = out.value
    status, reason = assess(res, spec)
    admission = None
    return record(_packet(status, reason, env, freeze, result=dict(res, deployment=deployment), method=method,
                          admission=admission, context_id=ctx_id))


def verify_hedge_qualification(q: dict, cfg=None) -> dict:
    """Pure re-derivation from the packet's own records. Never trusts `status`."""
    body = {k: v for k, v in q.items() if k != 'evidence_id'}
    if q.get('schema') != QUAL_SCHEMA or q.get('evidence_id') != digest(body):
        raise ClosureRefused('hedge_qualification_integrity')
    env, freeze = q['envelope'], q['freeze']
    manifest.verify_envelope(env)
    verify_hedge_freeze(freeze, env)
    if q['qualification_id'] != digest(['hedge-qualification', freeze['freeze_id']]) or q['rule_id'] != assessment_rule(freeze['hedge_spec']):
        raise ClosureRefused('hedge_qualification_id_mismatch')
    if q['status'] not in STATUSES:
        raise ClosureRefused('hedge_status_unknown')
    if q['protected_fingerprint'] != env.get('protected_fingerprint') or q['authority_class'] != env['policy_class']:
        raise ClosureRefused('hedge_binding_mismatch')
    if manifest.rederive_proofs(env) != env['exposure_proofs'] or env['exposure_proofs'] != q['exposure_proofs']:
        raise ClosureRefused('exposure_proof_not_rederivable')
    res = q.get('protected_result')
    if q['status'] in ('QUALIFIED', 'ASSESSED_SUPPORTED', 'ASSESSED_NOT_SUPPORTED', 'DOMINATED_BY_CASH'):
        if not isinstance(res, dict):
            raise ClosureRefused('hedge_protected_result_missing')
        status, _ = assess(res, freeze['hedge_spec'])
        expected = 'ASSESSED_SUPPORTED' if q['status'] == 'QUALIFIED' else q['status']
        if status != expected:
            raise ClosureRefused('hedge_status_not_rederivable')
    if q['status'] == 'QUALIFIED':
        adm = q['statistical_admission']
        if adm['status'] != 'ADMITTED' or not adm.get('method') or adm['method']['method_id'] not in ADMISSION_METHODS:
            raise ClosureRefused('hedge_admission_not_registered')
        proof=(res or {}).get('error_budget') or {}
        alpha=(adm.get('evidence') or {}).get('alpha')
        _verify_budget(proof,env,freeze,adm['evidence'])
        again = ADMISSION_METHODS[adm['method']['method_id']](dict(alpha=alpha,freeze_id=freeze['freeze_id'], result={k: v for k, v in res.items() if k not in ('deployment','error_budget')}))
        if again.get('admitted') is not True or again.get('evidence') != adm.get('evidence'):
            raise ClosureRefused('hedge_admission_not_rederivable')
        if env['policy_class'] == 'CONFIGURED' and set(env['exposure_proofs'].values()) != {'FORWARD_SEAL'}:
            raise ClosureRefused('configured_authority_requirements_unmet')
    return q


def _reserved_assessment(store,freeze,env,combo,payload,method,record,run,timeout_s,now_ms,ctx_id,deployment,budget_path):
    from ...core.journal import Journal
    from ...core.child import run_child
    from ..ledger import Ledger
    import fcntl
    budget_path.parent.mkdir(parents=True,exist_ok=True)
    journal=Journal(budget_path);ledger=Ledger(journal);ledger.ensure()
    key=digest(['objective-look',freeze['freeze_id']])
    with open(str(budget_path)+'.reserve','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        policy=env['policy']
        budget=ledger.register_budget(policy['fdr_target'],policy['lord_w0'])
        if budget!=(policy['fdr_target'],policy['lord_w0']):
            return record(_packet('BLOCKED','registered_error_budget_differs_from_policy',env,freeze,method=method))
        if ledger.spent(key) is not None:
            return record(_packet('INCONCLUSIVE','canonical_look_already_spent; no retry',env,freeze,method=method))
        number,alpha=ledger.next_alpha(*budget)
        count=(env['split']['end_ms']-env['split']['start_ms'])//TF_MS[env['tf']]+1
        if (count//OA.PROTOCOL['episode_bars'])*np.log1p(OA.PROTOCOL['bet']) < -np.log(alpha):
            return record(_packet('INCONCLUSIVE','episode_window_cannot_resolve_registered_alpha (no look spent)',env,freeze,method=method))
        detail=dict(cut_ms=int(env['split']['cut_ms']),evaluated=dict(combo_hash=combo.hash,freeze_id=freeze['freeze_id'],
            admission_protocol_id=OA.protocol_id()),look_state='pending',envelope_id=env['envelope_id'],method_id=OA.METHOD_ID)
        ledger.record_test(key,combo.tf,combo.geo,'gate1',1.,alpha,False,detail)
        store.put_once('looks',env['envelope_id'],dict(freeze_id=freeze['freeze_id'],kind='objective',alpha_t=alpha,t=number,canonical_hash=key))
        exposure.mark(store,'PROTECTED_EXPOSED',env['split']['start_ms'],env['split']['end_ms'],now_ms,'objective qualification '+key)
        try:
            outcome=run(protected_job,payload) if run else run_child(protected_job,payload,timeout_s=timeout_s,nice=19,memory_mb=3072)
            if not getattr(outcome,'ok',False) or getattr(outcome,'timed_out',False) or not isinstance(getattr(outcome,'value',None),dict):
                return record(_packet('INCONCLUSIVE','look_spent_evaluation_incomplete; no retry',env,freeze,method=method))
            result=outcome.value;status,reason=assess(result,freeze['hedge_spec'])
            admission=OA.evaluate(dict(result=result,alpha=alpha))
            qualified=status=='ASSESSED_SUPPORTED' and admission['admitted']
            p=admission['evidence'].get('p',1.) if qualified else 1.
            complete=dict(detail,look_state='complete',reason=reason,objective_evidence=admission['evidence'])
            ledger.complete_look(key,p,complete,'referee_passed' if qualified else 'gate1_fail')
            proof=dict(budget=dict(journal.query('SELECT * FROM research_budget WHERE id=1')[0]),
                       sequence=[dict(r) for r in ledger.tests()],canonical_hash=key)
            if qualified:status,reason='QUALIFIED',reason+'; frozen episode objective reliability admitted'
            return record(_packet(status,reason,env,freeze,result=dict(result,deployment=deployment,error_budget=proof),
                method=method,admission=admission['evidence'],context_id=ctx_id))
        except Exception as exc:
            return record(_packet('INCONCLUSIVE','look_spent_'+type(exc).__name__+': '+str(exc)[:120]+'; no retry',env,freeze,method=method))


def _verify_budget(proof,env,freeze,evidence):
    from .. import fdr
    rows=proof.get('sequence') or [];budget=proof.get('budget') or {}
    key=digest(['objective-look',freeze['freeze_id']])
    found=[(i,r) for i,r in enumerate(rows,1) if r['hash']==key and r['gate']=='gate1']
    if len(found)!=1 or proof.get('canonical_hash')!=key:
        raise ClosureRefused('objective_error_budget_missing')
    i,row=found[0]
    if (len({r['seq'] for r in rows})!=len(rows) or len({(r['hash'],r['gate']) for r in rows})!=len(rows)
            or [r['seq'] for r in rows]!=sorted(r['seq'] for r in rows)
            or any(not 0<=r['p']<=1 or not 0<r['alpha_t']<1 or bool(r['rejected'])!=(r['p']<=r['alpha_t']) for r in rows)):
        raise ClosureRefused('objective_error_budget_sequence_invalid')
    prior=[n for n,r in enumerate(rows[:i-1],1) if r['rejected']]
    policy=env['policy']
    if ((budget.get('alpha'),budget.get('w0'))!=(policy['fdr_target'],policy['lord_w0'])
            or row['alpha_t']>fdr.alpha_at(i,prior,budget['alpha'],budget['w0'])+1e-12
            or evidence.get('alpha')!=row['alpha_t'] or row['p']!=evidence.get('p') or not row['rejected']
            or row['p']>row['alpha_t']):
        raise ClosureRefused('objective_error_budget_invalid')
    detail=json.loads(row['detail'])
    if (detail.get('look_state')!='complete' or detail.get('evaluated',{}).get('freeze_id')!=freeze['freeze_id']
            or detail.get('evaluated',{}).get('admission_protocol_id')!=OA.protocol_id()
            or detail.get('cut_ms')!=env['split']['cut_ms'] or detail.get('envelope_id')!=env['envelope_id']
            or detail.get('objective_evidence')!=evidence or freeze.get('admission_protocol')!=OA.PROTOCOL):
        raise ClosureRefused('objective_error_budget_binding_differs')
