"""Reconcile an explicitly observed USD-M hedge-mode book by native side.

No account-mode change or fresh entry. Unowned or ambiguous exposure remains
visible and blocks readiness rather than being assigned to another leg.
"""
import json
import math
import time
from datetime import datetime, timezone

from . import protective, position_mode as PM, native_exit_provenance as N
from .accounting import number
from .booking import monetary_total
from ..core.types import MarketType, norm_symbol
from ..data.feed import execution_account_scope
from ..observability.portfolio_observation import observe_positions, trading_source



def close_from_protection(exchange, journal, trade):
    """Book a proven flat native side from its owned algo and actual fills."""
    from types import SimpleNamespace
    from .coordinated_close import _entry_receipt
    if trading_source(exchange)[0] != 'demo' or PM.position_side(trade) == 'BOTH':
        raise ValueError('native_protective_exit_scope_unavailable')
    identity = PM.identity_of(trade)
    _entry_receipt(SimpleNamespace(ex=exchange, journal=journal), trade, identity, identity['execution_binding'])
    capture = N.capture(N.reference_snapshot(trade), exchange)
    evidence = N.replay(capture)
    if not evidence['leg_verified']:
        return False
    fills = evidence['fills']; quantity = sum(number(f['amount']) for f in fills)
    pnl = monetary_total(fills)
    if pnl is None:raise ValueError('native_exit_fee_currency_unavailable')
    price = sum(number(f['price'])*number(f['amount']) for f in fills)/quantity
    closed = datetime.fromtimestamp(number(evidence['order']['updateTime'])/1000, timezone.utc).isoformat()
    reason = 'tp_fill' if evidence['algo']['orderType'] == 'TAKE_PROFIT_MARKET' else 'sl_fill'
    journal.close_trade(trade['id'], price, pnl, reason, closed_at=closed,
        accounting=dict(basis='venue_order_fills', purpose='native_exit',
            order_id=str(evidence['order']['orderId']), symbol=trade['symbol'],
            side='sell' if trade['side'] == 'long' else 'buy', quantity=quantity,
            since_ms=number(evidence['order']['time']), observed_ms=capture['captured_ms'],
            fills=fills, protective_algo_id=str(evidence['algo']['algoId']),
            native_exit_provenance=capture))
    return True


def run(exchange, journal, rows, exclude_symbols=()):
    from .reconcile import REARM_KEY
    result = dict(adopted=0, ghosts=0, aligned=0, rearmed=0, naked=0,
                  stops_swept=0, stops_stuck=0, positions_readable=False, safety_issues=[])
    issues = result['safety_issues']
    try:
        environment, source = trading_source(exchange)
        if environment != 'demo':
            raise ValueError('native_reconcile_demo_only')
        now = int(time.time()*1000)
        observation = observe_positions(rows, exchange_id=exchange.id, market_type=MarketType.FUTURES,
            environment=environment, source_ref=source, request_start_ms=now, response_received_ms=now)
        if any(p.position_side == 'BOTH' for p in observation.positions):
            raise ValueError('native_reconcile_mixed_position_modes')
        scope = execution_account_scope(exchange)
        held = {(p.instrument_id.value, p.position_side):p for p in observation.positions if p.position_present}
        result.update(positions_readable=True, position_count=len(held))
        raw = {(exchange.market(p['symbol'])['id'], PM.venue_side(p)):p for p in rows}
        owners = {}
        excluded = {norm_symbol(s) for s in exclude_symbols}
        for trade in journal.open_trades():
            if trade['market_type'] != 'futures' or norm_symbol(trade['symbol']) in excluded:
                continue
            identity = PM.identity_of(trade); binding = identity.get('execution_binding') or {}
            side = PM.position_side(identity); cap = binding.get('capability') or {}
            if (side == 'BOTH' or side.lower() != trade['side'] or cap.get('account_scope') != scope
                    or cap.get('instrument_id') != binding.get('instrument_id')
                    or binding.get('instrument_id') != 'binance_usdm:futures:'+exchange.market(trade['symbol'])['id']):
                raise ValueError('native_reconcile_owner_binding_unverified')
            from types import SimpleNamespace
            from .coordinated_close import _entry_receipt
            _entry_receipt(SimpleNamespace(ex=exchange, journal=journal), trade, identity, binding)
            key = binding['instrument_id'], side
            if key in owners:raise ValueError('native_reconcile_duplicate_side_owner')
            owners[key] = trade
        stops = protective.open_stops(exchange, strict=True)
        pending = json.loads(journal.kv_get(REARM_KEY, '{}'))
        if not isinstance(pending, dict):raise ValueError('protection_rearm_evidence_unreadable')
        for key, trade in owners.items():
            iid, side = key; symbol = trade['symbol']
            marker = PM.rearm_key(symbol, side)
            p = held.get(key)
            if p is None:
                if not close_from_protection(exchange, journal, trade):
                    issues.append('native_flat_exit_provenance_pending:'+symbol+':'+side)
                    continue
                pending.pop(marker, None); result['ghosts'] += 1
                continue
            amount = p.absolute_quantity
            if not math.isclose(number(trade['amount']), amount, rel_tol=1e-8, abs_tol=1e-8):
                issues.append('native_position_size_reconciliation_required:'+symbol+':'+side)
                continue
            expected = number(trade['stop_loss'])
            matches = [s for s in stops if protective.protection_match(exchange, symbol, trade['side'],
                       amount, expected, s, native_side=side).matches]
            known = [s for s in matches if str(s['id']) == str(trade.get('sl_order_id') or '')]
            if known:
                if marker in pending:
                    saved = pending[marker]
                    if saved.get('trade_id') != trade['id'] or str(saved.get('order_id')) != str(known[0]['id']):
                        raise ValueError('native_rearm_receipt_differs')
                    pending.pop(marker)
                continue
            if matches:
                # A submitted stop must match the retained action before it
                # can become the leg's durable protective order.
                saved = pending.get(marker)
                chosen = next((s for s in matches if saved and str(s['id']) == str(saved.get('order_id'))), None)
                if chosen is None:
                    issues.append('native_protective_order_ownership_unverified:'+symbol+':'+side)
                    continue
                journal.record_stop_order(trade['id'], chosen['id'], chosen['stop_price'])
                pending.pop(marker); continue
            result['naked'] += 1
            if marker in pending:
                issues.append('native_protection_awaiting_verification:'+symbol+':'+side)
                continue
            current = raw[(iid.split(':')[-1], side)]
            mark = number(current.get('markPrice'))
            if mark <= 0 or expected <= 0 or (expected >= mark if side == 'LONG' else expected <= mark):
                issues.append('native_protection_cannot_restore:'+symbol+':'+side)
                continue
            pending[marker] = dict(trade_id=trade['id'], order_id=None, amount=amount, stop_price=expected)
            journal.kv_set(REARM_KEY, json.dumps(pending, allow_nan=False))
            oid = protective.place_stop(exchange, symbol, 'sell' if side == 'LONG' else 'buy',
                                        amount, expected, native_side=side)
            pending[marker]['order_id'] = str(oid) if oid else None
            journal.kv_set(REARM_KEY, json.dumps(pending, allow_nan=False))
            result['rearmed'] += 1
            issues.append('native_protection_awaiting_verification:'+symbol+':'+side)
        for key in held.keys() - owners.keys():
            row = raw[(key[0].split(':')[-1], key[1])]
            if norm_symbol(row['symbol']) not in excluded:
                issues.append('untracked_native_position:'+key[0]+':'+key[1])
        # Leave every stop on an untracked side alone. For a tracked protected
        # side, only obsolete IDs can be swept; for a flat side, all are orphans.
        kept = {str(t.get('sl_order_id')) for t in journal.open_trades() if t.get('sl_order_id')}
        kept.update(str(v['order_id']) for v in pending.values() if isinstance(v, dict) and v.get('order_id'))
        for stop in stops:
            key = ('binance_usdm:futures:'+protective.venue_key(stop['symbol']), stop.get('position_side', 'BOTH'))
            if str(stop['id']) in kept or key in held or norm_symbol(stop['symbol']) in excluded:
                continue
            if protective.cancel_stop(exchange, stop['id'], stop['symbol'], kind=stop['kind']):
                result['stops_swept'] += 1
            else:
                result['stops_stuck'] += 1; issues.append('native_orphan_stop_cleanup_pending')
        journal.kv_set(REARM_KEY, json.dumps(pending, allow_nan=False))
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        if not code or len(code) > 100 or not all(c.islower() or c.isdigit() or c == '_' for c in code):
            code = type(exc).__name__
        issues.append(code)
    return result
