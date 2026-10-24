"""Shortlist -> strategy inputs -> evaluation -> trade-only evidence.

No collector, learning or portfolio analysis runs in this decision worker.
"""
import datetime as dt
import logging
import time

from ..core.types import Action, ControlState, MarketType, RiskError
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
    with stage_scope('scan.shared_collection'):
        if merged_refs:
            from ..strategy.spec_evidence import load_refs
            view._scan_references = load_refs(sorted(merged_refs), as_of_ms=int(time.time()*1000))
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
        with stage_scope('scan.collection', symbol=symbol):
            snap = view._candidate_snapshot_for(symbol, receipt, peers if plan.peers else {})
        if snap is None:
            reason = getattr(view, '_last_snapshot_diagnostic', {}).get('reason', 'missing')
            stats['snapshot_refusals'][reason] = stats['snapshot_refusals'].get(reason, 0) + 1
            continue
        stats['processing_boundaries']['execution_datafeed_available'] += 1
        stats['processing_boundaries']['snapshots_constructed'] += 1
        stats['deep_processed'] += 1
        with stage_scope('scan.strategies', symbol=symbol):
            decision = view.orchestrator.decide(snap, plan.population, entry_allowed=allowed,
                blocked_reason=blocked, measurements=False, persist_cooldown=False)
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
