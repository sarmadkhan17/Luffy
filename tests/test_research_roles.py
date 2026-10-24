import numpy as np
import pandas as pd

from trader.research.compose import diagnostics as D, graph
from trader.strategy.features import FeatureCtx
from trader.strategy.portfolio_evidence import Fill


def test_explanation_and_stress_roles_measure_actual_trade_outcomes_without_changing_entries():
    n = 240
    price = 100 + np.arange(n) * .02 + 4 * np.sin(np.arange(n) / 7)
    frame = pd.DataFrame(dict(ts=pd.date_range('2024-01-01', periods=n, freq='4h', tz='UTC'),
                              open=price, high=price + 1, low=price - 1, close=price, volume=np.arange(n) + 10.))
    ctx = FeatureCtx({'4h': frame}, '4h', symbol='A')
    fills = [Fill(i, i + 2, float(np.sin(i / 5)), 'A') for i in range(60, 220, 5)]
    candidate = dict(roles={'ret6': 'exposure', 'rsi14': 'stress'})
    out = D.role_report(candidate, {'A': ctx}, {'A': {'fills': fills}})
    assert out['ret6']['trades'] == len(fills)
    assert out['rsi14']['role'] == 'stress' and out['rsi14']['upper_condition']['trades'] > 0
    assert all(v['qualified_claim'] is False for v in out.values())
    changed = D.role_report(candidate, {'A': ctx}, {'A': {'fills': [Fill(f.entry_i, f.exit_i, -f.r_multiple, 'A') for f in fills]}})
    assert changed['ret6']['association'] == -out['ret6']['association']
    assert changed['rsi14']['upper_condition']['mean_net_r'] == -out['rsi14']['upper_condition']['mean_net_r']


def test_analysis_role_dependencies_and_identity_are_executable_requirements():
    spec = dict(question='test', rationale='test', expected='test', failure_conditions='test', tf='4h', geo='fixed',
                structure='long_only', long={'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'lit': 50}}, short=None)
    entry = graph.make_candidate(spec)
    analysis = graph.make_candidate(dict(spec, roles={'ctx_cg_total': 'stress'}))
    assert analysis['candidate_id'] != entry['candidate_id']
    assert 'ref:cg_total_daily' in graph.requires_of(analysis)
    assert graph.expressions(analysis) == graph.expressions(entry)
