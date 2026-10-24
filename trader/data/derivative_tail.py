"""Successful-acquisition coverage for the current derivative recorder.

`DerivFeed.record_all` used to re-request the whole supported window of every
series each pass. This module keeps just enough durable state to ask for a
bounded tail instead -- and to refuse to, whenever equivalence with the full
window cannot be shown. It never decides what is true about a value; receipts,
revisions and projections are written exactly as before.

State is one row per (series key, source, instrument, period) in
`deriv_acquisition_cursor`, written in the SAME transaction as the receipts and
projection it describes (`DerivFeed.save`). A fetch that succeeded but whose
save failed therefore leaves the old row; a restart reads the committed row.

What a row claims, and what it does not:

* `cov_lo..cov_hi` is the newest contiguous run of event times that
  successful, fully valid retrievals returned. It is NOT the maximum
  timestamp ever seen: a disjoint retrieval opens a new run and records the
  skipped interval in `gaps` (retained until a later retrieval covers it).
* A tail retrieval proves continuity only if its response contains the
  previous `cov_hi` event again (the anchor). Otherwise the run is marked
  `unproven` and the next pass is a full fetch.
* A finite overlap observes corrections only inside that overlap. Older rows
  are re-read by the periodic full fetch (`RECONCILE_MS`), which is the
  existing request unchanged. Until then an older correction is simply not
  observed; nothing here claims otherwise.
* The daily `derivative_history` pass is NOT used as a witness: it swallows
  per-page provider failures, so its completion does not prove coverage.
"""
from __future__ import annotations

import json
from typing import NamedTuple

#: same value as derivatives.FAPI (a test pins it; importing would be circular)
FAPI = 'https://fapi.binance.com'

FULL, TAIL = 'full', 'tail'
SCHEMA = 'derivative.coverage.v1'
HOUR = 3_600_000
#: A full (existing, unchanged) fetch re-reads the whole supported window at
#: least this often; it is also the outer bound on observing an older-row
#: correction that lies beyond the tail overlap.
RECONCILE_MS = 6 * HOUR
#: gaps retained per cursor row; older ones are merged into one wider gap
MAX_GAPS = 32


class Spec(NamedTuple):
    method: str        # DerivFeed fetcher
    source: str        # exact `source` string the fetcher stamps on its receipts
    period: str        # fetcher's default period ('' when it has none)
    limit: int         # rows one full request returns
    row_ms: int        # smallest event spacing (bounds rows in a tail range)
    tol_ms: int        # largest ordinary event spacing (gap tolerance)
    overlap_ms: int    # tail re-reads this much before the cursor
    window_ms: int     # span a full request reaches back


#: Funding stays a full fetch: Binance funding intervals vary per symbol (and
#: change; see fundingInfo), so no fixed row spacing can bound a tail range or
#: show continuity. A tail needs an exact per-symbol interval witness, which
#: this module does not have. KEEP-full is the conditional, not an 8h assumption.
FULL_ONLY = {'funding': 'variable_interval_no_witness'}

SPECS = {
    'oi': Spec('open_interest', FAPI + '/futures/data/openInterestHist', '15m',
               500, 900_000, 900_000, 24 * 900_000, 500 * 900_000),
    'taker_ratio': Spec('taker_ratio', FAPI + '/futures/data/takerlongshortRatio',
                        '15m', 500, 900_000, 900_000, 24 * 900_000, 500 * 900_000),
    'ls_ratio': Spec('ls_ratio', FAPI + '/futures/data/topLongShortPositionRatio',
                     '15m', 500, 900_000, 900_000, 24 * 900_000, 500 * 900_000),
    'basis': Spec('basis', 'binance:spot+usdm:basis', '1h', 500, HOUR, HOUR,
                  24 * HOUR, 500 * HOUR),
}


def init(conn):
    conn.execute(
        'CREATE TABLE IF NOT EXISTS deriv_acquisition_cursor ('
        'cursor_key TEXT PRIMARY KEY, series_key TEXT NOT NULL, source TEXT NOT NULL, '
        'instrument_id TEXT NOT NULL, period TEXT NOT NULL, cov_lo INTEGER NOT NULL, '
        'cov_hi INTEGER NOT NULL, last_full_ms INTEGER NOT NULL, unproven INTEGER NOT NULL, '
        'gaps_json TEXT NOT NULL, updated_ms INTEGER NOT NULL, passes INTEGER NOT NULL, '
        'schema TEXT NOT NULL)')


def cursor_key(series_key, source, instrument_id, period):
    return json.dumps([series_key, source, instrument_id, period], separators=(',', ':'))


def read(conn, key):
    row = conn.execute(
        'SELECT series_key,source,instrument_id,period,cov_lo,cov_hi,last_full_ms,unproven,'
        'gaps_json,updated_ms,passes FROM deriv_acquisition_cursor WHERE cursor_key=?',
        (key,)).fetchone()
    if row is None:
        return None
    return dict(cursor_key=key, series_key=row[0], source=row[1], instrument_id=row[2],
                period=row[3], cov_lo=int(row[4]), cov_hi=int(row[5]),
                last_full_ms=int(row[6]), unproven=bool(row[7]),
                gaps=[list(g) for g in json.loads(row[8])], updated_ms=int(row[9]),
                passes=int(row[10]))


def plan(state, spec, now_ms):
    """-> (mode, start_ms | None, reason). Anything unproven is FULL."""
    if state is None:
        return FULL, None, 'cold'
    if state['unproven']:
        return FULL, None, 'unproven'
    if any(b >= now_ms - spec.window_ms for _, b in state['gaps']):
        return FULL, None, 'gap_in_window'
    if state['cov_hi'] > now_ms or state['last_full_ms'] > now_ms:
        return FULL, None, 'clock_behind_cursor'
    if now_ms - state['last_full_ms'] >= RECONCILE_MS:
        return FULL, None, 'reconcile_due'
    start = max(0, state['cov_hi'] - spec.overlap_ms)
    # One request must be able to return the entire range, so the order the
    # venue pages in never matters: otherwise fall back to the full window.
    if (now_ms - start) // spec.row_ms > spec.limit - 1:
        return FULL, None, 'catch_up_exceeds_one_page'
    return TAIL, start, 'tail'


def _subtract(gaps, lo, hi):
    out = []
    for a, b in gaps:
        if b < lo or a > hi:
            out.append([a, b])
            continue
        if a < lo:
            out.append([a, lo - 1])
        if b > hi:
            out.append([hi + 1, b])
    return out


def _normalize(gaps, tol_ms=0, prune=False):
    """Sorted, merged, and widened (never forgotten) to MAX_GAPS. With
    `prune`, a leftover piece whose bounding events are within one ordinary
    spacing is dropped: it cannot contain an expected observation."""
    gaps = sorted(g for g in gaps if g[0] <= g[1] and (not prune or (g[1] + 1) - (g[0] - 1) > tol_ms))
    merged = []
    for a, b in gaps:
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    while len(merged) > MAX_GAPS:
        merged[:2] = [[merged[0][0], max(merged[0][1], merged[1][1])]]
    return merged


def runs_of(events, tol_ms):
    """Maximal stretches of actually returned event times, consecutive ones at
    most one ordinary spacing apart. Between two runs is a hole."""
    runs = [[events[0], events[0]]]
    for e in events[1:]:
        if e - runs[-1][1] <= tol_ms:
            runs[-1][1] = e
        else:
            runs.append([e, e])
    return runs


def advance(prev, spec, mode, events, now_ms, identity, req_start=None):
    """Next state after a fully valid retrieval returning `events` (ms).

    Coverage is built from the returned event times themselves: a stretch is
    verified only between consecutive returned events no further apart than
    one ordinary spacing; anything wider is recorded as a hole (provider
    omission is missingness, never an observation). Only holes inside a
    verified stretch are cleared. `prev` is read inside the committing
    transaction. Returns None when nothing can be claimed."""
    events = sorted(set(int(e) for e in events))
    lo, hi = events[0], events[-1]
    runs = runs_of(events, spec.tol_ms)
    holes = [[runs[i][1] + 1, runs[i + 1][0] - 1] for i in range(len(runs) - 1)]
    if mode == TAIL and req_start is not None and lo - req_start > spec.tol_ms:
        holes.insert(0, [int(req_start), lo - 1])      # requested range begins with nothing
    if prev is None:
        if mode != FULL:
            return None
        return dict(identity, cov_lo=lo, cov_hi=hi, last_full_ms=now_ms, unproven=False,
                    gaps=_normalize(holes), updated_ms=now_ms, passes=1)
    state = dict(prev, updated_ms=now_ms, passes=prev['passes'] + 1)
    gaps = [list(g) for g in prev['gaps']]
    if mode == FULL:
        if prev['cov_hi'] + spec.tol_ms < lo:          # outage / retention: skipped interval
            gaps.append([prev['cov_hi'] + 1, lo - 1])
            state['cov_lo'], state['cov_hi'] = lo, hi
        else:
            state['cov_lo'] = min(prev['cov_lo'], lo)
            state['cov_hi'] = max(prev['cov_hi'], hi)
        state['last_full_ms'], state['unproven'] = now_ms, False
    elif prev['cov_hi'] in set(events):                # anchor seen
        state['cov_hi'] = max(prev['cov_hi'], hi)
    else:                                              # continuity not shown
        if lo > prev['cov_hi']:
            gaps.append([prev['cov_hi'] + 1, lo - 1])
            state['cov_lo'], state['cov_hi'] = lo, hi
        else:
            state['cov_hi'] = max(prev['cov_hi'], hi)
        state['unproven'] = True
    for a, b in runs:                                  # clear only what was actually filled
        gaps = _subtract(gaps, a, b)
    state['gaps'] = _normalize(_normalize(gaps, spec.tol_ms, prune=True) + holes)
    return state


def write(conn, state):
    conn.execute(
        'INSERT INTO deriv_acquisition_cursor VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) '
        'ON CONFLICT(cursor_key) DO UPDATE SET cov_lo=excluded.cov_lo, cov_hi=excluded.cov_hi, '
        'last_full_ms=excluded.last_full_ms, unproven=excluded.unproven, '
        'gaps_json=excluded.gaps_json, updated_ms=excluded.updated_ms, passes=excluded.passes',
        (state['cursor_key'], state['series_key'], state['source'], state['instrument_id'],
         state['period'], state['cov_lo'], state['cov_hi'], state['last_full_ms'],
         int(state['unproven']), json.dumps(state['gaps'], separators=(',', ':')),
         state['updated_ms'], state['passes'], SCHEMA))
