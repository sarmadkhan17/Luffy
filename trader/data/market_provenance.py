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
    while batch := list(islice(iterator, 128)):
        # All parent selection remains inside the existing transaction. Bound
        # both temporary state and SQLite crossings, rather than releasing
        # the GIL once for every historical row while owning a writer lock.
        ids = tuple(dict.fromkeys(r['revision_id'] for r in batch))
        existing = set(json.loads(conn.execute('SELECT json_group_array(revision_id) FROM market_revisions '
                    'WHERE revision_id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchone()[0]))
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
                AND json_extract(record_json,'$.source') IS ?) WHERE ordinal=1'''
            # Aggregate bounded metadata in SQLite to avoid per-row cursor
            # crossings under background CPU contention.
            result = conn.execute('SELECT json_group_array(json_array(revision_id,event_ms,observed_ms)) '
                                  'FROM (' + query + ')', (key, *events, instrument, source)).fetchone()[0]
            for rid, event, observed in json.loads(result):
                parents[(instrument, source, event)] = (rid, observed)
        writes = []
        for original in batch:
            record = dict(original)
            identity = (record['instrument_id'], record['source'], record['event_time_ms'])
            prior = parents.get(identity)
            rid, observed = record['revision_id'], record['observed_at_ms']
            record['supersedes'] = prior[0] if prior and prior[0] != rid else None
            writes.append((rid, key, record['event_time_ms'], record['available_at_ms'], observed, encode(record)))
            # New equal-clock rows win by rowid, exactly as the sequential
            # INSERT does. Ignored retries and older observations cannot win.
            if rid not in existing and (prior is None or observed >= prior[1]):
                parents[identity] = (rid, observed)
            existing.add(rid)
        conn.execute('INSERT OR IGNORE INTO market_revisions VALUES '
                     + ','.join('(?,?,?,?,?,?)' for _ in writes),
                     tuple(value for row in writes for value in row))


def load(conn, key, *, as_of_ms, limit=200000, replay_tf=None, include_partial=False, revision_stream=False, instrument_id=None, source=None, window_start_ms=None):
    """Last retained revision of EACH event eligible at the exact decision cut.

    Replay additionally requires first-use eligibility at that bar's close.
    UNKNOWN legacy rows never receive inferred receipt clocks.
    """
    cut(as_of_ms)
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
        clauses += " AND json_extract(record_json,'$.source')=?"
        args.append(source)
    if replay_tf:
        clauses += ' AND available_ms<=event_ms+?'
        args.append(TF_MS[replay_tf])
    if not include_partial:
        clauses += " AND json_extract(record_json,'$.bar_state')!='PARTIAL'"
    if revision_stream:
        # Retain all versions of the bounded event window, not only the
        # database's newest projection of those events.
        query = f"""SELECT record_json FROM market_revisions {clauses}
            AND event_ms IN (SELECT DISTINCT event_ms FROM market_revisions
            WHERE series_key=? AND available_ms<=? AND observed_ms<=?
            ORDER BY event_ms DESC LIMIT ?) ORDER BY event_ms,observed_ms,rowid"""
        args += [key,as_of_ms,as_of_ms,limit]
    else:
        query = f"""SELECT record_json FROM (SELECT record_json,event_ms,
            ROW_NUMBER() OVER (PARTITION BY event_ms ORDER BY observed_ms DESC,rowid DESC) AS ordinal
            FROM market_revisions {clauses}) WHERE ordinal=1 ORDER BY event_ms DESC LIMIT ?"""
        if window_start_ms is not None:
            # Filter AFTER exact revision selection. Before the first supplied
            # bar retain its exact predecessor plus only late older events
            # that can become the newest event. Dominated late corrections
            # cannot change align() at any cut in this window.
            query = f"""WITH ranked AS (SELECT record_json,event_ms,available_ms,observed_ms,
                ROW_NUMBER() OVER (PARTITION BY event_ms ORDER BY observed_ms DESC,rowid DESC) AS ordinal
                FROM market_revisions {clauses}), current AS (
                SELECT *,MAX(event_ms,available_ms,observed_ms) AS ready_ms FROM ranked WHERE ordinal=1),
                frontier AS (SELECT *,MAX(event_ms) OVER (
                    ORDER BY ready_ms,event_ms DESC ROWS UNBOUNDED PRECEDING) AS newest FROM current)
                SELECT record_json FROM frontier WHERE event_ms>=?
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
    for (raw,) in rows:
        r = json.loads(raw)
        value = {k: r[k] for k in VALUES if k in r}
        body = {k:r[k] for k in META if k not in ('revision_id','supersedes')}
        if r['content_hash'] != digest({'raw': json.loads(r['raw_json']), 'value':value}) or r['revision_id'] != digest(body):
            raise ValueError('market_revision_content_corrupt')
        event = r['event_time_ms']
        if replay_tf and r['available_at_ms'] > event + TF_MS[replay_tf]:
            continue
        if r['bar_state'] == 'PARTIAL' and not include_partial:
            continue
        selected[event] = r
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
            row = conn.execute('SELECT record_json FROM market_revisions WHERE revision_id=?', (rid,)).fetchone()
            if row:
                break
        if not row:
            raise ValueError('lineage_receipt_missing')
        r = json.loads(row[0])
        value = {k: r[k] for k in VALUES if k in r}
        body = {k: r[k] for k in META if k not in ('revision_id', 'supersedes')}
        if (r['revision_id'] != rid or r['content_hash'] != digest({'raw': json.loads(r['raw_json']), 'value': value})
                or rid != digest(body)):
            raise ValueError('market_revision_content_corrupt')
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
