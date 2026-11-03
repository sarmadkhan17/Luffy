"""Independent kernel cycles, with one serialized venue-mutation boundary."""
from collections import deque
from functools import wraps
from contextlib import nullcontext
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import threading
import time

from .runtime import Calendar, Worker
from .timing import StageTimings, timed_worker
from .coordination import analysis_context, model_snapshot, input_fingerprint
from .maintenance import Maintenance

log = logging.getLogger('luffy.cycles')


def _read_batch(fn):
    @wraps(fn)
    def run(self, *args, **kwargs):
        feed = self.kernel.feed
        context = feed.immutable_read_batch() if hasattr(feed, 'immutable_read_batch') else nullcontext()
        with context:
            return fn(self, *args, **kwargs)
    return run


class KernelRuntime:
    def __init__(self, kernel):
        self.kernel = kernel
        self.stage_timings = StageTimings()
        self.execution_lock = threading.RLock()
        self.decision_lock = threading.RLock()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.model = (0, model_snapshot(kernel))
        self.pending_scan = None
        self.pending_portfolio = None
        self.holdings_market = {}
        self.scan_replaced = 0
        self.scan_in_flight = False
        self.scan_busy_ticks = 0
        self.account_data = None
        self.positions_at = None
        self.last_decisions = {}
        self.seen_inputs = {}
        self.derivative_inputs = ({}, {}, {})
        self.candle_pending = {}
        self.candle_boundaries = {}
        self.candle_stats = {}
        self.candle_events = {}
        self.held_symbols = set()
        self.shortlisted = set()
        self.derivative_needs = []
        self.candle_active = {}
        self.history_resume_index = 0
        self.alts_cold_status = None
        self.history_yields = 0
        self.need_stats = {}
        self.scans_completed = 0
        self.scans_discarded = 0
        self.last_full_cycle = None
        self.last_decision_stats = None
        self.last_scan_completed_at = None
        self.execution_metrics = dict(status="idle", runs=0, submitted=0, failures=0,
            last_duration_s=None, max_duration_s=None, last_error_type=None)
        self.maintenance = Maintenance(kernel.journal, kernel.cfg, kernel.feed)
        self.maintenance.apply_learning = self.apply_learning
        self.maintenance.after_outcomes = lambda: analysis_context(self)._record_excursions()
        self.jobs = {
            'commands': Worker('commands', self.commands, interval=1, immediate=True),
            'account': Worker('account', self.account, interval=30, immediate=True),
            'positions': Worker('positions', self.positions, interval=30, immediate=True),
            'market': Worker('market', self.market, interval=60, immediate=True),
            'decisions': Worker('decisions', self.decisions),
            'portfolio': Worker('portfolio', self.portfolio),
            'candles': Worker('candles', self.candles, interval=15, immediate=True, align_to_wall=True),
            'strategy_health': Worker('strategy-health', self.strategy_health, interval=3600),
            'research': Worker('research', self.research, calendar=Calendar((9, 21))),
            'validation': Worker('validation', self.validation, calendar=Calendar((3,), weekday=6)),
            'health': Worker('health', self.health, interval=10, immediate=True),
            'input_metadata': Worker('input-metadata', self.input_metadata, interval=30, immediate=True),
            'input_warm': Worker('input-warm', self.input_warm, interval=2, immediate=True),
            **self.maintenance.jobs,
        }
        cfg = kernel.cfg
        if cfg.get('derivatives', {}).get('enabled', True):
            self.jobs['derivatives'] = Worker('derivatives', self.derivatives, interval=900, immediate=False)
            self.jobs['derivative_needs'] = Worker('derivative-needs', self.derivative_current_needs,
                                                   interval=60, immediate=False)
            self.jobs['derivative_inputs'] = Worker('derivative-inputs', self.refresh_derivative_inputs,
                                                   interval=300, immediate=True)
            if cfg.get('derivatives', {}).get('backfill_on_start', True):
                self.jobs['derivative_history'] = Worker('derivative-history', self.derivative_history,
                                                         calendar=Calendar((3,), minute=30))
        if cfg.get('references', {}).get('enabled', True):
            self.jobs['references'] = Worker('references', self.references, interval=3600, immediate=True)
            self.jobs['alts_history'] = Worker('alts-history', self.alts_history, interval=300, immediate=True)
        if cfg.get('scraper', {}).get('enabled', False):
            self.jobs['scraper'] = Worker('scraper', self.scraper, interval=4*3600)
        if cfg.get('crawler', {}).get('enabled', False):
            self.jobs['crawler'] = Worker('crawler', self.crawler, interval=6*3600)
        if cfg.get('rent', {}).get('weekly_usdt'):
            self.jobs['rent'] = Worker('rent', self.rent, interval=3600)

    def start(self):
        k = self.kernel
        k._split_runtime = self
        k._maintenance = self.maintenance
        k._paper_executor()
        k.journal.on_trade_closed = self.trade_closed
        k.feed.on_candle_acquired = self.candle_acquired
        started = []
        try:
            for worker in self.jobs.values():
                if k._stop:
                    break
                worker.start()
                started.append(worker)
        except BaseException:
            self.stop()
            for worker in started:
                worker.join()
            raise

    def stop(self):
        self.stop_event.set()
        for worker in self.jobs.values():
            worker.stop()

    def join(self):
        for worker in self.jobs.values():
            worker.join()
        self.kernel.journal.on_trade_closed = None
        self.kernel.feed.on_candle_acquired = None
        self.publish()

    def trade_closed(self):
        self.maintenance.trade_closed()
        self.fill_event()
        self.jobs['strategy_health'].trigger()

    def fill_event(self):
        self.jobs['account'].trigger()
        self.jobs['positions'].trigger()

    def owner_event(self):
        self.jobs['commands'].trigger()

    def commands(self):
        with self.execution_lock:
            self.kernel._drain_panic()
            self.kernel._drain_close_requests()

    def account(self):
        k = self.kernel
        from ..core.types import ControlState, MarketType
        with self.execution_lock:
            k.supervisor.cycle()
            k.state_machine.refresh()
            balance, status = k._risk_step()
            state = k.state_machine.refresh()
            from ..observability.safety import entry_refusal
            allowed = (state == ControlState.ACTIVE and entry_refusal(k.journal) is None
                and status.get('risk_state') == 'ok'
                and (status.get('daily_pnl_pct') or 0) > -k.risk.daily_loss_block * 100
                and not k.executor.recovery_pending())
            k._record_risk_assessment(state, status, allowed, '' if allowed else 'account_cycle_entry_blocked')
            if k.market_type == MarketType.FUTURES and state != ControlState.HALTED:
                k.executor.recover_entries()
            with self.lock:
                self.account_data = (time.monotonic(), balance, deepcopy(status),
                    {name: deepcopy(getattr(k, name, None)) for name in
                     ('_account_obs', '_balance_read', '_fallback_row', '_risk_attempt_at')})
            k.journal.log_equity(status['equity'], balance, len(k.journal.open_trades()),
                                 provenance=k._equity_provenance(status['equity']))

    def account_snapshot(self, view=None):
        with self.lock:
            data = deepcopy(self.account_data)
        if data is None or time.monotonic() - data[0] > 60:
            raise ValueError('scheduled_account_unavailable_or_stale')
        if view is not None and len(data) > 3:
            for name, value in data[3].items():
                setattr(view, name, value)
        return data[1], data[2]

    @_read_batch
    def positions(self):
        view = analysis_context(self)
        required = view._exposure_symbols()
        with self.lock:
            self.held_symbols = set(required)
        if required:
            view._refresh_btc_context()
            peers = view._peer_frames(tuple(required))
            count = view._service_exposure(set(), peers)
            if count < len(required) or getattr(view, '_exposure_errors', []):
                raise ValueError('scheduled_exposure_incomplete')
        with self.lock:
            self.holdings_market = dict(view._portfolio_holdings_market)
            self.positions_at = time.monotonic()

    @timed_worker('market')
    @_read_batch
    def market(self):
        # Selection, preparation, queueing and evaluation share one reservation.
        # Timer/model events during that cycle never create another batch.
        with self.lock:
            if self.scan_in_flight or self.stop_event.is_set():
                self.scan_busy_ticks += int(self.scan_in_flight)
                return
            self.scan_in_flight = True
        cycle_started = time.monotonic()
        try:
            view = analysis_context(self)
            from .timing import stage_scope
            view._lean_scan = True
            with stage_scope("scan.shortlist"):
                receipt, frames = view._admission_plan()
            with self.lock:
                self.shortlisted = set(getattr(getattr(view, '_symbol_roles', None), 'deep', ()) or ())
            from ..data import market_provenance as mp
            with self.lock:
                held = set(self.held_symbols)
            # Pin held positions and the current shortlist's windows against eviction.
            mp.CURRENT.set_pins(symbols=held | set(self.shortlisted))
            from .lean_scan import prepare
            from ..data import market_provenance as mp
            with mp.cache_only():
                peers = prepare(view)
            view._prepared_scan = (receipt, frames, peers, {}, {})
            view._prepared_at = time.monotonic()
            view._cycle_started = cycle_started
            with self.lock:
                self.pending_scan = view
            if not self.jobs['decisions'].trigger():
                with self.lock:
                    self.pending_scan = None
                    self.scan_in_flight = False
        except BaseException:
            with self.lock:
                self.pending_scan = None
                self.scan_in_flight = False
            raise

    @timed_worker('decisions')
    @_read_batch
    def decisions(self):
        with self.lock:
            view, self.pending_scan = self.pending_scan, None
        if view is None:
            return
        start = time.monotonic()
        try:
            with self.decision_lock:
                if view._model_generation != self.model[0] or time.monotonic() - view._prepared_at > 60:
                    with self.lock:
                        self.scans_discarded += 1
                    self.jobs['market'].trigger()
                    return
                from ..data import market_provenance as mp
                with mp.cache_only():
                    stats = view.cycle()
            end = time.monotonic()
            with self.lock:
                self.last_decision_stats = stats
                self.last_scan_completed_at = None if stats.get('interrupted') else time.time()
                # Only a finished evaluation is a completed scan; skipped busy
                # ticks and discarded batches are counted separately.
                self.scans_completed += 1
                begun = getattr(view, '_cycle_started', start)
                self.last_full_cycle = dict(preparation_s=view._prepared_at - begun,
                    queue_s=start - view._prepared_at, evaluation_s=end - start, total_s=end - begun)
            self.kernel._last_cycle_s = end - start
        finally:
            with self.lock:
                self.scan_in_flight = False

    def apply_learning(self):
        # Model updates are independent of trading scans but share their model
        # lock, so a batch cannot mix two strategy generations.
        from ..learning.runtime import checkpoint
        with self.decision_lock:
            rows = checkpoint(self.kernel.journal, self.kernel.cfg,
                at_ms=int(time.time()*1000), max_work=8)
            if any(row.get('result') == 'APPLIED' for row in rows):
                self.reload_model()
                self.jobs['market'].trigger()
        return rows

    def queue_portfolio(self, view):
        # Transfer the finished batch to a separate proposal-only consumer.
        # No subsequent decision producer mutates these snapshots.
        with self.lock:
            view._portfolio_holdings_market = dict(self.holdings_market)
            self.pending_portfolio = view
        self.jobs['portfolio'].trigger()

    @timed_worker('portfolio')
    def portfolio(self):
        with self.lock:
            view, self.pending_portfolio = self.pending_portfolio, None
        if view is not None:
            result = view._portfolio_checkpoint()
            if result.get('status') == 'BLOCKED':
                raise ValueError('portfolio_evidence_unavailable')

    def changed(self, view, snap, state, funding, oi, book, learning):
        key = input_fingerprint(snap, generation=view._model_generation, control=state,
            funding=funding, oi=oi, book=book, learning=learning)
        return key, self.seen_inputs.get(snap.symbol) != key

    def decision_completed(self, symbol, key, decision):
        self.seen_inputs[symbol] = key
        with self.lock:
            self.last_decisions[symbol] = (time.monotonic(), decision)
        if len(self.seen_inputs) > 4096:
            self.seen_inputs.clear()

    def execution_started(self):
        with self.lock:
            self.execution_metrics['runs'] += 1
            self.execution_metrics['status'] = 'running'

    def execution_finished(self, duration, submitted, error):
        with self.lock:
            row = self.execution_metrics
            row.update(status='failed' if error else 'idle', last_duration_s=duration,
                max_duration_s=max(row['max_duration_s'] or 0, duration), last_error_type=error)
            row['submitted'] += int(bool(submitted))
            row['failures'] += int(error is not None)

    def exit_score(self, trade, symbol):
        from ..strategy.plan_owner import exit_score
        with self.lock:
            row = self.last_decisions.get(symbol)
        return exit_score(row[1], trade) if row and time.monotonic() - row[0] <= 60 else None

    def reload_model(self):
        with self.decision_lock:
            k = self.kernel
            k.population = k._load_population()
            k._pop_sig = [(st.id, st.state, st.params) for st, _ in k.population]
            k.exits.spec_exits = k._spec_exits
            k.exits.genomes = {st.id: st.params for st, _ in k.population}
            self.model = (self.model[0] + 1, model_snapshot(k))

    def strategy_health(self):
        view = analysis_context(self)
        from ..strategy.promotion import evaluate_population
        evaluate_population(view.journal, view.notifier)
        if view.cfg.get('mechanism', {}).get('enabled', True):
            view._mechanism_once(review_only=True)
        self.reload_model()

    def research(self):
        view = analysis_context(self)
        cfg = view.cfg
        if cfg.get('research', {}).get('enabled', False):
            # The configured research engine owns this slot. Running legacy
            # discovery first could consume the entire slot and starve the
            # compositional mechanism indefinitely.
            closure = cfg.get('research', {}).get('closure') or {}
            if closure.get('enabled', False):
                from ..observability.managed import launch_role, wait_role, release_role
                from ..core.config import ROOT
                revision = getattr(self.kernel, '_runtime_revision', None)
                if not revision:
                    self._research_managed_status = dict(state='UNKNOWN',
                        reason='runtime_revision_unavailable', mechanism_pending=True)
                    raise ValueError('managed_research_runtime_revision_unavailable')
                try:
                    handle = getattr(self, '_managed_research_receipt', None)
                    if handle is None:
                        handle = launch_role('research', ROOT, revision=revision)
                        self._managed_research_receipt = handle
                    self._research_managed_status = dict(state='RUNNING',
                        reason='awaiting_verified_completion', unit=handle.get('unit'),
                        mechanism_pending=True)
                    # Only this research thread waits. A stop/timeout releases
                    # the caller, not the independently managed workload.
                    outcome = wait_role(handle, ROOT, revision,
                        timeout_s=float(closure.get('slot_budget_s', 900)) + 30.0,
                        should_stop=lambda: bool(getattr(self.kernel, '_stop', False)))
                    state = outcome.get('state', 'UNKNOWN')
                    self._research_managed_status = dict(state=state,
                        reason=outcome.get('reason'), unit=handle.get('unit'),
                        mechanism_pending=state != 'SUCCEEDED')
                    if state in ('SUCCEEDED', 'FAILED'):
                        released = release_role(handle)
                        self._research_managed_status['unit_released'] = bool(released)
                        self._managed_research_receipt = None
                    if state != 'SUCCEEDED':
                        raise ValueError('managed_research_not_complete:' + state)
                except ValueError:
                    raise
                except Exception as exc:
                    self._research_managed_status = dict(state='UNKNOWN',
                        reason=type(exc).__name__, mechanism_pending=True)
                    raise ValueError('managed_research_unavailable') from exc
                log.info('closure research slot: %s', self._research_managed_status)
            else:
                from ..research.runner import ResearchRunner
                if not hasattr(self, '_research_runner'):
                    self._research_runner = ResearchRunner(view.journal, cfg)
                self._research_runner.step(cycle_seconds=self.kernel._last_cycle_s)
        if cfg.get('mechanism', {}).get('enabled', True):
            section = cfg.get('mechanism', {})
            view._mechanism_once(int(section.get('specs_per_cycle', 1)), int(section.get('max_specs', 8)))
            self.reload_model()

    def validation(self):
        analysis_context(self)._maybe_validate_agents(scheduled=True)

    def derivatives(self):
        view = analysis_context(self)
        from ..brain.harvester import Harvester
        if not hasattr(self, '_harvester'):
            self._harvester = Harvester(view.journal, view.cfg, view.feed, view.notifier)
        h = self._harvester
        h.deriv.cancelled = lambda: self.kernel._stop
        with self.lock:
            current_pairs = {(n[0], n[1]) for n in self.derivative_needs}
        h.deriv.record_all(h.symbols or view.cfg['universe']['majors'], exclude=current_pairs,
                          delay=float(view.cfg.get('derivatives', {}).get('request_delay_s', .3)))
        h.deriv._check_cancelled()

    def refresh_derivative_inputs(self):
        view = analysis_context(self)
        symbols = view.universe.symbols()
        funding, oi = view._funding_map(symbols), view._oi_map(symbols=symbols)
        with self.lock:
            self.derivative_inputs = (funding, oi, deepcopy(view._market_aux))

    def derivative_current_needs(self):
        """Acquire exactly what the current shortlist's strategies require.

        Held symbols first, then shortlist rank; bounded requests per pass. The
        broad configured recorder (``derivatives``) keeps its own coverage and
        cadence; this adds no optional series for any coin.
        """
        from ..data.derivatives import DerivFeed
        with self.lock:
            needs, held = list(self.derivative_needs), set(self.held_symbols)
        needs.sort(key=lambda n: n[0] not in held)           # stable: shortlist rank otherwise
        feed = getattr(self, '_needs_feed', None)
        if feed is None:
            feed = self._needs_feed = DerivFeed()
        feed.cancelled = lambda: self.kernel._stop
        cfg = self.kernel.cfg.get('derivatives', {})
        result = feed.acquire_needs(needs, max_requests=int(cfg.get('needs_per_pass', 20)),
                                    delay=float(cfg.get('request_delay_s', .3)), held=held)
        with self.lock:
            self.need_stats = dict(result, required=len(needs), at=time.time())

    def derivative_history(self):
        # Separate client/worker: multi-year history never delays current inputs.
        from ..brain.harvester import Harvester
        view = analysis_context(self)
        h = Harvester(view.journal, view.cfg, view.feed, view.notifier)
        h.deriv.cancelled = lambda: self.kernel._stop
        h.deriv.history_yield = self._history_yield
        h.deriv.backfill_start = self.history_resume_index
        h.deriv.backfill_budget_s = float(view.cfg.get('derivatives', {}).get('history_slice_s', 600))
        result = h.harvest_once(backfill=True)
        if result.get('error'):
            raise RuntimeError('derivative_history_incomplete')
        # Unfinished slices resume at the next symbol; a finished pass starts over tomorrow.
        self.history_resume_index = h.deriv.history_resume or 0
        if h.deriv.history_resume is not None and not self.kernel._stop:
            self.jobs['derivative_history'].trigger()

    def _history_yield(self):
        """Cold/history work waits (bounded) while a scan, warm refill or position cycle runs."""
        from ..data import market_provenance as mp
        waited = 0
        while (waited < 120 and not self.kernel._stop and not self.stop_event.is_set()
               and (self.scan_in_flight or mp._WARM or self.jobs['positions'].snapshot().get('running_for_s'))):
            time.sleep(1)
            waited += 1
        if waited:
            with self.lock:
                self.history_yields += 1

    def references(self):
        from ..data.ref_sources import refresh_all
        report = refresh_all(self.kernel.feed, list(self.kernel.cfg.get('references', {}).get('alts_members') or []),
                             current_window_ms=31 * 86_400_000)
        exports = self.kernel.cfg.get('references', {}).get('chart_exports') or []
        if exports:
            from ..data.chart_exports import ingest_exports
            from ..data.references import RefStore
            store = RefStore()
            try:
                report['published_charts'] = ingest_exports(exports, store, captured_ms=int(time.time()*1000))
            finally:
                store.db.close()

        if any(isinstance(v, str) for v in report.values()):
            raise ValueError('reference_sources_incomplete')

    def alts_history(self):
        """Cold alt-index rebuild as budgeted history maintenance (own worker).

        Separate from the hourly current reference refresh, yielding at every
        member boundary; a scan never waits on it (cache-only reads), and it never
        waits on a scan beyond the bounded yield."""
        from ..data.ref_sources import rebuild_alts_cold, alts_cold_pending
        from ..data.references import RefStore
        # Cold index history must not compete with initial current-data recovery.
        stats = self.last_decision_stats or {}
        if not stats.get('strategy_completed') or stats.get('strategy_blocked') or stats.get('snapshot_refusals'):
            self.alts_cold_status = dict(state='deferred', reason='current_inputs_not_ready')
            return
        members = list(self.kernel.cfg.get('references', {}).get('alts_members') or [])
        if not members:
            return
        store = RefStore()
        try:
            if not alts_cold_pending(store, members):
                return
            result = rebuild_alts_cold(self.kernel.feed, members, store, int(time.time()*1000),
                                       members_per_slice=int(self.kernel.cfg.get('references', {}).get('cold_members_per_slice', 8)),
                                       yield_hook=self._history_yield)
            self.alts_cold_status = result
            if result.get('state') == 'in_progress' and not self.kernel._stop:
                self.jobs['alts_history'].trigger()
        finally:
            store.db.close()

    def scraper(self):
        from ..brain.scraper import Scraper
        k = self.kernel
        Scraper(k.journal, k.cfg, k.feed, k.notifier).harvest_once()

    def crawler(self):
        from ..brain.crawler import DeepCrawler
        k = self.kernel
        DeepCrawler(k.journal, k.cfg, k.feed, k.notifier).crawl_once()

    def rent(self):
        from ..engine.rent_keeper import RentKeeper
        k = self.kernel
        RentKeeper(k.exchange, k.journal, k.notifier, k.cfg).tick()

    def candle_acquired(self, symbol, tf, outcome):
        from ..core.types import TF_MS
        if outcome != 'completed' or tf not in TF_MS:
            return
        boundary = int(time.time()*1000) // TF_MS[tf]
        with self.lock:
            key = (symbol, tf)
            if self.candle_events.get(key) == boundary:
                return
            self.candle_events[key] = boundary
            held = symbol in self.held_symbols
            if len(self.candle_events) > 4096:
                self.candle_events.clear()
        if held:
            self.jobs['positions'].trigger()
        if tf == '1h':
            self.jobs['outcomes'].trigger()

    def candles(self):
        from ..core.types import TF_MS, closed_bars
        from ..data.market_provenance import ms
        k = self.kernel
        now = int(time.time()*1000)
        required = set(k._exposure_symbols())
        symbols = sorted(required) + sorted(set(k.universe.symbols()) - required)
        timeframes = set(k.cfg['timeframes'].get('context', [])) | {'15m', '1h', '4h'}
        # Each new close is queued once. Failed/missing series stay pending and
        # are retried every 15s through the bounded acquisition queue.
        for tf in sorted(timeframes):
            if tf not in TF_MS:
                continue
            expected = (now // TF_MS[tf]) * TF_MS[tf] - TF_MS[tf]
            if self.candle_boundaries.get(tf) != expected:
                self.candle_boundaries[tf] = expected
                for symbol in symbols:
                    self.candle_pending[(symbol, tf)] = expected
        checked = completed = queued = 0
        # Fair rotation within priority tiers: held positions, then the current
        # shortlist, then the rest of the existing collection coverage. A failing
        # series is re-queued at the back, so it cannot starve the others.
        with self.lock:
            shortlisted = set(self.shortlisted)
        order = {key: i for i, key in enumerate(self.candle_pending)}
        tier = lambda key: 0 if key[0] in required else 1 if key[0] in shortlisted else 2
        for key in sorted(self.candle_pending, key=lambda k: (tier(k), order[k]))[:64]:
            expected = self.candle_pending.pop(key)
            symbol, tf = key
            checked += 1
            # Committed tip metadata decides completion; payload is decoded only
            # when the series witness changed since the last confirmation.
            if k.feed.tip_complete(symbol, tf, expected):
                completed += 1
                continue
            # force extends the stored tail (small correction overlap); it does not
            # re-download history, and a new close is acquired immediately.
            accepted = k.feed._queue_background_request(symbol, tf, 400, True, 30)
            queued += int(accepted)
            self.candle_pending[key] = expected
        # Proactive re-observation: held positions and the shortlist's actual timeframes
        # get a REAL tail observation before their original receipt budget expires,
        # instead of waiting for a scan to miss. Broad coverage stays at close cadence.
        active = {symbol: set(timeframes) for symbol in required}
        with self.lock:
            for symbol, tfs in self.candle_active.items():
                active.setdefault(symbol, set()).update(tfs)
        reobserved = 0
        for symbol in sorted(active, key=lambda s: (s not in required, s)):
            for tf in sorted(active[symbol]):
                if (symbol, tf) in self.candle_pending or tf not in TF_MS or reobserved >= 64:
                    continue
                if k.feed.reobservation_due(symbol, tf, now):
                    reobserved += int(bool(k.feed._queue_background_request(symbol, tf, 400, True, 30)))
        self.candle_stats = dict(pending=len(self.candle_pending), checked=checked,
                                 confirmed_stored=completed, queued=queued, reobserved=reobserved,
                                 active_symbols=len(active))

    def snapshot(self):
        # Read-only bounded stack locations for slow workers: no locals,
        # arguments, configuration or payloads enter the status surface.
        import sys
        slow = {}
        for name, worker in self.jobs.items():
            facts = worker.snapshot()
            if (facts.get('running_for_s') or 0) < 30:
                continue
            thread = getattr(worker, '_thread', None)
            frame = sys._current_frames().get(thread.ident) if thread is not None else None
            locations = []
            while frame is not None and len(locations) < 16:
                locations.append(dict(file=frame.f_code.co_filename, line=frame.f_lineno,
                                      function=frame.f_code.co_name))
                frame = frame.f_back
            slow[name] = locations
        with self.lock:
            latest_scan = None if self.last_scan_completed_at is None else dict(
                schema='current-scan-summary.v1', scan_id=f'{os.getpid()}:{self.scans_completed}',
                observed_at=self.last_scan_completed_at, shortlisted=self.last_decision_stats.get('shortlisted', 0),
                evaluated=self.last_decision_stats.get('decisions', 0),
                coins=deepcopy(self.last_decision_stats.get('scan_coins', [])),
                timings=deepcopy(self.last_full_cycle))
        return dict(latest_scan=latest_scan, slow_worker_stacks=slow, schema='kernel-cycles.v1', pid=os.getpid(), observed_at=time.time(),
            jobs={name: worker.snapshot() for name, worker in self.jobs.items()},
            stages=self.stage_timings.snapshot(),
            decision_summary=({key: self.last_decision_stats.get(key) for key in
                ('admission', 'processing_boundaries', 'input_refusals', 'snapshot_refusals', 'decisions', 'deep_processed', 'unchanged_inputs', 'learning_input_failures', 'learning_input_error_type', 'input_policy', 'shortlisted', 'no_strategy', 'eligible_strategies', 'requirement_errors', 'persisted_attempts', 'strategy_coverage', 'strategy_expected', 'strategy_blocked', 'strategy_regime_ineligible', 'strategy_ineligible_other', 'strategy_attempted', 'strategy_completed', 'strategy_completed_unreported', 'strategy_refused', 'strategy_not_invoked', 'blocked_reasons')}
                if self.last_decision_stats is not None else None),
            execution=dict(self.execution_metrics),
            research_managed=deepcopy(getattr(self, '_research_managed_status', None)),
            replaced_scan_batches=self.scan_replaced,
            scan_in_flight=self.scan_in_flight, skipped_busy_scan_ticks=self.scan_busy_ticks,
            scans_completed=self.scans_completed, scans_discarded=self.scans_discarded,
            last_full_cycle=deepcopy(self.last_full_cycle), input_access=self.input_access(),
            candles=dict(self.candle_stats), derivative_needs=deepcopy(self.need_stats), history_yields=self.history_yields,
            history_resume_index=self.history_resume_index, alts_cold=deepcopy(self.alts_cold_status))

    def input_access(self):
        from ..data import market_provenance as mp
        return dict(mp.STATS, cache_entries=len(mp.CURRENT.entries), cache_rows=mp.CURRENT.rows,
                    capacity=mp.CURRENT.capacity())

    def input_metadata(self):
        """Off-path: seed demanded witnesses, resolve identity metadata in bounded steps."""
        from ..data import market_provenance as mp
        from ..data.derivatives import DerivFeed
        feed = getattr(self, '_deriv_meta_feed', None)
        if feed is None:
            feed = self._deriv_meta_feed = DerivFeed()
        for conn in (self.kernel.feed.db, feed.db):
            mp.resolve_pending(conn, budget_rows=20000)

    def input_warm(self):
        """Refill windows the cache-only scan reported as warming (off the trading path)."""
        from ..data import market_provenance as mp
        mp.run_warm(max_s=5.0)

    def publish(self):
        target = Path(self.kernel.journal.db_path).parent / 'cycle_runtime.json'
        temporary = target.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(self.snapshot(), sort_keys=True))
        os.replace(temporary, target)

    def health(self):
        self.publish()
        with self.lock:
            account, positions = self.account_data, self.positions_at
        now = time.monotonic()
        if (account is None or positions is None or now - account[0] > 60 or now - positions > 60):
            raise ValueError('essential_cycles_unavailable_or_stale')
        # Completion now means current account AND independent exposure service.
        # Market/research completion cannot impersonate these safety cycles.
        completed = [self.jobs[name].snapshot()['last_success_at']
                     for name in ('account', 'positions')]
        if any(stamp is None or not 0 <= time.time() - stamp <= 60 for stamp in completed):
            raise ValueError('essential_cycles_unavailable_or_stale')
        k = self.kernel
        k.heartbeat.beat(dict(startup_state='CYCLE_READY', last_successful_cycle_at=min(completed),
            cycle_basis='account_and_positions', equity=round(account[1], 2),
            state=k.state_machine.state.value, boot_complete=k._boot_complete,
            stopping=k._stop))
