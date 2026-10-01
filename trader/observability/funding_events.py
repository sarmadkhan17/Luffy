"""Bounded public historical funding lookup; raw receipts, no position invention.

Inclusive API boundaries are queried so an event exactly at either trade
boundary can be refused. No schedule (including eight-hour spacing) is assumed.
Only no-crossing proof can currently enter the paper cost protocol: the paper
ledger has no frozen event-time mark/notional authority for crossed events.
"""
import hashlib
import json
import time
from decimal import Decimal
from urllib.parse import urlencode

from trader.data.registry_provider import urllib_fetch
from trader.engine.paper_exit_evidence import digest as _digest, canonical

def digest(body):
    return _digest(canonical(body))

SCHEMA = 'binance-funding-interval.v1'
METHOD = 'BINANCE_PUBLIC_NO_FUNDING_CROSSING_V1'
ENDPOINT = 'https://fapi.binance.com/fapi/v1/fundingRate'
DOC = 'https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data'
LIMIT = 1000
MAX_BYTES = 512 * 1024


def url(symbol, start, end):
    if not isinstance(symbol, str) or not symbol.isalnum() or not symbol.isupper():
        raise ValueError('funding_symbol_invalid')
    return ENDPOINT + '?' + urlencode(dict(symbol=symbol, startTime=start, endTime=end, limit=LIMIT))


def replay(receipt):
    if receipt['schema'] != SCHEMA or receipt['environment'] != 'production':
        raise ValueError('funding_environment_unproven')
    symbol, start, end = (receipt[k] for k in ('symbol', 'open_ms', 'close_ms'))
    if type(start) is not int or type(end) is not int or not 0 < start <= end:
        raise ValueError('funding_interval_invalid')
    cursor, events, terminal = start, [], False
    for page in receipt['pages']:
        if terminal or page['url'] != url(symbol, cursor, end):
            raise ValueError('funding_page_chain_invalid')
        body = page['response_body'].encode('utf-8')
        if len(body) > MAX_BYTES or hashlib.sha256(body).hexdigest() != page['response_sha256']:
            raise ValueError('funding_response_hash_invalid')
        if (page['http_status'] != 200 or type(page['received_ms']) is not int
                or type(page['request_start_ms']) is not int
                or page['request_start_ms'] < end or page['received_ms'] < page['request_start_ms']):
            raise ValueError('funding_receipt_clock_invalid')
        rows = json.loads(body)
        if not isinstance(rows, list) or len(rows) > LIMIT:
            raise ValueError('funding_response_invalid')
        last = cursor - 1
        for row in rows:
            ts = row['fundingTime']
            rate = Decimal(row['fundingRate'])
            if (row['symbol'] != symbol or type(ts) is not int or not cursor <= ts <= end
                    or ts <= last or not rate.is_finite()):
                raise ValueError('funding_event_timestamp_or_symbol_invalid')
            last = ts
            events.append(dict(symbol=symbol, timestamp_ms=ts, published_rate=row['fundingRate'],
                published_mark_price=row.get('markPrice'), source_endpoint=page['url'],
                received_ms=page['received_ms'], response_sha256=page['response_sha256'],
                version=SCHEMA, position_notional_basis=None))
        terminal = len(rows) < LIMIT or last == end
        if rows:
            cursor = last + 1
    if not terminal:
        raise ValueError('funding_interval_coverage_unproven')
    return events


def lookup(symbol, open_ms, close_ms, *, fetch=urllib_fetch, clock=None, max_pages=32):
    """Public GET only. Caller persists returned full receipt outside production.
    Bounds are collection limits, never sample-sufficiency thresholds.
    """
    clock = clock or (lambda: time.time_ns() // 1_000_000)
    if type(open_ms) is not int or type(close_ms) is not int or not 0 < open_ms <= close_ms:
        raise ValueError('funding_interval_invalid')
    receipt = dict(schema=SCHEMA, environment='production', symbol=symbol,
                   open_ms=open_ms, close_ms=close_ms, pages=[])
    cursor = open_ms
    for _ in range(max_pages):
        endpoint = url(symbol, cursor, close_ms)
        start = clock()
        if start < close_ms:
            raise ValueError('funding_interval_not_historical')
        response = fetch(endpoint, timeout_s=10, max_bytes=MAX_BYTES)
        received = clock()
        if response.status != 200:
            raise ValueError('funding_http_failure')
        page = dict(url=endpoint, request_start_ms=start, received_ms=received,
                    http_status=response.status, response_body=response.body.decode('utf-8'),
                    response_sha256=hashlib.sha256(response.body).hexdigest())
        receipt['pages'].append(page)
        rows = json.loads(response.body)
        # replay validates all pages, including symbol/ordering/hash/timestamps.
        try:
            events = replay(receipt)
        except ValueError as exc:
            if str(exc) != 'funding_interval_coverage_unproven':
                raise
        else:
            receipt['events'] = events
            receipt['crossed_events'] = [e for e in events if open_ms < e['timestamp_ms'] < close_ms]
            receipt['boundary_events'] = [e for e in events if e['timestamp_ms'] in (open_ms, close_ms)]
            receipt['receipt_id'] = digest(receipt)
            return receipt
        cursor = rows[-1]['fundingTime'] + 1
    raise ValueError('funding_page_bound_exhausted')


def no_crossing_source(bound, receipt, *, venue_symbol, environment):
    """No funding basis fallback. Boundary events are explicitly unresolved."""
    if (environment != 'production' or bound.get('venue_environment') != environment
            or bound['market_type'] != 'futures'):
        raise ValueError('funding_market_environment_unproven')
    # Existing Binance unified-symbol mapping is exact, not fuzzy attribution.
    if bound['instrument'].split(':')[0].replace('/', '') != venue_symbol:
        raise ValueError('funding_instrument_differs')
    if (receipt['symbol'] != venue_symbol or receipt['open_ms'] != bound['entry']['time_ms']
            or receipt['close_ms'] != bound['exit']['time_ms']):
        raise ValueError('funding_binding_differs')
    events = replay(receipt)
    if (receipt.get('crossed_events') != [e for e in events if receipt['open_ms'] < e['timestamp_ms'] < receipt['close_ms']]
            or receipt.get('boundary_events') != [e for e in events if e['timestamp_ms'] in (receipt['open_ms'], receipt['close_ms'])]):
        raise ValueError('funding_event_partition_differs')
    if events != receipt['events'] or receipt['receipt_id'] != digest(
            {k:v for k,v in receipt.items() if k != 'receipt_id'}):
        raise ValueError('funding_receipt_replay_differs')
    if events:
        boundary = any(e['timestamp_ms'] in (receipt['open_ms'], receipt['close_ms']) for e in events)
        raise ValueError('FUNDING_BOUNDARY_TIMING_UNPROVEN' if boundary else 'FUNDING_POSITION_NOTIONAL_BASIS_UNAVAILABLE')
    body = dict(method=METHOD, kind='funding', currency='USDT', version=SCHEMA,
        captured_ms=receipt['pages'][-1]['received_ms'], binding=bound,
        provenance=dict(endpoint=ENDPOINT, docs=DOC, receipt=receipt,
                        environment=environment, venue_symbol=venue_symbol),
        interval_complete=True, events=[],
        timing_semantics='Inclusive historical API interval contains no event, including either trade boundary')
    body['source_id'] = digest(body)
    return body


def validate_no_crossing(source):
    try:
        p = source['provenance']
        expected = no_crossing_source(source['binding'], p['receipt'],
            venue_symbol=p['venue_symbol'], environment=p['environment'])
        return source == expected
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return False
