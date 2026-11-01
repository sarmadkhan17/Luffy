"""A planted interaction can be measured without earning strategy admission.

The population mechanism is joint-only, but this finite synthetic sample is
not balanced. A simpler strategy can outperform the fitted interaction.
These are mechanism checks, never market evidence.
"""
import pytest

from tests import compose_fixtures as C
from trader.research.closure import development as D
from trader.research.compose import graph


def one(ref):
    f = {'op': 'ref', 'key': ref, 'a': {'f': 'ret24'}}
    return dict(question='q', rationale='r', expected='e', failure_conditions='f', structure='long_short', tf='4h',
                geo='fixed', long={'cmp': 'gt', 'a': f, 'b': {'lit': 0}}, short={'cmp': 'lt', 'a': f, 'b': {'lit': 0}})


def joint(model, product=False, p=0.7):
    a, b = ({'op': 'ref', 'key': 'spx', 'a': {'f': 'ret24'}}, {'op': 'ref', 'key': 'dxy', 'a': {'f': 'ret24'}})
    ins = [a, b] + ([{'op': 'mul', 'a': a, 'b': b}] if product else [])
    mk = lambda side: {'fit_spec': dict(model=model, side=side, horizon=6, inputs=ins, alpha=0.01, max_depth=3, min_leaf=100)}  # noqa: E731
    return dict(question='Does the joint state of equity and dollar momentum matter?', rationale='interaction track',
                expected='e', failure_conditions='f', structure='long_short', tf='4h', geo='fixed',
                long={'top': {'a': mk('long'), 'p': p, 'dir': 'above'}}, short={'top': {'a': mk('short'), 'p': p, 'dir': 'above'}})


@pytest.fixture(scope='module')
def xor(tmp_path_factory):
    return C.build_ctx(tmp_path_factory.mktemp('xor'), mode='xor')


def dev(b, spec):
    return D.develop(graph.make_candidate(spec), b.snap, b.env, draws=100)


def test_joint_effect_is_measured_but_rejected_when_a_simple_control_is_better(xor):
    alone = [dev(xor, one(r)) for r in ('spx', 'dxy')]
    assert all(r['outcome'] != 'DEVELOPMENT_POSITIVE' for r in alone), [r['reason'] for r in alone]
    both = dev(xor, joint('ridge', product=True))                            # interaction expression a*b
    assert both['metrics']['total_pct'] > 0 and both['metrics']['consistency_p'] <= xor.env['policy']['grow_max_p']
    assert both['outcome'] == 'NEGATIVE_ECONOMICS', both['reason']
    assert 'does not beat its own baseline' in both['reason']
    assert any(b['total_pct'] > both['metrics']['total_pct'] and not b['candidate_earns_place']
               for b in both['baselines'].values())
    assert both['graph_fit']['models'][0]['model'] == 'ridge' and both['graph_fit']['models'][0]['n_fit_final'] > 1000
    assert sorted(both['requires']) == ['ohlcv', 'ref:dxy', 'ref:spx']

# The fitted model must still use the declared interaction and point-in-time
# inputs. Positive returns alone cannot establish its value over a control.
