"""Shared receipt/revision helpers for the existing market-data SQLite stores.

This is storage metadata, not another feed. Legacy projections are retained
for inventory; only retained revisions can establish point-in-time truth.
Provider publication time is never inferred from a historical event stamp.
"""
from __future__ import annotations

import hashlib
import contextvars
import logging
import os
import random
import sys
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
import json
import math
import uuid
from itertools import islice
from ccxt.base.errors import ExchangeError

import numpy as np
import pandas as pd

from ..core.types import TF_MS, MarketType
from ..core.instrument_registry import InstrumentId
from ..world.observation import Quality

SCHEMA = 'market.receipt.v1'
META = ('instrument_id', 'source', 'kind', 'event_time_ms', 'observed_at_ms',
        'available_at_ms', 'request_id', 'content_hash', 'revision_id',
        'supersedes', 'quality', 'bar_state', 'raw_json', 'transform_version', 'max_age_ms',
        'request_started_ms')
VALUES = ('open', 'high', 'low', 'close', 'volume', 'taker_buy', 'value')


def receipt_metadata(row):
    optional = {'supersedes', 'transform_version', 'max_age_ms', 'request_started_ms'}
    result = {}
    for key in META:
        value = row[key]
        if pd.isna(value):
            if key not in optional:
                raise ValueError('required_receipt_metadata_unavailable')
            value = None
        if key in ('event_time_ms','observed_at_ms','available_at_ms','max_age_ms','request_started_ms') and value is not None:
            if not math.isfinite(float(value)) or int(value) != value:
                raise ValueError('receipt_clock_invalid')
            value = int(value)
        result[key] = value
    return result


def encode(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(obj):
    return hashlib.sha256(encode(obj).encode()).hexdigest()


def ms(ts):
    return pd.to_datetime(ts, utc=True).to_numpy(dtype='datetime64[ms]').astype('int64')


#: How long after its close a bar stays actionable, beyond its own interval.
#: The one existing freshness budget (DataFeed.ttl, usable_current, kernel).
DEFAULT_TTL_S = {"5m": 120, "15m": 180, "1h": 600, "4h": 1800, "1d": 7200}


def freshness_ms(tf, ttl_s=None):
    """interval + TTL: the existing age beyond which a closed bar is STALE."""
    return TF_MS[tf] + 1000 * (DEFAULT_TTL_S.get(tf, 300) if ttl_s is None else ttl_s)


def replay_deadline_ms(event_ms, tf):
    """Last receipt time that can still be a point-in-time decision input.

    Existing freshness contract measured from the close: a receipt later than
    interval + TTL after its close could never have been acted on, so it is
    retrospective, never replay. This only admits INPUTS; whether a signal may
    still execute is fill_timing's decision. Receipt clocks are never changed.
    """
    return event_ms + TF_MS[tf] + freshness_ms(tf)


def cut(value):
    if type(value) is not int or value < 0:
        raise ValueError('explicit nonnegative as_of_ms required')
    return value


def initialised(conn, *tables):
    """One read-only probe: current witness schema version and the store's own tables."""
    try:
        row = conn.execute("SELECT gen FROM market_meta_global WHERE name='schema'").fetchone()
        if row is None or row[0] < WITNESS_SCHEMA_VERSION:
            return False
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {'market_revisions', 'market_raw_sources', *tables} <= names
    except Exception:
        return False


def init(conn):
    if initialised(conn):
        return
    conn.execute("""CREATE TABLE IF NOT EXISTS market_raw_sources (
        content_hash TEXT PRIMARY KEY, source TEXT, raw_json TEXT NOT NULL)""")
    conn.execute('''CREATE TABLE IF NOT EXISTS market_revisions (
        revision_id TEXT PRIMARY KEY, series_key TEXT NOT NULL, event_ms INTEGER NOT NULL,
        available_ms INTEGER, observed_ms INTEGER NOT NULL, record_json TEXT NOT NULL)''')
    conn.execute('''CREATE INDEX IF NOT EXISTS market_revision_cut
        ON market_revisions(series_key, event_ms, available_ms, observed_ms)''')
    init_witness(conn)


# Physical references only. Logical source/raw strings and receipt hashes are
# reconstructed and checked before any consumer receives a receipt.
TEXT_REF = '!luffy-market-text.v1!'
TEXT_MIN = 256


def text_reference(text):
    return TEXT_REF + hashlib.sha256(text.encode('utf-8')).hexdigest()


def store_record(conn, record, *, text_cache=None):
    physical = dict(record)
    for field in ('source', 'raw_json'):
        text = physical.get(field)
        if not isinstance(text, str) or len(text.encode()) < TEXT_MIN:
            continue
        marker = text_reference(text)
        key = marker[len(TEXT_REF):]
        if text_cache is not None and key in text_cache:
            old = (text_cache[key],) if text_cache[key] is not None else None
        else:
            old = conn.execute('SELECT raw_json FROM market_raw_sources WHERE content_hash=?', (key,)).fetchone()
        if old is not None and old[0] != text:
            raise ValueError('market_source_content_conflict')
        # Raw bodies may include acquisition-specific clocks. Reuse an
        # existing exact immutable body, but do not create a second copy of
        # a unique raw response just to introduce a reference.
        if old is None and field == 'raw_json':
            continue
        if old is None:
            conn.execute('INSERT INTO market_raw_sources VALUES (?,?,?)', (key, 'immutable-text.v1', text))
        if text_cache is not None:
            text_cache[key] = text
        physical[field] = marker
    return encode(physical)


def read_record(conn, raw, *, text_cache=None):
    record = json.loads(raw)
    for field in ('source', 'raw_json'):
        marker = record.get(field)
        if not isinstance(marker, str) or not marker.startswith(TEXT_REF):
            continue
        key = marker[len(TEXT_REF):]
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('market_source_reference_invalid')
        if text_cache is not None and key in text_cache:
            text = text_cache[key]
        else:
            row = conn.execute('SELECT raw_json FROM market_raw_sources WHERE content_hash=?', (key,)).fetchone()
            text = None if row is None else row[0]
        # Verify every reference, including a held immutable string. The cache
        # lives for one bounded load only; nothing survives into another cut.
        if text is None or text_reference(text) != marker:
            raise ValueError('market_source_reference_missing_or_corrupt')
        if text_cache is not None:
            text_cache[key] = text
        record[field] = text
    return record


def logical_record_json(conn, raw):
    """Preserve legacy serialization fingerprints; expand physical refs only."""
    physical = json.loads(raw)
    if any(isinstance(physical.get(f), str) and physical[f].startswith(TEXT_REF)
           for f in ('source', 'raw_json')):
        return encode(read_record(conn, raw))
    return raw


def venue_source(exchange):
    from urllib.parse import urlsplit,urlunsplit
    def unsigned(value):
        if isinstance(value,dict):
            return {key:unsigned(item) for key,item in value.items()}
        if isinstance(value,str):
            parsed=urlsplit(value)
            host=parsed.hostname or ''
            if parsed.port is not None:
                host += ':'+str(parsed.port)
            return urlunsplit((parsed.scheme,host,parsed.path,'',''))
        return None
    venue=getattr(exchange,'id',None)
    urls=getattr(exchange,'urls',{}).get('api')
    return encode({'provider':venue,'api':unsigned(urls)}) if venue and urls else None


def venue_identity(exchange, symbol):
    """Resolve a loaded venue record, never guess canonical identity from a label."""
    try:
        try:
            market=exchange.market(symbol)
        except ExchangeError:
            # Current CCXT USD-M catalogues expose settled contract symbols.
            # Resolve that exact loaded record; never invent a venue id from
            # the display alias or fall back to a spot/inverse instrument.
            if (getattr(exchange, 'id', None) != 'binanceusdm' or
                    not isinstance(symbol, str) or '/' not in symbol or ':' in symbol):
                raise
            quote = symbol.rsplit('/', 1)[1]
            market = exchange.market(symbol + ':' + quote)
            if not (market.get('contract') is True and market.get('linear') is True
                    and market.get('swap') is True and market.get('settle') == quote):
                return None, None
        if market.get('contract') is True:
            typ=MarketType.FUTURES
        elif market.get('spot') is True or market.get('contract') is False:
            typ=MarketType.SPOT
        else:
            return None,None
        iid=InstrumentId(exchange.id,typ,market['id']).value
        return iid,venue_source(exchange)
    except (AttributeError,TypeError,KeyError,ValueError,ExchangeError):
        return None,None


def annotate(df, *, instrument_id, source, kind, received_ms, timeframe=None,
             request_id=None, raw=None, transform_version=None, max_age_ms=None,
             request_started_ms=None):
    """Attach truthful local receipt clocks; partial values remain INCOMPLETE."""
    cut(received_ms)
    if request_started_ms is not None:
        cut(request_started_ms)
    out = df.copy()
    stamps = ms(out['ts'])
    req = request_id or uuid.uuid4().hex
    records = []
    # Pandas propagates/deep-copies attrs into every iterrows Series. Source
    # responses can contain years of records: iterate values without copying
    # that identical context per bar, preserving attrs on the returned frame.
    values_frame = out.copy(deep=False)
    values_frame.attrs = {}
    for i, (event, (_, row)) in enumerate(zip(stamps, values_frame.iterrows())):
        value = {k: (float(row[k]) if pd.notna(row[k]) and math.isfinite(float(row[k])) else None)
                 for k in VALUES if k in out}
        closed = timeframe is None or int(event) + TF_MS[timeframe] <= received_ms
        quality = Quality.VALID
        if not instrument_id or not source:
            quality = Quality.UNKNOWN
        if any(value.get(k) is None for k in (('value',) if kind == 'derivative' else ('close',) if kind == 'reference' else
                                             ('open', 'high', 'low', 'close', 'volume'))):
            quality = Quality.INVALID
        if any(isinstance(row[k],(bool,np.bool_)) for k in VALUES if k in out):
            quality = Quality.INVALID
        if kind == 'candle' and quality is Quality.VALID:
            if (value['low'] <= 0 or value['volume'] < 0 or
                    not value['low'] <= min(value['open'], value['close']) <=
                    max(value['open'], value['close']) <= value['high']):
                quality = Quality.INVALID
        reversed_clock = request_started_ms is not None and received_ms < request_started_ms
        if event > received_ms or reversed_clock:
            quality = Quality.INVALID
        elif not closed and quality is Quality.VALID:
            quality = Quality.INCOMPLETE
        raw_record = raw[i] if raw is not None else {'event_ms': int(event), **value}
        # Preserve JSON-safe exact source records; never invent a source timestamp.
        raw_json = encode(raw_record)
        body = dict(instrument_id=instrument_id, source=source, kind=kind,
                    event_time_ms=int(event), observed_at_ms=received_ms,
                    available_at_ms=received_ms if event <= received_ms and not reversed_clock else None,
                    request_started_ms=request_started_ms,
                    request_id=req, content_hash=digest({'raw': raw_record, 'value': value}),
                    supersedes=None, quality=quality.value,
                    bar_state='FINAL' if closed else 'PARTIAL', raw_json=raw_json,
                    transform_version=transform_version, max_age_ms=max_age_ms)
        body['revision_id'] = digest(body)
        records.append(body)
    for k in META:
        out[k] = [r[k] for r in records]
    out.attrs.update(schema_version=SCHEMA, timestamp_semantics='interval_open',
                     timeframe=timeframe, read_mode='receipt', as_of_ms=received_ms)
    return seal(out)


def seal(df):
    out = df.copy()
    values_frame = out.copy(deep=False)
    values_frame.attrs = {}
    for index, row in values_frame.iterrows():
        value = {k: None if pd.isna(row[k]) or not math.isfinite(float(row[k])) else float(row[k])
                 for k in VALUES if k in out}
        content = digest({'raw': json.loads(row['raw_json']), 'value': value})
        body = {k: (None if pd.isna(row[k]) else row[k]) for k in META
                if k not in ('revision_id', 'supersedes', 'content_hash')}
        for k in ('event_time_ms','observed_at_ms','available_at_ms','request_started_ms','max_age_ms'):
            body[k] = int(body[k]) if body[k] is not None else None
        body['content_hash'] = content
        out.at[index,'content_hash'] = content
        out.at[index,'revision_id'] = digest(body)
    return out


def prepare(df):
    """Detach and seal receipt records before entering a data write transaction."""
    if not all(k in df for k in META):
        return ()
    records = []
    sealed = seal(df)
    sealed.attrs = {}
    for _, row in sealed.iterrows():
        record = {k: None if pd.isna(row[k]) else row[k] for k in META}
        record.update({k: None if pd.isna(row[k]) else float(row[k]) for k in VALUES if k in df})
        record['event_time_ms'] = int(record['event_time_ms'])
        record['observed_at_ms'] = int(record['observed_at_ms'])
        record['available_at_ms'] = (None if pd.isna(record['available_at_ms'])
                                     else int(record['available_at_ms']))
        for clock in ('request_started_ms','max_age_ms'):
            if record[clock] is not None:
                record[clock] = int(record[clock])
        records.append(record)
    return tuple(records)


def _current_parents(conn, key, events, instrument, source):
    """Same ancestry winners, reading identity/source only on newest candidates."""
    events = tuple(events)
    q = '''SELECT record_json,revision_id,event_ms,available_ms,observed_ms
        FROM market_revisions WHERE rowid IN (SELECT selected_rowid FROM (
          SELECT rowid AS selected_rowid,event_ms,
            ROW_NUMBER() OVER (PARTITION BY event_ms ORDER BY observed_ms DESC,rowid DESC) AS ordinal
          FROM market_revisions WHERE series_key=? AND event_ms IN ('''
    q += ','.join('?' for _ in events) + ')) WHERE ordinal=1)'
    result = {}
    marker = text_reference(source) if isinstance(source, str) else None
    rows = _selected_revision_rows(conn, q, (key, *events), batch=True)
    for raw, rid, event, available, observed in rows:
        physical = json.loads(raw)
        if physical.get('instrument_id') == instrument and physical.get('source') in (source, marker):
            result[event] = (rid, observed)
        else:
            # A newer row belonging to another feed must never hide this feed's
            # parent. Only this exceptional event needs the original predicate.
            prior = conn.execute('''SELECT revision_id,observed_ms FROM market_revisions
                WHERE series_key=? AND event_ms=?
                AND json_extract(record_json,'$.instrument_id') IS ?
                AND (json_extract(record_json,'$.source') IS ? OR json_extract(record_json,'$.source') IS ?)
                ORDER BY observed_ms DESC,rowid DESC LIMIT 1''',
                (key, event, instrument, source, marker)).fetchone()
            if prior is not None:
                result[event] = prior
    return result


def planning_witness(conn, key):
    """Like ``witness`` but a series with no rows yet is generation 0, not unproven.

    The first insert bumps the generation to 1, so a plan made for an empty
    series is invalidated by any concurrent first write. None still means the
    witness tables are absent, i.e. nothing can validate a plan.
    """
    try:
        row = conn.execute('SELECT gen FROM market_series_meta WHERE series_key=?', (key,)).fetchone()
        raw = conn.execute("SELECT gen FROM market_meta_global WHERE name='raw'").fetchone()
        epoch = conn.execute("SELECT gen FROM market_meta_global WHERE name='epoch'").fetchone()
    except Exception:
        return None
    if raw is None or epoch is None:
        return None
    return (0 if row is None else row[0], raw[0], epoch[0])


def plan_append(conn, key, prepared):
    """Read phase of ``append``: everything that needs history, no writer lock.

    Returns the exact inputs the write phase needs (rows not already stored,
    their current ancestry winners, shared-text lookups) together with the
    series witness they were read at. Run it OUTSIDE any transaction; the write
    phase re-checks the witness under the writer lock and re-plans if any
    committed change touched the series (or the shared raw-source table).
    """
    wit = planning_witness(conn, key)
    iterator = iter(prepared)
    seen, batches = set(), []
    while batch := list(islice(iterator, 128)):
        ids = tuple(dict.fromkeys(r['revision_id'] for r in batch))
        existing = set(json.loads(conn.execute('SELECT json_group_array(revision_id) FROM market_revisions '
                    'WHERE revision_id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchone()[0]))
        # Exact retries already have sealed identities: no parent reads.
        batch = [r for r in batch if r['revision_id'] not in existing and r['revision_id'] not in seen]
        if not batch:
            continue
        seen.update(r['revision_id'] for r in batch)
        groups = {}
        for r in batch:
            groups.setdefault((r['instrument_id'], r['source']), set()).add(r['event_time_ms'])
        parents = {}
        for (instrument, source), events in groups.items():
            for event, prior in _current_parents(conn, key, events, instrument, source).items():
                parents[(instrument, source, event)] = prior
        source_cache = {text_reference(r[f])[len(TEXT_REF):]: None for r in batch
                        for f in ('source', 'raw_json')
                        if isinstance(r.get(f), str) and len(r[f].encode()) >= TEXT_MIN}
        if source_cache:
            keys = tuple(source_cache)
            source_cache.update(conn.execute('SELECT content_hash,raw_json FROM market_raw_sources WHERE content_hash IN ('
                + ','.join('?' for _ in keys) + ')', keys).fetchall())
        batches.append((batch, parents, source_cache))
    return dict(key=key, wit=wit, batches=batches)


def append(conn, key, df=None, *, prepared=None, plan=None):
    """Append/link exact retrievals atomically.

    ``plan`` (from ``plan_append``, read before the writer lock) is used only
    while the series witness is unchanged; otherwise ancestry is re-read here,
    inside the transaction, exactly as before.
    """
    if prepared is None:
        prepared = prepare(df)
    if (plan is None or plan['key'] != key or plan['wit'] is None
            or planning_witness(conn, key) != plan['wit']):
        plan = plan_append(conn, key, prepared)
    wrote = False
    src, carried = {}, {}
    for batch, base_parents, base_src in plan['batches']:
        wrote = True
        # Shared text is content-addressed and immutable, but another writer may
        # have added a body since it was read: re-ask only for the missing hashes.
        for k, v in base_src.items():
            if src.get(k) is None:
                src[k] = v
        missing = tuple(k for k in base_src if src[k] is None)
        if missing:
            src.update(conn.execute('SELECT content_hash,raw_json FROM market_raw_sources WHERE content_hash IN ('
                + ','.join('?' for _ in missing) + ')', missing).fetchall())
        existing = set()
        writes = []
        for original in batch:
            record = dict(original)
            identity = (record['instrument_id'], record['source'], record['event_time_ms'])
            prior = base_parents.get(identity)
            won = carried.get(identity)
            if won is not None and (prior is None or won[1] >= prior[1]):
                prior = won
            rid, observed = record['revision_id'], record['observed_at_ms']
            record['supersedes'] = prior[0] if prior and prior[0] != rid else None
            writes.append((rid, key, record['event_time_ms'], record['available_at_ms'], observed, store_record(conn, record, text_cache=src)))
            # New equal-clock rows win by rowid, exactly as the sequential
            # INSERT does. Ignored retries and older observations cannot win.
            if rid not in existing and (prior is None or observed >= prior[1]):
                carried[identity] = (rid, observed)
            existing.add(rid)
        conn.execute('INSERT OR IGNORE INTO market_revisions VALUES '
                     + ','.join('(?,?,?,?,?,?)' for _ in writes),
                     tuple(value for row in writes for value in row))

    if wrote:
        from trader.persistence.storage_cleanup import market_completion
        market_completion(conn)


def _selected_revision_rows(conn, query, args, *, batch=False):
    """Transport bounded rows once; selection SQL and receipt checks stay exact.

    The cursor fetchall method steps into Python for every row, releasing the GIL each
    time. Busy background workers can turn a small read into seconds of waits.
    JSON transports the selected strings/integers in one result. Preserve raw
    BLOB bytes too, so the existing JSON decoder/validation still decides them.
    """
    if not batch:
        return conn.execute(query, args).fetchall()
    packet = conn.execute("""SELECT json_group_array(json_array(
        typeof(record_json), CASE WHEN typeof(record_json)='blob'
            THEN hex(record_json) ELSE record_json END,
        revision_id,event_ms,available_ms,observed_ms)) FROM (""" + query + ")", args).fetchone()[0]
    return [(bytes.fromhex(raw) if kind == 'blob' else raw, rid, event, available, observed)
            for kind,raw,rid,event,available,observed in json.loads(packet)]


def load(conn, key, *, as_of_ms, limit=200000, replay_tf=None, include_partial=False, revision_stream=False, instrument_id=None, source=None, window_start_ms=None, metadata_only=False, min_event_ms=None):
    """Last retained revision of EACH event eligible at the exact decision cut.

    Replay instead takes each event's FIRST eligible receipt, and requires it
    within one bar after the bar's close (replay_deadline_ms); later receipts
    are retrospective only.
    UNKNOWN legacy rows never receive inferred receipt clocks.
    """
    cut(as_of_ms)
    if metadata_only and (not replay_tf or revision_stream or include_partial):
        raise ValueError('metadata_requires_final_replay')
    if window_start_ms is not None:
        cut(window_start_ms)
        if window_start_ms > as_of_ms or replay_tf or revision_stream:
            raise ValueError('current_window_requires_exact_asof_revision_selection')
    clauses = 'WHERE series_key=? AND available_ms<=? AND observed_ms<=?'
    args = [key,as_of_ms,as_of_ms]
    if instrument_id is not None:
        clauses += " AND json_extract(record_json,'$.instrument_id')=?"
        args.append(instrument_id)
    elif not _single_identity_proven(conn, key):
        identities=conn.execute("SELECT DISTINCT json_extract(record_json,'$.instrument_id') FROM market_revisions WHERE series_key=? AND available_ms<=? AND observed_ms<=?",(key,as_of_ms,as_of_ms)).fetchall()
        if len({row[0] for row in identities if row[0] is not None})>1:
            return None  # an unqualified alias cannot choose a market
    if source is not None:
        clauses += " AND json_extract(record_json,'$.source') IN (?,?)"
        args.extend((source, text_reference(source)))
    if min_event_ms is not None:
        # Tail read for incremental refresh: exact selection among events >= floor.
        if replay_tf or revision_stream or window_start_ms is not None:
            raise ValueError('tail_read_requires_current_selection')
        clauses += ' AND event_ms>=?'
        args.append(cut(min_event_ms))
    if replay_tf:
        clauses += ' AND available_ms<=event_ms+?'
        args.append(replay_deadline_ms(0, replay_tf))
    if not include_partial:
        clauses += " AND json_extract(record_json,'$.bar_state')!='PARTIAL'"
    if revision_stream:
        # Retain all versions of the bounded event window, not only the
        # database's newest projection of those events.
        query = f"""SELECT record_json,revision_id,event_ms,available_ms,observed_ms FROM market_revisions {clauses}
            AND event_ms IN (SELECT DISTINCT event_ms FROM market_revisions
            WHERE series_key=? AND available_ms<=? AND observed_ms<=?
            ORDER BY event_ms DESC LIMIT ?) ORDER BY event_ms,observed_ms,rowid"""
        args += [key,as_of_ms,as_of_ms,limit]
    else:
        # Replay decides on a bar's FIRST eligible receipt: a later correction was
        # not known then and must not reach that bar's signal.
        order = 'observed_ms,rowid' if replay_tf else 'observed_ms DESC,rowid DESC'
        query = f"""SELECT record_json,revision_id,event_ms,available_ms,observed_ms FROM (SELECT record_json,revision_id,event_ms,available_ms,observed_ms,
            ROW_NUMBER() OVER (PARTITION BY event_ms ORDER BY {order}) AS ordinal
            FROM market_revisions {clauses}) WHERE ordinal=1 ORDER BY event_ms DESC LIMIT ?"""
        if not replay_tf and window_start_ms is None:
            # Choose the requested eligible event keys BEFORE ranking their
            # revisions. The event index can stop at LIMIT, rather than sorting
            # every historical event to return a small current window. Every
            # predicate is identical in both steps, including identity/source,
            # receipt clocks and partial-bar policy; rowid ties stay exact.
            predicates = tuple(args)
            query = f"""WITH wanted_events AS MATERIALIZED (
                SELECT event_ms FROM market_revisions {clauses}
                GROUP BY event_ms ORDER BY event_ms DESC LIMIT ?)
                SELECT record_json,revision_id,event_ms,available_ms,observed_ms
                FROM market_revisions WHERE rowid IN (
                SELECT selected_rowid FROM (
                SELECT rowid AS selected_rowid,event_ms,
                ROW_NUMBER() OVER (PARTITION BY event_ms ORDER BY {order}) AS ordinal
                FROM market_revisions {clauses}
                AND event_ms IN (SELECT event_ms FROM wanted_events))
                WHERE ordinal=1 ORDER BY event_ms DESC LIMIT ?)
                ORDER BY event_ms DESC"""
            args = [*predicates, limit, *predicates]
        if window_start_ms is not None:
            # Filter AFTER exact revision selection. Before the first supplied
            # bar retain its exact predecessor plus only late older events
            # that can become the newest event. Dominated late corrections
            # cannot change align() at any cut in this window.
            query = f"""WITH ranked AS (SELECT record_json,revision_id,event_ms,available_ms,observed_ms,
                ROW_NUMBER() OVER (PARTITION BY event_ms ORDER BY observed_ms DESC,rowid DESC) AS ordinal
                FROM market_revisions {clauses}), current AS (
                SELECT *,MAX(event_ms,available_ms,observed_ms) AS ready_ms FROM ranked WHERE ordinal=1),
                frontier AS (SELECT *,MAX(event_ms) OVER (
                    ORDER BY ready_ms,event_ms DESC ROWS UNBOUNDED PRECEDING) AS newest FROM current)
                SELECT record_json,revision_id,event_ms,available_ms,observed_ms FROM frontier WHERE event_ms>=?
                OR (ready_ms>=? AND event_ms=newest)
                OR event_ms=(SELECT MAX(event_ms) FROM current WHERE ready_ms<?)
                ORDER BY event_ms DESC LIMIT ?"""
            args += [window_start_ms]*3
        args.append(limit)
    # Cover bounded live inference windows too (the active strategy needs
    # 617 bars, above the former512-row transport boundary). This remains
    # a transport optimization only: larger/stream/replay reads retain their
    # original path. This does not bound or alter the requested evidence set.
    batch = (not revision_stream and not replay_tf and window_start_ms is None
             and type(limit) is int and 0 < limit <= 8192)
    rows = _selected_revision_rows(conn, query, args, batch=batch)
    return _assemble(conn, rows, batch=batch, as_of_ms=as_of_ms, limit=limit, replay_tf=replay_tf,
                     include_partial=include_partial, revision_stream=revision_stream,
                     metadata_only=metadata_only)


def _verify_row(conn, raw, rid, indexed_event, indexed_available, indexed_observed, text_cache):
    """Decode and authenticate one retained revision against its indexed clocks."""
    STATS['payload_rows_decoded'] += 1
    r = read_record(conn, raw, text_cache=text_cache) if text_cache is not None else read_record(conn, raw)
    value = {k: r[k] for k in VALUES if k in r}
    body = {k: r[k] for k in META if k not in ('revision_id', 'supersedes')}
    if r['content_hash'] != digest({'raw': json.loads(r['raw_json']), 'value': value}) or r['revision_id'] != digest(body):
        raise ValueError('market_revision_content_corrupt')
    # Selection/ranking columns are outside the receipt hash. They must
    # agree with the verified receipt before they can establish a cut.
    if (rid, indexed_event, indexed_available, indexed_observed) != (
            r['revision_id'], r['event_time_ms'], r['available_at_ms'], r['observed_at_ms']):
        raise ValueError('market_revision_index_corrupt')
    return r


def _assemble(conn, rows, *, batch, as_of_ms, limit, replay_tf, include_partial,
              revision_stream, metadata_only):
    """Decode and authenticate selected revision rows (shared by every as-of reader)."""
    text_cache = {} if batch else None
    if not revision_stream:
        rows.reverse()
    selected = {}
    stream = []
    for raw, rid, indexed_event, indexed_available, indexed_observed in rows:
        r = _verify_row(conn, raw, rid, indexed_event, indexed_available, indexed_observed,
                        text_cache if batch else None)
        event = r['event_time_ms']
        if replay_tf and r['available_at_ms'] > replay_deadline_ms(event, replay_tf):
            continue
        if r['bar_state'] == 'PARTIAL' and not include_partial:
            continue
        if metadata_only:
            # Same final/clock filter as eligible_frame, without constructing or
            # returning protected OHLCV columns to a discovery caller.
            if (event > as_of_ms or r['available_at_ms'] > as_of_ms
                    or r['observed_at_ms'] > as_of_ms
                    or r['bar_state'] != 'FINAL'
                    or event + TF_MS[replay_tf] > as_of_ms):
                continue
            selected[event] = {k: r[k] for k in ('event_time_ms', 'revision_id',
                                'available_at_ms', 'observed_at_ms', 'quality')}
        else:
            selected[event] = r
        if not metadata_only:
            stream.append(r)
    if not selected:
        return None
    df = pd.DataFrame(stream if revision_stream else list(selected.values())[-limit:])
    df['ts'] = pd.to_datetime(df['event_time_ms'], unit='ms', utc=True)
    df.attrs.update(schema_version=SCHEMA, timeframe=replay_tf,
                    timestamp_semantics='interval_open', as_of_ms=as_of_ms,
                    read_mode='replay' if replay_tf else 'as_of')
    return df


def eligible_frame(df, tf, as_of_ms, *, require_provenance=True, final=True):
    """One boundary for cached/decision frames. Invalid values become NaN."""
    cut(as_of_ms)
    if df is None:
        return None
    out = df.copy()
    if not all(k in out for k in META):
        if require_provenance:
            return None
        return out.loc[ms(out['ts']) + TF_MS.get(tf, 0) <= as_of_ms].copy()
    known = (pd.to_numeric(out['available_at_ms'], errors='coerce').le(as_of_ms) &
             pd.to_numeric(out['observed_at_ms'], errors='coerce').le(as_of_ms) &
             pd.to_numeric(out['event_time_ms'], errors='coerce').le(as_of_ms))
    if final:
        known &= out['bar_state'].eq('FINAL') & (ms(out['ts']) + TF_MS.get(tf, 0) <= as_of_ms)
    out = out.loc[known].copy()
    bad = ~out['quality'].eq(Quality.VALID.value)
    out.loc[bad, [k for k in VALUES if k in out]] = np.nan
    out.attrs.update(as_of_ms=as_of_ms, schema_version=SCHEMA)
    return out


def usable_current(df, tf, as_of_ms, ttl_s):
    out = eligible_frame(df, tf, as_of_ms)
    if out is None or out.empty:
        return None
    # Historical window values remain historical. The anchor must be current
    # within the feed's existing interval + TTL budget; no new risk policy.
    age = as_of_ms - (int(ms(out['ts'])[-1]) + TF_MS[tf])
    receipt_age = as_of_ms - int(out['observed_at_ms'].iloc[-1])
    if age > TF_MS[tf] + ttl_s * 1000 or receipt_age > ttl_s * 1000:
        out['quality'] = Quality.STALE.value
        out.loc[:, [k for k in VALUES if k in out]] = np.nan
        out.attrs['quality'] = Quality.STALE.value
    return out


def current_frame_status(df, tf, as_of_ms, ttl_s, *, instrument_id=None, source=None):
    """Qualify an already acquired frame once, with a finite refusal vocabulary.

    No reads, retries or inferred receipt clocks. The returned frame is the
    same eligible current view used by existing snapshot consumers.
    """
    if df is None or df.empty:
        return None, 'missing'
    if not all(k in df for k in (*META, 'ts', 'close')):
        return None, 'invalid'
    out = usable_current(df, tf, as_of_ms, ttl_s)
    if out is None or out.empty:
        reason = 'incomplete' if df['bar_state'].eq('PARTIAL').any() else 'unavailable_at_cut'
        return None, reason
    if out['quality'].eq('STALE').any():
        return None, 'stale'
    if not out['quality'].eq('VALID').all():
        return None, 'invalid'
    iid = out['instrument_id'].iloc[-1]
    if (not isinstance(iid, str) or not iid or not out['instrument_id'].eq(iid).all() or
            (instrument_id is not None and iid != instrument_id) or
            (source is not None and not out['source'].eq(source).all())):
        return None, 'identity_mismatch'
    return out, 'valid'


def resolve_lineage(conns, lineage, *, transforms=None):
    """Rebuild every retained receipt a journaled decision lineage names.

    The decision journal stores revision identities only. This reads the
    durable revision stores back (``conns``: every market store that may hold
    them) and refuses, never defaults, when an input cannot be proven: a
    missing or corrupt revision, one not yet available/observed at the
    decision cut, a quality state other than VALID, a foreign instrument, or
    a schema/transform identity different from the one the decision recorded.
    Returns ``{revision_id: receipt metadata}`` for audit and replay.
    """
    if lineage.get('schema_version') != SCHEMA:
        raise ValueError('lineage_schema_incompatible')
    at = cut(lineage.get('as_of_ms'))
    iid = lineage.get('instrument_id')
    expected = dict(transforms or {})
    wanted = []  # (revision_id, check instrument?)
    for group in ('frames', 'derivs', 'references'):
        for ids in (lineage.get(group) or {}).values():
            wanted += [(r, group != 'references') for r in ids]
    for frames in (lineage.get('universe') or {}).values():
        for ids in frames.values():
            wanted += [(r, False) for r in ids]
    btc = lineage.get('btc_context')
    if btc:
        if btc.get('transform_version') != expected.get('btc_context', 'btc_context.v1'):
            raise ValueError('lineage_transform_mismatch')
        wanted += [(r, False) for r in btc['source_revisions']]
    out = {}
    for rid, same_instrument in wanted:
        if rid in out:
            continue
        row = None
        for conn in conns:
            row = conn.execute('SELECT record_json,event_ms,available_ms,observed_ms FROM market_revisions WHERE revision_id=?', (rid,)).fetchone()
            if row:
                break
        if not row:
            raise ValueError('lineage_receipt_missing')
        r = read_record(conn, row[0])
        value = {k: r[k] for k in VALUES if k in r}
        body = {k: r[k] for k in META if k not in ('revision_id', 'supersedes')}
        if (r['revision_id'] != rid or r['content_hash'] != digest({'raw': json.loads(r['raw_json']), 'value': value})
                or rid != digest(body)):
            raise ValueError('market_revision_content_corrupt')
        if tuple(row[1:]) != (r['event_time_ms'], r['available_at_ms'], r['observed_at_ms']):
            raise ValueError('market_revision_index_corrupt')
        if r['available_at_ms'] is None or r['available_at_ms'] > at or r['observed_at_ms'] > at:
            raise ValueError('lineage_receipt_not_yet_available')
        if r['quality'] != Quality.VALID.value:
            raise ValueError('lineage_receipt_quality_not_valid')
        if same_instrument and iid and r['instrument_id'] != iid:
            raise ValueError('lineage_instrument_mismatch')
        want = expected.get(r['kind'])
        if want is not None and r.get('transform_version') != want:
            raise ValueError('lineage_transform_mismatch')
        out[rid] = receipt_metadata({**r, **{k: r.get(k) for k in META}})
    return out


def retrospective_load(conn, key, *, cut_ms, tf=None, start_ms=0,
                       end_ms=None, limit=200000):
    """Retained observations bounded by event close, regardless of acquisition.

    Receipt hashes and indexed clocks are checked as for as-of reads. Original
    acquisition clocks remain untouched; this is not historical availability.
    No source record beyond the event boundary is selected or decoded.
    """
    cut(cut_ms)
    start_ms = 0 if start_ms is None else start_ms
    cut(start_ms)
    if end_ms is not None:
        cut(end_ms)
    clauses = 'series_key=? AND event_ms>=? AND event_ms<=?'
    args = [key, start_ms, cut_ms - TF_MS.get(tf, 0)]
    if end_ms is not None:
        clauses += ' AND event_ms<?'
        args.append(end_ms)
    # Bounded tail: first the `limit` newest DISTINCT event times inside the exact bounds (a backward walk of the
    # (series_key, event_ms, ...) index, touching only those keys), THEN the latest revision of each of those times
    # (observed_ms DESC, rowid DESC), then their bodies. Raw revision rows are never limited before the per-event pick and
    # invalid/corrupt latest revisions are never filtered out of that pick, so a bad latest revision cannot fall back to an
    # older valid one. Work is bounded by the requested tail, not by the series' history.
    times = [r[0] for r in conn.execute(
        'SELECT DISTINCT event_ms FROM market_revisions WHERE ' + clauses + ' ORDER BY event_ms DESC LIMIT ?', args + [limit])]
    rows = []
    if times:
        rows = conn.execute(
            'SELECT record_json,revision_id,event_ms,available_ms,observed_ms FROM market_revisions WHERE rowid IN ('
            'SELECT selected_rowid FROM (SELECT rowid AS selected_rowid,ROW_NUMBER() OVER (PARTITION BY event_ms '
            'ORDER BY observed_ms DESC,rowid DESC) ordinal FROM market_revisions WHERE series_key=? AND event_ms>=? AND event_ms<=?) '
            'WHERE ordinal=1) ORDER BY event_ms DESC', (key, min(times), max(times))).fetchall()
    selected = []
    text_cache = {}  # one bounded load; shared immutable bodies still have their hash verified on every reference
    for raw, rid, event, available, observed in reversed(rows):
        r = read_record(conn, raw, text_cache=text_cache)
        value = {k:r[k] for k in VALUES if k in r}
        body = {k:r[k] for k in META if k not in ('revision_id','supersedes')}
        if r['content_hash'] != digest({'raw':json.loads(r['raw_json']),'value':value}) or r['revision_id'] != digest(body):
            raise ValueError('market_revision_content_corrupt')
        if (rid,event,available,observed) != (r['revision_id'],r['event_time_ms'],r['available_at_ms'],r['observed_at_ms']):
            raise ValueError('market_revision_index_corrupt')
        if r['bar_state'] != 'FINAL' or r['quality'] not in ('VALID','UNKNOWN'):
            continue
        selected.append(r)
    if not selected:
        return None
    df = pd.DataFrame(selected)
    df['ts'] = pd.to_datetime(df['event_time_ms'],unit='ms',utc=True)
    df.attrs.update(read_mode='retrospective',historical_cut_ms=cut_ms,
                    timeframe=tf,timestamp_semantics='interval_open',
                    evidence_scope='retrospective_observation')
    return df


# ── committed change witnesses ──────────────────────────────────────────────
# Triggers keep one small row per series (and one global raw-source counter)
# inside the SAME transaction as every change to market_revisions /
# market_raw_sources, from any connection or process. A rolled-back write rolls
# the witness back with it. Installing them is DDL only: no table is rebuilt and
# no history is read. Series that have not been written since installation have
# no witness row and are simply not cacheable until one is seeded.
log = logging.getLogger(__name__)
STATS = dict(frontier_index_rows=0, frontier_rebuilds=0, frontier_deltas=0, incremental_refreshes=0, incremental_rows_read=0, warm_refills=0, warm_failures=0, commit_refreshes=0, warming_misses=0, cache_only_unproven=0,
             cache_rejected_oversize=0, legacy_window_reads=0, cache_hits=0, cache_misses=0, cache_uncacheable=0, payload_rows_decoded=0,
             window_reads=0, identity_unresolved=0, identity_refused=0,
             resolver_rows_scanned=0, series_resolved=0)
_DEMAND = {}            # series_key -> needs identity resolution (bounded)
_DEMAND_LIMIT = 4096
_IDENT = ("CASE WHEN typeof(NEW.record_json)='text' AND json_valid(NEW.record_json) "
          "THEN json_quote(json_extract(NEW.record_json,'$.instrument_id')) ELSE json_quote('?unreadable') END")
_PART = ("CASE WHEN typeof(NEW.record_json)='text' AND json_valid(NEW.record_json) "
         "THEN COALESCE(json_extract(NEW.record_json,'$.bar_state')='PARTIAL',0) ELSE 1 END")


WITNESS_SCHEMA_VERSION = 3


def init_witness(conn):
    """Versioned, once-per-database setup. An initialised database takes a
    read-only path (one SELECT): no DDL, no writes, no trigger recreation, and no
    change to any generation witness. Writer/producer opens perform the one
    migration; scan lookups never do (they only reach here through stores that
    are already initialised)."""
    try:
        row = conn.execute("SELECT gen FROM market_meta_global WHERE name='schema'").fetchone()
    except Exception:
        row = None
    if row is not None and row[0] >= WITNESS_SCHEMA_VERSION:
        return False
    _migrate_witness(conn)
    conn.execute("INSERT INTO market_meta_global VALUES ('schema',?) "
                 "ON CONFLICT(name) DO UPDATE SET gen=excluded.gen", (WITNESS_SCHEMA_VERSION,))
    return True


def _migrate_witness(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS market_series_meta (
        series_key TEXT PRIMARY KEY, gen INTEGER NOT NULL, epoch INTEGER NOT NULL DEFAULT 0,
        idents TEXT NOT NULL DEFAULT '|', partial INTEGER NOT NULL DEFAULT 0,
        resolved INTEGER NOT NULL DEFAULT 0, scan_event INTEGER, scan_json TEXT)''')
    conn.execute('CREATE TABLE IF NOT EXISTS market_meta_global (name TEXT PRIMARY KEY, gen INTEGER NOT NULL)')
    if conn.execute("SELECT 1 FROM market_meta_global WHERE name='raw'").fetchone() is None:
        conn.execute("INSERT OR IGNORE INTO market_meta_global VALUES ('raw',0)")
    if conn.execute("SELECT 1 FROM market_meta_global WHERE name='epoch'").fetchone() is None:
        # Lineage token of THIS database: a different database or a rebuilt one
        # at the same path never shares a witness with it.
        conn.execute("INSERT OR IGNORE INTO market_meta_global VALUES ('epoch',?)", (random.getrandbits(62),))
    conn.execute('''CREATE TABLE IF NOT EXISTS market_change_log (
        series_key TEXT NOT NULL, bucket INTEGER NOT NULL, floor_ms INTEGER NOT NULL, hi_ms INTEGER NOT NULL,
        destructive INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (series_key, bucket))''')
    # The trigger version is part of the name: older databases gain the log-aware
    # triggers; the superseded ones are dropped in the same DDL step.
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    for old in ('market_witness_ins', 'market_witness_upd', 'market_witness_del'):
        if old in present:
            conn.execute(f'DROP TRIGGER {old}')
    log_insert = ("INSERT INTO market_change_log(series_key,bucket,floor_ms,hi_ms,destructive) "
                  "SELECT {key},gen/16,{lo},{hi},{bad} FROM market_series_meta WHERE series_key={key} "
                  "ON CONFLICT(series_key,bucket) DO UPDATE SET floor_ms=MIN(floor_ms,excluded.floor_ms),"
                  "hi_ms=MAX(hi_ms,excluded.hi_ms),destructive=destructive OR excluded.destructive;")
    conn.execute(f'''CREATE TRIGGER IF NOT EXISTS market_witness_ins2 AFTER INSERT ON market_revisions BEGIN
        INSERT INTO market_series_meta(series_key,gen,idents,partial,resolved)
        VALUES (NEW.series_key,1,'|'||{_IDENT}||'|',{_PART},
                NOT EXISTS (SELECT 1 FROM market_revisions WHERE series_key=NEW.series_key AND rowid<>NEW.rowid))
        ON CONFLICT(series_key) DO UPDATE SET gen=gen+1,
            idents=CASE WHEN instr(idents,'|'||{_IDENT}||'|')>0 THEN idents ELSE idents||{_IDENT}||'|' END,
            partial=partial OR {_PART};
        {log_insert.format(key='NEW.series_key', lo='NEW.event_ms', hi='NEW.event_ms', bad=0)}
        END''')
    conn.execute(f'''CREATE TRIGGER IF NOT EXISTS market_witness_upd2 AFTER UPDATE ON market_revisions BEGIN
        UPDATE market_series_meta SET gen=gen+1, epoch=epoch+1, resolved=0, scan_event=NULL, scan_json=NULL
        WHERE series_key IN (OLD.series_key, NEW.series_key);
        {log_insert.format(key='OLD.series_key', lo='MIN(OLD.event_ms,NEW.event_ms)', hi='MAX(OLD.event_ms,NEW.event_ms)', bad=1)}
        {log_insert.format(key='NEW.series_key', lo='MIN(OLD.event_ms,NEW.event_ms)', hi='MAX(OLD.event_ms,NEW.event_ms)', bad=1)}
        END''')
    conn.execute(f'''CREATE TRIGGER IF NOT EXISTS market_witness_del2 AFTER DELETE ON market_revisions BEGIN
        UPDATE market_series_meta SET gen=gen+1, epoch=epoch+1, scan_event=NULL, scan_json=NULL
        WHERE series_key=OLD.series_key;
        {log_insert.format(key='OLD.series_key', lo='OLD.event_ms', hi='OLD.event_ms', bad=1)}
        END''')
    # A raw body that is REPLACED (conflicting insert of an existing hash) is a
    # change of already-referenced content even though no DELETE trigger fires.
    conn.execute('''CREATE TRIGGER IF NOT EXISTS market_raw_witness_dup BEFORE INSERT ON market_raw_sources
        WHEN EXISTS (SELECT 1 FROM market_raw_sources WHERE content_hash=NEW.content_hash)
        BEGIN UPDATE market_meta_global SET gen=gen+1 WHERE name='raw'; END''')
    for name, event in (('upd', 'UPDATE'), ('del', 'DELETE')):
        conn.execute(f'''CREATE TRIGGER IF NOT EXISTS market_raw_witness_{name} AFTER {event} ON market_raw_sources
            BEGIN UPDATE market_meta_global SET gen=gen+1 WHERE name='raw'; END''')


def prune_change_log(conn, keep_buckets=512):
    """Bounded: change buckets older than ``keep_buckets`` x 16 generations are dropped."""
    try:
        conn.execute('DELETE FROM market_change_log WHERE rowid IN (SELECT l.rowid FROM market_change_log l '
                     'JOIN market_series_meta m USING(series_key) WHERE l.bucket < m.gen/16 - ? LIMIT 5000)',
                     (keep_buckets,))
    except Exception:
        pass


def changes_since(conn, key, gen0):
    """What changed in ``key`` since generation ``gen0``?

    -> (floor_event_ms, pure_inserts) or None when the log cannot prove it.
    Conservative by construction: the oldest bucket may include changes older
    than gen0, and buckets pruned or missing mean "unknown".
    """
    try:
        gen = conn.execute('SELECT gen FROM market_series_meta WHERE series_key=?', (key,)).fetchone()
        if gen is None or gen[0] < gen0:
            return None
        if gen[0] == gen0:
            return None, True
        lo, hi = gen0 // 16, gen[0] // 16
        rows = conn.execute('SELECT MIN(floor_ms),MAX(destructive),COUNT(*) FROM market_change_log '
                            'WHERE series_key=? AND bucket>=? AND bucket<=?', (key, lo, hi)).fetchone()
    except Exception:
        return None
    if rows is None or rows[2] != hi - lo + 1 or rows[0] is None:
        return None
    return int(rows[0]), not rows[1]


def demand(conn, key, identity=False):
    """Record that ``key`` in THIS physical database needs a witness/identity proof."""
    ident = (_db_id(conn), key)
    if len(_DEMAND) < _DEMAND_LIMIT or ident in _DEMAND:
        _DEMAND[ident] = _DEMAND.get(ident, False) or identity


def witness(conn, key):
    """(series generation, raw-source generation, database epoch), or None when unproven."""
    try:
        row = conn.execute('SELECT gen FROM market_series_meta WHERE series_key=?', (key,)).fetchone()
        raw = conn.execute("SELECT gen FROM market_meta_global WHERE name='raw'").fetchone()
        epoch = conn.execute("SELECT gen FROM market_meta_global WHERE name='epoch'").fetchone()
    except Exception:
        return None
    if row is None or raw is None or epoch is None:
        demand(conn, key)
        return None
    return (row[0], raw[0], epoch[0])


def _meta(conn, key):
    try:
        return conn.execute('SELECT gen,idents,partial,resolved FROM market_series_meta WHERE series_key=?', (key,)).fetchone()
    except Exception:
        return None


def _non_null_idents(idents):
    return [i for i in idents.strip('|').split('|') if i and i != 'null']


def _single_identity_proven(conn, key):
    row = _meta(conn, key)
    return bool(row and row[3] and len(_non_null_idents(row[1])) <= 1)


def _legacy_window(conn, key, as_of_ms, window_start_ms, instrument_id, limit):
    """The original unqualified query plus the consumer's identity filter.

    Exact prior semantics for series the maintained metadata cannot prove
    (unresolved, partial rows, foreign identities). Payload-scanning: only ever
    run by warm/producer threads, never by a cache-only trading read.
    """
    STATS['legacy_window_reads'] += 1
    df = load(conn, key, as_of_ms=as_of_ms, limit=limit, window_start_ms=window_start_ms)
    if df is None:
        return None, 'missing'
    if instrument_id is not None and not df['instrument_id'].eq(instrument_id).all():
        STATS['identity_refused'] += 1
        return None, 'identity_ambiguous'
    return df, 'valid'


# ── exact incremental frontier (RAM metadata only) ──────────────────────────
class _Frontier:
    """Compact index metadata of one series at a cut: the latest eligible revision
    of every event (event, available, observed, rowid) plus committed rows that
    become visible later. No payloads, no persistence; rebuilt from the covering
    index after any destructive/unknown change."""
    __slots__ = ('wit', 'cut', 'ev', 'av', 'ob', 'rid', 'pending', 'decoded', 'decoded_sizes', 'lock')

    def __init__(self):
        self.lock = threading.Lock()
        self.decoded = {}
        self.decoded_sizes = {}

    def nbytes(self):
        return 32 * len(self.ev) + 384 * len(self.pending) + sum(self.decoded_sizes.values())


FRONTIERS = OrderedDict()
FRONTIER_MAX_BYTES = 128 << 20
_FRONT_LOCK = threading.Lock()


def _winners(ev, av, ob, rid):
    """Latest (observed, rowid) per event among the given eligible rows, ordered by event."""
    order = np.lexsort((rid, ob, ev))
    ev, av, ob, rid = ev[order], av[order], ob[order], rid[order]
    last = np.ones(len(ev), dtype=bool)
    last[:-1] = ev[1:] != ev[:-1]
    return ev[last], av[last], ob[last], rid[last]


def _classify(state, rows, cut_ms):
    """Merge index rows (rowid, event, available, observed) into state at its cut."""
    if not len(rows):
        return
    arr = np.asarray(rows, dtype=np.int64).reshape(-1, 4)
    rid, ev, av, ob = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    known = av >= 0                                   # NULL availability is never visible
    eligible = known & (av <= cut_ms) & (ob <= cut_ms)
    waiting = known & ~eligible
    if waiting.any():
        for r in arr[waiting]:
            state.pending[int(r[0])] = (int(r[1]), int(r[2]), int(r[3]))
    if eligible.any():
        e_ev, e_av, e_ob, e_rid = ev[eligible], av[eligible], ob[eligible], rid[eligible]
        touched = np.isin(state.ev, e_ev)
        ev2 = np.concatenate([state.ev[touched], e_ev])
        av2 = np.concatenate([state.av[touched], e_av])
        ob2 = np.concatenate([state.ob[touched], e_ob])
        rid2 = np.concatenate([state.rid[touched], e_rid])
        w_ev, w_av, w_ob, w_rid = _winners(ev2, av2, ob2, rid2)
        ev3 = np.concatenate([state.ev[~touched], w_ev])
        order = np.argsort(ev3, kind='stable')
        state.ev = ev3[order]
        state.av = np.concatenate([state.av[~touched], w_av])[order]
        state.ob = np.concatenate([state.ob[~touched], w_ob])[order]
        state.rid = np.concatenate([state.rid[~touched], w_rid])[order]


_INDEX_ROWS = ("SELECT rowid,event_ms,COALESCE(available_ms,-1),observed_ms FROM market_revisions "
               "WHERE series_key=?")


def _rebuild_frontier(conn, key, cut_ms, wit):
    STATS['frontier_rebuilds'] += 1
    state = _Frontier()
    z = np.zeros(0, dtype=np.int64)
    state.wit, state.cut, state.ev, state.av, state.ob, state.rid, state.pending = wit, cut_ms, z, z, z, z, {}
    # Collapse repeated receipts to the winning indexed row INSIDE SQLite.
    # Returning every historical revision to Python caused long GIL handoffs
    # and large transient allocations, although only one row/event survives.
    rows = conn.execute('''SELECT selected_rowid,event_ms,available_ms,observed_ms FROM (
        SELECT rowid AS selected_rowid,event_ms,available_ms,observed_ms,
            ROW_NUMBER() OVER (PARTITION BY event_ms ORDER BY observed_ms DESC,rowid DESC) AS ordinal
        FROM market_revisions WHERE series_key=? AND available_ms>=0
            AND available_ms<=? AND observed_ms<=?) WHERE ordinal=1''', (key,cut_ms,cut_ms)).fetchall()
    pending = conn.execute(_INDEX_ROWS + ' AND available_ms>=0 AND (available_ms>? OR observed_ms>?)',
                           (key,cut_ms,cut_ms)).fetchall()
    STATS['frontier_index_rows'] += len(rows) + len(pending)
    _classify(state, rows, cut_ms)
    _classify(state, pending, cut_ms)
    return state


def _advance_frontier(conn, key, state, cut_ms, wit):
    """Bring ``state`` to (wit, cut_ms); None when exactness needs a rebuild."""
    if wit[1:] != state.wit[1:] or cut_ms < state.cut:
        return None
    if wit[0] != state.wit[0]:
        change = changes_since(conn, key, state.wit[0])
        if change is None or not change[1] or change[0] is None:
            return None                                   # destructive / unproven change
        STATS['frontier_deltas'] += 1
        _classify(state, conn.execute(_INDEX_ROWS + ' AND event_ms>=?', (key, change[0])).fetchall(), state.cut)
        # rows already pending were re-listed above: keep one copy, drop those now visible
    if cut_ms > state.cut or state.pending:
        due = [(r, *v) for r, v in state.pending.items() if max(v[1], v[2]) <= cut_ms]
        state.cut = cut_ms
        if due:
            for r, *_ in due:
                state.pending.pop(r)
            _classify(state, due, cut_ms)
    state.wit = wit
    return state


def _select_rowids(state, window_start_ms, limit):
    """The old ranking recomputed in RAM: ready asc / event desc, running maximum."""
    ev, av, ob, rid = state.ev, state.av, state.ob, state.rid
    if not len(ev):
        return []
    ready = np.maximum(np.maximum(ev, av), ob)
    order = np.lexsort((-ev, ready))
    newest = np.maximum.accumulate(ev[order])
    record = np.zeros(len(ev), dtype=bool)
    record[order] = ev[order] == newest
    before = ready < window_start_ms
    keep = (ev >= window_start_ms) | ((ready >= window_start_ms) & record)
    if before.any():
        keep |= ev == ev[before].max()
    chosen = np.nonzero(keep)[0]
    chosen = chosen[np.argsort(-ev[chosen], kind='stable')][:limit]
    return [int(x) for x in rid[chosen]]


def _frontier_window(conn, key, as_of_ms, window_start_ms, limit, snapshot_owned=False):
    """Exact current window from RAM metadata; payloads only for newly selected rows."""
    wit = witness(conn, key)
    dbid = _db_id(conn)
    if wit is None or dbid is None or (conn.in_transaction and not snapshot_owned):
        return _window_by_index(conn, key, as_of_ms, window_start_ms, limit)
    fk = (dbid, key)
    with _FRONT_LOCK:
        state = FRONTIERS.get(fk)
        if state is not None:
            FRONTIERS.move_to_end(fk)
    if state is None:
        state = _rebuild_frontier(conn, key, as_of_ms, wit)
        with _FRONT_LOCK:
            FRONTIERS[fk] = state
    with state.lock:
        if state.wit != wit or as_of_ms != state.cut or state.pending:
            if _advance_frontier(conn, key, state, as_of_ms, wit) is None:
                fresh = _rebuild_frontier(conn, key, as_of_ms, wit)
                for name in ('wit', 'cut', 'ev', 'av', 'ob', 'rid', 'pending'):
                    setattr(state, name, getattr(fresh, name))
                state.decoded = {}
                state.decoded_sizes = {}
        rids = _select_rowids(state, window_start_ms, limit)
        if not rids:
            return None
        missing = [r for r in rids if r not in state.decoded]
        if missing:
            rows = conn.execute("SELECT rowid,record_json,revision_id,event_ms,available_ms,observed_ms FROM market_revisions "
                                "WHERE rowid IN (SELECT value FROM json_each(?))", (json.dumps(missing),)).fetchall()
            if len(rows) != len(missing):
                state.decoded = {}
                raise ValueError('market_revision_index_corrupt')
            cache = {}
            for rowid, raw, rev, event, available, observed in rows:
                state.decoded[rowid] = _verify_row(conn, raw, rev, event, available, observed, cache)
                state.decoded_sizes[rowid] = _owned_size(state.decoded[rowid]) + 128
        records = [state.decoded[r] for r in rids]
        state.decoded = {r: state.decoded[r] for r in rids}
        state.decoded_sizes = {r: state.decoded_sizes[r] for r in rids}
        records = sorted(records, key=lambda r: r['event_time_ms'])
    with _FRONT_LOCK:
        while FRONTIERS and sum(f.nbytes() for f in FRONTIERS.values()) > FRONTIER_MAX_BYTES:
            FRONTIERS.popitem(last=False)
    df = pd.DataFrame(records)
    df['ts'] = pd.to_datetime(df['event_time_ms'], unit='ms', utc=True)
    df.attrs.update(schema_version=SCHEMA, timeframe=None, timestamp_semantics='interval_open',
                    as_of_ms=as_of_ms, read_mode='as_of')
    return df


def _window_by_index(conn, key, as_of_ms, window_start_ms, limit):
    """Stateless exact path (uncached connection): same ranking, built fresh."""
    state = _rebuild_frontier(conn, key, as_of_ms, (0, 0, 0))
    rids = _select_rowids(state, window_start_ms, limit)
    if not rids:
        return None
    rows = conn.execute("SELECT rowid,record_json,revision_id,event_ms,available_ms,observed_ms FROM market_revisions "
                        "WHERE rowid IN (SELECT value FROM json_each(?))", (json.dumps(rids),)).fetchall()
    cache = {}
    records = sorted((_verify_row(conn, raw, rev, event, available, observed, cache)
                      for _, raw, rev, event, available, observed in rows), key=lambda r: r['event_time_ms'])
    df = pd.DataFrame(records)
    df['ts'] = pd.to_datetime(df['event_time_ms'], unit='ms', utc=True)
    df.attrs.update(schema_version=SCHEMA, timeframe=None, timestamp_semantics='interval_open',
                    as_of_ms=as_of_ms, read_mode='as_of')
    return df


def _owned_size(value):
    """Conservative owned Python payload size (shared keys may be counted twice)."""
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_owned_size(k) + _owned_size(v) for k, v in value.items())
    elif isinstance(value, (list, tuple)):
        size += sum(_owned_size(v) for v in value)
    return size


def load_window_typed(conn, key, *, as_of_ms, window_start_ms, instrument_id, limit=200000):
    # Index selection, payload verification and witness reads must use ONE SQLite
    # snapshot. Otherwise a concurrent edit can change the selected row between
    # ranking and decoding. Never end a transaction owned by our caller.
    owned = not conn.in_transaction
    if owned:
        conn.execute('BEGIN')
    try:
        return _load_window_typed(conn, key, as_of_ms=as_of_ms,
                                  window_start_ms=window_start_ms,
                                  instrument_id=instrument_id, limit=limit,
                                  snapshot_owned=owned)
    finally:
        if owned:
            conn.rollback()


def _load_window_typed(conn, key, *, as_of_ms, window_start_ms, instrument_id, limit=200000, snapshot_owned=False):
    """Current-window reader. -> (frame | None, reason).

    Same selection as ``load(window_start_ms=...)`` (exact predecessor, late
    older frontier events, exact availability cut, equal-clock rowid winner).
    Rank the covering (series,event,available,observed) index and authenticate
    every selected receipt against the requested instrument. Unresolved global
    metadata does not force a payload scan of unrelated historical revisions.
    Known conflicting aliases remain refused; exceptional partial-bar series
    retain the original final-bar selection.
    """
    cut(as_of_ms), cut(window_start_ms)
    if window_start_ms > as_of_ms:
        raise ValueError('current_window_requires_exact_asof_revision_selection')
    meta = _meta(conn, key)
    idents = _non_null_idents(meta[1]) if meta is not None else []
    if instrument_id is None and (meta is None or not meta[3]):
        demand(conn, key, True)
        return _legacy_window(conn, key, as_of_ms, window_start_ms, instrument_id, limit)
    # A known conflicting alias remains refused. A historical metadata scan
    # is not needed to authenticate an explicitly named CURRENT instrument:
    # index-rank the exact requested window, then verify every selected receipt.
    if len(idents) > 1:
        STATS['identity_refused'] += 1
        return None, 'identity_ambiguous'
    if meta is None or not meta[3]:
        STATS['identity_unresolved'] += 1
        # A typed current window proves its own instrument below. Seed only
        # the commit witness; unqualified/history callers may request a global
        # alias scan explicitly, independently of the trading input path.
        demand(conn, key, False)
    if meta is not None and meta[2]:
        # Derivative observations are instantaneous/final. Preserve the older
        # final-bar selection for any exceptional series containing partials.
        return _legacy_window(conn, key, as_of_ms, window_start_ms, instrument_id, limit)
    STATS['window_reads'] += 1
    df = _frontier_window(conn, key, as_of_ms, window_start_ms, limit, snapshot_owned)
    if df is None:
        return None, 'missing'
    if not df['bar_state'].eq('FINAL').all():
        return _legacy_window(conn, key, as_of_ms, window_start_ms, instrument_id, limit)
    # NULL-identity legacy rows may legitimately sit in a series; if one is
    # actually selected the typed consumer must not receive the frame.
    if instrument_id is not None and not df['instrument_id'].eq(instrument_id).all():
        STATS['identity_refused'] += 1
        return None, 'identity_ambiguous'
    return df, 'valid'


# ── cross-cycle current-input cache ─────────────────────────────────────────
_CACHE_ONLY = contextvars.ContextVar('market_cache_only', default=False)
_REASON = contextvars.ContextVar('market_cache_reason', default=None)
_WARM = OrderedDict()           # cache key -> refill callable (bounded)
_WARM_LIMIT = 1024
WINDOW_SLACK_MS = 86_400_000
_WARM_LOCK = threading.Lock()


@contextmanager
def cache_only():
    """Trading-path reads: serve a valid cached window or report 'warming'.

    A miss queues the refill for the warm/producer threads instead of loading
    (or scanning history) inside the scan.
    """
    token = _CACHE_ONLY.set(True)
    try:
        yield
    finally:
        _CACHE_ONLY.reset(token)


def last_reason():
    return _REASON.get()


def enqueue_warm(ck, refill):
    if refill is None:
        return
    with _WARM_LOCK:
        if ck in _WARM or len(_WARM) < _WARM_LIMIT:
            _WARM[ck] = refill


def run_warm(max_s=5.0, max_items=64):
    """Execute queued refills on the CALLING (non-trading) thread. -> count done."""
    start, done = time.monotonic(), 0
    while done < max_items and time.monotonic() - start < max_s:
        with _WARM_LOCK:
            if not _WARM:
                break
            ck, refill = _WARM.popitem(last=False)
        token = _CACHE_ONLY.set(False)
        try:
            refill()
            STATS['warm_refills'] += 1
        except Exception as exc:                   # reported, never raised into a cycle
            STATS['warm_failures'] += 1
            log.warning('input warm refill failed: %s', type(exc).__name__)
        finally:
            _CACHE_ONLY.reset(token)
        done += 1
    return done


def refresh_entries(conn, key, *, deferred=False):
    """After a producer commit: re-read cached windows of that series now.

    Runs on the producer's thread, outside any transaction, so the next scan
    finds a valid entry instead of a miss.
    """
    dbid = _db_id(conn)
    if dbid is None or conn.in_transaction:
        return 0
    with CURRENT.lock:
        entries = [(ck, e['refill']) for ck, e in CURRENT.entries.items()
                   if ck[0] == dbid and ck[1] == key and e['refill'] is not None]
    if deferred:
        for ck, refill in entries:
            enqueue_warm(ck, refill)
        return len(entries)
    refills = [refill for _, refill in entries]
    for refill in refills:
        token = _CACHE_ONLY.set(False)
        try:
            refill()
            STATS['commit_refreshes'] += 1
        except Exception:
            STATS['warm_failures'] += 1
        finally:
            _CACHE_ONLY.reset(token)
    return len(refills)


class CurrentCache:
    """Validated current windows reused between scans.

    An entry is reused only while (a) the series' committed generation, the
    global raw-source generation and the database epoch are unchanged, (b) the
    new cut is not earlier than the cut it was read at, and (c) no
    already-committed row has a receipt clock inside (entry cut, new cut].
    Receipts keep their ORIGINAL clocks and every consumer still applies
    freshness at its own cut, so a hit never renews or hides anything.
    Bounded by entries, rows and deep bytes; callers always receive an owned
    deep copy. Entries are keyed by the physical database file identity.
    """
    def __init__(self, max_entries=256, max_rows=400_000, max_entry_rows=60_000,
                 max_bytes=256 << 20, max_entry_bytes=48 << 20):
        self.max_entries, self.max_rows, self.max_entry_rows = max_entries, max_rows, max_entry_rows
        self.max_bytes, self.max_entry_bytes = max_bytes, max_entry_bytes
        self.entries = {}
        self.rows = 0
        self.bytes = 0
        self.lock = threading.Lock()
        self.pin_symbols, self.pin_keys = frozenset(), frozenset()
        self.evictions = self.pinned_evictions = 0

    def set_pins(self, symbols=(), keys=()):
        """Held and currently-required inputs: evicted only after every unpinned entry."""
        with self.lock:
            self.pin_symbols, self.pin_keys = frozenset(symbols), frozenset(keys)

    def _pinned(self, ck):
        key = ck[1] if len(ck) > 1 else None
        if not isinstance(key, str):
            return False
        if key in self.pin_keys:
            return True
        parts = key.split(':')
        return len(parts) > 2 and ':'.join(parts[1:-1]) in self.pin_symbols

    def capacity(self):
        with self.lock:
            pinned = [e for ck, e in self.entries.items() if self._pinned(ck)]
            return dict(entries=len(self.entries), max_entries=self.max_entries, rows=self.rows,
                        max_rows=self.max_rows, bytes=self.bytes, max_bytes=self.max_bytes,
                        pinned_entries=len(pinned), pinned_bytes=sum(e['bytes'] for e in pinned),
                        evictions=self.evictions, pinned_evictions=self.pinned_evictions)

    def clear(self):
        with self.lock:
            self.entries.clear()
            self.rows = self.bytes = 0

    def _drop(self, ck):
        old = self.entries.pop(ck, None)
        if old is not None:
            self.rows -= old['n']
            self.bytes -= old['bytes']
        return old

    def get(self, ck, wit, cut_ms, limit, window, wstart=None):
        """-> (hit, frame, refill of a stale entry, the stale entry itself)."""
        with self.lock:
            e = self.entries.get(ck)
            if e is None:
                return False, None, None, None
            ok = (e['wit'] == wit and cut_ms >= e['cut'] and (e['next'] is None or cut_ms < e['next'])
                  and (limit == e['limit'] and e['wstart'] <= wstart <= e['wstart'] + WINDOW_SLACK_MS
                       if window else (limit <= e['limit'] or not e['full'])))
            if not ok:
                self._drop(ck)
                return False, None, e['refill'], e
            self.entries[ck] = self.entries.pop(ck)         # LRU touch
            frame = e['frame']
            return True, (None if frame is None else _owned(frame, None if window else limit)), None, None

    def put(self, ck, wit, cut_ms, limit, nxt, frame, refill=None, wstart=None):
        n = 0 if frame is None else len(frame)
        size = 0 if frame is None else int(frame.memory_usage(deep=True).sum())
        if n > self.max_entry_rows or size > self.max_entry_bytes:
            with self.lock:
                self._drop(ck)
            STATS['cache_rejected_oversize'] += 1
            return
        with self.lock:
            self._drop(ck)
            self.entries[ck] = dict(wit=wit, cut=cut_ms, limit=limit, next=nxt, n=n, bytes=size,
                                    frame=None if frame is None else _owned(frame, None), refill=refill, wstart=wstart,
                                    full=frame is not None and n >= limit)
            self.rows += n
            self.bytes += size
            while self.entries and (len(self.entries) > self.max_entries or self.rows > self.max_rows
                                    or self.bytes > self.max_bytes):
                victim = next((k for k in self.entries if k != ck and not self._pinned(k)), None)
                if victim is None:                       # only pinned entries remain: bounds still hold
                    victim = next((k for k in self.entries if k != ck), ck)
                    self.pinned_evictions += 1
                self.evictions += 1
                self._drop(victim)


def _owned(frame, limit):
    out = frame.iloc[-limit:] if limit else frame
    attrs = deepcopy(frame.attrs)
    out = out.copy(deep=True)
    out.attrs = attrs
    return out


CURRENT = CurrentCache()


def _db_id(conn):
    """Physical identity (resolved path, device, inode); None for memory/unknown."""
    try:
        path = conn.execute('PRAGMA database_list').fetchone()[2]
        if not path:
            return None
        st = os.stat(path)
        return (os.path.realpath(path), st.st_dev, st.st_ino)
    except Exception:
        return None


def next_ready(conn, key, floor_ms, cut_ms):
    """Earliest time an already-committed row of ``key`` (events >= floor) becomes
    visible after ``cut_ms``; None when nothing is pending. Index-only."""
    return conn.execute('SELECT MIN(MAX(available_ms,observed_ms)) FROM market_revisions WHERE series_key=? '
                        'AND event_ms>=? AND (available_ms>? OR observed_ms>?)',
                        (key, floor_ms, cut_ms, cut_ms)).fetchone()[0]


_NO_DELTA = object()


def _incremental(conn, key, stale, wit, cut_ms, limit, instrument_id, source, include_partial=False):
    """Refresh a stale candle-style window from committed change metadata.

    Exact only for pure insertions: events below the changed floor keep their
    winners, events at/above it are re-selected by the same query. Edits,
    deletions, unknown/pruned log ranges, a different epoch/raw generation, a
    crossed future-availability point or a different limit use the full
    (off-path) read instead.
    """
    old = stale['wit']
    if (old[1:] != wit[1:] or limit != stale['limit'] or cut_ms < stale['cut']
            or (stale['next'] is not None and cut_ms >= stale['next'])):
        return _NO_DELTA
    change = changes_since(conn, key, old[0])
    if change is None or not change[1] or change[0] is None:
        return _NO_DELTA
    floor = change[0]
    reader = load_current_candles if key.startswith('candle:') else load
    delta = reader(conn, key, as_of_ms=cut_ms, limit=limit, instrument_id=instrument_id, source=source,
                   min_event_ms=floor, include_partial=include_partial)
    kept = stale['frame']
    kept = None if kept is None else kept[kept['event_time_ms'] < floor]
    if delta is None:
        merged = kept if kept is not None and len(kept) else None
    elif kept is None or not len(kept):
        merged = delta
    else:
        merged = pd.concat([kept, delta], ignore_index=True)
    STATS['incremental_refreshes'] += 1
    STATS['incremental_rows_read'] += 0 if delta is None else len(delta)
    if merged is None:
        return None
    merged = merged.iloc[-limit:].reset_index(drop=True)
    merged.attrs = deepcopy(stale['frame'].attrs if stale['frame'] is not None else delta.attrs)
    merged.attrs['as_of_ms'] = cut_ms
    return merged


def load_current_candles(conn, key, *, as_of_ms, limit, instrument_id, source,
                         include_partial=False, min_event_ms=None, latest_only=False):
    """Rank covering-index metadata first; decode only current candle candidates.

    A candidate from another identity/source or a forming candle falls back to
    the exact original predicate for THAT event. Older matching revisions remain
    selectable, without authenticating every historical version in the window.
    Producers comparing unchanged values use latest_only: an excluded newest
    receipt must not be hidden by an older matching value and suppress a new
    verified observation. Consumer selection keeps its existing semantics.
    """
    cut(as_of_ms)
    if min_event_ms is not None:
        cut(min_event_ms)
    if instrument_id is None or source is None or type(limit) is not int or limit <= 0:
        return load(conn, key, as_of_ms=as_of_ms, limit=limit, instrument_id=instrument_id,
                    source=source, include_partial=include_partial, min_event_ms=min_event_ms)
    owned = not conn.in_transaction
    if owned:
        conn.execute('BEGIN')
    try:
        selected, before = [], None
        source_marker = text_reference(source)
        while len(selected) < limit:
            n = min(256, limit - len(selected))
            predicates = 'series_key=? AND available_ms<=? AND observed_ms<=?'
            args = [key, as_of_ms, as_of_ms]
            if before is not None:
                predicates += ' AND event_ms<?'; args.append(before)
            if min_event_ms is not None:
                predicates += ' AND event_ms>=?'; args.append(min_event_ms)
            # Both phases use only columns in market_revision_cut plus rowid.
            q = f'''WITH events AS MATERIALIZED (
                SELECT event_ms FROM market_revisions WHERE {predicates}
                GROUP BY event_ms ORDER BY event_ms DESC LIMIT ?), winners AS (
                SELECT rowid AS selected_rowid, event_ms,
                  ROW_NUMBER() OVER (PARTITION BY event_ms ORDER BY observed_ms DESC,rowid DESC) AS ordinal
                FROM market_revisions WHERE series_key=? AND available_ms<=? AND observed_ms<=?
                  AND event_ms IN (SELECT event_ms FROM events))
                SELECT record_json,revision_id,event_ms,available_ms,observed_ms FROM market_revisions
                WHERE rowid IN (SELECT selected_rowid FROM winners WHERE ordinal=1)
                ORDER BY event_ms DESC'''
            rows = _selected_revision_rows(conn, q, [*args, n, key, as_of_ms, as_of_ms], batch=True)
            if not rows:
                break
            for row in rows:
                physical = json.loads(row[0])
                matching = (physical.get('instrument_id') == instrument_id and
                            physical.get('source') in (source, source_marker) and
                            (include_partial or (physical.get('bar_state') is not None and physical['bar_state'] != 'PARTIAL')))
                if matching:
                    selected.append(row)
                    continue
                if latest_only:
                    continue
                # Filtering precedes ranking in the original reader. If the
                # newest row is excluded, retain its newest MATCHING predecessor.
                q2 = '''SELECT record_json,revision_id,event_ms,available_ms,observed_ms
                    FROM market_revisions WHERE series_key=? AND event_ms=?
                    AND available_ms<=? AND observed_ms<=?
                    AND json_extract(record_json,'$.instrument_id')=?
                    AND json_extract(record_json,'$.source') IN (?,?)'''
                a2 = [key, row[2], as_of_ms, as_of_ms, instrument_id, source, source_marker]
                if not include_partial:
                    q2 += " AND json_extract(record_json,'$.bar_state')!='PARTIAL'"
                q2 += ' ORDER BY observed_ms DESC,rowid DESC LIMIT 1'
                selected.extend(_selected_revision_rows(conn, q2, a2, batch=True))
            before = rows[-1][2]
        return _assemble(conn, selected, batch=True, as_of_ms=as_of_ms, limit=limit,
                         replay_tf=None, include_partial=include_partial,
                         revision_stream=False, metadata_only=False)
    finally:
        if owned:
            conn.rollback()


def cached_current(conn, key, *, as_of_ms, limit, instrument_id=None, source=None,
                   include_partial=False, window_start_ms=None, loader=None, refill=None):
    """Cross-cycle cached current read. ``loader()`` performs the uncached read.

    ``refill`` re-runs the same read on a producer/warm thread. Inside
    ``cache_only()`` a miss never loads: it queues ``refill`` and reports
    'warming' (see ``last_reason``).
    """
    default_reader = load_current_candles if key.startswith('candle:') and window_start_ms is None else load
    extra = {} if default_reader is load_current_candles else dict(window_start_ms=window_start_ms)
    loader = loader or (lambda: default_reader(conn, key, as_of_ms=as_of_ms, limit=limit, instrument_id=instrument_id,
                                      source=source, include_partial=include_partial, **extra))
    cut(as_of_ms)
    _REASON.set(None)
    only = _CACHE_ONLY.get()
    dbid = _db_id(conn)
    # An open transaction on this connection may hold uncommitted rows that
    # another connection can never see (and a rollback can reuse generations):
    # such a read is neither served from nor stored in the shared cache.
    wit = None if (dbid is None or conn.in_transaction) else witness(conn, key)
    if wit is None:
        if only:
            STATS['cache_only_unproven'] += 1
            _REASON.set('witness_unproven')
            enqueue_warm((dbid, key, 'unproven'), refill)
            return None
        STATS['cache_uncacheable'] += 1
        return loader()
    window = window_start_ms is not None
    # A window entry covers every start inside [its start, start + slack]: rows
    # from an earlier start are a superset of what a later start needs.
    ck = (dbid, key, instrument_id, source, include_partial, window)
    hit, frame, stale_refill, stale = CURRENT.get(ck, wit, as_of_ms, limit, window, window_start_ms)
    if hit:
        STATS['cache_hits'] += 1
        return frame
    STATS['cache_misses'] += 1
    if only:
        STATS['warming_misses'] += 1
        _REASON.set('warming')
        enqueue_warm(ck, refill or stale_refill)
        return None
    # Candle delta selection and raw payload expansion share a read snapshot.
    # Typed windows own their snapshot inside load_window_typed.
    owned = not window
    if owned:
        conn.execute('BEGIN')
    try:
        if owned:
            wit = witness(conn, key)
        frame = None
        if stale is not None and not window:
            frame = _incremental(conn, key, stale, wit, as_of_ms, limit, instrument_id, source, include_partial)
        if frame is _NO_DELTA or stale is None or window:
            frame = loader()
        floor = 0
        if not window and frame is not None and len(frame) >= limit:
            floor = int(frame['event_time_ms'].min())
        nxt = next_ready(conn, key, floor, as_of_ms)
    finally:
        if owned:
            conn.rollback()
    CURRENT.put(ck, wit, as_of_ms, limit, nxt, frame, refill, window_start_ms)
    return frame


# ── bounded, resumable metadata resolution (off the trading path) ───────────
def resolve_pending(conn, *, budget_rows=20000, seed=True):
    """Own metadata transactions completely, including a failed first INSERT."""
    if conn.in_transaction:
        raise ValueError('metadata_resolution_requires_idle_connection')
    try:
        return _resolve_pending(conn, budget_rows=budget_rows, seed=seed)
    except BaseException:
        # sqlite3 opens an implicit transaction before an INSERT that can fail
        # with SQLITE_BUSY. Never retain that transaction across worker retries.
        try:
            conn.rollback()
        except BaseException:
            conn.close()
        raise


def _resolve_pending(conn, *, budget_rows=20000, seed=True):
    """Seed witnesses for series demanded of THIS database; resolve identity
    metadata for the demanded ones that need it, in bounded resumable steps.

    Demand is scoped by physical database file. Only demanded series are
    scanned (never the whole unresolved universe), and a series demanded for
    identity is resolved before anything else. Writers keep maintaining the
    identity set via triggers while a scan is in flight; a concurrent
    UPDATE/DELETE bumps ``epoch`` and restarts that series' scan.
    """
    dbid = _db_id(conn)
    demanded = {key: v for (d, key), v in list(_DEMAND.items()) if d == dbid}
    if seed:
        for key in demanded:
            conn.execute('INSERT OR IGNORE INTO market_series_meta(series_key,gen) VALUES (?,0)', (key,))
        conn.commit()
    left, done = budget_rows, 0
    for key in sorted(k for k, wants_identity in demanded.items() if wants_identity):
        if left > 0:
            row = _meta(conn, key)
            if row is not None and not row[3]:
                left -= _resolve_one(conn, key, left)
                row = _meta(conn, key)
        if row is not None and row[3]:
            done += 1
            _DEMAND.pop((dbid, key), None)
    for key in [k for k, v in demanded.items() if not v]:
        if _meta(conn, key) is not None:
            _DEMAND.pop((dbid, key), None)     # witness seeded; nothing else was asked
    prune_change_log(conn)
    conn.commit()
    return dict(resolved=done, budget_left=left, pending=len(demanded) - done)


def _resolve_one(conn, key, budget):
    epoch, cursor, state = conn.execute(
        'SELECT epoch,scan_event,scan_json FROM market_series_meta WHERE series_key=?', (key,)).fetchone()
    state = json.loads(state) if state else dict(idents=[], partial=0, epoch=epoch, rowid=-1)
    if state['epoch'] != epoch:
        state, cursor = dict(idents=[], partial=0, epoch=epoch, rowid=-1), None
    event, rowid = (-1, -1) if cursor is None else (cursor, state['rowid'])
    # Total order (event_ms, rowid): equal-event revisions can never be skipped
    # or revisited across a budget boundary.
    rows = conn.execute(
        "SELECT event_ms,rowid,json_quote(json_extract(record_json,'$.instrument_id')),"
        "json_extract(record_json,'$.bar_state')='PARTIAL' FROM market_revisions "
        "WHERE series_key=? AND (event_ms>? OR (event_ms=? AND rowid>?)) ORDER BY event_ms,rowid LIMIT ?",
        (key, event, event, rowid, budget)).fetchall()
    STATS['resolver_rows_scanned'] += len(rows)
    idents = set(state['idents'])
    idents.update(r[2] for r in rows)
    state.update(idents=sorted(idents), partial=int(state['partial'] or any(r[3] for r in rows)))
    finished = len(rows) < budget
    conn.execute('BEGIN IMMEDIATE')
    try:
        cur = conn.execute('SELECT epoch,idents,partial FROM market_series_meta WHERE series_key=?', (key,)).fetchone()
        if cur is None or cur[0] != epoch:
            conn.rollback()
            return len(rows)
        if finished:
            seen = set(i for i in cur[1].strip('|').split('|') if i)
            merged = sorted(seen | idents)
            conn.execute('UPDATE market_series_meta SET idents=?,partial=?,resolved=1,scan_event=NULL,scan_json=NULL '
                         'WHERE series_key=?', ('|' + '|'.join(merged) + '|', int(cur[2] or state['partial']), key))
            STATS['series_resolved'] += 1
        else:
            state['rowid'] = rows[-1][1]
            conn.execute('UPDATE market_series_meta SET scan_event=?,scan_json=? WHERE series_key=?',
                         (rows[-1][0], json.dumps(state), key))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return len(rows)
