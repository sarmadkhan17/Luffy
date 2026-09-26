"""Health telemetry under a real SQLite write lock (temporary WAL journal).

Another process holds a write transaction while the kernel's mechanism
cycle flushes health observations. The journal's own busy timeout is used
unchanged. The flush must fail within a bound that does not grow with the
number of specs, write no partial record, and leave the retirement exactly
as it is without the lock.
"""
import logging
import sqlite3
import subprocess
import sys
import time

from trader import kernel as kmod
from trader.strategy import health_observation as ho

from tests.test_health_observation_production import (_events, _health,
                                                      _install, _kernel,
                                                      _states)
from tests.test_strategy_health_observation import DOWN, RARE, _spec

HOLDER = r"""
import sqlite3, sys
c = sqlite3.connect(sys.argv[1], timeout=0, isolation_level=None)
c.execute("BEGIN IMMEDIATE")
c.execute("INSERT INTO brain_events(ts,kind,subject,detail) "
          "VALUES ('t','lock_holder','x','{}')")
print("LOCKED", flush=True)
sys.stdin.read()
c.execute("ROLLBACK")
c.close()
"""

#: sqlite3.connect(timeout=30) in Journal._conn — read, not changed
JOURNAL_BUSY_TIMEOUT_S = 30


def _specs():
    return [_spec("r1"), _spec("q2", entry=RARE), _spec("r3")]


def test_locked_flush_is_bounded_and_retirement_unchanged(
        tmp_path, monkeypatch, caplog):
    # reference: the same cycle with no lock
    k0 = _kernel(tmp_path / "free", monkeypatch,
                 {"BTC/USDT": DOWN, "_btc_1h": DOWN})
    _install(k0.journal, _specs())
    rep0 = k0._mechanism_once(per_cycle=1, max_book=8)
    monkeypatch.undo()

    db = str(tmp_path / "locked" / "luffy.db")
    k = _kernel(tmp_path / "locked", monkeypatch,
                {"BTC/USDT": DOWN, "_btc_1h": DOWN})
    _install(k.journal, _specs())
    assert k.journal.query("PRAGMA journal_mode")[0]["journal_mode"] == "wal"

    real_flush = kmod._flush_health
    seen = {"calls": 0}
    holder = {}

    def flush_under_lock(analyst):
        seen["calls"] += 1
        if seen["calls"] == 2:
            # the final flush: retirement is committed; now lock the file
            p = subprocess.Popen([sys.executable, "-c", HOLDER, db],
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, text=True)
            assert p.stdout.readline().strip() == "LOCKED"
            holder["p"] = p
            t0 = time.monotonic()
            out = real_flush(analyst)
            holder["elapsed"] = time.monotonic() - t0
            return out
        return real_flush(analyst)
    monkeypatch.setattr(kmod, "_flush_health", flush_under_lock)

    with caplog.at_level(logging.WARNING):
        rep = k._mechanism_once(per_cycle=1, max_book=8)
    p = holder["p"]
    # while still locked: committed state has the retirement, no health row
    assert _states(db) == {"r1": "retired", "q2": "paper", "r3": "retired"}
    p.stdin.close()
    assert p.wait(timeout=10) == 0

    elapsed = holder["elapsed"]
    print(f"\nlocked flush elapsed: {elapsed:.2f}s "
          f"(journal busy timeout {JOURNAL_BUSY_TIMEOUT_S}s, 3 spec records "
          f"+ 1 sweep record pending)")
    # one failed spec write, then one sweep attempt: two busy timeouts at
    # most — not one per record
    assert JOURNAL_BUSY_TIMEOUT_S * 2 - 1 < elapsed < \
        JOURNAL_BUSY_TIMEOUT_S * 2 + 10

    # authoritative result identical to the unlocked run
    assert rep == rep0
    assert _states(db) == _states(str(tmp_path / "free" / "luffy.db"))
    # no half record, and the holder's own row rolled back
    assert _health(db) == []
    assert [e[0] for e in _events(db)] == [
        "spec_decayed", "spec_decayed", "spec_write_failed"]
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("health observation: write "
               f"{ho.KIND_SPEC}" in m for m in msgs) == 1
    assert sum("health observation: write "
               f"{ho.KIND_SWEEP}" in m for m in msgs) == 1
    assert all("database is locked" in m for m in msgs
               if m.startswith("health observation: write"))
    # the journal still works after the lock is released
    k.journal.log_brain_event("after_lock", "x", {})
    assert _events(db)[-1][0] == "after_lock"
