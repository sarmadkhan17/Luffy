"""research-shadow-report.v1: one read-only, context_only owner view of one
explicitly named invocation across both research families."""
import json
import sqlite3

import pytest

from tests.research_shadow_fixtures import dump, make_source, table_rows
from trader.cognition import research_bank as rb
from trader.cognition import research_cost as rc
from trader.cognition import research_families as rf
from trader.cognition import research_shadow as rs
from trader.cognition import research_shadow_contract as sc
from trader.cognition import research_shadow_report as rep
from trader.cognition import research_shadow_store as st
from trader.cognition import research_unreadable_bank as ub


@pytest.fixture
def run(tmp_path):
    src = make_source(tmp_path / "luffy.db")
    shadow = tmp_path / "research_shadow.db"
    res = rs.invoke(source_db=src, shadow_db=shadow, max_sources=50,
                    wall_clock_deadline_s=120, invocation_key="k",
                    recorded_at_ms=1000)
    assert res["receipt"]["outcome"] == sc.OK
    return src, shadow, res


def _build(src, shadow, inv, recall=None):
    return rep.build(shadow_db=shadow, source_db=src, invocation_id=inv,
                     recall_max_objects=recall)


def test_report_covers_both_families_with_verified_objects(run):
    src, shadow, res = run
    out = _build(src, shadow, res["invocation_id"])
    assert out["schema"] == rep.SCHEMA and out["authority"] == "context_only"
    assert out["read_only"] is True
    assert out["invocation"]["receipt"] == res["receipt"]
    assert out["invocation"]["canonical_sha256"] == res["canonical_sha256"]
    assert [f["family"] for f in out["families"]] == list(rf.FAMILY_NAMES)
    stored = {r["bank_object_id"]: json.loads(r["canonical_json"])
              for t in ("research_bank_objects",
                        "research_unreadable_bank_objects")
              for r in table_rows(shadow, t)}
    for fam in out["families"]:
        assert fam["recall"] is None
        assert fam["object_order"] == rep.OBJECT_ORDER
        assert fam["bank_objects"]
        run_ = next(r for r in res["receipt"]["family_runs"]
                    if r["family"] == fam["family"])
        f = run_["bank_filing"]
        assert [o["bank_object_id"] for o in fam["bank_objects"]] == \
            list(dict.fromkeys(f["inserted"] + f["duplicate"]))
        for o in fam["bank_objects"]:
            assert o["state"] == rep.VERIFIED
            assert o["links"] == stored[o["bank_object_id"]]["links"]
            assert o["bank_registration"]["state"] == rep.VERIFIED
            assert o["question_registration"]["state"] == rep.VERIFIED
            assert o["run_id"] == run_["run_id"]
        assert [s["source_event_id"] for s in fam["sources"]] == [
            s["source_event_id"] for s in res["receipt"]["sources"]
            if s["dispatch"]["family"] == fam["family"]]
    decay, unread = out["families"]
    assert decay["bank_objects"][0]["structural_result"] == {
        "result_status": "INCONCLUSIVE",
        "result_reason": "no_registered_falsifier_predicates"}
    assert unread["bank_objects"][0]["structural_result"]["status"] == \
        "INCONCLUSIVE"


def test_cost_uses_the_ledger_with_unreadable_coverage_enabled(run):
    src, shadow, res = run
    cost = _build(src, shadow, res["invocation_id"])["cost"]
    assert cost["schema"] == rc.SCHEMA
    assert cost["total_research_cost"]["state"] == rc.NOT_ESTABLISHED
    assert [f["family"] for f in cost["families"]] == [
        rc.RECEIPT_FAMILY, rc.RUN_FAMILY, rc.UNREADABLE_FAMILY]
    # the ledger's default mode is unchanged: two families only
    j = st.open_shadow(shadow, src, readonly=True)
    try:
        assert len(rc.from_sources(journal=j)["families"]) == 2
    finally:
        j.close()


def test_recall_only_when_explicitly_requested(run):
    src, shadow, res = run
    out = _build(src, shadow, res["invocation_id"], recall=5)
    for fam in out["families"]:
        assert fam["recall"]["max_objects"] == 5
        qs = fam["recall"]["questions"]
        assert [q["question_id"] for q in qs] == [
            s["question_id"] for s in fam["sources"]]
        for q in qs:
            assert q["state"] == "RECALLED"
            assert q["recall"]["authority"] == "context_only"
            assert q["recall"]["bound"]["max_objects"] == 5


@pytest.mark.parametrize("bad", [0, -1, True, 201, 2.0])
def test_recall_bound_must_be_explicit_and_valid(run, bad):
    src, shadow, res = run
    with pytest.raises(rep.ShadowReportError):
        _build(src, shadow, res["invocation_id"], recall=bad)


def test_no_default_invocation(run):
    src, shadow, _res = run
    with pytest.raises(TypeError):
        rep.build(shadow_db=shadow, source_db=src, recall_max_objects=None)
    with pytest.raises(rep.ShadowReportError):
        _build(src, shadow, "latest")
    with pytest.raises(rep.ShadowReportError):
        _build(src, shadow, "0" * 64)
    with pytest.raises(SystemExit):
        rep.main(["--shadow-db", str(shadow), "--source-db", str(src)])


def test_report_is_read_only(run):
    src, shadow, res = run
    before_src, before_shadow = dump(src), dump(shadow)
    _build(src, shadow, res["invocation_id"], recall=3)
    assert dump(src) == before_src and dump(shadow) == before_shadow
    j = st.open_shadow(shadow, src, readonly=True)
    try:
        with pytest.raises(st.ShadowStoreError):
            with j.unit():
                pass
        with pytest.raises(sqlite3.Error):
            j._conn().execute("DELETE FROM research_shadow_cursor")
    finally:
        j.close()


def test_legacy_receipt_less_rows_are_refused_where_recall_is_requested(
        run):
    src, shadow, res = run
    c = sqlite3.connect(shadow)
    c.execute("DROP TRIGGER research_registrations_no_delete")
    c.execute("DELETE FROM research_registrations")
    c.commit()
    c.close()
    out = _build(src, shadow, res["invocation_id"], recall=3)
    for fam in out["families"]:
        for o in fam["bank_objects"]:
            assert o["bank_registration"]["state"] == rep.MISSING
        for q in fam["recall"]["questions"]:
            assert q["state"] == "REFUSED" and q["recall"] is None
            assert "registration_missing" in q["reason"]


def test_tampered_bank_object_is_shown_unverified_only(run):
    src, shadow, res = run
    c = sqlite3.connect(shadow)
    bid = c.execute("SELECT bank_object_id FROM research_bank_objects "
                    "LIMIT 1").fetchone()[0]
    c.execute("UPDATE research_bank_objects SET canonical_json='{}' "
              "WHERE bank_object_id=?", (bid,))
    c.commit()
    c.close()
    out = _build(src, shadow, res["invocation_id"])
    [o] = [o for o in out["families"][0]["bank_objects"]
           if o["bank_object_id"] == bid]
    assert o["state"] == rep.UNVERIFIED and set(o) == {
        "bank_object_id", "state", "reason"}


def _keys(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from _keys(x)
    elif isinstance(v, list):
        for x in v:
            yield from _keys(x)


def test_report_adds_no_ranking_or_score_fields(run):
    src, shadow, res = run
    out = _build(src, shadow, res["invocation_id"], recall=3)
    top = set(out) | {k for f in out["families"] for k in f} | {
        k for f in out["families"] for o in f["bank_objects"] for k in o}
    for bad in ("score", "rank", "salience", "priority", "usefulness",
                "latest", "best", "important"):
        assert not [k for k in top if bad in k.lower()]


def test_cli_prints_the_report(run, capsys):
    src, shadow, res = run
    assert rep.main(["--shadow-db", str(shadow), "--source-db", str(src),
                     "--invocation-id", res["invocation_id"]]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["request"] == {"invocation_id": res["invocation_id"],
                              "recall_max_objects": None}
