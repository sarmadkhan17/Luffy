"""Development experiment plans and comparisons. Never admission or trading authority."""
from copy import deepcopy
from . import catalog, graph
from ..closure import digest

SCHEMA = 'research-experiment-plan.v1'


def needs_btc(candidate):
    import ast
    names = {'btc_ret', 'btc_ema_dist', 'corr_btc', 'rel_strength_btc'}
    factors={n['f'] for n in _nodes(candidate) if 'f' in n}
    factors.update(k for k,v in (candidate.get('roles') or {}).items() if v in ('exposure','stress'))
    for key in factors:
        tree = ast.parse(catalog.BY_KEY[key].formula)
        if any(isinstance(n, ast.Call) and getattr(n.func, 'id', None) in names for n in ast.walk(tree)):
            return True
    return False


def plan(candidate, *, budget=None):
    graph.verify(candidate)
    factors = sorted({n['f'] for n in _nodes(candidate) if 'f' in n})
    purpose = (candidate.get('hedge') or {}).get('purpose')
    objective = ('risk_reduction_after_carry' if purpose and purpose != 'relative_value' else
                 'relative_value_net_return' if purpose else 'net_return_after_costs')
    body = dict(schema=SCHEMA, candidate_id=candidate['candidate_id'],
                hypothesis=dict(question=candidate['question'], mechanism=candidate['rationale'],
                                expected=candidate['expected'], falsifier=candidate['failure_conditions']),
                objective=objective, timeframe=candidate['tf'], structure=candidate['structure'],
                required_inputs=sorted(set(graph.requires_of(candidate)) | ({'btc'} if needs_btc(candidate) else set())), factors=factors,
                comparators=['cash', 'direction_matched_simple_control', 'component_removals'],
                alternative_explanations=['market_direction', 'exit_geometry', 'costs', 'redundant_inputs'],
                comparisons=dict(indicator_alternatives={g: [x for x in ks if not catalog.BY_KEY[x].provider_gap]
                                 for g, ks in _groups().items() if set(ks).intersection(factors)},
                                 geometry=candidate.get('geo'), explicit_exit=candidate.get('exit')),
                execution_stress=dict(delays_bars=[0,1],cost_multipliers=[1.0,2.0],scope='DEVELOPMENT_DESCRIPTIVE_ONLY'),
                budget=deepcopy(budget or {}), status='UNTESTED', authority='DEVELOPMENT_ONLY')
    return dict(body, plan_id=digest(body))


def _groups():
    from .search import GROUPS
    return GROUPS


def _nodes(candidate):
    def walk(n):
        if isinstance(n, dict):
            yield n
            for v in n.values():
                yield from walk(v)
        elif isinstance(n, list):
            for v in n:
                yield from walk(v)
    for branch in ('long', 'short'):
        yield from walk(candidate.get(branch))
    yield from walk((candidate.get('exit') or {}).get('signal_exit'))


def comparisons(candidate, ctxs, *, max_factors=6):
    """Bounded matched development comparisons; descriptive, never admission.

    Measure the candidate's factors AND same-role alternatives, rather than
    deciding beforehand that correlated indicators are interchangeable.
    """
    from .search import factor_matrix
    selected = sorted({n['f'] for n in _nodes(candidate) if 'f' in n})
    available = list(selected)
    for keys in _groups().values():
        if set(keys).intersection(selected):
            available.extend(k for k in keys if k not in available)
    measured, refused = [], []
    for key in available[:max_factors]:
        try:
            # Only numeric factors can enter a numeric correlation comparison.
            if catalog.BY_KEY[key].unit != 'bool':
                factor_matrix([key], ctxs)
                measured.append(key)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            refused.append(dict(factor=key, reason=str(exc)[:160]))
    return dict(scope='MATCHED_DEVELOPMENT_DESCRIPTIVE',
                matrix=factor_matrix(measured, ctxs) if measured else None,
                unavailable=refused, deferred=[k for k in available if k not in measured
                                             and k not in {r['factor'] for r in refused}],
                authority='NO_QUALIFICATION')


def simple_control(candidate):
    """Prespecified direction-matched momentum control with the same exit.

    It is a comparator, never an independent confirmation or a new discovery.
    No data-dependent parameter selection and no fitted control weights.
    """
    graph.verify(candidate)
    if candidate['structure'] not in graph.ENTRY_STRUCTURES:
        return None
    positive={'cmp':'gt','a':{'f':'ret24'},'b':{'lit':0}}
    negative={'cmp':'lt','a':{'f':'ret24'},'b':{'lit':0}}
    long_=None if candidate['structure']=='short_only' else positive
    short_=None if candidate['structure']=='long_only' else negative
    if (graph.canon(long_) if long_ is not None else None,graph.canon(short_) if short_ is not None else None)==(graph.canon(candidate['long']) if candidate['long'] is not None else None,graph.canon(candidate['short']) if candidate['short'] is not None else None):
        return None  # the same idea does not become its own independent control
    spec={k:candidate[k] for k in ('tf','structure','geo','exit','target','hedge')}
    spec.update(long=long_,short=short_,roles={},question='Simple direction-matched momentum control',
        rationale='Prespecified 24-bar return sign control, with the candidate exit and the same cost/sample convention.',
        expected='Establish the simple-direction benchmark; not supporting evidence for the candidate.',
        failure_conditions='A candidate that does not improve on this control has not established incremental economics.',
        provenance={'track':'declared-control','parent':candidate['candidate_id']})
    return graph.make_candidate(spec)
