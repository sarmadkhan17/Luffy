"""Forward shadow run of the intelligence spine on real data (SPINE-SHADOW-ACTIVATION-R1).

Each cycle:
  1. reads the production Kernel's current Attention scan universe (the
     membership of its latest persisted scan) READ-ONLY from data/attention.db;
  2. reads those symbols' real closed 4h bars READ-ONLY from data/candles.db;
  3. captures a scan through the real Collector (producer -> queue -> worker
     child -> Store.write) with attention.world_model on, into SHADOW_DIR;
  4. runs the investigation consumer step and the bounded research pass on the
     shadow ledger.

Writes only under --shadow-dir. Never opens a production DB for writing, never
touches the Kernel, orders, Risk, strategy, allocation or control state, and
makes no network or LLM call. Candle availability is the shadow's own capture
time. Stops on --max-hours, --max-cycles, or when <shadow-dir>/STOP exists.
"""
from __future__ import annotations

import argparse
import json
import signal
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from trader.cognition import investigation as I                      # noqa: E402
from trader.observability import investigation as C                  # noqa: E402
from trader.observability import investigation_research as R         # noqa: E402
from trader.observability.collector import Collector                 # noqa: E402

BARS = 170          # >= 151 correlation closes + margin; capture keeps what it needs


def ro(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)


def universe(prod_attention):
    with closing(ro(prod_attention)) as db:
        row = db.execute("SELECT scan_id,as_of_ms,payload FROM scans WHERE payload IS NOT NULL "
                         "ORDER BY as_of_ms DESC LIMIT 1").fetchone()
    if row is None:
        return None, None
    return row[0], [m["symbol"] for m in json.loads(row[2])["membership"]]


def frames(candles_db, symbols):
    out = {}
    with closing(ro(candles_db)) as db:
        for sym in symbols:
            rows = db.execute("SELECT ts,open,high,low,close,volume FROM candles WHERE symbol=? "
                              "AND tf='4h' ORDER BY ts DESC LIMIT ?", (sym, BARS)).fetchall()
            if rows:
                df = pd.DataFrame(rows[::-1], columns=["ts", "open", "high", "low", "close", "volume"])
                df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
                out[sym] = {"4h": df}
    return out


def wait_processed(collector, scan_id, timeout_s=60):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        h = collector.health()
        if (h.get("last_complete") or {}).get("scan_id") == scan_id and h["queue_depth"] == 0:
            return True
        time.sleep(0.5)
    return False


def bounded(fn, seconds):
    def alarm(*_):
        raise TimeoutError("deadline")
    old = signal.signal(signal.SIGALRM, alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prod-data", required=True)
    ap.add_argument("--shadow-dir", required=True)
    ap.add_argument("--interval-s", type=int, default=900)
    ap.add_argument("--max-hours", type=float, default=72)
    ap.add_argument("--max-cycles", type=int, default=400)
    ap.add_argument("--research-max-cases", type=int, default=8)
    a = ap.parse_args()
    prod, shadow = Path(a.prod_data), Path(a.shadow_dir)
    shadow.mkdir(parents=True, exist_ok=True)
    log = (shadow / "shadow_log.jsonl").open("a")
    collector = Collector(shadow, {"timeframe": "4h", "world_model": True, "max_symbols": 16})
    stop_at = time.time() + a.max_hours * 3600

    def emit(rec):
        log.write(json.dumps(rec, default=str, sort_keys=True) + "\n")
        log.flush()

    emit({"event": "start", "ts_ms": int(time.time() * 1000), "args": vars(a),
          "commit": (ROOT / ".git").exists() and None})
    for cycle in range(a.max_cycles):
        if time.time() >= stop_at or (shadow / "STOP").exists():
            break
        rec = {"event": "cycle", "cycle": cycle, "ts_ms": int(time.time() * 1000)}
        try:
            prod_scan, members = universe(prod / "attention.db")
            rec["prod_scan_id"], rec["members"] = prod_scan, members
            if members:
                data = frames(prod / "candles.db", members)
                rec["symbols_with_bars"] = len(data)
                started = time.perf_counter()
                sid = collector.begin(data, members)
                collector.causes(sid, [])
                rec["scan_id"] = sid
                rec["scan_processed"] = bool(sid) and wait_processed(collector, sid)
                rec["capture_to_persist_s"] = round(time.perf_counter() - started, 3)
                now = int(time.time() * 1000)
                step = bounded(lambda: C.step(shadow / "attention.db", shadow / "investigation.db", now),
                               I.MAX_RUNTIME_MS / 1000)
                rec["step"] = {k: step.get(k) for k in ("status", "reason", "registered", "updated",
                                                        "skipped", "intelligence_trace",
                                                        "opportunity_context", "active", "cases",
                                                        "elapsed_ms")}
                started = time.perf_counter()
                rec["research"] = bounded(lambda: R.research_pass(
                    shadow / "investigation.db", recorded_at_ms=int(time.time() * 1000),
                    max_cases=a.research_max_cases), I.MAX_RUNTIME_MS / 1000)
                rec["research_s"] = round(time.perf_counter() - started, 3)
            else:
                rec["skipped"] = "no_production_scan_universe"
            rec["sizes"] = {p.name: p.stat().st_size for p in shadow.glob("*.db")}
        except Exception as exc:   # record and keep going; the shadow never affects trading
            rec["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        emit(rec)
        for _ in range(a.interval_s):
            if time.time() >= stop_at or (shadow / "STOP").exists():
                break
            time.sleep(1)
    collector.close()
    emit({"event": "stop", "ts_ms": int(time.time() * 1000)})


if __name__ == "__main__":
    main()
