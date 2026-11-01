"""Whole-trade reconciliation from immutable bookings and bounded venue reads.

Demo USDT linear and native-coin inverse perpetual one-way captures are
supported. Native coin results do not enter USDT learning without conversion.
Currency subtotals stay separate; no invented FX, funding or journal P&L.
Capture is manual/off-path. Every retry creates a new self-contained artifact.
"""
from __future__ import annotations

import math
import time

from .accounting import digest, number
from . import booking
from ..core.types import norm_symbol
from ..core.contract_values import Contract

VERSION = 'whole-trade-accounting.v1'
INTERVAL_VERSION = 'whole-trade-accounting.closed-interval.v2'
NATIVE_VERSION = 'whole-trade-accounting.native-coin.v1'
NATIVE_INTERVAL_VERSION = 'whole-trade-accounting.native-coin.closed-interval.v1'
DAY = 86_400_000
LIMIT = 1000
MAX_PAGES = 16


def scalar(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value if isinstance(value, (str, int, float, bool, type(None))) else None


def close(a, b):
    return math.isclose(number(a), number(b), rel_tol=1e-8, abs_tol=1e-8)


def legs(source, *, closed=True):
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
        is_entry = i == 0 or r['kind'] == 'increase'
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
        elif r['kind'] == 'increase':
            if (e.get('basis') != 'recovered_aggregate_position_and_full_stop'
                    or after['status'] != 'open'
                    or not close(after['realized_pnl'], before['realized_pnl'])):
                raise ValueError('increase_order_reference_or_balance_conflict')
            qty = number(after['amount']) - number(before['amount'])
            if (not close(qty, e.get('confirmed_quantity'))
                    or not close(after['amount'], e.get('aggregate_quantity'))):
                raise ValueError('increase_quantity_conflict')
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
        result.append(dict(order_id=oid, quantity=qty, entry=is_entry,
                           receipt_ms=r['observed_ms']))
        previous = after
    if previous != trade:
        raise ValueError('booking_tail_gap_retry')
    remaining = (sum(x['quantity'] for x in result if x['entry'])
                 - sum(x['quantity'] for x in result if not x['entry']))
    expected = 0 if trade['status']=='closed' else number(trade['amount'])
    if ((closed and trade['status'] != 'closed') or trade['status'] not in ('open','closed')
            or (trade['status']=='open' and expected<=0) or not close(remaining,expected)):
        raise ValueError('whole_trade_not_closed_or_balanced_retry')
    return result


def history(exchange, endpoint, symbol, start, end, observed, *, coin=False):
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
            if coin:
                method = method.replace('fapi', 'dapi', 1)
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


def assess(a, *, marked=False):
    result = dict(status='incomplete', reasons=[], learning_eligible=False,
                  fees_by_currency={}, funding_by_currency={}, gross_realized_usdt=None,
                  funding_usdt=None, net_trade_pnl_usdt=None, accounting=None,
                  retry_required=True, history_complete=False)
    try:
        ls = legs(a['bookings'], closed=not marked)
        t, observed = a['bookings']['trade'], a['observed_ms']
        if marked and t['status'] not in ('open', 'closed'):
            raise ValueError('marked_accounting_requires_booked_trade')
        from ..cognition.outcomes import timestamp
        if (timestamp(t['opened_at']) > a['bookings']['receipts'][0]['observed_ms']
                or (t['status']=='closed' and timestamp(t['closed_at']) > a['bookings']['receipts'][-1]['observed_ms'])):
            raise ValueError('journal_booking_clock_conflict')
        native = a.get('schema_version') in (NATIVE_VERSION,NATIVE_INTERVAL_VERSION)
        contract, currency, settlement_precision = Contract('linear'), 'USDT', None
        if native:
            binding = a['contract_binding']
            market = binding['market']
            size = number(market.get('contractSize'))
            from ..core.instrument_registry import InstrumentId
            from ..core.types import MarketType
            iid = InstrumentId('binancecoinm', MarketType.FUTURES, market['id']).value
            if (a['venue'] != 'binancecoinm' or binding.get('instrument_id') != iid
                    or binding.get('quantity_unit') != 'contracts'
                    or binding.get('bookings_sha256') != a['bookings']['sha256']
                    or market.get('symbol') != t['symbol'] or market.get('id') != a['venue_symbol']
                    or not market.get('contract') or market.get('inverse') is not True
                    or market.get('linear') is not False or market.get('quote') != 'USD'
                    or market.get('swap') is not True
                    or not isinstance(market.get('base'), str) or not market['base']
                    or market.get('settle') != market['base'] or size <= 0
                    or not a.get('account_scope')):
                raise ValueError('native_contract_binding_invalid')
            contract, currency = Contract('inverse', size), market['settle']
            if 'settlement_precision' in binding:
                settlement_precision=binding['settlement_precision']
                if type(settlement_precision) is not int or not 0<=settlement_precision<=18:
                    raise ValueError('native_settlement_precision_invalid')
            result.update(native_currency=currency, native_complete=False,
                          gross_realized_native=None, funding_native=None, net_pnl_native=None)
        if a['environment'] != 'demo' or a['venue'] != ('binancecoinm' if native else 'binanceusdm'):
            raise ValueError('unsupported_venue_environment')
        if a['errors']:
            raise ValueError('capture_inputs_missing_retry')
        symbol, start, end = a['venue_symbol'], a['start_ms'], a['end_ms']
        if not native and (symbol != norm_symbol(t['symbol']).replace('/', '') or not t['symbol'].endswith('/USDT')):
            raise ValueError('unsupported_settlement_currency')
        if (type(observed) is not int or type(a['captured_ms']) is not int
                or a['captured_ms'] < observed or a['bookings']['observed_ms'] > observed):
            raise ValueError('capture_clock_conflict')
        interval=a.get('schema_version') in (INTERVAL_VERSION,NATIVE_INTERVAL_VERSION)
        if start<0 or end>observed or end<start or (not interval and end!=observed):
            raise ValueError('capture_clock_conflict')
        if interval and t['status']!='closed':
            raise ValueError('closed_interval_requires_terminal_trade')
        if marked and interval and a['captured_ms']-observed>30_000:
            raise ValueError('marked_closed_cashflow_observation_stale')
        if marked and not interval and not (type(a.get('position_observed_ms')) is int
                and a['bookings']['receipts'][-1]['observed_ms']<=a['position_observed_ms']<=observed
                and a['captured_ms']-a['position_observed_ms']<=30_000):
            raise ValueError('marked_position_observation_stale_or_unbound')
        orders = {str(o['orderId']):o for o in a['orders']}
        if len(orders) != len(ls) or set(orders) != {l['order_id'] for l in ls}:
            raise ValueError('order_versions_missing_retry')
        if interval:
            proof=a['terminal_flat']
            last=orders[ls[-1]['order_id']]
            if (proof.get('schema')!='coordinated-terminal-flat.v1' or proof.get('trade_id')!=t['id']
                    or proof.get('symbol')!=t['symbol'] or str(proof.get('order_id'))!=ls[-1]['order_id']
                    or proof.get('flat') is not True or type(proof.get('quantity')) not in (int,float)
                    or proof['quantity']!=0 or not proof.get('logical_id')
                    or not proof.get('account_scope') or not proof.get('instrument_id')
                    or type(proof.get('request_start_ms')) is not int or type(proof.get('observed_ms')) is not int
                    or not end<=proof['request_start_ms']<=proof['observed_ms']<=ls[-1]['receipt_ms']<=observed
                    or end!=number(last['updateTime'])
                    or a.get('position_source')!='RETAINED_TERMINAL_FLAT_OBSERVATION'):
                raise ValueError('closed_interval_flat_evidence_unbound')
            if native and (proof['account_scope']!=a['account_scope']
                           or proof['instrument_id']!=a['contract_binding']['instrument_id']):
                raise ValueError('native_closed_interval_scope_differs')
        if start != number(orders[ls[0]['order_id']]['time']):
            raise ValueError('entry_history_start_conflict')
        fills = history_rows(a['fill_history'], 'fills', symbol, start, end, observed)
        funding = history_rows(a['funding_history'], 'income', symbol, start, end, observed)
        events = history_rows(a['funding_events'], 'funding_events', symbol, max(0, start-6*DAY), observed if interval else end, observed)
        result['history_complete'] = True
        if not fills:
            raise ValueError('fill_history_empty_retry')
        event_times = [number(e['fundingTime']) for e in events]
        if (not event_times or min(event_times) > min(number(f['time']) for f in fills)
                or (not marked and max(event_times) <= max(number(f['time']) for f in fills))):
            raise ValueError('funding_publication_frontier_pending_retry')
        if a['overlapping_trade_ids']:
            raise ValueError('journal_ownership_overlap_retry')
        # Current zero position plus a complete, balanced one-way fill stream
        # proves zero initial position within the captured interval. The position
        # is read before the history cutoff; any extra fills refuse ownership.
        position = a['position']
        direction = 1 if t['side']=='long' else -1
        expected_quantity = number(t['amount']) if marked and t['status']=='open' else 0.
        if (position['symbol'] != symbol or position['positionSide'] != 'BOTH'
                or not close(number(position['positionAmt']),direction*expected_quantity)):
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
                fee_currency = f['commissionAsset']
                if (qty <= 0 or price <= 0 or f['side'] != side or f['positionSide'] != 'BOTH'
                        or not number(o['time']) <= number(f['time']) <= number(o['updateTime'])
                        or not isinstance(fee_currency, str) or not fee_currency):
                    raise ValueError('fill_fields_or_currency_missing_retry')
                if l['entry'] and pnl != 0:
                    raise ValueError('entry_realized_pnl_ownership_conflict')
                gross += pnl
                fees[fee_currency] = fees.get(fee_currency, 0.0)+fee
                normalized.append(dict(id=str(f['id']), version=digest(f), trade_id=t['id'],
                    event_ms=int(f['time']), available_ms=a['captured_ms'], realized_pnl=pnl,
                    commission=fee, currency=fee_currency))
        # Refuse exits preceding entry, over-closes and funding at an ambiguous
        # fill timestamp. Actual signed income is a cashflow, never a rate estimate.
        for f in fills:
            balance += number(f['qty'])*(1 if f['side'] == entry_side else -1)
            if balance < -1e-8:
                raise ValueError('fill_sequence_ownership_conflict')
        if not close(balance, expected_quantity):
            raise ValueError('whole_trade_fill_balance_conflict')
        cash_gross = sum(contract.cashflow_native(number(f['qty']),number(f['price']),f['side']) for f in fills)
        unrealized = 0.
        def pnl_close(actual,expected,count=1):
            if settlement_precision is None:return close(actual,expected)
            # Each venue-reported subtotal can lose at most one declared
            # settlement unit through rounding/truncation. Do not treat many
            # individually rounded fills as one unrounded wallet value.
            return math.isclose(number(actual),number(expected),rel_tol=1e-8,
                                abs_tol=count*10.**(-settlement_precision))
        if marked:
            mark,entry = number(position['markPrice']),number(position['entryPrice'])
            unrealized = number(position['unRealizedProfit'])
            if ((expected_quantity and (mark<=0 or entry<=0))
                    or not pnl_close(unrealized,contract.pnl_native(direction*expected_quantity,entry,mark))):
                raise ValueError('marked_price_pnl_conflict')
            cash_gross += contract.cashflow_native(expected_quantity,mark,'SELL' if direction>0 else 'BUY')
        pnl_count=sum(f['side']!=entry_side for f in fills)+int(marked and bool(expected_quantity))
        if not pnl_close(gross+unrealized, cash_gross,max(1,pnl_count)):
            raise ValueError('fill_price_pnl_conflict')
        if settlement_precision is not None:
            result['pnl_reconciliation_tolerance_native']=max(1,pnl_count)*10.**(-settlement_precision)
        result.update(fees_by_currency=fees)
        result['gross_realized_native' if native else 'gross_realized_usdt'] = gross
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
        if set(fees)-{currency} or set(currencies)-{currency}:
            raise ValueError('currency_conversion_missing_retry')
        funding_net = currencies.get(currency, 0.0)
        net = gross+unrealized-fees.get(currency, 0.0)+funding_net
        if not all(math.isfinite(v) for v in (gross, funding_net, net, *fees.values())):
            raise ValueError('nonfinite_accounting_aggregate')
        if native:
            # Currency-aware facts are usable by the native book adapter, not
            # by the USDT learning adapter without a verified conversion.
            result.update(status='marked_native_venue_history' if marked else 'complete_native_venue_history',
                          native_complete=True, funding_native=funding_net, net_pnl_native=net,
                          unrealized_native=unrealized, retry_required=False,
                          final_realized_outcome=not marked, learning_eligible=False,
                          reasons=['reporting_currency_conversion_required'])
            if marked:
                result.update(marked_notional_native=contract.settlement_notional(expected_quantity,mark)
                              if expected_quantity else 0.,
                              mark_observed_ms=observed if interval else a['position_observed_ms'])
            return result
        if marked:
            result.update(status='marked_as_of_venue_history',funding_usdt=funding_net,
                          marked_net_pnl_usdt=net,unrealized_usdt=unrealized,
                          marked_notional_usdt=expected_quantity*mark,
                          mark_observed_ms=observed if interval else a['position_observed_ms'],retry_required=False,
                          basis=('CLOSED_INTERVAL_CURRENT_CASHFLOW_RECEIPTS' if interval else
                                 'CURRENT_VENUE_MARK_AND_OBSERVED_FILL_FEES_AND_SIGNED_FUNDING'),
                          final_realized_outcome=False)
            return result
        accounting = dict(schema_version='execution-accounting.v1', complete=True,
            trade_id=t['id'], symbol=t['symbol'], venue=a['venue'], environment=a['environment'],
            reconciliation_version=a['schema_version'], fills=normalized, currency='USDT',
            funding_complete=True, funding_net=funding_net, resolved_ms=a['captured_ms'],
            available_ms=a['captured_ms'])
        result.update(status='complete_as_of_venue_history', learning_eligible=True,
                      funding_usdt=funding_net, net_trade_pnl_usdt=net, accounting=accounting,
                      retry_required=False)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
        result['reasons'] = [str(exc) if isinstance(exc, ValueError) else 'malformed_or_missing_source_retry']
    return result


def capture(source, exchange, overlapping_trade_ids=(), *, marked=False,terminal_flat=None):
    """Bounded demo reads; no journal writes, trade submission or automatic retry."""
    a = dict(schema_version=INTERVAL_VERSION if terminal_flat is not None else VERSION, bookings=source, venue='binanceusdm', environment='demo',
             observed_ms=int(time.time()*1000), orders=[], position=None, errors=[],
             overlapping_trade_ids=list(overlapping_trade_ids))
    try:
        ls = legs(source, closed=not marked)
        coin = getattr(exchange, 'id', None) == 'binancecoinm'
        if coin:
            from ..observability.portfolio_observation import trading_source
            from ..data.feed import execution_account_scope
            from ..core.instrument_registry import InstrumentId
            from ..core.types import MarketType
            environment, _ = trading_source(exchange)
            if environment != 'demo':
                raise ValueError('native_capture_demo_only')
            market = exchange.market(source['trade']['symbol'])
            symbol = market['id']
            a.update(schema_version=NATIVE_INTERVAL_VERSION if terminal_flat is not None else NATIVE_VERSION,
                     venue='binancecoinm',environment=environment,
                     account_scope=execution_account_scope(exchange),contract_binding=dict(
                         market={k:market.get(k) for k in ('symbol','id','base','quote','settle','contract','inverse','linear','contractSize','swap')},
                         instrument_id=InstrumentId('binancecoinm',MarketType.FUTURES,symbol).value,
                         quantity_unit='contracts',bookings_sha256=source['sha256']))
            if not a['account_scope']:
                raise ValueError('native_account_scope_unavailable')
            info=market.get('info') or {}
            if 'baseAssetPrecision' in info:
                precision=info['baseAssetPrecision']
                if type(precision) is not int or not 0<=precision<=18:
                    raise ValueError('native_settlement_precision_invalid')
                a['contract_binding']['settlement_precision']=precision
            if market.get('swap') is not True:
                raise ValueError('native_perpetual_contract_required')
        else:
            symbol = source['trade']['symbol'].replace('/', '')
        a['venue_symbol'] = symbol
        for l in ls:
            raw = getattr(exchange,'dapiPrivateGetOrder' if coin else 'fapiPrivateGetOrder')(dict(symbol=symbol, orderId=l['order_id']))
            a['orders'].append({k:scalar(raw.get(k)) for k in ('orderId','symbol','side','positionSide',
                                'status','executedQty','time','updateTime')})
        a['start_ms'] = int(number(a['orders'][0]['time']))
        if terminal_flat is None:
            params = dict(pair=market['base']+market['quote']) if coin else dict(symbol=symbol)
            positions = getattr(exchange,'dapiPrivateGetPositionRisk' if coin else 'fapiPrivateV2GetPositionRisk')(params)
            if marked:a['position_observed_ms']=int(time.time()*1000)
            ps = [p for p in positions if p.get('symbol') == symbol]
            if len(ps) != 1:
                raise ValueError('one_way_position_snapshot_missing')
            fields = ('symbol','positionSide','positionAmt')
            if marked:fields += ('entryPrice','markPrice','unRealizedProfit')
            a['position'] = {k:scalar(ps[0].get(k)) for k in fields}
        else:
            if source['trade']['status']!='closed':
                raise ValueError('closed_interval_requires_terminal_trade')
            a.update(terminal_flat=terminal_flat,position_source='RETAINED_TERMINAL_FLAT_OBSERVATION',
                position=dict(symbol=symbol,positionSide='BOTH',positionAmt=terminal_flat['quantity'],
                              entryPrice=0.,markPrice=0.,unRealizedProfit=0.))
        a['observed_ms'] = int(time.time()*1000)
        a['end_ms'] = int(number(a['orders'][-1]['updateTime'])) if terminal_flat is not None else a['observed_ms']
        for key, endpoint in (('fill_history','fills'), ('funding_history','income'), ('funding_events','funding_events')):
            history_start = max(0, a['start_ms']-6*DAY) if endpoint == 'funding_events' else a['start_ms']
            history_end=a['observed_ms'] if terminal_flat is not None and endpoint=='funding_events' else a['end_ms']
            a[key] = history(exchange, endpoint, symbol, history_start, history_end, a['observed_ms'],coin=coin)
    except Exception as exc:
        a['errors'].append('capture_failed:'+type(exc).__name__)
    a['captured_ms'] = int(time.time()*1000)
    if marked:a['capture_kind']='CLOSED_INTERVAL_MARKED_ONLY' if terminal_flat is not None else 'OPEN_POSITION_MARKED_ONLY'
    a['assessment'] = assess(a,marked=marked)
    a['sha256'] = digest(a)
    return a


def replay(a):
    if a.get('schema_version') not in (VERSION,INTERVAL_VERSION,NATIVE_VERSION,NATIVE_INTERVAL_VERSION) or digest({k:v for k,v in a.items() if k != 'sha256'}) != a.get('sha256'):
        raise ValueError('whole_trade_integrity')
    expected = assess(a,marked=a.get('capture_kind') in ('OPEN_POSITION_MARKED_ONLY','CLOSED_INTERVAL_MARKED_ONLY'))
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
