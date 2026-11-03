"""P3a — historical development on a frozen envelope.

Fits ONLY on rows known by the envelope's `dev_end_ms` (the frames come from
`manifest.development_frames`, which cannot select later rows), measures
thresholds on that window, evaluates the candidate and its declared one-part
baselines with the existing `evaluate.evaluate` and `growth.earns_place`, and
returns a typed outcome:

  DEVELOPMENT_POSITIVE  eligible to be frozen — NOT admission, NOT a pass
  NEGATIVE_ECONOMICS    measured and powered, but the economics do not hold
  INCONCLUSIVE_POWER    too few trades/markets to judge; says nothing about edge
  BLOCKED_DATA          required data absent or not point-in-time
  CODE_FAILURE          the implementation failed; says nothing about economics

Positions still open at the end of the development window are censored by the
engine, so no development outcome can cross into the protected window.
Compositional candidates advance on their own economics and uncertainty;
component superiority is retained as comparison evidence, not a veto. Legacy
family selection keeps its separate existing rule.
"""
from __future__ import annotations

import numpy as np

from ...core.types import TF_MS

from ...strategy import spec_evidence
from ...strategy.compile import compile_spec
from ...strategy.vector_backtest import funding_for, simulate
from .. import evaluate as ev, growth, job, thresholds
from ..compose import data as cdata, graph
from . import ClosureRefused, canonical, cohort, digest, families, manifest

RESULT_SCHEMA = 'closure-development.v1'
COMBINED_SELECTION_POLICY = 'own-economics-component-comparison.v1'
OUTCOMES = ('DEVELOPMENT_POSITIVE', 'HEDGE_SUPPORTED', 'NEGATIVE_ECONOMICS', 'INCONCLUSIVE_POWER', 'BLOCKED_DATA', 'CODE_FAILURE')


def research_cfg(env: dict) -> dict:
    """The one config both stages evaluate under; derived from the envelope so
    the cost model cannot differ between development and qualification."""
    p = env['policy']
    risk = manifest.research_risk(env['risk'])      # idempotent on a frozen block; typed refusal if invalid
    return dict(risk=risk, research=dict(
        equity=2000.0, min_threshold_samples=p['min_threshold_samples'],
        min_finite_frac=p['min_finite_frac'], discovery_null_draws=p['draws'], seed=p['seed']))


def current_evaluator_id() -> str:
    return manifest.evaluator_id()


def require_current(env: dict) -> None:
    manifest.verify_envelope(env)
    if env['evaluator_id'] != current_evaluator_id():
        raise ClosureRefused('evaluator_changed_since_envelope')


def heldout_counts(env: dict) -> dict:
    """EXACT replay-eligible bar counts (metadata only) measured when the
    envelope was built; a missing bar is a missing count, never an assumption."""
    return {k: dict(v) for k, v in env['projection_counts'].items()}


def _context_timeframes(candidate):
    """Prepare only graph dependencies, including inputs needed BEFORE model fitting.

    Legacy families retain their existing context preparation. Unknown fitted
    inputs raise rather than silently discarding a required timeframe.
    """
    if candidate is None or not graph.is_graph(candidate):
        return None
    from ...strategy import dsl, fill_timing
    needed = {}

    def expression(text):
        fill_timing._span(dsl.parse(text), needed, candidate['tf'])

    def walk(node):
        if isinstance(node, dict):
            if node.get('op') == 'htf':
                needed[node['tf']] = 1
            if 'd' in node:
                expression(node['d'])
            for key in ('fit', 'fit_pass'):
                if key in node:
                    expression(f'{"fitted" if key == "fit" else "fitted_pass"}("{node[key]}")')
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    body = graph.identity(candidate)
    walk(body)
    signal = (candidate.get('exit') or {}).get('signal_exit')
    if isinstance(signal, str) and signal.strip():
        expression(signal)
    return tuple(sorted(tf for tf in needed if tf != '__external__'))


def build_bundle(frames: dict, env: dict, external: dict | None = None, *, candidate=None) -> ev.Bundle:
    cfg = research_cfg(env)
    external = external or {}
    tf = env['tf']
    contexts = _context_timeframes(candidate)
    sym_frames = {s: spec_evidence.frames_for(df, tf, context_timeframes=contexts) for s, df in frames.items()}
    risk = cfg['risk']
    return ev.Bundle(
        tf=tf, frames=frames, sym_frames=sym_frames, universe={s: {tf: d} for s, d in frames.items()},
        btc=external.get('btc') or ({tf: frames['BTC/USDT']} if 'BTC/USDT' in frames else None), market=external.get('market'),
        derivs=external.get('derivs') or {s: {} for s in frames}, risk=spec_evidence.risk_for(risk, tf), cut=int(env['dev_end_ms']),
        heldout_bars=heldout_counts(env), equity=float(cfg['research']['equity']),
        risk_pct=float(risk.get('risk_per_trade_pct', 0.5)), max_open=int(risk.get('max_open_trades', 8)),
        requested=tuple(frames), world=external.get('world'),entry_eligibility=external.get('entry_eligibility') or {})


def fit_gauges(bundle: ev.Bundle, candidate: dict, env: dict) -> dict:
    exprs = families.gauge_expressions(candidate)
    if not exprs:
        return {}
    cfg = research_cfg(env)['research']
    return thresholds.measure(exprs, job._ctxs(bundle), min_samples=int(cfg['min_threshold_samples']),
                              min_finite_frac=float(cfg['min_finite_frac']))


def run_symbols(c, bundle: ev.Bundle, *, delay_bars: int = 0, cost_mult: float = 1.0, traces: bool = False) -> dict:
    """Per-symbol simulate() mirroring evaluate()'s own inputs (entry signals,
    declared signal exit with its availability, signed funding) under the
    engine's trade definition. `delay_bars` delays only the FILL bar through
    the engine's fill-timing array (signal ATR, stop geometry and exit intent
    are unchanged); `cost_mult` scales fee and slippage and nothing else."""
    compiled = compile_spec(c.to_spec())
    spec = compiled.spec
    risk = dict(bundle.risk)
    risk['taker_fee_pct'] = float(risk.get('taker_fee_pct', 0.05)) * cost_mult
    risk['slippage_atr_frac'] = float(risk.get('slippage_atr_frac', 0.06)) * cost_mult
    out = {}
    for sym, df in bundle.frames.items():
        kw = dict(btc=bundle.btc, derivs=bundle.derivs.get(sym), universe=bundle.universe, market=bundle.market,
                  symbol=sym, world=bundle.world)
        lo, sh = compiled.entries(bundle.sym_frames[sym], **kw)
        if sym in bundle.entry_eligibility:
            allow=np.asarray(bundle.entry_eligibility[sym],dtype=bool)
            lo,sh=np.asarray(lo,dtype=bool)&allow,np.asarray(sh,dtype=bool)&allow
        ex, avail = compiled.exit_signal_detail(bundle.sym_frames[sym], **kw) if spec.exit.signal_exit.strip() \
            else (None, None)
        fills, trace = [], ([] if traces else None)
        res = simulate(np.asarray(lo, bool), np.asarray(sh, bool), df, spec.exit, risk, symbol=sym,
                       funding=funding_for(sym, df, risk), fills_out=fills, exit_sig=ex, exit_sig_available=avail,
                       fill_delay_bars=int(delay_bars), trace_out=trace)
        out[sym] = dict(result=res, fills=fills, df=df, trace=trace)
    return out


def classify(res: dict, baselines: dict, grow_max_p: float = growth.GROW_MAX_P, *, development_only=False,
             component_comparison_only=False) -> tuple:
    """(outcome, reason). Power problems are never negative economics."""
    v = res.get('verdict')
    if v == 'error':
        return 'CODE_FAILURE', 'evaluation raised: ' + str(res.get('error'))[:160]
    if v == 'untested':
        return 'BLOCKED_DATA', 'required data absent: ' + canonical(res.get('context_gaps'))[:160]
    if v == 'economic_screened':
        screen = res.get('economic_screen') or {}
        pct = (res.get('portfolio') or {}).get('total_pct')
        if (screen.get('policy') != ev.ECONOMIC_SCREEN_POLICY
                or screen.get('observed_trade_symbols', 0) < ev.MIN_PROJECTED_MARKETS
                or res.get('trades', 0) < ev.MIN_SYMBOL_TRADES * ev.MIN_PROJECTED_MARKETS
                or pct is None or not np.isfinite(pct) or pct > 0
                or res.get('consistency_p') is not None):
            return 'CODE_FAILURE', 'invalid directional economic screen'
        return 'NEGATIVE_ECONOMICS', f'closed-trade net return {pct}%; timing and component tests deferred, not evidence of no edge'
    development_scored = (v == 'scored' and res.get('scored_symbols', 0) >= ev.MIN_PROJECTED_MARKETS
                          and res.get('trades', 0) >= ev.MIN_SYMBOL_TRADES * ev.MIN_PROJECTED_MARKETS
                          and res.get('consistency_p') is not None)
    if v in ('empty', 'untestable') or (not res.get('testable') and not (development_only and development_scored)):
        return 'INCONCLUSIVE_POWER', f'verdict={v} testable={res.get("testable")} trades={res.get("trades")}'
    for key, b in baselines.items():
        if b.get('verdict') == 'error':
            return 'CODE_FAILURE', f'baseline {key} raised: ' + str(b.get('error'))[:120]
        if component_comparison_only and b.get('verdict') not in ('scored', 'empty', 'untestable', 'untested'):
            return 'CODE_FAILURE', f'baseline {key} returned an invalid comparison verdict'
        if not component_comparison_only and b.get('verdict') not in ('scored', 'empty'):
            return 'INCONCLUSIVE_POWER', f'baseline {key} could not be judged ({b.get("verdict")})'
    p, pct = res.get('consistency_p'), (res.get('portfolio') or {}).get('total_pct', 0.0)
    if p is None or not np.isfinite(p) or not np.isfinite(pct):
        return 'INCONCLUSIVE_POWER', 'nonfinite development economics'
    if p > grow_max_p or pct <= 0:
        return 'NEGATIVE_ECONOMICS', f'p={p} total_pct={pct} (needs p<={grow_max_p} and positive return)'
    if component_comparison_only:
        return 'DEVELOPMENT_POSITIVE', f'p={p} total_pct={pct}; component comparisons retained, not an advancement veto'
    weak = [k for k, b in baselines.items() if not growth.earns_place(res, b)[0]]
    if weak:
        return 'NEGATIVE_ECONOMICS', f'does not beat its own baseline(s): {sorted(weak)}'
    return 'DEVELOPMENT_POSITIVE', f'p={p} total_pct={pct}; beats every one-part baseline'


def cache_key(candidate: dict, env: dict, data_id: str, external_id: str = '', *, draws=None) -> str:
    body = dict(candidate=candidate['candidate_id'], envelope=env['envelope_id'],
                data=data_id, evaluator=env['evaluator_id'], cost=env['cost_id'], split=env['split_id'],
                cache_schema='development-cache.v2', draws=int(draws or env['policy']['draws']))
    if external_id:
        body['external'] = external_id          # absent for legacy candidates: their keys are unchanged
    from ..compose import hedge as H,capital as K
    if H.capital_enabled(candidate.get('hedge') or {}):body['capital_policy_id']=K.POLICY_ID
    return digest(body)


def develop(candidate: dict, snapshot_path, env: dict, *, store=None, draws: int | None = None,
            derivs_path=None, world_path=None, references_path=None, cache_path=None,universe_path=None,universe_environment='demo') -> dict:
    """Run (or recall) the development stage for one candidate.

    `references_path` names a detached reference store (e.g. a published CSV
    import). It is valid only for development_mode RETROSPECTIVE; the
    protected/qualification stage never reads it."""
    require_current(env)
    if references_path is not None and env['development_mode'] != 'RETROSPECTIVE':
        raise ClosureRefused('references_path_requires_retrospective_development')
    if env['development_status'] != 'ELIGIBLE':
        res = dict(schema=RESULT_SCHEMA, stage='DEVELOPMENT', outcome='BLOCKED_DATA', candidate_id=candidate['candidate_id'],
                   envelope_id=env['envelope_id'], evaluator_id=env['evaluator_id'], data_id=None,
                   data_mode=env['development_mode'], policy_class=env['policy_class'], authority='RESEARCH_ONLY',
                   reason='development_blocked: ' + ';'.join(env['development_reasons'])[:300])
        res['cache_key'] = cache_key(candidate, env, 'blocked', draws=draws)
        res['result_id'] = digest({k: v for k, v in res.items() if k != 'result_id'})
        if store is not None:
            store.put_run(res['cache_key'], res)          # recorded: revision and status read it like any result
        return dict(res, cached=False)
    families.verify_candidate(candidate)
    frames, data_id = manifest.development_frames(snapshot_path, env)
    external, ext_id = None, ''
    if graph.is_graph(candidate) or env['risk'].get('real_funding', False):
        external = _external(candidate, snapshot_path, derivs_path, frames, env, world_path, references_path)
        ext_id = external['data_id']
    if universe_path:
        from ..compose.universe_replay import entry_masks
        masks,parity=entry_masks(universe_path,universe_environment,frames,env['tf'])
        external=external or dict(gaps=[],provenance={})
        external.update(entry_eligibility=masks,trading_parity=parity)
        ext_id=digest([ext_id,parity])
    key = cache_key(candidate, env, data_id, ext_id, draws=draws)
    # Full consumed data/eligibility is rechecked above. Negative dependency and
    # transient failure reuse stays disabled until an absence witness exists.
    eligible_cache = external is None or not external.get('gaps')
    if eligible_cache and (store is not None or cache_path is not None):
        if store is not None:
            hit = store.get_run(key)
        else:
            from .store import read_cached_run
            hit = read_cached_run(cache_path, key)
        if hit is not None and hit.get('outcome') not in ('BLOCKED_DATA', 'CODE_FAILURE'):
            from ...strategy import fitted
            for part in (hit.get('combo') or {}).get('parts', []):
                for artifact in part.get('artifacts') or []:
                    fitted.register_artifact(artifact)
            return dict(hit, cached=True)
    result = _develop(candidate, frames, data_id, env, draws, external)
    if result.get('outcome') == 'CODE_FAILURE':
        # Retain failure evidence without occupying the reusable computation
        # key: a transient failure must not conflict with a later valid result.
        key = digest(['nonreusable-failure.v1', key, result])
    result['cache_key'] = key
    result['result_id'] = digest({k: v for k, v in result.items() if k not in ('result_id',)})
    if store is not None:
        store.put_run(key, result)
    return dict(result, cached=False)


def bind_funding(frames: dict, result: dict, env: dict, basis: str) -> None:
    """Bind each frame to the retained signed funding served for ITS symbol (aligned to the frame's own bars), or record a gap.
    Shared by development and the protected hedge evaluator, so both score the exact same funding convention."""
    from ...strategy.features_deriv import align
    from ...data.market_provenance import ms, digest as source_digest
    import pandas as pd
    for sym, frame in frames.items():
        source = result['derivs'].get(sym, {}).get('funding')
        if source is None:
            result['gaps'].append(dict(requirement='funding', reason='no retained funding for the symbol', symbols=[sym]))
            continue
        cuts = ms(frame['ts']) + TF_MS[env['tf']]
        rates = align(source, pd.to_datetime(cuts, unit='ms', utc=True)).to_numpy(float)
        if not np.isfinite(rates).all():
            result['gaps'].append(dict(requirement='funding', reason='measured funding does not cover every scored bar', symbols=[sym]))
            continue
        frame.attrs['research_funding_binding'] = dict(symbol=sym, timestamp_id=source_digest(ms(frame['ts']).tolist()),
            source_id=result['data_id'], rates=tuple(float(v) for v in rates), basis=basis)


def _external(candidate, snapshot_path, derivs_path, frames, env, world_path=None, references_path=None) -> dict:
    """Context series the graph reads, as known at the development end. A
    RETROSPECTIVE envelope reads references through the retrospective store
    contract (same cut as its candle frames), never as-of replay."""
    retro = dict(dataset_id='closure:' + env['manifest_id'][:16], cut_ms=int(env['dev_end_ms']), start_ms=int(env['fit_window'][0])) \
        if env['development_mode'] == 'RETROSPECTIVE' else None
    required = set(graph.requires_of(candidate) if graph.is_graph(candidate) else ('ohlcv',))
    if env['risk'].get('real_funding', False):
        required.add('funding')
    result = cdata.load_external(required, snapshot_path, derivs_path, list(frames),
                               int(env['dev_end_ms']), world_path=world_path, references_path=references_path,
                               retrospective=retro, window_start_ms=int(env['fit_window'][0]),
                               warmup_ms=graph.MAX_WINDOW * TF_MS[env['tf']])
    from ..compose.experiments import needs_btc
    if graph.is_graph(candidate) and needs_btc(candidate) and 'BTC/USDT' not in frames:
        reference, reference_id = manifest.development_reference(snapshot_path, env)
        df = reference.get('BTC/USDT')
        if df is None or not len(df):
            result['gaps'].append(dict(requirement='btc', reason='BTC reference unavailable in development era'))
        else:
            result['btc'] = {env['tf']: df}
            result['provenance']['btc'] = dict(data_id=reference_id, scope='DEVELOPMENT_REFERENCE_ONLY',
                                              first_ms=int(manifest._ms(df['ts']).min()),
                                              last_ms=int(manifest._ms(df['ts']).max()))
            result['data_id'] = digest([result['data_id'], reference_id])
    # Cohort rule (data availability only, before any trade is simulated): drop symbols that lack a required input.
    cohort.apply_exclusion(frames, result, required, env, spec_evidence._SERIES_FOR, stage='external')
    if env['risk'].get('real_funding', False) and not result['gaps']:
        bind_funding(frames, result, env, 'retrospective_observation' if retro is not None else 'retained_point_in_time')
        cohort.apply_exclusion(frames, result, required, env, spec_evidence._SERIES_FOR, stage='funding')
    return result


def _evaluation_failure(done, exc, tf, **detail):
    """Preserve typed missing-input evidence without hiding unexpected failures."""
    from ...strategy.compile import DependencyUnavailable
    from ...strategy.exit_policy import ExitObservationUnavailable
    if isinstance(exc, DependencyUnavailable):
        gaps = [dict(requirement=item.split(': ',1)[0], reason=item)
                for item in exc.requirements]
    elif isinstance(exc, ExitObservationUnavailable):
        gaps = [dict(requirement='candles:'+tf, reason='required completed consecutive bar unavailable',
                     expected_ms=exc.expected_ms, observed_ms=exc.observed_ms,
                     completed=exc.completed)]
    else:
        return done('CODE_FAILURE', f'{type(exc).__name__}: {exc}'[:200], **detail)
    return done('BLOCKED_DATA', f'{type(exc).__name__}: {exc}'[:200], context_gaps=gaps, **detail)


def _develop(candidate, frames, data_id, env, draws, external=None):
    base = dict(schema=RESULT_SCHEMA, stage='DEVELOPMENT', candidate_id=candidate['candidate_id'],
                envelope_id=env['envelope_id'], data_id=data_id, evaluator_id=env['evaluator_id'],
                cost_id=env['cost_id'], data_mode=env['development_mode'], policy_class=env['policy_class'],
                authority='RESEARCH_ONLY')
    if (external or {}).get('cohort'):
        base['cohort'] = external['cohort']        # which symbols were excluded for missing inputs, and why
    base['trading_parity']=(external or {}).get('trading_parity') or dict(
        status='UNAVAILABLE',reason='no detached historical scanner-selection evidence supplied; unfiltered retrospective experiment')
    if external is not None and external.get('provenance'):
        # capture identity of every retained input read, kept even when no trade results
        base.update(context_provenance=external['provenance'])
        if any(p.get('read_mode') == 'retrospective' for p in external['provenance'].values()):
            base['evidence_scope'] = 'retrospective_observation'

    def done(outcome, reason, **more):
        return dict(base, outcome=outcome, reason=reason, **more)

    if len(frames) < env['policy']['min_discovery_symbols']:
        return done('BLOCKED_DATA', f'{len(frames)} development markets (<{env["policy"]["min_discovery_symbols"]})')
    if external is not None and external['gaps']:
        return done('BLOCKED_DATA', 'required context absent: ' + canonical(external['gaps'])[:240],
                    context_gaps=external['gaps'])
    try:
        bundle = build_bundle(frames, env, external, candidate=candidate)
    except Exception as exc:  # noqa: BLE001 - retain a typed development failure
        return _evaluation_failure(done, exc, env['tf'])
    n_draws = int(draws or env['policy']['draws'])
    seed, floor = int(env['policy']['seed']), int(env['policy']['min_symbol_trades'])
    graph_fit = None
    if graph.is_graph(candidate) and candidate['structure'] in graph.HEDGE_STRUCTURES:
        return _develop_hedge(candidate, bundle, env, external, done)
    try:
        if graph.is_graph(candidate):
            resolved, graph_fit = graph.resolve(candidate, job._ctxs(bundle), env['fit_window'])
            c = graph.realize(resolved, evaluation_scope=env['envelope_id'], trigger=candidate['candidate_id'])
            gauges = {}
        else:
            gauges = fit_gauges(bundle, candidate, env)
            c = families.realize(candidate, gauges, evaluation_scope=env['envelope_id'])
    except ClosureRefused as exc:
        if exc.code == 'DATA_INSUFFICIENT':
            return done('BLOCKED_DATA', 'threshold fit unavailable: ' + str(exc))
        return done('CODE_FAILURE', 'candidate could not be realized: ' + str(exc))
    except Exception as exc:                                  # noqa: BLE001 - reported as code failure
        return _evaluation_failure(done, exc, env['tf'])
    fit = {e: {k: g.get(k) for k in ('p10', 'p25', 'p75', 'p90', 'n', 'usable')} for e, g in gauges.items()}
    try:
        sims = {}
        res = ev.evaluate(c, bundle, draws=n_draws, seed=seed, min_symbol_trades=floor,
                          raw_runs=sims, traces=True, economic_screen=True)
        screened = res.get('verdict') == 'economic_screened'
        variants = ([] if screened else [s for _, s in _graph_baselines(resolved, env)]
                    if graph_fit is not None else families.baselines(c))
        sub, baseline_runs = {}, {}
        for s in variants:
            label=s.keys[0] if s.k==1 else ','.join(s.keys)
            baseline_runs[label]={}
            sub[label]=ev.evaluate(s,bundle,draws=n_draws,seed=seed,min_symbol_trades=floor,
                                   raw_runs=baseline_runs[label])
        if not sims:  # compile/dependency preflight returned before simulation
            sims = run_symbols(c, bundle, traces=graph_fit is not None)
        censored = sum(r['result'].censored_position_count for r in sims.values())
    except Exception as exc:                                  # noqa: BLE001
        return _evaluation_failure(done, exc, env['tf'], combo=c.as_dict(), combo_hash=c.hash,
                                   threshold_fit=fit)
    outcome, reason = classify(res, sub, float(env['policy']['grow_max_p']), development_only=True,
                              component_comparison_only=graph.is_graph(candidate))
    blocked_gaps = _structured_gaps(res.get('context_gaps')) if outcome == 'BLOCKED_DATA' else None
    pf = res.get('portfolio') or {}
    metrics = dict(consistency_p=res.get('consistency_p'), median_pf=res.get('median_pf'),
                   total_pct=pf.get('total_pct'), max_dd_pct=pf.get('max_dd_pct'), trades=res.get('trades'),
                   scored_symbols=res.get('scored_symbols'), testable=res.get('testable'), verdict=res.get('verdict'),
                   censored_positions=int(censored))
    baseline_metrics = {k: dict(consistency_p=b.get('consistency_p'), total_pct=(b.get('portfolio') or {}).get('total_pct'),
                                verdict=b.get('verdict'), trades=b.get('trades'),
                                candidate_earns_place=growth.earns_place(res, b)[0]) for k, b in sub.items()}
    extra = dict(validation_readiness=dict(
        status='READY' if not screened and env['protected_status'] == 'ELIGIBLE' and res.get('testable') else 'UNAVAILABLE',
        protected_status=env['protected_status'], projection=res.get('projection'),
        projected_testable=bool(res.get('testable')), reasons=list(env.get('protected_reasons') or []),
        authority='NO_ADMISSION: development economics do not establish qualification'))
    if graph.is_graph(candidate):
        extra['selection_policy'] = COMBINED_SELECTION_POLICY
    if screened:
        extra.update(economic_screen=res['economic_screen'],
                     baseline_assessment='NOT_COMPUTED_ECONOMIC_SCREEN')
        extra['diagnostics'] = _diagnostics(c, bundle, env, floor, sims=sims, controls={}, robustness=False)
        costs = {}
        for symbol, run in sims.items():
            closed = [t for t in run['trace'] if not t.get('open_at_end')]
            fees = sum(t['fees_per_unit'] * t['amount'] for t in closed)
            funding = sum(t['carry_per_unit'] * t['amount'] for t in closed)
            net = float(run['result'].pnl_usdt)
            costs[symbol] = dict(closed_trade_net_pnl=net, fees=fees, signed_funding_cost=funding,
                                 pnl_before_fees_and_funding=net+fees+funding,
                                 funding_basis=sorted({t['funding_basis'] for t in closed}),
                                 censored_positions=int(run['result'].censored_position_count))
        extra['diagnostics']['economic_screen'] = dict(per_symbol=costs,
            scope='INDEPENDENT_PER_SYMBOL_SIMULATIONS_NOT_SHARED_PORTFOLIO_TOTAL',
            cost_evidence='SIMULATED_DECLARED_COSTS_NOT_VENUE_FILLS',
            slippage='retained in simulated fill prices')
    if graph_fit is not None and outcome == 'DEVELOPMENT_POSITIVE':
        extra['diagnostics'] = _diagnostics(c, bundle, env, floor, sims=sims,controls=baseline_runs)
    if graph_fit is not None:
        from ..compose import diagnostics as dg
        ctxs = {x.symbol: x for x in job._ctxs(bundle)}
        diagnostics = extra.setdefault('diagnostics', {})
        diagnostics['roles'] = dg.role_report(candidate, ctxs, sims)
        from ..compose.experiments import comparisons
        diagnostics['indicator_comparison'] = comparisons(candidate, list(ctxs.values()))
        diagnostics.setdefault('regime', dg.regime_report({s: r['fills'] for s, r in sims.items()}, ctxs))
    if graph_fit is not None:
        extra.update(graph_fit=graph_fit, resolved_graph_id=resolved['candidate_id'],
                     requires=list(c.requires), context_first_obs=(external or {}).get('first_obs'))
    if blocked_gaps:
        extra['context_gaps'] = blocked_gaps         # required-dependency gaps found by the evaluator stay structured
    return done(outcome, reason, combo=c.as_dict(), combo_hash=c.hash, threshold_fit=fit, metrics=metrics,
                baselines=baseline_metrics, fit_window=env['fit_window'], draws=n_draws, seed=seed, **extra)


def _structured_gaps(gaps) -> list:
    """The evaluator reports gaps as a list of {requirement, reason} or {symbol: ['ref:x: reason', ...]}; keep
    them as {requirement, reason[, symbols]} so a required reference problem is never reduced to prose."""
    if isinstance(gaps, list):
        return [g for g in gaps if isinstance(g, dict) and g.get('requirement')]
    out = {}
    for sym, entries in (gaps or {}).items():
        for text in entries if isinstance(entries, (list, tuple)) else ():
            req, _, why = str(text).partition(': ')
            g = out.setdefault(req, dict(requirement=req, reason=why[:160], symbols=[]))
            g['symbols'].append(sym)
    return list(out.values())


def _diagnostics(c, bundle, env, floor, gated=False, sims=None,controls=None,*,robustness=True) -> dict:
    """Regimes, marked exposure and already measured declared comparisons.

    No unrelated template portfolio is manufactured as an incumbent book and
    no baseline simulation is repeated just to produce a diagnostic.
    """
    from ..compose import diagnostics as dg
    try:
        sims = sims if sims is not None else run_symbols(c, bundle, traces=True)
        by_sym = {s: r['fills'] for s, r in sims.items()}
        ctxs = {x.symbol: x for x in job._ctxs(bundle)}
        fills = dg.common_fills(by_sym, bundle.frames)            # shared UTC clock; regime_report keeps local indices
        traces = [t for r in sims.values() for t in r['trace']]
        out = dict(regime=dg.regime_report(by_sym, ctxs, gated=gated),
                   mtm=dg.mtm_report(traces, bundle.frames, fills, bundle.equity, bundle.risk_pct, bundle.max_open))
        out['contribution'] = dict(
            scope='DECLARED_COMPONENT_CONTROLS',
            controls={name:dg.contribution(dg.common_fills({s:r['fills'] for s,r in runs.items()},bundle.frames),
                                          fills,bundle.equity,bundle.risk_pct,bundle.max_open)
                      for name,runs in (controls or {}).items()},
            actual_incumbent_contribution='NOT_ESTABLISHED: no declared historical incumbent book bound',
            cash_comparator=dict(net_return_pct=0.0,source='DECLARED_NO_EXPOSURE_CONTROL'))
        if robustness:
            from .robustness import report
            out['robustness']=report(c,None,bundle,delays=(0,1),cost_mults=(1.0,2.0),shifts=(1,2),base_runs=sims)
        else:
            out['robustness']=dict(status='NOT_COMPUTED_ECONOMIC_SCREEN')
        return out
    except Exception as exc:                                    # noqa: BLE001
        return dict(error=f'{type(exc).__name__}: {exc}'[:200])


def _graph_baselines(resolved, env) -> list:
    """A9 comparison for a graph: each top-level component removed. Realised
    under the same evaluation scope so identities cannot collide with the full rule."""
    from ..compose.experiments import simple_control
    variants=list(graph.component_removals(resolved))
    control=simple_control(resolved)
    if control is not None:
        variants.append(('simple_direction_control',control))
    return [(name, graph.realize(v, evaluation_scope=env['envelope_id'])) for name, v in variants]


def _develop_hedge(candidate, bundle, env, external, done):
    """Combined-book evaluation of a hedge candidate on the development window:
    ratio estimated on the first half only, comparison on the second half,
    against unhedged / reduced-size / cash under the declared objective.
    This is NOT the alpha referee policy and carries no qualification path."""
    from ..compose import hedge as H
    spec = candidate['hedge']
    if env.get('policy_class')=='CONFIGURED':
        from ..compose.managed_hedge import require_current
        try:require_current(spec)
        except ClosureRefused as exc:
            return done('CODE_FAILURE','hedge strategy needs the current primary-first combined-position policy: '+exc.code)
    hsym = spec['hedge']['symbol']
    if hsym not in bundle.frames:
        return done('BLOCKED_DATA', f'hedge instrument {hsym} has no development frames', context_gaps=[
            dict(requirement=f'hedge_leg:{hsym}', reason='instrument frames absent')])
    if spec['primary']['contract'] != 'linear' or spec['hedge']['contract'] != 'linear':
        return done('BLOCKED_DATA', 'COIN-M legs: no inverse-contract candle/funding history is wired',
                    context_gaps=[dict(requirement='contract:inverse', reason='no COIN-M data source')])
    try:
        resolved, fit = graph.resolve(candidate, job._ctxs(bundle), env['fit_window'])
        c = graph.realize(resolved, evaluation_scope=env['envelope_id'], trigger=candidate['candidate_id'],
                          entries_only=True)
        compiled = compile_spec(c.to_spec())
        hdf = bundle.frames[hsym]
        rows, per = [], {}
        psym = spec['primary']['symbol']
        if psym not in bundle.frames:
            return done('BLOCKED_DATA', f'primary instrument {psym} has no development frames',
                        context_gaps=[dict(requirement=f'primary_leg:{psym}', reason='instrument frames absent')])
        for sym, df in ((psym, bundle.frames[psym]),):
            kw = dict(btc=bundle.btc, derivs=bundle.derivs.get(sym), universe=bundle.universe, market=bundle.market,
                      symbol=sym, world=bundle.world)
            lo, sh = compiled.entries(bundle.sym_frames[sym], **kw)
            if sym in bundle.entry_eligibility:
                allow=np.asarray(bundle.entry_eligibility[sym],dtype=bool)
                lo,sh=np.asarray(lo,dtype=bool)&allow,np.asarray(sh,dtype=bool)&allow
            from ...strategy import fill_timing
            need = fill_timing.history_requirements(compiled.spec)
            rr = spec['ratio']
            al = H.align_pair(df, hdf, lo, sh, TF_MS[env['tf']], p_window=need['base'],
                              h_window=(int(rr['lookback']) + 1) if rr['method'] == 'rolling_beta' else 1)
            pcl, hcl, popen, hopen, sig = al['p'], al['h'], al['po'], al['ho'], al['sig']
            exits = H.aligned_exits(compiled, bundle.sym_frames[sym], al, **kw)
            primary_exit,decision_bars=H.primary_exit_inputs(compiled,df,al,hedge_frame=hdf)
            half = len(pcl) // 2
            pr, hr = np.r_[np.nan, np.diff(pcl) / pcl[:-1]], np.r_[np.nan, np.diff(hcl) / hcl[:-1]]
            beta = H.estimate_ratio(dict(spec, ratio=dict(spec['ratio'], method='ols_beta', fit_end=half)), pr, hr, half - 1) \
                if spec['ratio']['method'] != 'fixed' else float(spec['ratio']['value'])
            if spec['ratio']['method'] != 'rolling_beta' and not np.isfinite(beta):
                return done('INCONCLUSIVE_POWER', 'hedge ratio cannot be estimated from the development returns '
                                                  '(too few finite paired observations); no zero ratio is substituted')
            sp = dict(spec, ratio=dict(spec['ratio'], method='fixed', value=beta)) if spec['ratio']['method'] == 'ols_beta' else spec
            # A generated declaration cannot erase the configured taker fee.
            # Slippage remains a declared fixed-bps research assumption, reported below.
            fee_floor = max(0.0, float(bundle.risk.get('taker_fee_pct', 0.0))) * 100.0
            sp = dict(sp, fee_bps=max(float(sp['fee_bps']), fee_floor))
            if int(np.abs(sig[half:]).sum()) < 1:
                continue
            cost = TF_MS[env['tf']] / 28_800_000
            if bundle.risk.get('real_funding', False):
                n_grid = len(pcl)
                pfund = H.place(al['idx_p'], funding_for(sym, df, bundle.risk), n_grid)
                hfund = H.place(al['idx_h'], funding_for(hsym, hdf, bundle.risk), n_grid)
                funding = (pfund[half:] * cost, hfund[half:] * cost)
                flat = 0.0
            else:
                funding = None
                flat = abs(float(bundle.risk.get('funding_rate_8h', 0.0))) * cost
            res = H.evaluate(sp, pcl[half:], hcl[half:], sig[half:], exits[half:],
                             opens=(popen[half:], hopen[half:]),funding=funding,flat_funding_rate=flat,
                             primary_exit=primary_exit,decision_bars=decision_bars[half:])
            per[sym] = dict(verdict=res['verdict'], reason=res['reason'], ratio=beta,
                            comparators=res['comparators'], stress=res['stress'], return_sacrificed_pct=res['return_sacrificed_pct'],
                            effective_spec=sp, funding_basis='RETAINED_SIGNED_HISTORY' if funding is not None else 'CONSERVATIVE_FLAT_EIGHT_HOUR',
                            cost_model=dict(fee_bps=sp['fee_bps'], configured_fee_floor_bps=fee_floor, slip_bps=sp['slip_bps'],
                                            slippage_basis='DECLARED_FIXED_BPS_NOT_OBSERVED_ATR_SLIPPAGE', flat_funding_rate_per_bar=flat),
                            folds=res['folds'], alignment={k: al[k] for k in ('refused', 'bars', 'missing_primary', 'missing_hedge')})
            rows.append(res['verdict'])
    except ClosureRefused as exc:
        return done('CODE_FAILURE', 'hedge candidate could not be realized: ' + str(exc))
    except Exception as exc:                                    # noqa: BLE001
        return _evaluation_failure(done, exc, env['tf'])
    n_sup = sum(v == 'HEDGE_SUPPORTED' for v in rows)
    scored = [v for v in rows if v != 'INSUFFICIENT_EVIDENCE']
    if not scored:
        outcome, why = 'INCONCLUSIVE_POWER', 'declared instrument pair has too few complete hedged entries'
    elif n_sup > len(scored) / 2:
        outcome, why = 'HEDGE_SUPPORTED', f'{n_sup}/{len(scored)} symbols improve the declared objective vs unhedged and reduced'
    else:
        outcome, why = 'NEGATIVE_ECONOMICS', f'{n_sup}/{len(scored)} symbols improve the declared objective'
    return done(outcome, why, hedge=dict(spec_id=spec['spec_id'], purpose=spec['purpose'], objective=spec['objective'],
                                         per_symbol=per, qualification='closure.hedge_qualification: freeze_hedge_finalist -> qualify_hedge on sealed protected '
                                         'data under the predeclared risk or relative-value return objective; qualification additionally requires '
                                         'the configured registered objective method, approved search calibration, independent evidence and authority. '
                                         'Development support alone grants no qualification or live permission',
                                         runtime_support=H.RUNTIME_SUPPORT, deployment=H.deployment_contract(spec)),
                graph_fit=fit, requires=list(c.requires), combo=c.as_dict(), combo_hash=c.hash, metrics=dict(
                    supported_symbols=n_sup, scored_symbols=len(scored), trades=None, total_pct=None, consistency_p=None))
