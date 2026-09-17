"""Read-only demo conversion evidence; a proposed join never proves attribution."""
import argparse
import json
from pathlib import Path

from trader.engine import conversion_evidence as C


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--whole-trade', type=Path)
    p.add_argument('--request', action='append', default=[], metavar='KIND:CASHFLOW_ID:CONVERT_ORDER_ID')
    p.add_argument('--output', type=Path)
    p.add_argument('--replay', type=Path)
    args = p.parse_args()
    if args.replay:
        if args.whole_trade or args.request or args.output:
            p.error('--replay cannot be combined with capture arguments')
        artifact = json.loads(args.replay.read_text())
    else:
        if not args.whole_trade or not args.request or not args.output:
            p.error('--whole-trade, --request and --output required')
        if args.output.exists():
            p.error('output exists; retain it and use a new filename')
        from trader.core.config import Env
        if Env.get('BINANCE_DEMO', 'true').lower() not in ('1', 'true', 'yes'):
            p.error('demo only')
        items = []
        for request in args.request:
            parts = request.split(':')
            if len(parts) != 3:
                p.error('request must be KIND:CASHFLOW_ID:CONVERT_ORDER_ID')
            items.append(dict(zip(('kind', 'cashflow_id', 'order_id'), parts)))
        whole = json.loads(args.whole_trade.read_text())
        C.requests(whole, items)
        from trader.data.feed import make_exchange
        ex = make_exchange('futures', demo=True)
        ex.timeout = 5000
        artifact = C.capture(whole, ex, items)
        C.replay(artifact)
        with args.output.open('x') as out:
            json.dump(artifact, out, sort_keys=True, indent=2, allow_nan=False)
    print(json.dumps(C.replay(artifact), sort_keys=True))


if __name__ == '__main__':
    main()
