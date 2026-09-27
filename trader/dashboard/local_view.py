"""Local-first, read-only owner Overview.

Everything here reads bounded local evidence only: the journal through a
read-only SQLite connection and small files under data/. It never contacts a
venue, downloads market metadata, calls an LLM or runs migrations, so first
useful Overview data cannot wait on an external dependency.

Every section carries provenance. Values the journal recorded are labelled
as such; none is venue-confirmed here. Missing values stay None and are
marked "unavailable" — never replaced with zero.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

#: a kernel cycle writes equity + heartbeat about every 60 s
#: (config timeframes.scan_interval_seconds); three missed cycles is stale.
EQUITY_STALE_S = 180
HEARTBEAT_STALE_S = 180
#: open positions shown on the Overview; the book never approaches this.
MAX_OPEN_ROWS = 50
AGENT_VOTE_ROWS = 2000
LOG_TAIL_MAX_LINES = 500
LOG_TAIL_MAX_BYTES = 256 * 1024


class StoreUnavailable(Exception):
    pass


class ReadOnlyStore:
    """Thread-local read-only SQLite connections to an existing database.

    `mode=ro` + `query_only` make a write impossible, and opening never
    creates the file or executes schema/migration statements (unlike
    `Journal.__init__`). Each thread owns its own connection, as sqlite3
    requires by default.
    """

    def __init__(self, path: str | Path, busy_timeout_ms: int = 2000):
        self.path = Path(path)
        self.busy_timeout_ms = int(busy_timeout_ms)
        self._local = threading.local()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            if not self.path.exists():
                raise StoreUnavailable(f"{self.path.name} missing")
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True,
                                   timeout=self.busy_timeout_ms / 1000)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            self._local.conn = conn
        return conn

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        try:
            return [dict(r) for r in self._conn().execute(sql, params).fetchall()]
        except sqlite3.OperationalError as e:
            # a table this checkout predates, or a transient lock: unavailable
            raise StoreUnavailable(str(e)) from e


class Busy(Exception):
    """Shared work is saturated and no completed value exists yet."""


class SharedTTL:
    """Per-key TTL cache where concurrent callers share one computation.

    Callers await on the event loop, so a waiter holds no worker-thread
    token: only the single computation occupies one worker, until it really
    finishes. Admission is bounded per key; a caller refused admission, or
    whose wait times out, gets the last completed value marked stale, or
    `Busy` when none exists. The value's time is taken at completion.
    """

    def __init__(self, clock=time.monotonic, max_waiters: int = 32,
                 wait_s: float = 10.0):
        self._clock = clock
        self.max_waiters = max_waiters
        self.wait_s = wait_s
        self._tasks: dict[str, asyncio.Future] = {}
        self._values: dict[str, tuple[float, object]] = {}
        self._waiters: dict[str, int] = {}
        self.computed: dict[str, int] = {}

    def _meta(self, key: str, stale: bool) -> dict:
        at = self._values[key][0]
        return {"age_s": round(self._clock() - at, 2), "stale": stale}

    def _fallback(self, key: str, reason: str):
        if key in self._values:
            return self._values[key][1], {**self._meta(key, True),
                                          "reason": reason}
        raise Busy(reason)

    async def _compute(self, key: str, fn):
        from starlette.concurrency import run_in_threadpool
        value = await run_in_threadpool(fn)
        self._values[key] = (self._clock(), value)
        self.computed[key] = self.computed.get(key, 0) + 1

    async def get(self, key: str, ttl: float, fn):
        """(value, {"age_s", "stale"[, "reason"]})"""
        hit = self._values.get(key)
        if hit is not None and self._clock() - hit[0] < ttl:
            return hit[1], self._meta(key, False)
        loop = asyncio.get_running_loop()
        task = self._tasks.get(key)
        if task is None or task.done() or task.get_loop() is not loop:
            task = loop.create_task(self._compute(key, fn))
            # consume an exception nobody awaited (all waiters timed out)
            task.add_done_callback(lambda t: t.cancelled() or t.exception())
            self._tasks[key] = task
        if self._waiters.get(key, 0) >= self.max_waiters:
            return self._fallback(key, "saturated")
        self._waiters[key] = self._waiters.get(key, 0) + 1
        try:
            await asyncio.wait_for(asyncio.shield(task), self.wait_s)
        except asyncio.TimeoutError:
            return self._fallback(key, "timeout")
        finally:
            self._waiters[key] -= 1
        return self._values[key][1], self._meta(key, False)


def _parse_ts(ts) -> datetime | None:
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _age_s(ts, now: float) -> float | None:
    t = _parse_ts(ts)
    return None if t is None else round(now - t.timestamp(), 1)


#: a recorded time this far ahead of our clock is invalid, never fresh
FUTURE_TOLERANCE_S = 5.0


def _fresh(age: float | None, stale_after: float) -> str:
    if age is None:
        return "unavailable"
    if age < -FUTURE_TOLERANCE_S:
        return "stale"
    return "fresh" if age <= stale_after else "stale"


def _section(source: str, observed_at, now: float, stale_after: float | None,
             **values) -> dict:
    age = _age_s(observed_at, now) if observed_at else None
    if stale_after is None:
        freshness = "recorded" if observed_at else "unavailable"
    else:
        freshness = _fresh(age, stale_after)
    # stale_after_s lets a client keep ageing a retained copy by itself
    return {"source": source, "observed_at": observed_at, "age_s": age,
            "freshness": freshness, "stale_after_s": stale_after, **values}


def _unavailable(source: str, reason: str) -> dict:
    return {"source": source, "observed_at": None, "age_s": None,
            "freshness": "unavailable", "reason": reason}


def _heartbeat(root: Path, now: float) -> dict:
    path = root / "data" / "heartbeat_luffy.json"
    try:
        ts = float(json.loads(path.read_text())["timestamp"])
    except (OSError, ValueError, KeyError, TypeError):
        return _unavailable("data/heartbeat_luffy.json", "missing or unreadable")
    observed = datetime.fromtimestamp(ts, timezone.utc).isoformat()
    return _section("data/heartbeat_luffy.json", observed, now,
                    HEARTBEAT_STALE_S)


def _one(store: ReadOnlyStore, source: str, fn):
    """Run one section; a missing table or lock makes only it unavailable."""
    try:
        return fn()
    except StoreUnavailable as e:
        return _unavailable(source, str(e))


def overview(store: ReadOnlyStore, root: Path, now: float | None = None) -> dict:
    """The owner's first useful data, from local evidence only."""
    now = time.time() if now is None else now
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")

    def control():
        kv = {r["key"]: r["value"] for r in store.query(
            "SELECT key, value FROM state_kv WHERE key IN "
            "('control_state','market_type')")}
        last = store.query(
            "SELECT ts, event, to_state, actor FROM control_events "
            "ORDER BY id DESC LIMIT 1")
        last = last[0] if last else None
        # the journal's record of the operator/kernel intent — not a fresh
        # verification of what the kernel or venue is enforcing now
        return _section("journal.state_kv", last["ts"] if last else None,
                        now, None,
                        control_state=kv.get("control_state"),
                        market_type=kv.get("market_type"),
                        last_event=last, verified=False)

    def equity():
        rows = store.query(
            "SELECT ts, equity, balance, open_positions FROM equity "
            "ORDER BY ts DESC LIMIT 2")
        if not rows:
            return _unavailable("journal.equity", "no equity recorded")
        return _section("journal.equity", rows[0]["ts"], now, EQUITY_STALE_S,
                        equity=rows[0]["equity"], balance=rows[0]["balance"],
                        equity_prev=rows[1]["equity"] if len(rows) > 1 else None,
                        venue_confirmed=False)

    def positions():
        rows = store.query(
            "SELECT id, symbol, side, amount, entry_price, notional_usdt, "
            "leverage, stop_loss, take_profit, sl_order_id, strategy_name, "
            "opened_at FROM trades WHERE status='open' ORDER BY opened_at "
            "LIMIT ?", (MAX_OPEN_ROWS,))
        for r in rows:
            # stop/target are what the journal recorded at entry/trail time;
            # this page has not verified a venue protective order exists
            r["protection"] = {"source": "journal.trades",
                               "venue_verified": False,
                               "stop_loss": r.pop("stop_loss"),
                               "take_profit": r.pop("take_profit"),
                               "has_stop_order_id": bool(r.pop("sl_order_id"))}
        # exposure over the WHOLE open book, independent of the row cap
        agg = {r["side"]: r for r in store.query(
            "SELECT side, COUNT(*) n, COUNT(notional_usdt) known, "
            "SUM(notional_usdt) notional FROM trades WHERE status='open' "
            "GROUP BY side")}
        total = sum(r["n"] for r in agg.values())
        missing = sum(r["n"] - r["known"] for r in agg.values())
        known = {side: round(agg[side]["notional"] or 0.0, 2) if side in agg else 0.0
                 for side in ("long", "short")}
        return {"source": "journal.trades", "freshness": "recorded",
                "rows": rows, "open_total": total, "shown": len(rows),
                "truncated": total > len(rows),
                "entry_notional": {
                    # null unless every open row has a recorded notional;
                    # known_* are then the explicitly partial sums
                    "complete": missing == 0,
                    "long": known["long"] if missing == 0 else None,
                    "short": known["short"] if missing == 0 else None,
                    "known_long": known["long"], "known_short": known["short"],
                    "rows_missing_notional": missing,
                    "basis": "entry notional, not marked"}}

    def activity():
        # one pass over today's decisions instead of three LIKE scans
        d = store.query(
            "SELECT SUM(executed=1) taken, "
            "SUM(executed=0 AND action!='HOLD') skipped, "
            "SUM(action='HOLD') holds, COUNT(*) n FROM decisions "
            "WHERE ts >= ? AND ts < ?", (day, day + "~"))[0]
        pnl = store.query(
            "SELECT SUM(realized_pnl) s, COUNT(*) n, COUNT(realized_pnl) known "
            "FROM trades WHERE status='closed' AND closed_at >= ? "
            "AND closed_at < ?", (day, day + "~"))[0]
        missing = pnl["n"] - pnl["known"]
        known_sum = round(pnl["s"] or 0.0, 2)
        return {"source": "journal.decisions+trades", "freshness": "recorded",
                "utc_day": day, "taken": d["taken"] or 0,
                "skipped": d["skipped"] or 0, "holds": d["holds"] or 0,
                "closed_today": pnl["n"],
                # journal-booked realized P&L. 0 means the journal records no
                # close today; null means a close today has no recorded P&L
                # (see realized_pnl_missing / realized_pnl_known_sum)
                "realized_pnl_today": known_sum if missing == 0 else None,
                "realized_pnl_known_sum": known_sum,
                "realized_pnl_missing": missing}

    def performance():
        rows = store.query(
            "SELECT COALESCE(NULLIF(strategy_name,''),'orchestrator') sname, "
            "ROUND(SUM(realized_pnl),2) pnl, COUNT(*) n, "
            "COUNT(realized_pnl) known, SUM(realized_pnl>0) wins "
            "FROM trades WHERE status='closed' GROUP BY sname ORDER BY pnl")
        n = sum(r["n"] for r in rows)
        known = sum(r["known"] for r in rows)
        wins = sum(r["wins"] or 0 for r in rows)
        for r in rows:
            r.pop("wins")
            r["missing"] = r["n"] - r.pop("known")   # pnl sums known rows only
        return {"source": "journal.trades", "freshness": "recorded",
                "closed_trades": n,
                # outcome rate over trades whose P&L is recorded
                "winrate": round(wins / known * 100, 1) if known else None,
                "winrate_basis_trades": known, "pnl_missing": n - known,
                "strategy_pnl": rows}

    def agents():
        cutoff = datetime.fromtimestamp(now - 900, timezone.utc).isoformat()
        # latest vote per agent within 15 min. votes has no ts index, so read
        # only the newest AGENT_VOTE_ROWS rows by rowid (insertion order)
        # instead of a MAX(rowid) GROUP BY over the whole table. An agent
        # silent for that many rows is reported as not in the window.
        rows = store.query(
            "SELECT v.agent, v.side, v.conviction, v.confidence, v.ts, "
            "c.regime FROM (SELECT * FROM votes ORDER BY rowid DESC "
            "LIMIT ?) v LEFT JOIN cycles c ON c.id=v.cycle_id",
            (AGENT_VOTE_ROWS,))
        seen, out = set(), []
        for r in sorted(rows, key=lambda r: str(r["ts"]), reverse=True):
            if str(r["ts"]) > cutoff and r["agent"] not in seen:
                seen.add(r["agent"])
                out.append(r)
        return {"source": "journal.votes", "freshness": "recorded",
                "window_s": 900, "scanned_rows": len(rows),
                "agents_in_window": len(out), "rows": out[:6]}

    def strategies():
        n = store.query("SELECT COUNT(*) n FROM strategies "
                        "WHERE state IN ('paper','active')")[0]["n"]
        return {"source": "journal.strategies", "freshness": "recorded",
                "paper_or_active": n}

    return {
        "generated_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "scope": "local journal/files only; nothing here is venue-confirmed",
        "heartbeat": _heartbeat(root, now),
        "control": _one(store, "journal.state_kv", control),
        "equity": _one(store, "journal.equity", equity),
        "positions": _one(store, "journal.trades", positions),
        "today": _one(store, "journal.decisions+trades", activity),
        "performance": _one(store, "journal.trades", performance),
        "agents": _one(store, "journal.votes", agents),
        "strategies": _one(store, "journal.strategies", strategies),
    }


def live_payload(store: ReadOnlyStore) -> dict:
    """The WebSocket ticker frame: latest equity, open trades, decisions."""
    return {
        "equity": store.query(
            "SELECT equity, ts FROM equity ORDER BY ts DESC LIMIT 1"),
        "open_trades": store.query(
            "SELECT * FROM trades WHERE status='open' LIMIT ?",
            (MAX_OPEN_ROWS,)),
        "recent_decisions": store.query(
            "SELECT ts,symbol,action,score,executed,skip_reason "
            "FROM decisions ORDER BY ts DESC LIMIT 12"),
    }


def tail_lines(path: Path, lines: int) -> list[str]:
    """Last `lines` lines, reading at most LOG_TAIL_MAX_BYTES from the end."""
    lines = max(1, min(int(lines), LOG_TAIL_MAX_LINES))
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            start = max(0, size - LOG_TAIL_MAX_BYTES)
            f.seek(start)
            data = f.read(size - start)
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace").replace("\x00", "")
    out = text.splitlines()
    if start > 0 and out:
        out = out[1:]          # the first line was cut mid-way
    return out[-lines:]
