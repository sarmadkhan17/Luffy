"""P2/P3a: executable families, real numerical development, typed outcomes,
bounded revision with identity change, and final-test feedback bar."""
import json

import pytest

from tests.closure_fixtures import build
from trader.research.closure import ClosureRefused, consumer, development as D, families as F, manifest
from trader.research.closure.store import Store


@pytest.fixture(scope='module')
def b(tmp_path_factory):
    return build(tmp_path_factory.mktemp('dev'), name='dev', edge=0.007)


def test_every_advertised_family_compiles_and_declares_a_numerical_comparison(b):
    frames, _ = manifest.development_frames(b.snap, b.env)
    bundle = D.build_bundle(frames, b.env)
    for fid in F.FAMILIES:
        cand = F.make_candidate(fid)
        gauges = D.fit_gauges(bundle, cand, b.env)
        c = F.realize(cand, gauges, b.env['envelope_id'])
        assert c.hash and F.baselines(c), fid                      # baseline comparison is part of the interface
        assert len(F.mutations(cand)) >= 3 and F.make_candidate(fid, cand['params']) == cand


def test_unsupported_shapes_are_visible_never_defaulted():
    from trader.cognition import predictive as P
    assert F.TRANSFORM_FAMILY.keys() == set(P.generatable())
    with pytest.raises(ClosureRefused, match='UNSUPPORTED_FAMILY'):
        F.make_candidate('exact_volume_path.v1')
    assert F.match_text('order flow autocorrelation predicts daily volatility decay') == ('NONE', ())
    assert F.match_text('volume expansion confirms a breakout')[0] == 'MATCHED'
    assert F.match_text('volume breakout with trend following')[0] == 'AMBIGUOUS'


def test_illegal_parameters_and_mutations_refused():
    c = F.make_candidate('trend_breakout.v1')
    with pytest.raises(ClosureRefused):
        F.make_candidate('trend_breakout.v1', dict(c['params'], channel=7))
    with pytest.raises(ClosureRefused):
        F.mutate(c, dict(kind='economic', slot='channel', **{'from': 50, 'to': 7}))
    child = F.mutate(c, F.mutations(c)[0])
    assert child['candidate_id'] != c['candidate_id']                    # economic change = new identity
    assert sum(1 for k in c['params'] if c['params'][k] != child['params'][k]) == 1


def test_development_runs_the_real_evaluator_and_caches_by_identity(b, monkeypatch):
    cand = F.make_candidate('trend_breakout.v1')
    r = D.develop(cand, b.snap, b.env, store=b.store, draws=20)
    assert r['stage'] == 'DEVELOPMENT' and r['outcome'] in D.OUTCOMES and not r['cached']
    assert r['data_mode'] == 'REPLAY' and r['policy_class'] == 'TEST_ONLY'
    assert r['metrics']['verdict'] in ('scored', 'untestable', 'empty') and r['metrics']['censored_positions'] >= 0
    assert r['baselines'] and 'candidate_earns_place' in next(iter(r['baselines'].values()))
    assert D.develop(cand, b.snap, b.env, store=b.store, draws=20)['cached'] is True
    key = D.cache_key(cand, b.env, r['data_id'])
    assert key != D.cache_key(cand, dict(b.env, cost_id='x'), r['data_id'])
    assert key != D.cache_key(cand, dict(b.env, evaluator_id='x'), r['data_id'])
    assert key != D.cache_key(cand, b.env, 'other-data')
    assert key != D.cache_key(F.mutate(cand, F.mutations(cand)[0]), b.env, r['data_id'])


def test_no_trade_crosses_the_development_boundary(b):
    frames, _ = manifest.development_frames(b.snap, b.env)
    bundle = D.build_bundle(frames, b.env)
    cand = F.make_candidate('trend_breakout.v1')
    c = F.realize(cand, {}, b.env['envelope_id'])
    runs = D.run_symbols(c, bundle)
    for sym, r in runs.items():
        assert all(f.exit_i < len(r['df']) for f in r['fills'])
        assert int(manifest._ms(r['df']['ts']).max()) + 14_400_000 <= b.env['dev_end_ms']
    assert sum(r['result'].trades for r in runs.values()) > 0


@pytest.mark.parametrize('res,base,want', [
    (dict(verdict='error', error='boom'), {}, 'CODE_FAILURE'),
    (dict(verdict='untested', context_gaps={'S0': ['x']}), {}, 'BLOCKED_DATA'),
    (dict(verdict='empty', testable=False, trades=0), {}, 'INCONCLUSIVE_POWER'),
    (dict(verdict='scored', testable=False, trades=30), {}, 'INCONCLUSIVE_POWER'),
    (dict(verdict='scored', testable=True, consistency_p=.9, portfolio=dict(total_pct=-3.0)), {}, 'NEGATIVE_ECONOMICS'),
    (dict(verdict='scored', testable=True, consistency_p=.01, portfolio=dict(total_pct=8.0)),
     {'x': dict(verdict='untestable')}, 'INCONCLUSIVE_POWER'),
    (dict(verdict='scored', testable=True, consistency_p=.01, portfolio=dict(total_pct=8.0)),
     {'x': dict(verdict='error', error='e')}, 'CODE_FAILURE'),
])
def test_outcome_taxonomy_separates_code_power_and_economics(res, base, want):
    assert D.classify(res, base)[0] == want


def _result(cid, outcome, stage='DEVELOPMENT', rid=None):
    return dict(stage=stage, candidate_id=cid, outcome=outcome, result_id=rid or f'r-{outcome}-{cid[:6]}')


def test_revision_is_bounded_idempotent_and_changes_identity(tmp_path):
    st = Store(tmp_path / 's')
    parent = F.make_candidate('trend_breakout.v1')
    st.put_once('candidates', parent['candidate_id'], parent)
    res = _result(parent['candidate_id'], 'NEGATIVE_ECONOMICS')
    item = consumer.revise(st, parent['candidate_id'], res, now_ms=1)
    assert item['status'] == 'COMPILED' and item['candidate_id'] != parent['candidate_id']
    prov = json.loads(item['payload'])['provenance']
    assert prov['parent_candidate_id'] == parent['candidate_id'] and prov['change'] in F.mutations(parent)
    again = consumer.revise(st, parent['candidate_id'], res, now_ms=2)           # duplicate delivery
    assert again['item_id'] == item['item_id'] and len(st.all_once('candidates')) == 2
    # budget: at most max_revisions economic children per root
    cur, seen = parent, {parent['candidate_id']}
    for i in range(10):
        it = consumer.revise(st, cur['candidate_id'], _result(cur['candidate_id'], 'NEGATIVE_ECONOMICS', rid=f'x{i}'),
                             now_ms=3 + i, max_revisions=2)
        if it['status'] != 'COMPILED':
            break
        cur = st.get_once('candidates', it['candidate_id'])
        assert cur['candidate_id'] not in seen
        seen.add(cur['candidate_id'])
    assert it['reason'] == 'REVISION_BUDGET_EXHAUSTED' and len(seen) <= 4


def test_power_data_and_code_failures_never_become_economic_revisions(tmp_path):
    st = Store(tmp_path / 's')
    c = F.make_candidate('trend_breakout.v1')
    st.put_once('candidates', c['candidate_id'], c)
    for outcome, status, reason in (('INCONCLUSIVE_POWER', 'WAITING_DATA', None), ('BLOCKED_DATA', 'WAITING_DATA', None),
                                    ('CODE_FAILURE', 'BLOCKED', 'NEEDS_CODE_REPAIR'),
                                    ('DEVELOPMENT_POSITIVE', 'NO_CHANGE_JUSTIFIED', None)):
        it = consumer.revise(st, c['candidate_id'], _result(c['candidate_id'], outcome), now_ms=1)
        assert (it['status'], it['candidate_id']) == (status, None)
        if reason:
            assert it['reason'] == reason
    assert len(st.all_once('candidates')) == 1


def test_final_test_feedback_is_barred_from_development(tmp_path):
    st = Store(tmp_path / 's')
    c = F.make_candidate('trend_breakout.v1')
    st.put_once('candidates', c['candidate_id'], c)
    for stage in ('QUALIFICATION', 'PROTECTED', None):
        with pytest.raises(ClosureRefused, match='final_test_feedback_barred'):
            consumer.revise(st, c['candidate_id'], _result(c['candidate_id'], 'NEGATIVE_ECONOMICS', stage=stage), now_ms=1)


def test_protected_mutation_leaves_real_development_result_identical(b, tmp_path):
    import shutil
    import sqlite3
    from trader.research.closure import synthetic as S
    mutated = tmp_path / 'm.db'
    shutil.copy(b.snap, mutated)
    with sqlite3.connect(mutated) as db:
        db.execute('UPDATE candles SET close=close*2 WHERE ts>=?', (S.EPOCH_MS + 1200 * S.TF_MS,))
        db.execute('DELETE FROM market_revisions WHERE event_ms>=?', (S.EPOCH_MS + 1200 * S.TF_MS,))
    cand = F.make_candidate('trend_breakout.v1')
    r1 = D.develop(cand, b.snap, b.env, draws=20)
    r2 = D.develop(cand, mutated, b.env, draws=20)
    assert r1['result_id'] == r2['result_id'] and r1['metrics'] == r2['metrics']
