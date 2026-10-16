"""Bounded synthetic correctness/work probe, never operational coverage."""
import json
from pathlib import Path
import tempfile

from tests.test_da05_final_readiness import Venue
from trader.data.feed import DataFeed


def measure(root):
    out = {'evidence_kind':'deterministic synthetic; no provider calls', 'expansions':[]}
    with tempfile.TemporaryDirectory(prefix='da05-measure-',dir=root) as tmp:
        for initial,target,available in ((154,400,1600),(300,400,1600),(399,400,1600),(154,1000,1600),(154,400,250)):
            v = Venue(available)
            path = Path(tmp)/f'{initial}-{target}-{available}.db'
            f = DataFeed(exchange=v, db_path=path, clock_ms=lambda:v.now)
            try:
                f.fetch_ohlcv('S00/USDT','4h',initial,min_bars=26)
                result = f.fetch_ohlcv('S00/USDT','4h',target,min_bars=26)
                row = dict(initial=initial,target=target,source_available=available,
                    returned=len(result),floor=f._floor('S00/USDT','4h'),
                    requests=len(v.calls),max_request_limit=max(c[3] for c in v.calls),
                    candle_rows=f.db.execute('SELECT count(*) FROM candles').fetchone()[0],
                    revision_rows=f.db.execute('SELECT count(*) FROM market_revisions').fetchone()[0])
            finally: f.db.close()
            restarted = DataFeed(exchange=v, db_path=path, clock_ms=lambda:v.now)
            try:
                again = restarted.fetch_ohlcv('S00/USDT','4h',target,min_bars=26)
                row.update(after_restart=len(again),restart_additional_requests=len(v.calls)-row['requests'])
            finally: restarted.db.close()
            out['expansions'].append(row)
        v = Venue(20)
        f = DataFeed(exchange=v,db_path=Path(tmp)/'short.db',clock_ms=lambda:v.now)
        try:
            first = f.fetch_ohlcv('S00/USDT','4h',400,min_bars=26)
            v.unavailable.add('4h')
            second = f.fetch_ohlcv('S00/USDT','4h',400,min_bars=30)
            out['short_fallback'] = dict(first_refused=first is None,
                failed_refresh_refused=second is None,
                returned_after_failure=0 if second is None else len(second),
                retained_candle_rows=f.db.execute('SELECT count(*) FROM candles').fetchone()[0])
        finally: f.db.close()
    return out


if __name__ == '__main__':
    import sys
    Path(sys.argv[2]).write_text(json.dumps(measure(sys.argv[1]),indent=2)+'\n')
