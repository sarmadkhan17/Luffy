"""RES-02: normal generated questions, justified routes, no owner URLs.

Internal anomaly routing uses the same entry point as RES-01. External decay
routing uses the real shadow producer and worker CLI with offline transport.
"""
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from trader.cognition import external_sources as S
from trader.core.child import ChildResult
from trader.core.journal import Journal
from trader.observability import investigation_research as I
from trader.research import external_research as E
from tests.test_external_research_router import FakeChild
from tests.test_generated_question_handoff_negative_memory import produced
from tests.test_intelligence_spine import publish, table
from tests.test_investigation_state_feedback import paths  # noqa: F401
from tests.test_res01_question_producer import producer, root_for


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    import socket
    def forbidden(*args, **kwargs):
        pytest.fail("RES-02 network forbidden")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def anomaly(paths):
    root, src, dest, now = root_for(paths)
    publish(src, now)
    assert producer(root, now)["research"]["ok"] == 1
    (iid,), = table(dest, "SELECT id FROM cases")
    return root, dest, now, iid, I.chain(dest, iid)


def test_res01_generated_question_enters_internal_router_with_protocol_justification(paths):
    root, dest, now, iid, chain = anomaly(paths)
    q, plan = chain["question"], chain["plan"]
    assert plan["schema"] == I.PLAN_SCHEMA
    assert plan["question_id"] == q["question_id"]
    assert plan["question_sha256"] == I.sha256(I.canonical(q))
    assert plan["predicates_id"] == I.PREDICATES_ID
    for section, alternative in zip(plan["sections"][:3], q["protocol"]["alternatives"]):
        assert section["status"] == "ROUTED"
        assert section["hypothesis"] == alternative["name"]
        assert section["prediction"] == alternative["prediction"]
        assert section["invalidator"] == alternative["invalidator"]
        updates, candles = section["routes"]
        # The frozen volume hypothesis requires its exact forward measurement
        # and candle versions, rather than arbitrary external prose.
        assert updates["store"] == "investigation.updates"
        assert updates["locator"]["investigation_id"] == iid
        assert "evidence.target_versions" in updates["fields"]
        assert candles["store"] == "investigation.inputs"
        assert candles["locator"]["target_keys"] == q["protocol"]["target_keys"]
        assert "candle.volume" in candles["fields"]
    unavailable = plan["sections"][-1]
    assert unavailable["hypothesis"] == "participant_cause"
    assert unavailable["status"] == "UNAVAILABLE"
    assert unavailable["reason"] == I.PARTICIPANT_REASON
    assert unavailable["routes"] == []
    assert chain["runs"][0]["result"]["status"] == "INCONCLUSIVE"
    assert producer(root, now + 1)["research"]["attempted"] == 0
    assert I.chain(dest, iid) == chain


def test_anomaly_is_not_misrouted_to_unsupported_external_source_class(paths):
    _root, _dest, _now, _iid, chain = anomaly(paths)
    with pytest.raises(ValueError, match="unsupported_question_scope"):
        S.route(chain["question"])


def test_replay_refuses_substituted_manual_url_route(paths):
    _root, dest, _now, iid, chain = anomaly(paths)
    forged = dict(chain["plan"])
    forged["sections"] = [dict(chain["plan"]["sections"][0], routes=[{
        "store": "manual.urls", "urls": ["https://doi.org/10.1234/primed"]}])]
    forged.pop("plan_id")
    forged = I._with_id(forged, "plan_id")
    with sqlite3.connect(dest) as db:
        # Deliberate disk tampering also recomputes hashes; the verifier must
        # reject the substituted routing contract, not just a bad checksum.
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            db.execute('DROP TRIGGER "' + name + '"')
        encoded = I.canonical(forged)
        db.execute("UPDATE investigation_research_records SET record_id=?,canonical_sha256=?,canonical_json=? WHERE record_type=?",
                   (forged["plan_id"], I.sha256(encoded), encoded, I.PLAN_SCHEMA))
    with pytest.raises(I.ResearchRefused, match="plan_rederivation_mismatch"):
        I.chain(dest, iid)


def cli(source, reader, worker):
    return ["--enable", "--once", "--journal", str(worker.db_path),
            "--question-shadow-db", reader.shadow_path,
            "--question-source-db", str(source.db_path)]


@pytest.mark.parametrize("collection", ["COLLECTED", "EMPTY", "UNAVAILABLE"])
def test_generated_decay_question_enters_normal_worker_router_and_restarts(
        tmp_path, monkeypatch, capsys, collection):
    source, reader, worker = produced(tmp_path)
    child = (FakeChild([]) if collection == "EMPTY" else
             FakeChild(failure=ChildResult(ok=False, error="offline")) if collection == "UNAVAILABLE" else
             FakeChild())
    # Substitute only the bounded transport at the existing injection point.
    # The CLI, source adapter, router and research worker remain unchanged.
    monkeypatch.setitem(E.research_pass.__kwdefaults__, "child", child)
    try:
        original_question, provenance = reader.read(reader.candidates(1, set())[0]["question_id"])
        assert E.main(cli(source, reader, worker)) == 0
        result = json.loads(capsys.readouterr().out)
        bank = result["results"][0]["bank"]
        route = bank["plan"]["route"]
        assert bank["question"] == original_question
        assert bank["plan"]["question_source"] == provenance
        assert route["question_id"] == original_question["question_id"]
        assert route["question_sha256"] == S.digest(original_question)
        assert route["scope"]["kind"] == "strategy"
        assert original_question["question_kind"] == "strategy_decay"
        assert route["source_classes"] == ["academic_factors"]
        assert route["registry_sha256"] == S.REGISTRY_SHA256
        selected = route["sources"][0]
        assert selected == S.select("academic_factors")[0].record()
        assert selected["metadata"]["type"]["value"] == "scholarly_metadata"
        assert selected["metadata"]["credibility_class"]["state"] == "NOT_ASSESSED"
        assert bank["collection_status"] == collection
        assert bank["result_status"] == "INCONCLUSIVE"
        assert child.calls[0][0] == "search"
        assert set(child.calls[0][1]) == {"query", "limit"}
        assert child.calls[0][1]["query"] in route["queries"]
        assert worker.research_questions() == []
        row = worker.research_bank_object(bank["bank_object_id"])
        assert E.verify_bank(worker, row) == bank
        calls = list(child.calls)
        assert E.main(cli(source, reader, worker)) == 0
        assert json.loads(capsys.readouterr().out)["results"] == []
        assert child.calls == calls
        assert Journal(worker.db_path).research_bank_object(bank["bank_object_id"]) == row
        assert reader.read(original_question["question_id"]) == (original_question, provenance)
    finally:
        reader.close()


def test_manual_urls_cannot_replace_generated_question_source(tmp_path, capsys):
    worker = tmp_path / "worker.db"
    with pytest.raises(SystemExit) as refused:
        E.main(["--enable", "--once", "--journal", str(worker),
                "--urls", "https://doi.org/10.1234/primed"])
    assert refused.value.code == 2
    assert "unrecognized arguments: --urls" in capsys.readouterr().err
    with pytest.raises(SystemExit) as refused:
        E.main(["--enable", "--once", "--journal", str(worker)])
    assert refused.value.code == 2
    assert "requires both generated-question database paths" in capsys.readouterr().err
    assert not worker.exists()


def test_shadow_codec_fix_keeps_authority_and_network_imports_blocked():
    code = """
from trader.cognition import _research_shadow_child as child
assert child.install_import_guard() == []
from trader.core import evidence_zlib
raw = b'retained measurement evidence'
assert evidence_zlib.decompress(evidence_zlib.compress(raw), len(raw)) == raw
for name in child.AUTHORITY_MODULES + ('trader.learning', 'requests', 'socket', 'ccxt'):
    try:
        __import__(name)
    except ImportError:
        pass
    else:
        raise AssertionError('authority import allowed: ' + name)
assert child.loaded_forbidden() == []
"""
    completed = subprocess.run([sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        timeout=10)
    assert completed.returncode == 0, completed.stderr
