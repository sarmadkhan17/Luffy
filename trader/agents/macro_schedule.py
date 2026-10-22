"""Daily macro calendar plus event-boundary jobs, independent of trading cycles.

This is an in-process scheduler, not an OS cron entry that could start another
Kernel. The persisted calendar rebuilds jobs after restart. All control effects
remain in Kernel._macro_step and the existing fenced Supervisor release.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import logging
import time

log = logging.getLogger(__name__)
LOCAL = ZoneInfo('Asia/Bahrain')


class MacroSchedule:
    def __init__(self, guard, apply, *, release_pending=lambda: False):
        self.guard = guard
        self.apply = apply
        self.release_pending = release_pending
        self.refresh_at = 0.0
        self.assess_at = 0.0
        self.last_now = None

    @staticmethod
    def next_midnight(now):
        local = datetime.fromtimestamp(now, LOCAL)
        return (local.replace(hour=0, minute=0, second=0, microsecond=0)
                + timedelta(days=1)).timestamp()

    def _refresh(self, now):
        g = self.guard
        if not g.enabled:
            return
        # On restart, reuse today's persisted calendar, including a successful
        # empty result. Missing/old evidence gets one startup acquisition.
        if not g._calendar:
            g._load_cached()
        fetched = g._cal_fetched
        same_day = (0 < fetched <= now and
                    datetime.fromtimestamp(fetched, LOCAL).date() ==
                    datetime.fromtimestamp(now, LOCAL).date())
        if not same_day:
            ttl = g._cal_ttl
            try:
                g._cal_ttl = 0  # midnight refresh must not reuse yesterday's TTL
                g._fetch_calendar()
            finally:
                g._cal_ttl = ttl

    def step(self):
        """Run due jobs once; return the next wall-clock deadline."""
        now = self.guard._now().timestamp()
        if self.last_now is not None and now < self.last_now:
            # Rebuild after a backwards clock change; future receipts cannot
            # establish a current event window.
            if self.guard._cal_fetched > now:
                self.guard._calendar = []
                self.guard._cal_fetched = 0.0
                self.guard._sources_ok = False
            self.refresh_at = self.assess_at = 0.0
        self.last_now = now
        if now >= self.refresh_at:
            self._refresh(now)
            self.refresh_at = self.next_midnight(now)
            self.assess_at = 0.0
        if now >= self.assess_at:
            state = self.guard.check(scheduled=True)
            self.apply(state)
            deadlines = [self.refresh_at]
            if self.guard.enabled:
                for event in self.guard._calendar:
                    at = event['when'].timestamp()
                    # check() includes the end instant, so assess just after it.
                    deadlines.extend(t for t in (
                        at - self.guard.pre_min * 60,
                        at + self.guard.post_min * 60 + .001) if t > now)
            # While a window is armed, reconcile owner/control changes locally.
            # A busy/refused release also retries without fetching the calendar.
            if state['active'] or self.release_pending():
                deadlines.append(now + 30)
            self.assess_at = min(deadlines)
        return min(self.refresh_at, self.assess_at)

    def run(self, *, stopped=lambda: False):
        while not stopped():
            try:
                deadline = self.step()
            except Exception:
                log.exception('macro scheduled job failed; retry in 30 seconds')
                deadline = time.time() + 30
            # The stop flag and clock are checked without doing calendar work.
            # Short sleeps also detect clock jumps rather than sleeping a day.
            while not stopped():
                now = self.guard._now().timestamp()
                if now >= deadline or (self.last_now is not None and now < self.last_now):
                    break
                time.sleep(min(1.0, deadline - now))
