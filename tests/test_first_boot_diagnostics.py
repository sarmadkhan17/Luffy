"""OBS-05 bounded diagnostics: all transports and launch seams are inert."""
import json
import sqlite3

import ccxt
import pytest
import requests

from trader.observability import first_boot as F, bootstrap as B
from trader.engine import venue_reads as V
from tests.test_first_boot_admission import local, venue, deny_network, empty_receipt
from tests.test_bootstrap_contract import phase_a, InertHost
from tests.test_venue_reads import Env
from tests.test_launch_preflight import NOW

SECRET = 'secret-key-signature-response-body-SENTINEL'


def refused(local, monkeypatch, expected):
    result = phase_a(local, monkeypatch)
    assert not result['allow'] and not result['trading_authority']
    fact = result['facts']['protection']
    assert fact['status'] == 'FAIL' and fact['reasons'] == [expected]
    assert not fact['trading_authority']
    assert SECRET not in json.dumps(result)
    assert expected in F.REASON_CODES
    host = InertHost()
    host.prelaunch = lambda: result
    stopped = B.execute(host)
    assert not stopped['allow'] and not stopped['trading_authority']
    assert host.launches == 0 and host.events == []
    assert SECRET not in json.dumps(stopped)
    return result


@pytest.mark.parametrize('case,expected', [
    ('missing','read_credentials_missing'),
    ('missing_secret','read_credentials_missing'),
    ('wrong_type','read_credentials_unusable'),
    ('whitespace','read_credentials_unusable'),
    ('key_overlap','read_credentials_separation_violation'),
    ('secret_overlap','read_credentials_separation_violation'),
])
def test_credential_reasons_without_transport(local, monkeypatch, venue, case, expected):
    from trader.core import config
    empty_receipt(local)
    env = Env(read_key=True)
    if case == 'missing': env.values.pop('BINANCE_READ_API_KEY')
    if case == 'missing_secret': env.values.pop('BINANCE_READ_SECRET_KEY')
    if case == 'wrong_type': env.values['BINANCE_READ_API_KEY'] = 123
    if case == 'whitespace': env.values['BINANCE_READ_SECRET_KEY'] = ' '
    if case == 'key_overlap': env.values['BINANCE_READ_API_KEY'] = 'tk'
    if case == 'secret_overlap': env.values['BINANCE_READ_SECRET_KEY'] = 'ts'
    monkeypatch.setattr(config, 'Env', env)
    refused(local, monkeypatch, expected)
    assert venue.sent == []


@pytest.mark.parametrize('exception,expected', [
    (ccxt.AuthenticationError,'venue_authentication_or_permission_failure'),
    (ccxt.PermissionDenied,'venue_authentication_or_permission_failure'),
    (ccxt.ArgumentsRequired,'read_credentials_unusable'),
    (ccxt.RequestTimeout,'venue_timeout_or_network_failure'),
    (ccxt.NetworkError,'venue_timeout_or_network_failure'),
    (requests.exceptions.Timeout,'venue_timeout_or_network_failure'),
    (requests.exceptions.ConnectionError,'venue_timeout_or_network_failure'),
    (TimeoutError,'venue_timeout_or_network_failure'),
    (ccxt.BadResponse,'venue_response_malformed'),
    (RuntimeError,'first_boot_unexpected_failure'),
    (ValueError,'first_boot_unexpected_failure'),
])
def test_typed_transport_failures_never_emit_exception_payload(local, monkeypatch, venue, exception, expected):
    empty_receipt(local)
    venue.on_read = lambda n: (_ for _ in ()).throw(exception(SECRET))
    refused(local, monkeypatch, expected)
    assert len(venue.sent) == 1


@pytest.mark.parametrize('listing,value,expected', [
    ('positions',None,'venue_response_malformed'),
    ('positions',[{'symbol':SECRET,'positionSide':'BOTH','positionAmt':'NaN'}],'venue_response_malformed'),
    ('positions',[{'symbol':SECRET,'positionSide':'BOTH','positionAmt':'1'}],'venue_positions_nonzero'),
    ('orders',[{'orderId':SECRET}],'venue_orders_nonzero'),
    ('algos',[{'algoId':SECRET}],'venue_orders_nonzero'),
    ('orders',{'body':SECRET},'venue_response_malformed'),
    ('algos',{'orders':[],'hasMore':True,'body':SECRET},'venue_response_malformed'),
])
def test_response_reasons_never_emit_inventory_payload(local, monkeypatch, venue, listing, value, expected):
    empty_receipt(local)
    setattr(venue, listing, value)
    refused(local, monkeypatch, expected)


@pytest.mark.parametrize('statement', [
    "INSERT INTO execution_requests VALUES ('SUBMITTED')",
    "INSERT INTO partial_exit_intents VALUES ('PENDING')",
    "INSERT INTO trades VALUES ('t','SOL/USDT','long',1,10,'s','open')",
])
def test_local_ledger_reason_blocks_without_read(local, monkeypatch, venue, statement):
    empty_receipt(local)
    with sqlite3.connect(local[0]/'data/luffy.db') as db: db.execute(statement)
    refused(local, monkeypatch, 'local_execution_ledger_not_clear')
    assert venue.sent == []


@pytest.mark.parametrize('case',['stale','unverifiable'])
def test_snapshot_reason(local, monkeypatch, venue, case):
    empty_receipt(local)
    value = F.read_zero_account(clock=lambda: NOW-1)
    if case == 'stale': value['checked_at'] = NOW-121
    else: value['status'] = SECRET
    monkeypatch.setattr(F, 'read_zero_account', lambda **k: value)
    refused(local, monkeypatch, 'snapshot_stale_or_unverifiable')


def test_static_fact_reason(local, monkeypatch, venue):
    empty_receipt(local)
    (local[0]/'data/watchdog.off').unlink()
    refused(local, monkeypatch, 'first_boot_static_facts_not_pass')
    assert venue.sent == []


def test_credential_loader_malformed_is_unusable(local, monkeypatch, venue):
    empty_receipt(local)
    monkeypatch.setattr(V, '_read_credentials', lambda env: (_ for _ in ()).throw(TypeError(SECRET)))
    refused(local, monkeypatch, 'read_credentials_unusable')


def test_generic_unexpected_custom_class_name_is_not_emitted(local, monkeypatch, venue):
    empty_receipt(local)
    secret_exception = type(SECRET, (Exception,), {})
    monkeypatch.setattr(F, 'read_zero_account', lambda **k: (_ for _ in ()).throw(secret_exception(SECRET)))
    refused(local, monkeypatch, 'first_boot_unexpected_failure')


def test_json_decode_failure_has_fixed_reason():
    exc = requests.exceptions.JSONDecodeError(SECRET, SECRET, 0)
    assert F.failure_code(exc) == 'venue_response_malformed'
    assert F.failure_code(F.AdmissionFailure(SECRET)) == 'first_boot_unexpected_failure'


@pytest.mark.parametrize('table', ['execution_requests', 'trades'])
def test_unreadable_local_execution_schema_is_bounded(local, monkeypatch, venue, table):
    empty_receipt(local)
    with sqlite3.connect(local[0]/'data/luffy.db') as db: db.execute(f'DROP TABLE {table}')
    refused(local, monkeypatch, 'local_execution_ledger_not_clear')
    assert venue.sent == []


def test_nonzero_algo_total_is_order_exposure(local, monkeypatch, venue):
    empty_receipt(local)
    venue.algos = {'orders': [], 'total': 1}
    refused(local, monkeypatch, 'venue_orders_nonzero')


def test_deadline_reason_and_read_cap(local, monkeypatch, venue):
    empty_receipt(local)
    original = F.read_zero_account
    times = iter([0., 0., 21.])
    monkeypatch.setattr(F, 'read_zero_account', lambda **k: original(
        clock=lambda: NOW, monotonic=lambda: next(times)))
    refused(local, monkeypatch, 'venue_timeout_or_network_failure')
    assert len(venue.sent) == 1
