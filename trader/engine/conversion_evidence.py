"""Actual conversion observations, not a valuation or inferred cashflow allocation.

Binance convert/orderStatus does not identify the originating fee/funding flow
or account. Retain the operator's proposed join, but never accept it as proof.
"""
from decimal import Decimal, InvalidOperation
import time

from . import trade_accounting as A
from .accounting import digest

VERSION = 'conversion-evidence.v1'
MAX_REQUESTS = 8
FIELDS = ('orderId', 'orderStatus', 'fromAsset', 'fromAmount', 'toAsset',
          'toAmount', 'ratio', 'inverseRatio', 'createTime')


def amount(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('conversion_invalid_amount')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('conversion_invalid_amount') from None
    if not result.is_finite():
        raise ValueError('conversion_invalid_amount')
    return result


def cashflow(whole, kind, identity):
    A.replay(whole)
    if whole['venue'] != 'binanceusdm' or whole['environment'] != 'demo':
        raise ValueError('conversion_unsupported_scope')
    if kind not in ('commission', 'funding') or not isinstance(identity, str) or not identity:
        raise ValueError('conversion_invalid_cashflow_key')
    key, endpoint = ('fill_history', 'fills') if kind == 'commission' else ('funding_history', 'income')
    rows = A.history_rows(whole[key], endpoint, whole['venue_symbol'],
                          whole['start_ms'], whole['end_ms'], whole['observed_ms'])
    matches = [r for r in rows if str(r['id' if kind == 'commission' else 'tranId']) == identity]
    if len(matches) != 1:
        raise ValueError('conversion_cashflow_missing_or_ambiguous')
    row = matches[0]
    native = amount(row['commission' if kind == 'commission' else 'income'])
    if kind == 'commission':
        native = native.copy_negate()
    currency = row['commissionAsset' if kind == 'commission' else 'asset']
    if not isinstance(currency, str) or not currency or currency == 'USDT' or native == 0:
        raise ValueError('conversion_nonzero_native_cashflow_required')
    return dict(kind=kind, id=identity, source_version=digest(row), currency=currency,
                signed_native_amount=str(native), event_ms=int(A.number(row['time'])))


def requests(whole, items):
    if not 1 <= len(items) <= MAX_REQUESTS:
        raise ValueError('conversion_request_budget')
    result, flows, orders = [], set(), set()
    for item in items:
        if set(item) != {'kind', 'cashflow_id', 'order_id'}:
            raise ValueError('conversion_invalid_request')
        oid = item['order_id']
        if not isinstance(oid, str) or not oid.isascii() or not oid.isdigit() or len(oid) > 32 or oid != str(int(oid)) or int(oid) <= 0:
            raise ValueError('conversion_invalid_order_id')
        flow = cashflow(whole, item['kind'], item['cashflow_id'])
        key = (flow['kind'], flow['id'])
        if key in flows or oid in orders:
            raise ValueError('conversion_reused_order_or_cashflow')
        flows.add(key); orders.add(oid)
        result.append(flow)
    return result


def assess(artifact):
    whole = artifact['whole_trade_capture']
    flows = requests(whole, artifact['requests'])
    start, end = artifact['observed_ms'], artifact['captured_ms']
    if (type(start) is not int or type(end) is not int
            or not whole['captured_ms'] <= start <= end
            or len(artifact['observations']) != len(flows)):
        raise ValueError('conversion_capture_clock_or_count_conflict')
    reviews = []
    for req, flow, observation in zip(artifact['requests'], flows, artifact['observations']):
        reasons = ['conversion_cashflow_attribution_unproven_retry',
                   'conversion_account_link_unproven_retry', 'conversion_cost_completeness_unproven_retry']
        transaction_observed = False
        raw = observation['response']
        if observation['error_type'] is not None:
            reasons.append('conversion_fetch_failed_retry')
        else:
            try:
                if set(raw) != set(FIELDS) or str(raw['orderId']) != req['order_id']:
                    raise ValueError('conversion_order_identity_conflict')
                if raw['orderStatus'] != 'SUCCESS':
                    raise ValueError('conversion_not_successful_retry')
                ts = raw['createTime']
                if type(ts) is not int or not 0 <= ts <= end:
                    raise ValueError('conversion_order_clock_conflict')
                native = amount(flow['signed_native_amount'])
                # Sign defines the proposed conversion direction, never ownership.
                source, target = (flow['currency'], 'USDT') if native > 0 else ('USDT', flow['currency'])
                if (raw['fromAsset'], raw['toAsset']) != (source, target):
                    raise ValueError('conversion_currency_or_direction_conflict')
                debit, credit = amount(raw['fromAmount']), amount(raw['toAmount'])
                if debit <= 0 or credit <= 0:
                    raise ValueError('conversion_invalid_amount')
                if (debit if native > 0 else credit) != native.copy_abs():
                    raise ValueError('conversion_native_quantity_conflict')
                transaction_observed = True
            except (ValueError, TypeError, KeyError):
                # Fixed public reason; malformed response values never enter diagnostics.
                reasons.append('conversion_order_evidence_invalid_retry')
        reviews.append(dict(cashflow=flow, proposed_order_id=req['order_id'],
                            join_basis='operator_proposal_not_venue_attribution',
                            transaction_observed=transaction_observed,
                            attributed_usdt=None, reasons=reasons))
    return dict(status='attribution_unproven', learning_eligible=False,
                retry_required=True, net_trade_pnl_usdt=None, cashflows=reviews)


def capture(whole, exchange, items):
    requests(whole, items)  # Refuse bad/reused IDs before any network work.
    artifact = dict(schema_version=VERSION, whole_trade_capture=whole, requests=items,
                    observed_ms=int(time.time()*1000), observations=[])
    for item in items:
        try:
            raw = exchange.fapiPrivateGetConvertOrderStatus({'orderId': item['order_id']})
            if not isinstance(raw, dict):
                raise ValueError('invalid_response')
            safe = {k: A.scalar(raw.get(k)) for k in FIELDS}
            artifact['observations'].append(dict(response=safe, error_type=None))
        except Exception as exc:
            artifact['observations'].append(dict(response=None, error_type=type(exc).__name__))
    artifact['captured_ms'] = int(time.time()*1000)
    artifact['assessment'] = assess(artifact)
    artifact['sha256'] = digest(artifact)
    return artifact


def replay(artifact):
    if (artifact.get('schema_version') != VERSION
            or artifact.get('sha256') != digest({k:v for k,v in artifact.items() if k != 'sha256'})):
        raise ValueError('conversion_capture_integrity')
    result = assess(artifact)
    if artifact['assessment'] != result:
        raise ValueError('conversion_replay_mismatch')
    return result
