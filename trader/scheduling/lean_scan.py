"""Shortlist -> strategy inputs -> evaluation -> trade-only evidence.

No collector, learning or portfolio analysis runs in this decision worker.
"""
import datetime as dt
import logging
import time

from ..core.types import Action, ControlState, MarketType, RiskError, TF_MS
from .timing import stage_scope
from .scan_plan import build

log = logging.getLogger(__name__)


class BatchFeed:
    """One read per requested series/window in this batch, never across batches."""
    def __init__(self, feed):
        self.feed, self.frames, self.limits = feed, {}, {}

    def __getattr__(self, key):
        return getattr(self.feed, key)

    def fetch_ohlcv(self, symbol, tf='15m', limit=400, **kw):
        maximum = max(limit, self.limits.get((symbol, tf), 0))
        key = (symbol, tf, maximum, tuple(sorted(kw.items())))
        if key not in self.frames:
            self.frames[key] = self.feed.fetch_ohlcv(symbol, tf, limit=maximum, **kw)
        frame = self.frames[key]
        return frame.tail(limit).copy(deep=True) if frame is not None else None

    def fetch_multi(self, symbol, tfs, limit=400):
        return {tf: frame for tf in tfs
                if (frame := self.fetch_ohlcv(symbol, tf, limit=limit)) is not None}


def publish_needs(view):
    """Tell the current producer exactly which (symbol, derivative series) the
    applicable strategies of the shortlisted coins require (strategy-owned,
    ordered by shortlist rank). Nothing optional is added for any coin."""
    from ..strategy.spec_evidence import _SERIES_FOR
    needs = []
    for symbol, plan in view._scan_plan.items():
        needs += [(symbol, _SERIES_FOR[r], plan.spans.get(r, 0)) for r in sorted(plan.requires) if r in _SERIES_FOR]
    runtime = getattr(view, '_split_runtime', None)
    if runtime is not None and hasattr(runtime, 'derivative_needs'):
        with runtime.lock:
            runtime.derivative_needs = needs
            runtime.candle_active = {symbol: set(plan.limits) for symbol, plan in view._scan_plan.items()}


def prepare(view):
    with stage_scope('scan.requirements'):
        view._scan_plan, view._scan_requirement_errors = build(view, view._symbol_roles.deep)
    view._lean_scan = True
    view.feed = BatchFeed(view.feed)
    view._scan_references, view._scan_btc = {}, {}
    merged_refs, btc_limits, peer_limits = set(), {}, {}
    for plan in view._scan_plan.values():
        merged_refs.update(r for r in plan.requires if r.startswith('ref:'))
        for tf, bars in plan.btc.items():
            btc_limits[tf] = max(btc_limits.get(tf, 0), bars)
        if plan.peers:
            for tf, bars in plan.peer_limits.items():
                peer_limits[tf] = max(peer_limits.get(tf, 0), bars)
    for symbol, plan in view._scan_plan.items():
        for tf, bars in plan.limits.items():
            view.feed.limits[symbol, tf] = bars
    for symbol, limits in [('BTC/USDT', btc_limits),
                           *((symbol, peer_limits) for symbol in view._symbol_roles.peers)]:
        for tf, bars in limits.items():
            key = (symbol, tf)
            view.feed.limits[key] = max(view.feed.limits.get(key, 0), bars)
    publish_needs(view)
    with stage_scope('scan.shared_collection'):
        if merged_refs:
            from ..strategy.spec_evidence import load_refs
            now = int(time.time()*1000)
            # Per reference: the span of the strategies that actually read it, not the
            # longest frame of the shared guards.
            spans = {}
            for plan in view._scan_plan.values():
                for name, span in plan.spans.items():
                    spans[name] = max(spans.get(name, 0), span)
            view._scan_references = load_refs(sorted(merged_refs), as_of_ms=now,
                window_start_ms={name: max(0, now - spans.get(name, 0)) for name in merged_refs})
        for tf, bars in btc_limits.items():
            frame = view.feed.fetch_ohlcv('BTC/USDT', tf, limit=bars)
            if frame is not None:
                view._scan_btc[tf] = frame
        peers = {}
        for symbol in view._symbol_roles.peers if peer_limits else ():
            peers[symbol] = {}
            for tf, bars in peer_limits.items():
                frame = view.feed.fetch_ohlcv(symbol, tf, limit=bars)
                if frame is not None:
                    peers[symbol][tf] = frame
    return peers


def readiness(snap, need):
    """Which of a strategy's OWN declared inputs are absent or not VALID in this snapshot?

    Fresh candles never stand in for a missing funding/OI/reference/BTC input.
    """
    from ..strategy.spec_evidence import _SERIES_FOR
    missing = []
    def usable(frame):
        return frame is not None and len(frame) and str(frame['quality'].iloc[-1]) == 'VALID'
    for req in sorted(need['requires']):
        if req in _SERIES_FOR:
            if not usable((snap.derivs or {}).get(_SERIES_FOR[req])):
                missing.append(req)
        elif req.startswith('ref:'):
            if not usable((snap.market or {}).get(req[4:])):
                missing.append(req)
    if need['btc']:
        frames = getattr(snap, 'btc_frames', None) or {}
        if any(tf not in frames for tf in need['btc']):
            missing.append('btc')
    if need['peers'] and not snap.universe:
        missing.append('peers')
    for tf in sorted(need.get('tfs', ())):
        if tf not in snap.dfs:
            missing.append('tf:' + tf)
    return missing


def evidence(view, snap, decision, stats):
    """Called only at a real or paper order admission boundary, before submit."""
    if getattr(decision, '_trade_evidence_saved', False):
        return
    with stage_scope('scan.trade_persistence', symbol=snap.symbol):
        from ..learning.capture_runtime import runtime_inputs
        decision.learning_inputs = runtime_inputs(view.journal, snap, view.cfg,
            cut_ms=int(time.time()*1000), control_state=view.state_machine.state,
            additional_original_inputs=dict(admission=snap.admission_context,
                risk=getattr(decision, '_lean_risk_inputs', None)))
        view.orchestrator.journalize(snap, decision, view.market_type.value, mode='live',
                                    schedule_outcomes=False)
        risk_inputs = getattr(decision, '_lean_risk_inputs', None)
        if risk_inputs is not None:
            from ..learning import capture as lc
            with view.journal._tx() as db:
                lc.safely(db, 'decision:'+decision.id, lc.record_action, 'decision:'+decision.id,
                    dict(risk_inputs['sizing'], config_risk_sha256=lc.L.digest(view.cfg['risk']),
                         input={key:value for key,value in risk_inputs.items() if key != 'sizing'}),
                    int(time.time()*1000), risk=True)
        decision._trade_evidence_saved = True
        stats['persisted_attempts'] += 1


def run(view):
    stats = dict(deep_processed=0, decisions=0, entries=0, skips=0,
                 persisted_attempts=0, paper_entries=0, snapshot_refusals={}, no_strategy=0,
                 requirement_errors=view._scan_requirement_errors,
                 input_policy='strategy_required.trade_evidence_only.v1')
    receipt, _, peers, _, _ = view._prepared_scan
    stats['shortlisted'] = len(view._symbol_roles.deep)
    stats['admission'] = dict(receipt_id=receipt.get('receipt_id') if receipt else None,
        status=receipt.get('degraded_state') if receipt else 'REFUSED',
        deep_admitted=stats['shortlisted'],
        broad_observed=len(receipt.get('roles', {}).get('broad_crypto', ())) if receipt else 0,
        peer_cohort=len(peers), context_anchors=['BTC/USDT'] if view._scan_btc else [])
    stats['processing_boundaries'] = dict(discretionary_deep_admitted=stats['shortlisted'],
        execution_datafeed_available=0, snapshots_constructed=0)
    stats['strategy_coverage'] = {}
    stats['blocked_reasons'] = {}
    stats['input_refusals'] = []
    totals = ('expected', 'blocked', 'regime_ineligible', 'ineligible_other', 'attempted', 'completed',
              'completed_unreported', 'refused', 'not_invoked')
    for name in totals:
        stats['strategy_' + name] = 0
    def row_of(st):
        return stats['strategy_coverage'].setdefault(st.id, dict(
            {name: 0 for name in totals}, reasons={}))
    def note_reason(st, reason):
        row = row_of(st)
        row['reasons'][reason] = row['reasons'].get(reason, 0) + 1
        stats['blocked_reasons'][reason] = stats['blocked_reasons'].get(reason, 0) + 1
    def bump(st, name, reason=None):
        row_of(st)[name] += 1
        stats['strategy_' + name] += 1
        if reason is not None:
            note_reason(st, reason)
    stats['no_strategy'] = stats['shortlisted'] - len(view._scan_plan)
    stats['eligible_strategies'] = sorted({st.id for plan in view._scan_plan.values() for st, _ in plan.population})
    if not view._scan_plan:
        return stats
    runtime = view._split_runtime
    with stage_scope('scan.controls'):
        # No account/network work is needed to analyse a market. The
        # coordinated _try_enter boundary refreshes actual account/Risk before
        # it can submit; unknown scan equity is never replaced with zero.
        balance = None
        status = {'equity': None, 'drawdown_pct': None}
        state = view.state_machine.refresh()
        # Scanning only observes authority. The coordinated entry boundary
        # and Executor still perform the current writable-storage probes.
        from ..observability.safety import entry_refusal
        allowed = state == ControlState.ACTIVE and entry_refusal(view.journal) is None
        blocked = '' if allowed else 'state=' + state.value
        if view.market_type == MarketType.FUTURES and view.executor.recovery_pending():
            supervisor = getattr(view, 'supervisor', None)
            if state == ControlState.ACTIVE and supervisor is not None:
                supervisor.trigger('entry_recovery_pending')
                state = view.state_machine.refresh()
            allowed, blocked = False, 'execution_recovery_pending'
    view._portfolio_market = {}
    for symbol, plan in view._scan_plan.items():
        if getattr(getattr(runtime, 'kernel', None), '_stop', False):
            stats['interrupted'] = True
            break
        with stage_scope('scan.collection', symbol=symbol):
            snap = view._candidate_snapshot_for(symbol, receipt, peers if plan.peers else {})
        if snap is None:
            reason = getattr(view, '_last_snapshot_diagnostic', {}).get('reason', 'missing')
            stats['snapshot_refusals'][reason] = stats['snapshot_refusals'].get(reason, 0) + 1
            for st, _ in plan.population:
                bump(st, 'expected'); bump(st, 'blocked', 'no_snapshot:' + reason)
            continue
        stats['processing_boundaries']['execution_datafeed_available'] += 1
        stats['processing_boundaries']['snapshots_constructed'] += 1
        stats['deep_processed'] += 1
        ready, events = [], {}
        for st, genome in plan.population:
            bump(st, 'expected')
            missing = readiness(snap, plan.needs[st.id]) if st.id in plan.needs else []
            if missing:
                stats['input_refusals'].append(dict(symbol=symbol, strategy=st.id, missing=list(missing)))
                bump(st, 'blocked')
                for m in missing:
                    note_reason(st, 'missing:' + m)
            else:
                ready.append((st, genome))
        if not ready:
            continue            # every applicable strategy lacks its own inputs; nothing to evaluate
        def observe(sid, reason, exc=None, _events=events):
            _events.setdefault(sid, []).append(reason)
        with stage_scope('scan.strategies', symbol=symbol):
            decision = view.orchestrator.decide(snap, ready, entry_allowed=allowed,
                blocked_reason=blocked, measurements=False, persist_cooldown=False, observer=observe)
        from ..strategy import library as strat_library
        for st, genome in ready:
            reasons = events.get(st.id, [])
            capable = getattr(strat_library.EVALUATORS.get(genome.family), '_diagnostic_capable', False)
            if not reasons:
                bump(st, 'not_invoked', 'not_invoked')
            elif reasons[0].startswith('ineligible_'):
                bump(st, 'regime_ineligible' if reasons[0] == 'ineligible_regime' else 'ineligible_other', reasons[0])
            else:
                bump(st, 'attempted')
                last = reasons[-1]
                if last in ('emitted_signal', 'returned_none'):
                    bump(st, 'completed' if capable or last == 'emitted_signal' else 'completed_unreported')
                else:
                    bump(st, 'refused', last)
        stats['decisions'] += 1
        # Runtime retains bounded exit-score/owner visibility, not full snapshots.
        runtime.decision_completed(symbol, None, decision)
        if decision.action == Action.HOLD or decision.skip_reason or not allowed:
            stats['skips'] += int(decision.action != Action.HOLD)
            continue
        decision.scan_id = None
        decision._persist_trade_evidence = lambda: evidence(view, snap, decision, stats)
        try:
            with stage_scope('scan.risk_execution', symbol=symbol):
                ok = view._try_enter(decision, snap, balance, 0)  # coordinated entry refreshes count/risk
        except RiskError as exc:
            view.state_machine.set(ControlState.HALTED, 'risk_engine', str(exc))
            allowed = False
            blocked = 'state=HALTED'
            ok = False
        finally:
            # Never leave a closure holding the last coin's complete snapshot.
            decision.__dict__.pop('_persist_trade_evidence', None)
        if getattr(decision, '_trade_evidence_saved', False):
            view.journal.update_decision_outcome(decision.id, decision.executed,
                                               decision.size_usdt, decision.skip_reason)
            if getattr(decision, '_paper_entry_id', None) or ok:
                view._portfolio_market[decision.id] = snap
        stats['paper_entries'] += int(bool(getattr(decision, '_paper_entry_id', None)))
        decision.learning_inputs = None
        decision.__dict__.pop("_lean_risk_inputs", None)
        stats['entries'] += int(bool(ok))
    # Portfolio remains an independent consumer, and ordinary HOLD batches do
    # not carry 30 full snapshots into it.
    if view._portfolio_market:
        runtime.queue_portfolio(view)
    return {**stats, 'equity': status['equity'], 'dd_pct': status['drawdown_pct']}
