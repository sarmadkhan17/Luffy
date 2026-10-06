"""RES-04 supported question path; offline transports and zero reasoning."""
import copy
import socket
import sqlite3

import pytest

from tests.test_external_research_router import FakeChild, metadata, registered
from trader.core.child import ChildResult
from trader.research import external_research as R


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def refused(*args, **kwargs):
        pytest.fail("unexpected provider/network work")
    monkeypatch.setattr(socket.socket, "connect", refused)
    monkeypatch.setattr(socket, "create_connection", refused)
    from trader.brain.llm import BrainLLM
    for method in ("chat", "chat_tools", "chat_json"):
        monkeypatch.setattr(BrainLLM, method, refused)


class Abstracts(FakeChild):
    def __init__(self, abstracts):
        self.abstracts = abstracts
        super().__init__([metadata(doi, "Trading strategy persistence " + doi)
                          for doi in abstracts])

    def __call__(self, fn, operation, arguments, max_bytes, timeout, *, timeout_s):
        if operation == "search":
            return super().__call__(fn, operation, arguments, max_bytes, timeout, timeout_s=timeout_s)
        self.calls.append((operation, arguments, max_bytes, timeout_s))
        return ChildResult(ok=True, value=dict(status="OK", reason=None,
            metadata=metadata(arguments["doi"], abstract=self.abstracts[arguments["doi"]]),
            bytes=200, content_sha256="b"*64, url=R.request_url(operation,arguments), http_status=200))


def collect(tmp_path, abstracts, **bounds):
    j = registered(tmp_path)
    child = Abstracts(abstracts)
    bank = R.research_pass(j, child=child, bounds=R.Bounds(**bounds))["results"][0]["bank"]
    return j, child, bank


def test_equivalent_rendered_content_is_deduplicated(tmp_path):
    j, child, bank = collect(tmp_path, {
        "10.1234/one": "<p>Trading strategy persistence depends on market regimes.</p>",
        "10.1234/two": "<div>Trading <b>strategy</b> persistence depends on market regimes.</div>"})
    assert [e["extraction_status"] for e in bank["evidence"]] == ["EXTRACTED_UNVERIFIED", "DUPLICATE_CONTENT"]
    assert bank["evidence"][1]["passage"] is None
    assert bank["evidence"][1]["duplicate_of"] == bank["evidence"][0]["request_id"]
    assert bank["cost"]["llm_calls"] == 0
    before = len(child.calls)
    assert R.research_pass(j,child=child)["results"] == []
    assert len(child.calls) == before


def test_extract_relevant_passage_after_irrelevant_prefix(tmp_path):
    _, _, bank = collect(tmp_path, {"10.1234/one": "<p>Biological cell proliferation. </p>" * 20 +
        "<p>Trading strategy decay depends on financial market regimes.</p>"}, max_passage_chars=128)
    passage = bank["evidence"][0]["passage"]
    assert "Trading strategy decay" in passage
    assert "Biological" not in passage
    assert len(passage) <= 128


def test_irrelevant_abstract_is_explicitly_insufficient(tmp_path):
    _, _, bank = collect(tmp_path, {"10.1234/one": "<p>Biological cell proliferation.</p>"})
    assert bank["evidence"][0]["extraction_status"] == "NO_RELEVANT_PASSAGE"
    assert bank["evidence"][0]["passage"] is None
    assert bank["result_status"] == "INCONCLUSIVE"


def test_large_single_paragraph_has_relevant_bounded_window(tmp_path):
    _, _, bank = collect(tmp_path, {"10.1234/one": "unrelated introduction "*200 +
        "Trading strategy decay depends on financial market regimes."}, max_passage_chars=128)
    assert "Trading strategy decay" in bank["evidence"][0]["passage"]


def test_repeated_search_results_do_not_repeat_retrieval(tmp_path):
    j = registered(tmp_path)
    child = FakeChild([metadata(),metadata(),metadata("10.1234/noise", "Biology")])
    bank = R.research_pass(j,child=child,bounds=R.Bounds(max_queries=2))["results"][0]["bank"]
    assert [c[0] for c in child.calls] == ["search","search","retrieve"]
    assert len(bank["evidence"]) == 1 and len(bank["evidence"][0]["query_provenance"]) == 2
    assert bank["filtering"] == dict(metadata_seen=6,metadata_rejected=5,selected=1,pages_omitted=0)
    assert R.verify_bank(j,j.research_bank_object(bank["bank_object_id"])) == bank


def test_legacy_builder_receipt_remains_verifiable(tmp_path):
    j, _, bank = collect(tmp_path, {"10.1234/one": "Trading strategy performance decay."})
    legacy = copy.deepcopy(bank)
    legacy.pop("preprocessing")
    legacy["limitations"] = R.LIMITATIONS[:3]
    legacy["builder_id"] = legacy["plan"]["schema"] = R.LEGACY_BUILDER
    legacy["plan_id"] = R.sha(R.canonical(legacy["plan"]))
    legacy["evidence"][0].update(R.extraction(legacy["evidence"][0],R.Bounds(),{},legacy=True))
    legacy["evidence_id"] = R.sha(R.canonical(dict(searches=legacy["searches"],evidence=legacy["evidence"])))
    legacy["result_id"] = R.sha(R.canonical(dict(plan_id=legacy["plan_id"],evidence_id=legacy["evidence_id"],status=legacy["collection_status"])))
    del legacy["bank_object_id"]
    legacy["bank_object_id"] = R.sha(R.canonical(legacy))
    assert j.record_research_bank_object(R._row(legacy),recorded_at_ms=1) == "inserted"
    assert R.verify_bank(j,j.research_bank_object(legacy["bank_object_id"])) == legacy


@pytest.mark.parametrize("mutation", ["query", "missing_query", "url", "passage", "limits"])
def test_rehashed_registered_bank_cannot_drop_provenance_or_preprocessing(tmp_path, mutation):
    j, _, bank = collect(tmp_path, {"10.1234/one": "Trading strategy decay depends on market regimes."})
    forged = copy.deepcopy(bank)
    e = forged["evidence"][0]
    if mutation == "query": e["query_provenance"] = ["f"*64]
    if mutation == "missing_query": e["query_provenance"] = []
    if mutation == "url": e["source_url"] = "https://doi.org/10.1234/forged"
    if mutation == "passage":
        e["passage"] = "Biological cell proliferation."
        e["passage_sha256"] = R.sha(e["passage"])
    if mutation == "limits": forged["limitations"] = []
    forged["evidence_id"] = R.sha(R.canonical(dict(searches=forged["searches"],evidence=forged["evidence"])))
    forged["result_id"] = R.sha(R.canonical(dict(plan_id=forged["plan_id"],evidence_id=forged["evidence_id"],status=forged["collection_status"])))
    del forged["bank_object_id"]
    forged["bank_object_id"] = R.sha(R.canonical(forged))
    row = R._row(forged)
    # Rewrite the local fixture's protected row and registration deliberately,
    # so a hash or insertion conflict cannot substitute for semantic refusal.
    with sqlite3.connect(j.db_path) as conn:
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
            conn.execute('DROP TRIGGER "' + name + '"')
        conn.execute("DELETE FROM research_bank_objects WHERE bank_object_id=?", (bank["bank_object_id"],))
    assert j.record_research_bank_object(row,recorded_at_ms=1) == "inserted"
    with pytest.raises(ValueError):
        R.verify_bank(j,j.research_bank_object(forged["bank_object_id"]))
