"""Import the CoinGecko website's published TOTAL market-cap CSV into a
DETACHED research reference store.

Header `snapped_at` (Unix ms), `market_cap`, `total_volume`. Each row is a
daily SNAPSHOT of the provider's published figure at 00:00 UTC, not an OHLC bar:
only `close` (market cap) and `volume` (the provider's 24h total volume) are
stored; open/high/low stay missing. Nothing is recomputed, aggregated, filled
or inferred, and no ex-BTC / ex-BTC-ETH series is derived here.

The exact file bytes are retained (hash + text) as acquisition evidence. Values
are AS_RETURNED_AT_CAPTURE: the export carries no original release times, so it
is retrospective research input, never point-in-time historical proof. The
receipt clock is the capture time supplied by the caller; it is never
backdated, so a store written now cannot serve a historical as-of replay.
"""
from __future__ import annotations

import hashlib
import io
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .references import DAY, REFS, RefStore

KEY = 'cg_total_daily'
HEADER = ('snapped_at', 'market_cap', 'total_volume')
SCHEMA = 'published-market-csv.v1'
SOURCE = 'coingecko-website-csv:total_market_cap_usd'
SOURCE_URL = 'https://www.coingecko.com/en/charts'
DOWNLOAD_URL = 'https://www.coingecko.com/etl2/global_charts/usd/total_market_cap.csv'
MAX_BYTES = 50_000_000
_CLOCK_SLACK_MS = 60_000


def load_csv(path, *, captured_ms, now_ms=None) -> tuple:
    """(key, frame): parsed exact values with capture identity in `attrs`.

    Refuses rather than repairs: wrong header, non-numeric or non-finite
    values, non-positive market cap, negative volume (zero is a valid
    historical volume), non-midnight or duplicate or unordered stamps,
    timestamps that are not Unix milliseconds, a row after the capture, a
    capture in the future, or a capture older than the file itself."""
    path = Path(path)
    captured_ms, now = int(captured_ms), int(time.time() * 1000) if now_ms is None else int(now_ms)
    if captured_ms > now + _CLOCK_SLACK_MS:
        raise ValueError('capture time is in the future')
    st = path.stat()
    if st.st_mtime_ns // 1_000_000 > captured_ms + _CLOCK_SLACK_MS:
        raise ValueError('capture time precedes the file: receipt cannot be backdated')
    if st.st_size > MAX_BYTES:
        raise ValueError('published csv exceeds bounded import size')
    raw = path.read_bytes()
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('published csv is not utf-8') from exc
    body = text[1:] if text.startswith('﻿') else text
    frame = pd.read_csv(io.StringIO(body), dtype=str, keep_default_na=False)
    if tuple(c.strip() for c in frame.columns) != HEADER:
        raise ValueError('published csv header must be exactly ' + ','.join(HEADER))
    frame.columns = HEADER
    if frame.empty:
        raise ValueError('published csv has no rows')
    try:
        stamp = frame['snapped_at'].map(int)
        cap = frame['market_cap'].map(float)
        vol = frame['total_volume'].map(float)
    except ValueError as exc:
        raise ValueError('published csv has a non-numeric field') from exc
    stamp = stamp.to_numpy(dtype='int64')
    cap, vol = cap.to_numpy(dtype=float), vol.to_numpy(dtype=float)
    if stamp.min() < 10 ** 11 or stamp.max() > 10 ** 14:
        raise ValueError('snapped_at must be Unix milliseconds')
    if (stamp % DAY).any():
        raise ValueError('snapped_at must be 00:00 UTC daily stamps')
    if (np.diff(stamp) <= 0).any():
        raise ValueError('snapped_at duplicates or unordered')
    if stamp.max() > captured_ms:
        raise ValueError('published csv has a row after the capture time')
    if not (np.isfinite(cap).all() and (cap > 0).all()):
        raise ValueError('market_cap must be finite and positive')
    if not (np.isfinite(vol).all() and (vol >= 0).all()):
        raise ValueError('total_volume must be finite and non-negative')
    gaps = int((np.diff(stamp) > DAY).sum())
    out = pd.DataFrame({'ts': pd.to_datetime(stamp, unit='ms', utc=True), 'close': cap, 'volume': vol})
    sha = hashlib.sha256(raw).hexdigest()
    out.attrs.update(source=SOURCE, request_started_ms=captured_ms, raw_source_record=dict(
        schema=SCHEMA, provider='coingecko-website-csv', series='total_market_cap_usd', unit='USD',
        definition='daily snapshot of published total crypto market cap at snapped_at; not an OHLC bar; '
                   'volume is the provider total_volume',
        csv_sha256=sha, captured_ms=captured_ms, raw_csv=text, vintage='AS_RETURNED_AT_CAPTURE',
        rows_in_file=int(len(out)), calendar_gap_count=gaps, source_url=SOURCE_URL, download_url=DOWNLOAD_URL))
    return KEY, out


def _refuse_live_target(db_path) -> Path:
    from ..core.config import ROOT
    from ..research.closure.manifest import _LIVE_NAMES, _LIVE_ROOTS, _configured_live_files
    p = Path(db_path).resolve()
    roots = tuple(Path(r).resolve() for r in (ROOT / 'data', Path('/mnt/luffy-data/LUFFY'), *_LIVE_ROOTS))
    if (p.name in _LIVE_NAMES or any(r == p or r in p.parents for r in roots) or
            any(p == live.resolve() for live in _configured_live_files()) or
            any((p.parent / n).exists() for n in ('luffy.db', 'candles.db', 'derivs.db'))):
        raise ValueError('detached reference store must not be a live or production database')
    return p


def import_csv(path, db_path, *, captured_ms, now_ms=None) -> dict:
    """Write the export into the detached store at `db_path` and report what
    was retained. The store is created if absent and is never a live database."""
    target = _refuse_live_target(db_path)
    key, frame = load_csv(path, captured_ms=captured_ms, now_ms=now_ms)
    store = RefStore(target, clock_ms=lambda: int(captured_ms))
    try:
        written = store.save(key, frame, now_ms=int(captured_ms))
    finally:
        store.db.close()
    raw = frame.attrs['raw_source_record']
    return dict(status='IMPORTED', key=key, rows_in_file=raw['rows_in_file'], rows_stored=written,
                rows_not_closed_at_capture=raw['rows_in_file'] - written, csv_sha256=raw['csv_sha256'],
                captured_ms=int(captured_ms), vintage=raw['vintage'], calendar_gap_count=raw['calendar_gap_count'],
                first_ms=int(frame['ts'].iloc[0].value // 10 ** 6), last_ms=int(frame['ts'].iloc[-1].value // 10 ** 6),
                close_after_ms=REFS[key].close_after_ms, db_path=str(target))


def provenance(store: RefStore, df) -> dict:
    """Capture identity of a retrospective reference frame, read back from the
    retained raw source (never the filesystem): csv hash, capture time, vintage."""
    hashes = sorted({json.loads(r)['raw_source_hash'] for r in df['raw_json']})
    sources = []
    for h in hashes:
        row = store.db.execute('SELECT raw_json FROM market_raw_sources WHERE content_hash=?', (h,)).fetchone()
        if not row:
            raise ValueError('retained raw source missing for ' + h[:12])
        rec = json.loads(row[0])
        sources.append(dict({k: rec.get(k) for k in ('schema', 'provider', 'series', 'csv_sha256', 'captured_ms', 'vintage',
                                                       'rows_in_file', 'calendar_gap_count', 'source_url')}, raw_source_hash=h))
    return dict(sources=sources, rows=int(len(df)), first_ms=int(df['event_time_ms'].min()),
                last_ms=int(df['event_time_ms'].max()), last_observed_ms=int(df['observed_at_ms'].max()))


