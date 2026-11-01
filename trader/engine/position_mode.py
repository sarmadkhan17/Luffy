"""Native position-side identity; no account-mode mutation or entry authority."""
import json
import time

from ..research.closure import digest

SIDES = ('BOTH', 'LONG', 'SHORT')
MODE_SCHEMA = 'execution-position-mode.v1'
MAX_AGE_MS = 30_000


def identity_of(trade_or_identity):
    if not isinstance(trade_or_identity, dict):
        value = getattr(trade_or_identity, 'entry_identity', None)
        return value if isinstance(value, dict) else {}
    if 'entry_identity' in trade_or_identity:
        value = trade_or_identity['entry_identity']
        return value if isinstance(value, dict) else {}
    if 'entry_identity_json' in trade_or_identity:
        return json.loads(trade_or_identity.get('entry_identity_json') or '{}')
    return trade_or_identity


def position_side(trade_or_identity):
    identity = identity_of(trade_or_identity)
    bound = identity.get('execution_binding') or {}
    side = bound.get('position_side', identity.get('native_position_side', 'BOTH'))
    if side not in SIDES:
        raise ValueError('native_position_side_invalid')
    if identity.get('native_position_side', side) != side:
        raise ValueError('native_position_side_binding_differs')
    return side


def venue_side(position):
    info = position.get('info') or {}
    side = info.get('positionSide', position.get('positionSide', position.get('position_side', 'BOTH')))
    if side not in SIDES:
        raise ValueError('native_position_side_invalid')
    return side


def observe(ex):
    """Read current account mode through the existing client's GET endpoint."""
    from ..data.feed import execution_account_scope
    from ..observability.portfolio_observation import trading_source
    if ex.id != 'binanceusdm':
        raise ValueError('position_mode_venue_unsupported')
    environment, source = trading_source(ex)
    if ex.urls.get('api', {}).get('fapiPrivate') != source + '/fapi/v1':
        raise ValueError('position_mode_venue_mismatch')
    started = int(time.time() * 1000)
    raw = ex.fapiPrivateGetPositionSideDual()
    received = int(time.time() * 1000)
    if not isinstance(raw, dict) or type(raw.get('dualSidePosition')) is not bool:
        raise ValueError('position_mode_unavailable')
    if received - started > MAX_AGE_MS:
        raise ValueError('position_mode_read_stale')
    body = dict(schema=MODE_SCHEMA, account_scope=execution_account_scope(ex),
                environment=environment, source=source + '/fapi/v1/positionSide/dual',
                request_start_ms=started, observed_ms=received,
                hedged=raw['dualSidePosition'])
    return dict(body, receipt_id=digest(body))


def verify(receipt, account_scope, *, hedged, now_ms=None):
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if (not isinstance(receipt, dict) or receipt.get('schema') != MODE_SCHEMA
            or receipt.get('receipt_id') != digest({k:v for k,v in receipt.items() if k != 'receipt_id'})
            or receipt.get('account_scope') != account_scope or receipt.get('hedged') is not hedged
            or receipt.get('source') not in ('https://demo-fapi.binance.com/fapi/v1/positionSide/dual',
                                             'https://fapi.binance.com/fapi/v1/positionSide/dual')
            or receipt.get('environment') != ('demo' if receipt.get('source', '').startswith('https://demo-fapi.') else 'production')
            or type(receipt.get('observed_ms')) is not int
            or type(receipt.get('request_start_ms')) is not int
            or not 0 <= receipt['request_start_ms'] <= receipt['observed_ms'] <= now
            or now - receipt['request_start_ms'] > MAX_AGE_MS):
        raise ValueError('position_mode_receipt_unverified')
    return receipt


def intent_side(identity, direction, account_scope):
    side = position_side(identity or {})
    if side != 'BOTH':
        from ..research.compose.hedge import RUNTIME_SUPPORT
        if RUNTIME_SUPPORT.get('hedge_mode') is not True:
            raise ValueError('native_hedge_entry_not_integrated')
        if side != direction:
            raise ValueError('native_entry_side_differs')
        verify(identity.get('position_mode'), account_scope, hedged=True)
    return side


def order_params(trade_or_identity, *, reduce_only, client_id=None, **extra):
    side = position_side(trade_or_identity)
    # Binance hedge-mode orders must name the native side and omit reduceOnly.
    params = dict(reduceOnly=reduce_only) if side == 'BOTH' else dict(positionSide=side)
    if client_id is not None:
        params['newClientOrderId'] = client_id
    if {'reduceOnly', 'positionSide', 'newClientOrderId'} & extra.keys():
        raise ValueError('native_order_parameters_conflict')
    params.update(extra)
    return params


def rearm_key(symbol, side='BOTH'):
    from ..core.types import norm_symbol
    symbol = norm_symbol(symbol)
    return symbol if side == 'BOTH' else symbol + '|' + side


def position_key(symbol, side='BOTH'):
    from ..core.types import norm_symbol
    symbol = norm_symbol(symbol)
    return symbol if side == 'BOTH' else (symbol, side)


def bind_new_entry(ex, identity, direction):
    """Read the existing account mode for an ordinary entry; never set it."""
    if direction not in ('LONG', 'SHORT'):
        raise ValueError('native_entry_side_differs')
    receipt = observe(ex)
    if receipt['hedged']:
        verify(receipt, receipt['account_scope'], hedged=True)
        return dict(identity or {}, native_position_side=direction, position_mode=receipt)
    if position_side(identity or {}) != 'BOTH':
        raise ValueError('native_entry_account_mode_differs')
    return identity
