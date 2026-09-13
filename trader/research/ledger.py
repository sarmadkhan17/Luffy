"""Every combination the search ever looked at, kept or not.

Three jobs:

- RESUME. A restart continues from here; nothing is re-tested, and the days
  the first singles pass costs are not spent twice.
- COUNT THE LOOKS. The same set of parts reached by two growth paths has one
  canonical hash and is one look. Phase 3's error budget prices looks, so a
  double-counted or missed one corrupts the budget rather than the row.
- SAY WHY. A row carries the verdict AND the reason, so "nothing survives" is
  never reported without the ablation and the window's control beside it.

Written by the kernel's research thread through `Journal._tx()`. Never
through `Journal.query()`: that runs on a thread-local connection with no
transaction wrapper, so a write through it stays uncommitted and lock-holding
until some later `_tx()` on the same thread commits it as a side effect —
which is how an unvalidated strategy reached the book on 2026-09-11.
"""
from __future__ import annotations

import json
import logging

from ..core.types import now_utc

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_combos (
    hash TEXT PRIMARY KEY,
    tf TEXT NOT NULL,
    geo TEXT NOT NULL,
    k INTEGER NOT NULL,
    round TEXT,
    parent TEXT,
    trigger TEXT,
    window TEXT,
    parts TEXT,                    -- JSON list of canonical part keys
    entry_long TEXT,
    entry_short TEXT,
    status TEXT,                   -- scored | untestable | empty | error
    label TEXT,                    -- scored | no_edge | underpowered | ...
    verdict TEXT,                  -- survivor | grow | prune
    reason TEXT,
    consistency_p REAL,
    median_pf REAL,
    total_pct REAL,
    max_dd_pct REAL,
    trades INTEGER,
    scored_symbols INTEGER,
    testable INTEGER,
    ablation TEXT,                 -- JSON {removed part: what it cost}
    result TEXT,                   -- the full evaluation, JSON
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_combos_pick
    ON research_combos(tf, geo, k, verdict);

CREATE TABLE IF NOT EXISTS research_gauges (
    tf TEXT NOT NULL,
    expr TEXT NOT NULL,
    p10 REAL, p25 REAL, p75 REAL, p90 REAL,
    min REAL, max REAL,
    n INTEGER,
    finite_frac REAL,
    usable INTEGER,
    measured_at TEXT,
    PRIMARY KEY (tf, expr)
);

CREATE TABLE IF NOT EXISTS research_controls (
    tf TEXT NOT NULL,
    window TEXT NOT NULL,
    consistency_p REAL,
    powered INTEGER,
    status TEXT,                   -- measured | uncalibrated | untestable
    detail TEXT,
    measured_at TEXT,
    PRIMARY KEY (tf, window)
);

CREATE TABLE IF NOT EXISTS research_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tf TEXT,
    vintage TEXT,
    table_name TEXT,
    row_json TEXT,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS research_slices (
    tf TEXT PRIMARY KEY,
    cut_ms INTEGER,
    counts TEXT,                   -- JSON {discovery, heldout_a, heldout_b}
    measured_at TEXT
);

CREATE TABLE IF NOT EXISTS research_batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT, finished TEXT,
    tf TEXT, geo TEXT, round TEXT,
    n INTEGER, ok INTEGER, elapsed_s REAL, error TEXT
);
"""


class Ledger:
    def __init__(self, journal):
        self.journal = journal

    def ensure(self) -> None:
        with self.journal._tx() as c:
            c.executescript(SCHEMA)
            self._migrate(c)

    def _migrate(self, c) -> None:
        """Add columns to tables created before this fix, without touching
        their existing rows. `CREATE TABLE IF NOT EXISTS` cannot widen a
        table that already exists under the old shape, and the live ledger
        already held rows measured before `min`/`max`/`status` existed."""
        additions = {
            "research_gauges": (("min", "REAL"), ("max", "REAL")),
            "research_controls": (("status", "TEXT"),),
        }
        for table, cols in additions.items():
            have = {r[1] for r in
                    c.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, typ in cols:
                if name not in have:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")

    # ── the vocabulary's measurements ────────────────────────────────────
    def record_gauges(self, tf: str, rows: dict) -> int:
        now = now_utc().isoformat()
        with self.journal._tx() as c:
            for expr, m in rows.items():
                c.execute(
                    "INSERT OR REPLACE INTO research_gauges "
                    "(tf, expr, p10, p25, p75, p90, min, max, n, "
                    "finite_frac, usable, measured_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (tf, expr, m.get("p10"), m.get("p25"), m.get("p75"),
                     m.get("p90"), m.get("min"), m.get("max"),
                     int(m.get("n", 0)), float(m.get("finite_frac", 0.0)),
                     1 if m.get("usable") else 0, now))
        return len(rows)

    def gauges(self, tf: str) -> dict:
        out = {}
        for r in self.journal.query(
                "SELECT * FROM research_gauges WHERE tf=?", (tf,)):
            out[r["expr"]] = {"p10": r["p10"], "p25": r["p25"],
                              "p75": r["p75"], "p90": r["p90"],
                              "min": r.get("min"), "max": r.get("max"),
                              "n": r["n"], "finite_frac": r["finite_frac"],
                              "usable": bool(r["usable"])}
        return out

    # ── where the cut fell, and how much room each slice has ─────────────
    def record_slices(self, tf: str, cut_ms: int, counts: dict) -> None:
        with self.journal._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO research_slices "
                "(tf, cut_ms, counts, measured_at) VALUES (?,?,?,?)",
                (tf, int(cut_ms), json.dumps(counts),
                 now_utc().isoformat()))

    def slices(self, tf: str) -> dict | None:
        rows = self.journal.query(
            "SELECT * FROM research_slices WHERE tf=?", (tf,))
        if not rows:
            return None
        out = json.loads(rows[0]["counts"] or "{}")
        out["cut_ms"] = rows[0]["cut_ms"]
        return out

    # ── the window controls ──────────────────────────────────────────────
    def record_control(self, tf: str, window: str, res: dict,
                       powered: bool, status: str = "measured") -> None:
        with self.journal._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO research_controls "
                "(tf, window, consistency_p, powered, status, detail, "
                "measured_at) VALUES (?,?,?,?,?,?,?)",
                (tf, window, res.get("consistency_p"), 1 if powered else 0,
                 status, json.dumps(res)[:20000], now_utc().isoformat()))

    def control(self, tf: str, window: str) -> dict | None:
        rows = self.journal.query(
            "SELECT * FROM research_controls WHERE tf=? AND window=?",
            (tf, window))
        if not rows:
            return None
        return {"tf": tf, "window": window,
                "consistency_p": rows[0]["consistency_p"],
                "powered": bool(rows[0]["powered"]),
                "status": rows[0].get("status") or "measured"}

    # ── the combinations ─────────────────────────────────────────────────
    def record_result(self, res: dict, verdict: str, reason: str,
                      ablation: dict, label: str = "") -> None:
        pf = res.get("portfolio") or {}
        with self.journal._tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO research_combos "
                "(hash, tf, geo, k, round, parent, trigger, window, parts, "
                "entry_long, entry_short, status, label, verdict, reason, "
                "consistency_p, median_pf, total_pct, max_dd_pct, trades, "
                "scored_symbols, testable, ablation, result, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (res["hash"], res["tf"], res["geo"], int(res.get("k", 1)),
                 res.get("round", ""), res.get("parent", ""),
                 res.get("trigger", ""), res.get("window", "ohlcv"),
                 json.dumps(res.get("parts", [])),
                 res.get("entry_long", ""), res.get("entry_short", ""),
                 res.get("verdict", ""), label or res.get("verdict", ""),
                 verdict, reason[:500],
                 res.get("consistency_p"), res.get("median_pf"),
                 pf.get("total_pct"), pf.get("max_dd_pct"),
                 int(res.get("trades", 0)),
                 int(res.get("scored_symbols", 0)),
                 1 if res.get("testable") else 0,
                 json.dumps(ablation or {}),
                 json.dumps(_slim(res))[:200000],
                 now_utc().isoformat()))

    def has(self, h: str) -> bool:
        return bool(self.journal.query(
            "SELECT 1 FROM research_combos WHERE hash=?", (h,)))

    def known(self, tf: str, geo: str) -> set:
        return {r["hash"] for r in self.journal.query(
            "SELECT hash FROM research_combos WHERE tf=? AND geo=?",
            (tf, geo))}

    def result(self, h: str) -> dict | None:
        rows = self.journal.query(
            "SELECT result FROM research_combos WHERE hash=?", (h,))
        if not rows:
            return None
        try:
            return json.loads(rows[0]["result"] or "{}")
        except Exception:                               # noqa: BLE001
            return None

    def rows(self, tf: str, geo: str | None = None, k: int | None = None,
             verdict: str | None = None, limit: int | None = None) -> list:
        sql = "SELECT * FROM research_combos WHERE tf=?"
        args: list = [tf]
        if geo is not None:
            sql += " AND geo=?"
            args.append(geo)
        if k is not None:
            sql += " AND k=?"
            args.append(int(k))
        if verdict is not None:
            sql += " AND verdict=?"
            args.append(verdict)
        sql += " ORDER BY consistency_p IS NULL, consistency_p, " \
               "total_pct DESC, hash"
        if limit:
            sql += f" LIMIT {int(limit)}"
        return self.journal.query(sql, tuple(args))

    # ── re-measurement ───────────────────────────────────────────────────
    def combos_exist(self, tf: str) -> int:
        """How many `research_combos` rows already stand for this horizon.

        Thresholds are frozen once measured, deliberately: a hash carries a
        threshold's percentile RANK, never its value, so a silent
        re-measure re-points every stored hash at different numbers while
        `known()` still treats it as already evaluated. `--measure` reads
        this before touching a horizon that has scored rows.
        """
        rows = self.journal.query(
            "SELECT COUNT(*) AS n FROM research_combos WHERE tf=?", (tf,))
        return int(rows[0]["n"]) if rows else 0

    def archive_horizon(self, tf: str, vintage: str) -> dict:
        """Move every `research_combos`/`research_controls` row for `tf`
        aside, stamped with `vintage`, before its gauges are re-measured.

        This is what makes a deliberate re-measure honest rather than a
        no-op: without it, the old rows stay in place under hashes that now
        mean a different threshold, and `known()` would silently treat them
        as "already evaluated" under numbers that no longer exist anywhere.
        """
        moved = {"combos": 0, "controls": 0}
        now = now_utc().isoformat()
        with self.journal._tx() as c:
            for table, key in (("research_combos", "combos"),
                               ("research_controls", "controls")):
                cur = c.execute(f"SELECT * FROM {table} WHERE tf=?", (tf,))
                rows = cur.fetchall()
                cols = [d[0] for d in cur.description]
                for row in rows:
                    c.execute(
                        "INSERT INTO research_archive "
                        "(tf, vintage, table_name, row_json, archived_at) "
                        "VALUES (?,?,?,?,?)",
                        (tf, vintage, table,
                         json.dumps(dict(zip(cols, row))), now))
                moved[key] = len(rows)
                c.execute(f"DELETE FROM {table} WHERE tf=?", (tf,))
        return moved

    # ── batches ──────────────────────────────────────────────────────────
    def start_batch(self, tf: str, geo: str, round_: str, n: int) -> int:
        with self.journal._tx() as c:
            cur = c.execute(
                "INSERT INTO research_batches "
                "(started, tf, geo, round, n, ok, elapsed_s, error) "
                "VALUES (?,?,?,?,?,0,0,'')",
                (now_utc().isoformat(), tf, geo, round_, int(n)))
            return int(cur.lastrowid)

    def finish_batch(self, batch_id: int, ok: bool, elapsed_s: float,
                     error: str = "") -> None:
        with self.journal._tx() as c:
            c.execute(
                "UPDATE research_batches SET finished=?, ok=?, elapsed_s=?, "
                "error=? WHERE id=?",
                (now_utc().isoformat(), 1 if ok else 0, float(elapsed_s),
                 (error or "")[:500], int(batch_id)))

    # ── reporting ────────────────────────────────────────────────────────
    def counts(self) -> dict:
        out = {"combos": 0, "by_verdict": {}, "by_tf": {}, "by_k": {},
               "batches": 0, "failed_batches": 0}
        for r in self.journal.query(
                "SELECT verdict, tf, k, COUNT(*) n FROM research_combos "
                "GROUP BY verdict, tf, k"):
            out["combos"] += r["n"]
            out["by_verdict"][r["verdict"]] = \
                out["by_verdict"].get(r["verdict"], 0) + r["n"]
            out["by_tf"][r["tf"]] = out["by_tf"].get(r["tf"], 0) + r["n"]
            out["by_k"][r["k"]] = out["by_k"].get(r["k"], 0) + r["n"]
        for r in self.journal.query(
                "SELECT ok, COUNT(*) n FROM research_batches GROUP BY ok"):
            out["batches"] += r["n"]
            if not r["ok"]:
                out["failed_batches"] += r["n"]
        return out


def _slim(res: dict) -> dict:
    """The stored result, without the portfolio's per-fill ordering.

    `order` is one entry per taken trade — thousands of rows of symbol names
    that nothing reads back. The numbers computed from it are kept.
    """
    out = dict(res)
    pf = dict(out.get("portfolio") or {})
    pf.pop("order", None)
    out["portfolio"] = pf
    return out
