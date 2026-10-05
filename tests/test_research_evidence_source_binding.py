"""research-evidence.v1 resolves every routed source through
research-source-registry.v1 (SDD-STAGE-3-RESEARCH-EVIDENCE-SOURCE-REGISTRY-
BINDING-V1).

The fixture was captured from research_evidence.py before the binding
existed, over deterministic health rows (no journal wall clock), with and
without a decision history covering every selection branch. Valid evidence
must stay byte-identical; unregistered, unavailable, substituted and
malformed descriptors must fail closed with no fallback to store names.
"""
import ast
import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from tests.test_research_source_registry import VERDICTS
from tests.test_strategy_decay_research_evidence import _iso, _sig
from tests.test_strategy_decay_research_plan import _FullHist
from trader.cognition import research_evidence as re_
from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.cognition import research_sources as rs

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/research_evidence_v1_pre_source_registry.json"
#: fixture captured from research_evidence.py before the registry binding
FIXTURE_SHA256 = (
    "47c8ed7e8d2d3d32077c4b84dd331b941d6e4f06ee728ca6d89448465dad1c4b")
SCENARIOS = ("D", "D_W_D", "I_W_D", "W_CF_D", "W_D")
REGIME = rs.WORLDMODEL_REGIME_SOURCE_ID


# ── deterministic builder (shared by the fixture capture and the tests) ──
def _decision_row(did, ts, signals=None, raw=None):
    return {"id": did, "ts": ts, "scan_id": f"scan-{did}",
            "symbol": "BTC/USDT:USDT", "action": "BUY", "executed": 0,
            "reason_codes": None, "reason_codes_version": None,
            "signals_json": raw if raw is not None else json.dumps(signals)}


def _decision_rows(at):
    """The decision history of test_strategy_decay_research_evidence
    ._decisions, as raw rows: every selection branch around the sweep."""
    hi = rq._ms(at)
    return [
        _decision_row("d-sel", _iso(at, -60),
                      [_sig(close_ms=hi - 1), _sig("s2", hi - 1)]),
        _decision_row("d-edge", at, [_sig(close_ms=hi)]),
        _decision_row("d-late-bar", _iso(at, -30), [_sig(close_ms=hi + 1)]),
        _decision_row("d-after", _iso(at, 60), [_sig(close_ms=hi - 1)]),
        _decision_row("d-other", _iso(at, -10), [_sig("s2", hi - 1)]),
        _decision_row("d-nokey", _iso(at, -20),
                      [{"symbol": "X", "action": "BUY", "params": {}},
                       _sig(close_ms=hi - 5)]),
        _decision_row("d-hold", _iso(at, -40), []),
        _decision_row("d-naive", "2020-01-01T00:00:00", [_sig(close_ms=1)]),
        _decision_row("d-badjson", _iso(at, -5), raw="{not json"),
    ]


def _cases(name, with_decisions):
    """[(plan_text, plan, ctx)] for every question of one scenario."""
    rows = _FullHist().seq("s1", [VERDICTS[v] for v in name.split("_")]).rows
    d = rq.derive(rows)
    assert d.questions and not d.refusals
    out = []
    for q in d.questions:
        qtext = rq.canonical(q)
        ptext = rp.canonical(rp.build(qtext, rows))
        plan = rp.from_json(ptext, qtext, rows)
        decisions = (_decision_rows(q["source"]["sweep_started_at"])
                     if with_decisions else [])
        ctx = {"health": {r["id"]: r for r in rows},
               "question": {"canonical_json": qtext,
                            "source_event_id": q["source"]["event_id"]},
               "decisions": decisions}
        out.append((ptext, plan, ctx))
    return out


def _key(name, with_decisions):
    return f"{name}+decisions" if with_decisions else name


def build_all() -> dict:
    """{scenario: [{plan, evidence text, id, sha}]} from the collector."""
    out = {}
    for name in SCENARIOS:
        for with_dec in (True, False):
            recs = []
            for ptext, plan, ctx in _cases(name, with_dec):
                text = re_.canonical(re_.collect(ptext, plan, ctx))
                rec = json.loads(text)
                recs.append({"plan_canonical_json": ptext,
                             "evidence_canonical_json": text,
                             "evidence_id": rec["evidence_id"],
                             "evidence_sha256": hashlib.sha256(
                                 text.encode()).hexdigest()})
            out[_key(name, with_dec)] = recs
    return out


def fixture_text(data: dict) -> str:
    return json.dumps(data, sort_keys=True, indent=1) + "\n"


def _fixture():
    text = FIXTURE.read_text()
    assert hashlib.sha256(text.encode()).hexdigest() == FIXTURE_SHA256
    return json.loads(text)


# ── helpers ──────────────────────────────────────────────────────────────
def _case(name="W_D", with_decisions=True):
    return _cases(name, with_decisions)[0]


def _items(rec):
    return [it for s in rec["hypotheses"] for it in s["items"]]


def _routes(plan):
    return [e for s in plan["hypotheses"] for e in s["evidence"]]


def _mutated(plan, role, **over):
    """A canonical copy of `plan` with the first route of `role` (roles may
    repeat, e.g. one per history sweep) given the replaced fields."""
    p = copy.deepcopy(plan)
    next(e for e in _routes(p) if e["role"] == role).update(over)
    return rp.canonical(p), p


def _role(plan, source_id):
    """The first route role whose descriptor is `source_id`'s."""
    desc = rs.descriptor(source_id)
    return next(e["role"] for e in _routes(plan)
                if {k: e[k] for k in desc} == desc)


def _collect_err(ptext, plan, ctx):
    with pytest.raises(re_.ResearchEvidenceError) as ei:
        re_.collect(ptext, plan, ctx)
    return str(ei.value)


def _keys_of(v, acc):
    if isinstance(v, dict):
        for k, x in v.items():
            acc.add(k)
            _keys_of(x, acc)
    elif isinstance(v, list):
        for x in v:
            _keys_of(x, acc)
    return acc


# ── 1/2 valid evidence is byte-identical; IDs and hashes unchanged ──────
def test_fixture_covers_every_source_and_the_regime_path():
    fx = _fixture()
    assert set(fx) == {_key(n, d) for n in SCENARIOS for d in (True, False)}
    stores, regime, selected = set(), 0, 0
    for recs in fx.values():
        for r in recs:
            rec = json.loads(r["evidence_canonical_json"])
            stores |= {it["source"]["store"] for it in _items(rec)}
            regime += sum(s["hypothesis"] == rp.TEMPORARY_REGIME_ABSENCE
                          and s["status"] == rp.UNAVAILABLE
                          for s in rec["hypotheses"])
            selected += sum(len(it["values"]["selected_rows"])
                            for it in _items(rec)
                            if it["source"]["store"] == "journal.decisions")
    assert stores == {rs.descriptor(s)["store"] for s in (
        rs.HEALTH_SOURCE_ID, rs.QUESTION_SOURCE_ID, rs.DECISIONS_SOURCE_ID)}
    assert regime == sum(len(v) for v in fx.values())
    assert selected > 0


def test_valid_evidence_is_byte_identical_to_pre_binding_fixture():
    assert fixture_text(build_all()) == FIXTURE.read_text()


def test_evidence_ids_and_hashes_are_unchanged():
    now = build_all()
    for key, recs in _fixture().items():
        assert [r["evidence_id"] for r in now[key]] == [
            r["evidence_id"] for r in recs]
        assert [r["evidence_sha256"] for r in now[key]] == [
            r["evidence_sha256"] for r in recs]
        for r in recs:
            text = r["evidence_canonical_json"]
            assert hashlib.sha256(text.encode()).hexdigest() \
                == r["evidence_sha256"]
            # a record stored before the binding still verifies on its own
            rec = re_.from_json(text, r["plan_canonical_json"])
            assert rec["evidence_id"] == r["evidence_id"] \
                == re_.evidence_id(rec)


# ── 3 every collected source resolves through the registry ──────────────
def test_every_collected_source_resolves_through_registry(monkeypatch):
    calls = []
    real = rs.resolve

    def spy(desc):
        sid = real(desc)
        calls.append(sid)
        return sid

    monkeypatch.setattr(rs, "resolve", spy)
    ptext, plan, ctx = _case()
    rec = re_.collect(ptext, plan, ctx)
    items = _items(rec)
    assert len(calls) == len(items)
    assert set(calls) == {rs.HEALTH_SOURCE_ID, rs.QUESTION_SOURCE_ID,
                          rs.DECISIONS_SOURCE_ID}
    calls.clear()
    re_.from_json(re_.canonical(rec), ptext)
    assert len(calls) == len(items)
    calls.clear()
    re_.verify_sources(rec, ctx)
    assert len(calls) == len(items)
    for it in items:
        desc = {k: it["source"][k] for k in ("store", "reader",
                                            "record_schema")}
        assert rs.descriptor(real(desc)) == desc


# ── 4 unregistered descriptor fails closed; no store-name fallback ──────
@pytest.mark.parametrize("sid", [rs.HEALTH_SOURCE_ID, rs.QUESTION_SOURCE_ID,
                                 rs.DECISIONS_SOURCE_ID])
@pytest.mark.parametrize("field,value", [
    ("reader", "Journal.not_a_reader"),      # same store: no fallback
    ("record_schema", "other-schema.v1"),
    ("store", "journal.not_a_store"),
])
def test_unregistered_descriptor_fails_closed(sid, field, value):
    ptext, plan, ctx = _case()
    role = _role(plan, sid)
    bad_text, bad = _mutated(plan, role, **{field: value})
    assert _collect_err(bad_text, bad, ctx) == (
        f"source_registry:{role}:unregistered_descriptor")


def test_unregistered_descriptor_fails_stored_record_verification():
    ptext, plan, ctx = _case()
    rec = re_.collect(ptext, plan, ctx)
    role = _role(plan, rs.HEALTH_SOURCE_ID)
    bad_text, bad = _mutated(plan, role, reader="Journal.not_a_reader")
    # the stored record re-derived against an edited plan text is refused
    # by the plan binding first; the item binding refuses it independently
    with pytest.raises(re_.ResearchEvidenceError):
        re_.from_json(re_.canonical(rec), bad_text)
    it = next(i for i in _items(rec) if i["role"] == role)
    it = copy.deepcopy(it)
    it["source"]["reader"] = "Journal.not_a_reader"
    with pytest.raises(re_.ResearchEvidenceError,
                       match=f"^source_registry:{role}:unregistered"):
        re_.verify_sources({"hypotheses": [{"items": [it]}]}, ctx)


# ── 5 an UNAVAILABLE source fails closed with its exact registry reason ──
def test_unavailable_source_fails_closed_with_exact_reason(monkeypatch):
    ptext, plan, ctx = _case()
    role = _role(plan, rs.HEALTH_SOURCE_ID)
    real = rs.resolve
    health = rs.descriptor(rs.HEALTH_SOURCE_ID)
    # a descriptor that resolved to the UNAVAILABLE WorldModel/regime entry
    monkeypatch.setattr(rs, "resolve", lambda d: REGIME
                        if d == health else real(d))
    reason = rs.unavailable(REGIME)["reason"]
    assert reason == "no_truthful_worldmodel_source"
    assert _collect_err(ptext, plan, ctx) == (
        f"source_registry:{role}:source_unavailable:{REGIME}:{reason}")


def test_regime_section_carries_the_registry_unavailable_entry():
    ptext, plan, ctx = _case()
    rec = re_.collect(ptext, plan, ctx)
    [reg] = [s for s in rec["hypotheses"] if s["status"] == rp.UNAVAILABLE]
    u = rs.unavailable(REGIME)
    assert (reg["hypothesis"], reg["unavailable_reason"],
            reg["unavailable_detail"], reg["items"]) == (
        rp.TEMPORARY_REGIME_ABSENCE, u["reason"], u["detail"], [])


@pytest.mark.parametrize("edit", [
    {"unavailable_reason": "invented_reason"},
    {"unavailable_detail": "invented detail"},
    {"unavailable_reason": None, "unavailable_detail": None},
    {"status": "NOT_ROUTED"},
])
def test_unavailable_section_not_in_registry_fails_closed(edit):
    ptext, plan, ctx = _case()
    p = copy.deepcopy(plan)
    [s] = [s for s in p["hypotheses"]
           if s["hypothesis"] == rp.TEMPORARY_REGIME_ABSENCE]
    s.update(edit)
    assert _collect_err(rp.canonical(p), p, ctx) == (
        f"section_unavailable_unregistered:{rp.TEMPORARY_REGIME_ABSENCE}")


# ── 6 descriptor substitution cannot bypass the registry ────────────────
@pytest.mark.parametrize("sid,other", [
    (rs.HEALTH_SOURCE_ID, rs.QUESTION_SOURCE_ID),
    (rs.HEALTH_SOURCE_ID, rs.DECISIONS_SOURCE_ID),
    (rs.QUESTION_SOURCE_ID, rs.HEALTH_SOURCE_ID),
    (rs.DECISIONS_SOURCE_ID, rs.HEALTH_SOURCE_ID),
])
def test_substituting_another_registered_descriptor_fails_closed(sid, other):
    ptext, plan, ctx = _case()
    role = _role(plan, sid)
    bad_text, bad = _mutated(plan, role, **rs.descriptor(other))
    # resolves to `other`, whose contract the route does not meet
    _collect_err(bad_text, bad, ctx)


@pytest.mark.parametrize("sid,other", [
    (rs.HEALTH_SOURCE_ID, rs.QUESTION_SOURCE_ID),
    (rs.QUESTION_SOURCE_ID, rs.DECISIONS_SOURCE_ID),
])
def test_mixed_descriptor_fails_closed(sid, other):
    ptext, plan, ctx = _case()
    role = _role(plan, sid)
    # the store of one registered source with the reader of another
    bad_text, bad = _mutated(plan, role,
                             reader=rs.descriptor(other)["reader"])
    assert _collect_err(bad_text, bad, ctx) == (
        f"source_registry:{role}:unregistered_descriptor")


def test_resolved_id_is_bound_to_its_own_descriptor(monkeypatch):
    ptext, plan, ctx = _case()
    role = _role(plan, rs.QUESTION_SOURCE_ID)
    real = rs.resolve
    question = rs.descriptor(rs.QUESTION_SOURCE_ID)
    # a resolution naming another registered entry is a mismatch
    monkeypatch.setattr(rs, "resolve", lambda d: rs.HEALTH_SOURCE_ID
                        if d == question else real(d))
    assert _collect_err(ptext, plan, ctx) == (
        f"source_registry:{role}:descriptor_mismatch:{rs.HEALTH_SOURCE_ID}")


def test_registered_source_without_a_collector_fails_closed(monkeypatch):
    ptext, plan, ctx = _case()
    monkeypatch.delitem(re_._COLLECTORS, rs.QUESTION_SOURCE_ID)
    role = _role(plan, rs.QUESTION_SOURCE_ID)
    assert _collect_err(ptext, plan, ctx) == (
        f"source_unsupported:{role}:{rs.QUESTION_SOURCE_ID}")


# ── 7 malformed descriptors fail closed as evidence errors ──────────────
@pytest.mark.parametrize("field", ["store", "reader", "record_schema"])
@pytest.mark.parametrize("value", [None, "", 5, 1.5, True, [], {"a": 1}])
def test_malformed_descriptor_value_fails_closed(field, value):
    ptext, plan, ctx = _case()
    role = _role(plan, rs.HEALTH_SOURCE_ID)
    bad_text, bad = _mutated(plan, role, **{field: value})
    err = _collect_err(bad_text, bad, ctx)
    if field == "record_schema" and value is None:
        # well-formed, but no registered entry has it for this store
        assert err == f"source_registry:{role}:unregistered_descriptor"
    else:
        assert err == f"source_malformed:{role}"


@pytest.mark.parametrize("key", ["store", "reader", "record_schema",
                                 "record_kind", "record_variant"])
def test_missing_source_key_fails_closed(key):
    ptext, plan, ctx = _case()
    p = copy.deepcopy(plan)
    route = _routes(p)[0]
    del route[key]
    assert _collect_err(rp.canonical(p), p, ctx) == (
        f"source_malformed:{route['role']}")


# ── 8 decision-selection semantics unchanged ─────────────────────────────
def test_decision_selection_semantics_unchanged():
    fx = _fixture()
    for key, recs in fx.items():
        for r in recs:
            rec = json.loads(r["evidence_canonical_json"])
            [dec] = [it for it in _items(rec)
                     if it["source"]["store"] == "journal.decisions"]
            # the frozen values are the selection re-run over frozen rows
            assert re_.select_decisions(dec["locator"],
                                        dec["source_content"]) \
                == dec["values"]
            sel = [x["decision_id"] for x in dec["values"]["selected_rows"]]
            rep = sorted({x["decision_id"] for x in dec["values"]["reported"]})
            if key.endswith("+decisions"):
                assert sel == ["d-sel", "d-nokey", "d-edge"]
                assert rep == ["d-badjson", "d-naive", "d-nokey"]
            else:
                assert sel == [] and rep == []


# ── 9 no source_id field or schema migration ─────────────────────────────
def test_no_schema_or_source_id_migration():
    assert re_.SCHEMA == "research-evidence.v1"
    assert re_._ITEM_KEYS == (
        "hypothesis", "role", "locator", "source", "fields", "plan_binding",
        "source_identity", "source_sha256", "source_content", "values",
        "content_sha256")
    assert re_._SOURCE_KEYS == ("store", "reader", "record_schema",
                                "record_kind", "record_variant")
    for recs in build_all().values():
        for r in recs:
            keys = _keys_of(json.loads(r["evidence_canonical_json"]), set())
            assert "source_id" not in keys


# ── 10 no live / Attention / Risk / Execution wiring ─────────────────────
def test_registry_readers_are_offline_research_modules_only():
    root = ROOT / "trader"
    pat = re.compile(r"research_sources\b")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_sources.py"
                     and pat.search(p.read_text(errors="ignore")))
    # the offline research shadow child only NAMES it in its exact import
    # allow-list (research_evidence imports it); it reads no registry entry
    assert callers == ["cognition/_research_shadow_child.py",
                       "cognition/research_evidence.py",
                       "cognition/research_plan.py",
                       # offline unreadable-health plan (read-only pin check)
                       "cognition/research_unreadable_plan.py"]


def test_evidence_module_adds_only_the_registry_import():
    src = Path(re_.__file__).read_text()
    names = {a.name for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.ImportFrom) for a in n.names}
    assert names == {"annotations", "datetime", "research_plan",
                     "research_question", "research_sources",
                     "health_observation", "signal_occurrence_observation"}
    for word in ("kernel", "attention", "risk", "executor", "orchestrator",
                 "requests", "urllib", "socket", "llm"):
        assert not re.search(rf"^\s*(from|import)\s+\S*{word}", src,
                             re.I | re.M)
