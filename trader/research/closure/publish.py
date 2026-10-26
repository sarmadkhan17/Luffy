"""Publish a consistent, BOUNDED, exact-cut study snapshot through the data service's own reader contract.

A physical copy of every timeframe of the whole cache (candles 5.2 GB, derivatives 6.4 GB) is not what a study needs
and was measured to exceed the slot. This reads ONLY the declared rows through ONE read transaction of the source
(WAL snapshot isolation) per source. Candles, receipts and references share one source transaction; derivatives
have their own transaction at the same event cut, not a cross-database atomic snapshot. Retained receipts and bodies are copied
together without expanding repeated raw responses; the existing reader verifies their logical form. Provenance is
never weakened or synthesised. The source is opened read-only, never written, and is never copied as a file. The
caller runs this in a killable child with a deadline.

Bounds: symbols x window bars (+ declared warmup) of ONE timeframe, the reference keys' rows in the window, and the
declared derivative series for those symbols. `max_rows` refuses an unexpectedly large request instead of filling disk.
"""
from __future__ import annotations

import os
import json
import sqlite3
import sys
import time
import uuid
from collections import OrderedDict
from contextlib import closing
from pathlib import Path

from ...core.types import TF_MS
from ...data import market_provenance as mp
from . import ClosureRefused
from ..predictive_experiment import readonly

DERIV_SERIES = ('funding', 'oi', 'taker_ratio', 'ls_ratio', 'ls_account_ratio', 'basis')


def publish_universe(source, destination, *, start_ms, end_ms, deadline=None, max_bytes=64*1024*1024):
    """Bounded selection evidence only; never copy/open the whole Journal writable."""
    from ...engine.protection_snapshot import ro_connect
    target=Path(destination)
    if target.exists():
        raise ClosureRefused('universe_destination_exists')
    bound=Bounds(5000,max_bytes,deadline)
    try:
        with closing(ro_connect(source)) as src, sqlite3.connect(target) as dst:
            src.execute('BEGIN');bound.attach(src)
            dst.execute('CREATE TABLE universe_revisions(revision_id TEXT,environment TEXT,selection_version TEXT,source TEXT,'
                        'observed_at_ms INTEGER,members_json TEXT,exclusions_json TEXT,config_json TEXT,recorded_ms INTEGER)')
            dst.execute('CREATE INDEX universe_revision_cut ON universe_revisions(environment,observed_at_ms)')
            dst.execute('CREATE TABLE universe_selection_evidence(revision_id TEXT,environment TEXT,payload TEXT,sha256 TEXT,'
                        'PRIMARY KEY(environment,revision_id))')
            if _has(src,'universe_revisions'):
                rows=bound.take(src,'SELECT * FROM universe_revisions WHERE observed_at_ms BETWEEN ? AND ? '
                                'OR (environment,observed_at_ms) IN (SELECT environment,MAX(observed_at_ms) FROM universe_revisions WHERE observed_at_ms<? '
                                'GROUP BY environment)',(start_ms,end_ms,start_ms))
                for row in rows:
                    dst.execute('INSERT INTO universe_revisions VALUES(?,?,?,?,?,?,?,?,?)',tuple(row))
                    if _has(src,'universe_selection_evidence'):
                        for ev in bound.take(src,'SELECT * FROM universe_selection_evidence WHERE revision_id=? AND environment=?',
                                             (row[0],row[1])):
                            dst.execute('INSERT OR IGNORE INTO universe_selection_evidence VALUES(?,?,?,?)',tuple(ev))
        return dict(path=str(target),bytes=target.stat().st_size,rows=bound.used_rows,
                    payload_bytes=bound.used_bytes,scope='RETAINED_SELECTION_INPUTS_ONLY')
    except BaseException:
        target.unlink(missing_ok=True)
        raise


class Bounds:
    """One shared input-work/payload ceiling across all source transactions and outputs."""
    def __init__(self, rows, size, deadline):
        self.rows, self.size, self.deadline = int(rows), int(size), deadline
        self.used_rows = self.used_bytes = 0
        self.raw_seen = set()
        self.raw_texts = OrderedDict()
        # A disk publication allowance is not a RAM allowance. This also fits
        # one maximum 8MiB UTF-8 body represented as a wide Python string.
        self.text_cache_limit = 40 * 1024 * 1024
        self.text_cache_bytes = 0
        self.checked_texts = {}
        self.raw_parents = {}

    def remember_text(self,key,text):
        old=self.raw_texts.pop(key,None)
        if old is not None:self.text_cache_bytes-=sys.getsizeof(old)
        self.raw_texts[key]=text
        self.text_cache_bytes+=sys.getsizeof(text)
        while len(self.raw_texts)>1 and self.text_cache_bytes>self.text_cache_limit:
            evicted,value=self.raw_texts.popitem(last=False)
            self.text_cache_bytes-=sys.getsizeof(value)
            self.checked_texts.pop(evicted,None)

    def clear_text_cache(self):
        self.raw_texts.clear()
        self.checked_texts.clear()
        self.text_cache_bytes=0

    def charge(self, row):
        _tick(self.deadline)
        size = sum(len(v.encode()) if isinstance(v, str) else len(v) if isinstance(v, bytes) else 16 for v in row)
        self.used_rows += 1
        self.used_bytes += size
        if self.used_rows > self.rows:
            raise ClosureRefused('publish_rows_exceed_bound')
        if self.used_bytes > self.size:
            raise ClosureRefused('publish_bytes_exceed_bound')

    def iterate(self, db, sql, params=()):
        _tick(self.deadline)
        cur = db.execute(sql + ' LIMIT ?', (*params, max(1, self.rows - self.used_rows + 1)))
        try:
            for row in cur:
                self.charge(row)             # enforce before retaining or writing a row
                yield row
        finally:
            cur.close()

    def take(self, db, sql, params=()):
        return list(self.iterate(db, sql, params))

    def attach(self, db):
        db.set_progress_handler(lambda: int(self.deadline is not None and time.monotonic() > self.deadline), 2000)


def _raw(src, dst, key, bound, *, want_text=False):
    if not key:return
    seen=key in bound.raw_seen
    if seen and not want_text:return
    if seen and key in bound.raw_texts:
        bound.raw_texts.move_to_end(key)
        return
    length = src.execute('SELECT length(CAST(raw_json AS BLOB)) FROM market_raw_sources WHERE content_hash=?', (key,)).fetchone()
    if length and length[0] > min(8 * 1024 * 1024, bound.size - bound.used_bytes):
        raise ClosureRefused('publish_bytes_exceed_bound')
    for row in bound.take(src, 'SELECT * FROM market_raw_sources WHERE content_hash=?', (key,)):
        if not seen:dst.execute('INSERT OR IGNORE INTO market_raw_sources VALUES (?,?,?)', row)
        if want_text:bound.remember_text(key,row[-1])
    bound.raw_seen.add(key)


def _tick(deadline):
    if deadline is not None and time.monotonic() > deadline:
        raise ClosureRefused('publish_deadline')


def _ddl(src, names) -> list:
    return [r[0] for r in src.execute("SELECT sql FROM sqlite_master WHERE tbl_name IN (%s) AND sql IS NOT NULL "
                                      "AND name NOT LIKE 'sqlite_%%' ORDER BY "
                                      "CASE type WHEN 'table' THEN 0 WHEN 'index' THEN 1 "
                                      "WHEN 'view' THEN 2 WHEN 'trigger' THEN 3 ELSE 4 END,name"
                                      % ','.join('?' * len(names)), names)]


def _has(src, name) -> bool:
    return bool(src.execute('SELECT 1 FROM sqlite_master WHERE name=?', (name,)).fetchone())


def _candle_tails(src, tf, cut, symbols, deadline):
    """Seek the (symbol,tf,ts) index, rather than scan every cached timeframe for MAX(ts).

    The same closed-event maximum is preserved. With no explicit universe, skip directly to
    the next distinct symbol; a normal DISTINCT scan would still walk millions of index rows.
    """
    names = []
    row = src.execute('SELECT symbol FROM candles ORDER BY symbol LIMIT 1').fetchone()
    while row is not None:
        _tick(deadline)
        names.append(row[0])
        row = src.execute('SELECT symbol FROM candles WHERE symbol>? ORDER BY symbol LIMIT 1', (row[0],)).fetchone()
    tails = {}
    for sym in names:
        _tick(deadline)
        row = src.execute('SELECT ts FROM candles WHERE symbol=? AND tf=? AND ts<=? ORDER BY ts DESC LIMIT 1',
                          (sym, tf, cut)).fetchone()
        if row is not None:
            tails[sym] = int(row[0])
    return names if symbols is None else list(symbols), tails


def _copy_revisions(src, dst, series_key, lo, hi, deadline, counters, bound, retrospective=False, latest_before=None):
    if retrospective:
        # Match retrospective_load's exact per-event winner, including rowid
        # ties and invalid latest receipts. Older versions cannot affect that
        # reader; replay publications still retain the full revision history.
        cutoff = hi + 1 if latest_before is None else latest_before
        # Stream MAX(observed_ms) in event-index order, then choose the
        # largest rowid at that exact maximum. The old window rank sorted
        # every superseded revision. Only actual winners reach body decoding.
        sql = ('WITH newest AS MATERIALIZED ('
               'SELECT event_ms,MAX(observed_ms) AS observed FROM market_revisions '
               'WHERE series_key=? AND event_ms>=? AND event_ms<? GROUP BY event_ms) '
               'SELECT * FROM market_revisions WHERE rowid IN ('
               'SELECT rowid FROM market_revisions WHERE series_key=? AND event_ms>=? AND event_ms<=? '
               'UNION ALL SELECT (SELECT MAX(rowid) FROM market_revisions '
               'WHERE series_key=? AND event_ms=newest.event_ms AND observed_ms=newest.observed) '
               'FROM newest) ORDER BY event_ms,observed_ms,rowid')
        params=(series_key,lo,min(cutoff,hi+1),series_key,max(lo,cutoff),hi,series_key)
    else:
        sql = ('SELECT * FROM market_revisions WHERE series_key=? AND event_ms>=? AND event_ms<=? '
               'ORDER BY event_ms,observed_ms,rowid')
        params = (series_key, lo, hi)
    rows = []
    retained_bytes = 0
    for r in bound.iterate(src, sql, params):
        row_bytes=sys.getsizeof(r)+sum(sys.getsizeof(value) for value in r)
        if rows and retained_bytes+row_bytes>1024*1024:
            dst.executemany('INSERT OR IGNORE INTO market_revisions VALUES (?,?,?,?,?,?)', rows)
            counters['revisions'] += len(rows)
            rows.clear()
            retained_bytes=0
        _tick(deadline)
        rec=json.loads(r[-1]);raw_marker=rec.get('raw_json')
        for field in ('source','raw_json'):
            marker=rec.get(field)
            if not isinstance(marker,str) or not marker.startswith(mp.TEXT_REF):continue
            key=marker[len(mp.TEXT_REF):]
            if len(key)!=64 or any(c not in '0123456789abcdef' for c in key):
                raise ValueError('market_source_reference_invalid')
            _raw(src,dst,key,bound,want_text=True)
            text=bound.raw_texts.get(key)
            # One pinned source transaction, immutable Python strings. A
            # unique body is checked once; replacing the cached object forces
            # another check. Consumers still perform their normal full checks.
            if bound.checked_texts.get(key) is not text or text is None:
                if not isinstance(text,str) or mp.text_reference(text)!=marker:
                    raise ValueError('market_source_reference_missing_or_corrupt')
                bound.checked_texts[key]=text
            rec[field]=text
        _raw(src,dst,rec.get('content_hash'),bound)
        parent_key=raw_marker if isinstance(raw_marker,str) and raw_marker.startswith(mp.TEXT_REF) else None
        if parent_key is not None and parent_key in bound.raw_parents:
            raw_hash=bound.raw_parents[parent_key]
        else:
            try:raw_hash=json.loads(rec.get('raw_json') or 'null').get('raw_source_hash')
            except (ValueError,AttributeError):raw_hash=None
            if parent_key is not None:bound.raw_parents[parent_key]=raw_hash
        if raw_hash:_raw(src,dst,raw_hash,bound)
        rows.append(r)
        retained_bytes+=row_bytes
        if len(rows) == 256 or retained_bytes>=1024*1024:
            dst.executemany('INSERT OR IGNORE INTO market_revisions VALUES (?,?,?,?,?,?)', rows)
            counters['revisions'] += len(rows)
            rows.clear()
            retained_bytes=0
    # Preserve physical references plus their checked immutable bodies; expanding every repeated
    # response into each receipt would make a bounded source into an enormous duplicated snapshot.
    dst.executemany('INSERT OR IGNORE INTO market_revisions VALUES (?,?,?,?,?,?)', rows)
    counters['revisions'] += len(rows)


def publish_exact_cut(src_candles, dst_candles, *, tf, window_bars, symbols=None, deriv_src=None, dst_derivs=None,
                      deriv_series=DERIV_SERIES, deadline=None, max_rows=5_000_000,
                      max_bytes=512 * 1024 * 1024, now_ms=None, coverage_filter=None, retrospective=False,
                      development_derivatives_only=False, _between=None) -> dict:
    """Write the bounded study snapshot. `deadline` is a monotonic instant. Returns what was published and its cut."""
    step = TF_MS[tf]
    if coverage_filter is not None:
        if (set(coverage_filter) != {'fit_bars', 'reserve_bars', 'max_gap_bars'} or
                any(type(v) is not int or v < 0 for v in coverage_filter.values()) or not coverage_filter['fit_bars']):
            raise ClosureRefused('publish_coverage_filter_invalid')
    if development_derivatives_only and coverage_filter is None:
        raise ClosureRefused('development_derivatives_require_declared_window')
    _tick(deadline)
    dst_candles = Path(dst_candles)
    if Path(src_candles).resolve() == dst_candles.resolve():
        raise ClosureRefused('publish_destination_is_source')
    bound = Bounds(max_rows, max_bytes, deadline)
    clock_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    destinations = [dst_candles] + ([Path(dst_derivs)] if deriv_src is not None and dst_derivs is not None else [])
    if len({p.resolve() for p in destinations}) != len(destinations):
        raise ClosureRefused('publish_destinations_collide')
    sources = {Path(p).resolve() for p in (src_candles, deriv_src) if p is not None}
    if any(p.resolve() in sources for p in destinations):
        raise ClosureRefused('publish_destination_is_source')
    if any(p.exists() for p in destinations):
        raise ClosureRefused('publish_destination_exists')
    token = uuid.uuid4().hex
    tmp = dst_candles.with_name('.publishing-' + token + '-' + dst_candles.name)
    staged_derivs = Path(dst_derivs).with_name('.publishing-' + token + '-' + Path(dst_derivs).name) if len(destinations) > 1 else None
    installed = []
    success = False
    dst_candles.parent.mkdir(parents=True, exist_ok=True)
    counters = dict(rows=0, revisions=0)
    out = {}
    try:
        with closing(readonly(src_candles)) as src, closing(sqlite3.connect(tmp)) as dst:
            bound.attach(src)
            bound.attach(dst)
            src.execute('BEGIN')                                         # ONE read transaction: one consistent instant
            names, tails = _candle_tails(src, tf, (clock_ms // step) * step - step, symbols, deadline)
            if not tails:
                raise ClosureRefused('publish_source_empty')
            last = max(tails.values())
            if _between is not None:
                _between()                                               # test seam: a writer commits during the publish
            as_of = int(last) + step
            # Development can use retained latest observations. The reserved
            # final-test region must keep every receipt for later as-of replay.
            latest_before = as_of - coverage_filter['reserve_bars'] * step if coverage_filter else None
            start = as_of - int(window_bars) * step
            tables = [t for t in ('candles', 'candle_floor', 'refs', 'market_revisions', 'market_raw_sources') if _has(src, t)]
            for sql in _ddl(src, tables):
                dst.execute(sql)
            mp.init(dst)
            syms = names if symbols is not None else [sym for sym in names if tails.get(sym, -1) >= start]
            cohort = None
            if coverage_filter is not None:
                fit_start = as_of - (coverage_filter['fit_bars'] + coverage_filter['reserve_bars']) * step
                fit_end = as_of - coverage_filter['reserve_bars'] * step
                gap = coverage_filter['max_gap_bars'] * step
                selected, excluded = [], []
                for sym in syms:
                    _tick(deadline)
                    first = src.execute('SELECT ts FROM candles WHERE symbol=? AND tf=? AND ts>=? AND ts<=? '
                                        'ORDER BY ts LIMIT 1', (sym, tf, start, last)).fetchone()
                    if first is not None and first[0] <= fit_start + gap and tails.get(sym, -1) >= fit_end - step - gap:
                        selected.append(sym)
                    else:
                        excluded.append(sym)
                # This only removes symbols that CANNOT pass the existing fit
                # coverage rule. It grants no eligibility: full gap/quality/
                # receipt checks still run on the published cohort below.
                cohort = dict(considered=len(syms), published=len(selected), excluded=len(excluded),
                              excluded_symbols=excluded, fit_window_ms=[fit_start, fit_end],
                              reason='stored endpoints cannot cover the predeclared fit window; no value or performance ranking')
                syms = selected
            cols = len(src.execute('SELECT * FROM candles LIMIT 0').description)
            for sym in syms:
                _tick(deadline)
                rows = bound.take(src, 'SELECT * FROM candles WHERE symbol=? AND tf=? AND ts>=? AND ts<=? ORDER BY ts',
                                   (sym, tf, start, last))
                dst.executemany('INSERT INTO candles VALUES (%s)' % ','.join('?' * cols), rows)
                counters['rows'] += len(rows)
                if _has(src, 'candle_floor'):
                    floor = src.execute('SELECT * FROM candle_floor WHERE symbol=? AND tf=?', (sym, tf)).fetchall()
                    dst.executemany('INSERT INTO candle_floor VALUES (%s)' % ','.join('?' * len(floor[0])), floor) if floor else None
                if _has(src, 'market_revisions'):
                        _copy_revisions(src, dst, f'candle:{sym}:{tf}', start, last, deadline, counters, bound, retrospective, latest_before)
            if counters['rows'] > max_rows:
                raise ClosureRefused('publish_rows_exceed_bound')
            if _has(src, 'refs'):
                keys = [r[0] for r in src.execute('SELECT DISTINCT key FROM refs')]
                rcols = len(src.execute('SELECT * FROM refs LIMIT 0').description)
                for k in keys:
                    _tick(deadline)
                    rows = bound.take(src, 'SELECT * FROM refs WHERE key=? AND ts>=? AND ts<=?', (k, start, last))
                    dst.executemany('INSERT INTO refs VALUES (%s)' % ','.join('?' * rcols), rows)
                    counters['rows'] += len(rows)
                    if _has(src, 'market_revisions'):
                        _copy_revisions(src, dst, 'reference:' + k, start, last, deadline, counters, bound, retrospective, latest_before)
            dst.commit()
            out = dict(as_of_ms=as_of, start_ms=start, symbols=syms, rows=counters['rows'], revisions=counters['revisions'],
                       captured_at_ms=clock_ms, consistency='SINGLE_READ_TRANSACTION_EXACT_CUT',
                       revision_scope='RETROSPECTIVE_LATEST_PER_EVENT' if retrospective else 'FULL_REPLAY_HISTORY',
                       full_revision_history_from_ms=latest_before if retrospective else start,
                       source='candles/references one read transaction; derivatives separate transaction at same event cut')
            if cohort is not None:
                out['coverage_filter'] = cohort
        out['derivs'] = None
        if deriv_src is not None and dst_derivs is not None:
            # Fit windows are [start,end); the derivative copier uses an
            # inclusive SQL endpoint. Do not copy the first reserved event
            # into a development-only snapshot.
            deriv_last = min(as_of,latest_before)-1 if development_derivatives_only else as_of
            out['derivs'] = _publish_derivs(deriv_src, staged_derivs, syms, deriv_series, start, deriv_last, deadline, bound, retrospective, latest_before)
            out['derivs']['data_scope'] = 'DEVELOPMENT_ONLY' if development_derivatives_only else 'DEVELOPMENT_AND_PROTECTED'
            out['cross_source_atomic'] = False
        with closing(sqlite3.connect(tmp)) as chk:                       # settle: one quiescent file, no sidecars
            chk.execute('PRAGMA journal_mode=DELETE')
        for suffix in ('-wal', '-shm', '-journal'):
            Path(str(tmp) + suffix).unlink(missing_ok=True)
        _tick(deadline)
        disk_bytes = tmp.stat().st_size + (staged_derivs.stat().st_size if staged_derivs is not None else 0)
        if disk_bytes > max_bytes:
            raise ClosureRefused('publish_disk_bytes_exceed_bound')
        # Never replace an earlier study or another publisher's output. Two outputs
        # are not atomic: remove our first link if publishing the second fails.
        for source, target in [(tmp, dst_candles)] + ([(staged_derivs, Path(dst_derivs))] if staged_derivs is not None else []):
            os.link(source, target)
            installed.append((target, source.stat().st_ino))
        if out['derivs'] is not None:
            out['derivs']['path'] = str(dst_derivs)
        out['bytes'] = dst_candles.stat().st_size
        out['path'] = str(dst_candles)
        out.update(input_rows=bound.used_rows, input_payload_bytes=bound.used_bytes,
                   payload_byte_limit=bound.size, row_limit=bound.rows)
        success = True
        return out
    except sqlite3.OperationalError:
        _tick(deadline)
        raise
    finally:
        if not success:
            for target, inode in installed:
                if target.exists() and target.stat().st_ino == inode:
                    target.unlink()
        if staged_derivs is not None:
            staged_derivs.unlink(missing_ok=True)
        for p in (tmp, Path(str(tmp) + '-wal'), Path(str(tmp) + '-shm'), Path(str(tmp) + '-journal')):
            p.unlink(missing_ok=True)


def _publish_derivs(src_path, dst_path, symbols, series, lo, hi, deadline, bound, retrospective=False, latest_before=None) -> dict:
    bound.raw_seen.clear()                     # each destination needs its own referenced immutable bodies
    dst_path = Path(dst_path)
    if Path(src_path).resolve() == dst_path.resolve():
        raise ClosureRefused('publish_destination_is_source')
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst_path.with_name('.publishing-' + uuid.uuid4().hex + '-' + dst_path.name)
    for p in (tmp, Path(str(tmp) + '-wal'), Path(str(tmp) + '-shm')):
        p.unlink(missing_ok=True)
    counters = dict(rows=0, revisions=0)
    try:
        with closing(readonly(src_path)) as src, closing(sqlite3.connect(tmp)) as dst:
            # Separate source transaction AND destination: even an identical
            # content key must be read, verified and written again here.
            bound.raw_seen.clear()
            bound.clear_text_cache()
            bound.raw_parents.clear()
            bound.attach(src)
            bound.attach(dst)
            src.execute('BEGIN')
            src.execute('SELECT 1 FROM sqlite_master LIMIT 1').fetchone()
            for sql in _ddl(src, [t for t in ('derivs',) if _has(src, t)]):
                dst.execute(sql)
            mp.init(dst)
            ncol = len(src.execute('SELECT * FROM derivs LIMIT 0').description)
            for sym in symbols:
                for name in series:
                    _tick(deadline)
                    rows = bound.take(src, 'SELECT * FROM derivs WHERE symbol=? AND series=? AND ts>=? AND ts<=? ORDER BY ts',
                                       (sym, name, lo, hi))
                    dst.executemany('INSERT INTO derivs VALUES (%s)' % ','.join('?' * ncol), rows)
                    counters['rows'] += len(rows)
                    if _has(src, 'market_revisions'):
                        _copy_revisions(src, dst, f'derivative:{sym}:{name}', lo, hi, deadline, counters, bound, retrospective, latest_before)
            actual_range = list(dst.execute('SELECT MIN(ts),MAX(ts) FROM derivs').fetchone())
            dst.commit()
        with closing(sqlite3.connect(tmp)) as chk:
            chk.execute('PRAGMA journal_mode=DELETE')
        for suffix in ('-wal', '-shm', '-journal'):
            Path(str(tmp) + suffix).unlink(missing_ok=True)
        os.replace(tmp, dst_path)
        return dict(path=str(dst_path), rows=counters['rows'], revisions=counters['revisions'], bytes=dst_path.stat().st_size,
                    event_range_ms=actual_range, requested_event_range_ms=[lo, hi], consistency='SINGLE_READ_TRANSACTION')
    finally:
        for p in (tmp, Path(str(tmp) + '-wal'), Path(str(tmp) + '-shm'), Path(str(tmp) + '-journal')):
            p.unlink(missing_ok=True)
