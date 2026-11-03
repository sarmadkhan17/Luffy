"""Stdlib-only public candle fetcher; parent enforces a six-second wall deadline.

Executed as a script so neither trader nor the Attention/NumPy graph is imported.
The disposable child keeps DNS and slow response bodies within the parent deadline.
"""
import json
import re
import sys
from urllib.parse import urlencode
from urllib.request import urlopen

ENDPOINT = 'https://fapi.binance.com/fapi/v1/klines'


def fetch(symbol):
    if not re.fullmatch(r'[A-Z0-9]+/USDT', symbol):
        raise ValueError('invalid_symbol')
    url = ENDPOINT + '?' + urlencode(dict(symbol=symbol.replace('/', ''), interval='4h', limit=28))
    with urlopen(url, timeout=4) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise ValueError('public_response_too_large')
    return json.loads(raw)


if __name__ == '__main__':
    print(json.dumps(fetch(sys.argv[1]), separators=(',', ':'), allow_nan=False))
