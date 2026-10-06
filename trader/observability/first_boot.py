"""OBS-05 first-boot inventory: authenticated GETs only, no Kernel or mutation.

No cached file or journal-only inventory is accepted as venue truth. The reader
uses the existing distinct read-key / GET-only / aggregate rate guard boundary.
Nonzero exposure always refuses this narrow zero-account admission path.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import sqlite3
import time

READ_BUDGET_S = 20.0


REASON_CODES = frozenset({
    'read_credentials_missing', 'read_credentials_unusable',
    'read_credentials_separation_violation', 'venue_authentication_or_permission_failure',
    'venue_timeout_or_network_failure', 'venue_response_malformed',
    'venue_positions_nonzero', 'venue_orders_nonzero', 'local_execution_ledger_not_clear',
    'snapshot_stale_or_unverifiable', 'first_boot_static_facts_not_pass',
    'first_boot_unexpected_failure',
})


class AdmissionFailure(ValueError):
    """Only fixed codes cross the preflight output boundary."""
    def __init__(self, code):
        self.code = code if code in REASON_CODES else 'first_boot_unexpected_failure'
        super().__init__(self.code)


def failure_code(exc):
    # Never serialize exception text, class names, response bodies or headers.
    import ccxt
    import requests
    from trader.engine.venue_reads import ReadCredentialsMissing, ReadCredentialsSeparation
    if isinstance(exc, AdmissionFailure):
        return exc.code if exc.code in REASON_CODES else 'first_boot_unexpected_failure'
    if isinstance(exc, sqlite3.Error):
        return 'local_execution_ledger_not_clear'
    if isinstance(exc, ReadCredentialsMissing):
        return 'read_credentials_missing'
    if isinstance(exc, ReadCredentialsSeparation):
        return 'read_credentials_separation_violation'
    if isinstance(exc, (ccxt.AuthenticationError, ccxt.PermissionDenied)):
        return 'venue_authentication_or_permission_failure'
    if isinstance(exc, ccxt.ArgumentsRequired):
        return 'read_credentials_unusable'
    if isinstance(exc, (ccxt.NetworkError, requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError, TimeoutError, ConnectionError)):
        return 'venue_timeout_or_network_failure'
    if isinstance(exc, (ccxt.BadResponse, requests.exceptions.JSONDecodeError)):
        return 'venue_response_malformed'
    return 'first_boot_unexpected_failure'


def _positions_zero(rows):
    if not isinstance(rows, list):
        raise AdmissionFailure('venue_response_malformed')
    identities = set()
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('symbol'), str)
                or not row['symbol'] or row.get('positionSide') not in ('BOTH', 'LONG', 'SHORT')
                or type(row.get('positionAmt')) not in (str, int, float)):
            raise AdmissionFailure('venue_response_malformed')
        identity = row['symbol'], row['positionSide']
        if identity in identities:
            raise AdmissionFailure('venue_response_malformed')
        identities.add(identity)
        try:
            amount = Decimal(str(row['positionAmt']))
        except InvalidOperation:
            raise AdmissionFailure('venue_response_malformed') from None
        if not amount.is_finite():
            raise AdmissionFailure('venue_response_malformed')
        if amount != 0:
            raise AdmissionFailure('venue_positions_nonzero')


def _orders_zero(rows, *, algo=False):
    if algo and isinstance(rows, dict):
        if (set(rows) - {'orders','total','hasMore','nextPageToken'}
                or not isinstance(rows.get('orders'), list)
                or ('total' in rows and (type(rows['total']) is not int or rows['total'] < 0))
                or rows.get('hasMore', False) is not False
                or rows.get('nextPageToken') not in (None, '')):
            raise AdmissionFailure('venue_response_malformed')
        if rows.get('total', 0) > 0:
            raise AdmissionFailure('venue_orders_nonzero')
        rows = rows['orders']
    if not isinstance(rows, list):
        raise AdmissionFailure('venue_response_malformed')
    if rows:
        raise AdmissionFailure('venue_orders_nonzero')


def read_zero_account(*, clock=time.time, monotonic=time.monotonic):
    """Fresh complete account-global listings, never a symbol-filtered scan."""
    from trader.core.config import Env
    from trader.engine.venue_reads import _read_credentials, guarded_client
    if Env.get('BINANCE_DEMO', '').lower() not in ('1', 'true', 'yes'):
        raise AdmissionFailure('first_boot_static_facts_not_pass')
    try:
        key, secret, scope = _read_credentials(Env)
    except (TypeError, ValueError, AttributeError):
        raise AdmissionFailure('read_credentials_unusable') from None
    if any(not isinstance(value, str) or not value.strip() or value != value.strip()
           for value in (key, secret)):
        raise AdmissionFailure('read_credentials_unusable')
    # Raw signed endpoints need no markets/Kernel/trading client initialization.
    client, guard = guarded_client(api_key=key, secret=secret, demo=True, markets={})
    begin, start = clock(), monotonic()
    deadline = start + READ_BUDGET_S
    requests = 0
    try:
        for _ in range(2):
            for method, validator in [
                (client.fapiPrivateV3GetPositionRisk, _positions_zero),
                (client.fapiPrivateGetOpenOrders, _orders_zero),
                (client.fapiPrivateGetOpenAlgoOrders, lambda rows: _orders_zero(rows, algo=True)),
            ]:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError('first_boot_read_deadline')
                client.timeout = max(1, int(min(8., remaining) * 1000))
                rows = method({})  # no symbol filter or incomplete roster
                requests += 1
                if monotonic() > deadline:
                    raise TimeoutError('first_boot_read_deadline')
                validator(rows)
        end = clock()
        return dict(schema='luffy-first-boot-zero-account.v1', status='VERIFIED',
                    account_scope=scope, venue_host=guard.host, environment='DEMO',
                    complete_listing=True, checked_at=begin, completed_at=end,
                    read_elapsed_seconds=monotonic()-start, requests=requests,
                    position_count=0, ordinary_order_count=0, algo_order_count=0,
                    unknown_exposure=False, mutations=0, trading_authority=False)
    finally:
        client.close()


def validate_zero_account(value, now):
    from trader.engine.protection_snapshot import STALE_AFTER_S
    if (not isinstance(value, dict) or value.get('schema') != 'luffy-first-boot-zero-account.v1'
            or value.get('status') != 'VERIFIED' or value.get('account_scope') != 'read_only_key'
            or value.get('environment') != 'DEMO' or value.get('complete_listing') is not True
            or value.get('venue_host') != 'demo-fapi.binance.com'
            or value.get('unknown_exposure') is not False or value.get('trading_authority') is not False
            or any(type(value.get(key)) is not int or value[key] != 0 for key in
                   ['position_count','ordinary_order_count','algo_order_count','mutations'])
            or type(value.get('requests')) is not int or value['requests'] != 6):
        raise AdmissionFailure('snapshot_stale_or_unverifiable')
    for key in ('checked_at','completed_at','read_elapsed_seconds'):
        if type(value.get(key)) not in (int,float) or not math.isfinite(value[key]):
            raise AdmissionFailure('snapshot_stale_or_unverifiable')
    if (not 0 <= now-value['checked_at'] <= STALE_AFTER_S
            or not value['checked_at'] <= value['completed_at'] <= now
            or not 0 <= value['read_elapsed_seconds'] <= READ_BUDGET_S
            or value['completed_at']-value['checked_at'] > READ_BUDGET_S):
        raise AdmissionFailure('snapshot_stale_or_unverifiable')
    return value
