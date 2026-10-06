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


# ── durable history (owner decision: account eligibility stays explicit UNKNOWN) ──
import json
import sqlite3

from trader.core.journal import Journal
from trader.data import registry_store as store


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(module, '_UNTRADEABLE_SINK', None)
    return Journal(tmp_path / 'j.db')


def snap(symbols, now=NOW, env='production'):
    return provider(_body(symbols), env)[0].refresh()


def test_registry_snapshots_are_durable_exact_and_deduplicated(journal):
    a = snap([_symbol(), _symbol('ETHUSDT')], NOW)
    store.record_refresh(journal, a)
    store.record_refresh(journal, a)                                   # replay is idempotent
    text = store.snapshot_json(journal, a.snapshot.snapshot_id)
    assert text == a.snapshot.canonical_json()                          # byte-exact, hash-verified
    p2 = BinanceUsdmRegistryProvider(VenueTarget.production(), clock_ms=lambda: NOW + 5,
        fetcher=lambda *x, **k: HttpResponse(200, _body([_symbol(), _symbol('ETHUSDT')])))
    b = p2.refresh()                                                    # same venue state, later cut
    store.record_refresh(journal, b)
    c = snap([_symbol()])                                                # venue changed
    store.record_refresh(journal, c)
    with journal._tx() as db:
        assert db.execute('SELECT COUNT(*) FROM registry_snapshots').fetchone()[0] == 3
        # records carry their own observed_at_ms, so an identical venue state at a later cut is a new version
        assert db.execute('SELECT COUNT(*) FROM registry_contents').fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM registry_refreshes").fetchone()[0] == 4
    assert store.snapshot_json(journal, b.snapshot.snapshot_id) == b.snapshot.canonical_json()
    stored = json.loads(store.snapshot_json(journal, c.snapshot.snapshot_id))
    assert {r['account_eligibility'] for r in stored['records']} == {'UNKNOWN'}   # UNKNOWN is preserved
    assert all(r['eligibility_basis'] is None for r in stored['records'])


def test_failed_refresh_is_recorded_without_a_snapshot(journal):
    p = BinanceUsdmRegistryProvider(VenueTarget.production(), clock_ms=lambda: NOW,
        fetcher=lambda *a, **k: HttpResponse(503, b'x'), recorder=lambda r: store.record_refresh(journal, r))
    assert not p.refresh().ok
    with journal._tx() as db:
        row = db.execute('SELECT outcome,failure_reason,snapshot_id,environment FROM registry_refreshes').fetchone()
        assert tuple(row) == ('FAILED', 'http_error', None, 'production')
        assert db.execute('SELECT COUNT(*) FROM registry_snapshots').fetchone()[0] == 0


def test_recorder_failure_never_changes_the_refresh_result():
    def boom(_):
        raise RuntimeError('disk')
    p = BinanceUsdmRegistryProvider(VenueTarget.production(), clock_ms=lambda: NOW,
        fetcher=lambda *a, **k: HttpResponse(200, _body()), recorder=boom)
    assert p.refresh().ok and p.record_errors == 1 and p.latest() is not None


def test_history_is_append_only_and_environment_keeps_identity(journal):
    prod, demo = snap([_symbol()], env='production'), snap([_symbol()], env='demo')
    store.record_refresh(journal, prod); store.record_refresh(journal, demo)
    with journal._tx() as db:
        envs = {r[0]: r[1] for r in db.execute('SELECT snapshot_id,environment FROM registry_snapshots')}
        assert envs == {prod.snapshot.snapshot_id: 'production', demo.snapshot.snapshot_id: 'demo'}
        for sql in ("UPDATE registry_snapshots SET environment='demo'", 'DELETE FROM registry_refreshes',
                    "UPDATE registry_contents SET records_json='[]'"):
            with pytest.raises(sqlite3.DatabaseError, match='append-only'):
                db.execute(sql)
    with pytest.raises(store.RegistryStoreError):
        store.record_snapshot(journal, prod.snapshot, 'demo')            # id never rebinds to another environment
    with pytest.raises(store.RegistryStoreError):
        store.record_snapshot(journal, prod.snapshot, 'staging')


def test_corrupt_stored_snapshot_is_detected(journal):
    r = snap([_symbol()])
    store.record_refresh(journal, r)
    raw = sqlite3.connect(journal.db_path)
    raw.execute('DROP TRIGGER registry_contents_no_update')
    raw.execute("UPDATE registry_contents SET records_json='[]'"); raw.commit(); raw.close()
    with pytest.raises(store.RegistryStoreError, match='corrupt'):
        store.snapshot_json(journal, r.snapshot.snapshot_id)


def test_universe_revision_and_exclusions_persist_across_restart(monkeypatch, journal):
    tickers = {'AAA/USDT': tk(900), 'LOW/USDT': tk(5), 'USDC/USDT': tk(900)}
    u, _ = universe(monkeypatch, tickers)
    u.attach_journal(journal)
    u._rescan()
    u._persist_revision(u._selection_receipt)                          # replay of the same revision
    rid = u._selection_receipt['revision_id']
    with journal._tx() as db:
        rows = db.execute('SELECT * FROM universe_revisions').fetchall()
    assert len(rows) == 1 and rows[0]['revision_id'] == rid and rows[0]['environment'] == u.environment
    assert json.loads(rows[0]['members_json']) == ['AAA/USDT']
    assert json.loads(rows[0]['exclusions_json']) == {'LOW/USDT': 'below_min_volume', 'USDC/USDT': 'blacklisted'}
    cfg = json.loads(rows[0]['config_json'])
    assert cfg['majors_unobserved'] == ['BTC/USDT'] and cfg['selection_config']['top_n'] == 2
    reopened = Journal(journal.db_path)                                 # restart
    with reopened._tx() as db:
        assert db.execute('SELECT COUNT(*) FROM universe_revisions').fetchone()[0] == 1
    with reopened._tx() as db:
        for sql in ('DELETE FROM universe_revisions', "UPDATE universe_revisions SET members_json='[]'"):
            with pytest.raises(sqlite3.DatabaseError, match='append-only'):
                db.execute(sql)
    with pytest.raises(store.RegistryStoreError, match='conflict'):
        store.record_universe_revision(reopened, dict(u._selection_receipt, members=['ZZZ/USDT']), u.environment)


def test_runtime_untradeable_survives_restart_and_is_environment_scoped(monkeypatch, journal):
    u, _ = universe(monkeypatch, {'AAA/USDT': tk(900), 'XYZ/USDT': tk(900)})
    u.attach_journal(journal)
    assert module.mark_untradeable('XYZ/USDT', '-4411 sign TradFi-Perps agreement') is True
    assert store.load_untradeable(journal, u.environment) == {'XYZ/USDT': '-4411 sign TradFi-Perps agreement'}
    # simulated restart: process memory is gone, the journal is not
    module._RUNTIME_BLACKLIST.clear(); monkeypatch.setattr(module, '_UNTRADEABLE_SINK', None)
    u2, _ = universe(monkeypatch, {'AAA/USDT': tk(900), 'XYZ/USDT': tk(900)})
    u2.attach_journal(Journal(journal.db_path))
    u2._rescan()
    assert u2._alts == ['AAA/USDT'] and u2._selection_receipt['exclusions']['XYZ/USDT'] == 'runtime_untradeable'
    other = 'production' if u.environment == 'demo' else 'demo'
    assert store.load_untradeable(journal, other) == {}                 # a demo refusal is not a production fact


def test_untradeable_record_failure_still_excludes_the_symbol(monkeypatch, journal):
    u, _ = universe(monkeypatch, {'AAA/USDT': tk(900)})
    u.attach_journal(journal)
    monkeypatch.setattr(module, '_UNTRADEABLE_SINK', lambda *a: (_ for _ in ()).throw(OSError('disk')))
    assert module.mark_untradeable('BAD/USDT', 'x') is True and 'BAD/USDT' in module._RUNTIME_BLACKLIST


def test_majors_stay_observable_but_absence_never_implies_tradable(monkeypatch, journal, tmp_path):
    u, _ = universe(monkeypatch, {'AAA/USDT': tk(900)})                 # BTC/USDT absent from the venue scan
    u._rescan()
    assert 'BTC/USDT' in u.symbols() and u._selection_receipt['majors_unobserved'] == ['BTC/USDT']
    from trader.engine import entry_authority as EA
    with pytest.raises(ValueError, match='canonical_capability_unavailable'):
        EA.capability(journal, 'BTC/USDT:USDT', NOW, 3, 'LONG')         # observability grants no exposure
    # an UNKNOWN-eligibility record can never pass capability, even when fully registered
    s = snap([_symbol()]).snapshot
    iid = 'binance_usdm:futures:BTCUSDT'
    store.record_snapshot(journal, s, 'production')
    with pytest.raises(ValueError):
        EA.register_capability(journal, s, iid, execution_symbol='BTC/USDT:USDT',
                               leverage_evidence=None, valid_until_ms=s.as_of_ms + 1)
        EA.capability(journal, 'BTC/USDT:USDT', s.as_of_ms, 3, 'LONG')
