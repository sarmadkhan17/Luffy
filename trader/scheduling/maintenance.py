"""Off-cycle outcome, learning and reporting workers.

These jobs cannot submit orders. Learning applications run in a separate coordinated worker. Each dispatch worker exclusively owns its watermark.
"""
import threading
import time

from .runtime import Calendar, Worker


class Maintenance:
    def __init__(self, journal, cfg, feed):
        self.journal, self.cfg, self.feed = journal, cfg, feed
        self.apply_learning = lambda: []  # runtime installs its coordinated checkpoint
        self._vault_lock = threading.Lock()
        from ..learning.dispatch import Watermark
        self.watermark = Watermark()
        self.jobs = {
            'learning': Worker('learning', self.learning),
            'learning_apply': Worker('learning-apply', lambda: self.apply_learning(), interval=60),
            'learning_check': Worker('learning-check', self.learning_check, interval=60, immediate=True),
            'outcomes': Worker('outcomes', self.outcomes, interval=60, immediate=True),
            'reports': Worker('reports', self.reports, interval=3600),
            'daily_report': Worker('daily-report', self.daily_report,
                                   calendar=Calendar((0,), minute=10)),
            'postmortem': Worker('postmortem', self.postmortem, interval=6 * 3600),
        }

    def start(self):
        started = []
        try:
            for job in self.jobs.values():
                job.start()
                started.append(job)
        except BaseException:
            for job in started:
                job.stop()
            for job in started:
                job.join()
            raise

    def trade_closed(self):
        self.jobs['outcomes'].trigger()
        self.jobs['learning'].trigger()

    def outcomes(self):
        from ..engine.outcomes import resolve_pending
        from ..engine.outcome_backfill import upgrade_24h
        changed = resolve_pending(self.journal, self.feed, executed_only=True)
        symbols = {r['symbol'] for r in self.journal.query(
            'SELECT DISTINCT o.symbol FROM outcomes o JOIN decisions d ON d.id=o.decision_id '
            'WHERE o.correct_24h IS NULL AND d.executed=1')}
        frames = {}
        for symbol in sorted(symbols):
            frame = self.feed.fetch_ohlcv(symbol, '1h', limit=400)
            if frame is not None and len(frame):
                frames[symbol] = frame
        if frames:
            changed += upgrade_24h(self.journal, frames, executed_only=True)
        if changed:
            self.jobs['learning'].trigger()
        callback = getattr(self, 'after_outcomes', None)
        if callback is not None:
            callback()

    def learning_check(self):
        # Cheap recovery check: no payload expansion or historical anti-join.
        # A restart with registered rules recovers pending evidence. Without
        # rules there is no learning work, even if old evidence is retained.
        from ..learning.dispatch import production_registry, dispatchable, registry_digest
        rules = production_registry()
        if not any(dispatchable(rule) for rule in rules.values()):
            return
        if (self.watermark.digest != registry_digest(rules)
                or self.watermark._top(self.journal) > self.watermark.floor
                or self.watermark.swept_at is None
                or self.watermark.clock() - self.watermark.swept_at >= self.watermark.sweep_s):
            self.jobs['learning'].trigger()

    def learning(self):
        from ..learning.dispatch import dispatch_pending, production_registry, dispatchable
        if not any(dispatchable(rule) for rule in production_registry().values()):
            return
        rows = dispatch_pending(self.journal, self.cfg, at_ms=int(time.time() * 1000),
                                max_work=8, watermark=self.watermark)
        if len(rows) == 8:
            # Bounded batches, but don't wait a minute with known backlog.
            self.jobs['learning'].trigger()

    def reports(self):
        from ..knowledge.vault import Vault
        with self._vault_lock:
            vault = Vault(self.journal)
            vault.refresh_strategy_notes()
            vault.agent_ledger()
            vault.write_moc()

    def daily_report(self):
        from ..knowledge.vault import Vault
        with self._vault_lock:
            Vault(self.journal).daily_review()

    def postmortem(self):
        from ..knowledge.vault import Vault
        from ..brain.postmortem import run_postmortem
        with self._vault_lock:
            run_postmortem(self.journal, Vault(self.journal), cfg=self.cfg)

    def snapshot(self):
        return {name: job.snapshot() for name, job in self.jobs.items()}

    def stop(self):
        for job in self.jobs.values():
            job.stop()

    def join(self, timeout=None):
        end = None if timeout is None else time.monotonic() + timeout
        drained = True
        for job in self.jobs.values():
            remaining = None if end is None else max(0, end - time.monotonic())
            drained = job.join(remaining) and drained
        return drained
