"""RES-01: offline events through the unmocked, bounded producer entry point.

Only its data root and clock are redirected. No case/question registration,
URL priming, Kernel, provider, or research producer replacement is used.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from trader.cognition import investigation as I
from trader.observability import investigation as C, investigation_research as R
from tests.test_intelligence_spine import publish, scan_of, table
from tests.test_investigation_state_feedback import paths  # noqa: F401


def producer(directory, now, *, research=True, enabled=True):
    # A fresh interpreter on every invocation proves restart, rather than just
    # repeated calls against an in-memory question registry.
    code = """
import socket, sys
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError('RES-01 network forbidden')
socket.socket.connect = forbidden
socket.create_connection = forbidden
from trader.core import config
config.ROOT = Path(sys.argv[1])
from trader.observability import investigation as consumer
now_ms = int(sys.argv[2])
consumer.time.time = lambda: now_ms / 1000
sys.argv = ['investigation', '--once'] + sys.argv[3:]
raise SystemExit(consumer.main())
"""
    flags = (["--enable"] if enabled else []) + (
        ["--research-max-cases", "1"] if research else [])
    completed = subprocess.run(
        [sys.executable, "-c", code, str(directory), str(now), *flags],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        timeout=15)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return json.loads(completed.stdout)


def root_for(paths):
    src, _dest, now = paths
    # main uses ROOT/data; the fixture directory is exclusively this test's.
    root = src.parent / "root"
    data = root / "data"
    data.mkdir(parents=True)
    return root, data / "attention.db", data / "investigation.db", now


def retained(dest):
    return table(dest, "SELECT record_type,record_id,canonical_json "
                       "FROM investigation_research_records ORDER BY rowid")


def test_supported_event_generates_typed_question_and_survives_restart(paths):
    root, src, dest, now = root_for(paths)
    event = publish(src, now)
    result = producer(root, now)
    assert result["registered"] == 1
    assert result["research"] == {"attempted": 1, "ok": 1, "refused": {}}
    (iid, payload), = table(dest, "SELECT id,payload FROM cases")
    case = I.investigation_from_dict(json.loads(payload))
    chain = R.chain(dest, iid)
    q = chain["question"]
    assert q["schema"] == R.QUESTION_SCHEMA
    assert q["question_kind"] == R.FAMILY
    assert q["source_family"] == "volume_anomaly"
    assert q["question"] == case.question == I.QUESTIONS["volume_anomaly"]
    source = q["source"]
    assert source["investigation_id"] == iid
    assert source["state_id"] == case.state.state_id
    assert source["episode_id"] == case.episode_id
    assert source["catalog_id"] == I.CATALOG_ID
    assert source["case_payload_sha256"] == R.sha256(payload)
    assert source["registration_context_id"] == C.context_for(dest, iid).context_id
    assert source["registration_update_id"] == table(
        dest, "SELECT id FROM updates WHERE case_id=? ORDER BY rowid LIMIT 1", iid)[0][0]
    assert source["trace_id"] == table(
        dest, "SELECT id FROM intelligence_traces WHERE investigation_id=?", iid)[0][0]
    assert q["context"]["attention"]["scan_id"] == event["scan_id"]
    assert q["context"]["attention"]["scan_sha256"] == R.sha256(table(
        src, "SELECT payload FROM scans WHERE scan_id=?", event["scan_id"])[0][0])
    assert q["context"]["world_model"]["model_id"] == scan_of(src)["world_model"]["model_id"]
    assert q["protocol"]["horizon_bars"] == 5
    # A real question about unresolved evidence must not imply a result.
    assert chain["runs"][0]["result"]["status"] == "INCONCLUSIVE"
    assert {p["status"] for p in chain["runs"][0]["result"]["paths"]} == {"NOT_ASSESSED"}
    before = retained(dest)
    restarted = producer(root, now + 1)
    assert restarted["research"] == {"attempted": 0, "ok": 0, "refused": {}}
    assert retained(dest) == before
    assert R.chain(dest, iid)["question"] == q


@pytest.mark.parametrize("evidence", ["quiet", "missing_source", "missing_health"])
def test_no_supported_event_produces_no_question(paths, evidence):
    root, src, dest, now = root_for(paths)
    if evidence != "missing_source":
        publish(src, now, spikes=())
    if evidence == "missing_health":
        src.with_name("attention_health.json").unlink()
    result = producer(root, now)
    assert result["registered"] == 0
    assert result["research"] == {"attempted": 0, "ok": 0, "refused": {}}
    assert retained(dest) == []
    if evidence != "quiet":
        assert result["status"] == "degraded"
        assert result["reason"]


def test_missing_registration_evidence_is_refused_without_question(paths):
    root, src, dest, now = root_for(paths)
    publish(src, now)
    assert producer(root, now, research=False)["registered"] == 1
    with sqlite3.connect(dest) as db:
        db.execute("DELETE FROM opportunity_contexts")
    result = producer(root, now + 1)
    assert result["research"]["attempted"] == 1
    assert result["research"]["ok"] == 0
    assert sum(result["research"]["refused"].values()) == 1
    assert all(reason.startswith("registration_context_unverified:")
               for reason in result["research"]["refused"])
    assert retained(dest) == []


def test_disabled_producer_does_not_fabricate_work(paths):
    root, src, dest, now = root_for(paths)
    publish(src, now)
    assert producer(root, now, enabled=False) == {"status": "disabled"}
    assert not dest.exists()
