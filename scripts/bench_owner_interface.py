"""Isolated Owner Interface benchmark (temp journal + temp socket; no production state).

    ./venv/bin/python scripts/bench_owner_interface.py

Measures: idle gateway CPU, status and control latency over the real IPC
path, and the impact of repeated read-only requests on a synthetic cycle
that does journal reads/writes and numpy work like the kernel loop.
"""
from __future__ import annotations

import json
import logging
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal  # noqa: E402
from trader.engine.state import ControlStateMachine  # noqa: E402
from trader.engine.supervisor import OwnerRecoveryResult  # noqa: E402
from trader.owner.contract import OwnerRequest, new_request_id  # noqa: E402
from trader.owner.ipc import OwnerClient, OwnerIPCServer  # noqa: E402
from trader.owner.service import OwnerService  # noqa: E402


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def summary(ms):
    return {"n": len(ms), "p50_ms": round(pct(ms, 50), 3), "p95_ms": round(pct(ms, 95), 3),
            "p99_ms": round(pct(ms, 99), 3), "max_ms": round(max(ms), 3)}


def main() -> None:
    logging.disable(logging.WARNING)       # transition logs would swamp the output
    d = Path(tempfile.mkdtemp(prefix="oi-bench-"))
    j = Journal(d / "j.db")
    sm = ControlStateMachine(j)
    service = OwnerService(
        j, sm, resume=lambda ctx, allow_unhalt=False, **_: OwnerRecoveryResult("CONTAINED", "RECOVERY"),
        snapshot=lambda: {"open_trades": len(j.open_trades()), "heartbeat_age_s": 1.0})
    ipc_dir = d / "ipc"
    client = OwnerClient(ipc_dir, "dashboard")

    def R(op, rid=None, ref=None):     # dashboard channel: identity "session" → owner
        return OwnerRequest(rid or new_request_id("dashboard"), op, "dashboard", "session",
                            issued_at=time.time(), request_ref=ref)
    out = {}

    # idle overhead: process CPU with vs without the listener
    t0 = time.process_time(); time.sleep(3); base_cpu = time.process_time() - t0
    server = OwnerIPCServer(service, ipc_dir).start()
    t0 = time.process_time(); time.sleep(3); idle_cpu = time.process_time() - t0
    out["idle_cpu_s_per_3s"] = {"without_listener": round(base_cpu, 5),
                                "with_listener": round(idle_cpu, 5)}

    # status latency (IPC round trip)
    lat = []
    for _ in range(500):
        t = time.perf_counter()
        r = client.call(R("status"))
        lat.append((time.perf_counter() - t) * 1000)
        assert r.status == "ACCEPTED"
    out["status_latency"] = summary(lat)

    # control latency: alternating freeze/halt, each a fresh request (claim+audit+fence)
    lat = []
    for i in range(200):
        op = "freeze" if i % 2 else "halt"
        t = time.perf_counter()
        r = client.call(R(op, ref=f"b{i}"))
        lat.append((time.perf_counter() - t) * 1000)
        assert r.status in ("ACCEPTED", "ALREADY_SET"), r
    out["control_latency_freeze_halt"] = summary(lat)

    # replay latency: duplicate delivery answered from the record
    rid = new_request_id("dashboard")
    client.call(R("halt", rid, "dup"))
    lat = []
    for _ in range(200):
        t = time.perf_counter()
        client.call(R("halt", rid, "dup"))
        lat.append((time.perf_counter() - t) * 1000)
    out["duplicate_replay_latency"] = summary(lat)

    # cycle impact: synthetic cycle, quiet vs under repeated status reads
    def cycle():
        for i in range(40):
            j.kv_get("control_state")
        for i in range(5):
            j.log_control_event("bench_cycle", "bench", detail="x")
        a = np.random.default_rng(0).normal(size=(400, 400))
        (a @ a.T).sum()

    def run_cycles(n=40):
        ts = []
        for _ in range(n):
            t = time.perf_counter(); cycle(); ts.append((time.perf_counter() - t) * 1000)
        return ts

    run_cycles(5)
    quiet = run_cycles()
    stop = threading.Event()
    sent = [0]

    def hammer(rate_hz):
        gap = 0 if rate_hz is None else 1 / rate_hz
        while not stop.is_set():
            client.call(R("status"))
            sent[0] += 1
            if gap:
                time.sleep(gap)

    results = {"quiet_ms": round(statistics.median(quiet), 3)}
    for label, rate in (("status_20hz", 20), ("status_unthrottled", None)):
        stop.clear(); sent[0] = 0
        th = threading.Thread(target=hammer, args=(rate,), daemon=True)
        th.start()
        t = time.perf_counter()
        busy = run_cycles()
        el = time.perf_counter() - t
        stop.set(); th.join()
        results[label] = {"cycle_median_ms": round(statistics.median(busy), 3),
                          "ratio_vs_quiet": round(statistics.median(busy) / statistics.median(quiet), 3),
                          "status_requests_per_s": round(sent[0] / el, 1)}
    out["cycle_impact"] = results
    server.stop()
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
