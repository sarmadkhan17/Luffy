"""Isolated ledger/consumer tests, no production activation or network."""
import json
import sqlite3
import time

import pytest

from trader.cognition import investigation as I
from trader.observability import investigation as C
from tests.test_attention_learning import publish, update_health


@pytest.fixture
def paths(tmp_path, monkeypatch):
    import socket
    monkeypatch.setattr(socket.socket, "connect", lambda *a: pytest.fail("network forbidden"))
    now = int(time.time()*1000)//I.TF*I.TF+60_000
    return tmp_path/"attention.db", tmp_path/"investigation.db", now


def test_persistence_restart_no_repeated_scan_or_episode(paths):
    source, dest, now = paths
    publish(source, now)
    assert C.step(source, dest, now)["registered"] >= 1
    before = C.dossiers(dest)
    assert C.step(source, dest, now+1)["registered"] == 0
    assert C.dossiers(dest) == before
    publish(source, now+1000, "repeat")
    assert C.step(source, dest, now+1000)["registered"] == 0
    assert C.dossiers(dest) == before
    with sqlite3.connect(dest) as db:
        assert db.execute("SELECT COUNT(*) FROM inputs").fetchone()[0] > 0
        assert db.execute("SELECT activated_ms FROM protocols").fetchone()[0] == now


@pytest.mark.parametrize("offset,reason", [(-300001,"snapshot_stale"), (1,"snapshot_future"), (-1,"awaiting_post_activation_scan")])
def test_pre_activation_stale_future_refused(paths, offset, reason):
    source, dest, now = paths
    publish(source, now+offset)
    update_health(source, updated_ms=now)
    r = C.step(source, dest, now)
    assert r["registered"] == 0 and r["reason"] == reason


def test_source_unreadable_and_expiry_are_explicit(paths):
    source, dest, now = paths
    assert C.step(source, dest, now)["status"] == "degraded"
    publish(source, now+1)
    C.step(source, dest, now+1)
    before = C.dossiers(dest)
    expiry = max(d["investigation"]["measurement"]["expires_ms"] for d in before)
    r = C.step(source, dest, expiry+1)
    assert r["active"] == 0
    after = C.dossiers(dest)
    for d in after:
        assert d["updates"][-1]["next_action"]["kind"] == "UNASSESSABLE"
        assert d["updates"][-1]["reason_codes"][0] == "missing_data_expired"
        assert d["updates"][0] in next(x for x in before if x["investigation"] == d["investigation"])["updates"]


def test_capacity_retention_and_diagnostic_bounds(paths, monkeypatch):
    source, dest, now = paths
    publish(source, now)
    monkeypatch.setattr(C, "MAX_ACTIVE", 0)
    result = C.step(source, dest, now)
    assert result["reason"] == "capacity_exhausted" and not result["registered"]
    with sqlite3.connect(dest) as db:
        db.executemany("INSERT INTO diagnostics VALUES (?,?)", [(now,"{}")]*520)
    C.step(source, dest, now+1)
    with sqlite3.connect(dest) as db:
        assert db.execute("SELECT COUNT(*) FROM diagnostics").fetchone()[0] == 512
    monkeypatch.setattr(C, "MAX_ACTIVE", 32)
    # The capacity-blocked scan's decision is execution-closed; only a new scan may use freed capacity.
    assert C.step(source, dest, now+2)["registered"] == 0
    publish(source, now+3, "s2")
    assert C.step(source, dest, now+3)["registered"] == 1
    expiry = max(d["investigation"]["measurement"]["expires_ms"] for d in C.dossiers(dest))
    C.step(source, dest, expiry+1)
    C.step(source, dest, expiry+C.RETENTION_MS+2)
    with sqlite3.connect(dest) as db:
        for table in ("cases", "updates", "case_inputs", "inputs"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_partial_and_complete_window_persist_immutable_updates(paths):
    source, dest, now = paths
    publish(source, now)
    C.step(source, dest, now)
    original = C.dossiers(dest)
    end = original[0]["investigation"]["measurement"]["deadline_ms"]
    publish(source, end-I.TF+1, "partial")
    C.step(source, dest, end-I.TF+1)
    partial = C.dossiers(dest)
    for d in partial:
        assert d["updates"][-1]["evidence"]["status"] == "unresolved"
        assert d["updates"][-1]["evidence"]["score"] is None
    publish(source, end+1, "complete")
    C.step(source, dest, end+1)
    for old in original:
        current = next(d for d in C.dossiers(dest) if d["investigation"] == old["investigation"])
        assert current["updates"][0] == old["updates"][0]
        assert current["updates"][-1]["evidence"]["status"] == "measured"
        assert current["updates"][-1]["next_action"]["kind"] == "ACQUIRE"


def test_timeout_rolls_back_registration(paths, monkeypatch):
    source, dest, now = paths
    publish(source, now)
    monkeypatch.setattr(I, 'MAX_RUNTIME_MS', 0)
    with pytest.raises(TimeoutError):
        C.step(source, dest, now)
    with sqlite3.connect(dest) as db:
        assert db.execute('SELECT COUNT(*) FROM cases').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM updates').fetchone()[0] == 0


def test_collector_failure_and_write_lock_are_visible(paths):
    source, dest, now = paths
    publish(source, now)
    update_health(source,status='error')
    assert C.step(source, dest, now)['reason'] == 'collector_failing'
    with sqlite3.connect(dest) as db:
        db.execute('BEGIN EXCLUSIVE')
        with pytest.raises(sqlite3.OperationalError):
            C.step(source, dest, now)


def test_update_budget_retains_terminal_slot(paths, monkeypatch):
    source, dest, now = paths
    publish(source, now); C.step(source, dest, now)
    first = C.dossiers(dest)[0]
    end = first['investigation']['measurement']['deadline_ms']
    monkeypatch.setattr(C, 'MAX_CASE_UPDATES', 2)
    publish(source, end-I.TF+1, 'partial')
    r=C.step(source,dest,end-I.TF+1)
    assert r['skipped']['case_update_capacity'] >= 1
    publish(source,end+1,'final')
    C.step(source,dest,end+1)
    case=next(d for d in C.dossiers(dest) if d['investigation']==first['investigation'])
    assert len(case['updates'])==2 and case['updates'][-1]['evidence']['status']=='measured'


def test_reader_rejects_oversized_source_and_does_not_create_missing_file(paths):
    source,dest,now=paths
    with pytest.raises(sqlite3.OperationalError): C.source_snapshot(source)
    assert not source.exists()
    publish(source,now)
    with sqlite3.connect(source) as db:
        db.execute('UPDATE scans SET payload=?',('x'*(2*1024**2+1),))
    with pytest.raises(ValueError,match='source_payload_bound'): C.source_snapshot(source)


def test_watchdog_isolated_opt_in(tmp_path):
    import subprocess
    from pathlib import Path
    script=Path('scripts/watchdog.sh').read_text()
    root=tmp_path/'isolated';(root/'scripts').mkdir(parents=True);(root/'data').mkdir()
    (root/'scripts/watchdog.sh').write_text(script)
    (root/'data/investigation.enabled').touch()
    r=subprocess.run(['bash',str(root/'scripts/watchdog.sh')],env={'PATH':'/usr/bin:/bin','DRY_RUN':'1'},capture_output=True,text=True,timeout=10)
    assert r.returncode==0,r.stderr
    log=(root/'logs/watchdog.log').read_text()
    assert 'timeout 25s ./venv/bin/python -m trader.observability.investigation --once --enable' in log
    assert not (root/'data/investigation.db').exists()


def test_health_retains_last_success_failure_and_capacity_counts(tmp_path):
    path=tmp_path/'investigation_health.json'
    C.publish_health(path,{'status':'ok','source_scan_id':'good','updated_ms':1000})
    failed=C.publish_health(path,{'status':'degraded','updated_ms':2000,'source_scan_id':'bad','skipped':{'active_capacity':2,'existing_episode':3}})
    assert failed['last_successful_scan_id']=='good' and failed['last_successful_ms']==1000
    assert failed['failed_invocations']==1 and failed['deferred_updates']==2
    recovered=C.publish_health(path,{'status':'ok','updated_ms':3000,'source_scan_id':'new'})
    assert recovered['failed_invocations']==1 and recovered['last_successful_scan_id']=='new'
    assert recovered['limits']['db_bytes']==C.MAX_BYTES
    assert json.loads(path.read_text())==recovered
