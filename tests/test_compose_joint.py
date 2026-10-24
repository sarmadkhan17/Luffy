"""A4 / P2 / N4: a planted joint-only effect (drift +e when the S&P and dollar 30-day signs AGREE, -e otherwise;
each context alone has zero marginal effect). SYNTHETIC mechanism proof, not market evidence."""
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


def test_weak_standalone_components_do_not_pass_but_the_joint_interaction_model_does(xor):
    alone = [dev(xor, one(r)) for r in ('spx', 'dxy')]
    assert all(r['outcome'] != 'DEVELOPMENT_POSITIVE' for r in alone), [r['reason'] for r in alone]
    both = dev(xor, joint('ridge', product=True))                            # interaction expression a*b
    assert both['outcome'] == 'DEVELOPMENT_POSITIVE', both['reason']
    assert both['graph_fit']['models'][0]['model'] == 'ridge' and both['graph_fit']['models'][0]['n_fit_final'] > 1000
    assert sorted(both['requires']) == ['ohlcv', 'ref:dxy', 'ref:spx']

# NOTE: a plain ridge over (spx, dxy) ALSO scored DEVELOPMENT_POSITIVE on this fixture (measured, not asserted away): a
# linear score's top-quantile corner selects half of the parity structure. The fixture therefore proves that joint-only
# effects are reachable, not that only one model class can reach them.

