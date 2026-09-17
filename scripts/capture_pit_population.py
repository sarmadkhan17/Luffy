"""Capture immutable prospective exports and the existing typed PIT dataset."""
import argparse
import json
from pathlib import Path
import time
from trader.observability.population import capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = capture(json.loads(args.config.read_text()), int(time.time()*1000))
    with args.output.open('x') as out:
        json.dump(result, out, sort_keys=True, indent=2, allow_nan=False)
    print(json.dumps({'output': str(args.output), 'sha256': result['sha256'],
                      'registrations': result['coverage']['registrations'],
                      'cohort_rows': len(result['cohort']['rows']),
                      'window_status': result['cohort']['window_status'],
                      'receipt_reconciliation': result['cohort']['receipt_reconciliation'],
                      'search_ready': False}))


if __name__ == '__main__':
    main()
