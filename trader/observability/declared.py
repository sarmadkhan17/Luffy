"""Bounded public observation of frozen population membership, outside trading.

Each scheduled pass attempts EVERY declared member. A fresh completion receipt
attests collection completion, not data availability or trading eligibility.
No cache substitution, historical availability claim, or venue credentials.
"""
from concurrent.futures import ThreadPoolExecutor
import argparse
import fcntl
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

from .attention import SCHEMA, FIELDS, settings
from . import collector_health as H, population as P
from .store import Store, encode
from trader.cognition.contracts import INPUT_SCHEMA, TF_MS

HEALTH_SCHEMA = 'declared-population-health.v1'
PROTOCOL = 'declared-population-public-4h.v1'
MAX_SYMBOLS = 64
BAR_LIMIT = 28
ENDPOINT = 'https://fapi.binance.com/fapi/v1/klines'


def source_path(data):
    data = Path(data)
    return data/'declared-population/attention.db' if (data/'declared_population.enabled').exists() else data/'attention.db'


def declaration(config):
    d = json.loads(Path(config['declaration']).read_text())
    freeze = json.loads(Path(config['receipt']).read_text())
    P.D.validate_freeze(freeze, d)
    symbols = d['universe']
    if (d['collection_mode'] != 'forward' or not 1 <= len(symbols) <= MAX_SYMBOLS
            or len(set(symbols)) != len(symbols)
            or any(not re.fullmatch(r'[A-Z0-9]+/USDT', s) for s in symbols)):
        raise ValueError('declared_universe_unsupported')
    if H.clock_ms() < freeze['observed_frozen_ms']:
        raise ValueError('declared_freeze_future')
    return d


def fetch(symbol):
    # A disposable process imposes a TOTAL request/read deadline, including DNS.
    p = subprocess.run([sys.executable, '-m', 'trader.observability.declared', '--fetch', symbol],
                       capture_output=True, text=True, timeout=6)
    if p.returncode or len(p.stdout) > 65536:
        raise ValueError('public_fetch_failed')
    return json.loads(p.stdout)


def observe(symbol, fetcher, clock):
    started = clock()
    try:
        raw = fetcher(symbol)
        observed = clock()
        if not isinstance(raw, list) or len(raw) > BAR_LIMIT:
            raise ValueError('invalid_response')
        bars = []
        previous = -1
        for row in raw:
            ms = row[0]
            if type(ms) is not int or ms < 0 or ms % TF_MS['4h'] or ms <= previous:
                raise ValueError('invalid_bar_clock')
            previous = ms
            values = [float(v) for v in row[1:6]]
            if len(values) != 5 or not all(math.isfinite(v) for v in values):
                raise ValueError('invalid_bar_values')
            o, h, l, c, v = values
            if min(o,h,l,c) <= 0 or v < 0 or l > min(o,c) or h < max(o,c):
                raise ValueError('invalid_bar_values')
            if ms+TF_MS['4h'] <= observed:
                bars.append(dict(symbol=symbol, open_ms=ms, **dict(zip(FIELDS,values)),
                                 available_ms=observed, source=ENDPOINT))
        anchor = (observed//TF_MS['4h']-1)*TF_MS['4h']
        status = 'unavailable' if not bars else 'stale' if bars[-1]['open_ms'] != anchor else 'available'
        return bars[-26:], dict(symbol=symbol, status=status, started_ms=started,
                                observed_ms=observed, closed_bars=len(bars[-26:]),
                                newest_open_ms=bars[-1]['open_ms'] if bars else None,
                                error=None, retry_required=status != 'available')
    except Exception as exc:
        return [], dict(symbol=symbol, status='error', started_ms=started,
                        observed_ms=clock(), closed_bars=0, newest_open_ms=None,
                        error=type(exc).__name__, retry_required=True)


def collect(d, fetcher=fetch, clock=H.clock_ms):
    symbols = d['universe']
    # Refuse the entire unsupported declaration; never truncate its membership.
    if not 1 <= len(symbols) <= MAX_SYMBOLS or len(set(symbols)) != len(symbols):
        raise ValueError('declared_universe_capacity')
    started = clock()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda s: observe(s, fetcher, clock), symbols))
    now = clock()
    cfg = settings({'max_symbols': max(2,len(symbols))})
    receipts = [r for _,r in results]
    # Recheck freshness at scan completion (a pass may cross a bar boundary).
    anchor = (now//TF_MS['4h']-1)*TF_MS['4h']
    for r in receipts:
        if r['status']=='available' and r['newest_open_ms'] != anchor:
            r.update(status='stale',retry_required=True)
    return dict(kind='scan', schema_version=SCHEMA, scan_id='declared_'+uuid4().hex,
        as_of_ms=now, capture_settings=cfg, capture_ms=now-started,
        scope=dict(kind='declared_population', protocol=PROTOCOL,
                   declaration_version=P.D.declaration_version(d), candidate_count=len(symbols),
                   included_count=len(symbols), excluded_count=0, cap=MAX_SYMBOLS,
                   endpoint=ENDPOINT, availability_receipts=receipts,
                   trading_eligibility='not_assessed', prior_availability='unknown'),
        issues=[dict(symbol=r['symbol'],reason=r['status'],error=r['error']) for r in receipts if r['status']!='available'],
        input=dict(schema=INPUT_SCHEMA,timeframe='4h',decision_times=[now],
                   candles=[b for bars,_ in results for b in bars], participation=[],
                   membership=[dict(symbol=s,from_ms=now,to_ms=None,available_ms=now,
                                    source=PROTOCOL) for s in symbols]))


def publish(path, value):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(encode(value)); tmp.replace(path)


def run(config, directory, fetcher=fetch, clock=H.clock_ms):
    directory = Path(directory); directory.mkdir(parents=True,exist_ok=True)
    hp = directory/'attention_health.json'
    started = clock()
    ident = dict(schema=H.IDENTITY_SCHEMA, instance_id=uuid4().hex,seq=1)
    health = dict(health_schema=HEALTH_SCHEMA, instance_id=ident['instance_id'],
                  updated_ms=started, started_ms=started,status='collecting',
                  last_complete=None, protocol=PROTOCOL)
    publish(hp,health)  # A crash/timeout cannot leave an older success usable.
    try:
        d = declaration(config)
        event = collect(d,fetcher,clock); event['identity']=ident
        store = Store(directory/'attention.db',event['capture_settings'])
        try:
            store.write(event)
            proof = store.write(dict(kind='causes',scan_id=event['scan_id'],
                                     as_of_ms=clock(),identity=ident,items=[]))
            if not (proof['payload'] and proof['causes_complete'] and proof['completion_marker']
                    and proof['scan_identity']==ident and proof['cause_identities']==[ident]):
                raise ValueError('declared_incomplete_persistence')
        finally:
            store.close()
        health.update(status='ok',updated_ms=clock(),last_complete=dict(scan_id=event['scan_id'],seq=1),
                      declaration_version=P.D.declaration_version(d))
        publish(hp,health)
        return health
    except Exception as exc:
        health.update(status='error',updated_ms=clock(),error=type(exc).__name__)
        publish(hp,health)
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true'); parser.add_argument('--fetch')
    args=parser.parse_args()
    if args.fetch:
        from urllib.request import urlopen
        from urllib.parse import urlencode
        if not re.fullmatch(r'[A-Z0-9]+/USDT',args.fetch): return 1
        url=ENDPOINT+'?'+urlencode(dict(symbol=args.fetch.replace('/',''),interval='4h',limit=BAR_LIMIT))
        with urlopen(url,timeout=4) as response:
            raw=response.read(65537)
        if len(raw)>65536: return 1
        print(encode(json.loads(raw))); return 0
    from trader.core.config import ROOT
    data=ROOT/'data'
    if not args.once or not (data/'declared_population.enabled').exists(): return 0
    with (data/'declared_population.lock').open('a') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: return 0
        try: result=run(P.configured(data),data/'declared-population')
        except Exception as exc: result=dict(status='error',error=type(exc).__name__)
        print(encode(result))
        return int(result['status']!='ok')


if __name__=='__main__':
    raise SystemExit(main())
