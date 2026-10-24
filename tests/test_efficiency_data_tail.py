"""DV02: cursor-backed tail acquisition for `DerivFeed.record_all` (fixtures only).

A fake venue serves the documented startTime/endTime/limit contract and logs
every request, so each test can state exactly which rows were asked for.
"""
import inspect
import json
import sqlite3

import pytest

from trader.data import derivative_tail as tail
from trader.data import derivatives as D
from trader.data import sqlite_tx
from trader.data.derivatives import DerivFeed

M15, H = 900_000, 3_600_000
T0 = (1_700_000_000_000 // (8 * H)) * 8 * H
SYM = 'BTC/USDT'
PATHS = {  # series -> (path, period ms, value key, time key)
    'funding': ('/fapi/v1/fundingRate', 8 * H),
    'oi': ('/futures/data/openInterestHist', M15),
    'taker_ratio': ('/futures/data/takerlongshortRatio', M15),
    'ls_ratio': ('/futures/data/topLongShortPositionRatio', M15),
}
KEYS = {'/fapi/v1/fundingRate': ('fundingTime', 'fundingRate'),
        '/futures/data/openInterestHist': ('timestamp', 'sumOpenInterest'),
        '/futures/data/takerlongshortRatio': ('timestamp', 'buySellRatio'),
        '/futures/data/topLongShortPositionRatio': ('timestamp', 'longShortRatio')}


class Venue:
    """Published rows grow with the clock; startTime/endTime/limit as documented."""

    def __init__(self, t):
        self.now = t
        self.log = []
        self.corrections = {}      # (path, ts) -> value
        self.drop = set()          # (path, ts) the provider does not return
        self.fail = False

    def value(self, path, ts):
        if (path, ts) in self.corrections:
            return self.corrections[(path, ts)]
        return 1.0 + (ts // 1000 % 9973) / 10000.0

    def stamps(self, step):
        first = (self.now - 4000 * step) // step * step
        return [t for t in range(first, self.now + 1, step)]

    def get(self, feed):
        def _get(path, params, base=D.FAPI):
            self.log.append((base + path, dict(params)))
            if self.fail:
                feed._local.receipts = []
                return []
            started = feed._clock_ms()
            if path in KEYS:
                step = [p for k, (pp, p) in PATHS.items() if pp == path][0]
                rows = [t for t in self.stamps(step) if (path, t) not in self.drop]
                lo, hi = params.get('startTime'), params.get('endTime')
                if lo is not None or hi is not None:
                    rows = [t for t in rows if (lo is None or t >= lo) and (hi is None or t <= hi)]
                    rows = rows[:params['limit']]
                else:
                    rows = rows[-params['limit']:]
                tk, vk = KEYS[path]
                data = [{tk: t, vk: str(self.value(path, t)), 'symbol': params['symbol']} for t in rows]
            else:                                            # klines, both venues
                start = params['startTime']
                data = [[t - H, '1', '2', '0.5', str((100 + (t // H) % 7) * (1.001 if base == D.FAPI else 1.0)),
                         '9', t - 1, '9', 1, '1', '1', '0']
                        for t in range((self.now // H) * H - 1500 * H, self.now, H) if t - H >= start][:params['limit']]
            feed._local.receipts = [dict(source=base + path, params=dict(params), received_ms=feed._clock_ms(),
                                         request_started_ms=started, request_id='r%d' % len(self.log), raw=data)]
            return data
        return _get


class Clock:
    def __init__(self, venue):
        self.venue = venue

    def __call__(self):
        return self.venue.now


def make(tmp_path, venue=None, **kw):
    venue = venue or Venue(T0 + 3000 * M15)
    feed = DerivFeed(db_path=tmp_path / 'derivs.db', clock_ms=Clock(venue), **kw)
    feed._get = venue.get(feed)
    return feed, venue


def modes(feed):
    """(series, mode) / reasons for the tail-capable series; funding is asserted separately."""
    rows = [p for p in feed.last_pass if p['series'] != 'funding']
    return {(p['series'], p['mode']) for p in rows}, {p['series']: p['reason'] for p in rows}


def funding_pass(feed):
    return [p for p in feed.last_pass if p['series'] == 'funding'][0]


def cursor(feed, series):
    spec = tail.SPECS[series]
    inst = 'binance_usdm:futures:BTCUSDT'
    return tail.read(feed.db, tail.cursor_key('derivative:%s:%s' % (SYM, series), spec.source, inst, spec.period))


def revisions(feed, series, event=None):
    q = "SELECT COUNT(*) FROM market_revisions WHERE series_key=?"
    a = ['derivative:%s:%s' % (SYM, series)]
    if event is not None:
        q += " AND event_ms=?"
        a.append(event)
    return feed.db.execute(q, a).fetchone()[0]


def tail_requests(venue):
    return [(u, p) for u, p in venue.log if 'startTime' in p and 'klines' not in u]


# ── pins ────────────────────────────────────────────────────────────────
def test_specs_match_the_fetchers_they_describe():
    assert tail.FAPI == D.FAPI
    for series, spec in tail.SPECS.items():
        sig = inspect.signature(getattr(DerivFeed, spec.method)).parameters
        assert 'start_ms' in sig
        if series != 'funding' and 'period' in sig and series != 'basis':
            assert sig['period'].default == spec.period and sig['limit'].default == spec.limit
    assert inspect.signature(DerivFeed.basis).parameters['period'].default == '1h'
    assert [s for s, _ in DerivFeed._SERIES_FETCHERS] == ['funding', *tail.SPECS]
    assert 'funding' in tail.FULL_ONLY and 'funding' not in tail.SPECS


# ── cold → tail ─────────────────────────────────────────────────────────
def test_cold_pass_is_full_then_cursor_makes_the_next_pass_a_bounded_tail(tmp_path):
    feed, venue = make(tmp_path)
    feed.record_all([SYM], delay=0)
    kinds, reasons = modes(feed)
    assert {m for _, m in kinds} == {'full'} and set(reasons.values()) == {'cold'}
    assert funding_pass(feed)['mode'] == 'full' and funding_pass(feed)['reason'] == 'variable_interval_no_witness'
    assert not tail_requests(venue)
    full_rows = {p['series']: p['rows'] for p in feed.last_pass}
    assert full_rows['oi'] == 500 and full_rows['funding'] == 1000 and full_rows['basis'] >= 499
    hi = cursor(feed, 'oi')['cov_hi']
    assert hi == venue.now // M15 * M15

    venue.now += 2 * M15                      # two new publications
    venue.log.clear()
    counts = feed.record_all([SYM], delay=0)
    kinds, reasons = modes(feed)
    assert {m for _, m in kinds} == {'tail'}
    assert funding_pass(feed)['mode'] == 'full' and funding_pass(feed)['rows'] == 1000   # never a tail
    for series, spec in tail.SPECS.items():
        assert counts[series] <= spec.overlap_ms // spec.row_ms + 3 and counts[series] < full_rows[series]
    for url, params in tail_requests(venue):
        spec = [s for s in tail.SPECS.values() if s.source == url][0]
        assert params['endTime'] == venue.now
        assert (params['endTime'] - params['startTime']) // spec.row_ms <= spec.limit - 1   # one page can hold it all
    assert cursor(feed, 'oi')['cov_hi'] == hi + 2 * M15


def test_cursor_survives_restart_and_a_new_instance_uses_it(tmp_path):
    feed, venue = make(tmp_path)
    feed.record_all([SYM], delay=0)
    feed.db.close()
    again, _ = make(tmp_path, venue)
    venue.now += M15
    again.record_all([SYM], delay=0)
    assert {m for _, m in modes(again)[0]} == {'tail'}


def test_disabled_incremental_keeps_the_full_request_and_writes_no_cursor(tmp_path):
    feed, venue = make(tmp_path, incremental=False)
    feed.record_all([SYM], delay=0)
    venue.now += M15
    feed.record_all([SYM], delay=0)
    assert not tail_requests(venue)
    assert feed.db.execute('SELECT COUNT(*) FROM deriv_acquisition_cursor').fetchone()[0] == 0


# ── failed save / failed fetch never advance ────────────────────────────
def test_failed_first_save_leaves_no_cursor_and_no_receipts(tmp_path, monkeypatch):
    feed, venue = make(tmp_path)

    def boom(*a, **k):
        raise sqlite3.OperationalError('disk I/O error')
    monkeypatch.setattr(sqlite_tx, 'save_projection', boom)
    with pytest.raises(sqlite3.OperationalError):
        feed.record_all([SYM], delay=0)
    assert feed.db.execute('SELECT COUNT(*) FROM deriv_acquisition_cursor').fetchone()[0] == 0
    assert feed.db.execute('SELECT COUNT(*) FROM market_revisions').fetchone()[0] == 0
    monkeypatch.undo()
    feed.record_all([SYM], delay=0)
    assert {m for _, m in modes(feed)[0]} == {'full'}      # nothing was proven


def test_failed_tail_save_keeps_the_old_cursor_and_retries_the_same_range(tmp_path, monkeypatch):
    feed, venue = make(tmp_path)
    feed.record_all([SYM], delay=0)
    before = cursor(feed, 'oi')
    venue.now += 3 * M15
    real = sqlite_tx.save_projection

    def boom(conn, table, *a, **k):
        if table == 'derivs':
            raise sqlite3.OperationalError('database is locked')
        return real(conn, table, *a, **k)
    monkeypatch.setattr(sqlite_tx, 'save_projection', boom)
    with pytest.raises(sqlite3.OperationalError):
        feed.record_all([SYM], delay=0)
    assert cursor(feed, 'oi') == before
    monkeypatch.undo()
    venue.log.clear()
    feed.record_all([SYM], delay=0)
    oi_req = [p for u, p in tail_requests(venue) if u.endswith('openInterestHist')][0]
    assert oi_req['startTime'] == before['cov_hi'] - tail.SPECS['oi'].overlap_ms
    assert cursor(feed, 'oi')['cov_hi'] == before['cov_hi'] + 3 * M15


def test_empty_provider_response_does_not_advance_or_fabricate_zero(tmp_path):
    feed, venue = make(tmp_path)
    feed.record_all([SYM], delay=0)
    before, rows = cursor(feed, 'oi'), feed.db.execute('SELECT COUNT(*) FROM derivs').fetchone()[0]
    venue.now += 4 * M15
    venue.fail = True
    assert feed.record_all([SYM], delay=0) == {}
    assert cursor(feed, 'oi') == before
    assert feed.db.execute('SELECT COUNT(*) FROM derivs').fetchone()[0] == rows
    assert feed.db.execute('SELECT COUNT(*) FROM derivs WHERE value=0').fetchone()[0] == 0
    venue.fail = False
    feed.record_all([SYM], delay=0)
    assert cursor(feed, 'oi')['cov_hi'] == venue.now // M15 * M15


# ── holes, gaps and unproven continuity ────────────────────────────────
def test_missing_anchor_marks_unproven_and_forces_a_full_pass(tmp_path):
    feed, venue = make(tmp_path)
    feed.record_all([SYM], delay=0)
    hi = cursor(feed, 'oi')['cov_hi']
    venue.now += 2 * M15
    venue.drop.add(('/futures/data/openInterestHist', hi))     # the cursor row vanishes upstream
    feed.record_all([SYM], delay=0)
    state = cursor(feed, 'oi')
    assert state['unproven'] and state['cov_hi'] == hi + 2 * M15
    venue.now += M15
    feed.record_all([SYM], delay=0)
    assert modes(feed)[1]['oi'] == 'unproven'
    assert not cursor(feed, 'oi')['unproven']


def test_outage_longer_than_the_window_records_an_explicit_gap_and_goes_full(tmp_path):
    feed, venue = make(tmp_path)
    feed.record_all([SYM], delay=0)
    hi = cursor(feed, 'oi')['cov_hi']
    venue.now += 300 * H                                       # outage beyond the 500-row window
    feed.record_all([SYM], delay=0)
    assert modes(feed)[1]['oi'] in ('reconcile_due', 'catch_up_exceeds_one_page')
    assert modes(feed)[0] >= {('oi', 'full')}
    state = cursor(feed, 'oi')
    (a, b), = state['gaps']
    assert a == hi + 1 and b >= a and state['cov_lo'] == b + 1
    # the skipped interval is retained as a gap; it is never presented as covered
    venue.now += M15
    feed.record_all([SYM], delay=0)
    assert cursor(feed, 'oi')['gaps'] == [[a, b]]


def test_gap_inside_the_supported_window_blocks_tail_until_a_full_pass_covers_it(tmp_path):
    feed, venue = make(tmp_path)
    feed.record_all([SYM], delay=0)
    state = cursor(feed, 'oi')
    spec = tail.SPECS['oi']
    state['gaps'] = [[venue.now - 10 * M15, venue.now - 9 * M15]]
    state['cursor_key'] = tail.cursor_key('derivative:%s:oi' % SYM, spec.source, state['instrument_id'], spec.period)
    with feed.db:
        tail.write(feed.db, state)
    venue.now += M15
    feed.record_all([SYM], delay=0)
    assert modes(feed)[1]['oi'] == 'gap_in_window'
    assert cursor(feed, 'oi')['gaps'] == []                    # the full pass saw the whole window
    venue.now += M15
    feed.record_all([SYM], delay=0)
    assert modes(feed)[1]['oi'] == 'tail'


def test_plan_is_conservative_on_every_unproven_state():
    spec = tail.SPECS['oi']
    now = T0 + 10 ** 7
    good = dict(unproven=False, gaps=[], cov_hi=now - M15, cov_lo=now - 100 * M15, last_full_ms=now - H)
    assert tail.plan(good, spec, now)[0] == 'tail'
    assert tail.plan(None, spec, now)[2] == 'cold'
    assert tail.plan(dict(good, unproven=True), spec, now)[2] == 'unproven'
    assert tail.plan(dict(good, last_full_ms=now - tail.RECONCILE_MS), spec, now)[2] == 'reconcile_due'
    assert tail.plan(dict(good, cov_hi=now + 1), spec, now)[2] == 'clock_behind_cursor'
    assert tail.plan(dict(good, cov_hi=now - 600 * M15), spec, now)[2] == 'catch_up_exceeds_one_page'


# ── corrections: inside and outside the overlap ────────────────────────
def test_correction_inside_overlap_is_observed_by_the_tail_outside_only_by_reconcile(tmp_path):
    feed, venue = make(tmp_path)
    path = '/futures/data/openInterestHist'
    feed.record_all([SYM], delay=0)
    hi = cursor(feed, 'oi')['cov_hi']
    inside, outside = hi - 3 * M15, hi - 100 * M15       # overlap is 24 rows
    venue.now += M15
    venue.corrections[(path, inside)] = 77.0
    venue.corrections[(path, outside)] = 88.0
    before = revisions(feed, 'oi', outside)
    feed.record_all([SYM], delay=0)
    assert modes(feed)[1]['oi'] == 'tail'
    vals = dict(feed.db.execute("SELECT ts,value FROM derivs WHERE symbol=? AND series='oi'", (SYM,)))
    assert vals[inside] == 77.0                               # seen: inside the overlap
    assert vals[outside] != 88.0 and revisions(feed, 'oi', outside) == before   # NOT seen: finite overlap
    venue.now += tail.RECONCILE_MS
    feed.record_all([SYM], delay=0)
    assert modes(feed)[1]['oi'] == 'reconcile_due'
    vals = dict(feed.db.execute("SELECT ts,value FROM derivs WHERE symbol=? AND series='oi'", (SYM,)))
    assert vals[outside] == 88.0 and revisions(feed, 'oi', outside) == before + 1   # full re-read adds a receipt; the old one is kept


def test_unchanged_overlap_still_gets_new_receipts_and_ancestry(tmp_path):
    feed, venue = make(tmp_path)
    feed.record_all([SYM], delay=0)
    hi = cursor(feed, 'oi')['cov_hi']
    n = revisions(feed, 'oi', hi)
    projection = feed.db.execute("SELECT COUNT(*), SUM(value) FROM derivs WHERE series='oi'").fetchone()
    venue.now += 1000                                          # same numbers, later acquisition
    feed.record_all([SYM], delay=0)
    assert revisions(feed, 'oi', hi) == n + 1                  # new receipt, not de-duplicated
    parent = feed.db.execute("SELECT json_extract(record_json,'$.supersedes') FROM market_revisions "
                             "WHERE series_key=? AND event_ms=? ORDER BY observed_ms DESC LIMIT 1",
                             ('derivative:%s:oi' % SYM, hi)).fetchone()[0]
    assert parent
    assert feed.db.execute("SELECT COUNT(*), SUM(value) FROM derivs WHERE series='oi'").fetchone() == projection


def test_non_valid_response_never_claims_coverage(tmp_path):
    feed, venue = make(tmp_path)
    feed.record_all([SYM], delay=0)
    before = cursor(feed, 'oi')
    venue.now += M15
    real = venue.get(feed)

    def future_rows(path, params, base=D.FAPI):
        data = real(path, params, base)
        for r in feed._local.receipts:
            r['received_ms'] = 0 if 'openInterest' in path else r['received_ms']   # receipt before its events
        return data
    feed._get = future_rows
    feed.record_all([SYM], delay=0)
    assert cursor(feed, 'oi')['cov_hi'] == before['cov_hi']
    assert revisions(feed, 'oi') > 500                                           # still retained, as INVALID


# ── holes: coverage comes from returned event times, not endpoint extrema ──
OI = '/futures/data/openInterestHist'


def hole(stamp):
    return [stamp - M15 + 1, stamp + M15 - 1]


def test_cold_full_with_an_internal_hole_records_it_and_blocks_tail(tmp_path):
    feed, venue = make(tmp_path)
    missing = venue.now // M15 * M15 - 40 * M15
    venue.drop.add((OI, missing))                       # provider omitted this period
    feed.record_all([SYM], delay=0)
    assert cursor(feed, 'oi')['gaps'] == [hole(missing)]
    assert feed.db.execute("SELECT COUNT(*) FROM derivs WHERE series='oi' AND ts=?", (missing,)).fetchone()[0] == 0   # no fabricated row
    venue.now += M15
    feed.record_all([SYM], delay=0)
    assert modes(feed)[1]['oi'] == 'gap_in_window' and cursor(feed, 'oi')['gaps'] == [hole(missing)]


def test_anchored_tail_with_an_internal_hole_records_it_and_forces_full(tmp_path):
    feed, venue = make(tmp_path)
    feed.record_all([SYM], delay=0)
    assert cursor(feed, 'oi')['gaps'] == []
    venue.now += 3 * M15
    missing = venue.now // M15 * M15 - 2 * M15          # inside the tail range, after the anchor
    venue.drop.add((OI, missing))
    feed.record_all([SYM], delay=0)
    assert modes(feed)[1]['oi'] == 'tail'
    state = cursor(feed, 'oi')
    assert state['gaps'] == [hole(missing)] and not state['unproven']
    venue.now += M15
    feed.record_all([SYM], delay=0)
    assert modes(feed)[1]['oi'] == 'gap_in_window'


def test_partial_hole_repair_clears_only_what_was_filled():
    spec = tail.SPECS['oi']
    ident = dict(cursor_key='k', series_key='s', source=spec.source, instrument_id='i', period='15m')
    full = [t * M15 for t in range(0, 100)]
    h1, h2 = 20 * M15, 60 * M15
    cold = tail.advance(None, spec, 'full', [e for e in full if e not in (h1, h2)], 10 ** 9, ident)
    assert cold['gaps'] == [hole(h1), hole(h2)]
    repaired = tail.advance(cold, spec, 'full', [e for e in full if e != h2], 10 ** 9 + 1, ident)
    assert repaired['gaps'] == [hole(h2)]                # h1 filled, h2 still explicit
    # a full response that does not reach the old hole must not clear it
    later = tail.advance(repaired, spec, 'full', [e for e in full if e >= 80 * M15], 10 ** 9 + 2, ident)
    assert later['gaps'] == [hole(h2)] and later['cov_lo'] == 0
    # a response whose own extrema span the hole but omit it re-records it
    again = tail.advance(repaired, spec, 'full', [e for e in full if e != h2 and e >= 50 * M15], 10 ** 9 + 3, ident)
    assert again['gaps'] == [hole(h2)]


def test_funding_is_never_tailed_without_an_interval_witness(tmp_path):
    feed, venue = make(tmp_path)
    for _ in range(3):
        feed.record_all([SYM], delay=0)
        venue.now += M15
        assert funding_pass(feed)['mode'] == 'full'
    assert not [1 for _, p in venue.log if 'startTime' in p and 'fundingRate' in _]
