"""research-source-registry.v1 — frozen internal Research Source Registry
(trader/cognition/research_sources.py) and research-plan.v1 reading its
source descriptors from it without any change to plan output."""
import ast
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_plan import (CF, D, EF, I, W,
                                                     _FullHist)
from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.cognition import research_sources as rs
from trader.strategy import health_observation as ho

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/research_plan_v1_pre_source_registry.json"
#: fixture captured from research_plan.py before the registry existed
FIXTURE_SHA256 = (
    "452559229c478776f6ed26e2f07210de65794053c6ba917f03d9e69b4875f39c")
VERDICTS = {"W": W, "D": D, "I": I, "EF": EF, "CF": CF}

#: the descriptors research_plan.py hard-coded before the registry
PRE_REGISTRY_DESCRIPTORS = {
    rs.HEALTH_SOURCE_ID: {"store": "journal.brain_events",
                          "reader": "Journal.strategy_health_rows",
                          "record_schema": "strategy-health-observation.v1"},
    rs.QUESTION_SOURCE_ID: {"store": "journal.research_questions",
                            "reader": "Journal.research_questions",
                            "record_schema": "research-question.v1"},
    rs.DECISIONS_SOURCE_ID: {"store": "journal.decisions",
                             "reader": "Journal.decision_observation_rows",
                             "record_schema": None},
}
PRE_REGISTRY_REGIME_DETAIL = (
    "no truthful WorldModel regime source: the live Kernel supplies no "
    "WorldModel and no point-in-time regime state is persisted for strategy "
    "health sweeps; Analyst regime_filter/regime_fitness are backtest filter "
    "settings, not an observed regime state")


def _fixture():
    text = FIXTURE.read_text()
    assert hashlib.sha256(text.encode()).hexdigest() == FIXTURE_SHA256
    return json.loads(text)


def _plans_now(verdicts):
    rows = _FullHist().seq("s1", verdicts).rows
    d = rq.derive(rows)
    assert d.questions and not d.refusals
    return [rp.canonical(rp.build(rq.canonical(q), rows))
            for q in d.questions]


def _evidence(plan_text):
    return [e for s in json.loads(plan_text)["hypotheses"]
            for e in s["evidence"]]


# ── 1 canonical registry is deterministic ────────────────────────────────
def test_canonical_registry_is_deterministic_and_pinned():
    text = rs.canonical_registry()
    assert text == rs.canonical_registry()
    assert rs.validate(rs.registry()) == text
    assert rs.canonical(json.loads(text)) == text
    assert hashlib.sha256(text.encode()).hexdigest() == rs.REGISTRY_SHA256
    reg = rs.registry()
    assert reg["schema"] == rs.SCHEMA == "research-source-registry.v1"
    ids = [e["source_id"] for e in reg["sources"]]
    assert ids == sorted(ids) == list(rs.source_ids())
    assert ids == ["internal.research_questions",
                   "internal.strategy_health_observations",
                   "internal.strategy_signal_decisions",
                   "internal.worldmodel_regime_state"]


def test_exact_nested_keys_and_types():
    for e in rs.registry()["sources"]:
        assert set(e) == set(rs._ENTRY_KEYS)
        assert set(e["metadata"]) == set(rs.METADATA_FIELDS)
        for f in e["metadata"].values():
            assert set(f) == {"state", "value", "reason"}
            assert f["state"] in rs.FACT_STATES
        if e["status"] == rs.AVAILABLE:
            assert set(e["plan_descriptor"]) == {"store", "reader",
                                                 "record_schema"}
            assert e["unavailable"] is None
            assert e["metadata"]["access_method"]["value"] \
                == e["plan_descriptor"]["reader"]


def test_returned_copies_cannot_change_the_registry():
    before = rs.canonical_registry()
    rs.registry()["sources"].clear()
    rs.get(rs.HEALTH_SOURCE_ID)["status"] = rs.UNAVAILABLE
    rs.descriptor(rs.HEALTH_SOURCE_ID)["store"] = "journal.trades"
    assert rs.canonical_registry() == before
    assert (rs.descriptor(rs.HEALTH_SOURCE_ID)["store"]
            == "journal.brain_events")


def test_frozen_text_changed_at_runtime_fails_closed(monkeypatch):
    monkeypatch.setattr(rs, "_FROZEN", rs._FROZEN.replace(
        "journal.decisions", "journal.trades"))
    for call in (rs.registry, rs.canonical_registry, rs.source_ids,
                 lambda: rs.get(rs.DECISIONS_SOURCE_ID),
                 lambda: rs.descriptor(rs.DECISIONS_SOURCE_ID)):
        with pytest.raises(rs.ResearchSourceError, match="pin_mismatch"):
            call()


# ── 2 all existing plan sources are registered ───────────────────────────
def test_registry_reproduces_pre_refactor_descriptors_exactly():
    for sid, desc in PRE_REGISTRY_DESCRIPTORS.items():
        assert rs.canonical(rs.descriptor(sid)) == rs.canonical(desc)
    assert rp._HEALTH == PRE_REGISTRY_DESCRIPTORS[rs.HEALTH_SOURCE_ID]
    assert rp._QUESTION == PRE_REGISTRY_DESCRIPTORS[rs.QUESTION_SOURCE_ID]
    assert rp._DECISIONS == PRE_REGISTRY_DESCRIPTORS[rs.DECISIONS_SOURCE_ID]
    assert rs.descriptor(rs.HEALTH_SOURCE_ID)["record_schema"] == ho.SCHEMA
    assert rs.descriptor(rs.QUESTION_SOURCE_ID)["record_schema"] == rq.SCHEMA


def test_every_plan_evidence_descriptor_resolves_through_registry():
    seen = set()
    for scen in _fixture().values():
        for p in scen["plans"]:
            for e in _evidence(p["canonical_json"]):
                seen.add(rs.resolve({k: e[k] for k in rs._DESCRIPTOR_KEYS}))
    assert seen == set(PRE_REGISTRY_DESCRIPTORS)


# ── 3 WorldModel/regime source is UNAVAILABLE ────────────────────────────
def test_worldmodel_regime_source_is_unavailable_with_exact_reason():
    e = rs.get(rs.WORLDMODEL_REGIME_SOURCE_ID)
    assert e["status"] == rs.UNAVAILABLE
    assert e["plan_descriptor"] is None           # no fake store/reader
    assert e["unavailable"] == {"reason": "no_truthful_worldmodel_source",
                                "detail": PRE_REGISTRY_REGIME_DETAIL}
    assert all(f == {"state": rs.UNAVAILABLE, "value": None,
                     "reason": "no_truthful_worldmodel_source"}
               for f in e["metadata"].values())
    with pytest.raises(rs.ResearchSourceError,
                       match="source_unavailable:internal.worldmodel_regime"
                             "_state:no_truthful_worldmodel_source"):
        rs.descriptor(rs.WORLDMODEL_REGIME_SOURCE_ID)
    assert rp.NO_TRUTHFUL_WORLDMODEL_SOURCE == "no_truthful_worldmodel_source"
    assert rp.REGIME_UNAVAILABLE_DETAIL == PRE_REGISTRY_REGIME_DETAIL
    [plan] = _plans_now([W, D])
    [regime] = [s for s in json.loads(plan)["hypotheses"]
                if s["hypothesis"] == rp.TEMPORARY_REGIME_ABSENCE]
    assert regime == {"hypothesis": rp.TEMPORARY_REGIME_ABSENCE,
                      "status": rp.UNAVAILABLE,
                      "unavailable_reason": "no_truthful_worldmodel_source",
                      "unavailable_detail": PRE_REGISTRY_REGIME_DETAIL,
                      "evidence": []}


# ── 4 no invented credibility / cost / rate-limit / depth ────────────────
def test_no_unsupported_metadata_is_recorded():
    for e in rs.registry()["sources"]:
        for f in rs.UNSUPPORTED_FIELDS:
            fact = e["metadata"][f]
            assert fact["state"] != rs.RECORDED and fact["value"] is None
            assert fact["reason"]
    avail = [e for e in rs.registry()["sources"]
             if e["status"] == rs.AVAILABLE]
    for e in avail:
        m = e["metadata"]
        assert m["credibility_class"]["state"] == rs.NOT_ASSESSED
        assert m["primary_secondary"]["state"] == rs.NOT_ASSESSED
        assert m["cost"]["state"] == rs.NOT_MEASURED
        assert m["historical_depth"]["state"] == rs.NOT_MEASURED
        assert m["rate_limits"]["state"] == rs.NOT_ASSESSED


@pytest.mark.parametrize("field", rs.UNSUPPORTED_FIELDS)
def test_recorded_value_for_unsupported_field_is_refused(field):
    reg = rs.registry()
    reg["sources"][0]["metadata"][field] = {"state": rs.RECORDED,
                                            "value": "high", "reason": None}
    with pytest.raises(rs.ResearchSourceError, match="unsupported_value"):
        rs.validate(reg)


def test_known_weaknesses_are_verbatim_repository_statements():
    by_id = {e["source_id"]: e for e in rs.registry()["sources"]}
    dec = by_id[rs.DECISIONS_SOURCE_ID]["metadata"]["known_weaknesses"]
    assert dec["state"] == rs.RECORDED
    texts = [w["text"] for w in dec["value"]]
    assert ("Decision evidence is a declared selection, not independent "
            "opportunity evidence.") in texts
    state = (ROOT / "STATE.yaml").read_text()
    sources = {"STATE.yaml": state, "trader/cognition/research_plan.py":
               rp.__doc__, "trader/cognition/research_question.py":
               rq.SEMANTICS}
    for e in by_id.values():
        kw = e["metadata"]["known_weaknesses"]
        for w in kw["value"] or ():
            [src] = [v for k, v in sources.items()
                     if w["provenance"].startswith(k)]
            assert w["text"] in src
    q = by_id[rs.QUESTION_SOURCE_ID]["metadata"]["known_weaknesses"]
    assert q["state"] == rs.NOT_ASSESSED and q["value"] is None


# ── 5/6 plan output, IDs and hashes unchanged ────────────────────────────
def test_plan_outputs_are_byte_identical_to_pre_refactor_fixture():
    fx = _fixture()
    assert set(fx) == {"D", "W_D", "W_EF_D", "W_CF_D", "I_W_D", "D_W_D"}
    for name, scen in fx.items():
        now = _plans_now(scen["verdicts"])
        assert now == [p["canonical_json"] for p in scen["plans"]], name


def test_plan_ids_and_hashes_are_unchanged():
    for scen in _fixture().values():
        for p, text in zip(scen["plans"], _plans_now(scen["verdicts"])):
            plan = json.loads(text)
            assert plan["schema"] == "research-plan.v1"
            assert plan["plan_id"] == p["plan_id"] == rp.plan_id(plan)
            assert hashlib.sha256(text.encode()).hexdigest() == p["sha256"]


# ── 7 malformed / duplicate definitions fail closed ──────────────────────
def _dup_id(r):
    r["sources"].insert(1, copy.deepcopy(r["sources"][0]))


def _dup_store(r):
    r["sources"][1]["plan_descriptor"] = copy.deepcopy(
        r["sources"][0]["plan_descriptor"])
    r["sources"][1]["metadata"]["access_method"]["value"] = \
        r["sources"][0]["plan_descriptor"]["reader"]


_MALFORMED = {
    "schema": (lambda r: r.update(schema="research-source-registry.v2"),
               "registry:schema"),
    "extra_key": (lambda r: r.update(version=1), "registry_keys"),
    "missing_key": (lambda r: r.pop("semantics"), "registry_keys"),
    "empty_sources": (lambda r: r.update(sources=[]), "registry:sources"),
    "sources_not_list": (lambda r: r.update(sources={}), "registry:sources"),
    "duplicate_id": (_dup_id, "registry:duplicate_source_id"),
    "order": (lambda r: r["sources"].reverse(), "registry:order"),
    "duplicate_store": (_dup_store, "registry:duplicate_store"),
    "entry_extra": (lambda r: r["sources"][0].update(x=1), "entry_keys"),
    "entry_missing": (lambda r: r["sources"][0].pop("metadata"),
                      "entry_keys"),
    "empty_id": (lambda r: r["sources"][0].update(source_id=""),
                 "entry:source_id"),
    "status": (lambda r: r["sources"][0].update(status="PARTIAL"),
               ":status"),
    "available_with_unavailable": (
        lambda r: r["sources"][0].update(
            unavailable={"reason": "x", "detail": "y"}), "available_shape"),
    "unavailable_with_descriptor": (
        lambda r: r["sources"][3].update(
            plan_descriptor={"store": "journal.world", "reader": "R",
                             "record_schema": None}), "unavailable_shape"),
    "unavailable_empty_reason": (
        lambda r: r["sources"][3]["unavailable"].update(reason=""),
        "unavailable:"),
    "unavailable_recorded_fact": (
        lambda r: r["sources"][3]["metadata"].update(
            type={"state": rs.RECORDED, "value": "worldmodel",
                  "reason": None}), "unavailable_recorded"),
    "descriptor_extra_key": (
        lambda r: r["sources"][0]["plan_descriptor"].update(table="x"),
        "descriptor:"),
    "descriptor_bool_schema": (
        lambda r: r["sources"][0]["plan_descriptor"].update(
            record_schema=True), "descriptor:"),
    "access_method_mismatch": (
        lambda r: r["sources"][0]["metadata"]["access_method"].update(
            value="Journal.other"), "access_method"),
    "metadata_missing": (
        lambda r: r["sources"][0]["metadata"].pop("cost"), "metadata:"),
    "fact_bad_state": (
        lambda r: r["sources"][0]["metadata"]["cost"].update(state="GUESS"),
        ":state"),
    "unrecorded_with_value": (
        lambda r: r["sources"][0]["metadata"]["cost"].update(value=0),
        "unrecorded_shape"),
    "unrecorded_without_reason": (
        lambda r: r["sources"][0]["metadata"]["cost"].update(reason=None),
        "unrecorded_shape"),
    "recorded_with_reason": (
        lambda r: r["sources"][0]["metadata"]["type"].update(reason="x"),
        "recorded_shape"),
    "domains_unsorted": (
        lambda r: r["sources"][0]["metadata"]["domains"].update(
            value=["z", "a"]), "domains:value"),
    "weakness_no_provenance": (
        lambda r: r["sources"][2]["metadata"]["known_weaknesses"][
            "value"][0].update(provenance=""), "weakness"),
}


@pytest.mark.parametrize("name", sorted(_MALFORMED))
def test_malformed_definition_fails_closed(name):
    mutate, why = _MALFORMED[name]
    reg = rs.registry()
    mutate(reg)
    with pytest.raises(rs.ResearchSourceError, match=re.escape(why)):
        rs.validate(reg)


def test_validate_refuses_non_dict():
    for bad in (None, [], "x"):
        with pytest.raises(rs.ResearchSourceError):
            rs.validate(bad)


# ── 8 unknown source IDs fail closed ─────────────────────────────────────
@pytest.mark.parametrize("sid", ["internal.unknown", "", None,
                                 "INTERNAL.RESEARCH_QUESTIONS"])
def test_unknown_source_id_fails_closed(sid):
    for call in (rs.get, rs.descriptor, rs.unavailable):
        with pytest.raises(rs.ResearchSourceError, match="unknown_source"):
            call(sid)


def test_unavailable_lookup_of_available_source_fails():
    with pytest.raises(rs.ResearchSourceError, match="source_not_unavailable"):
        rs.unavailable(rs.HEALTH_SOURCE_ID)


@pytest.mark.parametrize("desc", [
    None, {}, {"store": "journal.trades", "reader": "Journal.trades",
               "record_schema": None},
    {**PRE_REGISTRY_DESCRIPTORS[rs.DECISIONS_SOURCE_ID], "record_schema": ""},
    {**PRE_REGISTRY_DESCRIPTORS[rs.HEALTH_SOURCE_ID], "extra": 1}])
def test_unregistered_descriptor_fails_closed(desc):
    with pytest.raises(rs.ResearchSourceError, match="unregistered"):
        rs.resolve(desc)


def test_plan_with_unregistered_descriptor_is_refused(monkeypatch):
    rows = _FullHist().seq("s1", [W, D]).rows
    [q] = rq.derive(rows).questions
    monkeypatch.setattr(rp, "_DECISIONS", {"store": "journal.trades",
                                           "reader": "Journal.trades",
                                           "record_schema": None})
    with pytest.raises(rp.ResearchPlanError,
                       match="source_unregistered:strategy_signal_decisions"):
        rp.build(rq.canonical(q), rows)


_SUBSTITUTES = [(name, sid) for name in ("_HEALTH", "_QUESTION", "_DECISIONS")
                for sid in PRE_REGISTRY_DESCRIPTORS]


@pytest.mark.parametrize("name,sid", _SUBSTITUTES)
def test_substituting_another_registered_descriptor_is_refused(
        monkeypatch, name, sid):
    """Membership is not enough: each route is bound to its own entry, so
    swapping in a different *registered* descriptor cannot silently reroute
    a plan (which would keep plan_id and change content)."""
    orig = getattr(rp, name)
    sub = rs.descriptor(sid)
    if rs.canonical(sub) == rs.canonical(orig):
        return                                   # not a substitution
    rows = _FullHist().seq("s1", [W, D]).rows
    [q] = rq.derive(rows).questions
    text = rs.canonical(q)
    good = rp.canonical(rp.build(text, rows))
    monkeypatch.setattr(rp, name, sub)
    with pytest.raises(rp.ResearchPlanError, match="source_unregistered:"):
        rp.build(text, rows)
    with pytest.raises(rp.ResearchPlanError, match="source_unregistered:"):
        rp.from_json(good, text, rows)


def test_require_binds_descriptor_to_its_own_entry():
    for sid, desc in PRE_REGISTRY_DESCRIPTORS.items():
        rs.require(sid, desc)
        for other in PRE_REGISTRY_DESCRIPTORS:
            if other != sid:
                with pytest.raises(rs.ResearchSourceError,
                                   match=f"descriptor_mismatch:{other}"):
                    rs.require(other, desc)
    with pytest.raises(rs.ResearchSourceError, match="unknown_source"):
        rs.require("internal.unknown", PRE_REGISTRY_DESCRIPTORS[
            rs.HEALTH_SOURCE_ID])
    with pytest.raises(rs.ResearchSourceError, match="source_unavailable"):
        rs.require(rs.WORLDMODEL_REGIME_SOURCE_ID, None)


_BAD_VALUES = [set(), {"x"}, float("nan"), float("inf"), 1, True, b"x",
               ["research-question.v1"], {"a": 1}, object()]


@pytest.mark.parametrize("bad", _BAD_VALUES, ids=repr)
@pytest.mark.parametrize("key", ["store", "reader", "record_schema"])
def test_malformed_descriptor_values_fail_as_source_errors(key, bad):
    for sid, desc in PRE_REGISTRY_DESCRIPTORS.items():
        d = {**desc, key: bad}
        with pytest.raises(rs.ResearchSourceError,
                           match="unregistered_descriptor"):
            rs.resolve(d)
        with pytest.raises(rs.ResearchSourceError,
                           match=f"descriptor_mismatch:{sid}"):
            rs.require(sid, d)


@pytest.mark.parametrize("bad", [set(), float("nan")], ids=repr)
def test_malformed_descriptor_value_is_a_plan_refusal(monkeypatch, tmp_path,
                                                      bad):
    from tests.test_strategy_decay_research_plan import _journal
    j = _journal(tmp_path, [W, D])
    monkeypatch.setattr(rp, "_HEALTH", {**rp._HEALTH, "record_schema": bad})
    rows = j.strategy_health_rows()
    [qrow] = j.research_questions()
    with pytest.raises(rp.ResearchPlanError, match="source_unregistered:"):
        rp.build(qrow["canonical_json"], rows)
    res = rp.record_from_journal(j, now_ms=5)       # refused, not raised
    assert res["inserted"] == [] and len(res["refusals"]) == 1
    assert res["refusals"][0][1].startswith("source_unregistered:")
    assert j.research_plans() == []


# ── 9/10 no network / LLM / live / Risk / Execution / Attention wiring ──
def test_module_imports_only_contract_modules():
    tree = ast.parse(Path(rs.__file__).read_text())
    mods, names = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
            if node.module and node.module.startswith("trader"):
                names |= {a.name for a in node.names}
    assert mods <= {"__future__", "hashlib", "json", "trader.cognition",
                    "trader.strategy"}
    assert names == {"research_question", "health_observation"}


def test_only_research_plan_reads_the_registry():
    root = ROOT / "trader"
    pat = re.compile(r"research_sources\b")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_sources.py"
                     and pat.search(p.read_text(errors="ignore")))
    # no Kernel, Attention, Analyst, Risk, Execution or journal caller
    assert callers == ["cognition/research_plan.py"]


def test_registry_names_no_external_source():
    # the semantics sentence disclaims these words; only entries are checked
    text = rs.canonical(rs.registry()["sources"]).lower()
    for word in ("http", "https", "url", "api.", "binance", "llm_", "search"):
        assert word not in text.replace("research", "")
    for e in rs.registry()["sources"]:
        assert e["source_id"].startswith("internal.")
        if e["status"] == rs.AVAILABLE:
            assert e["plan_descriptor"]["store"].startswith("journal.")
            assert e["metadata"]["type"]["value"] == rs.INTERNAL_STORE
