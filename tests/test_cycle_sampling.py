import json
import threading
import time
import pytest
from trader.observability import cycle_sampling


def test_samples_only_invoking_thread_and_preserves_result(tmp_path):
    stop = threading.Event()
    def unrelated_background():
        stop.wait()
    worker = threading.Thread(target=unrelated_background)
    worker.start()
    result = object()
    def work():
        time.sleep(.08)
        return result
    try:
        assert cycle_sampling.sample_cycle(work, tmp_path, interval=.001) is result
    finally:
        stop.set()
        worker.join()
    path, = tmp_path.glob('*.samples.json')
    data = json.loads(path.read_text())
    assert data['complete'] and data['samples'] > 0
    assert path.stat().st_mode & 0o777 == 0o600
    frames = [f[2] for s in data['stacks'] for f in s['frames']]
    assert 'work' in frames
    assert 'unrelated_background' not in frames


@pytest.mark.parametrize('failure_kind', ['write', 'thread'])
@pytest.mark.parametrize('callback_fails', [False, True])
def test_diagnostics_cannot_replace_callback_outcome(tmp_path, monkeypatch, failure_kind, callback_fails):
    failure = ValueError('callback failure')
    calls = []
    def work():
        calls.append(1)
        if callback_fails:
            raise failure
        return 'ok'
    def refuse(*args):
        raise OSError('diagnostic unavailable')
    if failure_kind == 'write':
        monkeypatch.setattr(cycle_sampling.os, 'replace', refuse)
    else:
        monkeypatch.setattr(cycle_sampling.threading.Thread, 'start', refuse)
    if callback_fails:
        with pytest.raises(ValueError) as caught:
            cycle_sampling.sample_cycle(work, tmp_path)
        assert caught.value is failure
    else:
        assert cycle_sampling.sample_cycle(work, tmp_path) == 'ok'
    assert calls == [1]
    assert list(tmp_path.iterdir()) == []
