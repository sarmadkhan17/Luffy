"""Whole-trade reconciliation for a FLAT emergency exit, from the immutable
archived `emergency-accounting-intent.v1` record (see accounting.py).

accounting.py's own capture/replay only proves the exact close orders it was
told about, fills-only, funding forever unknown ("emergency receipt"). This
module extends that same archived intent to the *whole* trade's fills and
signed funding, reusing trade_accounting.py's bounded history/order/fill
verification machinery unchanged (trade_accounting.py itself is frozen v1 and
is not modified here).

There is no `trade-booking.v1` receipt chain for an emergency exit — the
position was never journalled as open before the recovery barrier released
it flat. So the per-leg order/quantity breakdown below (`derive_legs`) is an
explicit, labelled DERIVED reconstruction: rebuilt deterministically from the
raw archived intent's `entry_observation` / `close_orders` on every capture
AND every replay (never cached/trusted from a prior run), and never written
back to the journal or presented as an actual trade-booking receipt.

Conservatively NOT learning-eligible: this module never emits a
`verified_outcome` and never touches the outcomes/learning memory. It is a
manual, off-path diagnostic extension for owner visibility into unwound
emergency exits, not a new input to strategy learning.
"""
from __future__ import annotations

import math
import time

from .accounting import digest, number
from . import trade_accounting as WT
from ..core.types import norm_symbol

VERSION = 'emergency-whole-accounting.v1'
DAY = WT.DAY
LIMIT = WT.LIMIT
MAX_PAGES = WT.MAX_PAGES
MAX_ORDERS = 32
SCAN_INT_FIELDS = ('trades_scanned', 'intents_scanned')
SCAN_BOOL_FIELDS = ('truncated', 'active_recovery_conflict')
SCAN_LIST_FIELDS = ('malformed_intent_ids', 'malformed_trade_ids')


def validate_overlap_scan(scan):
    """A missing/omitted scan is unknown, never proof of no conflict — this
    must raise rather than let an absent scan default to "all clear".

    Every count/flag is checked for its exact type (`type(x) is int`, not
    `isinstance`, so booleans — which are ints in Python — don't slip
    through) and its value; an empty dict or partial metadata refuses just
    like a fully-populated-but-positive scan does.
    """
    if not isinstance(scan, dict):
        raise ValueError('overlap_scan_missing_retry')
    for field in SCAN_INT_FIELDS:
        v = scan.get(field)
        if type(v) is not int or v < 0:
            raise ValueError('overlap_scan_incomplete_or_malformed_retry')
    for field in SCAN_BOOL_FIELDS:
        if type(scan.get(field)) is not bool:
            raise ValueError('overlap_scan_incomplete_or_malformed_retry')
    for field in SCAN_LIST_FIELDS:
        if not isinstance(scan.get(field), list):
            raise ValueError('overlap_scan_incomplete_or_malformed_retry')
    if (scan['truncated'] or scan['active_recovery_conflict']
            or scan['malformed_intent_ids'] or scan['malformed_trade_ids']):
        raise ValueError('overlap_scan_incomplete_or_malformed_retry')


def derive_legs(intent):
    """DERIVED order legs rebuilt deterministically from the raw archived intent.

    Not a trade-booking.v1 replay — there is no booking chain for an
    emergency exit. Every field checked here mirrors trade_accounting.legs()'s
    identity/quantity/terminal-status invariants so the two paths refuse on
    the same classes of missing/conflicting evidence.
    """
    if intent.get('schema_version') != 'emergency-accounting-intent.v1':
        raise ValueError('unsupported_intent_schema')
    if not intent.get('id') or not isinstance(intent['id'], str):
        raise ValueError('missing_intent_identity')
    created_ms, flat_verified_ms = intent.get('created_ms'), intent.get('flat_verified_ms')
    if (type(created_ms) is not int or type(flat_verified_ms) is not int
            or not 0 <= created_ms <= flat_verified_ms):
        raise ValueError('intent_clock_bounds_invalid')
    position = intent.get('position') or {}
    if (position.get('side') not in ('long', 'short')
            or position.get('market_type') != 'futures'
            or position.get('exec_mode') != 'live'):
        raise ValueError('unsupported_trade_scope')
    if not position.get('id') or not isinstance(position['id'], str):
        raise ValueError('missing_position_identity')
    if not intent.get('symbol') or intent['symbol'] != position.get('symbol'):
        raise ValueError('intent_position_symbol_conflict')
    entry = intent.get('entry_observation') or {}
    closes = intent.get('close_orders') or []
    # Older recovery records may retain only their last close.
    if not closes and intent.get('close_observation'):
        closes = [intent['close_observation']]
    if not closes:
        raise ValueError('missing_close_orders_retry')
    orders = [entry, *closes]
    if len(orders) > MAX_ORDERS:
        raise ValueError('order_capture_budget_retry')
    ids = [str(o.get('id') or '') for o in orders]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError('missing_or_duplicate_order_identity')
    if any(o.get('status') not in ('closed', 'canceled', 'expired', 'rejected') for o in orders):
        raise ValueError('nonterminal_order')
    quantities = [number(o.get('filled')) for o in orders]
    if quantities[0] <= 0 or any(q < 0 for q in quantities):
        raise ValueError('invalid_order_quantity')
    if not WT.close(quantities[0], sum(quantities[1:])):
        raise ValueError('entry_exit_quantity_mismatch')
    # `position.amount` is the size the entry was REQUESTED for; an emergency
    # exit can follow a partial entry, so filled may be less, never more.
    requested = number(position.get('amount'))
    if requested <= 0:
        raise ValueError('invalid_requested_position_amount')
    if quantities[0] > requested+1e-8:
        raise ValueError('entry_exceeds_requested_amount')
    return [dict(order_id=oid, quantity=qty, entry=(i == 0),
                basis='derived_from_emergency_intent')
            for i, (oid, qty) in enumerate(zip(ids, quantities))]


def assess(a):
    result = dict(status='incomplete', reasons=[], learning_eligible=False,
                  fees_by_currency={}, funding_by_currency={}, gross_realized_usdt=None,
                  funding_usdt=None, net_trade_pnl_usdt=None, accounting=None,
                  retry_required=True, history_complete=False)
    try:
        intent = a['intent']
        ls = derive_legs(intent)
        # Checked fresh here (not just once in capture()) for the same reason
        # derive_legs is: a missing/malformed scan must surface its own exact
        # reason regardless of what capture()'s try/except recorded.
        validate_overlap_scan(a.get('overlap_scan'))
        observed = a['observed_ms']
        if a['environment'] != 'demo' or a['venue'] != 'binanceusdm':
            raise ValueError('unsupported_venue_environment')
        if a['errors']:
            raise ValueError('capture_inputs_missing_retry')
        symbol, start, end = a['venue_symbol'], a['start_ms'], a['end_ms']
        if symbol != norm_symbol(intent['symbol']).replace('/', '') or not intent['symbol'].endswith('/USDT'):
            raise ValueError('unsupported_settlement_currency')
        if (type(observed) is not int or type(a['captured_ms']) is not int
                or a['captured_ms'] < observed):
            raise ValueError('capture_clock_conflict')
        if end != observed or start < 0:
            raise ValueError('capture_clock_conflict')
        # Required, not optional: no flat proof means no complete accounting.
        if intent['flat_verified_ms'] > end:
            raise ValueError('flat_verification_after_observation_window')
        orders = {str(o['orderId']): o for o in a['orders']}
        if len(orders) != len(ls) or set(orders) != {l['order_id'] for l in ls}:
            raise ValueError('order_versions_missing_retry')
        if start != number(orders[ls[0]['order_id']]['time']):
            raise ValueError('entry_history_start_conflict')
        fills = WT.history_rows(a['fill_history'], 'fills', symbol, start, end, observed)
        funding = WT.history_rows(a['funding_history'], 'income', symbol, start, end, observed)
        events = WT.history_rows(a['funding_events'], 'funding_events', symbol, max(0, start-6*DAY), end, observed)
        result['history_complete'] = True
        if not fills:
            raise ValueError('fill_history_empty_retry')
        event_times = [number(e['fundingTime']) for e in events]
        if (not event_times or min(event_times) > min(number(f['time']) for f in fills)
                or max(event_times) <= max(number(f['time']) for f in fills)):
            raise ValueError('funding_publication_frontier_pending_retry')
        if a['overlapping_trade_ids'] or a['overlapping_intent_ids']:
            raise ValueError('journal_or_emergency_ownership_overlap_retry')
        position = a['position']
        if (position['symbol'] != symbol or position['positionSide'] != 'BOTH'
                or number(position['positionAmt']) != 0):
            raise ValueError('venue_flat_ownership_unproven_retry')
        if set(str(f['orderId']) for f in fills) != set(orders):
            raise ValueError('unowned_or_missing_fills_retry')
        gross, fees = 0.0, {}
        entry_side = 'BUY' if intent['position']['side'] == 'long' else 'SELL'
        normalized = []
        flat_verified = intent['flat_verified_ms']
        for l in ls:
            o = orders[l['order_id']]
            side = entry_side if l['entry'] else ('SELL' if entry_side == 'BUY' else 'BUY')
            if (o['symbol'] != symbol or o['side'] != side or o['positionSide'] != 'BOTH'
                    or o['status'] not in ('FILLED', 'CANCELED', 'EXPIRED', 'EXPIRED_IN_MATCH')
                    or not WT.close(o['executedQty'], l['quantity'])
                    or not intent['created_ms'] <= number(o['time']) <= number(o['updateTime']) <= flat_verified):
                raise ValueError('order_owner_quantity_or_clock_conflict')
            fs = [f for f in fills if str(f['orderId']) == l['order_id']]
            if not WT.close(sum(number(f['qty']) for f in fs), l['quantity']):
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
                normalized.append(dict(id=str(f['id']), version=digest(f), intent_id=intent['id'],
                    event_ms=int(f['time']), available_ms=a['captured_ms'], realized_pnl=pnl,
                    commission=fee, currency=currency, basis='derived_from_emergency_intent'))
        balance = 0.0
        for f in fills:
            balance += number(f['qty'])*(1 if f['side'] == entry_side else -1)
            if balance < -1e-8:
                raise ValueError('fill_sequence_ownership_conflict')
        if not WT.close(balance, 0):
            raise ValueError('whole_trade_fill_balance_conflict')
        cash_gross = sum(number(f['qty'])*number(f['price'])*(1 if f['side'] == 'SELL' else -1) for f in fills)
        if not WT.close(gross, cash_gross):
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
        accounting = dict(schema_version='emergency-whole-accounting-derived.v1', complete=True,
            derived=True, intent_id=intent['id'], symbol=intent['symbol'], venue=a['venue'],
            environment=a['environment'], reconciliation_version=VERSION, fills=normalized,
            currency='USDT', funding_complete=True, funding_net=funding_net,
            resolved_ms=a['captured_ms'], available_ms=a['captured_ms'],
            derivation_note='rebuilt_from_emergency_accounting_intent_not_a_journal_booking')
        result.update(status='derived_complete_as_of_venue_history', learning_eligible=False,
                      funding_usdt=funding_net, net_trade_pnl_usdt=net, accounting=accounting,
                      retry_required=False)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
        result['reasons'] = [str(exc) if isinstance(exc, ValueError) else 'malformed_or_missing_source_retry']
    return result


def capture(intent, exchange, overlapping_trade_ids=(), overlapping_intent_ids=(), overlap_scan=None):
    """Bounded demo reads; no orders, no journal writes, no automatic retry.

    `overlap_scan` must attest that the caller's overlap query was bounded,
    complete and well-formed (see scripts/capture_emergency_whole_accounting.py):
    {trades_scanned, intents_scanned, truncated, malformed_intent_ids,
    malformed_trade_ids, active_recovery_conflict}. An omitted scan is
    UNKNOWN, never "no conflict" — it is stored as given (including `None`)
    and both this call and assess() refuse rather than fabricate an
    all-clear default. An empty overlap id list alone never proves a full
    enumeration happened either way.
    """
    a = dict(schema_version=VERSION, intent=intent, venue='binanceusdm', environment='demo',
             observed_ms=int(time.time()*1000), orders=[], position=None, errors=[],
             overlapping_trade_ids=list(overlapping_trade_ids),
             overlapping_intent_ids=list(overlapping_intent_ids),
             overlap_scan=overlap_scan)
    try:
        ls = derive_legs(intent)   # malformed/budget-exceeded refuses before any network call
        validate_overlap_scan(overlap_scan)   # missing/malformed scan refuses before any network call too
        symbol = intent['symbol'].replace('/', '')
        a['venue_symbol'] = symbol
        for l in ls:
            raw = exchange.fapiPrivateGetOrder(dict(symbol=symbol, orderId=l['order_id']))
            a['orders'].append({k: WT.scalar(raw.get(k)) for k in ('orderId', 'symbol', 'side', 'positionSide',
                                'status', 'executedQty', 'time', 'updateTime')})
        a['start_ms'] = int(number(a['orders'][0]['time']))
        positions = exchange.fapiPrivateV2GetPositionRisk(dict(symbol=symbol))
        ps = [p for p in positions if p.get('symbol') == symbol]
        if len(ps) != 1:
            raise ValueError('one_way_position_snapshot_missing')
        a['position'] = {k: WT.scalar(ps[0].get(k)) for k in ('symbol', 'positionSide', 'positionAmt')}
        a['observed_ms'] = int(time.time()*1000)
        a['end_ms'] = a['observed_ms']
        for key, endpoint in (('fill_history', 'fills'), ('funding_history', 'income'), ('funding_events', 'funding_events')):
            history_start = max(0, a['start_ms']-6*DAY) if endpoint == 'funding_events' else a['start_ms']
            a[key] = WT.history(exchange, endpoint, symbol, history_start, a['end_ms'], a['observed_ms'])
    except Exception as exc:
        # Never archive exception text, which may include authenticated requests.
        a['errors'].append('capture_failed:'+type(exc).__name__)
    a['captured_ms'] = int(time.time()*1000)
    a['assessment'] = assess(a)
    a['sha256'] = digest(a)
    return a


def replay(a):
    """Self-consistency/integrity hash check on the captured JSON, not a
    cryptographic attestation from the venue: it proves this artifact matches
    what capture() would have produced from the same raw fields, nothing
    about whether the venue itself can dispute those fields."""
    if a.get('schema_version') != VERSION or digest({k: v for k, v in a.items() if k != 'sha256'}) != a.get('sha256'):
        raise ValueError('emergency_whole_accounting_integrity')
    expected = assess(a)
    if expected != a['assessment']:
        raise ValueError('emergency_whole_accounting_replay_mismatch')
    return expected
