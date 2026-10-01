"""Read-only exact execution calibration primitives. No validated cost model.

Static book walks are observations, never realized slippage or impact proof.
This module is not wired to Kernel, Risk, orders or paper execution.
"""
from decimal import Decimal

from trader.engine import execution_evidence as E
from trader.engine.paper_exit_evidence import digest as _digest, canonical

def digest(body):
    return _digest(canonical(body))
from trader.observability import depth_evidence as D


def positive(value):
    if isinstance(value, bool):
        raise ValueError('invalid_quantity_or_price')
    d = Decimal(str(value))
    if not d.is_finite() or d <= 0:
        raise ValueError('invalid_quantity_or_price')
    return d


def book_walk(snapshot, side, requested_quantity):
    if side not in ('buy', 'sell') or snapshot.get('schema') != D.SCHEMA:
        raise ValueError('book_side_or_schema_invalid')
    for key in ('symbol', 'source_endpoint', 'response_sha256', 'environment'):
        if not snapshot.get(key):
            raise ValueError('snapshot_identity_missing')
    for key in ('venue_event_ms', 'venue_transaction_ms', 'received_ms', 'request_start_ms', 'last_update_id'):
        if type(snapshot.get(key)) is not int or snapshot[key] <= 0:
            raise ValueError('snapshot_clock_missing')
    bids = D._side(snapshot['bids'], snapshot['configured_depth'], descending=True)
    asks = D._side(snapshot['asks'], snapshot['configured_depth'], descending=False)
    if positive(bids[0][0]) >= positive(asks[0][0]):
        raise ValueError('crossed_book')
    levels = asks if side == 'buy' else bids
    requested = positive(requested_quantity)
    remaining, cost, consumed = requested, Decimal(0), []
    for price, quantity in levels:
        taken = min(remaining, positive(quantity))
        cost += positive(price) * taken
        consumed.append(dict(price=price, quantity=str(taken)))
        remaining -= taken
        if remaining == 0:
            break
    covered = remaining == 0
    vwap = cost / requested if covered else None
    top = positive(levels[0][0])
    return dict(label='STATIC_BOOK_QUOTE', qualifications=['NOT_REALIZED_SLIPPAGE', 'NOT_MARKET_IMPACT_PROOF'],
        covered=covered, requested_quantity=str(requested), covered_quantity=str(requested-remaining),
        book_vwap=str(vwap) if covered else None, top_price=str(top),
        static_book_price_difference=str(abs(vwap-top)) if covered else None,
        levels_consumed=consumed, snapshot_identity=digest(snapshot), snapshot=snapshot)


def exact_orders(query):
    """Strengthen existing leg linkage with exact market/symbol/order/trade IDs.
    Never repair attribution by searching a symbol/time window.
    """
    def strict(sql, params):
        rows = query(sql, params)
        if sql.startswith('SELECT * FROM trade_fills WHERE leg_id='):
            legs = query('SELECT * FROM trade_legs WHERE id=?', (params[0],))
            if len(legs) != 1:
                return []
            leg = legs[0]
            scoped = query('SELECT id FROM trade_legs WHERE market_type=? AND symbol=? AND venue_order_id=?',
                           (leg['market_type'], leg['symbol'], leg['venue_order_id']))
            if len(scoped) != 1:
                return []
            rows = [f for f in rows if all(f.get(k) == leg.get(k) for k in
                    ('market_type', 'symbol', 'venue_order_id', 'trade_id', 'side'))
                    and f.get('venue_fill_id') is not None]
        return rows
    return E.executed_orders(strict)


def dataset(query):
    observations = exact_orders(query)
    matched = []
    for order in observations:
        ref = order['reference']
        # Exact captured timestamps required; journal timestamps are not substituted.
        try:
            decision_ms = ref.get('observed_at')
            from trader.engine.paper_cost_evidence import ms
            decision_ms = ms(decision_ms) if isinstance(decision_ms, str) else decision_ms
            submitted = order['submitted_ms']
            valid = (type(decision_ms) is int and type(submitted) is int
                and decision_ms <= submitted and order['first_fill_ms'] is not None
                and submitted <= order['first_fill_ms']
                and all(type(f['venue_ts_ms']) is int for f in order['fills']))
        except (ValueError, TypeError):
            valid = False
        if (not valid or order['slippage']['status'] != E.MEASURED
                or order['commission_status'] != 'VERIFIED' or not order['requested_qty']):
            continue
        matched.append(dict(order, decision_ms=decision_ms,
            decision_to_submission_ms=submitted-decision_ms,
            submission_to_first_fill_ms=order['first_fill_ms']-submitted,
            submission_to_last_fill_ms=order['last_fill_ms']-submitted,
            requested_fill_fraction=order['fill_qty']/order['requested_qty'],
            book=None, static_book_prediction=None, realized_deviation=None,
            size_depth_ratio=None, spread_regime=None, volatility_regime=None,
            rejection_coverage='UNAVAILABLE', cancellation_coverage='UNAVAILABLE'))
    return dict(schema='exact-execution-calibration.v1', observations=observations, orders=matched,
        matched_orders=len(matched), instruments=sorted({o['instrument_id'] or o['symbol'] for o in matched}),
        sides=sorted({o['side'] for o in matched}), slippage_model='NOT_VALIDATED',
        reason='NO_VALIDATED_EXECUTION_COST_METHOD; no minimum N grants authority')


def inventory(query):
    """Per-instrument evidence counts. Journal trade rows are never fill evidence."""
    tables = {r['name'] for r in query("SELECT name FROM sqlite_master WHERE type='table'", ())}
    legs = query('SELECT * FROM trade_legs', ()) if 'trade_legs' in tables else []
    fills = query('SELECT * FROM trade_fills', ()) if 'trade_fills' in tables else []
    data = dataset(query)
    groups = {}
    for symbol in sorted({r['symbol'] for r in legs+fills}):
        ls = [r for r in legs if r['symbol']==symbol]
        fs = [r for r in fills if r['symbol']==symbol]
        os = [r for r in data['observations'] if r['symbol']==symbol]
        groups[symbol] = dict(order_legs=len(ls), real_fill_rows=len(fs),
            attributed_fill_rows=sum(f['attribution']=='ATTRIBUTED' for f in fs),
            requested_quantity=sum(l['requested_qty'] is not None for l in ls),
            reference_prices=sum(o['reference']['status']=='RECORDED' for o in os),
            decision_timestamps=sum(o['reference'].get('observed_at') is not None for o in os),
            submission_timestamps=sum(o['submitted_ms'] is not None for o in os),
            complete_fill_orders=sum(o['fill_coverage']=='COMPLETE' for o in os),
            commissions=sum(o['commission_status']=='VERIFIED' for o in os),
            partial_fill_orders=sum(o['fill_coverage']=='PARTIAL' or
                (o['fill_qty'] is not None and o['requested_qty'] is not None and o['fill_qty']<o['requested_qty']) for o in os),
            matched_orders=sum(o['symbol']==symbol for o in data['orders']),
            event_linked_books=0, spread='UNAVAILABLE', cancellations='UNAVAILABLE', rejections='UNAVAILABLE')
    return dict(tables=sorted(tables), total_legs=len(legs), total_fill_rows=len(fills),
                by_instrument=groups, calibration=data)
