"""Execution serialization and isolated analysis contexts for kernel workers."""
from copy import copy, deepcopy
from functools import wraps
import datetime as dt
import hashlib
import json
import time


def _coordinated(fn):
    @wraps(fn)
    def call(kernel, *args, **kwargs):
        runtime = getattr(kernel, '_split_runtime', None)
        if runtime is None:
            return fn(kernel, *args, **kwargs)
        with runtime.execution_lock:
            if fn.__name__ == '_try_enter':
                decision, snap = args[:2]
                if getattr(getattr(runtime, 'kernel', None), '_stop', False):
                    decision.skip_reason = 'scheduled_execution: stopping'
                    return False
                age = time.time() - dt.datetime.fromisoformat(snap.ts).timestamp()
                if age < 0 or age > 60:
                    decision.skip_reason = 'scheduled_execution: stale_decision'
                    return False
                root = runtime.kernel
                balance, status = root._risk_step()
                if (not root.state_machine.can_enter() or status.get('risk_state') != 'ok'
                    or (status.get('daily_pnl_pct') or 0) <= -root.risk.daily_loss_block * 100):
                    decision.skip_reason = 'scheduled_execution: current_risk_or_control_refused'
                    return False
                closed = int(root.journal.query("SELECT COUNT(*) AS n FROM trades WHERE status='closed'")[0]['n'])
                args = (decision, snap, balance, closed)
            elif fn.__name__ == '_manage_one':
                trade, snap, score = args
                rows = kernel.journal.query("SELECT * FROM trades WHERE id=? AND status='open'", (trade['id'],))
                if not rows or not kernel._manages_exits():
                    return None
                if score is None:
                    score = runtime.exit_score(rows[0], snap.symbol)
                args = (rows[0], snap, score)
            result = fn(kernel, *args, **kwargs)
            if fn.__name__ in ('_try_enter', '_manage_one', '_detect_exchange_exits', '_manage_paper') and result:
                if fn.__name__ == '_manage_paper':
                    runtime.trade_closed()
                else:
                    runtime.fill_event()
            return result
    return call


def coordinated(fn):
    guarded = _coordinated(fn)
    @wraps(fn)
    def call(kernel, *args, **kwargs):
        runtime = getattr(kernel, '_split_runtime', None)
        if runtime is None or fn.__name__ != '_try_enter' or not hasattr(runtime, 'execution_started'):
            return guarded(kernel, *args, **kwargs)
        started = time.monotonic()
        runtime.execution_started()
        error = None
        result = False
        try:
            result = guarded(kernel, *args, **kwargs)
            return result
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            runtime.execution_finished(time.monotonic()-started, result, error)
    return call


def model_coordinated(fn):
    @wraps(fn)
    def call(kernel, *args, **kwargs):
        runtime = getattr(kernel, '_split_runtime', None)
        if runtime is None:
            return fn(kernel, *args, **kwargs)
        with runtime.decision_lock:
            return fn(kernel, *args, **kwargs)
    return call


MODEL_FIELDS = ('population', '_pop_sig', '_spec_exits', '_spec_rows', '_spec_requires',
                '_scan_timeframes', '_entry_identities', '_spec_identities', '_identities_loaded_at')


def model_snapshot(kernel):
    return {key: deepcopy(getattr(kernel, key)) for key in MODEL_FIELDS if hasattr(kernel, key)}


def analysis_context(runtime):
    """A worker owns all its mutable scan caches; capabilities remain shared.

    The model dictionary is published in one assignment. Its contents are never
    subsequently mutated; each reader receives an independent deep copy.
    """
    from ..agents.positioning import PositioningAnalyst
    from ..agents.orderbook_depth import DepthScout
    from ..engine.orchestrator import Orchestrator
    root = runtime.kernel
    view = copy(root)
    generation, model = runtime.model
    for key, value in deepcopy(model).items():
        setattr(view, key, value)
    view._model_generation = generation
    for key in ('_book_cache', '_btc_ctx', '_derivs_cache', '_market_aux',
                '_portfolio_market', '_portfolio_holdings_market', '_provenance_acquisitions',
                '_context_anchor_frames', '_scan_membership_receipts', '_probation_at'):
        setattr(view, key, {})
    for key in ('_market_cache', '_candidate_cache', '_funding_cache'):
        setattr(view, key, None)
    view._oi_cache = (0., {})
    view.positioning_agent = PositioningAnalyst(root.exchange)
    view.depth_agent = DepthScout()
    view.analysts = [view._make_agent(k) for k in
        ('structure', 'momentum', 'flow', 'value', 'rotation', 'positioning', 'depth')]
    view.orchestrator = Orchestrator(view.analysts, root.journal, cfg=root.cfg)
    view.exits = copy(root.exits)
    view.exits.spec_exits = view._spec_exits
    view.exits.genomes = {st.id: deepcopy(st.params) for st, _ in view.population}
    # Do not inherit a mutable admission receipt from another worker.
    view._admission_receipt = None
    return view


def input_fingerprint(snap, *, generation, control, funding, oi, book, learning):
    """Hash actual decision inputs, not a new scan timestamp/receipt identifier."""
    import pandas as pd
    h = hashlib.sha256()
    def add(value):
        if isinstance(value, pd.DataFrame):
            columns = [c for c in value.columns if c not in
                       ('received_at_ms', 'request_started_ms', 'available_at_ms', 'revision_id')]
            h.update(str(columns).encode())
            h.update(pd.util.hash_pandas_object(value[columns], index=True).values.tobytes())
        elif isinstance(value, dict):
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0])):
                h.update(str(key).encode())
                add(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                add(item)
        else:
            h.update(json.dumps(value, sort_keys=True, default=str).encode())
    for value in (snap.dfs, snap.btc_ctx, snap.universe, snap.derivs, snap.market,
                  snap.anchor_frames, snap.world, snap.regime, snap.btc_trend,
                  (snap.admission_context or {}).get('category')):
        add(value)
    h.update(json.dumps(dict(generation=generation, control=str(control), price=snap.price,
        funding=funding, oi=oi, book=book, learning=learning), sort_keys=True, default=str).encode())
    return h.hexdigest()
