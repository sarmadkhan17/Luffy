"""I3 — hierarchical, budgeted search over the typed graph.

Two coordinated tracks share one data contract and one ledger:
  * rule/formula construction: single factors -> complementary pairs ->
    alternative-indicator substitutions and regime-conditioned revisions
  * learned combinations: equal-weight, ridge/lasso, and a RESERVED
    interaction track (bounded trees over weak pairs) so effects that only
    work jointly stay reachable.

The budget is registered once per study (write-once: a larger one conflicts,
it cannot be widened after outcomes are seen). Every planned candidate is an
`attempts` row keyed by (study, canonical candidate id), so exact duplicates
and restarts add nothing; failures stay in history. Domains are allocated
round-robin so cheap OHLCV studies cannot starve macro/derivative ones.
Factors whose data is a gap are not searched: they become `requirements`
rows (an explicit development requirement), never a substituted proxy.
Similarity (numeric, entry-overlap, return) is evidence, never a deletion rule.
"""
from __future__ import annotations

import json
from copy import deepcopy

import numpy as np

from ..closure import ClosureRefused, canonical, digest
from . import catalog, graph

#: indicator alternatives: members measure a comparable idea; correlation is
#: tested, not assumed, and a conditional difference may keep both
GROUPS = {
    'oscillator': ('rsi14', 'stoch14', 'bbpctb20', 'zclose96'),
    'trend_distance': ('emadist50', 'emadist200', 'vwapdist96'),
    'return': ('ret6', 'ret24', 'ret72'),
    'volatility': ('atrrank200', 'rvol96'),
    'participation': ('relvol50', 'volz96', 'takerfrac'),
    'external_equity': ('ref_spx_ret30', 'ref_dxy_ret30', 'ref_gold_ret30', 'ref_oil_ret30'),
}
REGIME_GATES = ('regime_trend', 'regime_range', 'regime_highvol', 'regime_thin')
DEFAULT_BUDGET = dict(max_attempts=24, max_fits=40, max_nodes=30, cpu_seconds=3600, dataset_reads=200, memory_mb=3072, symbols=1,
                      interaction_share=0.25, p_long=0.9, p_short=0.9, similarity_label_threshold=0.9,
                      domains={}, tf='4h', prediction_horizon=6,
                      structures=['long_short', 'long_only', 'short_only'], factors=None, question=None)


def register_budget(store, study_id: str, budget: dict) -> dict:
    b = dict(DEFAULT_BUDGET, **budget)
    validate_budget(b)
    old = store.get_once('budgets', study_id)
    if old is not None:                              # budget keys added after a study registered take their default
        if any(b.get(k) != v for k, v in old.items()):
            raise ClosureRefused('record_conflict', f'budgets:{study_id[:12]}')
        return dict(DEFAULT_BUDGET, **old)
    store.put_once('budgets', study_id, b)          # a different budget for the same study conflicts: no silent widening
    return store.get_once('budgets', study_id)


def budget_for(store, study_id: str, study: dict) -> dict:
    """The study's registered totals: those the search registered, else the declared/default budget registered NOW
    (write-once). Seed graphs, intake and revision candidates therefore run under the same totals as searched ones."""
    return register_budget(store, study_id, dict(study.get('search') or {}, tf=study['tf'],
                                                 symbols=max(len(study.get('discovery_symbols') or []), 1)))


def dispatch_gate(store, study_id: str, budget: dict, *, fits: int = 0, reads: int = 0) -> str | None:
    """Refusal reason if ONE more dispatch of this size would exceed the registered CUMULATIVE totals (every earlier
    dispatch, retry and interrupted attempt included), else None. Totals are never widened here."""
    t = store.resource_totals(study_id)
    if t['fits'] + fits > budget['max_fits']:
        return 'BUDGET_EXHAUSTED:fits'
    if t['reads'] + reads > budget['dataset_reads']:
        return 'BUDGET_EXHAUSTED:dataset_reads'
    if t['seconds'] >= budget['cpu_seconds']:
        return 'BUDGET_EXHAUSTED:cpu_seconds'
    return None


def cpu_left(store, study_id: str, budget: dict) -> float:
    return max(float(budget['cpu_seconds']) - store.resource_totals(study_id)['seconds'], 0.0)


def validate_budget(b):
    from ...core.types import TF_MS
    if b['tf'] not in TF_MS or not 1 <= b['prediction_horizon'] <= graph.MAX_WINDOW:
        raise ClosureRefused('search_horizon_invalid')
    if not b['structures'] or any(x not in graph.ENTRY_STRUCTURES for x in b['structures']):
        raise ClosureRefused('search_structures_invalid')
    if b['factors'] is not None and (not b['factors'] or any(x not in catalog.BY_KEY for x in b['factors'])):
        raise ClosureRefused('search_factors_invalid')
    for key in ('max_attempts', 'max_fits', 'max_nodes', 'cpu_seconds', 'dataset_reads', 'memory_mb', 'symbols'):
        if type(b[key]) not in (int, float) or not np.isfinite(b[key]) or b[key] <= 0:
            raise ClosureRefused('search_budget_invalid', key)
    if not 0 <= b['interaction_share'] <= 1 or not all(0 < b[k] < 1 for k in ('p_long', 'p_short')):
        raise ClosureRefused('search_probability_invalid')


def _attempts(store, study_id):
    return store.attempts_for(study_id)


def _fit_specs(node):
    if isinstance(node, dict):
        if 'fit_spec' in node:
            yield node['fit_spec']
        for v in node.values():
            yield from _fit_specs(v)
    elif isinstance(node, list):
        for v in node:
            yield from _fit_specs(v)


def _fits(c) -> int:
    """ACTUAL model fits the development will execute for this candidate (not the number of declarations):
    (folds+1) walk-forward segments, each with one inner fit per grid value when a grid is declared, plus the
    segment's own final fit. Segments that abstain for lack of rows execute fewer, never more."""
    total = 0
    for s in list(_fit_specs(c.get('long'))) + list(_fit_specs(c.get('short'))):
        key = 'max_depth' if s.get('model') == 'tree' else 'alpha'
        g = len((s.get('grid') or {}).get(key) or [])
        total += (int(s.get('folds', 4)) + 1) * ((g if g > 1 else 0) + 1)
    return total


def reads_of(c, symbols: int = 1) -> int:
    """Dataset loads one development of this candidate performs, counted as the loaders execute them: candle frames
    once per symbol, each derivative series once per symbol, each reference store key once, the World cut once.
    Counted at admission, enforced against `dataset_reads`."""
    req = [r for r in graph.requires_of(c) if r != 'ohlcv']
    refs = sum(1 for r in req if r.startswith('ref:'))
    world = 1 if 'world' in req else 0
    derivs = len(req) - refs - world
    return int(symbols) * (1 + derivs) + refs + world


def _nodes(c) -> int:
    def count(node):
        if isinstance(node, dict):
            return 1 + sum(count(v) for v in node.values())
        if isinstance(node, list):
            return sum(count(v) for v in node)
        return 0
    return count(c.get('long')) + count(c.get('short')) + count((c.get('exit') or {}).get('signal_exit'))


def spent(store, study_id: str, units=()) -> dict:
    at = _attempts(store, study_id)
    cpu = 0.0
    for u in units:
        try:
            cpu += float((json.loads(u['result']) or {}).get('elapsed_s') or 0.0) if u.get('result') else 0.0
        except (TypeError, ValueError):
            pass
    cpu = max(cpu, float(store.resource_seconds(study_id)))      # durable ledger: restarts/timeouts keep what they spent
    return dict(attempts=len(at), fits=sum(a['fits'] for a in at), cpu_seconds=round(cpu, 2),
                dataset_reads=sum(a.get('reads', 1) for a in at),
                by_domain={d: sum(1 for a in at if a['domain'] == d) for d in sorted({a['domain'] for a in at})})


def exhausted(store, study_id: str, budget: dict, units=()) -> str | None:
    s = spent(store, study_id, units)
    for k, lim in (('attempts', 'max_attempts'), ('fits', 'max_fits'), ('cpu_seconds', 'cpu_seconds'),
                   ('dataset_reads', 'dataset_reads')):
        if s[k] >= budget[lim]:
            return f'BUDGET_EXHAUSTED:{k}'
    return None


def domain_of(c: dict) -> str:
    req = graph.requires_of(c)
    if 'world' in req or any(r.startswith('ref:') for r in req):
        return 'market_context'
    if any(r != 'ohlcv' and not r.startswith('ref:') for r in req):
        return 'derivatives_liquidity'
    keys = [n['f'] for n in _walk_factors(c)]
    return catalog.BY_KEY[keys[0]].domain if keys else 'price_structure'


def _walk_factors(c):
    out = []

    def w(n):
        if isinstance(n, dict):
            if 'f' in n:
                out.append(n)
            for v in n.values():
                w(v)
        elif isinstance(n, list):
            for v in n:
                w(v)
    w(c.get('long')), w(c.get('short'))
    return out


def _top(factor, p, d):
    return {'top': {'a': {'f': factor} if isinstance(factor, str) else factor, 'p': p, 'dir': d}}


def _spec(question, long, short, *, track, structure='long_short', geo='fixed', rationale=None, **extra):
    return dict(question=question, long=long, short=short, rationale=rationale or 'composed from catalogued factors; effect is an untested hypothesis',
                expected='the stated condition precedes favourable forward returns on the declared side',
                failure_conditions='no net edge after costs; instability across eras; redundancy with simpler parts',
                structure=structure, tf='4h', geo=geo, provenance=dict(track=track), **extra)


def _unit_ok(key):
    f = catalog.BY_KEY[key]
    return f.unit != 'bool' and not f.provider_gap


def _candidate_templates(budget: dict, status: dict) -> dict:
    """{domain: [spec, ...]} in deterministic order. `status` maps factor key ->
    inventory status; only EXECUTABLE factors are searched."""
    ok = lambda k: status.get(k, 'GAP_PROVIDER') in ('EXECUTABLE', 'EXECUTABLE_UNVERIFIED') and _unit_ok(k)  # noqa: E731
    pl, ps = budget['p_long'], budget['p_short']
    out = {}
    for f in catalog.FACTORS:
        if not ok(f.key):
            continue
        dom = 'market_context' if any(r.startswith('ref:') for r in f.requires) else (
            'derivatives_liquidity' if f.requires != ('ohlcv',) else f.domain)
        q = f'Does {f.meaning} direct forward returns?'
        for tag, hi_long in (('trend', True), ('reversion', False)):
            lg = _top(f.key, pl, 'above') if hi_long else _top(f.key, 1 - pl, 'below')
            sh = _top(f.key, 1 - ps, 'below') if hi_long else _top(f.key, ps, 'above')
            out.setdefault(dom, []).append(_spec(f'{q} ({tag})', lg, sh, track='single', roles={f.key: 'feature'}))
    # complementary pairs: one factor from each of two different groups/domains (A, B, A+B compared via ablation)
    keys = [f.key for f in catalog.FACTORS if ok(f.key)]
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            fa, fb = catalog.BY_KEY[a], catalog.BY_KEY[b]
            if fa.domain == fb.domain:
                continue
            dom = 'market_context' if any(r.startswith('ref:') for r in fa.requires + fb.requires) else (
                'derivatives_liquidity' if (fa.requires + fb.requires) != ('ohlcv', 'ohlcv') else fa.domain)
            lg = {'all': [_top(a, pl, 'above'), _top(b, pl, 'above')]}
            sh = {'all': [_top(a, 1 - ps, 'below'), _top(b, 1 - ps, 'below')]}
            out.setdefault(dom, []).append(_spec(f'Do {fa.meaning} and {fb.meaning} agree before a move?', lg, sh,
                                                 track='pair', roles={a: 'feature', b: 'condition'}))
    # Select from available sources, rather than a fixed technical shortlist.
    # The matched equal-weight baseline needs 6*n+2 nodes for both branches;
    # derive input count from the existing complexity budget, not feature IDs.
    limit = max(0, (int(budget['max_nodes']) - 2) // 6)
    sources = {}
    for k in keys:
        req = catalog.BY_KEY[k].requires
        source = ('reference' if any(r.startswith('ref:') for r in req) else
                  'world' if 'world' in req else 'ohlcv' if req == ('ohlcv',) else 'derivative')
        sources.setdefault(source, []).append(k)
    mix = [v[0] for v in sources.values()][:limit]
    for k in keys:
        if len(mix) >= limit:
            break
        if k not in mix and catalog.BY_KEY[k].domain not in {catalog.BY_KEY[m].domain for m in mix}:
            mix.append(k)
    if len(mix) >= 3:
        ins = [{'f': k} for k in mix]
        for model in ('ridge', 'lasso'):
            spec_l = lambda side: {'fit_spec': dict(model=model, side=side, horizon=6, inputs=ins, alpha=0.01 if model == 'lasso' else 0.1)}  # noqa: E731
            out.setdefault('learned', []).append(_spec(
                f'Does a {model} combination of {", ".join(mix)} rank forward returns?',
                {'top': {'a': spec_l('long'), 'p': pl, 'dir': 'above'}}, {'top': {'a': spec_l('short'), 'p': ps, 'dir': 'above'}},
                track=f'learned-{model}'))
        terms = [{'op': 'zscore', 'a': {'f': k}, 'n': 200} for k in mix]
        score = terms[0]
        for term in terms[1:]:
            score = {'op': 'add', 'a': score, 'b': term}
        out.setdefault('learned', []).append(_spec(
            f'Does an equal-weight combination of {", ".join(mix)} rank forward returns?',
            _top(deepcopy(score), pl, 'above'), _top(deepcopy(score), 1 - ps, 'below'),
            track='equal-weight'))
    # reserved interaction track: bounded trees over weak pairs
    ext = [k for k in keys if any(r.startswith('ref:') for r in catalog.BY_KEY[k].requires)]
    base = [k for k in keys if catalog.BY_KEY[k].requires == ('ohlcv',) and catalog.BY_KEY[k].unit not in ('bool',)]
    pairs = [(a, b) for i, a in enumerate(ext) for b in ext[i + 1:]] + [(a, b) for a in ext[:3] for b in base[:3]]
    for a, b in pairs:
        fa, fb = {'f': a}, {'f': b}
        # an interaction EXPRESSION (a*b) lets a regularised linear model represent parity/agreement effects with zero
        # marginals; a bounded tree is the second, structure-free representation of the same joint state
        for tag, model, ins, extra in (('tree', 'tree', [fa, fb], dict(max_depth=3, min_leaf=100)),
                                        ('product', 'ridge', [fa, fb, {'op': 'mul', 'a': fa, 'b': fb}], dict(alpha=0.01))):
            mk = lambda side: {'fit_spec': dict(model=model, side=side, horizon=6, inputs=ins, **extra)}  # noqa: E731
            out.setdefault('interaction', []).append(_spec(
                f'Does the joint state of {a} and {b} matter even if neither does alone? ({tag})',
                {'top': {'a': mk('long'), 'p': pl, 'dir': 'above'}}, {'top': {'a': mk('short'), 'p': ps, 'dir': 'above'}},
                track='interaction'))
    return out


def candidate_stream(budget: dict, status: dict) -> dict:
    """Instantiate search operators under the study's frozen contract.

    Direction alternatives are independent candidates, never an implicit
    symmetric execution rule. Question text is provenance, not numeric evidence.
    """
    budget = dict(DEFAULT_BUDGET, **budget)
    validate_budget(budget)
    selected = budget['factors']
    status = {k:v for k,v in status.items() if (selected is None or k in selected)
              and budget['tf'] in catalog.BY_KEY[k].horizons}
    source = _candidate_templates(budget, status)
    def horizon(n):
        if isinstance(n, dict):
            if 'fit_spec' in n:
                n['fit_spec']['horizon'] = budget['prediction_horizon']
            for v in n.values():
                horizon(v)
        elif isinstance(n, list):
            for v in n:
                horizon(v)
    out = {}
    for domain, templates in source.items():
        for template in templates:
            for structure in budget['structures']:
                proposal = deepcopy(template)
                proposal['tf'] = budget['tf']
                proposal['structure'] = structure
                if structure == 'long_only':
                    proposal['short'] = None
                if structure == 'short_only':
                    proposal['long'] = None
                if budget['question']:
                    proposal['question'] = budget['question'] + ' / ' + proposal['question']
                    proposal['provenance']['study_question'] = budget['question']
                proposal['provenance']['prediction_horizon'] = budget['prediction_horizon']
                horizon(proposal)
                out.setdefault(domain, []).append(proposal)
    # Interleave model types before direction variants. Otherwise the first
    # model can consume the whole fit budget before another model is considered.
    for domain in ('learned', 'interaction'):
        tracks = {}
        for proposal in out.get(domain, []):
            spec = next(_fit_specs(proposal), {})
            key = (proposal['provenance']['track'], spec.get('model'))
            tracks.setdefault(key, []).append(proposal)
        ordered = []
        while any(tracks.values()):
            for queue in tracks.values():
                if queue:
                    ordered.append(queue.pop(0))
        if ordered:
            out[domain] = ordered
    return out


def requirements_for(status: dict) -> list:
    """Declared-but-unavailable factors: retained development requirements."""
    return [dict(factor=f.key, domain=f.domain, status=status.get(f.key, 'GAP_PROVIDER'),
                 reason=f.provider_gap or 'coverage not measured/insufficient')
            for f in catalog.FACTORS if status.get(f.key, 'GAP_PROVIDER') not in ('EXECUTABLE', 'EXECUTABLE_UNVERIFIED')]


def plan(store, study_id: str, budget: dict, status: dict, *, units=(), now_ms: int = 0) -> dict:
    """Admit candidates round-robin across domains until the budget ends.
    Interaction share is reserved first so it cannot be crowded out."""
    for r in requirements_for(status):
        store.put_once('requirements', digest([study_id, r['factor']]), dict(r, study_id=study_id))
    stream = candidate_stream(budget, status)
    reserve = int(budget['max_attempts'] * budget['interaction_share']) if stream.get('interaction') else 0
    admitted, why, fit_limited = [], None, []
    queues = {d: list(v) for d, v in sorted(stream.items())}
    order = [d for d in queues if d != 'interaction']
    caps = budget.get('domains') or {}

    def admit(spec, track_domain):
        nonlocal why
        try:
            c = graph.make_candidate(spec)
        except ClosureRefused as exc:
            store.put_once('requirements', digest([study_id, 'refused', canonical(spec)[:200]]),
                           dict(study_id=study_id, factor=None, status='REFUSED', reason=str(exc)))
            return False
        dom = track_domain
        aid = digest([study_id, c['candidate_id']])
        if store.get_once('attempts', aid) is not None:
            return False                                   # exact duplicate / restart: no new attempt
        s = spent(store, study_id, units)
        if _nodes(c) > budget['max_nodes']:
            why = 'COMPLEXITY_EXCEEDED'
            return False
        inter_fits = sum(a['fits'] for a in _attempts(store, study_id) if a['domain'] == 'interaction')
        inter_cap = int(np.ceil(budget['max_fits'] * budget['interaction_share']))
        if dom == 'interaction' and inter_fits + _fits(c) > inter_cap:
            fit_limited.append('interaction')
            return False
        if dom != 'interaction' and _fits(c) and (s['fits'] - inter_fits) + _fits(c) > budget['max_fits'] - inter_cap:
            fit_limited.append(dom)             # fitting candidates stop; rule candidates still proceed
            return False
        if s['fits'] + _fits(c) > budget['max_fits']:
            fit_limited.append(dom)
            return False
        if caps.get(dom) is not None and s['by_domain'].get(dom, 0) >= caps[dom]:
            return False
        if s['dataset_reads'] + reads_of(c, budget['symbols']) > budget['dataset_reads']:
            why = why or 'BUDGET_EXHAUSTED:dataset_reads'
            return False
        if s['attempts'] >= budget['max_attempts'] or exhausted(store, study_id, budget, units):
            why = why or 'BUDGET_EXHAUSTED:attempts'
            return False
        store.put_once('candidates', c['candidate_id'], c)
        store.put_once('attempts', aid, dict(study_id=study_id, candidate_id=c['candidate_id'], domain=dom,
                                             track=spec['provenance']['track'], fits=_fits(c), nodes=_nodes(c), reads=reads_of(c, budget['symbols']),
                                             parent=None, change=None, at_ms=now_ms))
        admitted.append(c['candidate_id'])
        return True

    # reserved interaction track first
    for spec in queues.get('interaction', []):
        if sum(1 for a in _attempts(store, study_id) if a['domain'] == 'interaction') >= reserve:
            break
        admit(spec, 'interaction')
    progress = True
    while progress:
        progress = False
        for d in order:
            while queues[d]:
                spec = queues[d].pop(0)
                if admit(spec, d):
                    progress = True
                    break
        if why and why.startswith('BUDGET_EXHAUSTED'):
            break
    stopped = why or ('STREAM_EXHAUSTED' if not fit_limited else 'FIT_BUDGET_EXHAUSTED_FOR:' + ','.join(sorted(set(fit_limited))))
    return dict(admitted=admitted, stopped=stopped, spent=spent(store, study_id, units),
                interaction_reserved=reserve)


# ── revisions (Step D): alternatives and regime-conditioned versions ─────
def _subst(node, a, b):
    if isinstance(node, dict):
        if node.get('f') == a:
            return {'f': b}
        return {k: _subst(v, a, b) for k, v in node.items()}
    if isinstance(node, list):
        return [_subst(v, a, b) for v in node]
    return node


def mutations(c: dict) -> list:
    """Every legal graph revision, fixed order: indicator substitution inside a
    comparable group, then a regime gate on both branches."""
    out = []
    present = {n['f'] for n in _walk_factors(c)}
    for gname, members in GROUPS.items():
        for a in sorted(present & set(members)):
            for b in members:
                if b != a and b not in present:
                    out.append(dict(kind='substitute', group=gname, **{'from': a, 'to': b}))
    if True:  # regime revisions remain candidates, never automatic filters
        for g in REGIME_GATES:
            if g not in present:
                out.append(dict(kind='regime_gate', to=g))
    return out


def apply_change(c: dict, change: dict) -> dict:
    if change not in mutations(c):
        raise ClosureRefused('mutation_not_allowed', str(change))
    if change['kind'] == 'substitute':
        long_, short_ = (None if c[k] is None else _subst(c[k], change['from'], change['to']) for k in ('long', 'short'))
    else:
        gate = {'f': change['to']}
        wrap = lambda n: None if n is None else {'all': [n, gate]}      # noqa: E731
        long_, short_ = wrap(c['long']), wrap(c['short'])
    spec = {k: c[k] for k in ('question', 'rationale', 'expected', 'failure_conditions', 'structure', 'tf', 'geo',
                              'target', 'hedge')}
    spec.update(long=long_, short=short_, roles=dict(c.get('roles') or {}),
                provenance=dict(c.get('provenance') or {}, parent=c['candidate_id'], change=change,
                                track='revision'))
    return graph.make_candidate(spec)


#: spec step D vocabulary over the closure outcomes
DIAGNOSIS = {'BLOCKED_DATA': 'DATA_BLOCKED', 'CODE_FAILURE': 'IMPLEMENTATION_FAILED',
             'INCONCLUSIVE_POWER': 'INSUFFICIENT_EVIDENCE', 'NEGATIVE_ECONOMICS': 'ECONOMIC_UNSUPPORTED',
             'DEVELOPMENT_POSITIVE': 'DEVELOPMENT_SUPPORTED'}


# ── similarity is evidence, not a deletion rule ──────────────────────────
def factor_matrix(keys, ctxs) -> dict:
    """Matched-sample comparison of catalogued factors on the SAME rows."""
    cols = {k: np.concatenate([graph._values(catalog.BY_KEY[k].formula, c) for c in ctxs]) for k in keys}
    n_all = len(next(iter(cols.values())))
    matched = np.all([np.isfinite(v) for v in cols.values()], axis=0)
    out = dict(n_rows=int(n_all), n_matched=int(matched.sum()),
               coverage={k: round(float(np.isfinite(v).mean()), 4) for k, v in cols.items()}, pairs=[])
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            va, vb = cols[a][matched], cols[b][matched]
            both = np.isfinite(cols[a]) & np.isfinite(cols[b])
            corr = float(np.corrcoef(va, vb)[0, 1]) if matched.sum() > 3 and va.std() > 0 and vb.std() > 0 else None
            corr_pair = float(np.corrcoef(cols[a][both], cols[b][both])[0, 1]) if both.sum() > 3 else None
            out['pairs'].append(dict(a=a, b=b, numeric_corr_matched=corr, numeric_corr_pairwise=corr_pair,
                                     n_pair=int(both.sum())))
    return out


def entry_overlap(expr_a: str, expr_b: str, ctxs) -> dict:
    from ...strategy import dsl
    ea = np.concatenate([dsl.evaluate_bool(dsl.parse(expr_a), c) for c in ctxs])
    eb = np.concatenate([dsl.evaluate_bool(dsl.parse(expr_b), c) for c in ctxs])
    union = int((ea | eb).sum())
    return dict(n_a=int(ea.sum()), n_b=int(eb.sum()), jaccard=(float((ea & eb).sum()) / union) if union else None)


def signal_return_similarity(expr_pairs, ctxs) -> float | None:
    """Correlation of gross next-bar signal returns of two (long, short) rules."""
    from ...strategy import dsl
    rs = [[], []]
    for c in ctxs:
        close = c.df['close'].to_numpy(dtype=float)
        nxt = np.full(len(close), np.nan)
        nxt[:-1] = close[1:] / close[:-1] - 1.0
        for i, (lg, sh) in enumerate(expr_pairs):
            pos = dsl.evaluate_bool(dsl.parse(lg), c).astype(float) - dsl.evaluate_bool(dsl.parse(sh), c).astype(float)
            rs[i].append(pos * nxt)
    a, b = np.concatenate(rs[0]), np.concatenate(rs[1])
    ok = np.isfinite(a) & np.isfinite(b)
    if ok.sum() < 10 or a[ok].std() == 0 or b[ok].std() == 0:
        return None
    return float(np.corrcoef(a[ok], b[ok])[0, 1])
