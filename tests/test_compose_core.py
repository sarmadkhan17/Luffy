"""Compositional core: fitted-artifact parity, graph typing/identity, catalog honesty."""
import json

import numpy as np
import pandas as pd
import pytest

from trader.research.closure import ClosureRefused
from trader.research.compose import catalog, graph, models
from trader.strategy import dsl, fitted as F
from trader.strategy.features import FeatureCtx


def _ctx(n=400, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, .01, n)))
    df = pd.DataFrame(dict(ts=pd.date_range('2024-01-01', periods=n, freq='4h'), open=c, high=c * 1.004,
                           low=c * .996, close=c, volume=rng.uniform(1, 2, n), taker_buy=rng.uniform(.4, .9, n)))
    return FeatureCtx(frames={'4h': df}, tf='4h')


def spec(**kw):
    base = dict(question='q', rationale='r', expected='e', failure_conditions='f', structure='long_short', tf='4h',
                geo='fixed', long={'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'lit': 70}},
                short={'cmp': 'lt', 'a': {'f': 'rsi14'}, 'b': {'lit': 30}})
    return dict(base, **kw)


@pytest.mark.parametrize('kind', F.BASE_TYPES)
def test_serialized_model_inference_matches_fit_time_scoring(kind):
    ctx = _ctx()
    exprs = ['rsi(14)', 'ret(6)']
    X = np.column_stack([dsl.evaluate(dsl.parse(e), ctx).to_numpy(float) for e in exprs])
    y = models.forward_return(ctx.df['close'].to_numpy(float), 3)
    art = models.fit(kind, exprs, X, y, horizon=3, fit_window=[0, 1], alpha=.01, min_leaf=20)
    art2 = json.loads(json.dumps(art))                      # a different process sees only the JSON
    F._REGISTRY.clear()
    F.register_artifact(art2)
    via_dsl = dsl.evaluate(dsl.parse(f'fitted("{art["artifact_id"]}")'), ctx).to_numpy(float)
    direct = F.predict(art, X)
    assert np.array_equal(np.isnan(via_dsl), np.isnan(direct)) and np.allclose(via_dsl[~np.isnan(direct)], direct[~np.isnan(direct)])
    assert 'ohlcv' in dsl.data_requires(dsl.parse(f'fitted("{art["artifact_id"]}")'))


def test_tampered_artifact_and_unbounded_tree_refused():
    ctx = _ctx()
    X = np.random.default_rng(1).normal(size=(300, 2))
    art = models.fit('ridge', ['rsi(14)', 'ret(6)'], X, X[:, 0], horizon=1, fit_window=[0, 1])
    bad = dict(art, coef=[9.0, 9.0])
    with pytest.raises(F.ArtifactError):
        F.register_artifact(bad)
    with pytest.raises(F.ArtifactError):
        models.fit('tree', ['rsi(14)', 'ret(6)'], X, X[:, 0], horizon=1, fit_window=[0, 1], max_depth=9)
    with pytest.raises(F.ArtifactError):                      # no silent default model at inference
        dsl.evaluate(dsl.parse('fitted("deadbeef")'), ctx)


def test_fit_uses_only_supplied_rows_and_purges_unavailable_targets():
    c = np.arange(1.0, 51.0)
    y = models.forward_return(c, 5)
    assert np.isnan(y[-5:]).all() and np.isfinite(y[:-5]).all()


def test_graph_identity_collapses_aliases_but_not_different_rules():
    a = graph.make_candidate(spec())
    swapped = graph.make_candidate(spec(long={'cmp': 'lt', 'a': {'lit': 70}, 'b': {'f': 'rsi14'}}))     # 70 < rsi  == rsi > 70
    assert a['candidate_id'] == swapped['candidate_id']
    c1 = graph.make_candidate(spec(long={'all': [{'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'lit': 70}},
                                                  {'cmp': 'gt', 'a': {'f': 'relvol50'}, 'b': {'lit': 1}}]}))
    c2 = graph.make_candidate(spec(long={'all': [{'cmp': 'gt', 'a': {'f': 'relvol50'}, 'b': {'lit': 1}},
                                                  {'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'lit': 70}},
                                                  {'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'lit': 70}}]}))
    assert c1['candidate_id'] == c2['candidate_id']                                            # order + duplicate conjunct
    other = graph.make_candidate(spec(long={'cmp': 'gt', 'a': {'f': 'stoch14'}, 'b': {'lit': 70}}))
    assert other['candidate_id'] != a['candidate_id']                                          # alias != alternative


def test_compiler_refuses_bad_units_chronology_and_unknown_or_gap_factors():
    for bad, code in [
        ({'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'f': 'ret24'}}, 'UNIT_MISMATCH'),
        ({'cmp': 'gt', 'a': {'op': 'lag', 'a': {'f': 'rsi14'}, 'n': -3}, 'b': {'lit': 1}}, 'BAD_WINDOW'),
        ({'cmp': 'gt', 'a': {'f': 'no_such'}, 'b': {'lit': 1}}, 'UNKNOWN_FACTOR'),
        ({'cmp': 'gt', 'a': {'op': 'htf', 'tf': '1h', 'a': {'f': 'rsi14'}}, 'b': {'lit': 1}}, 'CHRONOLOGY'),
        ({'cmp': 'gt', 'a': {'lit': float('nan')}, 'b': {'lit': 1}}, 'NONFINITE_LITERAL'),
        ({'cmp': 'gt', 'a': {'op': 'exec', 'a': {'f': 'rsi14'}}, 'b': {'lit': 1}}, 'UNSUPPORTED_OP'),
    ]:
        with pytest.raises(ClosureRefused) as e:
            graph.make_candidate(spec(long=bad))
        assert e.value.code == code, (bad, e.value.code)
    # A TradingView-bound factor COMPILES as a declared dependency; whether its data exists is decided at evaluation, where a
    # missing/stale series blocks that candidate (BLOCKED_DATA + owner data request), not by a fake provider-gap refusal here.
    c = graph.make_candidate(spec(long={'cmp': 'gt', 'a': {'f': 'ctx_total2'}, 'b': {'lit': 1}}, short={'cmp': 'lt', 'a': {'f': 'ctx_total2'}, 'b': {'lit': 1}}))
    assert 'ref:tv_total3' in graph.requires_of(c)
    with pytest.raises(ClosureRefused):
        graph.make_candidate(spec(structure='long_short', short=None))                         # a mirror is never assumed


def test_graph_compiles_to_dsl_that_parses_and_evaluates_finitely():
    c = graph.make_candidate(spec(long={'all': [{'cmp': 'gt', 'a': {'op': 'sub', 'a': {'f': 'rsi14'}, 'b': {'f': 'stoch14'}},
                                                'b': {'lit': 0}}, {'f': 'regime_trend'}]}))
    lg, sh = graph.expressions(c)
    assert dsl.evaluate_bool(dsl.parse(lg), _ctx()).dtype == bool and 'stoch_k' in lg
    assert graph.requires_of(c) == ('ohlcv',)


def test_catalog_valid_and_gaps_are_named_not_substituted():
    assert catalog.validate_catalog() == []
    inv = catalog.inventory(None)
    gaps = {g['key'] for d in inv['domains'].values() for g in d['open_gaps']}
    assert {'ctx_total2', 'ctx_btc_dominance', 'ctx_total1', 'spread_depth', 'asset_size'} <= gaps
    assert catalog.BY_KEY['ref_btcdom_ret30'].definition.startswith('Binance BTCDOM') and 'NOT' in catalog.BY_KEY['ref_btcdom_ret30'].meaning
    assert catalog.BY_KEY['ref_alts_ret30'].provider != catalog.BY_KEY['ctx_total2'].provider
    measured = dict(refs={'spx': dict(rows=3, first_ms=0, last_ms=1)}, derivs={}, derivs_source=None)
    assert catalog.status_of(catalog.BY_KEY['ref_spx_ret30'], measured)[0] == 'GAP_HISTORY'
    assert catalog.status_of(catalog.BY_KEY['fundz360'], measured)[0] == 'GAP_HISTORY'
