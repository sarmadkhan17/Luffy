"""Single-flight workers with coalesced events and honest timing.

No trading capabilities are provided here. Callers own state isolation and
must drain these workers before releasing the kernel instance lock.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
import logging
import threading
import time
import traceback
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Calendar:
    hours: tuple[int, ...]
    minute: int = 0
    weekday: int | None = None  # Monday = 0
    timezone: str = 'Asia/Bahrain'

    def __post_init__(self):
        if not self.hours or any(h not in range(24) for h in self.hours):
            raise ValueError('invalid calendar hours')
        if self.minute not in range(60) or (self.weekday is not None and self.weekday not in range(7)):
            raise ValueError('invalid calendar boundary')
        ZoneInfo(self.timezone)

    def next_after(self, timestamp: float) -> float:
        local = datetime.fromtimestamp(timestamp, ZoneInfo(self.timezone))
        for offset in range(8):
            day = local.date() + timedelta(days=offset)
            if self.weekday is not None and day.weekday() != self.weekday:
                continue
            for hour in sorted(set(self.hours)):
                candidate = datetime(day.year, day.month, day.day, hour, self.minute,
                                     tzinfo=local.tzinfo).timestamp()
                if candidate > timestamp:
                    return candidate
        raise RuntimeError('calendar has no next boundary')


class Worker:
    """One callback at a time; at most one queued event, no catch-up storm.

    Interval deadlines use monotonic time. Calendar deadlines use wall time.
    A timer firing during a callback is coalesced, with missed deadlines counted.
    Exceptions count as failures, never successful completions. stop() prevents
    new work; join() reports whether the in-flight callback actually drained.
    """
    def __init__(self, name, callback, *, interval=None, calendar=None,
                 immediate=False, monotonic=time.monotonic, wall=time.time, align_to_wall=False):
        if interval is not None and (not math.isfinite(interval) or interval <= 0):
            raise ValueError('interval must be finite and positive')
        if interval is not None and calendar is not None:
            raise ValueError('choose interval or calendar')
        self.name, self.callback = name, callback
        self.interval, self.calendar = interval, calendar
        self._mono, self._wall = monotonic, wall
        self._condition = threading.Condition()
        self._stopping = False
        self._pending = self._mono() if immediate else None
        self._deadline = (self._mono() + interval if interval is not None else
                          calendar.next_after(self._wall()) if calendar else None)
        if align_to_wall and interval is not None:
            self._deadline = self._mono() + interval - self._wall() % interval
        self._thread = None
        self._running_mono = None
        self._metrics = dict(name=name, status='idle', runs=0, successes=0,
                             failures=0, coalesced_events=0, missed_deadlines=0,
                             last_duration_s=None, max_duration_s=None,
                             last_queue_wait_s=None, last_finished_at=None,
                             last_success_at=None, running_since=None,
                             last_error_type=None)

    def trigger(self):
        with self._condition:
            if self._stopping:
                return False
            if self._pending is None:
                self._pending = self._mono()
            else:
                self._metrics['coalesced_events'] += 1
            self._condition.notify_all()
            return True

    def _timer(self, now):
        clock = now if self.interval is not None else self._wall()
        if self._deadline is None or clock < self._deadline:
            return
        if self.interval is not None:
            count = int((clock - self._deadline) // self.interval) + 1
            due = self._deadline
            self._deadline += count * self.interval
            self._metrics['missed_deadlines'] += count - 1
        else:
            due = now - max(0, clock - self._deadline)
            self._deadline = self.calendar.next_after(clock)
        self._pending = due if self._pending is None else min(due, self._pending)

    def run_once(self):
        """Run one due callback; also supports deterministic clock-driven tests."""
        with self._condition:
            if self._stopping or self._metrics['status'] == 'running':
                return False
            start = self._mono()
            self._timer(start)
            if self._pending is None:
                return False
            queued, self._pending = self._pending, None
            self._running_mono = start
            self._metrics.update(status='running', running_since=self._wall(),
                                 last_queue_wait_s=max(0, start - queued))
            self._metrics['runs'] += 1
        error = None
        frames = []
        try:
            self.callback()
        except Exception as exc:
            # Only the exception class is published; arbitrary messages may
            # include response bodies or credentials owned by a callback.
            error = type(exc).__name__
            frames = [dict(file=f.filename, line=f.lineno, function=f.name)
                      for f in traceback.extract_tb(exc.__traceback__)[-4:]]
        finally:
            with self._condition:
                duration = max(0, self._mono() - start)
                self._running_mono = None
                self._metrics.update(status='failed' if error else 'idle',
                    running_since=None, last_duration_s=duration,
                    max_duration_s=max(duration, self._metrics['max_duration_s'] or 0),
                    last_finished_at=self._wall(), last_error_type=error, last_error_frames=frames)
                self._metrics['failures' if error else 'successes'] += 1
                if error is None:
                    self._metrics['last_success_at'] = self._wall()
                self._condition.notify_all()
            logging.getLogger('luffy.cycles').info(
                'cycle=%s duration_s=%.3f queue_wait_s=%.3f result=%s',
                self.name, duration, max(0, start-queued), error or 'completed')
        return True

    def snapshot(self):
        with self._condition:
            return {**self._metrics, 'pending': self._pending is not None,
                    'stopping': self._stopping,
                    'running_for_s': (max(0, self._mono() - self._running_mono)
                        if self._metrics['running_since'] is not None else None)}

    def start(self):
        with self._condition:
            if self._thread is not None or self._stopping:
                raise RuntimeError('worker already started or stopped')
            self._thread = threading.Thread(target=self._run, name='cycle-' + self.name,
                                            daemon=False)
            self._thread.start()

    def _run(self):
        while True:
            self.run_once()
            with self._condition:
                if self._stopping:
                    return
                # Bounded wall-clock recheck also handles a system clock jump.
                clock = self._mono() if self.interval is not None else self._wall()
                delay = max(0, self._deadline - clock) if self._deadline is not None else 1
                self._condition.wait(0 if self._pending is not None else min(1, delay))

    def stop(self):
        with self._condition:
            self._stopping = True
            self._condition.notify_all()

    def join(self, timeout=None):
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return self.snapshot()['status'] != 'running'
