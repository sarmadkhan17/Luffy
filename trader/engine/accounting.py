"""Exact emergency round-trip fill receipts; never estimates or trading authority.

Funding attribution is deliberately unknown in v1. This separate ledger cannot
silently feed legacy realized_pnl or learning as a complete economic outcome.
"""
from __future__ import annotations

import hashlib
import json
import math
import time


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def archive_flat(db, intent):
    """Called in the same transaction that releases the recovery barrier."""
    record = {k: intent.get(k) for k in (
        'id', 'symbol', 'position', 'created_ms', 'code_hash',
        'entry_observation', 'close_observation', 'close_orders')}
    record['schema_version'] = 'emergency-accounting-intent.v1'
    record['flat_verified_ms'] = intent['updated_ms']
    record['status'] = 'fills_pending_funding_unknown'
    payload = encode(record)
    db.execute('INSERT INTO execution_accounting(id,payload) VALUES (?,?)',
               (intent['id'], payload))


def safe_fill(fill):
    info = fill.get('info') or {}
    result = {**{k: fill.get(k) for k in ('id', 'symbol', 'timestamp', 'side', 'amount', 'price')},
            'order': str(fill.get('order') or info.get('orderId') or ''),
            'realized_pnl': info.get('realizedPnl'),
            'commission': info.get('commission'),
            'commission_asset': info.get('commissionAsset')}
    # Invalid numeric responses must remain serializable missing evidence.
    return {k: (None if isinstance(v, float) and not math.isfinite(v)
                else v if isinstance(v, (str, int, float, bool, type(None))) else None)
            for k, v in result.items()}


def number(value):
    if value is None or isinstance(value, bool):
        raise ValueError('missing_or_invalid_number')
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('nonfinite_number')
    return value


def reconcile(record, fills, fetch_reason=None):
    """Require every known order's full quantity, IDs, currency and venue P&L."""
    from ..core.types import norm_symbol
    result = {'status': 'incomplete', 'reasons': [], 'fills': [],
              'gross_realized_usdt': None, 'commission_usdt': None,
              'fill_net_excluding_funding_usdt': None, 'funding_usdt': None,
              'net_economic_pnl_usdt': None, 'learning_eligible': False}
    reasons = result['reasons']
    if fetch_reason:
        reasons.append(fetch_reason)
    entry = record.get('entry_observation') or {}
    closes = record.get('close_orders') or []
    # Older recovery records may retain only their last close. Quantity checks
    # below refuse to claim completeness when earlier legs are missing.
    if not closes and record.get('close_observation'):
        closes = [record['close_observation']]
    orders = [entry, *closes]
    ids = [str(o.get('id') or '') for o in orders]
    if not all(ids) or len(set(ids)) != len(ids):
        reasons.append('missing_or_duplicate_order_identity')
    try:
        quantities = [number(o.get('filled')) for o in orders]
        if any(q < 0 for q in quantities) or quantities[0] <= 0:
            raise ValueError('invalid_order_quantity')
        if not math.isclose(quantities[0], sum(quantities[1:]), rel_tol=1e-9, abs_tol=1e-12):
            reasons.append('entry_exit_quantity_mismatch')
        if any(o.get('status') not in {'closed', 'canceled', 'expired', 'rejected'} for o in orders):
            reasons.append('nonterminal_order')
    except (ValueError, TypeError):
        reasons.append('missing_or_invalid_order_quantity')
        quantities = []
    seen = {}
    for f in fills:
        if f.get('order') not in ids:
            continue
        fid = f.get('id')
        if not fid:
            reasons.append('missing_fill_identity')
            continue
        key = str(fid)
        if key in seen and seen[key] != f:
            reasons.append('conflicting_fill_identity')
        seen[key] = f
    result['fills'] = sorted(seen.values(), key=lambda f: str(f['id']))
    gross = fees = 0.0
    by_order = dict.fromkeys(ids, 0.0)
    entry_side = 'buy' if record['position']['side'] == 'long' else 'sell'
    for f in result['fills']:
        try:
            qty, price = number(f['amount']), number(f['price'])
            ts = number(f['timestamp'])
            if qty <= 0 or price <= 0:
                raise ValueError('invalid_fill')
            if norm_symbol(f['symbol']) != record['symbol']:
                raise ValueError('wrong_symbol')
            if not record['created_ms'] <= ts <= record['flat_verified_ms']:
                raise ValueError('fill_outside_intent_window')
            side = entry_side if f['order'] == ids[0] else ('sell' if entry_side == 'buy' else 'buy')
            if f['side'] != side:
                raise ValueError('wrong_side')
            by_order[f['order']] += qty
            gross += number(f['realized_pnl'])
            fees += number(f['commission'])
            if f['commission_asset'] != 'USDT':
                reasons.append('commission_currency_unattributed')
        except (ValueError, TypeError, KeyError):
            reasons.append('missing_or_invalid_fill_fields')
    if quantities:
        for oid, qty in zip(ids, quantities):
            if not math.isclose(by_order[oid], qty, rel_tol=1e-9, abs_tol=1e-12):
                reasons.append('order_fill_quantity_missing_or_mismatched')
    if not all(math.isfinite(x) for x in (gross, fees, gross-fees)):
        reasons.append('nonfinite_aggregate')
    if not reasons:
        result.update(status='fills_verified_funding_unknown', gross_realized_usdt=gross,
                      commission_usdt=fees, fill_net_excluding_funding_usdt=gross-fees)
    reasons.append('funding_unattributed')
    result['reasons'] = sorted(set(reasons))
    return result


def capture(record, exchange):
    """One bounded read, no orders, no journal mutation, no blind pagination."""
    reason = None
    fills = []
    try:
        raw = exchange.fetch_my_trades(record['symbol'], since=record['created_ms'], limit=1000)
        if not isinstance(raw, list):
            raise ValueError('invalid_fill_response')
        fills = [safe_fill(f) for f in raw]
        if len(raw) >= 1000:
            reason = 'history_page_full_retry_required'
    except Exception as exc:
        # Never archive exception text, which may include authenticated requests.
        reason = 'fill_fetch_failed:' + type(exc).__name__
    artifact = {'schema_version': 'emergency-accounting-capture.v1',
                'observed_ms': int(time.time()*1000), 'intent': record,
                'fills': fills, 'fetch_reason': reason,
                'assessment': reconcile(record, fills, reason)}
    artifact['sha256'] = digest(artifact)
    return artifact


def replay(artifact):
    body = {k: v for k, v in artifact.items() if k != 'sha256'}
    if artifact.get('schema_version') != 'emergency-accounting-capture.v1' or digest(body) != artifact.get('sha256'):
        raise ValueError('accounting_capture_integrity')
    expected = reconcile(artifact['intent'], artifact['fills'], artifact['fetch_reason'])
    if artifact['assessment'] != expected:
        raise ValueError('accounting_replay_mismatch')
    return expected
