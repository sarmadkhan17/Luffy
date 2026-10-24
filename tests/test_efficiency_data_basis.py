"""DV01: indexed single-pass basis receipts == the retired quadratic scan.

The oracle below is the pre-change `_basis_between`, kept verbatim so every
scenario compares old and new output record-for-record (fixture data only).
"""
import uuid

import pandas as pd
import pytest

from trader.core.instrument_registry import InstrumentId
from trader.core.types import MarketType
from trader.data import derivatives as D
from trader.data import market_provenance as mp
from trader.data.derivatives import DerivFeed, _PERIOD_MS, to_binance

FAPI, SAPI = D.FAPI, D.SAPI
H = 3_600_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % H)



def old_basis_between(self, symbol: str, period: str,
                   start_ms: int) -> pd.DataFrame:
    self._check_cancelled()
    sym = to_binance(symbol)
    perp = dict(self._klines(FAPI, "/fapi/v1/klines", sym, period,
                             start_ms, self._KLINE_PAGE))
    perp_receipts = list(getattr(self._local, 'receipts', []))
    spot = dict(self._klines(SAPI, "/api/v3/klines", sym, period,
                             start_ms, self._KLINE_PAGE))
    # inner join on close time: a bar missing on either venue has no basis
    pairs = [(ts, (perp[ts] - spot[ts]) / spot[ts])
             for ts in sorted(perp.keys() & spot.keys())
             if spot[ts] > 0]
    spot_receipts = list(getattr(self._local, 'receipts', []))
    self._local.receipts = perp_receipts + spot_receipts
    if not pairs:
        return self._frame([])
    rows = []
    for event, value in pairs:
        self._check_cancelled()
        components = []
        for receipts, price, venue, market in (
                (perp_receipts, perp[event], 'binanceusdm', MarketType.FUTURES),
                (spot_receipts, spot[event], 'binance', MarketType.SPOT)):
            for r in receipts:
                records = [k for k in r['raw'] if isinstance(k, (list, tuple))
                           and len(k)>6 and k[6] == event and float(k[4]) == price]
                if not records:
                    continue
                component = mp.annotate(pd.DataFrame(dict(
                    ts=pd.to_datetime([event], unit='ms', utc=True), value=[price])),
                    instrument_id=InstrumentId(venue, market, sym).value,
                    source=r['source'], kind='derivative', received_ms=r['received_ms'],
                    request_id=r.get('request_id'), request_started_ms=r['request_started_ms'],
                    raw=[records[-1]])
                # Convert once per input receipt, not once per metadata field.
                # Repeated conversion makes historical backfill spend most
                # of its time constructing the same pandas rows.
                component_record = component.to_dict('records')[0]
                components.append({k: component_record[k] for k in mp.META})
        valid = len(components) == 2 and all(c['quality'] == 'VALID' for c in components)
        # A derived receipt cannot precede either required input receipt.
        component_times = [c['available_at_ms'] for c in components if c['available_at_ms'] is not None]
        component_times += [c['observed_at_ms'] for c in components]
        available = max([self._clock_ms()] + component_times)
        raw = dict(components=components, derivation='basis.spot-perp.v1',
                   event_ms=event, value=value)
        rows.append(mp.annotate(pd.DataFrame(dict(
            ts=pd.to_datetime([event], unit='ms', utc=True), value=[value])),
            instrument_id=InstrumentId('binanceusdm', MarketType.FUTURES, sym).value if valid else None,
            source='binance:spot+usdm:basis' if valid else None, kind='derivative',
            received_ms=available, raw=[raw], transform_version='basis.spot-perp.v1',
            max_age_ms=_PERIOD_MS.get(period, 3_600_000)+1_800_000))
    return pd.concat(rows, ignore_index=True)




# ── fixtures ────────────────────────────────────────────────────────────
def kline(close_ms, close):
    return [close_ms - H + 1, "1", "2", "0.5", str(close), "9", close_ms, "9", 1, "1", "1", "0"]


class Clock:
    def __init__(self, t=T0 + 900 * H, step=0):
        self.t0 = self.t = t
        self.step = step

    def reset(self):
        self.t = self.t0

    def __call__(self):
        v = self.t
        self.t += self.step
        return v


def make_feed(tmp_path, perp, spot, *, clock=None, perp_extra=(), spot_extra=(), rid=True):
    """DerivFeed whose `_get` serves canned klines and records receipts as the real one does."""
    feed = DerivFeed(db_path=tmp_path / 'd.db', clock_ms=clock or Clock())
    serial = iter(range(10 ** 6))

    def fake_get(path, params, base=FAPI):
        raw = list(perp if base == FAPI else spot)
        raw += list(perp_extra if base == FAPI else spot_extra)
        started = feed._clock_ms()
        feed._local.receipts = [dict(
            source=base + path, params=dict(params), received_ms=feed._clock_ms(),
            request_started_ms=started, raw=raw,
            **({'request_id': 'rq%d' % next(serial)} if rid else {}))]
        return raw
    feed._get = fake_get
    return feed


def canon(df):
    """Order- and dtype-insensitive record view: what prepare() would persist."""
    records = mp.prepare(df)
    return [{k: (None if v != v else v) if not isinstance(v, str) else v for k, v in r.items()}
            for r in records]


def both(tmp_path, monkeypatch, perp, spot, **kw):
    """Run old and new on identical inputs; uuid4 is a counter so ids are comparable."""
    out = []
    for fn in (old_basis_between, DerivFeed._basis_between):
        counter = iter(range(10 ** 6))
        monkeypatch.setattr(uuid, 'uuid4', lambda c=counter: type('U', (), {'hex': 'u%07d' % next(c)})())
        mk = dict(kw)
        if 'clock' in mk and callable(mk['clock']) and hasattr(mk['clock'], 'reset'):
            mk['clock'].reset()
        feed = make_feed(tmp_path / ('o' if fn is old_basis_between else 'n'), perp, spot, **mk)
        out.append(fn(feed, 'BTC/USDT', '1h', T0))
    return out


@pytest.fixture(autouse=True)
def _dirs(tmp_path):
    (tmp_path / 'o').mkdir()
    (tmp_path / 'n').mkdir()


def series(n, base=100.0, off=0.0):
    return [kline(T0 + (i + 1) * H, base + i + off) for i in range(n)]


def assert_same(old, new):
    assert canon(old) == canon(new)
    assert list(old['ts']) == list(new['ts'])
    assert old.attrs == new.attrs
    assert list(old.columns) == list(new.columns)


FixedClock = StepClock = Clock


# ── equivalence ─────────────────────────────────────────────────────────
def test_fixed_clock_matches_the_retired_scan(tmp_path, monkeypatch):
    old, new = both(tmp_path, monkeypatch, series(40), series(40, 99.0), clock=FixedClock())
    assert len(new) == 40 and set(new['quality']) == {'VALID'}
    assert_same(old, new)


def test_advancing_clock_matches_per_event_clock_reads(tmp_path, monkeypatch):
    old, new = both(tmp_path, monkeypatch, series(25), series(25, 99.0), clock=StepClock(step=7))
    assert len(set(new['observed_at_ms'])) > 1       # clock really advanced per event
    assert_same(old, new)


def test_missing_bar_on_either_venue_has_no_basis(tmp_path, monkeypatch):
    perp, spot = series(10), series(10, 99.0)
    del spot[3]
    del perp[7]
    old, new = both(tmp_path, monkeypatch, perp, spot, clock=FixedClock())
    assert len(new) == 8
    assert_same(old, new)


def test_last_match_wins_within_a_receipt(tmp_path, monkeypatch):
    # Two raw records share (close time, close price) but differ elsewhere.
    dup = kline(T0 + 3 * H, 102.0)
    dup[1] = "7777"
    old, new = both(tmp_path, monkeypatch, series(6), series(6, 99.0),
                    perp_extra=[dup], clock=FixedClock())
    assert_same(old, new)
    component = [c for c in __import__('json').loads(new.raw_json.iloc[2])['components']][0]
    assert '7777' in component['raw_json']             # records[-1], not records[0]


def test_extra_receipt_components_make_the_basis_invalid(tmp_path, monkeypatch):
    """A third component (duplicate perp receipt) is retained and invalidates."""
    def run(fn):
        feed = make_feed(tmp_path / ('o' if fn is old_basis_between else 'n'),
                         series(5), series(5, 99.0), clock=FixedClock())
        real = feed._get

        def twice(path, params, base=FAPI):
            real(path, params, base)
            if base == FAPI:
                feed._local.receipts = feed._local.receipts * 2     # same receipt, twice
            return feed._local.receipts[-1]['raw']
        feed._get = twice
        return fn(feed, 'BTC/USDT', '1h', T0)
    counter = iter(range(10 ** 6))
    monkeypatch.setattr(uuid, 'uuid4', lambda: type('U', (), {'hex': 'u%d' % next(counter)})())
    old = run(old_basis_between)
    counter = iter(range(10 ** 6))
    new = run(DerivFeed._basis_between)
    assert set(new['quality']) == {'UNKNOWN'} and new['instrument_id'].isna().all()
    comps = __import__('json').loads(new.raw_json.iloc[0])['components']
    assert len(comps) == 3
    assert_same(old, new)


def test_receipt_without_request_id_mints_one_per_component(tmp_path, monkeypatch):
    old, new = both(tmp_path, monkeypatch, series(6), series(6, 99.0), clock=FixedClock(), rid=False)
    assert_same(old, new)


def test_component_receipt_mismatch_never_becomes_valid(tmp_path, monkeypatch):
    # The perp receipt carries the bar at another close price: no component.
    perp = series(4)
    feed_perp = [list(k) for k in perp]
    old_feed = make_feed(tmp_path / 'o', perp, series(4, 99.0), clock=FixedClock())
    new_feed = make_feed(tmp_path / 'n', perp, series(4, 99.0), clock=FixedClock())
    for feed in (old_feed, new_feed):
        real = feed._get

        def skewed(path, params, base=FAPI, real=real, feed=feed):
            data = real(path, params, base)
            if base == FAPI:
                for r in feed._local.receipts:
                    r['raw'] = [[*k[:4], "123456", *k[5:]] for k in r['raw']]
            return data
        feed._get = skewed
    counter = iter(range(10 ** 6))
    monkeypatch.setattr(uuid, 'uuid4', lambda: type('U', (), {'hex': 'u%d' % next(counter)})())
    old = old_basis_between(old_feed, 'BTC/USDT', '1h', T0)
    counter = iter(range(10 ** 6))
    new = DerivFeed._basis_between(new_feed, 'BTC/USDT', '1h', T0)
    assert set(new['quality']) == {'UNKNOWN'} and new['value'].notna().all()
    assert_same(old, new)


def test_future_event_is_not_valid(tmp_path, monkeypatch):
    # Clock before the last bar's close: partial/future receipts stay INVALID.
    clock = FixedClock(t=T0 + 3 * H)
    old, new = both(tmp_path, monkeypatch, series(6), series(6, 99.0), clock=clock)
    assert list(new['quality']) == ['VALID'] * 3 + ['INVALID'] * 3
    assert_same(old, new)


def test_cancellation_stops_before_any_publication(tmp_path):
    feed = make_feed(tmp_path, series(30), series(30, 99.0), clock=FixedClock())
    calls = []

    def cancelled():
        calls.append(1)
        return len(calls) > 3
    feed.cancelled = cancelled
    with pytest.raises(InterruptedError):
        feed._basis_between('BTC/USDT', '1h', T0)


def test_raw_record_comparisons_scale_linearly(tmp_path, monkeypatch):
    """Count float() conversions of raw kline prices: events × (1 bucket), not events × records."""
    n = 300
    feed = make_feed(tmp_path, series(n), series(n, 99.0), clock=FixedClock())

    class Counted(str):
        seen = 0

        def __float__(self):
            Counted.seen += 1
            return float.__call__(str(self)) if False else float(str.__str__(self))
    counted_perp = [[*k[:4], Counted(k[4]), *k[5:]] for k in series(n)]
    counted_spot = [[*k[:4], Counted(k[4]), *k[5:]] for k in series(n, 99.0)]
    feed = make_feed(tmp_path, counted_perp, counted_spot, clock=FixedClock())
    feed._basis_between('BTC/USDT', '1h', T0)
    # _klines converts each record once per venue (2n); the receipt match adds
    # one conversion per event per venue (2n): linear, not 2·n·n.
    assert Counted.seen <= 6 * n
