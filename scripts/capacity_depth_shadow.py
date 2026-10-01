"""Public order-book depth shadow collector (LUFFY-CAPACITY-EVIDENCE-CAPTURE-R1).

Every --interval-s, for each symbol of a bounded set frozen at start, one
public GET /fapi/v1/depth?symbol=S&limit=N (no credentials), validated by
`trader.observability.depth_evidence.observe` and stored in
<shadow-dir>/depth.db. Rejections and fetch failures are telemetry rows only.

The symbol set is --symbols, or the membership of the production Kernel's
latest persisted Attention scan read READ-ONLY from --prod-attention, capped
at --max-symbols and written to <shadow-dir>/manifest.json before the first
request. Writes only under --shadow-dir. Never opens a production DB for
writing and never touches the Kernel, orders, Risk, strategy, allocation or
control state. Computes no capacity, participation rate or impact.

Stops on --max-hours, --max-cycles, --max-db-bytes, SIGTERM/SIGINT, or when
<shadow-dir>/STOP exists. Writes <shadow-dir>/storage.json every cycle.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trader.data.registry_provider import (FetchError, VenueTarget,   # noqa: E402
                                           urllib_fetch)
from trader.observability import depth_evidence as D                  # noqa: E402

TIMEOUT_S = 10.0


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def attention_universe(path):
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        row = db.execute("SELECT scan_id, as_of_ms, payload FROM scans WHERE payload IS "
                         "NOT NULL ORDER BY as_of_ms DESC LIMIT 1").fetchone()
    if row is None:
        return None, []
    members = [m["symbol"] for m in json.loads(row[2])["membership"]]
    return {"scan_id": row[0], "as_of_ms": row[1]}, members


def venue_symbol(sym: str) -> str:
    return sym.split(":")[0].replace("/", "")


def collect_one(db, target, symbol, depth, max_age_ms, fetch=None) -> str:
    fetch = fetch or urllib_fetch
    url = D.request_url(target, symbol, depth)
    start = now_ms()
    try:
        resp = fetch(url, timeout_s=TIMEOUT_S, max_bytes=D.MAX_BODY_BYTES)
    except FetchError as e:
        D.reject(db, symbol, "fetch:" + e.reason, request_start_ms=start,
                 http_status=e.status, body=e.body)
        return "fetch:" + e.reason
    except Exception as e:                       # noqa: BLE001 - telemetry only
        D.reject(db, symbol, "fetch:" + type(e).__name__, request_start_ms=start)
        return "fetch:" + type(e).__name__
    received = now_ms()
    if resp.status != 200:
        D.reject(db, symbol, "http_status", request_start_ms=start, received_ms=received,
                 http_status=resp.status, body=resp.body)
        return "http_status"
    try:
        obs = D.observe(resp.body, target=target, symbol=symbol, limit=depth,
                        request_start_ms=start, received_ms=received,
                        max_age_ms=max_age_ms)
    except D.DepthRejected as e:
        D.reject(db, symbol, e.reason, request_start_ms=start, received_ms=received,
                 http_status=resp.status, body=resp.body)
        return e.reason
    return D.store(db, obs)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shadow-dir", required=True)
    ap.add_argument("--symbols", nargs="*", default=None,
                    help="venue symbols (BTCUSDT) or unified (BTC/USDT)")
    ap.add_argument("--prod-attention", default=None,
                    help="production data/attention.db, opened read-only")
    ap.add_argument("--max-symbols", type=int, default=16)
    ap.add_argument("--depth", type=int, default=20, choices=D.ALLOWED_LIMITS)
    ap.add_argument("--interval-s", type=float, default=60.0)
    ap.add_argument("--max-age-ms", type=int, default=5_000,
                    help="collector validity bound: |receipt - venue event time|")
    ap.add_argument("--max-hours", type=float, default=72.0)
    ap.add_argument("--max-cycles", type=int, default=10_000)
    ap.add_argument("--max-db-bytes", type=int, default=2 * 1024 ** 3)
    ap.add_argument("--environment", choices=("production", "demo"), default="production")
    a = ap.parse_args(argv)
    if not (1 <= a.max_symbols <= 32 and a.interval_s >= 10 and a.max_hours > 0):
        ap.error("bounds: 1..32 symbols, interval >= 10 s, max-hours > 0")

    shadow = Path(a.shadow_dir)
    shadow.mkdir(parents=True, exist_ok=True)
    target = VenueTarget.production() if a.environment == "production" else VenueTarget.demo()
    scan = None
    if a.symbols:
        symbols = [venue_symbol(s) for s in a.symbols]
    elif a.prod_attention:
        scan, members = attention_universe(a.prod_attention)
        symbols = [venue_symbol(s) for s in members]
    else:
        ap.error("--symbols or --prod-attention is required")
    symbols = list(dict.fromkeys(symbols))[:a.max_symbols]
    if not symbols:
        print("no symbols", file=sys.stderr)
        return 2
    started = now_ms()
    manifest = {"package": "LUFFY-CAPACITY-EVIDENCE-CAPTURE-R1",
                "collector": "scripts/capacity_depth_shadow.py", "started_ms": started,
                "pid": os.getpid(), "environment": target.environment,
                "endpoint": target.base_url + D.DEPTH_PATH, "credentials": "none",
                "symbols": symbols, "symbol_source": "explicit" if a.symbols else
                {"prod_attention_latest_scan": scan}, "depth": a.depth,
                "interval_s": a.interval_s, "max_age_ms": a.max_age_ms,
                "max_hours": a.max_hours, "max_cycles": a.max_cycles,
                "max_db_bytes": a.max_db_bytes, "stop_file": str(shadow / "STOP")}
    (shadow / "manifest.json").write_text(json.dumps(manifest, indent=2))

    stop = {"sig": None}
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda n, _f: stop.__setitem__("sig", n))
    db_path = shadow / "depth.db"
    log = open(shadow / "collector_log.jsonl", "a")
    why = None
    with closing(D.connect(db_path)) as db:
        cycle = 0
        while True:
            if stop["sig"] is not None:
                why = f"signal_{stop['sig']}"
            elif (shadow / "STOP").exists():
                why = "stop_file"
            elif cycle >= a.max_cycles:
                why = "max_cycles"
            elif now_ms() - started >= a.max_hours * 3_600_000:
                why = "max_hours"
            elif db_path.stat().st_size >= a.max_db_bytes:
                why = "max_db_bytes"
            if why:
                break
            t0 = time.monotonic()
            outcomes = {}
            for sym in symbols:
                try:
                    r = collect_one(db, target, sym, a.depth, a.max_age_ms)
                except Exception as e:           # noqa: BLE001 - never crash the loop
                    r = "collector_error:" + type(e).__name__
                outcomes[r] = outcomes.get(r, 0) + 1
            span = db.execute("SELECT MIN(received_ms), MAX(received_ms) FROM "
                              "depth_observations").fetchone()
            st = D.storage(db, db_path.stat().st_size, span[0], span[1])
            (shadow / "storage.json").write_text(json.dumps(
                {**st, "measured_ms": now_ms(), "cycles": cycle + 1}, indent=2))
            log.write(json.dumps({"cycle": cycle, "ms": now_ms(), "outcomes": outcomes,
                                  "elapsed_s": round(time.monotonic() - t0, 3)}) + "\n")
            log.flush()
            cycle += 1
            deadline = t0 + a.interval_s
            while time.monotonic() < deadline and stop["sig"] is None \
                    and not (shadow / "STOP").exists():
                time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    log.write(json.dumps({"stopped": why, "ms": now_ms()}) + "\n")
    log.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
