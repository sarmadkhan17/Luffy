"""Heavy research work runs in a supervised child, never in the kernel.

The kernel's pandas work holds the GIL, so a CPU-heavy search thread would
slow the trade loop directly, and a crash in it would take trading down.
The child is spawned (no inherited SQLite handles or threads), niced, and
killed at its deadline.
"""
import os
import time

from trader.core.child import run_child


def _double(x):
    return 2 * x


def _boom():
    raise ValueError("search blew up")


def _sleep(s):
    time.sleep(s)
    return "late"


def _niceness():
    return os.nice(0)


def _hard_exit():
    os._exit(3)


def test_the_value_comes_back():
    r = run_child(_double, 21, timeout_s=60)
    assert r.ok is True and r.value == 42 and r.error == ""


def test_an_exception_is_reported_not_raised():
    r = run_child(_boom, timeout_s=60)
    assert r.ok is False
    assert "ValueError" in r.error and "search blew up" in r.error


def test_a_child_past_its_deadline_is_killed():
    t0 = time.monotonic()
    r = run_child(_sleep, 30, timeout_s=2)
    assert r.ok is False and r.timed_out is True
    assert time.monotonic() - t0 < 15


def test_the_child_runs_at_low_priority():
    r = run_child(_niceness, timeout_s=60, nice=19)
    assert r.ok is True and r.value >= 19


def test_a_child_that_dies_without_answering_is_a_failure():
    r = run_child(_hard_exit, timeout_s=60)
    assert r.ok is False and r.timed_out is False
    assert r.exitcode == 3
