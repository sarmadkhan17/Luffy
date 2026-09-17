"""Replayable provenance for journal bookings; not complete economic outcomes."""
from __future__ import annotations

import json
import math
import time

from ..core.types import norm_symbol
from .accounting import digest, number, safe_fill

TRADE_FIELDS = ('id', 'decision_id', 'symbol', 'side', 'amount', 'entry_price',
                'exit_price', 'realized_pnl', 'status', 'strategy_id', 'exec_mode',
                'market_type', 'opened_at', 'closed_at')


def monetary_total(fills):
    """Strict USDT numeric subtotal; does not establish trade ownership/coverage."""
    if not fills:
        return None
    try:
        unique = {}
        for fill in fills:
            key = fill.get('id')
            if not key or (str(key) in unique and unique[str(key)] != fill):
                return None
            unique[str(key)] = fill
        fills = list(unique.values())
        if any(f.get('commission_asset') != 'USDT' for f in fills):
            return None
        total = sum(number(f.get('realized_pnl')) - number(f.get('commission')) for f in fills)
        return total if math.isfinite(total) else None
    except (ValueError, TypeError, OverflowError):
        return None


def order_evidence(trade, order, fills, quantity, since_ms, basis):
    return {'basis': basis, 'order_id': str(order.get('id') or ''),
            'symbol': trade['symbol'], 'side': 'sell' if trade['side'] == 'long' else 'buy',
            'quantity': quantity, 'since_ms': since_ms, 'observed_ms': int(time.time()*1000),
            'fills': [safe_fill(f) for f in fills]}


def assess(evidence):
    result = {'status': 'unverified', 'reasons': [], 'leg_fill_net_excluding_funding_usdt': None,
              'funding_usdt': None, 'net_economic_pnl_usdt': None, 'learning_eligible': False}
    if evidence.get('basis') != 'venue_order_fills':
        result['reasons'] = ['booking_not_verified_by_exact_order_fills', 'funding_unattributed']
        return result
    reasons = result['reasons']
    fs = evidence.get('fills') or []
    total = monetary_total(fs)
    if total is None:
        reasons.append('missing_or_invalid_pnl_fee_currency')
    seen = set()
    qty = 0.0
    try:
        expected = number(evidence['quantity'])
        if expected <= 0 or not evidence['order_id'] or not fs:
            raise ValueError('missing_order_or_quantity')
        for f in fs:
            if not f.get('id') or str(f['id']) in seen:
                raise ValueError('missing_or_duplicate_fill_identity')
            seen.add(str(f['id']))
            if (str(f.get('order')) != evidence['order_id'] or
                    norm_symbol(f['symbol']) != evidence['symbol'] or f['side'] != evidence['side']):
                raise ValueError('fill_attribution_mismatch')
            if not evidence['since_ms'] <= number(f['timestamp']) <= evidence['observed_ms']:
                raise ValueError('fill_clock_mismatch')
            amount, price = number(f['amount']), number(f['price'])
            if amount <= 0 or price <= 0:
                raise ValueError('fill_geometry_invalid')
            qty += amount
        if not math.isclose(qty, expected, rel_tol=1e-9, abs_tol=1e-12):
            raise ValueError('fill_quantity_mismatch')
    except (ValueError, KeyError, TypeError, AttributeError):
        reasons.append('missing_or_conflicting_order_fill_evidence')
    if not reasons:
        result.update(status='verified_leg_fills_only', leg_fill_net_excluding_funding_usdt=total)
    reasons.append('funding_unattributed')
    return result


def snapshot(row):
    return {k: row[k] for k in TRADE_FIELDS if k in row.keys()} if row else None


def persist(db, trade_id, kind, before, evidence=None):
    """Invoked inside the booking transaction; evidence and balance commit together."""
    after = snapshot(db.execute('SELECT * FROM trades WHERE id=?', (trade_id,)).fetchone())
    if after is None:
        return
    evidence = evidence or {'basis': 'unattributed_journal_booking'}
    receipt = {'schema_version': 'trade-booking.v1', 'trade_id': trade_id,
               'kind': kind, 'observed_ms': int(time.time()*1000),
               'before': before, 'after': after, 'evidence': evidence,
               'assessment': assess(evidence)}
    receipt['sha256'] = digest(receipt)
    db.execute('INSERT INTO trade_accounting_bookings(trade_id,payload) VALUES (?,?)',
               (trade_id, json.dumps(receipt, sort_keys=True, allow_nan=False)))


def replay(receipt):
    if (receipt.get('schema_version') != 'trade-booking.v1' or
            digest({k:v for k,v in receipt.items() if k != 'sha256'}) != receipt.get('sha256')):
        raise ValueError('booking_integrity')
    if receipt['assessment'] != assess(receipt['evidence']):
        raise ValueError('booking_assessment_mismatch')
    if receipt['after']['id'] != receipt['trade_id']:
        raise ValueError('booking_trade_mismatch')
    return receipt['assessment']


def export(db, trade_id):
    """Read-only snapshot, including legacy rows with no provenance receipts."""
    db.row_factory = __import__('sqlite3').Row
    db.execute('BEGIN')
    row = db.execute('SELECT * FROM trades WHERE id=?', (trade_id,)).fetchone()
    if row is None:
        raise ValueError('trade_not_found')
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='trade_accounting_bookings'").fetchone()
    receipts = [json.loads(r[0]) for r in db.execute(
        'SELECT payload FROM trade_accounting_bookings WHERE trade_id=? ORDER BY id', (trade_id,))] if exists else []
    for receipt in receipts:
        replay(receipt)
    result = {'schema_version': 'trade-booking-export.v1', 'trade': snapshot(row),
              'observed_ms': int(time.time()*1000), 'receipts': receipts,
              'status': 'incomplete_accounting', 'funding_usdt': None,
              'net_economic_pnl_usdt': None, 'learning_eligible': False,
              'reasons': ['funding_unattributed', 'whole_trade_fill_attribution_pending']}
    if not receipts:
        result['reasons'].append('legacy_booking_receipts_absent')
    result['sha256'] = digest(result)
    return result


def replay_export(artifact):
    if (artifact.get('schema_version') != 'trade-booking-export.v1' or
            digest({k:v for k,v in artifact.items() if k != 'sha256'}) != artifact.get('sha256')):
        raise ValueError('booking_export_integrity')
    for receipt in artifact['receipts']:
        replay(receipt)
        if receipt['trade_id'] != artifact['trade']['id']:
            raise ValueError('booking_export_trade_mismatch')
    if (artifact['status'] != 'incomplete_accounting' or artifact['learning_eligible'] is not False
            or artifact['funding_usdt'] is not None or artifact['net_economic_pnl_usdt'] is not None):
        raise ValueError('booking_export_incomplete_promoted')
    return {'receipts': len(artifact['receipts']), 'status': artifact['status'], 'learning_eligible': False}
