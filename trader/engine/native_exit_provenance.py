"""Native protective-exit provenance: one bounded, read-only mapping from the
journal's own protective algo ID to the actual executed order and its fills.

protective.py already establishes that Binance USDM books a reduceOnly stop
as an ALGO (conditional) order, addressed by `algoId`, not an ordinary order
id. When that algo fires, the venue opens an ordinary order to execute the
close; Binance's algo-order status call exposes that bridge as
`actualOrderId`. This module never infers the bridge from symbol/time
proximity — only an explicit, present `actualOrderId` on the exact `algoId`
the journal recorded is accepted.

Field contract for `GET /fapi/v1/algoOrder` (by `algoId`) confirmed against
the official docs 2026-09-17: algoId, algoType, orderType, symbol, side,
positionSide, quantity, algoStatus, actualOrderId (empty if not triggered),
actualQty (present only once filled/partially filled), actualType, reduceOnly,
createTime, updateTime, triggerTime. There is no `bookTime` field. Local ccxt
maps both FINISHED and TRIGGERED to a closed-like state, but TRIGGERED alone
is not execution proof — only `algoStatus == 'FINISHED'` is accepted as
terminal-successful here; TRIGGERED refuses as not-yet-executed.

Leg-only: this proves one exit order's fills, never the whole trade's
economics. `leg_verified` never implies `learning_eligible` or any P&L claim
— `whole_economics` stays the literal string 'unknown'. Whole-trade P&L
still requires the entry leg independently verified elsewhere (see
emergency_whole_accounting.py / trade_accounting.py).

`replay()` is a self-consistency/integrity hash check on the captured JSON
(did this artifact get tampered with, does its stored assessment match a
fresh recompute) — it is NOT a cryptographic attestation from the venue.
Nothing here proves the venue itself signed or cannot repudiate this data;
it proves only that the artifact we're looking at is internally consistent
with what `capture()` would have produced from the same raw fields.
"""
from __future__ import annotations

import math
import time

from .accounting import digest, number, safe_fill
from . import booking
from . import trade_accounting as WT
from ..core.types import norm_symbol

VERSION = 'native-exit-provenance.v1'
TERMINAL_ALGO_STATUS = 'FINISHED'
NON_TRIGGERED_ALGO_STATUSES = ('NEW', 'WORKING', None)
TERMINAL_ORDER_STATUSES = ('FILLED',)
SUPPORTED_PROTECTIVE_ORDER_TYPES = ('STOP_MARKET', 'TAKE_PROFIT_MARKET')
MAX_FILLS_PAGE = 1000

ALGO_FIELDS = ('algoId', 'algoType', 'orderType', 'symbol', 'side', 'positionSide',
              'quantity', 'algoStatus', 'actualOrderId', 'actualQty', 'actualType',
              'reduceOnly', 'createTime', 'updateTime', 'triggerTime')
ORDER_FIELDS = ('orderId', 'symbol', 'side', 'positionSide', 'status', 'executedQty',
                'reduceOnly', 'time', 'updateTime')


def canonical_id(value):
    """Binance order/algo IDs are positive integers. Accept an int or a plain
    digit string; reject bool, zero, negative and non-numeric values — a
    present-but-malformed ID must never be treated as a valid identity."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError('id_field_invalid')
    text = str(value).strip()
    if not text or not text.lstrip('-').isdigit():
        raise ValueError('id_field_invalid')
    n = int(text)
    if n <= 0:
        raise ValueError('id_field_invalid')
    return str(n)


def has_actual_order_id(algo):
    """'0'/0/''/None all mean "not triggered yet" per the official field
    contract — distinct from a malformed-but-present id, which canonical_id
    itself refuses once this returns True."""
    v = algo.get('actualOrderId')
    if v in (None, 0, '0', ''):
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True


def validate_reference(ref):
    """Structural check on the embedded trade reference, re-run on every replay.

    A resealed artifact carries a fresh sha256 over whatever `reference` it
    contains, so integrity alone cannot catch a malformed/inconsistent
    reference — it must be checked again here, not only once at capture time.
    Returns the reference's `opened_at` in epoch ms for the clock chain.
    """
    from ..cognition.outcomes import timestamp
    if not ref.get('id') or not isinstance(ref['id'], str):
        raise ValueError('reference_trade_id_missing')
    if ref.get('side') not in ('long', 'short'):
        raise ValueError('unsupported_trade_scope')
    if ref.get('market_type') != 'futures' or ref.get('exec_mode') != 'live':
        raise ValueError('unsupported_trade_scope')
    if not ref.get('symbol') or not isinstance(ref['symbol'], str):
        raise ValueError('reference_symbol_missing')
    if not ref.get('sl_order_id'):
        raise ValueError('missing_protective_algo_id_reference')
    canonical_id(ref['sl_order_id'])
    if number(ref.get('amount')) <= 0:
        raise ValueError('reference_quantity_invalid')
    try:
        return timestamp(ref['opened_at'])
    except (ValueError, TypeError, AttributeError):
        # datetime.fromisoformat() embeds the raw malformed string in its
        # message; never let that leak through — fixed code only.
        raise ValueError('reference_clock_invalid')


def reference_snapshot(row):
    """Immutable trade snapshot embedded verbatim, including sl_order_id.

    booking.TRADE_FIELDS deliberately excludes sl_order_id (see booking.py),
    so the normal trade-booking.v1 export cannot carry the protective algo id.
    This reads it directly off the trades row instead of inferring it from a
    caller-supplied order id.
    """
    fields = booking.TRADE_FIELDS + ('sl_order_id',)
    ref = {k: row[k] for k in fields if k in row.keys()}
    validate_reference(ref)
    return ref


def assess(a):
    result = dict(status='incomplete', reasons=[], leg_verified=False,
                  whole_economics='unknown', algo=None, order=None, fills=[])
    try:
        ref = a['reference']
        opened_ms = validate_reference(ref)
        algo, order, fills = a['algo_order'], a['order'], a['fills']
        observed, captured = a['observed_ms'], a['captured_ms']
        if a['environment'] != 'demo' or a['venue'] != 'binanceusdm':
            raise ValueError('unsupported_venue_environment')
        if a['errors']:
            raise ValueError('capture_inputs_missing_retry')
        if type(observed) is not int or type(captured) is not int or not observed <= captured:
            raise ValueError('capture_clock_conflict')
        if algo is None:
            raise ValueError('missing_algo_order')
        symbol = norm_symbol(ref['symbol']).replace('/', '')
        close_side = 'SELL' if ref['side'] == 'long' else 'BUY'
        if canonical_id(algo.get('algoId')) != canonical_id(ref['sl_order_id']):
            raise ValueError('algo_id_reference_mismatch')
        if algo.get('symbol') != symbol or algo.get('side') != close_side:
            raise ValueError('algo_symbol_or_side_mismatch')
        if algo.get('positionSide') != 'BOTH':
            raise ValueError('algo_not_one_way')
        if algo.get('reduceOnly') is not True:
            raise ValueError('algo_not_reduce_only')
        if algo.get('algoType') != 'CONDITIONAL':
            raise ValueError('algo_not_conditional_type')
        if algo.get('orderType') not in SUPPORTED_PROTECTIVE_ORDER_TYPES:
            raise ValueError('algo_order_type_unsupported')
        status = algo.get('algoStatus')
        if status != TERMINAL_ALGO_STATUS:
            if status in NON_TRIGGERED_ALGO_STATUSES:
                raise ValueError('algo_not_triggered_retry')
            raise ValueError('algo_not_terminal_successful')
        if not has_actual_order_id(algo):
            raise ValueError('algo_actual_order_id_missing')
        actual_id = canonical_id(algo.get('actualOrderId'))
        create_ms, trigger_ms, algo_update_ms = algo.get('createTime'), algo.get('triggerTime'), algo.get('updateTime')
        if create_ms is None or not trigger_ms or algo_update_ms is None:
            raise ValueError('algo_clock_fields_missing')
        create_ms, trigger_ms, algo_update_ms = number(create_ms), number(trigger_ms), number(algo_update_ms)
        if order is None:
            raise ValueError('missing_actual_order')
        if canonical_id(order.get('orderId')) != actual_id:
            raise ValueError('actual_order_id_mismatch')
        if order.get('symbol') != symbol or order.get('side') != close_side:
            raise ValueError('actual_order_symbol_or_side_mismatch')
        if order.get('positionSide') != 'BOTH':
            raise ValueError('actual_order_not_one_way')
        if order.get('reduceOnly') is not True:
            raise ValueError('actual_order_not_reduce_only')
        if order.get('status') not in TERMINAL_ORDER_STATUSES:
            raise ValueError('actual_order_not_terminal_executed')
        order_time, order_update = number(order.get('time')), number(order.get('updateTime'))
        # journal opened <= algo create <= trigger <= order time <= order update
        # <= algo update <= captured. Venue races never relax this ordering.
        chain = (opened_ms, create_ms, trigger_ms, order_time, order_update, algo_update_ms, captured)
        if not all(lo <= hi for lo, hi in zip(chain, chain[1:])):
            raise ValueError('provenance_clock_chain_conflict')
        expected_qty = number(ref['amount'])
        algo_qty, order_qty = number(algo.get('quantity')), number(order.get('executedQty'))
        if expected_qty <= 0 or algo_qty <= 0 or order_qty <= 0:
            raise ValueError('nonpositive_quantity')
        if not WT.close(algo_qty, expected_qty):
            raise ValueError('algo_quantity_journal_mismatch')
        actual_qty_field = algo.get('actualQty')
        if actual_qty_field not in (None, ''):
            actual_qty = number(actual_qty_field)
            # Conservative: this bounded slice proves a full native close only.
            if not WT.close(actual_qty, algo_qty):
                raise ValueError('partial_exit_not_supported_retry')
            if not WT.close(actual_qty, order_qty):
                raise ValueError('actual_qty_order_quantity_mismatch')
        if not WT.close(order_qty, algo_qty):
            raise ValueError('algo_actual_order_quantity_mismatch')
        seen, qty = set(), 0.0
        for f in fills:
            fid = f.get('id')
            if not fid or str(fid) in seen:
                raise ValueError('missing_or_duplicate_fill_identity')
            seen.add(str(fid))
            try:
                fill_order_id = canonical_id(f.get('order'))
            except ValueError:
                raise ValueError('fill_attribution_mismatch')
            if (fill_order_id != actual_id or norm_symbol(f.get('symbol') or '') != norm_symbol(ref['symbol'])
                    or f.get('side') != close_side.lower()):
                raise ValueError('fill_attribution_mismatch')
            ts = number(f.get('timestamp'))
            if not order_time <= ts <= order_update:
                raise ValueError('fill_clock_mismatch')
            amt, price = number(f.get('amount')), number(f.get('price'))
            if amt <= 0 or price <= 0:
                raise ValueError('fill_geometry_invalid')
            qty += amt
        if not fills or not WT.close(qty, order_qty):
            raise ValueError('fill_quantity_missing_or_mismatched')
        result.update(status='leg_verified_whole_economics_unknown', leg_verified=True,
                      algo=algo, order=order, fills=fills)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, OverflowError) as exc:
        result['reasons'] = [str(exc) if isinstance(exc, ValueError) else 'malformed_or_missing_source_retry']
    return result


def capture(reference, exchange):
    """One bounded algo lookup; the actual-order/fills lookups only run once
    the algo itself proves terminal-successful and triggered. Read-only."""
    a = dict(schema_version=VERSION, reference=reference, venue='binanceusdm', environment='demo',
             observed_ms=int(time.time()*1000), algo_order=None, order=None, fills=[], errors=[])
    try:
        validate_reference(reference)
        symbol = norm_symbol(reference['symbol']).replace('/', '')
        algo_id = str(reference['sl_order_id'])
        raw_algo = exchange.fapiPrivateGetAlgoOrder({'algoId': algo_id})
        a['algo_order'] = {k: WT.scalar(raw_algo.get(k)) for k in ALGO_FIELDS}
        if a['algo_order'].get('algoStatus') == TERMINAL_ALGO_STATUS and has_actual_order_id(a['algo_order']):
            actual_id = str(a['algo_order']['actualOrderId'])
            raw_order = exchange.fapiPrivateGetOrder({'symbol': symbol, 'orderId': actual_id})
            a['order'] = {k: WT.scalar(raw_order.get(k)) for k in ORDER_FIELDS}
            raw_fills = exchange.fetch_my_trades(reference['symbol'],
                                                 since=int(number(a['order'].get('time') or 0)),
                                                 limit=MAX_FILLS_PAGE)
            if not isinstance(raw_fills, list):
                raise ValueError('invalid_fill_response')
            a['fills'] = [safe_fill(f) for f in raw_fills
                         if str(f.get('order') or (f.get('info') or {}).get('orderId') or '') == actual_id]
            if len(raw_fills) >= MAX_FILLS_PAGE:
                a['errors'].append('history_page_full_retry_required')
        # else: not triggered / not terminal-successful yet — no unnecessary
        # order or fill calls; assess() below reports the specific reason.
    except Exception as exc:
        # Never archive exception text, which may include authenticated requests.
        a['errors'].append('capture_failed:'+type(exc).__name__)
    a['observed_ms'] = int(time.time()*1000)
    a['captured_ms'] = int(time.time()*1000)
    a['assessment'] = assess(a)
    a['sha256'] = digest(a)
    return a


def replay(a):
    if a.get('schema_version') != VERSION or digest({k: v for k, v in a.items() if k != 'sha256'}) != a.get('sha256'):
        raise ValueError('native_exit_provenance_integrity')
    expected = assess(a)
    if expected != a['assessment']:
        raise ValueError('native_exit_provenance_replay_mismatch')
    return expected
