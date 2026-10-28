"""Exposure constraints must survive actual fills, marks and direction changes."""
import numpy as np
import pytest

from trader.research.closure import ClosureRefused
from trader.research.compose import hedge as H
from tests.test_compose_hedge import spec


@pytest.mark.parametrize('constraint', [True, {}, {'min_net_exposure': 0},
    {'min_net_exposure': True}, {'min_net_exposure': float('nan')},
    {'min_net_exposure': 1.1}, {'min_net_exposure': 0.5, 'extra': 1}])
def test_invalid_constraints_are_refused_before_evaluation(constraint):
    with pytest.raises(ClosureRefused):
        spec(primary_exposure_constraint=constraint)


def replay(direction=1, *, ratio=0.5, missing=False):
    n = 8
    p, h = np.full(n, 100.), np.full(n, 50.)
    if missing:
        h[3] = np.nan
    return H.replay(spec(primary_exposure_constraint={'min_net_exposure': .4},
                        ratio={'method': 'fixed', 'value': ratio},
                        fee_bps=0., slip_bps=0.),
                    p, h, np.r_[direction, np.zeros(n-1, int)], np.zeros(n, bool),
                    equity=1000., opens=(p, h))


@pytest.mark.parametrize('direction', [1, -1])
def test_retained_exposure_measures_both_primary_directions(direction):
    measured = H.risk_metrics(replay(direction))
    assert measured['primary_active_bars'] > 0
    assert measured['minimum_retained_net_fraction'] == pytest.approx(.5)


def test_full_offset_is_measured_as_zero_retained_exposure():
    assert H.risk_metrics(replay(ratio=1.))['minimum_retained_net_fraction'] == pytest.approx(0.)


def test_unknown_held_mark_cannot_prove_exposure_feasibility():
    measured = H.risk_metrics(replay(missing=True))
    assert measured['primary_active_bars'] > 0
    assert measured['minimum_retained_net_fraction'] is None
    assert not measured['risk_complete']
