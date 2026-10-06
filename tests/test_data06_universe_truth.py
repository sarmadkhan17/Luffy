"""DATA-06: tradable instruments come from connected venue truth, with explicit reasons.

Offline only: fake fetchers/exchanges; sockets are denied.
"""
import copy
import socket
from types import SimpleNamespace

import pytest

from tests.registry_observation_fixtures import _body, _symbol
from trader.core.instrument_registry import AccountTrading, Eligibility
from trader.data import feed as module
from trader.data.feed import Universe
from trader.data.registry_provider import (
    BinanceUsdmRegistryProvider, HttpResponse, VenueTarget)
from trader.observability import registry_selector as rs

NOW = 2_000_000_000_000


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def deny(*a, **k):
        raise AssertionError('external connection attempted')
    monkeypatch.setattr(socket.socket, 'connect', deny)
    monkeypatch.setattr(socket, 'getaddrinfo', deny)
    monkeypatch.setattr(module, '_RUNTIME_BLACKLIST', set())


class Venue:
    id = 'binanceusdm'
    urls = {'api': 'https://offline.example/fapi/v1'}

    def __init__(self, tickers):
        self.tickers = tickers

    def market(self, symbol):
        return dict(id=symbol.replace('/', ''), contract=True)

    def fetch_tickers(self):
        return copy.deepcopy(self.tickers)

    def fetch_ohlcv(self, *a, **k):
        return [[1, 10, 11, 9, 10, 5]]


def tk(vol, last=10.0):
    return dict(timestamp=1, quoteVolume=vol, last=last)


def universe(monkeypatch, tickers, top_n=2, blacklist=()):
    ex = Venue(tickers)
    monkeypatch.setattr(module, 'make_exchange', lambda *a, **k: ex)
    u = Universe({'universe': {'majors': ['BTC/USDT'], 'auto_scan': {
        'top_n': top_n, 'min_volume_usdt': 100, 'min_price': 1, 'min_age_days': 0,
        'rescan_hours': .01, 'blacklist': list(blacklist)}}}, ex)
    return u, ex


def test_every_unselected_usdt_ticker_has_an_explicit_reason(monkeypatch):
    module._RUNTIME_BLACKLIST.add('LEARN/USDT')
    u, ex = universe(monkeypatch, {
        'AAA/USDT': tk(900), 'BBB/USDT': tk(800), 'CCC/USDT': tk(700), 'LOW/USDT': tk(5),
        'CHEAP/USDT': tk(900, last=.1), 'BAD/USDT': dict(timestamp=1, quoteVolume=None, last=10),
        'USDC/USDT': tk(900), 'LEARN/USDT': tk(900), 'XAU/USDT': tk(900), 'FOO/BTC': tk(900)},
        blacklist=['XAU/USDT'])
    u._rescan()
    r = u._selection_receipt
    assert r['members'] == ['AAA/USDT', 'BBB/USDT']
    assert r['exclusions'] == {
        'BAD/USDT': 'ticker_invalid_or_identity_unverified', 'CCC/USDT': 'below_top_n_rank',
        'CHEAP/USDT': 'below_min_price', 'LEARN/USDT': 'runtime_untradeable',
        'LOW/USDT': 'below_min_volume', 'USDC/USDT': 'blacklisted', 'XAU/USDT': 'blacklisted'}
    assert not set(r['exclusions']) & set(r['members'])           # a member is never also excluded


def test_exclusions_are_part_of_the_revision_identity(monkeypatch):
    base = {'AAA/USDT': tk(900), 'BBB/USDT': tk(800)}
    a, _ = universe(monkeypatch, base); a._rescan()
    b, _ = universe(monkeypatch, dict(base, ZZZ=tk(1))); b._rescan()
    b2, _ = universe(monkeypatch, dict(base, **{'ZZZ/USDT': tk(1)})); b2._rescan()
    assert a._selection_receipt['exclusions'] == {}
    assert b2._selection_receipt['exclusions'] == {'ZZZ/USDT': 'below_min_volume'}
    assert a._selection_receipt['revision_id'] != b2._selection_receipt['revision_id']


def test_top_n_is_recomputed_from_the_venue_not_remembered(monkeypatch):
    u, ex = universe(monkeypatch, {'AAA/USDT': tk(900), 'BBB/USDT': tk(800), 'CCC/USDT': tk(700)})
    u._rescan()
    assert u._alts == ['AAA/USDT', 'BBB/USDT']
    ex.tickers = {'CCC/USDT': tk(990), 'DDD/USDT': tk(980), 'AAA/USDT': tk(1)}      # the venue changed
    u._rescan()
    assert u._alts == ['CCC/USDT', 'DDD/USDT'] and u._selection_receipt['exclusions']['AAA/USDT'] == 'below_min_volume'


def provider(body, env='production'):
    target = VenueTarget.production() if env == 'production' else VenueTarget.demo()
    seen = []
    def fetch(url, **k):
        seen.append(url)
        return HttpResponse(200, body)
    return BinanceUsdmRegistryProvider(target, fetcher=fetch, clock_ms=lambda: NOW), seen


def test_refresh_records_capabilities_and_never_invents_account_truth():
    p, seen = provider(_body([_symbol(), _symbol('ETHUSDT'), _symbol('OLDUSDT', status='SETTLING')]))
    res = p.refresh()
    assert res.ok and res.provenance.environment == 'production' and res.provenance.record_count == 3
    assert seen == ['https://fapi.binance.com/fapi/v1/exchangeInfo']
    snap = res.snapshot
    assert snap.account_trading is AccountTrading.UNKNOWN
    for r in snap.records:
        assert r.account_eligibility is Eligibility.UNKNOWN and r.eligibility_basis is None
        assert r.symbol_config.value == 'UNKNOWN' and r.leverage_bracket.value == 'UNKNOWN'
        assert r.shortability.value == 'UNKNOWN'
    btc = next(r for r in snap.records if r.instrument_id.venue_symbol == 'BTCUSDT')
    assert btc.constraints.price_tick == '0.1' and btc.constraints.minimum_notional == '5'
    old = next(r for r in snap.records if r.instrument_id.venue_symbol == 'OLDUSDT')
    assert old.venue_status == 'SETTLING' and rs.observation_exclusion(old) == rs.VENUE_STATUS_NOT_TRADING


def test_failed_refresh_keeps_last_good_and_changed_venue_changes_snapshot_id():
    bodies = [_body([_symbol()]), b'not json', _body([_symbol(), _symbol('ETHUSDT')])]
    p = BinanceUsdmRegistryProvider(VenueTarget.production(), clock_ms=lambda: NOW,
        fetcher=lambda *a, **k: HttpResponse(200, bodies.pop(0)))
    first = p.refresh().snapshot
    bad = p.refresh()
    assert not bad.ok and p.latest() is first and p.latest_attempt().failure_reason == 'malformed_json'
    third = p.refresh().snapshot
    assert third.snapshot_id != first.snapshot_id and len(third.records) == 2


def test_environment_identity_cannot_be_mixed():
    with pytest.raises(ValueError):
        VenueTarget('https://demo-fapi.binance.com', 'production')
    with pytest.raises(ValueError):
        VenueTarget('https://fapi.binance.com', 'demo')
    with pytest.raises(ValueError):
        VenueTarget('https://evil.example', 'production')
    assert VenueTarget.demo().source != VenueTarget.production().source
    a, _ = provider(_body(), 'production'); b, _ = provider(_body(), 'demo')
    assert a.refresh().snapshot.snapshot_id != b.refresh().snapshot.snapshot_id   # source is in the identity


def test_attention_subset_is_distinct_from_the_tradable_universe(monkeypatch):
    snap = provider(_body([_symbol(), _symbol('ETHUSDT'), _symbol('SOLUSDT')]))[0].refresh().snapshot
    u, _ = universe(monkeypatch, {'ETH/USDT': tk(900)}); u._rescan()
    before = (list(u.symbols()), copy.deepcopy(u._selection_receipt))
    sel = rs.select(snap, cycle_as_of_ms=NOW + 1, max_snapshot_age_ms=60_000,
                    strategy_scan=['BTC/USDT:USDT'], cursor_before=None)
    assert sel.outcome == rs.SELECTED and sel.selected_symbol == 'ETH/USDT:USDT'
    assert sel.selected_account_eligibility == 'UNKNOWN'          # observation is not permission
    assert (list(u.symbols()), u._selection_receipt) == before    # selecting never touches the universe
    covered = rs.select(snap, cycle_as_of_ms=NOW + 1, max_snapshot_age_ms=60_000,
                        strategy_scan=['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT'], cursor_before=None)
    assert covered.outcome == rs.NOTHING_SELECTABLE               # the scan already covers everything
    bare = rs.select(snap, cycle_as_of_ms=NOW + 1, max_snapshot_age_ms=60_000,
                     strategy_scan=['BTC/USDT'], cursor_before=None)
    assert 'BTC/USDT' in bare.unmatched_scan_symbols              # bare spelling may be spot


def test_unobserved_snapshot_grants_no_entry_capability_or_leverage(tmp_path):
    from trader.core.journal import Journal
    from trader.engine import entry_authority as EA
    j = Journal(tmp_path / 'j.db')
    snap = provider(_body())[0].refresh().snapshot
    with pytest.raises(ValueError, match='canonical_capability_unavailable'):
        EA.capability(j, 'BTC/USDT:USDT', NOW, 5, 'LONG')          # nothing is inferred from the registry
    with pytest.raises(ValueError):                                # leverage cannot be defaulted or guessed
        EA.observe_leverage(instrument_id='binance_usdm:futures:BTCUSDT', account_scope=snap.account_scope,
                            snapshot_id=snap.snapshot_id, symbol_config={'symbol': 'BTCUSDT', 'marginType': 'CROSSED'},
                            leverage_brackets={'symbol': 'BTCUSDT', 'brackets': []},
                            observed_at_ms=1, valid_until_ms=2, source='x')
    with pytest.raises(ValueError):                                # a different symbol's brackets are refused
        EA.observe_leverage(instrument_id='binance_usdm:futures:BTCUSDT', account_scope='a', snapshot_id='s',
                            symbol_config={'symbol': 'BTCUSDT', 'leverage': 5, 'marginType': 'CROSSED'},
                            leverage_brackets={'symbol': 'ETHUSDT', 'brackets': [dict(
                                notionalFloor=0, notionalCap=1e6, initialLeverage=20)]},
                            observed_at_ms=1, valid_until_ms=2, source='x')
