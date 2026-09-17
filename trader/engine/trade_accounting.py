"""Whole-trade reconciliation from immutable bookings and bounded venue reads.

Only demo USDT linear, one-way positions are supported. Currency subtotals are
kept separate; no invented FX rates, funding rates or journal P&L substitutes.
Capture is manual/off-path. Every retry creates a new self-contained artifact.
"""
from __future__ import annotations

import math
import time

from .accounting import digest, number
from . import booking
from ..core.types import norm_symbol

VERSION = 'whole-trade-accounting.v1'
DAY = 86_400_000
LIMIT = 1000
MAX_PAGES = 16


def scalar(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if isinstance(value, (str, int, float, bool, type(None))) else None


def close(a, b):
    return math.isclose(number(a), number(b), rel_tol=1e-8, abs_tol=1e-8)


def legs(source):
    """Derive ownership from booking transitions, never a symbol/time subtotal."""
    booking.replay_export(source)
    rs, trade = source['receipts'], source['trade']
    if not rs or rs[0]['kind'] != 'entry' or rs[0]['before'] is not None:
        raise ValueError('legacy_entry_receipt_missing_retry')
    if trade.get('side') not in ('long', 'short') or trade.get('market_type') != 'futures' or trade.get('exec_mode') != 'live':
        raise ValueError('unsupported_trade_scope')
    if len(rs) > 32:
        raise ValueError('order_capture_budget_retry')
    result, previous, seen = [], None, set()
    identity = ('id', 'decision_id', 'symbol', 'side', 'strategy_id', 'opened_at', 'market_type', 'exec_mode')
    for i, r in enumerate(rs):
        after, before, e = r['after'], r['before'], r['evidence']
        if any(after.get(k) != trade.get(k) for k in identity):
            raise ValueError('booking_owner_conflict')
        if before != previous or (previous and previous['status'] != 'open'):
            raise ValueError('booking_chain_gap_retry')
        if i == 0:
            if e.get('basis') not in ('entry_order_confirmation', 'recovered_entry_order_confirmation') or after['status'] != 'open':
                raise ValueError('entry_order_reference_missing_retry')
            qty = number(after['amount'])
            if not close(qty, e.get('confirmed_quantity')):
                raise ValueError('entry_quantity_conflict')
        else:
            if e.get('basis') != 'venue_order_fills':
                raise ValueError('exit_order_reference_missing_retry')
            if r['kind'].startswith('close:') and after['status'] == 'closed':
                qty = number(before['amount'])
            elif r['kind'] == 'align_delta' and after['status'] == 'open':
                qty = number(before['amount']) - number(after['amount'])
            else:
                raise ValueError('ambiguous_booking_transition_retry')
            if not close(qty, e.get('quantity')):
                raise ValueError('exit_quantity_conflict')
        oid = str(e.get('order_id') or '')
        if not oid or oid in seen or qty <= 0:
            raise ValueError('missing_or_reused_order_identity')
        seen.add(oid)
        result.append(dict(order_id=oid, quantity=qty, entry=i == 0,
                           receipt_ms=r['observed_ms']))
        previous = after
    if previous != trade:
        raise ValueError('booking_tail_gap_retry')
    if trade['status'] != 'closed' or not close(result[0]['quantity'], sum(x['quantity'] for x in result[1:])):
        raise ValueError('whole_trade_not_closed_or_balanced_retry')
    return result


def history(exchange, endpoint, symbol, start, end, observed):
    """Split saturated time windows instead of skipping equal-timestamp rows.

    A short response proves endpoint enumeration only, not publication finality.
    Conservative 80-day retention and 6-day requests fit both API contracts.
    """
    out = dict(endpoint=endpoint, symbol=symbol, start_ms=start, end_ms=end,
               observed_ms=observed, pages=[], errors=[])
    if not 0 <= start <= end <= observed or start < observed-80*DAY:
        out['errors'].append('history_outside_supported_retention_retry')
        return out
    windows = [(s, min(end, s+6*DAY-1)) for s in range(start, end+1, 6*DAY)]
    while windows and len(out['pages']) < MAX_PAGES:
        lo, hi = windows.pop(0)
        params = dict(symbol=symbol, startTime=lo, endTime=hi, limit=LIMIT)
        if endpoint == 'income':
            params['incomeType'] = 'FUNDING_FEE'
        try:
            method = {'fills': 'fapiPrivateGetUserTrades', 'income': 'fapiPrivateGetIncome',
                      'funding_events': 'fapiPublicGetFundingRate'}[endpoint]
            raw = getattr(exchange, method)(params)
            if not isinstance(raw, list) or any(not isinstance(r, dict) for r in raw):
                raise ValueError('invalid_history_response')
            fields = (('id', 'orderId', 'symbol', 'side', 'positionSide', 'time', 'qty',
                       'price', 'realizedPnl', 'commission', 'commissionAsset') if endpoint == 'fills'
                      else ('fundingTime', 'symbol', 'fundingRate') if endpoint == 'funding_events'
                      else ('tranId', 'symbol', 'incomeType', 'time', 'income', 'asset'))
            rows = [{k: scalar(r.get(k)) for k in fields} for r in raw]
            out['pages'].append(dict(start_ms=lo, end_ms=hi, rows=rows))
            if len(rows) >= LIMIT:
                if lo == hi:
                    out['errors'].append('history_timestamp_saturated_retry')
                else:
                    mid = (lo+hi)//2
                    windows[0:0] = [(lo, mid), (mid+1, hi)]
        except Exception as exc:
            out['errors'].append('history_fetch_failed:'+type(exc).__name__)
            break
    if windows:
        out['errors'].append('history_page_budget_retry')
    return out


def history_rows(h, endpoint, symbol, start, end, observed):
    if (h['endpoint'] != endpoint or h['symbol'] != symbol or h['start_ms'] != start
            or h['end_ms'] != end or h['observed_ms'] != observed or h['errors']):
        raise ValueError(endpoint+'_history_incomplete_retry')
    cursor, rows, seen = start, [], {}
    leaves = sorted((p for p in h['pages'] if len(p['rows']) < LIMIT), key=lambda p:p['start_ms'])
    for p in leaves:
        if p['start_ms'] != cursor or not cursor <= p['end_ms'] <= end:
            raise ValueError(endpoint+'_history_coverage_gap_retry')
        cursor = p['end_ms']+1
        for r in p['rows']:
            key = r.get('id' if endpoint == 'fills' else 'fundingTime' if endpoint == 'funding_events' else 'tranId')
            if key is None or isinstance(key, bool) or str(key) == '':
                raise ValueError(endpoint+'_identity_missing_retry')
            if not p['start_ms'] <= number(r.get('fundingTime' if endpoint == 'funding_events' else 'time')) <= p['end_ms'] or r.get('symbol') != symbol:
                raise ValueError(endpoint+'_history_row_scope_conflict')
            if str(key) in seen:
                if seen[str(key)] != r:
                    raise ValueError(endpoint+'_identity_conflict')
                continue
            seen[str(key)] = r
            rows.append(r)
    for p in h['pages']:
        if len(p['rows']) >= LIMIT:
            for r in p['rows']:
                key = str(r.get('id' if endpoint == 'fills' else 'fundingTime' if endpoint == 'funding_events' else 'tranId'))
                if seen.get(key) != r:
                    raise ValueError(endpoint+'_history_changed_during_capture_retry')
    if cursor != end+1 or start < observed-80*DAY or end > observed:
        raise ValueError(endpoint+'_history_coverage_gap_retry')
    return sorted(rows, key=lambda r:(number(r.get('time', r.get('fundingTime'))), str(r.get('id', r.get('tranId')))))


def assess(a):
    result = dict(status='incomplete', reasons=[], learning_eligible=False,
                  fees_by_currency={}, funding_by_currency={}, gross_realized_usdt=None,
                  funding_usdt=None, net_trade_pnl_usdt=None, accounting=None,
                  retry_required=True, history_complete=False)
    try:
        ls = legs(a['bookings'])
        t, observed = a['bookings']['trade'], a['observed_ms']
        from ..cognition.outcomes import timestamp
        if (timestamp(t['opened_at']) > a['bookings']['receipts'][0]['observed_ms']
                or timestamp(t['closed_at']) > a['bookings']['receipts'][-1]['observed_ms']):
            raise ValueError('journal_booking_clock_conflict')
        if a['environment'] != 'demo' or a['venue'] != 'binanceusdm':
            raise ValueError('unsupported_venue_environment')
        if a['errors']:
            raise ValueError('capture_inputs_missing_retry')
        symbol, start, end = a['venue_symbol'], a['start_ms'], a['end_ms']
        if symbol != norm_symbol(t['symbol']).replace('/', '') or not t['symbol'].endswith('/USDT'):
            raise ValueError('unsupported_settlement_currency')
        if (type(observed) is not int or type(a['captured_ms']) is not int
                or a['captured_ms'] < observed or a['bookings']['observed_ms'] > observed):
            raise ValueError('capture_clock_conflict')
        if end != observed or start < 0:
            raise ValueError('capture_clock_conflict')
        orders = {str(o['orderId']):o for o in a['orders']}
        if len(orders) != len(ls) or set(orders) != {l['order_id'] for l in ls}:
            raise ValueError('order_versions_missing_retry')
        if start != number(orders[ls[0]['order_id']]['time']):
            raise ValueError('entry_history_start_conflict')
        fills = history_rows(a['fill_history'], 'fills', symbol, start, end, observed)
        funding = history_rows(a['funding_history'], 'income', symbol, start, end, observed)
        events = history_rows(a['funding_events'], 'funding_events', symbol, max(0, start-6*DAY), end, observed)
        result['history_complete'] = True
        if not fills:
            raise ValueError('fill_history_empty_retry')
        event_times = [number(e['fundingTime']) for e in events]
        if (not event_times or min(event_times) > min(number(f['time']) for f in fills)
                or max(event_times) <= max(number(f['time']) for f in fills)):
            raise ValueError('funding_publication_frontier_pending_retry')
        if a['overlapping_trade_ids']:
            raise ValueError('journal_ownership_overlap_retry')
        # Current zero position plus a complete, balanced one-way fill stream
        # proves zero initial position within the captured interval. The position
        # is read before the history cutoff; any extra fills refuse ownership.
        position = a['position']
        if (position['symbol'] != symbol or position['positionSide'] != 'BOTH'
                or number(position['positionAmt']) != 0):
            raise ValueError('venue_flat_ownership_unproven_retry')
        if set(str(f['orderId']) for f in fills) != set(orders):
            raise ValueError('unowned_or_missing_fills_retry')
        gross, fees, balance = 0.0, {}, 0.0
        entry_side = 'BUY' if t['side'] == 'long' else 'SELL'
        normalized = []
        for l in ls:
            o = orders[l['order_id']]
            side = entry_side if l['entry'] else ('SELL' if entry_side == 'BUY' else 'BUY')
            if (o['symbol'] != symbol or o['side'] != side or o['positionSide'] != 'BOTH'
                    or o['status'] not in ('FILLED','CANCELED','EXPIRED','EXPIRED_IN_MATCH')
                    or not close(o['executedQty'], l['quantity'])
                    or not start <= number(o['time']) <= number(o['updateTime']) <= l['receipt_ms'] <= observed):
                raise ValueError('order_owner_quantity_or_clock_conflict')
            fs = [f for f in fills if str(f['orderId']) == l['order_id']]
            if not close(sum(number(f['qty']) for f in fs), l['quantity']):
                raise ValueError('order_fill_quantity_missing_retry')
            for f in fs:
                qty, price = number(f['qty']), number(f['price'])
                pnl, fee = number(f['realizedPnl']), number(f['commission'])
                currency = f['commissionAsset']
                if (qty <= 0 or price <= 0 or f['side'] != side or f['positionSide'] != 'BOTH'
                        or not number(o['time']) <= number(f['time']) <= number(o['updateTime'])
                        or not isinstance(currency, str) or not currency):
                    raise ValueError('fill_fields_or_currency_missing_retry')
                if l['entry'] and pnl != 0:
                    raise ValueError('entry_realized_pnl_ownership_conflict')
                gross += pnl
                fees[currency] = fees.get(currency, 0.0)+fee
                normalized.append(dict(id=str(f['id']), version=digest(f), trade_id=t['id'],
                    event_ms=int(f['time']), available_ms=a['captured_ms'], realized_pnl=pnl,
                    commission=fee, currency=currency))
        # Refuse exits preceding entry, over-closes and funding at an ambiguous
        # fill timestamp. Actual signed income is a cashflow, never a rate estimate.
        for f in fills:
            balance += number(f['qty'])*(1 if f['side'] == entry_side else -1)
            if balance < -1e-8:
                raise ValueError('fill_sequence_ownership_conflict')
        if not close(balance, 0):
            raise ValueError('whole_trade_fill_balance_conflict')
        cash_gross = sum(number(f['qty'])*number(f['price'])*(1 if f['side'] == 'SELL' else -1) for f in fills)
        if not close(gross, cash_gross):
            raise ValueError('fill_price_pnl_conflict')
        result.update(fees_by_currency=fees, gross_realized_usdt=gross)
        currencies = {}
        for r in funding:
            if r['incomeType'] != 'FUNDING_FEE' or not isinstance(r['asset'], str) or not r['asset']:
                raise ValueError('funding_fields_missing_retry')
            ts = number(r['time'])
            if any(number(f['time']) == ts for f in fills):
                raise ValueError('funding_fill_boundary_ambiguous_retry')
            exposure = sum(number(f['qty'])*(1 if f['side'] == entry_side else -1)
                           for f in fills if number(f['time']) < ts)
            if exposure <= 1e-8:
                raise ValueError('funding_without_owned_exposure_retry')
            currencies[r['asset']] = currencies.get(r['asset'], 0.0)+number(r['income'])
        result['funding_by_currency'] = currencies
        expected_times = set()
        for event in events:
            ts = number(event['fundingTime'])
            number(event['fundingRate'])
            if any(number(f['time']) == ts for f in fills):
                raise ValueError('funding_fill_boundary_ambiguous_retry')
            exposure = sum(number(f['qty'])*(1 if f['side'] == entry_side else -1)
                           for f in fills if number(f['time']) < ts)
            if exposure > 1e-8:
                expected_times.add(ts)
        actual_times = [number(r['time']) for r in funding]
        if set(actual_times) != expected_times or len(actual_times) != len(expected_times):
            raise ValueError('funding_event_cashflow_missing_or_ambiguous_retry')
        if set(fees)-{'USDT'} or set(currencies)-{'USDT'}:
            raise ValueError('currency_conversion_missing_retry')
        funding_net = currencies.get('USDT', 0.0)
        net = gross-fees.get('USDT', 0.0)+funding_net
        if not all(math.isfinite(v) for v in (gross, funding_net, net, *fees.values())):
            raise ValueError('nonfinite_accounting_aggregate')
        accounting = dict(schema_version='execution-accounting.v1', complete=True,
            trade_id=t['id'], symbol=t['symbol'], venue=a['venue'], environment=a['environment'],
            reconciliation_version=VERSION, fills=normalized, currency='USDT',
            funding_complete=True, funding_net=funding_net, resolved_ms=a['captured_ms'],
            available_ms=a['captured_ms'])
        result.update(status='complete_as_of_venue_history', learning_eligible=True,
                      funding_usdt=funding_net, net_trade_pnl_usdt=net, accounting=accounting,
                      retry_required=False)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
        result['reasons'] = [str(exc) if isinstance(exc, ValueError) else 'malformed_or_missing_source_retry']
    return result


def capture(source, exchange, overlapping_trade_ids=()):
    """Bounded demo reads; no journal writes, trade submission or automatic retry."""
    a = dict(schema_version=VERSION, bookings=source, venue='binanceusdm', environment='demo',
             observed_ms=int(time.time()*1000), orders=[], position=None, errors=[],
             overlapping_trade_ids=list(overlapping_trade_ids))
    try:
        ls = legs(source)
        symbol = source['trade']['symbol'].replace('/', '')
        a['venue_symbol'] = symbol
        for l in ls:
            raw = exchange.fapiPrivateGetOrder(dict(symbol=symbol, orderId=l['order_id']))
            a['orders'].append({k:scalar(raw.get(k)) for k in ('orderId','symbol','side','positionSide',
                                'status','executedQty','time','updateTime')})
        a['start_ms'] = int(number(a['orders'][0]['time']))
        positions = exchange.fapiPrivateV2GetPositionRisk(dict(symbol=symbol))
        ps = [p for p in positions if p.get('symbol') == symbol]
        if len(ps) != 1:
            raise ValueError('one_way_position_snapshot_missing')
        a['position'] = {k:scalar(ps[0].get(k)) for k in ('symbol','positionSide','positionAmt')}
        a['observed_ms'] = int(time.time()*1000)
        a['end_ms'] = a['observed_ms']
        for key, endpoint in (('fill_history','fills'), ('funding_history','income'), ('funding_events','funding_events')):
            history_start = max(0, a['start_ms']-6*DAY) if endpoint == 'funding_events' else a['start_ms']
            a[key] = history(exchange, endpoint, symbol, history_start, a['end_ms'], a['observed_ms'])
    except Exception as exc:
        a['errors'].append('capture_failed:'+type(exc).__name__)
    a['captured_ms'] = int(time.time()*1000)
    a['assessment'] = assess(a)
    a['sha256'] = digest(a)
    return a


def replay(a):
    if a.get('schema_version') != VERSION or digest({k:v for k,v in a.items() if k != 'sha256'}) != a.get('sha256'):
        raise ValueError('whole_trade_integrity')
    expected = assess(a)
    if expected != a['assessment']:
        raise ValueError('whole_trade_replay_mismatch')
    return expected


def verified_outcome(a, imported_ms):
    """Only complete receipts enter the adapter; retain raw evidence for replay."""
    from ..cognition.outcomes import verified_execution, _record
    result = replay(a)
    if not result['learning_eligible']:
        raise ValueError('incomplete_accounting_retry')
    t = a['bookings']['trade']
    registration = dict(trade_id=t['id'], symbol=t['symbol'], venue=a['venue'],
                        environment=a['environment'], registered_ms=a['start_ms'])
    outcome = verified_execution(registration, result['accounting'], imported_ms)
    return _record(outcome['kind'], {'whole_trade_capture': a}, outcome['registered_ms'],
                   outcome['resolved_ms'], outcome['available_ms'], imported_ms,
                   actual=outcome['actual_execution'])
