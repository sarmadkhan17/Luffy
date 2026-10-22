from datetime import datetime
import threading
from zoneinfo import ZoneInfo

import pytest

from trader.scheduling.runtime import Calendar, Worker


class Clock:
    def __init__(self):
        self.mono = 0.
        self.wall = 1000.

    def advance(self, seconds):
        self.mono += seconds
        self.wall += seconds

    def worker(self, callback, **kwargs):
        return Worker('test', callback, monotonic=lambda: self.mono,
                      wall=lambda: self.wall, **kwargs)


def test_interval_is_not_completion_plus_interval():
    c = Clock()
    w = c.worker(lambda: c.advance(7), interval=30, immediate=True)
    assert w.run_once()
    c.advance(22)
    assert not w.run_once()
    c.advance(1)
    assert w.run_once()
    assert w.snapshot()['last_duration_s'] == 7


def test_event_during_callback_survives_and_coalesces():
    c = Clock()
    calls = []
    def work():
        calls.append(1)
        if len(calls) == 1:
            w.trigger()
            w.trigger()
            c.advance(4)
    w = c.worker(work, immediate=True)
    assert w.run_once()
    assert w.snapshot()['pending']
    assert w.run_once()
    assert not w.run_once()
    assert w.snapshot()['last_queue_wait_s'] == 4
    assert w.snapshot()['coalesced_events'] == 1


def test_long_work_exposes_missed_deadlines_without_replay_storm():
    c = Clock()
    w = c.worker(lambda: c.advance(95), interval=30, immediate=True)
    w.run_once()
    w.run_once()
    assert w.snapshot()['missed_deadlines'] == 2
    assert w.snapshot()['last_queue_wait_s'] == 65
    assert w.snapshot()['max_duration_s'] == 95


def test_failure_does_not_publish_success_or_error_body():
    c = Clock()
    def fail():
        raise ValueError('private response body')
    w = c.worker(fail, immediate=True)
    w.run_once()
    result = w.snapshot()
    assert result['failures'] == 1
    assert result['last_success_at'] is None
    assert result['last_error_type'] == 'ValueError'
    assert 'private' not in str(result)


def test_stop_waits_for_actual_callback_completion():
    entered, release = threading.Event(), threading.Event()
    def work():
        entered.set()
        assert release.wait(3)
    w = Worker('blocking', work, immediate=True)
    w.start()
    try:
        assert entered.wait(1)
        w.trigger()
        w.stop()
        assert not w.join(.01)
        assert not w.trigger()
    finally:
        release.set()
        assert w.join(2)
    assert w.snapshot()['runs'] == 1


def test_single_flight_across_concurrent_manual_drives():
    entered, release = threading.Event(), threading.Event()
    w = Worker('blocking', lambda: (entered.set(), release.wait(3)), immediate=True)
    t = threading.Thread(target=w.run_once)
    t.start()
    try:
        assert entered.wait(1)
        w.trigger()
        assert not w.run_once()
    finally:
        release.set()
        t.join(2)
    assert w.run_once()
    assert w.snapshot()['runs'] == 2


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=ZoneInfo('Asia/Bahrain')).timestamp()


def test_research_calendar_bahrain_boundaries():
    plan = Calendar((9, 21))
    assert plan.next_after(at(22, 8)) == at(22, 9)
    assert plan.next_after(at(22, 9)) == at(22, 21)
    assert plan.next_after(at(22, 22)) == at(23, 9)


def test_weekly_validation_sunday():
    plan = Calendar((3,), weekday=6)
    assert plan.next_after(at(22, 18)) == at(25, 3)
    assert plan.next_after(at(25, 3)) == datetime(2026, 11, 1, 3,
        tzinfo=ZoneInfo('Asia/Bahrain')).timestamp()


def test_delayed_calendar_coalesces_and_clock_reversal_does_not_duplicate():
    c = Clock()
    c.wall = at(22, 8)
    w = c.worker(lambda: None, calendar=Calendar((9, 21)))
    c.advance(15 * 3600)
    assert w.run_once()
    assert not w.run_once()
    c.wall = at(22, 8)
    assert not w.run_once()
    c.wall = at(23, 9)
    assert w.run_once()


@pytest.mark.parametrize('interval', [0, -1, float('nan'), float('inf')])
def test_reject_invalid_intervals(interval):
    with pytest.raises(ValueError):
        Worker('bad', lambda: None, interval=interval)
