"""Decision Journal — the memory of the Trading OS.

Every cycle, vote, decision, trade and outcome lands here. This is what
makes learning possible: rejected setups are tracked with the same rigor
as executed trades, so we can later answer "which agent was right?".

SQLite + WAL; one writer (the OS process), many readers (dashboard, brain).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

from .types import Decision, Snapshot, now_utc

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS cycles (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price REAL,
    regime TEXT,
    adx REAL,
    btc_trend TEXT,
    market_type TEXT,
    mode TEXT
);
CREATE INDEX IF NOT EXISTS idx_cycles_ts ON cycles(ts);

CREATE TABLE IF NOT EXISTS votes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL REFERENCES cycles(id),
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    agent TEXT NOT NULL,
    side TEXT NOT NULL,
    conviction REAL NOT NULL,
    confidence REAL NOT NULL,
    rationale TEXT,
    meta TEXT
);
CREATE INDEX IF NOT EXISTS idx_votes_agent ON votes(agent, ts);
CREATE INDEX IF NOT EXISTS idx_votes_symbol ON votes(symbol, ts);

CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL REFERENCES cycles(id),
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    score REAL NOT NULL,
    threshold REAL NOT NULL,
    confidence REAL NOT NULL,
    executed INTEGER NOT NULL DEFAULT 0,
    skip_reason TEXT,
    size_usdt REAL DEFAULT 0,
    entry_price REAL,
    strategy_ids TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol, ts);
CREATE INDEX IF NOT EXISTS idx_decisions_exec ON decisions(executed);

-- Outcome resolution: what actually happened after a non-HOLD decision.
CREATE TABLE IF NOT EXISTS outcomes (
    decision_id TEXT PRIMARY KEY REFERENCES decisions(id),
    cycle_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,                -- decision time
    action TEXT NOT NULL,
    entry_price REAL NOT NULL,
    resolved_at TEXT,
    fwd_ret_1h REAL,
    fwd_ret_4h REAL,
    fwd_ret_24h REAL,
    correct_1h INTEGER,              -- 1/0/NULL: did direction win at horizon?
    correct_4h INTEGER,
    correct_24h INTEGER
);

CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    decision_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    amount REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    notional_usdt REAL,
    leverage INTEGER DEFAULT 1,
    stop_loss REAL, take_profit REAL, sl_order_id TEXT DEFAULT '',
    strategy_id TEXT, strategy_name TEXT,
    market_type TEXT, exec_mode TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    realized_pnl REAL DEFAULT 0,
    close_reason TEXT,
    status TEXT NOT NULL DEFAULT 'open'   -- open | closed
);
CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy_id);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);

CREATE TABLE IF NOT EXISTS equity (
    ts TEXT PRIMARY KEY,
    equity REAL NOT NULL,
    balance REAL NOT NULL,
    open_positions INTEGER
);

CREATE TABLE IF NOT EXISTS brain_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,              -- review / promote / demote / retire / propose / mutate / note
    subject TEXT,                    -- strategy id or scope
    detail TEXT                      -- free text / JSON
);

CREATE TABLE IF NOT EXISTS control_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,             -- state_change / panic / mode_switch / budget_stop
    from_state TEXT,
    to_state TEXT,
    actor TEXT NOT NULL,             -- operator / luffy / watchdog / risk_engine
    detail TEXT
);

CREATE TABLE IF NOT EXISTS state_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL              -- control_state, market_type, proving_trades, ...
);

CREATE TABLE IF NOT EXISTS strategies (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    params TEXT NOT NULL,            -- JSON genes
    state TEXT NOT NULL,
    description TEXT,
    origin TEXT,
    hypothesis TEXT,
    invalidation TEXT,
    regime_filter TEXT,              -- JSON list
    markets TEXT,                    -- JSON list
    generation INTEGER DEFAULT 0,
    parent_id TEXT DEFAULT '',
    created_at TEXT,
    stats_json TEXT DEFAULT '{}'
);
"""


class Journal:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._write_lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    # -- connection -----------------------------------------------------
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    @contextmanager
    def _tx(self):
        """Serialized write transaction (single-writer discipline)."""
        with self._write_lock:
            conn = self._conn()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # -- writes ----------------------------------------------------------
    def log_cycle(self, snap: Snapshot, cycle_id: str, mode: str) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO cycles VALUES (?,?,?,?,?,?,?,?,?)",
                (cycle_id, snap.ts, snap.symbol, snap.price, snap.regime,
                 snap.adx, snap.btc_trend, snap.market_type, mode))

    def log_votes(self, cycle_id: str, symbol: str, votes: list) -> None:
        rows = [(cycle_id, v.ts, symbol, v.agent, v.side.value,
                 v.conviction, v.confidence, v.rationale,
                 json.dumps(v.meta)) for v in votes]
        if not rows:
            return
        with self._tx() as c:
            c.executemany(
                "INSERT INTO votes(cycle_id,ts,symbol,agent,side,conviction,"
                "confidence,rationale,meta) VALUES (?,?,?,?,?,?,?,?,?)", rows)

    def log_decision(self, d: Decision) -> None:
        strat_ids = ",".join(s.get("strategy_id", "")
                             for s in d.strategy_signals or [])
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (d.id, d.cycle_id, d.ts, d.symbol, d.action.value, d.score,
                 d.threshold, d.confidence, int(d.executed), d.skip_reason,
                 d.size_usdt, None, strat_ids))

    def update_decision_outcome(self, decision_id: str, executed: bool,
                                size_usdt: float = 0.0,
                                skip_reason: str = "") -> None:
        with self._tx() as c:
            c.execute("UPDATE decisions SET executed=?, size_usdt=?, "
                      "skip_reason=? WHERE id=?",
                      (int(executed), size_usdt, skip_reason, decision_id))

    def set_decision_entry_price(self, decision_id: str, price: float) -> None:
        with self._tx() as c:
            c.execute("UPDATE decisions SET entry_price=? WHERE id=?",
                      (price, decision_id))

    def add_trade(self, p) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open')",
                (p.id, p.decision_id, p.symbol, p.side.value, p.amount,
                 p.entry_price, None, p.notional_usdt, p.leverage,
                 p.stop_loss, p.take_profit, getattr(p, "sl_order_id", ""),
                 p.strategy_id, p.strategy_name,
                 p.market_type, p.exec_mode, p.opened_at, None, 0.0, None))

    def close_trade(self, trade_id: str, exit_price: float, pnl: float,
                    reason: str, closed_at: str | None = None) -> None:
        closed_at = closed_at or now_utc().isoformat()
        with self._tx() as c:
            c.execute(
                "UPDATE trades SET exit_price=?, realized_pnl=?, close_reason=?, "
                "closed_at=?, status='closed' WHERE id=?",
                (exit_price, pnl, reason, closed_at, trade_id))

    def update_position_protection(self, trade_id: str, sl: float, tp: float) -> None:
        with self._tx() as c:
            c.execute("UPDATE trades SET stop_loss=?, take_profit=? WHERE id=?",
                      (sl, tp, trade_id))

    def log_equity(self, equity: float, balance: float, n_open: int) -> None:
        ts = now_utc().isoformat(timespec="seconds")
        with self._tx() as c:
            c.execute("INSERT OR REPLACE INTO equity VALUES (?,?,?,?)",
                      (ts, equity, balance, n_open))

    def log_brain_event(self, kind: str, subject: str, detail: Any) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO brain_events(ts,kind,subject,detail) VALUES (?,?,?,?)",
                      (now_utc().isoformat(), kind, subject,
                       detail if isinstance(detail, str) else json.dumps(detail)))

    # -- control state ----------------------------------------------------
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        row = self._conn().execute(
            "SELECT value FROM state_kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self._tx() as c:
            c.execute("INSERT OR REPLACE INTO state_kv(key,value) VALUES (?,?)",
                      (key, value))

    def log_control_event(self, event: str, actor: str,
                          from_state: str = "", to_state: str = "",
                          detail: Any = "") -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO control_events(ts,event,from_state,to_state,actor,detail) "
                "VALUES (?,?,?,?,?,?)",
                (now_utc().isoformat(), event, from_state, to_state, actor,
                 detail if isinstance(detail, str) else json.dumps(detail)))

    def schedule_outcome(self, decision_id: str, cycle_id: str, symbol: str,
                         ts: str, action: str, entry_price: float) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO outcomes"
                "(decision_id,cycle_id,symbol,ts,action,entry_price) "
                "VALUES (?,?,?,?,?,?)",
                (decision_id, cycle_id, symbol, ts, action, entry_price))

    # -- strategy population ------------------------------------------------
    def upsert_strategy(self, st) -> None:
        from .types import Strategy as _S   # typing only; avoid cycle at import
        with self._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO strategies VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (st.id, st.name, st.kind, json.dumps(st.params), st.state.value,
                 st.description, st.origin,
                 getattr(st, "hypothesis", ""), getattr(st, "invalidation", ""),
                 json.dumps(sorted(getattr(st, "regime_filter", []) or [])),
                 json.dumps(sorted(getattr(st, "markets", []) or [])),
                 int(getattr(st, "generation", 0)), getattr(st, "parent_id", ""),
                 st.created_at, json.dumps(st.stats)))

    def list_strategies(self, states: list[str] | None = None) -> list[dict]:
        if states:
            q = f"SELECT * FROM strategies WHERE state IN " \
                f"({','.join('?' * len(states))}) ORDER BY created_at"
            return self.query(q, tuple(states))
        return self.query("SELECT * FROM strategies ORDER BY created_at")

    # -- reads (used by brain, dashboard, learning) -----------------------
    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        rows = self._conn().execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def open_trades(self) -> list[dict]:
        return self.query("SELECT * FROM trades WHERE status='open'")

    def trades_for_strategy(self, strategy_id: str) -> list[dict]:
        return self.query(
            "SELECT * FROM trades WHERE strategy_id=? ORDER BY opened_at",
            (strategy_id,))

    def unresolved_outcomes(self, older_than_hours: float = 1.0) -> list[dict]:
        return self.query(
            "SELECT * FROM outcomes WHERE resolved_at IS NULL "
            "AND ts <= datetime('now', ?) ",
            (f"-{older_than_hours} hours",))

    def agent_accuracy(self, since_hours: float = 168.0) -> list[dict]:
        """Directional accuracy of each agent's vote vs 4h forward return."""
        return self.query("""
            SELECT v.agent,
                   COUNT(*) AS n,
                   AVG(o.correct_4h) AS accuracy
            FROM votes v
            JOIN outcomes o ON o.symbol=v.symbol AND o.cycle_id=v.cycle_id
             AND o.resolved_at IS NOT NULL
            WHERE v.conviction != 0 AND o.correct_4h IS NOT NULL
              AND v.ts >= datetime('now', ?)
            GROUP BY v.agent
        """, (f"-{since_hours} hours",))
