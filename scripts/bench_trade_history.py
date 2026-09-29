"""Measure keyset trade-history pages (owner frontend) on a large temporary journal.

Offline: builds temporary journals only; never opens data/luffy.db.

    ./venv/bin/python scripts/bench_trade_history.py out.json

Reports per case: page size, latency (median / p95 / max over untraced
repeats), peak traced Python memory of one separate call (tracemalloc; SQLite's
native allocations are not traced), response size (the endpoint's JSON body for
the page), the SQLite plans — including the history-membership check, whose
cost grows with the traversal's depth — a complete traversal's wall time, and —
for contrast — the legacy offset read at the same depth and a full-history
serialization.
"""
from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.core.journal import Journal                      # noqa: E402
from trader.dashboard import trade_history as th             # noqa: E402

SIZES = (10_000, 100_000)
REPEATS = 40
T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def build(root: Path, n: int) -> Journal:
    j = Journal(root / f"j{n}.db")
    rows = []
    for i in range(n):
        opened = T0 + timedelta(minutes=7 * i + (i % 3))
        is_open = i % 100 == 0                                 # ~1 % open
        rows.append((f"t_{i:07d}", f"d_{i:07d}", f"S{i % 40}/USDT", "long", 1.0, 100.0,
                     None if is_open else 101.0, 100.0, 3, 95.0, 110.0, f"algo-{i}",
                     1.2, 0, "spec:donchian", "Donchian breakout", "futures", "live",
                     opened.isoformat(),
                     None if is_open else (opened + timedelta(hours=5)).isoformat(),
                     0.0 if is_open else 1.25, None if is_open else "trail",
                     "open" if is_open else "closed", 0.8, -0.4,
                     json.dumps({"frame": "5m", "coverage": 0.99, "bars": list(range(40))})))
    with j._tx() as c:
        c.executemany(
            "INSERT INTO trades (id,decision_id,symbol,side,amount,entry_price,exit_price,"
            "notional_usdt,leverage,stop_loss,take_profit,sl_order_id,initial_risk,tp1_done,"
            "strategy_id,strategy_name,market_type,exec_mode,opened_at,closed_at,realized_pnl,"
            "close_reason,status,mfe_r,mae_r,excursion_json) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    j._conn().execute("ANALYZE")
    return j


def measure(fn):
    times, peaks, out = [], [], None
    for _ in range(REPEATS):
        t = time.perf_counter()
        out = fn()
        times.append((time.perf_counter() - t) * 1000)
    tracemalloc.start()                              # traced separately: tracing slows calls
    fn()
    peaks.append(tracemalloc.get_traced_memory()[1])
    tracemalloc.stop()
    times.sort()
    return out, {"median_ms": round(statistics.median(times), 3),
                 "p95_ms": round(times[int(len(times) * 0.95) - 1], 3),
                 "max_ms": round(times[-1], 3),
                 "peak_mem_kib": round(max(peaks) / 1024, 1)}


def body_bytes(page: dict) -> int:
    return len(json.dumps({"generated_at": "2026-09-29T00:00:00+00:00", **page,
                           "consistency": "x" * 70, "source": "x" * 72}).encode())


def cursor_at(j, status, depth):
    """The cursor an unchanged traversal from the newest row holds after `depth` rows:
    the endpoint's own anchor and membership digest (``walked_cursor`` checks equality
    with a real walk)."""
    where = "" if status == "all" else f"WHERE status='{status}'"
    q = f"SELECT opened_at, id FROM trades {where} ORDER BY opened_at DESC, id DESC LIMIT 1 OFFSET ?"
    anchor = tuple(j.query(q, (0,))[0].values())
    row = tuple(j.query(q, (depth - 1,))[0].values())
    filt, params = ("1", []) if status == "all" else ("status = ?", [status])
    conn = th.ro_connect(j.db_path)
    try:
        _, digest = th._membership(conn, filt, params, anchor, row, anchor)
    finally:
        conn.close()
    return th.encode_cursor(status, *row, anchor, digest, False)


def walked_cursor(j, status, pages, limit):
    cur = None
    for _ in range(pages):
        cur = th.trade_page(j.db_path, status=status, limit=limit, cursor=cur)["page"]["next_cursor"]
    return cur


def traverse(j, status, limit=th.DEFAULT_LIMIT) -> dict:
    """Wall time of one complete newest-first traversal."""
    t, cur, pages = time.perf_counter(), None, 0
    while True:
        p = th.trade_page(j.db_path, status=status, limit=limit, cursor=cur)
        pages += 1
        if not p["page"]["has_more"]:
            break
        cur = p["page"]["next_cursor"]
    return {"status": status, "limit": limit, "pages": pages,
            "seconds": round(time.perf_counter() - t, 2)}


def plan(j, sql, params=()):
    return [r[3] for r in j._conn().execute("EXPLAIN QUERY PLAN " + sql, params)]


def run() -> dict:
    out = {"repeats": REPEATS, "page_size": th.DEFAULT_LIMIT, "max_limit": th.MAX_LIMIT,
           "sqlite": __import__("sqlite3").sqlite_version, "cases": []}
    with tempfile.TemporaryDirectory() as tmp:
        for n in SIZES:
            j = build(Path(tmp), n)
            opens = j.query("SELECT COUNT(*) n FROM trades WHERE status='open'")[0]["n"]
            for status in ("all", "closed"):          # the fabricated cursor is the real one
                assert cursor_at(j, status, 4 * th.DEFAULT_LIMIT) == walked_cursor(
                    j, status, 4, th.DEFAULT_LIMIT), status
            for status, depth in (("all", 0), ("all", n // 2), ("all", n - 50),
                                  ("open", 0), ("open", max(opens - 10, 1)),
                                  ("closed", 0), ("closed", n - opens - 50)):
                cur = cursor_at(j, status, depth) if depth else None
                page, m = measure(lambda: th.trade_page(j.db_path, status=status, cursor=cur))
                out["cases"].append({"trades": n, "status": status, "depth": depth,
                                     "rows": len(page["trades"]), **m,
                                     "response_bytes": body_bytes(page),
                                     "preceding": page["page"]["preceding"]})
            deep = n - 50
            _, legacy = measure(lambda: j.query(
                "SELECT * FROM trades ORDER BY opened_at DESC LIMIT 50 OFFSET ?", (deep,)))
            _, full = measure(lambda: json.dumps(j.query("SELECT * FROM trades")))
            out.setdefault("contrast", []).append({
                "trades": n, "legacy_offset_deep_page": legacy,
                "full_history_serialization": full,
                "full_history_bytes": len(json.dumps(j.query("SELECT * FROM trades")).encode())})
            filt = "status = ?"
            out.setdefault("plans", {})[str(n)] = {
                "page_all": plan(j, "SELECT id FROM trades WHERE 1 AND (opened_at, id) < (?, ?) "
                                    "ORDER BY opened_at DESC, id DESC LIMIT 51", ("z", "z")),
                "page_status": plan(j, f"SELECT id FROM trades WHERE {filt} AND (opened_at, id) "
                                       "< (?, ?) ORDER BY opened_at DESC, id DESC LIMIT 51",
                                    ("open", "z", "z")),
                "preceding_status": plan(j, f"SELECT COUNT(*) FROM trades WHERE {filt} AND "
                                            "(opened_at, id) >= (?, ?)", ("open", "a", "a")),
                "total_all": plan(j, "SELECT COUNT(*) FROM trades WHERE 1"),
                "membership_status": plan(
                    j, "SELECT quote(opened_at) || ',' || quote(id) || ';', (opened_at, id) "
                       f">= (?, ?) FROM trades WHERE {filt} AND (opened_at, id) <= (?, ?) AND "
                       "(opened_at, id) >= (?, ?) ORDER BY opened_at DESC, id DESC",
                    ("m", "m", "closed", "z", "z", "a", "a")),
            }
            if n <= 10_000:                           # quadratic in history; 100k is minutes
                out.setdefault("traversals", []).append({"trades": n, **traverse(j, "all")})
    return out


if __name__ == "__main__":
    result = run()
    Path(sys.argv[1]).write_text(json.dumps(result, indent=1))
    for c in result["cases"]:
        print(f"{c['trades']:>7} {c['status']:<6} depth={c['depth']:<6} rows={c['rows']:<3} "
              f"median={c['median_ms']}ms p95={c['p95_ms']}ms mem={c['peak_mem_kib']}KiB "
              f"bytes={c['response_bytes']}")
    for t in result.get("traversals", []):
        print(t["trades"], "full traversal:", t["pages"], "pages in", t["seconds"], "s")
    for c in result["contrast"]:
        print(c["trades"], "legacy deep:", c["legacy_offset_deep_page"]["median_ms"], "ms;",
              "full serialization:", c["full_history_serialization"]["median_ms"], "ms",
              c["full_history_bytes"], "bytes")
