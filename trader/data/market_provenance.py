"""Shared receipt/revision helpers for the existing market-data SQLite stores.

This is storage metadata, not another feed. Legacy projections are retained
for inventory; only retained revisions can establish point-in-time truth.
Provider publication time is never inferred from a historical event stamp.
"""
from __future__ import annotations

import hashlib
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


def init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS market_raw_sources (
        content_hash TEXT PRIMARY KEY, source TEXT, raw_json TEXT NOT NULL)""")
    conn.execute('''CREATE TABLE IF NOT EXISTS market_revisions (
        revision_id TEXT PRIMARY KEY, series_key TEXT NOT NULL, event_ms INTEGER NOT NULL,
        available_ms INTEGER, observed_ms INTEGER NOT NULL, record_json TEXT NOT NULL)''')
    conn.execute('''CREATE INDEX IF NOT EXISTS market_revision_cut
        ON market_revisions(series_key, event_ms, available_ms, observed_ms)''')


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


def read_record(conn, raw):
    record = json.loads(raw)
    for field in ('source', 'raw_json'):
        marker = record.get(field)
        if not isinstance(marker, str) or not marker.startswith(TEXT_REF):
            continue
        key = marker[len(TEXT_REF):]
        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
            raise ValueError('market_source_reference_invalid')
        row = conn.execute('SELECT raw_json FROM market_raw_sources WHERE content_hash=?', (key,)).fetchone()
        if row is None or text_reference(row[0]) != marker:
            raise ValueError('market_source_reference_missing_or_corrupt')
        record[field] = row[0]
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
        market=exchange.market(symbol)
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


def append(conn, key, df=None, *, prepared=None):
    """Append/link exact retrievals atomically; ancestry stays inside the write."""
    iterator = iter(prepare(df) if prepared is None else prepared)
    wrote = False
    while batch := list(islice(iterator, 128)):
        # All parent selection remains inside the existing transaction. Bound
        # both temporary state and SQLite crossings, rather than releasing
        # the GIL once for every historical row while owning a writer lock.
        ids = tuple(dict.fromkeys(r['revision_id'] for r in batch))
        existing = set(json.loads(conn.execute('SELECT json_group_array(revision_id) FROM market_revisions '
                    'WHERE revision_id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchone()[0]))
        # Exact retries already have sealed identities. Avoid parent reads
        # and record serialization; a new acquisition has a new revision ID
        # even when the observed numeric values are unchanged.
        batch = [r for r in batch if r['revision_id'] not in existing]
        if not batch:
            continue
        wrote = True
        groups = {}
        for r in batch:
            groups.setdefault((r['instrument_id'], r['source']), set()).add(r['event_time_ms'])
        parents = {}
        for (instrument, source), events in groups.items():
            events = tuple(events)
            query = '''SELECT revision_id,event_ms,observed_ms FROM (
                SELECT revision_id,event_ms,observed_ms,ROW_NUMBER() OVER (
                PARTITION BY event_ms ORDER BY observed_ms DESC,rowid DESC) AS ordinal
                FROM market_revisions WHERE series_key=? AND event_ms IN ('''
            query += ','.join('?' for _ in events) + ''')
                AND json_extract(record_json,'$.instrument_id') IS ?
                AND (json_extract(record_json,'$.source') IS ? OR json_extract(record_json,'$.source') IS ?)) WHERE ordinal=1'''
            # Aggregate bounded metadata in SQLite to avoid per-row cursor
            # crossings under background CPU contention.
            result = conn.execute('SELECT json_group_array(json_array(revision_id,event_ms,observed_ms)) '
                                  'FROM (' + query + ')', (key, *events, instrument, source, text_reference(source) if isinstance(source, str) else None)).fetchone()[0]
            for rid, event, observed in json.loads(result):
                parents[(instrument, source, event)] = (rid, observed)
        # Fetch shared content once per bounded batch, preserving append's
        # bounded SQLite crossings under acquisition-thread contention.
        source_cache = {text_reference(r[f])[len(TEXT_REF):]: None for r in batch
                        for f in ('source','raw_json')
                        if isinstance(r.get(f),str) and len(r[f].encode()) >= TEXT_MIN}
        if source_cache:
            keys = tuple(source_cache)
            source_cache.update(conn.execute('SELECT content_hash,raw_json FROM market_raw_sources WHERE content_hash IN ('
                + ','.join('?' for _ in keys) + ')', keys).fetchall())
        writes = []
        for original in batch:
            record = dict(original)
            identity = (record['instrument_id'], record['source'], record['event_time_ms'])
            prior = parents.get(identity)
            rid, observed = record['revision_id'], record['observed_at_ms']
            record['supersedes'] = prior[0] if prior and prior[0] != rid else None
            writes.append((rid, key, record['event_time_ms'], record['available_at_ms'], observed, store_record(conn, record, text_cache=source_cache)))
            # New equal-clock rows win by rowid, exactly as the sequential
            # INSERT does. Ignored retries and older observations cannot win.
            if rid not in existing and (prior is None or observed >= prior[1]):
                parents[identity] = (rid, observed)
            existing.add(rid)
        conn.execute('INSERT OR IGNORE INTO market_revisions VALUES '
                     + ','.join('(?,?,?,?,?,?)' for _ in writes),
                     tuple(value for row in writes for value in row))

    if wrote:
        from trader.persistence.storage_cleanup import market_completion
        market_completion(conn)


def load(conn, key, *, as_of_ms, limit=200000, replay_tf=None, include_partial=False, revision_stream=False, instrument_id=None, source=None, window_start_ms=None, metadata_only=False):
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
    else:
        identities=conn.execute("SELECT DISTINCT json_extract(record_json,'$.instrument_id') FROM market_revisions WHERE series_key=? AND available_ms<=? AND observed_ms<=?",(key,as_of_ms,as_of_ms)).fetchall()
        if len({row[0] for row in identities if row[0] is not None})>1:
            return None  # an unqualified alias cannot choose a market
    if source is not None:
        clauses += " AND json_extract(record_json,'$.source') IN (?,?)"
        args.extend((source, text_reference(source)))
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
    rows = conn.execute(query,args).fetchall()
    if not revision_stream:
        rows.reverse()
    selected = {}
    stream = []
    for raw, rid, indexed_event, indexed_available, indexed_observed in rows:
        r = read_record(conn, raw)
        value = {k: r[k] for k in VALUES if k in r}
        body = {k:r[k] for k in META if k not in ('revision_id','supersedes')}
        if r['content_hash'] != digest({'raw': json.loads(r['raw_json']), 'value':value}) or r['revision_id'] != digest(body):
            raise ValueError('market_revision_content_corrupt')
        # Selection/ranking columns are outside the receipt hash. They must
        # agree with the verified receipt before they can establish a cut.
        if (rid, indexed_event, indexed_available, indexed_observed) != (
                r['revision_id'], r['event_time_ms'], r['available_at_ms'], r['observed_at_ms']):
            raise ValueError('market_revision_index_corrupt')
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
    rows = conn.execute('SELECT record_json,revision_id,event_ms,available_ms,observed_ms '
        'FROM (SELECT *,ROW_NUMBER() OVER (PARTITION BY event_ms '
        'ORDER BY observed_ms DESC,rowid DESC) ordinal FROM market_revisions WHERE '+
        clauses+') WHERE ordinal=1 ORDER BY event_ms DESC LIMIT ?', args+[limit]).fetchall()
    selected = []
    for raw, rid, event, available, observed in reversed(rows):
        r = read_record(conn, raw)
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
