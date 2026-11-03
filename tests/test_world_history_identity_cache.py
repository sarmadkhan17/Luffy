"""An immutable context's memo identity must not reserialize its history."""
import hashlib
import pytest

from tests.test_wrld05_normal_context import retained, query, NOW  # noqa: F401
from trader.world import WorldHistory
from trader.world import WorldModelRecord
from trader.world.context import load_context


def test_cached_history_identity_is_exact_and_queries_stay_unchanged(retained, monkeypatch):
    path, _ = retained
    context = load_context(path)
    canonical = context.history.to_json()
    expected = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
    before = query(context)
    def no_serialization(self):
        pytest.fail('feature identity reserialized the entire historical World')
    monkeypatch.setattr(WorldHistory, 'to_json', no_serialization)
    for _ in range(20):
        assert context.history.history_id == expected
        assert context.context_id[0] == expected
        assert query(context) == before


def test_distinct_histories_do_not_share_an_identity(retained):
    path, _ = retained
    history = load_context(path).history
    prefix = WorldHistory((history.get_exact(NOW),))
    assert prefix.history_id != history.history_id
    assert WorldHistory.from_json(history.to_json()).history_id == history.history_id


def test_verified_record_is_reconstructed_once_but_changed_content_is_refused(retained, monkeypatch):
    from trader.world import replay
    path, _ = retained
    text = WorldModelRecord.from_model(load_context(path).history.get_exact(NOW)).to_json()
    calls = []
    original = replay.reconstruct_world_model
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(replay, 'reconstruct_world_model', counted)
    record = WorldModelRecord.from_json(text)
    assert len(calls) == 1
    for _ in range(5):
        assert record.reconstruct().as_of_ms == NOW
    history = WorldHistory((record,))
    assert history.get_exact(NOW).model_id == record.model_id
    assert len(calls) == 1
    # A low-level alteration must invalidate even the already verified cache.
    object.__setattr__(record, 'record_id', 'tampered')
    with pytest.raises(ValueError, match='record_id'):
        record.reconstruct()


def test_directly_constructed_invalid_record_never_uses_a_cache():
    record = WorldModelRecord('world.replay.record.v1', '{}', 'fake', (), 'fake')
    with pytest.raises(ValueError):
        record.reconstruct()
