"""Independent kernel cycles, with one serialized venue-mutation boundary."""
from collections import deque
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import threading
import time

from .runtime import Calendar, Worker
from .coordination import analysis_context, model_snapshot, input_fingerprint
from .maintenance import Maintenance

log = logging.getLogger('luffy.cycles')


class KernelRuntime:
    def __init__(self, kernel):
        self.kernel = kernel
        self.execution_lock = threading.RLock()
        self.decision_lock = threading.RLock()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.model = (0, model_snapshot(kernel))
        self.pending_scan = None
        self.pending_portfolio = None
        self.holdings_market = {}
        self.scan_replaced = 0
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
        self.last_decision_stats = None
        self.execution_metrics = dict(status="idle", runs=0, submitted=0, failures=0,
            last_duration_s=None, max_duration_s=None, last_error_type=None)
        self.maintenance = Maintenance(kernel.journal, kernel.cfg, kernel.feed)
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
            **self.maintenance.jobs,
        }
        cfg = kernel.cfg
        if cfg.get('derivatives', {}).get('enabled', True):
            self.jobs['derivatives'] = Worker('derivatives', self.derivatives, interval=900, immediate=True)
        if cfg.get('references', {}).get('enabled', True):
            self.jobs['references'] = Worker('references', self.references, interval=3600, immediate=True)
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

    def market(self):
        view = analysis_context(self)
        view._refresh_btc_context()
        if view.cfg.get('attention', {}).get('admission'):
            view._refresh_context_anchors()
        receipt, frames = view._admission_plan()
        peers = view._peer_frames(view._symbol_roles.peers)
        with self.lock:
            funding, oi, aux = deepcopy(self.derivative_inputs)
        now_ms = int(time.time()*1000)
        def fresh(kind, symbol, ttl):
            receipt = aux.get(kind + ':' + symbol)
            return receipt is not None and 0 <= now_ms - receipt['available_at_ms'] <= ttl * 1000
        funding = {symbol: value for symbol, value in funding.items()
                   if fresh('funding', symbol, view._DERIVS_TTL)}
        oi = {symbol: value for symbol, value in oi.items()
              if fresh('open_interest', symbol, 900)}
        view._market_aux.update({key: value for key, value in aux.items()
            if key.split(':', 1)[1] in (funding if key.startswith('funding:') else oi)})
        view._prepared_scan = (receipt, frames, peers, funding, oi)
        view._prepared_at = time.monotonic()
        with self.lock:
            if self.pending_scan is not None:
                self.scan_replaced += 1
            self.pending_scan = view
        self.jobs['decisions'].trigger()

    def decisions(self):
        with self.lock:
            view, self.pending_scan = self.pending_scan, None
        if view is None:
            return
        start = time.monotonic()
        try:
            with self.decision_lock:
                if view._model_generation != self.model[0] or time.monotonic() - view._prepared_at > 60:
                    self.jobs['market'].trigger()
                    return
                stats = view.cycle()
            with self.lock:
                self.last_decision_stats = stats
        finally:
            self.kernel._last_cycle_s = time.monotonic() - start

    def queue_portfolio(self, view):
        # Transfer the finished batch to a separate proposal-only consumer.
        # No subsequent decision producer mutates these snapshots.
        with self.lock:
            view._portfolio_holdings_market = dict(self.holdings_market)
            self.pending_portfolio = view
        self.jobs['portfolio'].trigger()

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
        if not getattr(self, '_deriv_backfilled', False) and view.cfg.get('derivatives', {}).get('backfill_on_start', True):
            h.harvest_once(backfill=True)
            self._deriv_backfilled = True
        h.deriv.record_all(h.symbols or view.cfg['universe']['majors'],
                          delay=float(view.cfg.get('derivatives', {}).get('request_delay_s', .3)))
        h.deriv._check_cancelled()
        symbols = view.universe.symbols()
        funding, oi = view._funding_map(symbols), view._oi_map(symbols=symbols)
        with self.lock:
            self.derivative_inputs = (funding, oi, deepcopy(view._market_aux))

    def references(self):
        from ..data.ref_sources import refresh_all
        report = refresh_all(self.kernel.feed, list(self.kernel.cfg.get('references', {}).get('alts_members') or []))
        if any(isinstance(v, str) for v in report.values()):
            raise ValueError('reference_sources_incomplete')

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
        # Fair rotation: held symbols lead at a new boundary, then every series
        # gets a turn; neither queue saturation nor an unlisted symbol starves it.
        for key in list(self.candle_pending)[:64]:
            expected = self.candle_pending.pop(key)
            symbol, tf = key
            checked += 1
            frame = k.feed.latest_ohlcv(symbol, tf, 2)
            frame = closed_bars(frame, tf, now_ms=now)
            if frame is not None and len(frame) and int(ms(frame['ts'])[-1]) >= expected and (
                'quality' not in frame or frame['quality'].eq('VALID').all()):
                completed += 1
                continue
            accepted = k.feed._queue_background_request(symbol, tf, 400, True, 30)
            queued += int(accepted)
            self.candle_pending[key] = expected
        self.candle_stats = dict(pending=len(self.candle_pending), checked=checked,
                                 confirmed_stored=completed, queued=queued)

    def snapshot(self):
        return dict(schema='kernel-cycles.v1', pid=os.getpid(), observed_at=time.time(),
            jobs={name: worker.snapshot() for name, worker in self.jobs.items()},
            execution=dict(self.execution_metrics),
            replaced_scan_batches=self.scan_replaced, candles=dict(self.candle_stats))

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
        k = self.kernel
        k.heartbeat.beat(dict(startup_state='CYCLE_READY', last_successful_cycle_at=time.time() - max(now-account[0], now-positions),
            cycle_basis='account_and_positions', equity=round(account[1], 2),
            state=k.state_machine.state.value, boot_complete=k._boot_complete,
            stopping=k._stop))
