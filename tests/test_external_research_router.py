"""Offline acceptance proof: normal registered questions, no real network/spend."""
from dataclasses import replace
import ast
import json
from pathlib import Path
import time

import pytest

from tests.test_strategy_decay_research_run import _fresh
from trader.cognition import external_sources as S, research_question as Q, research_bank as B
from trader.core.child import ChildResult, run_child
from trader.core.journal import Journal
from trader.research import external_research as R


def registered(tmp_path):
    j = _fresh(tmp_path)
    Q.record_from_journal(j, now_ms=1)
    return j


def metadata(doi="10.1234/one", title="Trading strategy performance decay", abstract=None):
    return dict(doi=doi, title=title, url="https://doi.org/" + doi, abstract=abstract, type="journal-article")


class FakeChild:
    def __init__(self, items=None, failure=None):
        self.calls = []
        self.items = items if items is not None else [metadata()]
        self.failure = failure

    def __call__(self, fn, operation, arguments, max_bytes, timeout, *, timeout_s):
        self.calls.append((operation, arguments, max_bytes, timeout_s))
        if self.failure:
            return self.failure
        value = (self.items if operation == "search" else metadata(
            arguments["doi"], abstract="<p>Trading strategy performance decay may depend on financial market regimes. "
            "This claim is unverified and requires an independent quantitative falsification protocol.</p>"))
        return ChildResult(ok=True, value=dict(status="OK" if value else "EMPTY", reason=None,
            metadata=value, bytes=200, content_sha256="a" * 64,
            url=R.request_url(operation, arguments), http_status=200))


def test_normal_question_no_owner_urls_router_provenance_and_bank(tmp_path):
    j, child = registered(tmp_path), FakeChild()
    before = {t: j.query("SELECT * FROM " + t) for t in ("strategies", "decisions", "trades", "research_questions")}
    result = R.research_pass(j, child=child)
    bank = result["results"][0]["bank"]
    assert bank["plan"]["route"]["source_classes"] == ["academic_factors"]
    assert [c[0] for c in child.calls] == ["search", "retrieve"]
    assert bank["question"] == Q.load(j)[0]
    assert bank["collection_status"] == "COLLECTED"
    assert bank["result_status"] == "INCONCLUSIVE" and bank["authority"] == "RESEARCH_ONLY"
    evidence = bank["evidence"][0]
    assert evidence["source_url"] == "https://doi.org/10.1234/one"
    assert evidence["question_id"] == bank["question"]["question_id"]
    assert evidence["query_provenance"] == [bank["searches"][0]["request_id"]]
    assert evidence["available_ms"] >= evidence["retrieved_at_ms"] > 0
    assert evidence["extraction_status"] == "EXTRACTED_UNVERIFIED"
    assert evidence["passage_sha256"] == R.sha(evidence["passage"])
    assert evidence["source_sha256"] == S.digest(S.source(evidence["source_id"]).record())
    assert bank["limitations"] and bank["next_questions"] and bank["cost"]["paid_cost_usd"] == 0
    assert R.verify_bank(j, j.research_bank_object(bank["bank_object_id"])) == bank
    assert {t: j.query("SELECT * FROM " + t) for t in before} == before
    assert B.load(j) == []  # same table, independent verified contract families
    assert j.research_bank_objects(schema=R.BANK_SCHEMA)[0]["bank_object_id"] == bank["bank_object_id"]


def test_registry_reuses_all_metadata_fields_and_does_not_invent_unknowns():
    src = S.select("academic_factors")[0]
    assert set(src.metadata) == set(S.S.METADATA_FIELDS)
    assert src.metadata["historical_depth"]["value"] is None
    assert src.metadata["rate_limits"]["value"] is None
    assert src.metadata["credibility_class"]["value"] is None
    changed = src.metadata
    changed["cost"]["value"]["paid_request_usd"] = 100
    assert S.source(src.source_id).metadata["cost"]["value"]["paid_request_usd"] == 0
    assert S.S.registry()["schema"] == "research-source-registry.v1"


def test_unsupported_and_paid_fail_closed(tmp_path):
    q = Q.load(registered(tmp_path))[0]
    with pytest.raises(ValueError, match="unsupported_question_scope"):
        S.route(dict(q, question_kind="invented"))
    with pytest.raises(ValueError, match="unsupported_source_class"):
        S.select("made_up")
    with pytest.raises(ValueError, match="unsupported_external_source"):
        S.source("paid.vendor")
    broker = R.SearchBroker(Journal(tmp_path / "other.db"), R.Bounds())
    with pytest.raises(ValueError, match="source_definition_mismatch"):
        broker.request(replace(S.select("academic_factors")[0], metadata_json="{}"), "search", {})


def test_metadata_filter_dedup_before_retrieval_and_content_dedup(tmp_path):
    child = FakeChild([metadata(), metadata(), metadata("10.1234/two", "Financial trading strategy persistence"),
                       metadata("10.1234/noise", "Biological cell proliferation")])
    bank = R.research_pass(registered(tmp_path), child=child)["results"][0]["bank"]
    assert [c[0] for c in child.calls] == ["search", "retrieve", "retrieve"]
    assert bank["filtering"]["metadata_rejected"] == 2
    assert [e["extraction_status"] for e in bank["evidence"]] == ["EXTRACTED_UNVERIFIED", "DUPLICATE_CONTENT"]


def test_restart_no_repeat_network_or_evidence_work(tmp_path):
    j, child = registered(tmp_path), FakeChild()
    first = R.research_pass(j, child=child)
    rows = j.research_bank_objects(schema=R.BANK_SCHEMA)
    assert R.research_pass(Journal(j.db_path), child=child)["results"] == []
    assert len(child.calls) == 2
    assert Journal(j.db_path).research_bank_objects(schema=R.BANK_SCHEMA) == rows
    q = first["results"][0]["bank"]["question"]
    assert R._collect(j, q, R.Bounds(), child)["status"] == "DUPLICATE"
    assert len(child.calls) == 2


def test_crash_after_claim_recovery_is_explicit_and_does_not_retry(tmp_path):
    j = registered(tmp_path)
    def crash(*args, **kwargs):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        R.research_pass(j, child=crash)
    child = FakeChild()
    bank = R.research_pass(Journal(j.db_path), child=child)["results"][0]["bank"]
    assert bank["collection_status"] == "UNAVAILABLE" and not child.calls
    assert bank["searches"][0]["reason"] == "INTERRUPTED_REQUEST_NOT_RETRIED"


@pytest.mark.parametrize("failure, expected", [(ChildResult(ok=False,timed_out=True), "retrieval_timeout"),
                                               (ChildResult(ok=False,error="broken"), "retrieval_child_failed")])
def test_failed_retrieval_is_negative_bank_memory(tmp_path, failure, expected):
    j = registered(tmp_path)
    bank = R.research_pass(j, child=FakeChild(failure=failure))["results"][0]["bank"]
    assert bank["collection_status"] == "UNAVAILABLE"
    assert bank["searches"][0]["reason"] == expected
    assert R.research_pass(j, child=FakeChild())["results"] == []


def test_empty_is_distinct_from_failed_and_saved(tmp_path):
    bank = R.research_pass(registered(tmp_path), child=FakeChild([]))["results"][0]["bank"]
    assert bank["collection_status"] == "EMPTY" and bank["searches"][0]["status"] == "EMPTY"
    assert bank["extracted_claims"]["status"] == "NOT_AVAILABLE"


@pytest.mark.parametrize("kw", [dict(max_queries=3), dict(max_pages=9), dict(max_metadata=21),
    dict(max_response_bytes=10000000), dict(max_seconds=float("nan")), dict(max_seconds=61),
    dict(max_cost_usd=.01), dict(max_pages=True)])
def test_invalid_bounds_refuse(kw):
    with pytest.raises(ValueError):
        R.Bounds(**kw)


def test_query_page_passage_and_time_limits(tmp_path):
    child = FakeChild([metadata("10.1234/"+str(i), "Trading strategy performance decay " + str(i)) for i in range(10)])
    bank = R.research_pass(registered(tmp_path), bounds=R.Bounds(max_pages=1,max_passage_chars=128), child=child)["results"][0]["bank"]
    assert len(child.calls) == 2 and len(bank["evidence"]) == 1
    assert len(bank["evidence"][0]["passage"]) <= 128
    assert bank["filtering"]["pages_omitted"] == 9
    broker = R.SearchBroker(Journal(tmp_path / "bound.db"), R.Bounds(), child=child)
    broker.deadline = time.monotonic()-1
    assert broker.request(S.select("academic_factors")[0], "search", dict(query="a",limit=1))["reason"] == "request_time_or_count_bound"
    assert len(child.calls) == 2


def hang():
    time.sleep(5)


def test_existing_child_runner_hard_deadline():
    started = time.monotonic()
    result = run_child(hang, timeout_s=.2)
    assert result.timed_out and time.monotonic() - started < 3


def test_cli_disabled_never_initializes_journal_or_network(tmp_path, capsys):
    path = tmp_path / "absent.db"
    assert R.main(["--journal", str(path), "--once"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "DISABLED"
    assert not path.exists()


def test_transport_byte_bound_redirect_and_absent_abstract(monkeypatch):
    import requests
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def iter_content(self, n): return iter([b"a"*2048])
    class Session:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, **kw):
            assert kw["allow_redirects"] is False and kw["stream"] is True
            assert self.trust_env is False
            return Response()
    monkeypatch.setattr(requests, "Session", Session)
    result = R.crossref_request("search", dict(query="decay",limit=1), 1024, 1)
    assert result["reason"] == "response_byte_bound"
    Response.status_code = 302
    assert R.crossref_request("search", dict(query="decay",limit=1), 1024, 1)["reason"] == "http_status:302"
    assert R._metadata({"DOI":"10.1234/one", "title":["Trading strategy decay"]})["abstract"] is None


def test_checkpoint_and_bank_corruption_refused(tmp_path):
    j = registered(tmp_path)
    bank = R.research_pass(j, child=FakeChild())["results"][0]["bank"]
    row = j.research_bank_object(bank["bank_object_id"])
    with pytest.raises(ValueError, match="external_bank_integrity"):
        R.verify_bank(j, dict(row,canonical_sha256="0"*64))
    with j._tx() as db:
        db.execute("UPDATE brain_events SET detail='{}' WHERE kind=?", (R.DONE,))
    with pytest.raises((ValueError, KeyError)):
        R._checkpoint(j, R.DONE, bank["searches"][0]["request_id"])


def test_predictive_bridge_refuses_external_bank_and_live_has_no_worker(tmp_path):
    from trader.research import predictive_bridge as P
    j = registered(tmp_path)
    R.research_pass(j, child=FakeChild())
    assert P.read_sources(j.db_path, set(), 1, int(time.time()*1000))[0] == []
    root = Path(R.__file__).parents[1]
    for path in (root / "kernel.py", root / "engine/orchestrator.py", root / "engine/risk.py", root / "engine/executor.py"):
        assert "external_research" not in path.read_text()
    imports = [n.module for n in ast.walk(ast.parse(Path(R.__file__).read_text())) if isinstance(n,ast.ImportFrom)]
    assert not any(x and ("strategy" in x or "engine" in x or "predictive" in x) for x in imports)


def test_existing_bank_family_and_recall_remain_verifiable(tmp_path):
    from trader.cognition import research_run as Run, research_recall as Recall
    j = registered(tmp_path)
    Run.run(j, "test-existing-bank", 2)
    B.record_from_journal(j, now_ms=3)
    existing = B.load(j)
    assert existing
    q = Q.load(j)[0]
    prior = Recall.recall(j, q["question_id"], max_objects=4)
    R.research_pass(j, child=FakeChild())
    assert B.load(j) == existing
    assert Recall.recall(j, q["question_id"], max_objects=4) == prior
    assert len(j.query("SELECT * FROM research_bank_objects")) == len(existing) + 1


def test_transport_parses_metadata_and_detail_with_exact_hash(monkeypatch):
    import requests
    search_body = {"message":{"items":[{"DOI":"10.1234/one", "title":["Trading strategy decay"]}]}}
    class Response:
        status_code = 200
        def __init__(self, body): self.data = json.dumps(body).encode()
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def iter_content(self, n): return iter([self.data])
    class Session:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def get(self, url, **kw):
            if "?" in url: return Response(search_body)
            return Response({"message":search_body["message"]["items"][0]})
    monkeypatch.setattr(requests, "Session", Session)
    found = R.crossref_request("search", dict(query="strategy decay",limit=1), 1024, 1)
    assert found["status"] == "OK" and found["metadata"][0]["doi"] == "10.1234/one"
    assert found["content_sha256"] == R.sha(json.dumps(search_body))
    page = R.crossref_request("retrieve", dict(doi="10.1234/one"), 1024, 1)
    assert page["status"] == "OK" and page["metadata"]["abstract"] is None


def test_storage_capacity_and_concurrent_worker_bounds(tmp_path, monkeypatch):
    import fcntl
    j = registered(tmp_path)
    with open(str(j.db_path)+".external-research.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert R.research_pass(j, child=FakeChild())["status"] == "BUSY"
    monkeypatch.setattr(R,"MAX_CHECKPOINTS",1)
    child = FakeChild()
    result = R.research_pass(j,child=child)["results"][0]
    assert result["status"] == "REFUSED" and result["reason"] == "checkpoint_capacity"
    assert child.calls == []


def test_url_dedup_preserves_meaning_and_rejects_unknown_hosts():
    assert R.normalize_url("https://doi.org/10.1234/one/?a=1&utm_source=x#part") == "https://doi.org/10.1234/one?a=1"
    with pytest.raises(ValueError): R.normalize_url("https://evil.example/10.1234/one")
    with pytest.raises(ValueError): R.normalize_url("http://doi.org/10.1234/one")
