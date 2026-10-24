"""Real fixture subprocesses, no data/provider/venue paths."""
import os
import time
from trader.core.child import run_child


def _return(value):
    return value


def _sleep():
    time.sleep(60)


def _exit():
    os._exit(3)


def _large():
    return 'x' * 100000


def test_complete_owned_result_roundtrip():
    value = {3: (b'bytes', [False, None, 2.5])}
    r = run_child(_return, value, timeout_s=10)
    assert r.ok and r.terminated and r.exitcode == 0 and r.value == value


def test_cancel_reaps_child_without_reader_thread():
    start = time.monotonic()
    r = run_child(_sleep, timeout_s=20, stop_requested=lambda: time.monotonic() - start > .3)
    assert not r.ok and r.cancelled and r.terminated and time.monotonic() - start < 3


def test_timeout_and_mid_result_death_are_not_success():
    r = run_child(_sleep, timeout_s=.2)
    assert not r.ok and r.timed_out and r.terminated
    r = run_child(_exit, timeout_s=10)
    assert not r.ok and r.exitcode == 3 and r.terminated


def test_oversize_and_parent_decode_budget_checked():
    r = run_child(_large, timeout_s=10, max_result_bytes=1000)
    assert not r.ok and r.terminated
    r = run_child(_return, list(range(1000)), timeout_s=10, max_decode_bytes=10000)
    assert not r.ok and r.terminated


def test_array_dtype_allocation_checked_before_asarray(monkeypatch):
    import numpy as np
    from trader.core.child import _unpack
    import pytest
    monkeypatch.setattr(np, 'asarray', lambda *a, **k: pytest.fail('allocation happened before guard'))
    wire = {'t': 'array', 'dtype': '<U100000', 'shape': [1], 'v': {'t': 'list', 'v': ['']}}
    with pytest.raises(ValueError, match='allocation_budget'):
        _unpack(wire, [8000])


def test_numpy_complex_datetime_and_integer_keys_roundtrip():
    import numpy as np
    values = {7: np.array([1+2j, 3-4j]), 8: np.array(['2020-01-01', 'NaT'], dtype='datetime64[us]'),
              9: np.array([2, -3], dtype='timedelta64[us]')}
    r = run_child(_return, values, timeout_s=10)
    assert r.ok, r.error
    for key, value in values.items():
        assert r.value[key].dtype == value.dtype
        assert r.value[key].tobytes() == value.tobytes()
