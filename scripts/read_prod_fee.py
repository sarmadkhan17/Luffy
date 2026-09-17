"""Read Binance PRODUCTION futures commission rates for the declared universe.

The demo key cannot answer `commissionRate` (-2015), and demo rates are not
production rates: measured 2026-09-13, production charges taker 0.0500% /
maker 0.0200% on all 16 declared symbols where the demo charged 0.0400% taker
on 14 of them. `config.yaml`'s `taker_fee_pct` is the demo figure; this
script is how the production one is re-measured before `BINANCE_DEMO=false`.

Credentials come ONLY from BINANCE_PROD_READONLY_KEY / _SECRET (in the
environment or `.env`) — names the kernel never reads. Create the key with
"Enable Reading" alone and restrict it to this host's IP. The script issues
GET requests only and never prints the credentials.

    ./venv/bin/python -m scripts.read_prod_fee [--out FILE]
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEY_ENV, SECRET_ENV = "BINANCE_PROD_READONLY_KEY", "BINANCE_PROD_READONLY_SECRET"
SPEC = ROOT / "data/authored_specs/donchian_breakout_trail.json"
RESTRICTION_FIELDS = ("ipRestrict", "enableReading", "enableFutures",
                      "enableSpotAndMarginTrading", "enableWithdrawals",
                      "enableInternalTransfer", "enableMargin",
                      "permitsUniversalTransfer")


def credentials() -> tuple[str, str]:
    """The read-only production key, or SystemExit naming what is missing."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    key, secret = os.environ.get(KEY_ENV), os.environ.get(SECRET_ENV)
    missing = [n for n, v in ((KEY_ENV, key), (SECRET_ENV, secret)) if not v]
    if missing:
        raise SystemExit(f"read_prod_fee: missing {', '.join(missing)} — create a "
                         "reading-only, IP-restricted production key")
    return key, secret


def declared_symbols() -> list[str]:
    spec = json.loads(SPEC.read_text())
    return list(spec["universe"]["include"])


def _get(base: str, path: str, key: str, secret: str, params=None):
    import requests
    p = dict(params or {}, timestamp=int(time.time() * 1000), recvWindow=10000)
    q = "&".join(f"{a}={b}" for a, b in p.items())
    sig = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
    r = requests.get(f"{base}{path}?{q}&signature={sig}",
                     headers={"X-MBX-APIKEY": key}, timeout=15)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:200]


def measure(key: str, secret: str, symbols: list[str]) -> dict:
    code, r = _get("https://api.binance.com", "/sapi/v1/account/apiRestrictions",
                   key, secret)
    restrictions = ({f: r.get(f) for f in RESTRICTION_FIELDS}
                    if code == 200 and isinstance(r, dict) else {"error": [code, r]})
    rates = {}
    for sym in symbols:
        code, r = _get("https://fapi.binance.com", "/fapi/v1/commissionRate",
                       key, secret, {"symbol": sym.replace("/", "")})
        if code == 200 and isinstance(r, dict):
            rates[sym] = {"maker": float(r["makerCommissionRate"]),
                          "taker": float(r["takerCommissionRate"])}
        else:
            rates[sym] = {"error": [code, r]}
    return {"measured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": "GET fapi.binance.com/fapi/v1/commissionRate, production key",
            "key_restrictions": restrictions, "rates": rates}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", help="also write the JSON record to this file")
    args = ap.parse_args(argv)
    key, secret = credentials()
    record = measure(key, secret, declared_symbols())
    text = json.dumps(record, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")
    if record["key_restrictions"].get("enableFutures") or \
            record["key_restrictions"].get("enableWithdrawals"):
        print("WARNING: this key can trade or withdraw — it should be reading-only",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
