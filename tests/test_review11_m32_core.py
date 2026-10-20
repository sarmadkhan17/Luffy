"""Deterministic defect witnesses for frozen descriptive search; no search run.

These are passing characterizations of retained defects, NOT statistical
qualification. Frozen source remains unchanged pending a versioned protocol.
"""
from copy import deepcopy

from trader.cognition import m32_search as S


def rows():
    return [dict(row_id=str(i), symbol='BTC/USDT', dependence_group='one',
                 features=dict(direction=-1 if i == 0 else 1,
                               current_row_id='target-' + str(i)),
                 labels=dict(price_change_bps=10 + i)) for i in range(3)]


def test_frozen_continuation_shuffle_does_not_reach_scored_targets(monkeypatch):
    source = rows()
    lookup = {'target-' + str(i): dict(row_type='state', producer=str(i))
              for i in range(3)}
    targets = [S._target(r, 'continuation', lookup) for r in source]
    captured = []

    class Reverse:
        def __init__(self, seed):
            pass

        def shuffle(self, values):
            values.reverse()

    def metric(fake, matched, label, fake_lookup):
        captured.append([S._target(r, label, fake_lookup) for r in fake])
        # The null wrote the reversed targets into an unused field.
        assert [r['labels']['case_kind'] for r in fake] == targets[::-1]
        return {'signed_effect': 0.0}

    before = deepcopy(source)
    monkeypatch.setattr(S.random, 'Random', Reverse)
    monkeypatch.setattr(S, '_metric', metric)
    S._null_p(source, {'0'}, 'continuation', lookup, 'full', 1.0,
              draws=1, seed_text='review11-fixture')
    assert captured == [targets]
    assert captured[0] != targets[::-1]  # Required permutation never reaches metric.
    assert source == before


def test_frozen_persistence_identity_rotation_applies_direction_twice(monkeypatch):
    source = rows()
    targets = [S._target(r, 'persistence', {}) for r in source]
    captured = []

    class Identity:
        def __init__(self, seed):
            pass

        def randrange(self, size):
            return 0

    def metric(fake, matched, label, lookup):
        captured.append([S._target(r, label, lookup) for r in fake])
        return {'signed_effect': 0.0}

    before = deepcopy(source)
    monkeypatch.setattr(S.random, 'Random', Identity)
    monkeypatch.setattr(S, '_metric', metric)
    S._null_p(source, {'0'}, 'persistence', {}, 'full', 1.0,
              draws=1, seed_text='review11-fixture')
    assert targets == [-10.0, 11.0, 12.0]
    assert captured == [[10.0, 11.0, 12.0]]
    assert captured[0] != targets  # Identity must preserve the statistic's inputs.
    assert source == before


def test_frozen_v4_untouched_check_omits_deleted_v3_inventory(tmp_path, monkeypatch):
    """Current-directory enumeration cannot prove historical inventory completeness."""
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(root / 'scripts/m32_scheme_d_sharding_v4_20260923'))
    path = root / 'scripts/m32_scheme_d_qualification_v4_20260923/qualify_v4.py'
    spec = importlib.util.spec_from_file_location('review11_qualify_v4_witness', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    shard, bundle = tmp_path / 'sharding', tmp_path / 'bundle'
    shard.mkdir()
    bundle.mkdir()
    monkeypatch.setattr(module, 'V3_SHARDING_DIR', shard)
    monkeypatch.setattr(module, 'V3_BUNDLE_DIR', bundle)
    result = module.check_v3_untouched()
    assert result['status'] == 'PASS'
    assert result['detail']['files_checked'] == 0  # Historical files vanished.
