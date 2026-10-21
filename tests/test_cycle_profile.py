import pstats
import pytest
from trader.observability import cycle_profile


def test_profile_preserves_result_and_records_calls(tmp_path):
    result = object()
    calls = []
    def work():
        calls.append(1)
        return result
    assert cycle_profile.profile_cycle(work, tmp_path) is result
    assert calls == [1]
    paths = list(tmp_path.glob('kernel-cycle-*.prof'))
    assert len(paths) == 1
    assert paths[0].stat().st_mode & 0o777 == 0o600
    assert any(key[2] == 'work' for key in pstats.Stats(str(paths[0])).stats)


def test_profile_preserves_callback_exception(tmp_path):
    failure = ValueError('callback failure')
    def work():
        raise failure
    with pytest.raises(ValueError) as caught:
        cycle_profile.profile_cycle(work, tmp_path)
    assert caught.value is failure
    assert len(list(tmp_path.glob('kernel-cycle-*.prof'))) == 1


@pytest.mark.parametrize('callback_fails', [False, True])
def test_diagnostic_write_failure_cannot_change_callback_outcome(tmp_path, monkeypatch, callback_fails):
    failure = ValueError('callback failure')
    calls = []
    def work():
        calls.append(1)
        if callback_fails:
            raise failure
        return 'result'
    def refuse(*args):
        raise OSError('unavailable diagnostic storage')
    monkeypatch.setattr(cycle_profile.os, 'replace', refuse)
    if callback_fails:
        with pytest.raises(ValueError) as caught:
            cycle_profile.profile_cycle(work, tmp_path)
        assert caught.value is failure
    else:
        assert cycle_profile.profile_cycle(work, tmp_path) == 'result'
    assert calls == [1]
    assert list(tmp_path.iterdir()) == []
