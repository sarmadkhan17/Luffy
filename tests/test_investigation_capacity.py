"""investigation.db capacity: measured on active pages; pruning respects retention and every protection.

All stores are temporary. Caps are scaled down with monkeypatch so a few hundred KiB behaves like 32 MiB;
the default caps themselves are asserted unchanged below.
"""
import json
import sqlite3
import time

import pytest

from trader.cognition import investigation as I
from trader.observability import investigation as C, population
from tests.test_attention_learning import publish

DAY = 86_400_000
NOW = 1_800_000_000_000
BLOB = "x" * 20_000


@pytest.fixture
def small(monkeypatch, tmp_path_factory):
    """Caps scaled relative to the measured size of an empty ledger (the schema alone is hundreds of KiB)."""
    probe = C.ledger(tmp_path_factory.mktemp("probe") / "p.db")
    base = C.capacity(probe)["active_bytes"]
    probe.close()
    monkeypatch.setattr(C, "SOFT_BYTES", base + 250_000)
    monkeypatch.setattr(C, "HARD_BYTES", base + 600_000)
    monkeypatch.setattr(C, "MAX_BYTES", base + 800_000)
    return base


def _add_case(db, cid, terminal_ms, created_ms=NOW - 90 * DAY, inputs=3):
    db.execute("INSERT INTO cases VALUES (?,?,?,?,?,?)", (cid, "ep-" + cid, "SYM-" + cid, created_ms, terminal_ms, "{}"))
    for i in range(inputs):
        db.execute("INSERT INTO inputs VALUES (?,?)", (f"{cid}-in{i}", BLOB + str(i)))
        db.execute("INSERT INTO case_inputs VALUES (?,?)", (cid, f"{cid}-in{i}"))
    db.execute("INSERT INTO updates VALUES (?,?,?,?)", ("u-" + cid, cid, created_ms, "{}"))


def _ids(db):
    return {r[0] for r in db.execute("SELECT id FROM cases")}


def test_default_caps_and_retention_are_unchanged():
    assert C.MAX_BYTES == 32 * 1024 ** 2 and C.RETENTION_MS == 30 * DAY
    assert C.SOFT_BYTES == 28 * 1024 ** 2 and C.HARD_BYTES == C.MAX_BYTES - 1024 ** 2


def test_capacity_is_measured_on_active_pages_not_file_size(tmp_path, small):
    path = tmp_path / "inv.db"
    with sqlite3.connect(path) as pre:                    # an existing database where auto_vacuum never took effect
        pre.execute("PRAGMA auto_vacuum=NONE")
        pre.execute("CREATE TABLE seed(x)")
    db = C.ledger(path)
    for n in range(6):
        _add_case(db, f"c{n}", NOW)
    db.commit()
    full = C.capacity(db)
    db.execute("DELETE FROM cases"); C._cascade_case_rows(db); db.commit()
    after = C.capacity(db)
    assert after["file_bytes"] == full["file_bytes"], 'deleted pages stay in the file'
    assert after["free_bytes"] > 0 and after["active_bytes"] < full["active_bytes"] / 2
    assert after["active_bytes"] + after["free_bytes"] == after["file_bytes"]
    assert after["headroom_bytes"] == C.MAX_BYTES - after["active_bytes"]
    assert C.prune_for_cap(db, NOW)["blocked"] is False   # file is big, but active space is small: not full


def test_only_terminal_expired_unreferenced_cases_are_pruned(tmp_path, small):
    db = C.ledger(tmp_path / "inv.db")
    old = NOW - 45 * DAY                                  # terminal and past the 30-day retention
    _add_case(db, "open", None)                           # still open: never pruned
    _add_case(db, "young", NOW - 5 * DAY)                 # terminal but inside retention
    _add_case(db, "edge", NOW - 30 * DAY + 1)             # one ms inside retention
    _add_case(db, "outcome", old)                         # referenced by a live typed outcome
    _add_case(db, "pending", old)                         # referenced by an unresolved counterfactual registration
    _add_case(db, "learned", old)                         # named by a learning population event
    _add_case(db, "gone1", old - 2 * DAY)                 # eligible
    _add_case(db, "gone2", old - 1 * DAY)                 # eligible
    db.execute("INSERT INTO typed_outcomes VALUES (?,?,?)", ("k1", NOW - DAY, json.dumps({"case": "outcome"})))
    db.execute("INSERT INTO counterfactual_registrations VALUES (?,?,NULL,?)", ("r1", NOW - DAY, json.dumps({"case": "pending"})))
    population.schema(db)                                 # created lazily once population tracking is configured
    db.execute("INSERT INTO population_events VALUES (?,?,?,?)", ("p1", "h", NOW, json.dumps({"case": "learned"})))
    db.commit()
    assert C.capacity(db)["active_bytes"] > C.SOFT_BYTES, "fixture must be over the soft cap"
    report = C.prune_for_cap(db, NOW)
    assert _ids(db) == {"open", "young", "edge", "outcome", "pending", "learned"}
    assert report["deleted_cases"] == 2
    left = {r[0] for r in db.execute("SELECT case_id FROM case_inputs")}
    assert left == _ids(db), "rows that only existed for pruned cases are removed with them"
    assert db.execute("SELECT COUNT(*) FROM typed_outcomes").fetchone()[0] == 1       # outcome records are never pruned
    assert db.execute("SELECT COUNT(*) FROM counterfactual_registrations").fetchone()[0] == 1


def test_pruning_works_when_population_tracking_was_never_configured(tmp_path, small):
    db = C.ledger(tmp_path / "inv.db")
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='population_events'").fetchone()
    _add_case(db, "plain", NOW - 60 * DAY)
    assert C.prune_candidates(db, NOW) == ["plain"]        # no 'no such table' failure inside a live step


def test_an_expired_reference_stops_protecting_a_case(tmp_path, small):
    db = C.ledger(tmp_path / "inv.db")
    _add_case(db, "late", NOW - 60 * DAY)
    db.execute("INSERT INTO typed_outcomes VALUES (?,?,?)", ("k", NOW - 31 * DAY, json.dumps({"case": "late"})))   # outcome itself expired
    db.commit()
    assert C.prune_candidates(db, NOW) == ["late"]


def test_nothing_younger_than_retention_is_ever_deleted_even_when_full(tmp_path, small):
    db = C.ledger(tmp_path / "inv.db")
    for n in range(7):
        _add_case(db, f"y{n}", NOW - (n + 1) * DAY)       # all terminal, all inside the 30-day window
    db.commit()
    assert C.capacity(db)["active_bytes"] > C.SOFT_BYTES
    report = C.prune_for_cap(db, NOW)
    assert report["deleted_cases"] == 0 and len(_ids(db)) == 7
    assert report["eligible_remaining"] == 0


def test_blocked_is_reported_at_the_hard_cap_and_clear_below_the_soft_cap(tmp_path, small, monkeypatch):
    db = C.ledger(tmp_path / "inv.db")
    for n in range(7):
        _add_case(db, f"o{n}", None, created_ms=NOW - n)  # open cases are protected
    db.commit()
    monkeypatch.setattr(C, "HARD_BYTES", C.capacity(db)["active_bytes"] - 1)
    assert C.prune_for_cap(db, NOW)["blocked"] is True
    monkeypatch.setattr(C, "HARD_BYTES", C.capacity(db)["active_bytes"] + 1)
    assert C.prune_for_cap(db, NOW)["blocked"] is False


def test_pruning_is_bounded_per_call(tmp_path, small):
    db = C.ledger(tmp_path / "inv.db")
    for n in range(8):
        _add_case(db, f"g{n}", NOW - (50 + n) * DAY)
    db.commit()
    report = C.prune_for_cap(db, NOW, batch=2, max_batches=1)
    assert report["deleted_cases"] == 2 and len(_ids(db)) == 6


@pytest.fixture
def paths(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network forbidden"))
    now = int(time.time() * 1000) // I.TF * I.TF + 60_000
    return tmp_path / "attention.db", tmp_path / "investigation.db", now


def test_a_full_database_refuses_registration_with_a_reason_instead_of_failing(paths, monkeypatch):
    source, dest, now = paths
    publish(source, now)
    monkeypatch.setattr(C, "HARD_BYTES", 0)               # as if active space were at the hard cap
    refused = C.step(source, dest, now)
    assert refused["registered"] == 0
    assert refused["skipped"].get("db_capacity", 0) > 0
    assert refused["status"] == "degraded" and refused["reason"] == "capacity_exhausted"
    assert refused["capacity"]["blocked"] is True
    monkeypatch.setattr(C, "HARD_BYTES", C.MAX_BYTES - 1024 ** 2)      # space is available again
    publish(source, now + 1000, "again")
    recovered = C.step(source, dest, now + 1000)
    assert recovered["registered"] >= 1 and "db_capacity" not in recovered["skipped"]
