"""End-to-end mechanisms through the REAL stores, DSL, evaluator and closure
development on SYNTHETIC context snapshots (planted structure only proves the
mechanism; nothing here is evidence of an edge)."""
import copy
import json

import numpy as np
import pytest

from tests import compose_fixtures as C
from trader.data import market_provenance as mp
from trader.research import job
from trader.research.closure import ClosureRefused, development as D, manifest, synthetic as S
from trader.research.compose import data as cdata, graph, search
from trader.strategy import fitted as F
from trader.strategy.compile import compile_spec


def spec(long, short, **kw):
    return dict(dict(question='q', rationale='r', expected='e', failure_conditions='f', structure='long_short',
                     tf='4h', geo='fixed', long=long, short=short), **kw)


def spx_rule(ref='spx', n=24):
    f = {'op': 'ref', 'key': ref, 'a': {'f': 'ret24'}}
    return spec({'cmp': 'gt', 'a': f, 'b': {'lit': 0}}, {'cmp': 'lt', 'a': f, 'b': {'lit': 0}})


@pytest.fixture(scope='module')
def spx(tmp_path_factory):
    return C.build_ctx(tmp_path_factory.mktemp('spx'), mode='spx', n_bars=1500)


def entries(b, cand, market, derivs=None, frames=None):
    frames, _ = manifest.development_frames(b.snap, b.env)
    bundle = D.build_bundle(frames, b.env, dict(market=market, derivs=derivs))
    c = graph.realize(cand, 'x')
    comp = compile_spec(c.to_spec())
    sym = b.names[0]
    kw = dict(btc=bundle.btc, derivs=bundle.derivs.get(sym), universe=bundle.universe, market=bundle.market, symbol=sym, world=None)
    lo, sh = comp.entries(bundle.sym_frames[sym], **kw)
    return np.asarray(lo, bool), np.asarray(sh, bool), bundle


def test_external_series_reaches_entries_and_absent_series_halts_not_nans(spx):
    cand = graph.make_candidate(spx_rule())
    ext = cdata.load_external(graph.requires_of(cand), spx.snap, None, spx.names[:6], int(spx.env['dev_end_ms']))
    assert not ext['gaps'] and ext['market']['spx'] is not None
    lo, sh, _ = entries(spx, cand, ext['market'])
    assert lo.sum() > 100 and sh.sum() > 100                              # the context decides actual entries
    lo0, sh0, _ = entries(spx, cand, None)
    assert lo0.sum() == 0 and sh0.sum() == 0                              # (what a silent fallback would look like)
    oil = graph.make_candidate(spx_rule('oil'))
    res = D.develop(oil, spx.snap, spx.env, draws=50)
    assert res['outcome'] == 'BLOCKED_DATA' and 'ref:oil' in res['reason'] and res['context_gaps']   # halts, never NaN-scored


def test_past_context_intervention_changes_later_decisions_and_future_changes_nothing_earlier(spx):
    cand = graph.make_candidate(spx_rule())
    ext = cdata.load_external(['ref:spx'], spx.snap, None, spx.names[:6], int(spx.env['dev_end_ms']))
    base_lo, base_sh, bundle = entries(spx, cand, ext['market'])
    mk = ext['market']['spx']
    cut = len(mk) // 2
    # (1) future-only change: rows after the cut are replaced; decisions before their availability are unchanged
    fut = {'spx': mk.copy()}
    fut['spx'].loc[cut:, 'close'] = fut['spx'].loc[cut:, 'close'].to_numpy()[::-1]
    lo_f, sh_f, _ = entries(spx, cand, fut)
    cut_ms = int(mp.ms(mk['ts'])[cut])
    t_ms = np.asarray(mp.ms(bundle.frames[spx.names[0]]['ts']))
    before = t_ms + S.TF_MS <= cut_ms                                      # bars that closed before the replaced rows existed
    assert before.sum() > 100 and np.array_equal(base_lo[before], lo_f[before]) and np.array_equal(base_sh[before], sh_f[before])
    assert not np.array_equal(base_lo[~before], lo_f[~before])             # ...but later decisions do respond
    # (2) past-input intervention: a shock to an early KNOWN value changes decisions after it
    pst = {'spx': mk.copy()}
    pst['spx'].loc[20:60, 'close'] = pst['spx'].loc[20:60, 'close'] * np.linspace(1.0, 0.5, 41)
    lo_p, _, _ = entries(spx, cand, pst)
    assert not np.array_equal(base_lo, lo_p)


def test_same_day_reference_value_is_not_visible_until_published(spx):
    """Daily reference bars are known one day after their stamp: changing day d's close cannot move any decision
    taken during day d (the late-publication mutation of N2)."""
    cand = graph.make_candidate(spx_rule())
    ext = cdata.load_external(['ref:spx'], spx.snap, None, spx.names[:6], int(spx.env['dev_end_ms']))
    base_lo, _, bundle = entries(spx, cand, ext['market'])
    mk = ext['market']['spx']
    d = 100
    mut = {'spx': mk.copy()}
    mut['spx'].loc[d, 'close'] = mk['close'].iloc[d] * 3.0
    lo_m, _, _ = entries(spx, cand, mut)
    stamp = int(mp.ms(mk['ts'])[d])
    t_ms = np.asarray(mp.ms(bundle.frames[spx.names[0]]['ts']))
    same_day = (t_ms + S.TF_MS < stamp + 86_400_000) & (t_ms >= stamp)   # bars closing strictly before publication
    assert same_day.sum() >= 5 and np.array_equal(base_lo[same_day], lo_m[same_day])


def fit_graph(model='ridge', inputs=None, p=0.9):
    ins = inputs or [{'op': 'ref', 'key': 'spx', 'a': {'f': 'ret24'}}, {'f': 'ret24'}]
    mk = lambda side: {'fit_spec': dict(model=model, side=side, horizon=6, inputs=ins, alpha=0.01, max_depth=2, min_leaf=100)}  # noqa: E731
    return spec({'top': {'a': mk('long'), 'p': p, 'dir': 'above'}}, {'top': {'a': mk('short'), 'p': p, 'dir': 'above'}})


def test_fitted_graph_serialized_artifact_parity_after_development(spx):
    cand = graph.make_candidate(fit_graph('ridge'))
    frames, _ = manifest.development_frames(spx.snap, spx.env)
    ext = cdata.load_external(graph.requires_of(cand), spx.snap, None, spx.names[:6], int(spx.env['dev_end_ms']))
    bundle = D.build_bundle(frames, spx.env, ext)
    resolved, fit = graph.resolve(cand, job._ctxs(bundle), spx.env['fit_window'])
    assert [m['model'] for m in fit['models']] == ['ridge', 'ridge'] and fit['models'][0]['n_fit_final'] > 1000
    combo = graph.realize(resolved, 'x', trigger=cand['candidate_id'])
    sym = spx.names[0]
    kw = dict(btc=bundle.btc, derivs=None, universe=bundle.universe, market=bundle.market, symbol=sym, world=None)
    lo1, sh1 = compile_spec(combo.to_spec()).entries(bundle.sym_frames[sym], **kw)
    # a fresh process sees only the serialized combination
    from trader.research.combo import Combination
    blob = json.loads(json.dumps(combo.as_dict()))
    F._REGISTRY.clear()
    lo2, sh2 = compile_spec(Combination.from_dict(blob).to_spec()).entries(bundle.sym_frames[sym], **kw)
    assert lo1.sum() > 0 and np.array_equal(lo1, lo2) and np.array_equal(sh1, sh2)
    spec_dict = Combination.from_dict(blob).to_spec().to_dict()
    assert spec_dict['provenance']['fitted_artifacts']                      # the spec a Factory version stores carries the model
    F._REGISTRY.clear()
    from trader.strategy.spec import StrategySpec
    restored = StrategySpec.from_dict(spec_dict)                            # runtime reload registers it again
    assert F.get(fit['models'][0]['artifact_id']) is not None and not StrategySpec.validate(restored)


def test_budget_dedup_and_restart_do_not_spend_twice(tmp_path):
    from trader.research.closure.store import Store
    from trader.research.compose import catalog
    st = Store(tmp_path / 's.db')
    inv = catalog.inventory(dict(refs={k: dict(rows=900, first_ms=0, last_ms=1) for k in ('spx', 'dxy', 'gold')},
                                 derivs={}, derivs_source=None))
    status = {r['key']: r['status'] for d in inv['domains'].values() for r in d['factors']}
    b = search.register_budget(st, 'S', dict(max_attempts=20, max_fits=4))
    first = search.plan(st, 'S', b, status, now_ms=1)
    n = len(st.all_once('attempts'))
    again = search.plan(st, 'S', b, status, now_ms=2)
    assert n == 20 and again['admitted'] == [] and len(st.all_once('attempts')) == n
    assert first['stopped'].startswith('BUDGET_EXHAUSTED') and first['spent']['fits'] <= 4
    with pytest.raises(ClosureRefused):
        search.register_budget(st, 'S', dict(max_attempts=200))              # budget cannot be widened after the fact
    doms = first['spent']['by_domain']
    assert 'market_context' in doms and 'price_structure' in doms            # no domain starved by the cheap one
    reqs = st.all_once('requirements')
    assert any(r['factor'] == 'ctx_total2' and r['status'] == 'GAP_HISTORY' for r in reqs)   # no measured rows stays a requirement
    assert any(r['factor'] == 'fundz360' for r in reqs)                      # no derivatives measured -> not searched


def test_exact_alias_dedups_but_correlated_alternatives_are_separate_with_evidence(spx):
    a = graph.make_candidate(spx_rule())
    b = graph.make_candidate(spec(a['long'], a['short'], rationale='reworded', question='another phrasing'))
    assert a['candidate_id'] == b['candidate_id']                           # exact: collapses
    frames, _ = manifest.development_frames(spx.snap, spx.env)
    bundle = D.build_bundle(frames, spx.env)
    ctxs = job._ctxs(bundle)
    mat = search.factor_matrix(['rsi14', 'stoch14', 'relvol50'], ctxs)
    pair = next(p for p in mat['pairs'] if {p['a'], p['b']} == {'rsi14', 'stoch14'})
    assert pair['numeric_corr_matched'] > 0.5 and mat['n_matched'] > 1000   # similar, measured on the matched sample
    r1 = graph.make_candidate(spec({'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'lit': 70}}, {'cmp': 'lt', 'a': {'f': 'rsi14'}, 'b': {'lit': 30}}))
    r2 = graph.make_candidate(spec({'cmp': 'gt', 'a': {'f': 'stoch14'}, 'b': {'lit': 80}}, {'cmp': 'lt', 'a': {'f': 'stoch14'}, 'b': {'lit': 20}}))
    assert r1['candidate_id'] != r2['candidate_id']                         # numerically similar is NOT identical
    ov = search.entry_overlap(graph.expressions(r1)[0], graph.expressions(r2)[0], ctxs)
    assert 0.0 < ov['jaccard'] < 1.0                                        # entry overlap is its own diagnostic
    assert search.signal_return_similarity([graph.expressions(r1), graph.expressions(r2)], ctxs) is not None
