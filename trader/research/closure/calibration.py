"""P4b — whole-process calibration harness.

Every replicate runs the REAL adaptive flow end to end on generated markets:
registry seal -> manifest -> envelope -> candidate development (existing
evaluator + baselines) -> bounded revisions -> freeze -> independent referee
look priced by the LORD++ ledger -> status. Nothing is mocked: a replicate's
outcome is whatever the pipeline returned.

Cases are declared before running (`PROTOCOL`): independent no-edge, CORRELATED
no-edge (common factor), planted weak/strong continuation, and an unsealed
(exposure UNKNOWN) case that must never qualify. Seeds are fixed ranges:
`development` seeds are for debugging the harness and can never produce a
FINAL verdict; `final` seeds are for the retained report. Targets are
declared up front, intervals are exact Clopper-Pearson, and an unresolved
interval is INCONCLUSIVE — never a pass, never evidence of no edge, and never
a reason to move a threshold.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

from . import canonical, digest, exposure, manifest, study as S, synthetic
from .store import Store

#: Predeclared presets. SMOKE is for exercising the harness (never a verdict);
#: FINAL's 60 replicates allow one false admission per null case at 95%
#: (Clopper-Pearson upper bound at 1/60 is 0.089 <= 0.10), which 30 could not.
SMOKE = dict(kind='DEVELOPMENT_SMOKE', replicates=2)


#: Numbers drive sizes. EXACT_CONFIGURED runs the production numbers (16 development
#: markets, 5000 threshold samples, alpha_1 = 0.0025 needing ~1600 rotation draws)
#: in a TEST_ONLY authority context, so it needs synthetic series long enough for
#: the registered alpha to be resolvable (checked by `feasibility`, not assumed).
SMALL = dict(numerics='SMALL', n_symbols=10, n_discovery=6, n_bars=3300, dev_bars=1000)
EXACT = dict(numerics='EXACT_CONFIGURED', n_symbols=20, n_discovery=16, n_bars=9200, dev_bars=2200)
SMOKE = dict(SMALL, kind='DEVELOPMENT_SMOKE', replicates=2)

PROTOCOL = dict(
    schema='closure-calibration-protocol.v3',
    cases=[dict(name='null_independent', edge=0.0, common=0.0, sealed=True, truth='NO_EDGE'),
           dict(name='null_correlated', edge=0.0, common=0.8, sealed=True, truth='NO_EDGE'),
           dict(name='planted_weak', edge=0.002, common=0.0, sealed=True, truth='EDGE'),
           dict(name='planted_strong', edge=0.007, common=0.0, sealed=True, truth='EDGE'),
           dict(name='unsealed_exposure_unknown', edge=0.007, common=0.0, sealed=False, truth='UNAVAILABLE')],
    replicates=60, kind='FINAL', final_seed_base=910_000, development_seed_base=10_000, embargo_bars=10,
    families=None, max_revisions=None, max_false_admission=0.10, min_power=0.50, confidence=0.95, **EXACT)


def feasibility(protocol: dict, policy: dict | None = None) -> list:
    """Reasons the protocol is STRUCTURALLY unable to measure power under its
    own numbers (empty = feasible). Never relaxes a number to fit."""
    from .. import portfolio_null as pn
    from .. import fdr
    pol = policy or _policy(protocol)
    out = []
    if protocol['n_discovery'] < pol['min_discovery_symbols']:
        out.append('discovery_symbols_below_floor')
    if protocol['n_symbols'] - protocol['n_discovery'] < pol['min_heldout_symbols']:
        out.append('heldout_symbols_below_floor')
    dev = protocol['dev_bars'] - 210
    if protocol['n_discovery'] * dev < pol['min_threshold_samples']:
        out.append('threshold_samples_unreachable')
    prot = protocol['n_bars'] - 1 - (protocol['dev_bars'] + 100)
    if prot < pol['min_protected_bars']:
        out.append('protected_window_below_floor')
    alpha1 = fdr.alpha_at(1, [], pol['fdr_target'], pol['lord_w0'])
    draws = pn.draws_for(alpha1, pol['referee_max_draws'])
    b_bars = prot - int(prot * 0.7)
    if draws is None or pn.rotation_offsets(b_bars) < draws:
        out.append(f'registered_alpha_{alpha1:.2e}_unresolvable_in_{b_bars}_later_era_bars')
    return out


def clopper_pearson(k: int, n: int, conf: float = 0.95) -> tuple:
    """Exact binomial interval (bisection on the CDF; no scipy dependency)."""
    if type(n) is not int or type(k) is not int or not 0<=k<=n or not 0<conf<1:
        raise ValueError('binomial_interval_input_invalid')
    if n == 0:
        return 0.0, 1.0

    import numpy as np
    # Log probabilities avoid overflow in comb(n,k) and underflow in tails
    # when calibration uses tens of thousands of numerical replicates.
    terms=min(k,n-k)+1
    indices=np.arange(terms+1,dtype=float)
    coefficients=np.array([math.lgamma(n+1)-math.lgamma(i+1)-math.lgamma(n-i+1) for i in range(terms+1)])
    def cdf_lower(x,p):
        if x<0:return 0.
        if x>=n or p==0:return 1.
        if p==1:return 0.
        logs=coefficients[:x+1]+indices[:x+1]*math.log(p)+(n-indices[:x+1])*math.log1p(-p)
        top=float(logs.max())
        return min(1.,math.exp(top)*float(np.exp(logs-top).sum()))
    def cdf(x,p):
        return cdf_lower(x,p) if x<=n//2 else max(0.,1-cdf_lower(n-x-1,1-p))

    a = (1 - conf) / 2

    def solve(f):
        lo, hi = 0.0, 1.0
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2

    lower = 0.0 if k == 0 else solve(lambda p: 1 - cdf(k - 1, p) < a)
    upper = 1.0 if k == n else solve(lambda p: cdf(k, p) > a)
    return lower, upper


def min_replicates_for(target: float, conf: float) -> int:
    """Smallest n at which ZERO false admissions can show rate <= target."""
    n = 1
    while clopper_pearson(0, n, conf)[1] > target:
        n += 1
    return n


def judge(case: dict, k: int, n: int, protocol: dict) -> dict:
    """Case verdict from counts only. OK / FAIL / INCONCLUSIVE."""
    lo, hi = clopper_pearson(k, n, protocol['confidence'])
    if case['truth'] in ('NO_EDGE', 'UNAVAILABLE'):
        limit = protocol['max_false_admission'] if case['truth'] == 'NO_EDGE' else 0.0
        verdict = 'FAIL' if lo > limit else 'OK' if hi <= limit or (limit == 0.0 and k == 0) else 'INCONCLUSIVE'
        if case['truth'] == 'UNAVAILABLE' and k:
            verdict = 'FAIL'                       # any qualification without proven exposure is a defect
        metric = 'false_admission_rate'
    else:
        verdict = 'FAIL' if hi < protocol['min_power'] else 'OK' if lo >= protocol['min_power'] else 'INCONCLUSIVE'
        metric = 'power'
    return dict(case=case['name'], truth=case['truth'], n=n, qualified=k, metric=metric,
                rate=(k / n if n else None), interval=[round(lo, 4), round(hi, 4)], verdict=verdict)


def _policy(protocol):
    if protocol['numerics'] == 'EXACT_CONFIGURED':
        from ...core.config import load_config
        return manifest.exact_numeric_policy(load_config())
    return manifest.test_policy(**protocol.get('policy', {}))


def _replicate(protocol, case, idx, seed, workdir: Path, run_id: str = '') -> dict:
    d = workdir / f'{case["name"]}-{idx}'
    d.mkdir(parents=True, exist_ok=True)
    snap = d / 'snapshot.db'
    if (d / 'snapshot.ok').exists():          # resume: the generated data are NOT rewritten
        names = synthetic.symbols(protocol['n_symbols'])
    else:
        names = synthetic.write_snapshot(snap, n_symbols=protocol['n_symbols'], n_bars=protocol['n_bars'], seed=seed,
                                         edge=case['edge'], common=case['common'],tf=protocol.get('tf','4h'))
        (d / 'snapshot.ok').write_text(run_id)
    store = Store(d / 'closure.db')
    from ...core.types import TF_MS
    tf=protocol.get('tf','4h')
    st,step_ms=synthetic.EPOCH_MS,TF_MS[tf]
    if case['sealed'] and not exposure.events(store):
        exposure.seal(store, 'SYNTHETIC_DECLARED', st, st + protocol['n_bars'] * step_ms, 1)
    fams = list(protocol.get('families') or [])
    search = None if fams else protocol.get('search')
    if search is None and not fams:
        raise ValueError('calibration_search_not_frozen')
    risk = dict(risk_per_trade_pct=.5, taker_fee_pct=.04, slippage_atr_frac=.015, funding_rate_8h=.0001,
                real_funding=False, max_open_trades=8)
    pol = _policy(protocol)
    sid = S.create_study(store, dict(
        snapshot_path=str(snap), tf=tf, discovery_symbols=names[:protocol['n_discovery']],
        heldout_symbols=names[protocol['n_discovery']:], fit_start_ms=st, dev_end_ms=st + protocol['dev_bars'] * step_ms,
        embargo_bars=protocol['embargo_bars'], protected_start_ms=st + (protocol['dev_bars'] + 100) * step_ms,
        protected_end_ms=st + (protocol['n_bars'] - 1) * step_ms, policy=pol, risk=risk, seed_families=fams,
        search=search, max_revisions=protocol['max_revisions'], workdir=str(d),
        max_candidates=(len(fams) + protocol['max_revisions']) if fams else protocol['max_candidates']),
        now_ms=2)
    for _ in range(50):
        out = S.step(store, sid, budget_s=10 ** 6, child=False)
        if out['step'] != 'DEADLINE':
            break
    status = S.study_status(store, sid)
    runs = [r for r in store.all_once('runs') if r.get('stage') == 'DEVELOPMENT']
    store.close()
    return dict(run_id=run_id, case=case['name'], index=idx, seed=seed, outcome=status['research_outcome'], reason=status['research_reason'],
                trials=len(runs), development_outcomes={o: sum(1 for r in runs if r['outcome'] == o)
                                                        for o in sorted({r['outcome'] for r in runs})},
                process_status=status['process_status'])


def run_identity(protocol: dict, n: int) -> str:
    """Everything that can change a replicate's result: protocol, policy,
    replicate count, evaluator (all code) and the data generator."""
    gen = __import__('hashlib').sha256(Path(synthetic.__file__).read_bytes()).hexdigest()
    return digest(dict(protocol=protocol, n=n, evaluator=manifest.evaluator_id(), generator=gen,
                       policy=_policy(protocol)))


def search_contract(*, search, max_candidates, max_revisions, tf='4h', intake=False,development_bars=None):
    body=dict(search=search,max_candidates=int(max_candidates),max_revisions=int(max_revisions),
              tf=tf,retained_question_intake=bool(intake))
    if development_bars is not None:
        if type(development_bars) is not int or development_bars<=0:raise ValueError('calibration_development_window_invalid')
        body['development_bars']=development_bars
    return body


def report_authorizes(rep: dict, approval: dict, env: dict) -> bool:
    """May this retained report be the approval the configuration names? The
    report is produced in a TEST_ONLY context and grants nothing itself; the
    approval is an explicit, separate configuration act bound to the report id,
    the exact numeric policy and the current evaluator."""
    body = {k: v for k, v in rep.items() if k != 'report_id'}
    return (rep.get('report_id') == digest(body) == approval.get('report_id')
            and rep.get('schema') == 'closure-calibration-report.v3'
            and rep.get('search_scope') == 'COMPOSITIONAL_SEARCH'
            and env.get('search_contract_id') is not None
            and rep.get('search_contract_id') == env['search_contract_id']
            and rep.get('calibration_status') == 'CALIBRATED' and rep.get('kind') == 'FINAL'
            and rep.get('numerics') == 'EXACT_CONFIGURED' and rep.get('authority_granted') is False
            and rep.get('numeric_id') == env.get('numeric_id')
            and rep.get('evaluator_id') == env['evaluator_id'])


def run(protocol: dict, workdir, *, replicates: int | None = None) -> dict:
    """Run (or resume) the protocol. The run identity is bound into the
    directory: a changed protocol/policy/evaluator/generator is refused rather
    than reusing stale replicates, and an interrupted replicate resumes."""
    from copy import deepcopy
    protocol = deepcopy(protocol)
    if not protocol.get('families') and protocol.get('search') is None:
        from ...core.config import load_config
        auto = load_config().get('research', {}).get('closure', {}).get('auto', {})
        protocol['search'] = deepcopy(auto.get('search') or {})
        protocol['max_candidates'] = int(protocol.get('max_candidates',auto.get('max_candidates', 8)))
        # Same default root revision bound as an automatically created study.
        if protocol.get('max_revisions') is None:
            protocol['max_revisions'] = int(auto.get('max_revisions', 4))
    if protocol.get('max_revisions') is None:
        protocol['max_revisions'] = 4
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    n = int(replicates or protocol['replicates'])
    kind = protocol['kind']
    dev_lo, fin_lo = protocol['development_seed_base'], protocol['final_seed_base']
    if kind == 'FINAL' and abs(fin_lo - dev_lo) < len(protocol['cases']) * 10_000 + n:
        raise ValueError('final and development seed ranges overlap')
    impossible = feasibility(protocol)
    if impossible:
        raise ValueError('calibration_structurally_infeasible: ' + ','.join(impossible))
    run_id = run_identity(protocol, n)
    pf = workdir / 'protocol.json'
    if pf.exists():
        if json.loads(pf.read_text()).get('run_id') != run_id:
            raise ValueError('calibration_run_identity_changed: use a new directory')
    else:
        pf.write_text(canonical(dict(protocol=protocol, n=n, run_id=run_id)))
    base = fin_lo if kind == 'FINAL' else dev_lo
    reps = []
    for ci, case in enumerate(protocol['cases']):
        for i in range(n):
            f = workdir / f'rep-{case["name"]}-{i}.json'
            if f.exists():
                r = json.loads(f.read_text())
                if r.get('run_id') != run_id:
                    raise ValueError('calibration_replicate_identity_mismatch')
                reps.append(r)
                continue
            r = _replicate(protocol, case, i, base + ci * 10_000 + i, workdir, run_id)
            f.write_text(canonical(r))
            reps.append(r)
    judged = []
    for case in protocol['cases']:
        mine = [r for r in reps if r['case'] == case['name']]
        judged.append(judge(case, sum(1 for r in mine if r['outcome'] == 'QUALIFIED'), len(mine), protocol))
    need = min_replicates_for(protocol['max_false_admission'], protocol['confidence'])
    pol = _policy(protocol)
    if any(j['verdict'] == 'FAIL' for j in judged):
        status = 'FAILED'
    elif kind != 'FINAL' or n < need or any(j['verdict'] != 'OK' for j in judged):
        status = 'INCONCLUSIVE'
    else:
        status = 'CALIBRATED' if protocol['numerics'] == 'EXACT_CONFIGURED' else 'MACHINERY_CALIBRATED_SMALL_POLICY'
    body = dict(schema='closure-calibration-report.v3',
                search_scope='EXPLICIT_LEGACY_FAMILIES' if protocol.get('families') else 'COMPOSITIONAL_SEARCH',
                search_contract_id=digest(search_contract(search=protocol.get('search'), max_candidates=protocol.get('max_candidates',8),
                                             max_revisions=protocol['max_revisions'],tf=protocol.get('tf','4h'),development_bars=protocol['dev_bars'])),
                omitted_paths=['external LLM source intake and live multi-leg execution'], calibration_status=status, kind=kind, run_id=run_id,
                protocol_id=digest(protocol), policy_class=pol['klass'], numerics=protocol['numerics'],
                numeric_id=manifest.numeric_id(pol), authority_granted=False,
                evaluator_id=manifest.evaluator_id(), replicates_per_case=n, min_replicates_for_null_resolution=need,
                seeds=dict(base=base, scheme='base + case_index*10000 + replicate'), cases=judged,
                replicates=[{k: r[k] for k in ('case', 'index', 'seed', 'outcome', 'trials')} for r in reps],
                note=('Run in a TEST_ONLY authority context; the report grants nothing. CALIBRATED needs FINAL seeds, enough '
                      'replicates, every case OK and the EXACT configured numbers; approval is a separate explicit '
                      'configuration act. INCONCLUSIVE is neither a pass nor evidence of no edge.'))
    return dict(body, report_id=digest(body))
