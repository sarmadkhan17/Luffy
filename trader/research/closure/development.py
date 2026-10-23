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
"""
from __future__ import annotations

import numpy as np

from ...strategy import spec_evidence
from ...strategy.compile import compile_spec
from ...strategy.vector_backtest import funding_for, simulate
from .. import evaluate as ev, growth, job, slices, thresholds
from . import ClosureRefused, canonical, digest, families, manifest

RESULT_SCHEMA = 'closure-development.v1'
OUTCOMES = ('DEVELOPMENT_POSITIVE', 'NEGATIVE_ECONOMICS', 'INCONCLUSIVE_POWER', 'BLOCKED_DATA', 'CODE_FAILURE')


def research_cfg(env: dict) -> dict:
    """The one config both stages evaluate under; derived from the envelope so
    the cost model cannot differ between development and qualification."""
    p = env['policy']
    risk = dict(env['risk'])
    risk.setdefault('real_funding', False)
    risk.setdefault('max_open_trades', 8)
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


def build_bundle(frames: dict, env: dict) -> ev.Bundle:
    cfg = research_cfg(env)
    tf = env['tf']
    sym_frames = {s: spec_evidence.frames_for(df, tf) for s, df in frames.items()}
    risk = cfg['risk']
    return ev.Bundle(
        tf=tf, frames=frames, sym_frames=sym_frames, universe={s: {tf: d} for s, d in frames.items()},
        btc={tf: frames['BTC/USDT']} if 'BTC/USDT' in frames else None, market=None,
        derivs={s: {} for s in frames}, risk=spec_evidence.risk_for(risk, tf), cut=int(env['dev_end_ms']),
        heldout_bars=heldout_counts(env), equity=float(cfg['research']['equity']),
        risk_pct=float(risk.get('risk_per_trade_pct', 0.5)), max_open=int(risk.get('max_open_trades', 8)),
        requested=tuple(frames))


def fit_gauges(bundle: ev.Bundle, candidate: dict, env: dict) -> dict:
    exprs = families.gauge_expressions(candidate)
    if not exprs:
        return {}
    cfg = research_cfg(env)['research']
    return thresholds.measure(exprs, job._ctxs(bundle), min_samples=int(cfg['min_threshold_samples']),
                              min_finite_frac=float(cfg['min_finite_frac']))


def run_symbols(c, bundle: ev.Bundle, *, delay_bars: int = 0, cost_mult: float = 1.0) -> dict:
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
        ex, avail = compiled.exit_signal_detail(bundle.sym_frames[sym], **kw) if spec.exit.signal_exit.strip() \
            else (None, None)
        fills = []
        res = simulate(np.asarray(lo, bool), np.asarray(sh, bool), df, spec.exit, risk, symbol=sym,
                       funding=funding_for(sym, df, risk), fills_out=fills, exit_sig=ex, exit_sig_available=avail,
                       fill_delay_bars=int(delay_bars))
        out[sym] = dict(result=res, fills=fills, df=df)
    return out


def classify(res: dict, baselines: dict, grow_max_p: float = growth.GROW_MAX_P) -> tuple:
    """(outcome, reason). Power problems are never negative economics."""
    v = res.get('verdict')
    if v == 'error':
        return 'CODE_FAILURE', 'evaluation raised: ' + str(res.get('error'))[:160]
    if v == 'untested':
        return 'BLOCKED_DATA', 'required data absent: ' + canonical(res.get('context_gaps'))[:160]
    if v in ('empty', 'untestable') or not res.get('testable'):
        return 'INCONCLUSIVE_POWER', f'verdict={v} testable={res.get("testable")} trades={res.get("trades")}'
    for key, b in baselines.items():
        if b.get('verdict') == 'error':
            return 'CODE_FAILURE', f'baseline {key} raised: ' + str(b.get('error'))[:120]
        if b.get('verdict') not in ('scored', 'empty'):
            return 'INCONCLUSIVE_POWER', f'baseline {key} could not be judged ({b.get("verdict")})'
    p, pct = res.get('consistency_p'), (res.get('portfolio') or {}).get('total_pct', 0.0)
    if p is None or p > grow_max_p or pct <= 0:
        return 'NEGATIVE_ECONOMICS', f'p={p} total_pct={pct} (needs p<={grow_max_p} and positive return)'
    weak = [k for k, b in baselines.items() if not growth.earns_place(res, b)[0]]
    if weak:
        return 'NEGATIVE_ECONOMICS', f'does not beat its own baseline(s): {sorted(weak)}'
    return 'DEVELOPMENT_POSITIVE', f'p={p} total_pct={pct}; beats every one-part baseline'


def cache_key(candidate: dict, env: dict, data_id: str) -> str:
    return digest(dict(candidate=candidate['candidate_id'], envelope=env['envelope_id'],
                       data=data_id, evaluator=env['evaluator_id'], cost=env['cost_id'], split=env['split_id']))


def develop(candidate: dict, snapshot_path, env: dict, *, store=None, draws: int | None = None) -> dict:
    """Run (or recall) the development stage for one candidate."""
    require_current(env)
    if env['development_status'] != 'ELIGIBLE':
        res = dict(schema=RESULT_SCHEMA, stage='DEVELOPMENT', outcome='BLOCKED_DATA', candidate_id=candidate['candidate_id'],
                   envelope_id=env['envelope_id'], evaluator_id=env['evaluator_id'], data_id=None,
                   data_mode=env['development_mode'], policy_class=env['policy_class'], authority='RESEARCH_ONLY',
                   reason='development_blocked: ' + ';'.join(env['development_reasons'])[:300])
        res['cache_key'] = cache_key(candidate, env, 'blocked')
        res['result_id'] = digest({k: v for k, v in res.items() if k != 'result_id'})
        if store is not None:
            store.put_run(res['cache_key'], res)          # recorded: revision and status read it like any result
        return dict(res, cached=False)
    families.verify_candidate(candidate)
    frames, data_id = manifest.development_frames(snapshot_path, env)
    key = cache_key(candidate, env, data_id)
    if store is not None:
        hit = store.get_run(key)
        if hit is not None:
            return dict(hit, cached=True)
    result = _develop(candidate, frames, data_id, env, draws)
    result['cache_key'] = key
    result['result_id'] = digest({k: v for k, v in result.items() if k not in ('result_id',)})
    if store is not None:
        store.put_run(key, result)
    return dict(result, cached=False)


def _develop(candidate, frames, data_id, env, draws):
    base = dict(schema=RESULT_SCHEMA, stage='DEVELOPMENT', candidate_id=candidate['candidate_id'],
                envelope_id=env['envelope_id'], data_id=data_id, evaluator_id=env['evaluator_id'],
                cost_id=env['cost_id'], data_mode=env['development_mode'], policy_class=env['policy_class'],
                authority='RESEARCH_ONLY')

    def done(outcome, reason, **more):
        return dict(base, outcome=outcome, reason=reason, **more)

    if len(frames) < env['policy']['min_discovery_symbols']:
        return done('BLOCKED_DATA', f'{len(frames)} development markets (<{env["policy"]["min_discovery_symbols"]})')
    bundle = build_bundle(frames, env)
    n_draws = int(draws or env['policy']['draws'])
    seed, floor = int(env['policy']['seed']), int(env['policy']['min_symbol_trades'])
    try:
        gauges = fit_gauges(bundle, candidate, env)
        c = families.realize(candidate, gauges, evaluation_scope=env['envelope_id'])
    except ClosureRefused as exc:
        if exc.code == 'DATA_INSUFFICIENT':
            return done('BLOCKED_DATA', 'threshold fit unavailable: ' + str(exc))
        return done('CODE_FAILURE', 'candidate could not be realized: ' + str(exc))
    except Exception as exc:                                  # noqa: BLE001 - reported as code failure
        return done('CODE_FAILURE', f'{type(exc).__name__}: {exc}'[:200])
    fit = {e: {k: g.get(k) for k in ('p10', 'p25', 'p75', 'p90', 'n', 'usable')} for e, g in gauges.items()}
    try:
        res = ev.evaluate(c, bundle, draws=n_draws, seed=seed, min_symbol_trades=floor)
        sub = {s.keys[0] if s.k == 1 else ','.join(s.keys): ev.evaluate(s, bundle, draws=n_draws, seed=seed,
                                                                         min_symbol_trades=floor)
               for s in families.baselines(c)}
        censored = sum(r['result'].censored_position_count for r in run_symbols(c, bundle).values())
    except Exception as exc:                                  # noqa: BLE001
        return done('CODE_FAILURE', f'{type(exc).__name__}: {exc}'[:200], combo=c.as_dict(), combo_hash=c.hash,
                    threshold_fit=fit)
    outcome, reason = classify(res, sub, float(env['policy']['grow_max_p']))
    pf = res.get('portfolio') or {}
    metrics = dict(consistency_p=res.get('consistency_p'), median_pf=res.get('median_pf'),
                   total_pct=pf.get('total_pct'), max_dd_pct=pf.get('max_dd_pct'), trades=res.get('trades'),
                   scored_symbols=res.get('scored_symbols'), testable=res.get('testable'), verdict=res.get('verdict'),
                   censored_positions=int(censored))
    baseline_metrics = {k: dict(consistency_p=b.get('consistency_p'), total_pct=(b.get('portfolio') or {}).get('total_pct'),
                                verdict=b.get('verdict'), trades=b.get('trades'),
                                candidate_earns_place=growth.earns_place(res, b)[0]) for k, b in sub.items()}
    return done(outcome, reason, combo=c.as_dict(), combo_hash=c.hash, threshold_fit=fit, metrics=metrics,
                baselines=baseline_metrics, fit_window=env['fit_window'], draws=n_draws, seed=seed)
