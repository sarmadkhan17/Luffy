"""Contextual investigation-slot allocation; synthetic, no network, LLM or trading."""
import ast
import json
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from trader.cognition import investigation as I
from trader.observability import attention as A, investigation as C
from trader.observability.store import Store
from tests.test_attention_telemetry import event, frames

SPIKES = {0: 100_000, 1: 50_000, 2: 20_000, 3: 10_000, 4: 5_000, 5: 2_500}
ALL = range(6)


@pytest.fixture
def paths(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network forbidden"))
    now = int(time.time()*1000)//I.TF*I.TF+60_000
    return tmp_path/"attention.db", tmp_path/"investigation.db", now


def publish(path, now, sid, spikes):
    """A scan whose volume spikes give a strict salience order S0 > S1 > ... ."""
    data = frames(6, now)
    for j in spikes:
        data[f'S{j}/USDT']['4h'].loc[29, 'volume'] = SPIKES[j]
    store = Store(path, A.settings())
    with patch('trader.observability.store.time', SimpleNamespace(time=lambda: now/1000)):
        ident = dict(schema='attention-scan-identity.v1', instance_id='1'*32, seq=now)
        captured = event(sid, now, data)
        store.write(dict(captured, identity=ident))
        store.write({"kind": "causes", "scan_id": sid, "as_of_ms": now, "identity": ident,
                     "items": [{"symbol": s, "decision_id": "d_"+s} for s in data]})
    store.close()
    path.with_name("attention_health.json").write_text(json.dumps({
        "health_schema": "attention-collector-health.v2", "instance_id": "1"*32,
        "updated_ms": now, "status": "ok", "worker_alive": True, "errors": 0,
        "capture_errors": 0, "worker_errors": 0, "dropped": 0, "details_lost": 0,
        "process_started_ms": 0, "failure_generation": 0, "fence_seq": 0, "certificate": None,
        "last_error": None, "first_error_ms": None, "last_error_ms": None,
        "last_complete": {"scan_id": sid, "seq": now}}))
    return captured


def scan_payload(path, sid):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT payload FROM scans WHERE scan_id=?", (sid,)).fetchone()[0]


def open_symbols(dest):
    with sqlite3.connect(dest) as db:
        return sorted(r[0] for r in db.execute("SELECT symbol FROM cases WHERE terminal_ms IS NULL"))


def decisions(dest):
    with sqlite3.connect(dest) as db:
        return db.execute("SELECT id,scan_id,payload FROM allocation_decisions ORDER BY decided_ms").fetchall()


def two_active(paths):
    """S0,S1 already investigated; then scan s2 ranks S0 > S1 > ... > S5."""
    source, dest, now = paths
    publish(source, now, "s1", [0, 1])
    assert C.step(source, dest, now)["registered"] == 2
    two_active.event = publish(source, now+1000, "s2", ALL)
    return now+1000


def snapshot(source, now):
    return C.adapt(C.H.bound_snapshot(source, now_ms=now)[0], now)


def test_lower_ranked_candidates_fill_slots_of_active_ones(paths):
    source, dest, _ = paths
    t = two_active(paths)
    r = C.step(source, dest, t)
    a = r["allocation"]
    # Multiple active top candidates are skipped without wasting capacity.
    assert a["legacy_selected"] == ["S0/USDT", "S1/USDT", "S2/USDT"]
    assert a["active_skipped"] == ["S0/USDT", "S1/USDT"]
    assert a["selected"] == ["S2/USDT", "S3/USDT", "S4/USDT"]
    assert r["registered"] == 3 and r["status"] == "ok" and not r["skipped"]
    assert open_symbols(dest) == [f"S{i}/USDT" for i in range(5)]


def test_no_active_investigation_is_exact_legacy_selection(paths):
    source, dest, now = paths
    publish(source, now, "s2", ALL)
    legacy = snapshot(source, now).result["selected"]
    r = C.step(source, dest, now)
    assert r["allocation"]["selected"] == r["allocation"]["legacy_selected"] == legacy
    assert r["allocation"]["active_skipped"] == []
    assert open_symbols(dest) == sorted(legacy)


def test_original_scan_rows_hash_and_replay_are_unchanged(paths):
    source, dest, _ = paths
    t = two_active(paths)
    raw = scan_payload(source, "s2")
    scan = json.loads(raw)
    replay = A.evaluate_snapshot(two_active.event)
    assert replay["rows"] == scan["rows"] and replay["market"] == scan["market"]
    snap = snapshot(source, t)
    rows = json.loads(json.dumps(snap.result["rows"]))
    C.step(source, dest, t)
    C.step(source, dest, t+1)
    assert scan_payload(source, "s2") == raw
    assert A.evaluate_snapshot(two_active.event) == replay
    assert snapshot(source, t+2).result["rows"] == rows
    body = json.loads(decisions(dest)[-1][2])
    assert body["source_scan_sha256"] == A.digest(scan)
    for sym, row in rows.items():               # salience, rank and reasons untouched
        assert row["reason"] == scan_row(scan, sym)["reason"]
        assert row.get("salience") == scan_row(scan, sym).get("salience")
        assert row.get("rank") == scan_row(scan, sym).get("rank")


def scan_row(scan, sym):
    return next(r for r in scan["rows"] if r["symbol"] == sym)


def test_retry_and_restart_are_idempotent_without_progressive_admission(paths):
    source, dest, _ = paths
    t = two_active(paths)
    first = C.step(source, dest, t)
    stored = decisions(dest)
    # A recompute against the post-registration ledger WOULD admit S5.
    snap = snapshot(source, t+1)
    with closing_ledger(dest) as db:
        rows = db.execute("SELECT id,episode_id,symbol,created_ms,payload FROM cases WHERE terminal_ms IS NULL").fetchall()
        state = C.ledger_state(rows, 5, 0)
    assert C.allocate(snap, state, "x", t+1)["selected"] == ["S5/USDT"]
    for i in (1, 2, 3):                         # each step reopens the ledger: a restart
        again = C.step(source, dest, t+i)
        assert again["allocation"]["reused"] is True and again["allocation"]["execution"] == "execution_closed"
        assert again["allocation"]["decision_id"] == first["allocation"]["decision_id"]
        assert again["allocation"]["selected"] == first["allocation"]["selected"]
        assert again["registered"] == 0
    assert "S5/USDT" not in open_symbols(dest)
    assert decisions(dest) == stored            # later ledger writes never touch it


def closing_ledger(dest):
    from contextlib import closing
    db = sqlite3.connect(dest)
    db.row_factory = sqlite3.Row
    return closing(db)


def test_later_scan_does_not_mutate_historical_decision(paths):
    source, dest, _ = paths
    t = two_active(paths)
    C.step(source, dest, t)
    stored = decisions(dest)
    publish(source, t+1000, "s3", ALL)
    r = C.step(source, dest, t+1000)
    assert r["allocation"]["reused"] is False and r["allocation"]["selected"] == ["S5/USDT"]
    assert decisions(dest)[:len(stored)] == stored
    old = json.loads(stored[-1][2])
    assert old["ledger_state"]["active_count"] == 2
    assert [a["symbol"] for a in old["ledger_state"]["active"]] == ["S0/USDT", "S1/USDT"]


def test_decision_hash_is_deterministic(paths):
    source, dest, _ = paths
    t = two_active(paths)
    snap = snapshot(source, t)
    with closing_ledger(dest) as db:
        rows = db.execute("SELECT id,episode_id,symbol,created_ms,payload FROM cases WHERE terminal_ms IS NULL").fetchall()
        s1, s2 = C.ledger_state(rows, 2, 0), C.ledger_state(list(reversed(rows)), 2, 0)
    a, b = C.allocate(snap, s1, "h", t), C.allocate(snap, s2, "h", t)
    assert a == b and a["decision_id"] == A.digest({k: v for k, v in a.items() if k != "decision_id"})
    assert C.allocate(snap, s1, "h", t+1)["decision_id"] != a["decision_id"]


def test_active_investigations_keep_advancing_and_close(paths):
    source, dest, _ = paths
    t = two_active(paths)
    before = {d["investigation"]["investigation_id"]: len(d["updates"]) for d in C.dossiers(dest)}
    C.step(source, dest, t)
    end = max(d["investigation"]["measurement"]["deadline_ms"] for d in C.dossiers(dest))
    publish(source, end+1, "final", [])
    r = C.step(source, dest, end+1)
    assert r["updated"] == 5 and open_symbols(dest) == []
    for d in C.dossiers(dest):
        if d["investigation"]["investigation_id"] in before:
            assert len(d["updates"]) > before[d["investigation"]["investigation_id"]]
        assert d["updates"][-1]["evidence"]["status"] == "measured"


def test_closed_episode_semantics_unchanged(paths):
    source, dest, _ = paths
    t = two_active(paths)
    with sqlite3.connect(dest) as db:
        db.execute("UPDATE cases SET terminal_ms=? WHERE symbol='S0/USDT'", (t,))
    r = C.step(source, dest, t)
    # A closed case is not active: S0 is selectable, but its same-anchor episode stays closed.
    assert r["allocation"]["active_skipped"] == ["S1/USDT"]
    assert r["allocation"]["selected"] == ["S0/USDT", "S2/USDT", "S3/USDT"]
    assert r["skipped"] == {"existing_closed_episode": 1} and r["registered"] == 2
    assert open_symbols(dest) == ["S1/USDT", "S2/USDT", "S3/USDT"]


def test_capacity_limits_still_enforced(paths, monkeypatch):
    source, dest, _ = paths
    t = two_active(paths)
    monkeypatch.setattr(C, "MAX_ACTIVE", 3)
    r = C.step(source, dest, t)
    assert r["registered"] == 1 and r["skipped"] == {"active_capacity": 2}
    assert r["reason"] == "capacity_exhausted" and open_symbols(dest) == ["S0/USDT", "S1/USDT", "S2/USDT"]
    assert json.loads(decisions(dest)[-1][2])["capacity"]["active_available"] == 1


def cases(dest):
    with sqlite3.connect(dest) as db:
        return db.execute("SELECT COUNT(*) FROM cases").fetchone()[0]


def blocked_first_execution(paths, monkeypatch):
    """s2 executes once with room for one: S2 registers, S3/S4 are capacity-blocked."""
    source, dest, _ = paths
    t = two_active(paths)
    monkeypatch.setattr(C, "MAX_ACTIVE", 3)
    first = C.step(source, dest, t)
    assert first["allocation"]["execution"] == "executed" and first["allocation"]["reused"] is False
    assert first["registered"] == 1 and first["skipped"] == {"active_capacity": 2}
    assert open_symbols(dest) == ["S0/USDT", "S1/USDT", "S2/USDT"]
    return t, first


def assert_closed_retry(r, first):
    a = r["allocation"]
    assert a["reused"] is True and a["execution"] == "execution_closed"
    assert a["decision_id"] == first["allocation"]["decision_id"] and a["selected"] == first["allocation"]["selected"]
    assert r["registered"] == 0 and not r["skipped"]


def test_retry_of_executed_decision_registers_nothing(paths, monkeypatch):
    source, dest, _ = paths
    t, first = blocked_first_execution(paths, monkeypatch)
    stored, n = decisions(dest), cases(dest)
    for i in (1, 2, 3):                          # each step reopens the ledger: a restart
        assert_closed_retry(C.step(source, dest, t+i), first)
    assert cases(dest) == n and decisions(dest) == stored


def test_freed_capacity_is_not_spent_by_the_old_decision(paths, monkeypatch):
    source, dest, _ = paths
    t, first = blocked_first_execution(paths, monkeypatch)
    monkeypatch.setattr(C, "MAX_ACTIVE", 50)     # capacity opens after the decision cut
    assert_closed_retry(C.step(source, dest, t+1), first)
    assert open_symbols(dest) == ["S0/USDT", "S1/USDT", "S2/USDT"]


def test_closing_a_first_execution_case_does_not_readmit_old_candidates(paths, monkeypatch):
    source, dest, _ = paths
    t, first = blocked_first_execution(paths, monkeypatch)
    with sqlite3.connect(dest) as db:            # S2's case closes, freeing its slot
        db.execute("UPDATE cases SET terminal_ms=? WHERE symbol='S2/USDT'", (t,))
    r = C.step(source, dest, t+1)
    assert_closed_retry(r, first)
    assert open_symbols(dest) == ["S0/USDT", "S1/USDT"]


def test_new_scan_may_use_newly_available_capacity(paths, monkeypatch):
    source, dest, _ = paths
    t, first = blocked_first_execution(paths, monkeypatch)
    monkeypatch.setattr(C, "MAX_ACTIVE", 50)
    assert_closed_retry(C.step(source, dest, t+1), first)
    publish(source, t+1000, "s3", ALL)
    r = C.step(source, dest, t+1000)
    assert r["allocation"]["reused"] is False and r["allocation"]["execution"] == "executed"
    assert r["allocation"]["selected"] == ["S3/USDT", "S4/USDT", "S5/USDT"] and r["registered"] == 3
    assert open_symbols(dest) == [f"S{i}/USDT" for i in range(6)]


def test_rollback_before_commit_leaves_no_decision_or_registration(paths, monkeypatch):
    source, dest, _ = paths
    t = two_active(paths)
    n = cases(dest)
    real = C._append
    def crash(db, update):
        if update.investigation_id not in {r for r, in db.execute(
                "SELECT id FROM cases WHERE symbol IN ('S0/USDT','S1/USDT')")}:
            raise RuntimeError("crash_mid_execution")
        return real(db, update)
    monkeypatch.setattr(C, "_append", crash)
    with pytest.raises(RuntimeError, match="crash_mid_execution"):
        C.step(source, dest, t)
    assert len(decisions(dest)) == 1 and cases(dest) == n
    assert open_symbols(dest) == ["S0/USDT", "S1/USDT"]
    monkeypatch.setattr(C, "_append", real)
    r = C.step(source, dest, t+1)                # the decision may now legitimately be made
    assert r["allocation"]["reused"] is False and r["allocation"]["execution"] == "executed"
    assert r["allocation"]["selected"] == ["S2/USDT", "S3/USDT", "S4/USDT"] and r["registered"] == 3
    assert len(decisions(dest)) == 2


def test_active_investigations_advance_while_old_decision_is_closed(paths, monkeypatch):
    source, dest, _ = paths
    t, first = blocked_first_execution(paths, monkeypatch)
    monkeypatch.setattr(C, "MAX_ACTIVE", 50)
    before = {d["investigation"]["investigation_id"]: len(d["updates"]) for d in C.dossiers(dest)}
    expires = max(d["investigation"]["measurement"]["expires_ms"] for d in C.dossiers(dest))
    monkeypatch.setattr(C, "FRESH_MS", expires + 10**9)   # re-read the same s2 scan much later
    r = C.step(source, dest, expires+1)
    assert_closed_retry(r, first)
    assert r["updated"] == 3 and open_symbols(dest) == []
    for d in C.dossiers(dest):
        assert len(d["updates"]) == before[d["investigation"]["investigation_id"]] + 1
        assert d["updates"][-1]["evidence"]["status"] != "unresolved"


def test_missing_ledger_evidence_fails_closed(paths, monkeypatch):
    source, dest, _ = paths
    t = two_active(paths)
    with pytest.raises(C.LedgerRefused, match="ledger_evidence_missing"):
        C.ledger_state(None, 0, 0)
    with pytest.raises(C.LedgerRefused, match="ledger_evidence_missing"):
        C.allocate(snapshot(source, t), None, "h", t)
    def unavailable(*_args):
        raise C.LedgerRefused("ledger_evidence_missing")
    monkeypatch.setattr(C, "ledger_state", unavailable)
    r = C.step(source, dest, t)
    assert r["registered"] == 0 and r["status"] == "degraded" and r["reason"] == "ledger_evidence_missing"
    assert "allocation" not in r and len(decisions(dest)) == 1 and open_symbols(dest) == ["S0/USDT", "S1/USDT"]


def test_corrupt_or_refused_ledger_evidence_fails_closed(paths):
    source, dest, _ = paths
    t = two_active(paths)
    with closing_ledger(dest) as db:
        rows = [dict(r) for r in db.execute("SELECT id,episode_id,symbol,created_ms,payload FROM cases")]
    with pytest.raises(C.LedgerRefused, match="corrupt"):
        C.ledger_state([dict(rows[0], payload="{not json")], 2, 0)
    with pytest.raises(C.LedgerRefused, match="inconsistent"):
        C.ledger_state([rows[0], dict(rows[0])], 2, 0)
    with sqlite3.connect(dest) as db:            # column no longer matches its payload
        db.execute("UPDATE cases SET created_ms=created_ms+1 WHERE symbol='S1/USDT'")
    r = C.step(source, dest, t)
    assert r["registered"] == 0 and r["reason"] == "ledger_evidence_inconsistent"
    assert len(decisions(dest)) == 1 and open_symbols(dest) == ["S0/USDT", "S1/USDT"]
    with sqlite3.connect(dest) as db:            # a tampered frozen decision is refused
        db.execute("UPDATE cases SET created_ms=created_ms-1 WHERE symbol='S1/USDT'")
    C.step(source, dest, t+1)
    with sqlite3.connect(dest) as db:
        db.execute("UPDATE allocation_decisions SET payload=replace(payload,'S4/USDT','S5/USDT') WHERE scan_id='s2'")
    r = C.step(source, dest, t+2)
    assert r["reason"] == "allocation_decision_corrupt" and r["registered"] == 0


def test_scan_processed_before_policy_keeps_legacy_selection(paths):
    source, dest, _ = paths
    t = two_active(paths)
    C.step(source, dest, t)
    with sqlite3.connect(dest) as db:             # as if s2 ran under the old consumer
        db.execute("DELETE FROM allocation_decisions WHERE scan_id='s2'")
    r = C.step(source, dest, t+1)
    assert r["allocation"]["refusal"] == "scan_processed_before_policy"
    assert r["allocation"]["selected"] == r["allocation"]["legacy_selected"] and r["registered"] == 0


def population_config(tmp_path, now):
    from tests.test_pit_dataset import declaration as make_declaration
    from trader.cognition import dataset as D
    declaration = make_declaration([f'S{i}/USDT' for i in range(6)], now+86_400_000,
                                   start=now-10_000, mode='forward')
    freeze = D.freeze(declaration, now-20_000, code_manifest={'fixture': 'synthetic'})
    a, b = tmp_path/'declaration.json', tmp_path/'freeze.json'
    a.write_text(json.dumps(declaration)); b.write_text(json.dumps(freeze))
    return {'declaration': str(a), 'receipt': str(b), 'export_directory': str(tmp_path/'exports')}


def exported(cfg):
    return {p.name: p.read_bytes() for p in Path(cfg['export_directory']).glob('*/*.json')}


def test_declared_cohort_before_policy_is_refused_and_receipts_untouched(paths, tmp_path):
    source, dest, now = paths
    cfg = population_config(tmp_path, now)
    publish(source, now, "s1", [0, 1])
    C.step(source, dest, now, cfg)
    with sqlite3.connect(dest) as db:             # cohort observed legacy allocation
        db.execute("DELETE FROM allocation_bindings")
        before = db.execute("SELECT id,hash,payload FROM population_events ORDER BY id").fetchall()
    files = exported(cfg)
    publish(source, now+1000, "s2", ALL)
    r = C.step(source, dest, now+1000, cfg)
    assert r["allocation"]["refusal"] == "declared_cohort_predates_policy"
    assert r["allocation"]["selected"] == ["S0/USDT", "S1/USDT", "S2/USDT"]
    assert r["registered"] == 1 and open_symbols(dest) == ["S0/USDT", "S1/USDT", "S2/USDT"]
    with sqlite3.connect(dest) as db:
        after = {row[0]: row for row in db.execute("SELECT id,hash,payload FROM population_events")}
    for row in before:                            # byte-for-byte where still pending
        assert after[row[0]][1] == row[1] and (row[2] is None or after[row[0]][2] == row[2])
    new = exported(cfg)
    assert all(new[name] == data for name, data in files.items())


def test_new_declared_cohort_binds_policy_and_selection_is_distinct(paths, tmp_path):
    source, dest, _ = paths
    cfg = population_config(tmp_path, paths[2])
    publish(source, paths[2], "s1", [0, 1])
    C.step(source, dest, paths[2], cfg)
    publish(source, paths[2]+1000, "s2", ALL)
    r = C.step(source, dest, paths[2]+1000, cfg)
    assert r["allocation"]["refusal"] is None and r["allocation"]["selected"] == ["S2/USDT", "S3/USDT", "S4/USDT"]
    scans = [json.loads(v) for v in exported(cfg).values() if json.loads(v)['kind'] == 'population']
    rows = {row['symbol']: row for row in max(scans, key=lambda e: e['source']['observed_ms'])['source']['rows']}
    # Scanner selection/reasons stay as scanned; only the consumer's reason differs.
    assert rows['S0/USDT']['selected'] is True and rows['S0/USDT']['reason'] == 'selected'
    assert rows['S0/USDT']['registration_reason'] == 'active_investigation'
    assert rows['S3/USDT']['selected'] is False and rows['S3/USDT']['registration_reason'] == 'registered'


def test_no_llm_venue_or_trading_imports():
    tree = ast.parse(Path(C.__file__).read_text())
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    banned = ("trader.brain", "trader.engine", "trader.execution", "trader.kernel",
              "ccxt", "anthropic", "openai", "requests", "httpx", "urllib", "trader.data")
    assert not [m for m in mods if m.startswith(banned)]
