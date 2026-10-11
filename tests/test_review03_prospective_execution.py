"""Review-03 (execution_observation): observer isolation and evidence contracts.

Offline only: fake exchange, tmp_path stores, no network, no production DB.
"""
import json
import sqlite3

import pytest

from trader.observability import depth_evidence as D, funding_events as F
from trader.observability import prospective_execution as P


class FakeEx:
    urls = {'api': {'fapiPublic': 'https://fapi.binance.com/fapi/v1',
                    'fapiPrivate': 'https://fapi.binance.com/fapi/v1'}}

    def __init__(self, trades=None, order=None):
        self.trades, self.order, self.created = trades, order, 0

    def fetch_my_trades(self, symbol=None, *a, **k):
        return self.trades

    def create_order(self, symbol, type, side, amount, *a, **k):
        self.created += 1
        return self.order or {'id': '1', 'status': 'closed'}


def _obs(tmp_path, ex):
    cap = P.Capture(tmp_path / 'e.db')
    return P.ObservedExchange(ex, cap), cap


def test_fetch_my_trades_observer_never_breaks_caller(tmp_path):
    rows = [{'info': 'not-a-dict', 'id': 1}, None, {'id': 2, 'order': '9', 'info': {}}]
    obs, _ = _obs(tmp_path, FakeEx(trades=rows))
    assert obs.fetch_my_trades('BTC/USDT:USDT') is rows


def test_create_order_survives_malformed_context_reference(tmp_path):
    ex = FakeEx()
    obs, _ = _obs(tmp_path, ex)
    obs.context({'decision_id': 'd1', 'reference': 'not-a-dict'})
    assert obs.create_order('BTC/USDT:USDT', 'market', 'buy', 1)['id'] == '1'
    assert ex.created == 1


def test_capture_write_is_idempotent_and_immutable(tmp_path):
    cap = P.Capture(tmp_path / 'c.db')
    k1 = cap.write('k', {'a': 1})
    k2 = cap.write('k', {'a': 1})
    assert k1 == k2
    db = sqlite3.connect(tmp_path / 'c.db')
    assert db.execute('select count(*) from observations').fetchone()[0] == 1
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("update observations set body='x'")


def test_capture_nan_body_refused_not_zeroed(tmp_path):
    cap = P.Capture(tmp_path / 'c.db')
    assert cap.write('k', {'a': float('nan')}) is None
    assert not cap.path.exists() or not sqlite3.connect(cap.path).execute(
        "select count(*) from observations").fetchone()[0]


def test_stop_file_blocks_writes(tmp_path):
    cap = P.Capture(tmp_path / 'c.db')
    cap.path.with_suffix('.STOP').write_text('')
    assert cap.write('k', {'a': 1}) is None


class T:
    base_url = 'https://fapi.binance.com'
    environment = 'production'


def _body(bids, asks, e=1000):
    return json.dumps({'lastUpdateId': 5, 'E': e, 'T': e, 'bids': bids, 'asks': asks}).encode()


@pytest.mark.parametrize('bids,asks,reason', [
    ([['NaN', '1']], [['101', '1']], 'nonpositive_level'),
    ([['100', '0']], [['101', '1']], 'nonpositive_level'),
    ([['102', '1']], [['101', '1']], 'crossed_book'),
    ([[100, '1']], [['101', '1']], 'malformed_level'),
])
def test_depth_rejects_bad_books(bids, asks, reason):
    with pytest.raises(D.DepthRejected) as e:
        D.observe(_body(bids, asks), target=T, symbol='BTCUSDT', limit=20,
                  request_start_ms=1000, received_ms=1001, max_age_ms=5000)
    assert e.value.reason == reason


def test_depth_rejects_stale_and_keeps_strings():
    with pytest.raises(D.DepthRejected):
        D.observe(_body([['100', '1']], [['101', '1']]), target=T, symbol='BTCUSDT',
                  limit=20, request_start_ms=9000, received_ms=9001, max_age_ms=5000)
    o = D.observe(_body([['100.10', '1.0']], [['101', '1']]), target=T, symbol='BTCUSDT',
                  limit=20, request_start_ms=1000, received_ms=1001, max_age_ms=5000)
    assert o['bids'] == [['100.10', '1.0']] and o['environment'] == 'production'


def test_funding_replay_rejects_tampered_receipt():
    body = json.dumps([]).encode().decode()
    import hashlib
    receipt = dict(schema=F.SCHEMA, environment='production', symbol='BTCUSDT',
                   open_ms=1000, close_ms=2000, pages=[dict(
                       url=F.url('BTCUSDT', 1000, 2000), request_start_ms=2000, received_ms=2001,
                       http_status=200, response_body=body,
                       response_sha256=hashlib.sha256(body.encode()).hexdigest())])
    assert F.replay(receipt) == []
    receipt['pages'][0]['response_body'] = '[ ]'
    with pytest.raises(ValueError):
        F.replay(receipt)
    receipt['environment'] = 'demo'
    with pytest.raises(ValueError):
        F.replay(receipt)
