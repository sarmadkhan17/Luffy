"""Shared fixtures for the research shadow harness tests (not collected)."""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from tests.test_strategy_decay_research_plan import CF, D, EF, W, _FullHist
from trader.core.journal import Journal

I = "idle"

#: both families routable, one refused-free idle, in two specs
SWEEPS = (({"s1": W, "s2": W}, 10),
          ({"s1": D, "s2": EF}, 20),
          ({"s1": EF, "s2": CF}, 30),
          ({"s1": W, "s2": D}, 40))


def make_source(path: Path, sweeps=SWEEPS, hist=None) -> Path:
    """A journal database holding exactly the health rows of ``sweeps``."""
    hist = hist or _FullHist()
    for verdicts, minute in sweeps:
        hist.sweep(dict(verdicts), minute)
    j = Journal(path)
    for r in hist.rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    j._conn().close()
    checkpoint(path)
    return path


def checkpoint(path: Path) -> None:
    c = sqlite3.connect(str(path))
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        c.close()


def spec_event_ids(path: Path) -> list:
    c = sqlite3.connect(str(path))
    try:
        return [r[0] for r in c.execute(
            "SELECT id FROM brain_events WHERE kind='strategy_health_observed'"
            " ORDER BY id")]
    finally:
        c.close()


def dump(path: Path) -> list:
    """Logical content of every table of a database (read-only)."""
    c = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return list(c.iterdump())
    finally:
        c.close()


def file_sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def table_rows(path: Path, table: str) -> list:
    c = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(
            f"SELECT * FROM {table} ORDER BY rowid")]
    finally:
        c.close()


RESEARCH_TABLES = ("research_questions", "research_plans",
                   "research_evidence", "research_results", "research_runs",
                   "research_bank_objects", "research_next_questions",
                   "research_unreadable_questions",
                   "research_unreadable_plans",
                   "research_unreadable_evidence",
                   "research_unreadable_results", "research_unreadable_runs",
                   "research_unreadable_bank_objects",
                   "research_registrations")


def research_counts(path: Path) -> dict:
    c = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        have = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                for t in RESEARCH_TABLES if t in have}
    finally:
        c.close()


def det_clock(step=7):
    t = {"n": 0}

    def clock():
        t["n"] += step
        return t["n"]
    return clock
