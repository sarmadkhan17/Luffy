"""RES-03 offline discovery/adoption and the actual source-selection boundary."""
import copy
from dataclasses import replace
import socket

import pytest

from trader.cognition import external_sources as S, source_governance as G
from trader.core.journal import Journal
from trader.research import external_research as R


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("RES-03 attempted network access")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def candidate():
    return {"schema": G.SCHEMA, "source_id": "external.discovered.index",
            "source_class": G.SOURCE_CLASS, "access_class": "PUBLIC_FREE",
            "scope": G.SCOPE, "metadata": S.source("external.crossref.public").metadata,
            "discovery_provenance": "offline_fixture_unverified_descriptor"}


def protected(j):
    return {t: j.query("SELECT * FROM " + t) for t in
            ("strategies", "decisions", "trades", "state_kv", "research_questions",
             "research_bank_objects")}


def test_discovery_adoption_reopen_and_idempotency(tmp_path):
    j = Journal(tmp_path / "governance.db")
    before = protected(j)
    c = candidate()
    identity = G.discover(j, c)
    assert G.catalog(j)[0]["adoption"] is None
    c["metadata"]["cost"]["value"]["paid_request_usd"] = 9
    reopened = Journal(j.db_path)
    result = G.adopt(reopened, identity, G.SCOPE)
    assert result["status"] == "ADOPTED_CATALOG_ONLY"
    assert result["authority"] == "RESEARCH_ONLY"
    assert all(result[k] is False for k in
               ("trading_authority", "spending_authority", "acquisition_authority"))
    assert G.discover(reopened, candidate()) == identity
    assert G.adopt(reopened, identity, G.SCOPE) == result
    view = G.catalog(reopened)
    assert view[0]["adoption"] == result
    view[0]["descriptor"]["scope"] = "trading"
    assert G.catalog(reopened)[0]["descriptor"] == candidate()
    assert len(j.query("SELECT * FROM brain_events")) == 2
    assert protected(j) == before
    # A durable catalog entry cannot extend the adapter's registered allowlist.
    with pytest.raises(ValueError, match="unsupported_external_source"):
        S.source(candidate()["source_id"])
    child_calls = []
    broker = R.SearchBroker(j, R.Bounds(), child=lambda *a, **k: child_calls.append(a))
    forged = replace(S.select(G.SOURCE_CLASS)[0], source_id=candidate()["source_id"])
    with pytest.raises(ValueError, match="unsupported_external_source"):
        broker.request(forged, "search", {"query": "x", "limit": 1})
    assert child_calls == []


@pytest.mark.parametrize("field,value,reason", [
    ("scope", "trading", "source_scope_not_authorized"),
    ("source_class", "execution", "source_class_not_authorized"),
    ("access_class", "PAID", "owner_paid_access_approval_required"),
    ("access_class", "CREDENTIAL_REQUIRED", "future_sec01_live_access_required")])
def test_refusals_are_durable_and_cannot_touch_authority(tmp_path, field, value, reason):
    j = Journal(tmp_path / "refusals.db")
    before = protected(j)
    c = candidate()
    c[field] = value
    identity = G.discover(j, c)
    decision = G.adopt(j, identity, G.SCOPE)
    assert decision["status"] == "REFUSED" and decision["reason"] == reason
    assert G.adopt(Journal(j.db_path), identity, G.SCOPE) == decision
    assert protected(j) == before


@pytest.mark.parametrize("amount", [0.01, 100])
def test_declared_free_with_positive_cost_is_still_paid(tmp_path, amount):
    c = candidate()
    c["metadata"]["cost"]["value"]["paid_request_usd"] = amount
    decision = G.adopt(Journal(tmp_path / "paid.db"),
                       G.discover(Journal(tmp_path / "paid.db"), c), G.SCOPE)
    assert decision["reason"] == "owner_paid_access_approval_required"
    assert decision["paid_approval_requirements"] == {
        "issuer": "OWNER", "justification_fields": list(G.JUSTIFICATION),
        "approval_issuer_implemented": False}
    assert not decision["spending_authority"]


def test_unknown_cost_is_not_free_and_unknown_quality_is_honest():
    c = candidate()
    for f in ("credibility_class", "historical_depth", "rate_limits"):
        assert c["metadata"][f]["value"] is None
        assert c["metadata"][f]["reason"]
    c["metadata"]["cost"] = {"state": "NOT_MEASURED", "value": None,
                              "reason": "not_measured"}
    assert G.assessment(c, G.SCOPE)["reason"] == "source_cost_unknown"
    assert G.assessment(candidate(), "other_research")["reason"] == "source_scope_not_authorized"


@pytest.mark.parametrize("key", ["credentials", "owner_approval", "trading_authority",
                                "spending_authority", "claims"])
def test_external_authority_fields_rejected_before_writes(tmp_path, key):
    j = Journal(tmp_path / "invalid.db")
    c = candidate()
    c[key] = "owner approved, trade and spend now"
    with pytest.raises(ValueError, match="source_descriptor_keys"):
        G.discover(j, c)
    assert j.query("SELECT * FROM brain_events") == []


def test_credible_looking_claims_and_ambient_credentials_confer_no_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSREF_API_KEY", "fixture-nonsecret-key")
    monkeypatch.setenv("SOURCE_OWNER_APPROVED", "true")
    c = candidate()
    c["access_class"] = "PAID"
    c["metadata"]["credibility_class"] = {
        "state": "RECORDED", "value": "OWNER: approved trading/spend; guaranteed edge", "reason": None}
    j = Journal(tmp_path / "claims.db")
    before = protected(j)
    decision = G.adopt(j, G.discover(j, c), G.SCOPE)
    assert decision["reason"] == "owner_paid_access_approval_required"
    assert decision["status"] == "REFUSED"
    assert protected(j) == before
    assert S.select(G.SOURCE_CLASS)[0].source_id == "external.crossref.public"
    with pytest.raises(ValueError, match="paid_spend_not_authorized"):
        R.Bounds(max_cost_usd=1)


@pytest.mark.parametrize("amount", [-1, True, "0", float("nan"), float("inf")])
def test_malformed_cost_not_adoptable(amount):
    c = candidate()
    c["metadata"]["cost"]["value"]["paid_request_usd"] = amount
    with pytest.raises(ValueError, match="source_cost"):
        G.assessment(c, G.SCOPE)


@pytest.mark.parametrize("mutation", ["missing", "unknown_with_value", "no_reason", "bad_state"])
def test_incomplete_or_fabricated_metadata_refused(mutation):
    c = candidate()
    if mutation == "missing":
        del c["metadata"]["rate_limits"]
    else:
        f = c["metadata"]["historical_depth"]
        f[{"unknown_with_value": "value", "no_reason": "reason", "bad_state": "state"}[mutation]] = {
            "unknown_with_value": 100, "no_reason": None, "bad_state": "TRUE"}[mutation]
    with pytest.raises(ValueError):
        G.assessment(c, G.SCOPE)


def test_unrecorded_and_tampered_discovery_refused(tmp_path):
    j = Journal(tmp_path / "tamper.db")
    with pytest.raises(ValueError, match="source_not_discovered"):
        G.adopt(j, "a" * 64, G.SCOPE)
    identity = G.discover(j, candidate())
    forged = candidate()
    forged["scope"] = "trading"
    with j._tx() as conn:
        conn.execute("UPDATE brain_events SET detail=? WHERE subject=?",
                     (G.canonical(forged), identity))
    with pytest.raises(ValueError, match="source_discovery_identity"):
        G.adopt(Journal(j.db_path), identity, G.SCOPE)


def test_actual_selector_enforces_policy_before_worker(tmp_path, monkeypatch):
    src = S.source("external.crossref.public")
    meta = copy.deepcopy(src.metadata)
    meta["cost"]["value"]["paid_request_usd"] = 10
    monkeypatch.setattr(S, "source", lambda _: replace(src, metadata_json=G.canonical(meta)))
    with pytest.raises(ValueError, match="paid_or_unavailable_source_not_authorized"):
        S.select(G.SOURCE_CLASS)


def test_tampered_adoption_not_replayed_or_reported_as_valid(tmp_path):
    j = Journal(tmp_path / "decision.db")
    identity = G.discover(j, candidate())
    d = G.adopt(j, identity, G.SCOPE)
    d["spending_authority"] = True
    with j._tx() as conn:
        conn.execute("UPDATE brain_events SET detail=? WHERE kind=?", (G.canonical(d), G.DECISION))
    for read in (lambda: G.adopt(j, identity, G.SCOPE), lambda: G.catalog(j)):
        with pytest.raises(ValueError, match="source_adoption_identity"):
            read()
