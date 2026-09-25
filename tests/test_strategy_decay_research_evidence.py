"""research-evidence.v1: frozen evidence for strategy-decay research plans.

One fully verified research-plan.v1 yields one deterministic, immutable
record that resolves every ROUTED locator against its existing internal
store and freezes the exact routed values with source identity and hash.
The record classifies and concludes nothing, the regime section stays
UNAVAILABLE, and nothing live, Attention, Risk or Execution calls it.
"""
import ast
import json
import random
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_plan import (_FullHist, _NO_CONCLUSION,
                                                     _edit_detail)
from tests.test_strategy_decay_research_question import _keys
from trader.cognition import research_evidence as re_
from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.core.journal import Journal
from trader.strategy import health_observation as ho

D, W, I, EF = ho.DECAYED, ho.STILL_WORKING, ho.IDLE, ho.EVALUATION_FAILED


def _journal(tmp_path, verdicts, name="j.db"):
    j = Journal(tmp_path / name)
    for r in _FullHist().seq("s1", verdicts).rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    rq.record_from_journal(j, now_ms=1)
    rp.record_from_journal(j, now_ms=1)
    return j


def _source_at(j):
    return rq.load(j)[0]["source"]["sweep_started_at"]


def _iso(at, minutes):
    return (datetime.fromisoformat(at) + timedelta(minutes=minutes)).isoformat()


def _sig(spec="s1", close_ms=0, symbol="BTC/USDT:USDT", **over):
    params = {"spec_id": spec, "spec_fingerprint": "fp1",
              "signal_timeframe": "4h", "signal_bar_close_ms": close_ms}
    params.update(over)
    return {"strategy_id": f"spec:{spec}", "symbol": symbol, "action": "BUY",
            "params": params}


def _decision(j, did, ts, signals, executed=0, raw_signals=None):
    with j._tx() as c:
        c.execute("INSERT OR IGNORE INTO cycles (id,ts,symbol) VALUES "
                  "('c1','2020-01-01T00:00:00+00:00','BTC/USDT:USDT')")
        c.execute(
            "INSERT INTO decisions (id,cycle_id,ts,symbol,action,score,"
            "threshold,confidence,executed,signals_json,scan_id,reason_codes,"
            "reason_codes_version) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, "c1", ts, "BTC/USDT:USDT", "BUY", 0.0, 0.0, 0.0, executed,
             raw_signals if raw_signals is not None else json.dumps(signals),
             f"scan-{did}", None, None))


def _decisions(j, order=None):
    """A decision history around the source sweep covering every selection
    branch; `order` permutes insertion."""
    at = _source_at(j)
    hi = rq._ms(at)
    rows = [
        ("d-sel", _iso(at, -60), [_sig(close_ms=hi - 1), _sig("s2", hi - 1)]),
        ("d-edge", at, [_sig(close_ms=hi)]),
        ("d-late-bar", _iso(at, -30), [_sig(close_ms=hi + 1)]),
        ("d-after", _iso(at, 60), [_sig(close_ms=hi - 1)]),
        ("d-other", _iso(at, -10), [_sig("s2", hi - 1)]),
        ("d-nokey", _iso(at, -20), [{"symbol": "X", "action": "BUY",
                                      "params": {}}, _sig(close_ms=hi - 5)]),
        ("d-hold", _iso(at, -40), []),
    ]
    if order is not None:
        rows = [rows[i] for i in order]
    for did, ts, sigs in rows:
        _decision(j, did, ts, sigs)
    _decision(j, "d-naive", "2020-01-01T00:00:00", [_sig(close_ms=1)])
    _decision(j, "d-badjson", _iso(at, -5), None, raw_signals="{not json")


def _stored(j):
    [r] = j.research_evidence()
    return r


def _items(rec, role=None):
    return [it for s in rec["hypotheses"] for it in s["items"]
            if role is None or it["role"] == role]


# ── 1 verified plan -> one deterministic evidence record ─────────────────
def test_verified_plan_yields_one_deterministic_record(tmp_path):
    j = _journal(tmp_path, [W, EF, D])
    _decisions(j)
    res = re_.record_from_journal(j, now_ms=10)
    assert len(res["inserted"]) == 1 and not res["refusals"]
    [rec] = re_.load(j)
    [plan] = rp.load(j)
    assert rec["evidence_id"] == res["inserted"][0]
    assert rec["source_plan"] == {
        "schema": rp.SCHEMA, "plan_id": plan["plan_id"],
        "plan_kind": rp.PLAN_KIND, "planner_id": rp.PLANNER_ID,
        "question_id": plan["source_question"]["question_id"],
        "canonical_sha256": re_._sha(j.research_plans()[0]["canonical_json"])}
    assert rec["scope"] == {"kind": "strategy", "spec_id": "s1"}
    row = _stored(j)
    assert row["canonical_json"] == re_.canonical(rec)
    assert row["canonical_sha256"] == re_._sha(row["canonical_json"])
    assert row["plan_sha256"] == rec["source_plan"]["canonical_sha256"]
    # a second journal with the same content gives byte-identical evidence
    src, dst = sqlite3.connect(tmp_path / "j.db"), sqlite3.connect(tmp_path / "b.db")
    src.backup(dst)
    src.close(), dst.close()
    b = Journal(tmp_path / "b.db")
    with b._tx() as c:
        c.execute("DELETE FROM research_evidence")
    re_.record_from_journal(b, now_ms=99)
    assert _stored(b)["canonical_json"] == row["canonical_json"]


def test_every_plan_yields_its_own_record(tmp_path):
    j = _journal(tmp_path, [W, D, D, W, D])
    res = re_.record_from_journal(j, 1)
    assert len(res["inserted"]) == 2 == len(j.research_plans())
    assert {r["plan_id"] for r in j.research_evidence()} == {
        r["plan_id"] for r in j.research_plans()}


# ── 2 every ROUTED locator resolves to its truthful record ──────────────
def test_every_routed_locator_resolves_to_its_record(tmp_path):
    j = _journal(tmp_path, [W, EF, D])
    re_.record_from_journal(j, 1)
    [rec] = re_.load(j)
    [plan] = rp.load(j)
    raw = {r["id"]: r for r in j.strategy_health_rows()}
    [qrow] = j.research_questions()
    routed = [(s["hypothesis"], e) for s in plan["hypotheses"]
              for e in s["evidence"]]
    items = _items(rec)
    assert len(items) == len(routed)
    for (h, e), it in zip(routed, items):
        assert it["hypothesis"] == h and it["role"] == e["role"]
        assert it["locator"] == e["locator"] and it["fields"] == e["fields"]
        assert it["plan_binding"] == e["binding"]
        assert it["source"] == {k: e[k] for k in re_._SOURCE_KEYS}
        assert it["content_sha256"] == re_._sha(re_.canonical(it["values"]))
        if e["store"] == "journal.brain_events":
            row = raw[e["binding"]["event_id"]]
            assert it["source_identity"] == {
                "event_id": row["id"], "kind": row["kind"],
                "subject": row["subject"]}
            assert it["source_sha256"] == re_._sha(row["detail"]) \
                == e["binding"]["record_sha256"]
            assert it["source_content"] == row["detail"]
            detail = json.loads(row["detail"])
            assert it["values"] == {f: re_._get(detail, f) for f in e["fields"]}
        elif e["store"] == "journal.research_questions":
            assert it["source_sha256"] == re_._sha(qrow["canonical_json"])
            assert it["source_content"] == qrow["canonical_json"]
            assert it["source_identity"] == {
                "question_id": qrow["question_id"],
                "source_event_id": qrow["source_event_id"]}
            q = json.loads(qrow["canonical_json"])
            assert it["values"] == {f: re_._get(q, f) for f in e["fields"]}
        else:
            assert e["store"] == "journal.decisions"
            assert it["source_identity"] == e["locator"]


def test_real_writer_records_are_collected(tmp_path):
    from tests.test_strategy_decay_research_plan import _real_journal
    j = _real_journal(tmp_path)
    rq.record_from_journal(j, 1)
    rp.record_from_journal(j, 1)
    res = re_.record_from_journal(j, 1)
    assert len(res["inserted"]) == 1 and res["refusals"] == []
    [rec] = re_.load(j)
    unread = _items(rec, "unreadable_observation")
    assert [it["source"]["record_variant"] for it in unread] == [
        rp.COMPILE_FAILED_RECORD, rp.EVALUATION_EXCEPTION_RECORD]
    assert all(it["values"]["error"]["error_class"] for it in unread)


# ── 3 decision selection is frozen deterministically ────────────────────
def test_decision_selection_is_frozen_deterministically(tmp_path):
    _journal(tmp_path, [W, D], name="base.db")
    texts = []
    for n, seed in enumerate((0, 1, 2)):
        # same health history (brain_events ts is wall clock, so copy it)
        src = sqlite3.connect(tmp_path / "base.db")
        dst = sqlite3.connect(tmp_path / f"{n}.db")
        src.backup(dst)
        src.close(), dst.close()
        j = Journal(tmp_path / f"{n}.db")
        order = list(range(7))
        random.Random(seed).shuffle(order)
        _decisions(j, order)
        re_.record_from_journal(j, 1)
        texts.append(_stored(j)["canonical_json"])
    assert len(set(texts)) == 1
    [it] = _items(json.loads(texts[0]), "strategy_signal_decisions")
    v = it["values"]
    assert [r["decision_id"] for r in v["selected_rows"]] == [
        "d-sel", "d-nokey", "d-edge"]          # by decision time, then id
    sel = v["selected_rows"][0]
    assert sel["selected_entry_indexes"] == [0]      # s2 entry not selected
    assert [s["params"]["spec_id"] for s in sel["selected_signals"]] == ["s1"]
    assert set(sel["columns"]) == set(rp._DECISION_FIELDS) - {"signals_json"}
    assert sel["columns"]["scan_id"] == "scan-d-sel"
    assert v["reported"] == [
        {"decision_id": "d-badjson", "entry_index": None,
         "reason": "signals_json_undecodable"},
        {"decision_id": "d-naive", "entry_index": None,
         "reason": re_.DECISION_TS_UNREADABLE},
        {"decision_id": "d-nokey", "entry_index": 0,
         "reason": re_.SIGNAL_ENTRY_WITHOUT_OCCURRENCE}]
    assert it["content_sha256"] == re_._sha(re_.canonical(v))
    # frozen raw content: exactly the selected + reported rows, by id
    assert [r["id"] for r in it["source_content"]] == [
        "d-badjson", "d-edge", "d-naive", "d-nokey", "d-sel"]
    assert it["source_sha256"] == re_._sha(re_.canonical(it["source_content"]))


def test_selected_row_hash_binds_the_raw_routed_columns(tmp_path):
    j = _journal(tmp_path, [W, D])
    _decisions(j)
    re_.record_from_journal(j, 1)
    [row] = j.query("SELECT id, ts, scan_id, executed, reason_codes, "
                    "reason_codes_version, signals_json FROM decisions "
                    "WHERE id='d-sel'")
    [it] = _items(re_.load(j)[0], "strategy_signal_decisions")
    [sel] = [r for r in it["values"]["selected_rows"]
             if r["decision_id"] == "d-sel"]
    assert sel["row_sha256"] == re_._sha(re_.canonical(row))


def test_empty_decisions_freeze_as_empty_selection(tmp_path):
    j = _journal(tmp_path, [D])
    re_.record_from_journal(j, 1)
    [it] = _items(re_.load(j)[0], "strategy_signal_decisions")
    assert it["values"] == {"selected_rows": [], "reported": []}


# ── 4 regime section remains UNAVAILABLE ────────────────────────────────
def test_regime_section_remains_unavailable(tmp_path):
    j = _journal(tmp_path, [W, D])
    re_.record_from_journal(j, 1)
    [rec] = re_.load(j)
    assert [s["hypothesis"] for s in rec["hypotheses"]] == list(rp.HYPOTHESES)
    [reg] = [s for s in rec["hypotheses"]
             if s["hypothesis"] == rp.TEMPORARY_REGIME_ABSENCE]
    assert reg == {"hypothesis": rp.TEMPORARY_REGIME_ABSENCE,
                   "status": rp.UNAVAILABLE,
                   "unavailable_reason": rp.NO_TRUTHFUL_WORLDMODEL_SOURCE,
                   "unavailable_detail": rp.REGIME_UNAVAILABLE_DETAIL,
                   "items": []}
    assert all(s["status"] == rp.ROUTED and s["items"]
               for s in rec["hypotheses"] if s is not reg)


def test_unavailable_section_with_evidence_is_refused(tmp_path):
    j = _journal(tmp_path, [W, D])
    [prow] = j.research_plans()
    plan = json.loads(prow["canonical_json"])
    plan["hypotheses"][3]["evidence"] = plan["hypotheses"][0]["evidence"]
    with pytest.raises(re_.ResearchEvidenceError, match="unavailable"):
        re_.collect(rp.canonical(plan), plan, re_._ctx(j, plan[
            "source_question"]["question_id"]))


# ── 5 retry / restart idempotent ────────────────────────────────────────
def test_retry_and_restart_are_idempotent(tmp_path):
    j = _journal(tmp_path, [W, D, D, W, D])
    _decisions(j)
    first = re_.record_from_journal(j, now_ms=1)
    assert len(first["inserted"]) == 2
    again = re_.record_from_journal(j, now_ms=2)
    assert again["inserted"] == [] and again["duplicate"] == first["inserted"]
    j2 = Journal(tmp_path / "j.db")
    third = re_.record_from_journal(j2, now_ms=3)
    assert third["duplicate"] == first["inserted"]
    assert [r["evidence_id"] for r in re_.load(j2)] == first["inserted"]
    assert {r["recorded_at_ms"] for r in j2.research_evidence()} == {1}


# ── 6 conflicting duplicate rejected ────────────────────────────────────
def test_conflicting_duplicate_is_refused_not_overwritten(tmp_path):
    j = _journal(tmp_path, [W, D])
    _decisions(j)
    re_.record_from_journal(j, 1)
    before = _stored(j)
    row = {k: before[k] for k in j._EVIDENCE_COLUMNS}
    rec = json.loads(row["canonical_json"])
    rec["hypotheses"][0]["items"][0]["values"]["metrics.trades"] = 999
    for bad in (dict(row, canonical_json=re_.canonical(rec)),
                dict(row, evidence_id="0" * 64)):     # same plan, other id
        assert j.record_research_evidence(bad, recorded_at_ms=5) == "conflict"
    assert j.research_evidence() == [before]


def test_recollection_after_decision_change_is_a_conflict(tmp_path):
    j = _journal(tmp_path, [W, D])
    _decisions(j)
    re_.record_from_journal(j, 1)
    before = _stored(j)
    j.update_decision_outcome("d-sel", True)
    res = re_.record_from_journal(j, 2)
    assert res["conflict"] == [before["evidence_id"]] and not res["inserted"]
    assert j.research_evidence() == [before]


# ── 7 missing / tampered source evidence fails closed at collection ─────
def _health_id(j, role):
    [plan] = rp.load(j)
    [e] = [e for s in plan["hypotheses"] for e in s["evidence"]
           if e["role"] == role][:1]
    return e["binding"]["event_id"]


@pytest.mark.parametrize("role", ["source_observation", "prior_observation",
                                  "source_sweep"])
def test_deleted_or_tampered_health_source_refuses_collection(tmp_path, role):
    for mode in ("delete", "tamper"):
        j = _journal(tmp_path, [W, D], name=f"{role}-{mode}.db")
        eid = _health_id(j, role)
        with j._tx() as c:
            if mode == "delete":
                c.execute("DELETE FROM brain_events WHERE id=?", (eid,))
            else:
                c.execute("UPDATE brain_events SET detail=detail||' ' "
                          "WHERE id=?", (eid,))
        res = re_.record_from_journal(j, 1)
        assert res["inserted"] == [] and len(res["refusals"]) == 1
        assert j.research_evidence() == []


def test_tampered_source_question_refuses_collection(tmp_path):
    j = _journal(tmp_path, [W, D])
    with j._tx() as c:
        c.execute("UPDATE research_questions SET canonical_json="
                  "canonical_json||' '")
    res = re_.record_from_journal(j, 1)
    assert res["inserted"] == [] and len(res["refusals"]) == 1


def test_tampered_or_missing_plan_row_refuses_collection(tmp_path):
    j = _journal(tmp_path, [W, D])
    with j._tx() as c:
        c.execute("UPDATE research_plans SET canonical_json="
                  "replace(canonical_json, 'genuine', 'genuinE')")
    assert re_.record_from_journal(j, 1)["refusals"]
    assert j.research_evidence() == []


@pytest.mark.parametrize("fault", ["missing", "sha", "kind", "field"])
def test_collect_fails_closed_on_resolved_source_faults(tmp_path, fault):
    """Direct resolution checks, independent of plan re-verification."""
    j = _journal(tmp_path, [W, D])
    [prow] = j.research_plans()
    plan = json.loads(prow["canonical_json"])
    ctx = re_._ctx(j, plan["source_question"]["question_id"])
    eid = _health_id(j, "source_observation")
    row = dict(ctx["health"][eid])
    if fault == "missing":
        del ctx["health"][eid]
    elif fault == "sha":
        ctx["health"][eid] = dict(row, detail=row["detail"] + " ")
    elif fault == "kind":
        ctx["health"][eid] = dict(row, kind=ho.KIND_SWEEP)
    else:
        e = plan["hypotheses"][0]["evidence"][0]
        e["fields"] = e["fields"] + ["no.such.field"]
    with pytest.raises(re_.ResearchEvidenceError):
        re_.collect(rp.canonical(plan), plan, ctx)


def test_non_finite_value_fails_closed(tmp_path):
    j = _journal(tmp_path, [W, D])
    at = _source_at(j)
    _decision(j, "d-nan", _iso(at, -1), None, raw_signals=json.dumps(
        [_sig(close_ms=1, extra=float("nan"))]))
    res = re_.record_from_journal(j, 1)
    assert res["inserted"] == [] and "value_not_canonical" in res["refusals"][0][1]


# ── 8 later source mutation cannot mutate stored frozen evidence ────────
def test_later_decision_change_leaves_record_and_fails_load(tmp_path):
    j = _journal(tmp_path, [W, D])
    _decisions(j)
    re_.record_from_journal(j, 1)
    before = _stored(j)
    j.update_decision_outcome("d-sel", True, reason_codes=[])
    assert _stored(j) == before
    with pytest.raises(re_.ResearchEvidenceError, match="source_changed"):
        re_.load(j)


def test_new_decision_entering_selection_fails_load(tmp_path):
    j = _journal(tmp_path, [W, D])
    _decisions(j)
    re_.record_from_journal(j, 1)
    at = _source_at(j)
    _decision(j, "d-backfill", _iso(at, -90), [_sig(close_ms=1)])
    with pytest.raises(re_.ResearchEvidenceError, match="source_changed"):
        re_.load(j)


def test_later_health_edit_leaves_record_and_fails_load(tmp_path):
    j = _journal(tmp_path, [W, D])
    re_.record_from_journal(j, 1)
    before = _stored(j)
    eid = _health_id(j, "source_observation")
    _edit_detail(j, eid, lambda r: r["metrics"].update(trades=0))
    assert _stored(j) == before
    with pytest.raises(re_.ResearchEvidenceError, match="source_changed"):
        re_.load(j)
    # re-collection is refused (plan no longer verifies), never rewritten
    assert re_.record_from_journal(j, 2)["refusals"]
    assert j.research_evidence() == [before]


def test_deleted_sources_are_tolerated_and_listed_absent(tmp_path):
    j = _journal(tmp_path, [W, D])
    _decisions(j)
    re_.record_from_journal(j, 1)
    before = _stored(j)
    eid = _health_id(j, "prior_observation")
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE id=?", (eid,))
        c.execute("DELETE FROM decisions WHERE id IN ('d-sel','d-naive')")
    [rec] = re_.load(j)                  # frozen record still loads intact
    assert re_.canonical(rec) == before["canonical_json"]
    rep = re_.verify_sources(rec, re_._ctx(
        j, rec["source_plan"]["question_id"]))
    assert f"prior_observation:{eid}" in rep["absent"]
    assert {"strategy_signal_decisions:d-sel",
            "strategy_signal_decisions:d-naive"} <= set(rep["absent"])


def test_load_rejects_tampered_stored_record(tmp_path):
    j = _journal(tmp_path, [W, D])
    _decisions(j)
    re_.record_from_journal(j, 1)
    text = _stored(j)["canonical_json"]
    rec = json.loads(text)
    rec["hypotheses"][1]["items"][0]["values"]["metrics.trades"] = 0
    for bad in (text + " ", re_.canonical(rec)):
        with j._tx() as c:
            c.execute("UPDATE research_evidence SET canonical_json=?", (bad,))
        with pytest.raises(re_.ResearchEvidenceError):
            re_.load(j)


def test_load_rejects_missing_or_changed_plan_row(tmp_path):
    j = _journal(tmp_path, [W, D])
    re_.record_from_journal(j, 1)
    with j._tx() as c:
        c.execute("UPDATE research_plans SET canonical_json="
                  "canonical_json||' '")
    with pytest.raises(re_.ResearchEvidenceError, match="source_plan"):
        re_.load(j)
    with j._tx() as c:
        c.execute("DELETE FROM research_plans")
    with pytest.raises(re_.ResearchEvidenceError, match="source_plan_missing"):
        re_.load(j)


def _rehash(rec):
    """Recompute every hash an attacker could recompute."""
    for it in _items(rec):
        it["content_sha256"] = re_._sha(re_.canonical(it["values"]))
        if it["source"]["store"] == "journal.decisions":
            it["source_sha256"] = re_._sha(re_.canonical(it["source_content"]))
    rec["evidence_id"] = re_.evidence_id(rec)
    return re_.canonical(rec)


def _store_text(j, text):
    rec = json.loads(text)
    with j._tx() as c:
        c.execute("UPDATE research_evidence SET canonical_json=?, "
                  "canonical_sha256=?, evidence_id=?",
                  (text, re_._sha(text), rec["evidence_id"]))


# ── review blocker 1: frozen values are bound to their bound sources ────
@pytest.mark.parametrize("role,field,bad", [
    ("source_observation", "metrics.trades", {"not": "a number"}),
    ("source_observation", "metrics.per_symbol", {"X": 1}),
    ("question_change_flags",
     "changes_since_prior.thresholds_changed", "wrong type")])
def test_rehashed_value_substitution_is_rejected(tmp_path, role, field, bad):
    j = _journal(tmp_path, [W, D])
    re_.record_from_journal(j, 1)
    rec = json.loads(_stored(j)["canonical_json"])
    for it in _items(rec, role):
        it["values"][field] = bad
    text = _rehash(rec)
    [prow] = j.research_plans()
    with pytest.raises(re_.ResearchEvidenceError, match="item_values"):
        re_.from_json(text, prow["canonical_json"])
    _store_text(j, text)
    with pytest.raises(re_.ResearchEvidenceError):
        re_.load(j)                       # sources still present
    with j._tx() as c:                    # ...and after they are deleted
        c.execute("DELETE FROM brain_events")
        c.execute("DELETE FROM research_questions")
    with pytest.raises(re_.ResearchEvidenceError):
        re_.load(j)


@pytest.mark.parametrize("edit", [
    lambda it: it["source_identity"].update(event_id=10 ** 6),
    lambda it: it["source_identity"].update(subject="s2"),
    lambda it: it.update(source_content=it["source_content"] + " "),
    lambda it: it.update(source_sha256="0" * 64)])
def test_health_identity_and_content_bound_to_plan(tmp_path, edit):
    j = _journal(tmp_path, [W, D])
    re_.record_from_journal(j, 1)
    rec = json.loads(_stored(j)["canonical_json"])
    edit(_items(rec, "source_observation")[0])
    [prow] = j.research_plans()
    with pytest.raises(re_.ResearchEvidenceError):
        re_.from_json(_rehash(rec), prow["canonical_json"])


def test_frozen_content_with_consistent_fake_detail_is_rejected(tmp_path):
    """Re-extractable but unbound content: the plan binding hash fails."""
    j = _journal(tmp_path, [W, D])
    re_.record_from_journal(j, 1)
    rec = json.loads(_stored(j)["canonical_json"])
    it = _items(rec, "source_observation")[0]
    detail = json.loads(it["source_content"])
    detail["metrics"]["trades"] = 999
    it["source_content"] = json.dumps(detail)
    it["source_sha256"] = re_._sha(it["source_content"])
    it["values"]["metrics.trades"] = 999
    [prow] = j.research_plans()
    with pytest.raises(re_.ResearchEvidenceError, match="binding"):
        re_.from_json(_rehash(rec), prow["canonical_json"])


# ── review blocker 4: exact JSON types in skeleton and identity ────────
def _as_float(d, k):
    d[k] = float(d[k])


_TYPE_SUBSTITUTIONS = {
    "plan_binding.event_id": ("source_observation",
                              lambda it: _as_float(it["plan_binding"],
                                                   "event_id")),
    "locator.event_id": ("source_observation",
                         lambda it: _as_float(it["locator"], "event_id")),
    "source_identity.event_id": ("source_observation",
                                 lambda it: _as_float(it["source_identity"],
                                                      "event_id")),
    "question.source_event_id": ("question_change_flags",
                                 lambda it: _as_float(it["source_identity"],
                                                      "source_event_id")),
    "decisions.bar_cutoff": ("strategy_signal_decisions",
                             lambda it: _as_float(
                                 it["locator"],
                                 "signal_bar_close_ms_at_or_before")),
    "decisions.identity_cutoff": ("strategy_signal_decisions",
                                  lambda it: _as_float(
                                      it["source_identity"],
                                      "signal_bar_close_ms_at_or_before")),
    "section.status_none_vs_false": (None, None),
}


@pytest.mark.parametrize("deleted", [False, True])
@pytest.mark.parametrize("name", sorted(_TYPE_SUBSTITUTIONS))
def test_numeric_type_substitution_is_rejected(tmp_path, name, deleted):
    j = _journal(tmp_path, [W, D])
    _decisions(j)
    re_.record_from_journal(j, 1)
    rec = json.loads(_stored(j)["canonical_json"])
    role, edit = _TYPE_SUBSTITUTIONS[name]
    if role is None:                       # section-level: null -> false
        rec["hypotheses"][0]["unavailable_reason"] = False
    else:
        edit(_items(rec, role)[0])
    text = _rehash(rec)
    assert text != _stored(j)["canonical_json"]
    [prow] = j.research_plans()
    with pytest.raises(re_.ResearchEvidenceError):
        re_.from_json(text, prow["canonical_json"])
    _store_text(j, text)
    if deleted:
        with j._tx() as c:
            c.execute("DELETE FROM brain_events")
            c.execute("DELETE FROM research_questions")
            c.execute("DELETE FROM decisions")
    with pytest.raises(re_.ResearchEvidenceError):
        re_.load(j)


# ── review blocker 2: frozen decision selection is self-verifying ───────
def _decision_item(rec):
    [it] = _items(rec, "strategy_signal_decisions")
    return it


def test_invalid_frozen_selection_fails_even_after_deletion(tmp_path):
    j = _journal(tmp_path, [W, D])
    _decisions(j)
    re_.record_from_journal(j, 1)
    rec = json.loads(_stored(j)["canonical_json"])
    it = _decision_item(rec)
    [sel] = [r for r in it["values"]["selected_rows"]
             if r["decision_id"] == "d-sel"]
    sel["selected_signals"] = [_sig("s2", 10 ** 15)]
    sel["selected_entry_indexes"] = [False]
    sel["row_sha256"] = "not a hash"
    text = _rehash(rec)
    _store_text(j, text)
    with pytest.raises(re_.ResearchEvidenceError):
        re_.load(j)
    with j._tx() as c:
        c.execute("DELETE FROM decisions WHERE id='d-sel'")
    with pytest.raises(re_.ResearchEvidenceError, match="item_values"):
        re_.load(j)


@pytest.mark.parametrize("edit", [
    # raw content altered consistently with values: still re-derived
    lambda it: it["source_content"].pop(),
    lambda it: it["source_content"].reverse(),
    lambda it: it["source_content"].append(dict(
        it["source_content"][0], id="zz-extra",
        signals_json="[]")),
    lambda it: it["source_content"][0].update(executed=True),
    lambda it: it["source_content"][0].update(extra=1),
    lambda it: it["values"]["selected_rows"].reverse(),
    lambda it: it["values"]["reported"].pop(),
])
def test_frozen_decision_content_contract(tmp_path, edit):
    j = _journal(tmp_path, [W, D])
    _decisions(j)
    re_.record_from_journal(j, 1)
    rec = json.loads(_stored(j)["canonical_json"])
    edit(_decision_item(rec))
    [prow] = j.research_plans()
    with pytest.raises(re_.ResearchEvidenceError):
        re_.from_json(_rehash(rec), prow["canonical_json"])


def test_deleted_decisions_keep_a_verifiable_frozen_selection(tmp_path):
    j = _journal(tmp_path, [W, D])
    _decisions(j)
    re_.record_from_journal(j, 1)
    before = _stored(j)["canonical_json"]
    with j._tx() as c:
        c.execute("DELETE FROM decisions")
    [rec] = re_.load(j)
    it = _decision_item(rec)
    # every row hash and the selection recompute from the frozen content
    raw = {r["id"]: r for r in it["source_content"]}
    for sel in it["values"]["selected_rows"]:
        assert sel["row_sha256"] == re_._sha(re_.canonical(raw[sel["decision_id"]]))
    assert re_.select_decisions(it["locator"], it["source_content"]) \
        == it["values"]
    assert re_.canonical(rec) == before


# ── review blocker 3: decision instants at full precision ───────────────
def _selected_ids(j):
    re_.record_from_journal(j, 1)
    return [r["decision_id"] for r in
            _decision_item(re_.load(j)[0])["values"]["selected_rows"]]


def test_microsecond_after_cutoff_is_not_selected(tmp_path):
    j = _journal(tmp_path, [W, D])
    at = datetime.fromisoformat(_source_at(j))
    one_us = timedelta(microseconds=1)
    _decision(j, "d-after-1us", (at + one_us).isoformat(), [_sig(close_ms=1)])
    _decision(j, "d-at", at.isoformat(), [_sig(close_ms=1)])
    _decision(j, "d-before-1us", (at - one_us).isoformat(), [_sig(close_ms=1)])
    assert _selected_ids(j) == ["d-before-1us", "d-at"]


def test_sub_millisecond_instants_sort_by_time_not_id(tmp_path):
    j = _journal(tmp_path, [W, D])
    at = datetime.fromisoformat(_source_at(j)) - timedelta(minutes=1)
    # same millisecond; id order is the reverse of time order
    _decision(j, "a-later", (at + timedelta(microseconds=900)).isoformat(),
              [_sig(close_ms=1)])
    _decision(j, "b-earlier", (at + timedelta(microseconds=100)).isoformat(),
              [_sig(close_ms=1)])
    assert _selected_ids(j) == ["b-earlier", "a-later"]


def test_equivalent_offsets_compare_as_the_same_instant(tmp_path):
    from datetime import timezone
    j = _journal(tmp_path, [W, D])
    at = datetime.fromisoformat(_source_at(j))
    plus5 = timezone(timedelta(hours=5))
    # the cutoff instant written in +05:00 is still at-or-before
    _decision(j, "d-at-plus5", at.astimezone(plus5).isoformat(),
              [_sig(close_ms=1)])
    # 1 us later in +05:00 is after
    _decision(j, "d-after-plus5",
              (at + timedelta(microseconds=1)).astimezone(plus5).isoformat(),
              [_sig(close_ms=1)])
    # an earlier instant whose +05:00 text sorts after the UTC text
    _decision(j, "d-early-plus5",
              (at - timedelta(hours=1)).astimezone(plus5).isoformat(),
              [_sig(close_ms=1)])
    assert _selected_ids(j) == ["d-early-plus5", "d-at-plus5"]


def test_signal_bar_cutoff_is_integer_ms_inclusive(tmp_path):
    j = _journal(tmp_path, [W, D])
    at = _source_at(j)
    hi = rq._ms(at)
    _decision(j, "d-bar-eq", _iso(at, -1), [_sig(close_ms=hi)])
    _decision(j, "d-bar-over", _iso(at, -2), [_sig(close_ms=hi + 1)])
    assert _selected_ids(j) == ["d-bar-eq"]


# ── 9 extra score / conclusion / support / refute / asset fields ────────
_ADDED = ("score", "conclusion", "supported", "refuted", "support",
          "contradiction", "asset", "probability", "salience", "usefulness")


def _where(rec, level):
    return {"record": rec, "section": rec["hypotheses"][0],
            "item": rec["hypotheses"][0]["items"][0],
            "source": rec["hypotheses"][0]["items"][0]["source"],
            "values": rec["hypotheses"][0]["items"][0]["values"],
            "decision_values": _items(rec, "strategy_signal_decisions")[0][
                "values"],
            "decision_row": _items(rec, "strategy_signal_decisions")[0][
                "values"]["selected_rows"][0]}[level]


@pytest.mark.parametrize("level", ["record", "section", "item", "source",
                                   "values", "decision_values",
                                   "decision_row"])
@pytest.mark.parametrize("key", _ADDED)
def test_added_conclusion_or_asset_fields_are_rejected(tmp_path, level, key):
    j = _journal(tmp_path, [W, D], name=f"{level}-{key}.db")
    _decisions(j)
    re_.record_from_journal(j, 1)
    [prow] = j.research_plans()
    rec = json.loads(_stored(j)["canonical_json"])
    _where(rec, level)[key] = 1
    _rehash(rec)
    with pytest.raises(re_.ResearchEvidenceError):
        re_.from_json(re_.canonical(rec), prow["canonical_json"])


def test_record_structure_carries_no_conclusion_or_asset_fields(tmp_path):
    j = _journal(tmp_path, [W, EF, D])
    _decisions(j)
    re_.record_from_journal(j, 1)
    [rec] = re_.load(j)
    # frozen source values are verbatim source content; the collector's own
    # structure (everything outside values) carries no judgement
    shell = json.loads(re_.canonical(rec))
    for it in _items(shell):
        it.pop("values")
        it.pop("source_content")          # the source's own raw content
        it.pop("locator")                 # the plan's own locator, verbatim
    assert not (set(_keys(shell)) & (_NO_CONCLUSION | set(_ADDED)))
    assert set(rec) == set(re_._RECORD_KEYS)
    for s in rec["hypotheses"]:
        assert set(s) == set(re_._SECTION_KEYS)
        assert all(set(it) == set(re_._ITEM_KEYS) for it in s["items"])


# ── legacy journals ─────────────────────────────────────────────────────
def test_legacy_journal_without_evidence_table_remains_readable(tmp_path):
    db = tmp_path / "legacy.db"
    j = _journal(tmp_path, [W, D], name="legacy.db")
    with j._tx() as c:
        c.execute("DROP TABLE research_evidence")
    j2 = Journal(db)
    assert j2.research_evidence() == [] and re_.load(j2) == []
    assert len(rp.load(j2)) == 1
    assert len(re_.record_from_journal(j2, 1)["inserted"]) == 1


def test_journal_without_plans_collects_nothing(tmp_path):
    j = Journal(tmp_path / "e.db")
    assert re_.record_from_journal(j, 1) == {"inserted": [], "duplicate": [],
                                             "conflict": [], "refusals": []}


# ── 10 no live-loop / Risk / Execution / Attention wiring ───────────────
def test_module_imports_only_contract_modules():
    src = Path(re_.__file__).read_text()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert mods <= {"__future__", "hashlib", "json", "datetime",
                    "trader.cognition", "trader.strategy"}
    names = {a.name for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.ImportFrom) for a in n.names}
    assert names == {"annotations", "datetime", "research_plan",
                     "research_question", "health_observation",
                     "signal_occurrence_observation"}


def test_nothing_in_the_live_path_calls_it():
    root = Path(re_.__file__).resolve().parents[1]
    pat = re.compile(r"research_evidence|record_research_evidence")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_evidence.py"
                     and pat.search(p.read_text(errors="ignore")))
    # the journal owns the storage primitive; nothing else references it
    assert callers == ["core/journal.py"]
    assert "research_evidence import" not in (root / "core/journal.py").read_text()


def test_collection_is_read_only_on_evidence(tmp_path):
    j = _journal(tmp_path, [W, D])
    _decisions(j)
    tables = ("brain_events", "research_questions", "research_plans",
              "decisions", "trades")
    before = {t: j.query(f"SELECT * FROM {t}") for t in tables}
    re_.record_from_journal(j, 1)
    re_.load(j)
    assert {t: j.query(f"SELECT * FROM {t}") for t in tables} == before
