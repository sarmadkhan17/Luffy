"""The qualifier REFUSES a held-out (protected) window that overlaps discovery or selection.

Mechanism under test (existing, unmodified): the protected window must carry a forward seal that
predates its data, and any development/selection or earlier-look exposure mark that overlaps it is
re-derived at qualification time and blocks the look BEFORE a held-out price is read.

Scope note, stated plainly: this covers the compositional (closure) qualifier. The legacy
`research.referee.examine` path fixes B at the discovery cut / first-look cut; it has no per-candidate
selection-date check. That gap is tracked in docs/protocol-changes-20261102.md (Task 3.1), not here.
"""
import pytest

from tests.closure_fixtures import build, fixture_freeze, scripted_referee
from trader.research import referee as rf
from trader.research.closure import exposure, manifest, qualification as Q


def _qualify(tmp_path, mark=None):
    b = build(tmp_path, name='ov', seal='FORWARD')
    assert b.env['protected_status'] == 'ELIGIBLE', 'precondition: the window is clean before any exposure'
    split = b.env['split']
    if mark:
        state, start, end, reason = mark(split)
        exposure.mark(b.store, state, start, end, at_ms=5, reason=reason, symbol=b.names[0])
    calls = []
    packet = Q.qualify(b.store, fixture_freeze(b), b.env, b.snap, tmp_path / 'w', now_ms=1,
                       run=scripted_referee(True, calls))
    return b, packet, calls


def test_a_clean_window_is_not_refused_by_the_exposure_check(tmp_path):
    """Control: the refusal below is caused by the overlap, not by the fixture."""
    _b, packet, _calls = _qualify(tmp_path)
    assert 'protected_exposure' not in packet.get('reason', '')


@pytest.mark.parametrize('state,reason', [
    ('DEVELOPMENT_EXPOSED', 'selection used these bars'),
    ('DEVELOPMENT_EXPOSED', 'discovery fitted on these bars'),
    ('PROTECTED_EXPOSED', 'an earlier look already read these bars'),
])
def test_overlap_with_discovery_selection_or_an_earlier_look_is_refused_before_any_read(tmp_path, state, reason):
    b, packet, calls = _qualify(
        tmp_path, mark=lambda s: (state, s['start_ms'], s['start_ms'] + 1, reason))      # a single overlapping ms
    assert packet['status'] == 'BLOCKED'
    assert f'protected_exposure_{state.lower()}:{b.names[0]}' in packet['reason']
    assert calls == [], 'the referee reading must not run: no held-out price may be read'
    assert not (tmp_path / 'w' / 'qualification.db').exists(), 'no look may be recorded or spent'


def test_overlap_deep_inside_the_window_is_refused_too(tmp_path):
    b, packet, calls = _qualify(
        tmp_path, mark=lambda s: ('DEVELOPMENT_EXPOSED', s['cut_ms'], s['cut_ms'] + 1, 'selection inside held-out B'))
    assert packet['status'] == 'BLOCKED' and 'protected_exposure_development_exposed' in packet['reason']
    assert calls == []


def test_the_boundary_is_exact_an_exposure_that_ends_before_the_window_does_not_overlap():
    seal = dict(type='SEAL', kind='FORWARD_SEAL', symbol='*', start_ms=0, end_ms=10_000, at_ms=500)
    start, end = 1000, 2000
    before = dict(type='MARK', symbol='A', start_ms=100, end_ms=start - 1, state='DEVELOPMENT_EXPOSED')
    touching = dict(type='MARK', symbol='A', start_ms=100, end_ms=start, state='DEVELOPMENT_EXPOSED')
    assert manifest.exposure_state([seal, before], 'A', start, end, first_available_ms=600)[0] == 'UNEXPOSED'
    assert manifest.exposure_state([seal, touching], 'A', start, end, first_available_ms=600)[0] == 'DEVELOPMENT_EXPOSED'


def test_a_seal_written_after_the_data_existed_proves_nothing():
    late_seal = dict(type='SEAL', kind='FORWARD_SEAL', symbol='*', start_ms=0, end_ms=10_000, at_ms=500)
    assert manifest.exposure_state([late_seal], 'A', 1000, 2000, first_available_ms=400)[0] == 'UNKNOWN'


def test_a_configured_cut_that_differs_from_the_registered_split_is_refused():
    cfg = {'research': {'predictive_split': {'cut_ms': 2_000, 'start_ms': 0, 'end_ms': 9_000}}}
    with pytest.raises(ValueError, match='protected_cut_differs'):
        rf.load_heldout('4h', 'b', ['A/USDT'], 1_000, cfg)
