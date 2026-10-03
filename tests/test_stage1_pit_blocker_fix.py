"""Two R1 blockers: real offline production paths, exact PIT negative controls."""
import copy
import inspect
import json
import socket
import sys
import textwrap
from types import SimpleNamespace

import pandas as pd
import pytest

from trader.data import market_provenance as P
from trader.data.feed import Universe
from trader.data.derivatives import DerivFeed, FAPI, SAPI
from trader.kernel import Kernel
from trader.observability.attention import capture, settings
from trader.observability.collector import Collector
from trader.strategy.features_deriv import align

T1, T2 = 1_000_000, 2_000_000
EVENT = 900_000
U1, U2 = 864_000_000+T1, 864_000_000+T2


@pytest.fixture(autouse=True)
def no_connections(monkeypatch):
    denied = []
    def deny(*args, **kwargs):
        denied.append(True)
        raise AssertionError('external connection attempted')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket.socket, 'connect_ex', deny)
    monkeypatch.setattr(socket, 'getaddrinfo', deny)
    yield denied
    assert len(denied) == 0, 'network guard was entered; campaign invalid'


class ScanVenue:
    id = 'binanceusdm'
    urls = {'api': 'https://offline.example/fapi/v1'}
    def __init__(self): self.fail = False; self.calls = 0
    def market(self, symbol): return dict(id=symbol.replace('/', ''), contract=True)
    def fetch_tickers(self):
        self.calls += 1
        if self.fail: raise OSError('offline refresh failure')
        return {'ALT/USDT': dict(timestamp=U2, quoteVolume=1000, last=10)}
    def fetch_ohlcv(self, *args, **kwargs): return [[1, 10, 11, 9, 10, 5]]


@pytest.fixture
def universe(monkeypatch):
    import trader.data.feed as module
    clock = SimpleNamespace(at=U2)
    ex = ScanVenue()
    monkeypatch.setattr(module, 'make_exchange', lambda *a, **k: ex)
    monkeypatch.setattr(module.time, 'time', lambda: clock.at/1000)
    u = Universe({'universe': {'majors': ['BTC/USDT'], 'auto_scan': {
        'min_volume_usdt': 1, 'min_price': 1, 'min_age_days': 0, 'rescan_hours': .01}}}, ex)
    # Explicit acquisition avoids the cache TTL determining fixture setup.
    u._rescan()
    assert u.symbols() == ['BTC/USDT', 'ALT/USDT']
    ex.fail = True
    clock.at = U1
    return u, clock, ex


def assert_historical_membership(u):
    assert 'ALT/USDT' not in u.symbols(as_of_ms=U1), 'future universe membership leaked'


def test_future_cache_failed_refresh(universe):
    u, _, ex = universe
    assert_historical_membership(u)
    assert ex.calls == 2
    assert 'ALT/USDT' not in u.volumes(as_of_ms=U1)


def test_failed_refresh_does_not_rewrite_identity(universe):
    u, _, _ = universe
    receipt, timestamp = copy.deepcopy(u._selection_receipt), u._last_scan
    assert_historical_membership(u)
    assert u._last_scan == timestamp == U2/1000
    assert u._selection_receipt == receipt
    assert receipt['available_at_ms'] == receipt['observed_at_ms'] == U2
    assert receipt['volume_receipts']['ALT/USDT']['instrument_id'] == 'binanceusdm:futures:ALTUSDT'
    assert receipt['listing_receipts']['ALT/USDT']['available_at_ms'] == U2
    assert receipt['revision_id'] == P.digest({k:v for k,v in receipt.items() if k != 'revision_id'})


def test_future_cache_preserved_for_later(universe):
    u, clock, ex = universe
    old = copy.deepcopy(u._selection_receipt)
    assert_historical_membership(u)
    clock.at = U2
    assert 'ALT/USDT' in u.symbols(as_of_ms=U2)
    assert u._selection_receipt == old
    assert ex.calls == 2


def test_kernel_and_normal_attention_lineage(universe, tmp_path):
    u, clock, _ = universe
    k = Kernel.__new__(Kernel)
    k.universe = u
    k._spec_rows = []
    k._attention = Collector(tmp_path, start=False, clock=lambda: clock.at)
    members = k._scan_symbols(as_of_ms=U1)
    assert members == ['BTC/USDT'], 'Kernel consumed future universe'
    assert k._attention_call('begin', {}, members, U1)
    event = k._attention.queue.get_nowait()
    assert [m['symbol'] for m in event['input']['membership']] == ['BTC/USDT']
    assert all(m['source_receipt']['available_at_ms'] <= U1 for m in event['input']['membership'])
    clock.at = U2
    members = k._scan_symbols(as_of_ms=U2)
    assert k._attention_call('begin', {}, members, U2)
    event = k._attention.queue.get_nowait()
    alt = next(m for m in event['input']['membership'] if m['symbol']=='ALT/USDT')
    assert alt['source_receipt']['available_at_ms'] == U2
    assert alt['source_receipt']['revision_id'] == u._selection_receipt['revision_id']
    from trader.observability.store import Store
    store = Store(tmp_path/'attention.db', k._attention.cfg)
    store.write(event)
    retained = json.loads(store.db.execute('SELECT payload FROM scans WHERE scan_id=?',
                         (event['scan_id'],)).fetchone()[0])
    saved_alt = next(m for m in retained['membership'] if m['symbol']=='ALT/USDT')
    assert saved_alt['source_receipt'] == alt['source_receipt']
    # Normal capture detaches the source result and cannot rewrite it later.
    u._selection_receipt['members'].clear()
    assert alt['source_receipt']['members'] == ['ALT/USDT']


def test_attention_rejects_future_membership_even_with_eligible_candles(universe):
    u, _, _ = universe
    df = P.annotate(pd.DataFrame(dict(ts=pd.to_datetime([0],unit='ms',utc=True),
        open=[10], high=[11], low=[9], close=[10], volume=[1])),
        instrument_id='binanceusdm:futures:ALTUSDT', source='fixture',
        kind='candle', timeframe='15m', received_ms=U1)
    event = capture({'ALT/USDT': {'15m': df}}, ['ALT/USDT'], 's', settings({}),
        U1, membership_receipts={'ALT/USDT': u._selection_receipt})
    assert not event['input']['membership'], 'future Attention membership leaked'
    assert not event['input']['candles']
    assert event['issues'][0]['reason'] == 'unavailable_membership'


def mutant(fn, old, new):
    source = textwrap.dedent(inspect.getsource(fn))
    assert old in source
    scope = dict(fn.__globals__)
    exec(source.replace(old, new), scope)
    return scope[fn.__name__]


def test_mutant_preserve_alts_rewrite_scan_detected(universe, monkeypatch):
    u, _, _ = universe
    broken = mutant(Universe._rescan, 'log.warning(f"universe rescan failed: {e}")',
        'log.warning(f"universe rescan failed: {e}")\n        self._last_scan = time.time()')
    monkeypatch.setattr(Universe, '_rescan', broken)
    with pytest.raises(AssertionError, match='availability identity rewritten'):
        u.symbols(as_of_ms=U1)
        assert u._last_scan == U2/1000, 'availability identity rewritten'


class BasisTransport:
    """Substitute requests.get only; no production parser or derivation mock."""
    def __init__(self, clock): self.clock = clock; self.calls = []; self.perp = 110; self.spot = 100
    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url == FAPI+'/fapi/v1/klines':
            self.clock.at = T1
            price = self.perp
        elif url == SAPI+'/api/v3/klines':
            self.clock.at = T2
            price = self.spot
        else:
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: [])
        row = [0, str(price), str(price), str(price), str(price), '1', EVENT]
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: [row])


@pytest.fixture
def basis_feed(monkeypatch, tmp_path):
    import trader.data.derivatives as module
    clock = SimpleNamespace(at=T1)
    transport = BasisTransport(clock)
    monkeypatch.setattr(module.requests, 'get', transport)
    f = DerivFeed(tmp_path/'derivs.db', clock_ms=lambda: clock.at)
    return f, clock, transport


def test_real_basis_path_component_provenance_and_pit(basis_feed, no_connections):
    f, clock, transport = basis_feed
    called = []
    def trace(frame, event, arg):
        if event == 'call' and frame.f_code.co_filename.endswith('/trader/data/derivatives.py'):
            called.append(frame.f_code.co_name)
    previous = sys.getprofile()
    try:
        sys.setprofile(trace)
        df = f.basis('BTC/USDT', limit=1)
    finally:
        sys.setprofile(previous)
    assert all(name in called for name in ('basis', '_basis_between', '_klines', '_get'))
    assert called.count('_klines') == called.count('_get') == 2
    assert len(transport.calls) == 2 and no_connections == []
    assert df.iloc[0]['value'] == pytest.approx(.1)
    row = df.iloc[0]
    raw = json.loads(row.raw_json)
    components = raw['components']
    assert {c['instrument_id'] for c in components} == {'binance:spot:BTCUSDT', 'binanceusdm:futures:BTCUSDT'}
    assert {c['source'] for c in components} == {FAPI+'/fapi/v1/klines', SAPI+'/api/v3/klines'}
    assert [c['available_at_ms'] for c in components] == [T1, T2]
    assert [c['observed_at_ms'] for c in components] == [T1, T2]
    assert all(c['event_time_ms'] == EVENT and len(c['content_hash']) == len(c['revision_id']) == 64
        and c['request_id'] and json.loads(c['raw_json'])[6] == EVENT for c in components)
    assert row.transform_version == raw['derivation'] == 'basis.spot-perp.v1'
    assert row.available_at_ms >= max(c['available_at_ms'] for c in components) == T2
    assert P.eligible_frame(df, None, (T1+T2)//2).empty, 'basis available before required spot leg'
    f.save('BTC/USDT', 'basis', df)
    assert f.load('BTC/USDT', 'basis', as_of_ms=T2-1) is None
    assert f.load('BTC/USDT', 'basis', as_of_ms=T2).iloc[0].raw_json == row.raw_json


def test_basis_record_backfill_revision_restart_replay(basis_feed, tmp_path):
    f, clock, transport = basis_feed
    assert f.record_all(['BTC/USDT'], delay=0)['basis'] == 1
    old = f.load('BTC/USDT', 'basis', as_of_ms=T2).iloc[0]
    # A second genuine two-leg retrieval revises only the perp component.
    transport.perp = 120
    original = transport.__class__.__call__
    def later(self, url, **kwargs):
        started = self.clock.at
        response = original(self, url, **kwargs)
        self.clock.at = T1+T2 if url == FAPI+'/fapi/v1/klines' else 2*T2 if url == SAPI+'/api/v3/klines' else started
        return response
    # Transport boundary remains the sole substituted production layer.
    transport.__class__.__call__ = later
    try:
        clock.at = T2+T1
        counts = f.backfill(['BTC/USDT'], delay=0, since={'BTC/USDT': EVENT})
    finally:
        transport.__class__.__call__ = original
    assert counts['basis@history'] == 1
    clock.at = 2*T2
    new = f.load('BTC/USDT', 'basis', as_of_ms=clock.at).iloc[0]
    assert new['value'] == pytest.approx(.2)
    assert new.supersedes == old.revision_id and new.revision_id != old.revision_id
    old_components = json.loads(old.raw_json)['components']
    new_components = json.loads(new.raw_json)['components']
    assert json.loads(old_components[0]['raw_json'])[4] == '110'
    assert json.loads(new_components[0]['raw_json'])[4] == '120'
    reopened = DerivFeed(tmp_path/'derivs.db', clock_ms=lambda: clock.at)
    assert reopened.load('BTC/USDT','basis',as_of_ms=T2).iloc[0].revision_id == old.revision_id
    stream = reopened.load('BTC/USDT','basis')
    cuts = pd.to_datetime([T2-1, T2, 2*T2],unit='ms',utc=True)
    values = align(stream, cuts)
    assert pd.isna(values.iloc[0]) and values.iloc[1] == pytest.approx(.1) and values.iloc[2] == pytest.approx(.2)


def assert_basis_max_with_reversed_derivation_clock(tmp_path, monkeypatch):
    import trader.data.derivatives as module
    clock = SimpleNamespace(at=T1)
    transport = BasisTransport(clock)
    monkeypatch.setattr(module.requests, 'get', transport)
    times = iter([T1, T1, T1, T1, T2, T1]) # start calculation, two requests, derivation
    f = DerivFeed(tmp_path/'max.db', clock_ms=lambda: next(times))
    df = f.basis('BTC/USDT', limit=1)
    assert P.eligible_frame(df, None, T2-1).empty, 'basis escaped required component availability'


def test_basis_max_even_if_derivation_clock_reverses(tmp_path, monkeypatch):
    assert_basis_max_with_reversed_derivation_clock(tmp_path, monkeypatch)


@pytest.mark.parametrize('replacement', [
    "available = self._clock_ms()",
    "available = max([self._clock_ms()] + [c['event_time_ms'] for c in components])",
    "available = max([self._clock_ms(), components[0]['available_at_ms']])",
])
def test_basis_component_availability_mutants_detected(tmp_path, monkeypatch, replacement):
    broken = mutant(DerivFeed._basis_between,
        "available = max([self._clock_ms()] + component_times)", replacement)
    monkeypatch.setattr(DerivFeed, '_basis_between', broken)
    with pytest.raises(AssertionError, match='basis escaped required component availability'):
        assert_basis_max_with_reversed_derivation_clock(tmp_path, monkeypatch)


def test_mutant_original_universe_bypass_fails_membership_pit(universe, monkeypatch):
    u, _, _ = universe
    broken_refresh = mutant(Universe._rescan, 'log.warning(f"universe rescan failed: {e}")',
        'log.warning(f"universe rescan failed: {e}")\n        self._last_scan = time.time()')
    broken_read = mutant(Universe.symbols,
        'return self.majors + [s for s in self._alts if s not in self.majors and s in receipt]',
        'return self.majors + [s for s in self._alts if s not in self.majors and self._last_scan*1000 <= at]')
    monkeypatch.setattr(Universe, '_rescan', broken_refresh)
    monkeypatch.setattr(Universe, 'symbols', broken_read)
    with pytest.raises(AssertionError, match='future universe membership leaked'):
        assert_historical_membership(u)


def test_missing_ticker_response_does_not_manufacture_empty_result(universe, monkeypatch):
    u, _, ex = universe
    old = copy.deepcopy(u._selection_receipt)
    monkeypatch.setattr(ex, 'fetch_tickers', lambda: None)
    assert_historical_membership(u)
    assert u._selection_receipt == old and u._alts == ['ALT/USDT']


def test_universe_result_cannot_precede_component_receipt(universe, monkeypatch):
    u, clock, ex = universe
    u._selection_receipt = None
    u._last_scan = 0
    u._alts = []
    u._listing_cache = {}
    clock.at = U1
    ex.fail = False
    original = ex.fetch_tickers
    def tickers():
        clock.at = U2
        return original()
    def listing(*args, **kwargs):
        clock.at = U1
        return [[1, 10, 11, 9, 10, 5]]
    monkeypatch.setattr(ex, 'fetch_tickers', tickers)
    monkeypatch.setattr(ex, 'fetch_ohlcv', listing)
    assert 'ALT/USDT' not in u.symbols(as_of_ms=U1), 'selection preceded a required volume receipt'
    assert u._selection_receipt is None
