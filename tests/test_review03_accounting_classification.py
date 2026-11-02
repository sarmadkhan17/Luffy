"""Accounting worker: permanent-evidence classification and bounded error text."""
import json
import sqlite3

import pytest

from trader.engine import trade_accounting as A
from trader.observability import accounting as W


def _db(tmp_path, attempts):
    db = sqlite3.connect(tmp_path / "queue.db")
    W.initialize(db)
    db.execute("INSERT INTO jobs(trade_id,status,due_ms,attempts) VALUES ('t','running',0,?)", (attempts,))
    path = tmp_path / "a.json"
    path.write_text(json.dumps({"bookings": {"trade": {"id": "t"}}}))
    db.execute("INSERT INTO attempts(id,trade_id,path,status,started_ms) VALUES ('a','t',?,'running',0)", (str(path),))
    db.commit()
    return db, ("a", "t", str(path))


def _finish(tmp_path, monkeypatch, attempts, reasons):
    db, attempt = _db(tmp_path, attempts)
    monkeypatch.setattr(A, "replay", lambda a: dict(learning_eligible=False, reasons=list(reasons)))
    W.finish(db, attempt, 1000)
    return db.execute("SELECT status FROM jobs WHERE trade_id='t'").fetchone()[0]


def test_conflict_reason_leaves_the_due_queue_after_the_budget(tmp_path, monkeypatch):
    assert _finish(tmp_path, monkeypatch, 3, ["whole_trade_fill_balance_conflict"]) == "conflict"


def test_conflict_reason_is_still_retried_inside_the_budget(tmp_path, monkeypatch):
    assert _finish(tmp_path, monkeypatch, 1, ["whole_trade_fill_balance_conflict"]) == "retry"


@pytest.mark.parametrize("reasons", [["fill_history_empty_retry"],
                                     ["capture_failed:TimeoutError"],
                                     ["whole_trade_fill_balance_conflict", "unowned_or_missing_fills_retry"]])
def test_transient_reasons_are_never_terminal(tmp_path, monkeypatch, reasons):
    assert _finish(tmp_path, monkeypatch, 50, reasons) == "retry"


def test_conflict_jobs_are_not_selected_as_due(tmp_path):
    from trader.core.journal import Journal
    j = Journal(tmp_path / "j.db")
    q = tmp_path / "q"
    q.mkdir()
    db = sqlite3.connect(q / "queue.db")
    W.initialize(db)
    db.execute("INSERT INTO jobs(trade_id,status,due_ms,attempts) VALUES ('t','conflict',0,3)")
    db.commit()
    db.close()
    calls = []
    health = W.step(j.db_path, q, lambda: calls.append(1), now_ms=10 ** 12)
    assert calls == [] and health["attempted"] == 0 and health["jobs"] == {"conflict": 1}


def test_delivery_never_stores_artifact_or_exception_text(tmp_path, monkeypatch):
    from trader.learning import producers
    db = sqlite3.connect(tmp_path / "q.db")
    W.initialize(db)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json with SECRET-token")
    db.execute("INSERT INTO attempts(id,trade_id,path,status,started_ms) VALUES ('a','t',?,'complete',0)", (str(bad),))
    db.commit()
    W.deliver_completed(db, journal=None)
    out = db.execute("SELECT outcome_id FROM learning_deliveries").fetchone()[0]
    assert out == "UNBOUND:artifact_unreadable:JSONDecodeError" and "SECRET" not in out
    # producer ValueError: only a lowercase code is stored
    # deliver_completed now resolves coordinated-group membership first, from a real journal and an artifact shaped
    # like production's (bookings.trade.id); no such trade row exists, so the attempt is ungrouped and reaches the
    # producer, which is what this half of the test exercises.
    from trader.core.journal import Journal
    journal_path = Journal(tmp_path / "j.db").db_path
    good = tmp_path / "g.json"
    good.write_text(json.dumps({"bookings": {"trade": {"id": "t2"}}}))
    db.execute("INSERT INTO attempts(id,trade_id,path,status,started_ms) VALUES ('b','t2',?,'complete',1)", (str(good),))
    db.commit()
    monkeypatch.setattr(producers, "deliver_accounting",
                        lambda j, a: (_ for _ in ()).throw(ValueError("https://x?key=SECRET")))
    W.deliver_completed(db, journal=journal_path)
    out = db.execute("SELECT outcome_id FROM learning_deliveries WHERE attempt_id='b'").fetchone()[0]
    assert out == "UNBOUND:value_error"
