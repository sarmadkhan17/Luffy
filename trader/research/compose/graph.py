"""I2 — the typed expression graph and its compiler.

A candidate is JSON: nodes reference catalog factors (never raw strings), are
unit/chronology/dependency checked, and compile to the EXISTING DSL, so the
existing evaluator, point-in-time alignment and trade engine execute them. No
generated Python runs. A requested mechanism the catalog/operators cannot
express stays visible as a development requirement (`GraphRefused` codes
GAP_PROVIDER / UNSUPPORTED_OP), never silently replaced.

Nodes
  {"f": key}                      catalog factor
  {"lit": x}
  {"op": add|sub|mul|div|min|max|neg|abs|lag|sma|std|zscore|pct_rank|xrank|corr|ref|htf, ...}
  {"fit_spec": {...}}             a model to FIT on development (-> {"fit": id})
  {"fit": artifact_id}            fitted score (strategy.fitted)
  {"cmp": gt|lt|ge|le, "a": n, "b": n}
  {"top": {"a": n, "p": 0.9, "dir": above|below}}   threshold learned on development (-> cmp)
  {"all": [...]} {"any": [...]} {"not": n}

Identity (`graph_id`) hashes the canonical form: commutative arguments sorted,
conjunctions flattened and de-duplicated, comparisons oriented, double
negation removed, factor keys replaced by their formulas. Aliases therefore
collapse; numerically similar but different graphs do not (see search.py).
"""
from __future__ import annotations

import json

import numpy as np

from ...strategy import dsl, fitted
from ...strategy.features import FeatureCtx
from ..closure import ClosureRefused, digest
from . import catalog, models

GRAPH_SCHEMA = 'closure-graph-candidate.v1'
STRUCTURES = ('long_short', 'long_only', 'short_only', 'hedged', 'pair', 'overlay', 'cash')
ENTRY_STRUCTURES = ('long_short', 'long_only', 'short_only')    # executable by the existing evaluator
HEDGE_STRUCTURES = ('hedged', 'pair', 'overlay')                # evaluated by compose.hedge on the combined book
BINOPS = {'add': '+', 'sub': '-', 'mul': '*', 'div': '/'}
COMMUTATIVE = {'add', 'mul', 'min', 'max'}
NEVER = 'close < 0'                                                # constant False: an absent branch
MAX_NODES = 40
MAX_WINDOW = 500
HTF = ('1h', '4h')


class GraphRefused(ClosureRefused):
    pass


def _bad(code, detail=''):
    raise GraphRefused(code, detail)


# ── typing ───────────────────────────────────────────────────────────────
def _unit_join(a, b, what):
    if a == b or b == 'any':
        return a
    if a == 'any':
        return b
    _bad('UNIT_MISMATCH', f'{what}: {a} vs {b}')


def _win(n, what):
    if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= MAX_WINDOW:
        _bad('BAD_WINDOW', f'{what}={n!r}')
    return n


def check(node, tf, *, _count=None) -> str:
    """Unit of `node` ('bool' for conditions); raises GraphRefused."""
    _count = _count if _count is not None else [0]
    _count[0] += 1
    if _count[0] > MAX_NODES:
        _bad('GRAPH_TOO_LARGE', f'>{MAX_NODES} nodes')
    if not isinstance(node, dict) or len(node) < 1:
        _bad('MALFORMED_NODE', repr(node)[:60])
    rec = lambda n: check(n, tf, _count=_count)              # noqa: E731
    if 'f' in node:
        f = catalog.BY_KEY.get(node['f'])
        if f is None:
            _bad('UNKNOWN_FACTOR', str(node['f']))
        if f.provider_gap:
            _bad('GAP_PROVIDER', f'{f.key}: {f.provider_gap}')
        if tf not in f.horizons:
            _bad('HORIZON_UNSUPPORTED', f'{f.key}@{tf}')
        return f.unit
    if 'lit' in node:
        v = node['lit']
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v):
            _bad('NONFINITE_LITERAL', repr(v))
        return 'any'
    if 'fit' in node:
        fitted.get(node['fit'])
        return 'score'
    if 'fit_pass' in node:
        if fitted.get(node['fit_pass'])['model_type'] != 'schedule':
            _bad('BAD_FIT_PASS', 'needs a schedule artifact')
        return 'bool'
    if 'fit_spec' in node:
        s = node['fit_spec']
        if s.get('model') not in fitted.MODEL_TYPES or s.get('side') not in ('long', 'short'):
            _bad('BAD_FIT_SPEC', 'model/side')
        _win(s.get('horizon'), 'horizon')
        if not s.get('inputs'):
            _bad('BAD_FIT_SPEC', 'no inputs')
        for i in s['inputs']:
            if rec(i) == 'bool':
                _bad('UNIT_MISMATCH', 'fit input must be numeric')
        return 'score'
    if 'cmp' in node:
        if node['cmp'] not in ('gt', 'lt', 'ge', 'le'):
            _bad('UNSUPPORTED_OP', str(node['cmp']))
        a, b = rec(node['a']), rec(node['b'])
        if 'bool' in (a, b):
            _bad('UNIT_MISMATCH', 'comparison of a condition')
        _unit_join(a, b, 'cmp')
        return 'bool'
    if 'top' in node:
        t = node['top']
        if rec(t['a']) == 'bool' or not 0.0 < float(t['p']) < 1.0 or t.get('dir') not in ('above', 'below'):
            _bad('BAD_TOP', 'a/p/dir')
        return 'bool'
    for key in ('all', 'any'):
        if key in node:
            if not node[key]:
                _bad('EMPTY_BOOL')
            for n in node[key]:
                if rec(n) != 'bool':
                    _bad('UNIT_MISMATCH', f'{key} of non-condition')
            return 'bool'
    if 'not' in node:
        if rec(node['not']) != 'bool':
            _bad('UNIT_MISMATCH', 'not of non-condition')
        return 'bool'
    op = node.get('op')
    if op in BINOPS or op in ('min', 'max'):
        a, b = rec(node['a']), rec(node['b'])
        if 'bool' in (a, b):
            _bad('UNIT_MISMATCH', f'{op} of a condition')
        if op in ('add', 'sub', 'min', 'max'):
            return _unit_join(a, b, op)
        return 'derived' if 'any' not in (a, b) or op == 'div' else (b if a == 'any' else a)
    if op in ('neg', 'abs'):
        u = rec(node['a'])
        return u if u != 'bool' else _bad('UNIT_MISMATCH', op)
    if op in ('lag', 'sma', 'std'):
        _win(node.get('n'), op)
        u = rec(node['a'])
        return u if u != 'bool' else _bad('UNIT_MISMATCH', op)
    if op in ('zscore', 'pct_rank'):
        _win(node.get('n'), op)
        if rec(node['a']) == 'bool':
            _bad('UNIT_MISMATCH', op)
        return 'z' if op == 'zscore' else 'rank01'
    if op == 'xrank':
        if rec(node['a']) == 'bool':
            _bad('UNIT_MISMATCH', op)
        return 'rank01'
    if op == 'corr':
        _win(node.get('n'), 'corr')
        rec(node['a']), rec(node['b'])
        return 'corr'
    if op == 'ref':
        from ...data.references import REFS
        if node.get('key') not in REFS:
            _bad('UNKNOWN_REFERENCE', str(node.get('key')))
        u = rec(node['a'])
        return u if u != 'bool' else _bad('UNIT_MISMATCH', 'ref')
    if op == 'htf':
        from ...core.types import TF_MS
        if node.get('tf') not in TF_MS or tf not in TF_MS or TF_MS[node['tf']] <= TF_MS[tf]:
            _bad('CHRONOLOGY', f'htf {node.get("tf")} must be slower than {tf}')
        return rec(node['a'])
    _bad('UNSUPPORTED_OP', json.dumps(node, sort_keys=True)[:80])


# ── canonical form ───────────────────────────────────────────────────────
def _j(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'))


def canon(node):
    if 'd' in node:
        return node
    if 'f' in node:
        return {'d': catalog.BY_KEY[node['f']].formula}
    if 'lit' in node:
        return {'lit': float(node['lit'])}
    if 'fit' in node:
        return {'fit': node['fit']}
    if 'fit_pass' in node:
        return {'fit_pass': node['fit_pass']}
    if 'fit_spec' in node:
        s = node['fit_spec']
        return {'fit_spec': dict(s, inputs=[canon(i) for i in s['inputs']])}
    if 'top' in node:
        t = node['top']
        return {'top': dict(t, a=canon(t['a']), p=float(t['p']))}
    if 'not' in node:
        inner = canon(node['not'])
        return inner['not'] if 'not' in inner else {'not': inner}
    for key in ('all', 'any'):
        if key in node:
            flat = []
            for n in node[key]:
                c = canon(n)
                flat += c[key] if key in c else [c]
            return flat[0] if len(set(map(_j, flat))) == 1 else {key: sorted({_j(c): c for c in flat}.values(), key=_j)}
    if 'cmp' in node:
        a, b, o = canon(node['a']), canon(node['b']), node['cmp']
        if o in ('lt', 'le'):
            a, b, o = b, a, {'lt': 'gt', 'le': 'ge'}[o]
        return {'cmp': o, 'a': a, 'b': b}
    op = node['op']
    out = {k: (canon(v) if k in ('a', 'b') else v) for k, v in node.items()}
    if op in COMMUTATIVE and _j(out['a']) > _j(out['b']):
        out['a'], out['b'] = out['b'], out['a']
    if op == 'neg' and out['a'].get('op') == 'neg':
        return out['a']['a']
    return out


# ── rendering to the DSL ─────────────────────────────────────────────────
def _num(x):
    return repr(float(x)) if not float(x).is_integer() else str(int(x))


def render(c) -> str:
    """DSL text for a CANONICAL node. Unresolved fit/top nodes cannot render."""
    if 'd' in c:
        return f'({c["d"]})'
    if 'lit' in c:
        return _num(c['lit'])
    if 'fit' in c:
        return f'fitted("{c["fit"]}")'
    if 'fit_pass' in c:
        return f'fitted_pass("{c["fit_pass"]}")'
    if 'fit_spec' in c or 'top' in c:
        _bad('UNRESOLVED_FIT', 'resolve() on a development window first')
    if 'not' in c:
        return f'(not {render(c["not"])})'
    for key in ('all', 'any'):
        if key in c:
            return '(' + f' {key.replace("all", "and").replace("any", "or")} '.join(render(n) for n in c[key]) + ')'
    if 'cmp' in c:
        return f'({render(c["a"])} {dict(gt=">", ge=">=")[c["cmp"]]} {render(c["b"])})'
    op, a = c['op'], (render(c['a']) if 'a' in c else None)
    if op in BINOPS:
        return f'({a} {BINOPS[op]} {render(c["b"])})'
    if op in ('min', 'max'):
        return f'{op}({a}, {render(c["b"])})'
    if op == 'neg':
        return f'(0 - {a})'
    if op == 'abs':
        return f'abs({a})'
    if op == 'lag':
        return f'prev({a}, {c["n"]})'
    if op == 'sma':
        return f'sma_of({a}, {c["n"]})'
    if op == 'std':
        return f'rolling_std({a}, {c["n"]})'
    if op in ('zscore', 'pct_rank'):
        return f'{op}({a}, {c["n"]})'
    if op == 'xrank':
        return f'xs_rank({a})'
    if op == 'corr':
        return f'rolling_corr({a}, {render(c["b"])}, {c["n"]})'
    if op == 'ref':
        return f'ref("{c["key"]}", {a})'
    if op == 'htf':
        return f'htf("{c["tf"]}", {a})'
    _bad('UNSUPPORTED_OP', op)


# ── candidate ────────────────────────────────────────────────────────────
REQUIRED = ('question', 'rationale', 'expected', 'failure_conditions', 'structure', 'tf')


STOP_KINDS, TARGET_KINDS, TRAIL_KINDS = ('atr', 'pct', 'swing'), ('atr', 'rr', 'pct', 'none'), ('none', 'atr')
MAX_HOLD_BARS = 5000


def _check_exit(e: dict, tf: str) -> dict:
    """Composable exit / holding / risk declaration -> a validated ExitSpec dict. ATR geometry is measured on the
    candidate's own timeframe by the engine; `signal_exit` is a condition node rendered to the DSL."""
    def num(x, what):
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not np.isfinite(x) or x <= 0:
            _bad('BAD_EXIT', what)
    stop, target = dict(e.get('stop') or {}), dict(e.get('target') or {'kind': 'none'})
    trail, time = dict(e.get('trail') or {'kind': 'none'}), dict(e.get('time') or {})
    if stop.get('kind') not in STOP_KINDS:
        _bad('BAD_EXIT', 'stop.kind')
    num(stop.get('mult', stop.get('v')), 'stop magnitude')
    if target.get('kind') not in TARGET_KINDS:
        _bad('BAD_EXIT', 'target.kind')
    if target['kind'] != 'none':
        num(target.get('v', target.get('mult')), 'target magnitude')
    if trail.get('kind') not in TRAIL_KINDS:
        _bad('BAD_EXIT', 'trail.kind')
    if trail['kind'] == 'atr':
        num(trail.get('mult'), 'trail mult')
    mb = time.get('max_bars')
    if not isinstance(mb, int) or isinstance(mb, bool) or not 1 <= mb <= MAX_HOLD_BARS:
        _bad('BAD_EXIT', 'time.max_bars (holding horizon) is required')
    sig = ''
    if e.get('signal_exit') not in (None, ''):
        if check(e['signal_exit'], tf) != 'bool':
            _bad('UNIT_MISMATCH', 'signal_exit must be a condition')
        sig = e['signal_exit']
    return dict(stop=stop, target=target, trail=trail, time=time, signal_exit=sig)


def make_candidate(spec: dict) -> dict:
    """Validate and identify a graph candidate. `spec` carries the proposal
    (question, rationale, expected relationship, failure conditions, data), the
    branches (`long`/`short` nodes; either may be None) and the structure."""
    miss = [k for k in REQUIRED if not spec.get(k)]
    if miss:
        _bad('PROPOSAL_INCOMPLETE', ','.join(miss))
    if spec['structure'] not in STRUCTURES:
        _bad('UNSUPPORTED_STRUCTURE', spec['structure'])
    from ...strategy.geometries import GEOS
    exit_decl = spec.get('exit')
    if exit_decl is not None:
        exit_decl = _check_exit(exit_decl, spec['tf'])
    elif spec.get('geo') not in GEOS:
        _bad('UNKNOWN_GEOMETRY', str(spec.get('geo')))
    tf = spec['tf']
    long_, short_ = spec.get('long'), spec.get('short')
    if spec['structure'] in ENTRY_STRUCTURES:
        want = dict(long_short=(True, True), long_only=(True, False), short_only=(False, True))[spec['structure']]
        if (long_ is not None, short_ is not None) != want:
            _bad('BRANCH_MISMATCH', f'{spec["structure"]} needs branches {want}')
    if spec['structure'] in HEDGE_STRUCTURES:
        from . import hedge
        if not spec.get('hedge') or (long_ is None and short_ is None):
            _bad('HEDGE_DECLARATION_MISSING', 'hedge spec and a primary entry branch are required')
        hedge.validate(spec['hedge'])
    for br in (long_, short_):
        if br is not None and check(br, tf) != 'bool':
            _bad('UNIT_MISMATCH', 'branch must be a condition')
    roles = dict(spec.get('roles') or {})
    for k, r in roles.items():
        if k not in catalog.BY_KEY or r not in ('feature', 'condition', 'exposure', 'stress'):
            _bad('BAD_ROLE', f'{k}:{r}')
        if catalog.BY_KEY[k].provider_gap:
            _bad('GAP_PROVIDER', k+':'+catalog.BY_KEY[k].provider_gap)
    for field in ('question','rationale','expected'):
        if not isinstance(spec[field],str) or not spec[field].strip() or len(spec[field])>8000:
            _bad('HYPOTHESIS_TEXT_INVALID',field)
    failure=spec['failure_conditions']
    if not (isinstance(failure,str) and failure.strip() or isinstance(failure,list) and 1<=len(failure)<=20
            and all(isinstance(x,str) and x.strip() for x in failure)) or len(_j(failure))>8000:
        _bad('HYPOTHESIS_TEXT_INVALID','failure_conditions')
    body = dict(schema=GRAPH_SCHEMA, question=spec['question'], rationale=spec['rationale'],
                expected=spec['expected'], failure_conditions=spec['failure_conditions'],
                structure=spec['structure'], tf=tf, geo=spec.get('geo') or 'custom', exit=exit_decl, long=long_, short=short_,
                roles=roles, target=spec.get('target', 'forward_return'),
                hedge=spec.get('hedge'), provenance=spec.get('provenance') or {},
                catalog_id=catalog.catalog_id())
    return dict(body, candidate_id=digest(dict(identity(body))))


def _exit_identity(e):
    if e is None:
        return None
    s = e.get('signal_exit')
    return dict(e, signal_exit=canon(s) if isinstance(s, dict) else s)


def identity(body: dict) -> dict:
    """Economic content only: provenance prose and roles are excluded so an
    alias or reworded rationale is the same candidate."""
    result = dict(schema=body['schema'], structure=body['structure'], tf=body['tf'], geo=body['geo'],
                target=body.get('target', 'forward_return'),
                long=None if body['long'] is None else canon(body['long']),
                short=None if body['short'] is None else canon(body['short']), hedge=body.get('hedge'),
                exit=_exit_identity(body.get('exit')))
    analysis = {k: v for k, v in (body.get('roles') or {}).items() if v in ('exposure', 'stress')}
    if analysis:
        result['analysis_roles'] = analysis   # these require actual data/experiments, not just descriptive prose
    return result


def verify(c: dict) -> dict:
    if c.get('schema') != GRAPH_SCHEMA or c.get('candidate_id') != digest(dict(identity(c))):
        raise ClosureRefused('candidate_integrity')
    return c


def is_graph(c: dict) -> bool:
    return c.get('schema') == GRAPH_SCHEMA


def unresolved(c: dict) -> bool:
    def has(n):
        if isinstance(n, dict):
            return 'fit_spec' in n or 'top' in n or any(has(v) for v in n.values())
        if isinstance(n, list):
            return any(has(v) for v in n)
        return False
    return has(c.get('long')) or has(c.get('short'))


def expressions(c: dict) -> tuple:
    """(long DSL, short DSL) of a RESOLVED candidate."""
    ex = lambda n: NEVER if n is None else render(canon(n))      # noqa: E731
    return ex(c['long']), ex(c['short'])


def artifacts_of(c: dict) -> list:
    ids = []

    def walk(n):
        if isinstance(n, dict):
            for k in ('fit', 'fit_pass'):
                if k in n:
                    ids.append(n[k])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(c.get('long')), walk(c.get('short'))
    return [fitted.get(i) for i in dict.fromkeys(ids)]


def realize(c: dict, evaluation_scope: str = '', trigger: str = '', *, entries_only: bool = False):
    """The existing Combination for a RESOLVED graph candidate."""
    from ..combo import Combination
    from ..vocab import Part
    verify(c)
    if unresolved(c):
        _bad('UNRESOLVED_FIT', 'candidate has unfitted nodes')
    if c['structure'] not in ENTRY_STRUCTURES and not (entries_only and c['structure'] in HEDGE_STRUCTURES):
        _bad('NOT_EXECUTABLE_AS_ENTRY_RULE', c['structure'])
    lg, sh = expressions(c)
    key = 'g:' + digest(dict(identity(c), arts=[a['artifact_id'] for a in artifacts_of(c)]))[:20]
    part = Part(key, 'state', key, lg, sh, tuple(artifacts_of(c)))
    ex = c.get('exit')
    exit_spec = None
    if ex is not None:
        sig = ex.get('signal_exit')
        exit_spec = dict(ex, signal_exit=render(canon(sig)) if isinstance(sig, dict) else (sig or ''))
    return Combination((part,), c['tf'], c['geo'], trigger=trigger or c['candidate_id'], round='closure',
                       evaluation_scope=evaluation_scope, exit_spec=exit_spec)


# ── development-window resolution (fit models, learn thresholds) ─────────
def _values(expr: str, ctx) -> np.ndarray:
    v = dsl.evaluate(dsl.parse(expr), ctx)
    return v.to_numpy(dtype=float, na_value=np.nan) if hasattr(v, 'to_numpy') else np.full(len(ctx.index), float(v))


def _ts_ms(ctx):
    from ...data import market_provenance as mp
    return np.asarray(mp.ms(ctx.df['ts']), dtype='int64')


def resolve(c: dict, ctxs: list, fit_window: list, *, min_samples: int = 200) -> tuple:
    """Fit every `fit_spec` by chronological walk-forward and learn every `top` threshold from the supplied
    development contexts ONLY. A model comes back as a SCHEDULE artifact: out-of-fold segments for the development
    window plus the final segment (trained on all development) valid from its end. Returns (resolved, fit record)."""
    from ...core.types import TF_MS
    verify(c)
    bar_ms = TF_MS[c['tf']]
    lo, hi = int(fit_window[0]), int(fit_window[1])
    record = dict(models=[], thresholds=[], walk_forward=[])

    def fit_node(spec, p=None, direction='above'):
        ins = [go(i) for i in spec['inputs']]
        exprs = [render(canon(i)) for i in ins]
        h = int(spec['horizon'])
        data = []
        for ctx in ctxs:
            y = models.forward_return(ctx.df['close'].to_numpy(dtype=float), h)
            data.append(dict(X=np.column_stack([_values(e, ctx) for e in exprs]),
                             y=-y if spec['side'] == 'short' else y, ts=_ts_ms(ctx)))
        art, rec = models.walk_forward(
            spec['model'], exprs, data, horizon=h, bar_ms=bar_ms, fit_start_ms=lo, dev_end_ms=hi,
            n_folds=int(spec.get('folds', 4)), grid=spec.get('grid'), alpha=float(spec.get('alpha', 1.0)),
            max_depth=int(spec.get('max_depth', 2)), min_leaf=int(spec.get('min_leaf', 50)), p=p, direction=direction,
            target=f'{spec["side"]}_forward_return_{h}')
        fitted.register_artifact(art)
        record['models'].append(dict(artifact_id=art['artifact_id'], model=spec['model'], side=spec['side'],
                                     n_folds=art['n_folds'], inputs=exprs, hyper=art['hyper'],
                                     n_fit_final=rec['folds'][-1]['n_train']))
        record['walk_forward'].append(dict(artifact_id=art['artifact_id'], **rec))
        return art['artifact_id']

    def go(n):
        if not isinstance(n, dict):
            return n
        if 'fit_spec' in n:
            return {'fit': fit_node(n['fit_spec'])}
        if 'top' in n:
            t = n['top']
            if isinstance(t['a'], dict) and 'fit_spec' in t['a']:
                return {'fit_pass': fit_node(t['a']['fit_spec'], p=float(t['p']), direction=t['dir'])}
            a = go(t['a'])
            vals = np.concatenate([_values(render(canon(a)), ctx) for ctx in ctxs])
            vals = vals[np.isfinite(vals)]
            if vals.size < min_samples:
                _bad('THRESHOLD_UNMEASURABLE', f'{vals.size} finite development samples')
            thr = round(float(np.quantile(vals, float(t['p']))), 8)
            record['thresholds'].append(dict(p=float(t['p']), dir=t['dir'], value=thr, n=int(vals.size)))
            # inclusive: a piecewise-constant score puts its quantile ON a plateau; a strict cut would select nothing
            return {'cmp': 'ge' if t['dir'] == 'above' else 'le', 'a': a, 'b': {'lit': thr}}
        return {k: ([go(x) for x in v] if isinstance(v, list) else go(v)) for k, v in n.items()}

    out = dict(c, long=go(c['long']) if c['long'] is not None else None,
               short=go(c['short']) if c['short'] is not None else None)
    resolved = dict(out, candidate_id=digest(dict(identity(out))))
    return resolved, record


def component_removals(c: dict) -> list:
    """Declared comparison (A9): the candidate with each top-level conjunct
    removed from the branch(es) that have more than one, for a RESOLVED graph."""
    out = []
    for branch in ('long', 'short'):
        n = c.get(branch)
        if n is None:
            continue
        cn = canon(n)
        if 'all' not in cn or len(cn['all']) < 2:
            continue
        for i in range(len(cn['all'])):
            rest = cn['all'][:i] + cn['all'][i + 1:]
            v = dict(c, **{branch: rest[0] if len(rest) == 1 else {'all': rest}})
            out.append((f'{branch}-{i}', dict(v, candidate_id=digest(dict(identity(v))))))
    return out


def requires_of(c: dict) -> tuple:
    """Data requirements DERIVED from the graph (catalog entries, references,
    fitted-model inputs), never declared by the proposer."""
    req = set()

    def walk(n):
        if isinstance(n, dict):
            if 'f' in n:
                req.update(catalog.BY_KEY[n['f']].requires)
            if n.get('op') == 'ref':
                req.add(f'ref:{n["key"]}')
            for k in ('fit', 'fit_pass'):
                if k in n:
                    req.update(fitted.requires_of(n[k]))
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(c.get('long')), walk(c.get('short'))
    walk((c.get('exit') or {}).get('signal_exit'))
    for key, role in (c.get('roles') or {}).items():
        if role in ('exposure', 'stress'):
            req.update(catalog.BY_KEY[key].requires)
    return tuple(sorted(req or {'ohlcv'}))
