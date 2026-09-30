"""Measure the read-only protection snapshot (R2): duration, requests and
request weight per check, memory growth, journal-write and trade-loop impact.
Offline only: simulated reads, a temporary journal. No venue call.

    ./venv/bin/python scripts/bench_protection_snapshot.py out.json

Request weights are Binance USD-M's documented weights (not measured here):
positionRisk 5, openAlgoOrders without symbol 40, openOrders with symbol 1.
"""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import threading
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal                      # noqa: E402
from trader.core.types import Position, Side                 # noqa: E402
from trader.engine import protection_snapshot as ps          # noqa: E402
from trader.engine.venue_reads import TickPrecision          # noqa: E402

SYMBOLS = ["SUI", "LINK", "ZEC", "SOL", "UNI", "AVAX", "NEAR", "TAO"]
WEIGHT = {"positions": 5, "algo_orders": 40, "open_orders": 1}


class SimReads:
    def __init__(self, n=len(SYMBOLS), latency_s=0.0):
        self.latency_s = latency_s
        self.calls: list[str] = []
        self.hang = None
        bases = SYMBOLS[:n]
        self.positions_ = [{"symbol": f"{b}/USDT:USDT", "contracts": 10.0, "side": "long",
                            "entryPrice": 10.0, "updateTime": "1790000000000"} for b in bases]
        self.stops = [{"algoId": f"id-{b}", "symbol": f"{b}USDT", "side": "SELL",
                       "reduceOnly": "true", "orderType": "STOP_MARKET", "quantity": "10",
                       "triggerPrice": "9.5"} for b in bases]
        self.precision = TickPrecision({f"{b}USDT": "0.01" for b in SYMBOLS})

    def _wait(self, name):
        self.calls.append(name)
        if self.hang is not None:
            self.hang.wait(30)
        if self.latency_s:
            time.sleep(self.latency_s)

    def positions(self):
        self._wait("positions")
        return [dict(p) for p in self.positions_]

    def algo_orders(self):
        self._wait("algo_orders")
        return [dict(s) for s in self.stops]

    def open_orders(self, symbol):
        self._wait("open_orders")
        return []

    def rate_state(self):
        return {"used_weight_1m": None}


def journal_with_book(root: Path, n=len(SYMBOLS)) -> Journal:
    j = Journal(root / "j.db")
    for b in SYMBOLS[:n]:
        j.add_trade(Position(id=f"t-{b}", symbol=f"{b}/USDT", side=Side.LONG, amount=10.0,
                             entry_price=10.0, notional_usdt=100.0, leverage=5,
                             stop_loss=9.5, sl_order_id=f"id-{b}", market_type="futures"))
    return j


def observer(j, reads):
    return ps.Observer(reads, ps.JournalReads(j.db_path), lambda: False)


def one(j, reads):
    obs = ps.observe(observer(j, reads), budget=ps.Budget(time.monotonic() + 15))
    return ps.evaluate(obs, reads.precision, boot=1, seq=1)


def loop_latency(stop: threading.Event, samples: list[float]) -> None:
    """A stand-in trade loop: fixed CPU work, measured per iteration."""
    while not stop.is_set():
        t0 = time.perf_counter()
        x = 0
        for i in range(20000):
            x += i * i
        samples.append((time.perf_counter() - t0) * 1000)


def main() -> None:
    out: dict = {"generated_at": ps._iso(), "positions": len(SYMBOLS),
                 "note": "offline simulated reads + temporary journal; not production; "
                         "weights are Binance documented values, not measured"}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # 1. requests and weight per check, by open-position count
        table = []
        for n in (0, 1, 5, 8):
            sub = root / f"n{n}"
            sub.mkdir()
            j = journal_with_book(sub, n)
            r = SimReads(n)
            s = one(j, r)
            assert s["status"] == "VERIFIED", s["reasons"]
            table.append({"positions": n, "requests": len(r.calls),
                          "weight": sum(WEIGHT[c] for c in r.calls),
                          "names": r.calls})
        out["per_check"] = table

        j = journal_with_book(root)
        # 2. CPU-only duration (zero venue latency), including the journal reads
        durs = []
        for _ in range(200):
            t0 = time.perf_counter()
            assert one(j, SimReads())["status"] == "VERIFIED"
            durs.append((time.perf_counter() - t0) * 1000)
        out["cpu_only_duration_ms"] = {"median": round(statistics.median(durs), 3),
                                       "p95": round(sorted(durs)[int(len(durs) * .95)], 3),
                                       "max": round(max(durs), 3)}
        # 3. 60 ms per request (a typical REST round trip), serial
        durs = []
        for _ in range(5):
            t0 = time.perf_counter()
            one(j, SimReads(latency_s=0.06))
            durs.append((time.perf_counter() - t0) * 1000)
        out["latency_60ms_duration_ms"] = {"median": round(statistics.median(durs), 1),
                                           "max": round(max(durs), 1)}
        # 4. memory growth over repeated checks incl. publication
        mon = ps.ProtectionMonitor(observer(j, SimReads()), ps.SnapshotStore(j.db_path),
                                   timeout_s=5)
        for _ in range(50):
            mon.run_once()
        tracemalloc.start()
        base = tracemalloc.take_snapshot()
        for _ in range(1000):
            mon.run_once()
        after = tracemalloc.take_snapshot()
        out["memory_growth_bytes_over_1000_checks"] = sum(
            st.size_diff for st in after.compare_to(base, "filename"))
        tracemalloc.stop()
        out["stored_rows"] = j.query(f"SELECT COUNT(*) n FROM {ps.TABLE}")[0]["n"]
        out["stored_bytes"] = j.query(f"SELECT LENGTH(value) n FROM {ps.TABLE}")[0]["n"]

        # 5. journal writes (kernel cycle stand-in) while the monitor publishes
        # continuously — the monitor never holds Journal._write_lock
        stop = threading.Event()
        mon = ps.ProtectionMonitor(observer(j, SimReads()), ps.SnapshotStore(j.db_path),
                                   timeout_s=5)

        def spin():
            while not stop.is_set():
                mon.run_once()
        lat = []
        for label, run in (("baseline", False), ("monitor_publishing_continuously", True)):
            th = threading.Thread(target=spin) if run else None
            stop.clear()
            if th:
                th.start()
            samples = []
            for i in range(200):
                t0 = time.perf_counter()
                j.kv_set("bench_cycle", str(i))
                samples.append((time.perf_counter() - t0) * 1000)
            stop.set()
            if th:
                th.join(10)
            lat.append({label: {"median_ms": round(statistics.median(samples), 3),
                                "p99_ms": round(sorted(samples)[int(len(samples) * .99)], 3),
                                "max_ms": round(max(samples), 3)}})
        out["journal_write_latency"] = lat

        # 6. trade-loop CPU impact: baseline vs monitor looping far above cadence
        def measure(reads, seconds=3.0):
            samples: list[float] = []
            s = threading.Event()
            t = threading.Thread(target=loop_latency, args=(s, samples))
            m = mt = None
            if reads is not None:
                m = ps.ProtectionMonitor(observer(j, reads), ps.SnapshotStore(j.db_path),
                                         timeout_s=1.0, interval_s=0.001)
                mt = threading.Thread(target=m.loop, kwargs={"tick_s": 0.001})
                mt.start()
            t.start()
            time.sleep(seconds)
            s.set()
            t.join()
            if m is not None:
                m.stop()
                mt.join(5)
            return {"median_ms": round(statistics.median(samples), 3),
                    "p99_ms": round(sorted(samples)[int(len(samples) * .99)], 3),
                    "iterations": len(samples), "monitor_runs": m.stats["runs"] if m else 0}
        out["loop_baseline"] = measure(None)
        out["loop_with_monitor_continuous"] = measure(SimReads(latency_s=0.02))
        hung = SimReads()
        hung.hang = threading.Event()
        out["loop_with_hung_venue"] = measure(hung)
        out["hung_venue_requests_total"] = len(hung.calls)
        hung.hang.set()

        # 7. a hung venue: run_once returns at the timeout; the late result is rejected
        hv = SimReads()
        hv.hang = threading.Event()
        mon = ps.ProtectionMonitor(observer(j, hv), ps.SnapshotStore(j.db_path), timeout_s=2.0)
        t0 = time.monotonic()
        snap = mon.run_once()
        out["hung_run_once_s"] = round(time.monotonic() - t0, 3)
        out["hung_status"] = [snap["status"], snap["reasons"]]
        hv.hang.set()
        time.sleep(0.3)
        mon.collect_late()
        out["hung_late_discarded"] = mon.stats["late_discarded"]
    text = json.dumps(out, indent=2, default=str)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
