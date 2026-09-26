"""SDD-STAGE-3-RESEARCH-BANK-RUN-SCOPED-FILING-V1.

research_bank.record_run(journal, run_id, now_ms) files Research Bank
objects for one stored research-run.v1 with exactly record_from_journal's
semantics for that run: the same verification, inserted / duplicate /
conflict / refusal outcomes and bank rows. It reads no other stored run's
research_runs row and no stored bank object. Chain verification reads by
key (SDD-STAGE-3-RESEARCH-CHAIN-SCOPED-VERIFICATION-V1; see
tests/test_research_chain_scoped_verification.py). Nothing live calls it.
"""
import json
import re
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_bank import (_bank_row, _ran,
                                                     _set_bank, _sha)
from tests.test_strategy_decay_research_run import _clock, _copy, _fresh
from trader.cognition import research_bank as rb
from trader.cognition import research_evidence as re_
from trader.cognition import research_result as rr
from trader.cognition import research_run as run_
from trader.core.journal import Journal

EMPTY = {"inserted": [], "duplicate": [], "conflict": [], "refusals": []}


def _run_ids(j):
    return [r["run_id"] for r in j.research_runs()]


def _two_runs(tmp_path):
    j = _ran(tmp_path)
    run_.run(j, "k2", 5, clock=_clock())
    return j, _run_ids(j)


# ── single-run fixtures covering every filing outcome ────────────────────
def _good(tmp_path):
    return _ran(tmp_path)


def _failed(tmp_path, monkeypatch):
    j = _fresh(tmp_path)

    def boom(journal, now_ms):
        raise RuntimeError("collector unavailable")
    monkeypatch.setattr(re_, "record_from_journal", boom)
    run_.run(j, "k", 1)
    monkeypatch.undo()
    return j


def _no_result(tmp_path):
    j = Journal(tmp_path / "e.db")
    run_.run(j, "k", 1)
    return j


def _invalid(tmp_path):
    j = _ran(tmp_path)
    with j._tx() as c:
        c.execute("DELETE FROM research_results")
    return j


def _result_conflict(tmp_path, monkeypatch):
    j = _fresh(tmp_path)
    orig = rr.record_from_journal

    def conflicting(journal, now_ms):
        out = orig(journal, now_ms=now_ms)
        out["conflict"], out["inserted"] = out["inserted"], []
        return out
    monkeypatch.setattr(rr, "record_from_journal", conflicting)
    run_.run(j, "k", 1)
    monkeypatch.undo()
    return j


def _malformed(tmp_path):
    j = _ran(tmp_path)
    with j._tx() as c:
        c.execute("UPDATE research_runs SET canonical_json='{}'")
    return j


def _filed_twice(tmp_path):
    j = _ran(tmp_path)
    rb.record_from_journal(j, now_ms=2)
    return j


def _stored_conflict(tmp_path):
    j = _ran(tmp_path)
    rb.record_from_journal(j, now_ms=2)
    rec = json.loads(_bank_row(j)["canonical_json"])
    rec["next_questions"] = {"status": "NOT_AVAILABLE", "reason": "x"}
    text = rb.canonical(rec)
    _set_bank(j, canonical_json=text, canonical_sha256=_sha(text))
    return j


CASES = {"good": _good, "failed": _failed, "no_result": _no_result,
         "invalid": _invalid, "result_conflict": _result_conflict,
         "malformed": _malformed, "duplicate": _filed_twice,
         "stored_conflict": _stored_conflict}


def _build(name, tmp_path, monkeypatch):
    fn = CASES[name]
    return (fn(tmp_path, monkeypatch)
            if fn in (_failed, _result_conflict) else fn(tmp_path))


# ── 1 record_run files exactly the requested run ─────────────────────────
def test_record_run_files_exactly_the_requested_run(tmp_path):
    j, (r1, r2) = _two_runs(tmp_path)
    res = rb.record_run(j, r2, now_ms=6)
    assert len(res["inserted"]) == 1 and res["refusals"] == []
    assert [r["run_id"] for r in j.research_bank_objects()] == [r2]
    res1 = rb.record_run(j, r1, now_ms=7)
    assert len(res1["inserted"]) == 1
    assert [r["run_id"] for r in j.research_bank_objects()] == [r2, r1]


# ── 2 another stored run is neither read nor filed ──────────────────────
def test_another_stored_run_is_not_read_or_filed(tmp_path, monkeypatch):
    j, (r1, r2) = _two_runs(tmp_path)
    with j._tx() as c:      # a global filer would refuse this run
        c.execute("UPDATE research_runs SET canonical_json='{}' "
                  "WHERE run_id=?", (r1,))
    seen = []
    orig_runs = j.research_runs

    def research_runs(run_id=None):
        seen.append(run_id)
        return orig_runs(run_id=run_id)

    def research_bank_objects(run_id=None):
        raise AssertionError("stored bank scan")
    monkeypatch.setattr(j, "research_runs", research_runs)
    monkeypatch.setattr(j, "research_bank_objects", research_bank_objects)
    res = rb.record_run(j, r2, now_ms=6)
    assert res["refusals"] == [] and len(res["inserted"]) == 1
    assert seen and set(seen) == {r2}
    monkeypatch.undo()
    assert [r["run_id"] for r in j.research_bank_objects()] == [r2]
    assert rb.record_from_journal(j, now_ms=7)["refusals"][0][0] == r1


# ── 3/4/5 identical to record_from_journal for that run ─────────────────
@pytest.mark.parametrize("name", sorted(CASES))
def test_output_and_rows_equal_record_from_journal(tmp_path, monkeypatch,
                                                   name):
    j = _build(name, tmp_path, monkeypatch)
    twin = _copy(tmp_path, j, "twin.db")
    [rid] = _run_ids(j)
    got = rb.record_run(j, rid, now_ms=9)
    want = rb.record_from_journal(twin, now_ms=9)
    assert got == want
    assert j.research_bank_objects() == twin.research_bank_objects()


def test_per_run_filing_composes_to_the_global_filer(tmp_path):
    j, rids = _two_runs(tmp_path)
    with j._tx() as c:
        c.execute("UPDATE research_runs SET canonical_json='{}' "
                  "WHERE run_id=?", (rids[0],))
    run_.run(j, "k3", 8, clock=_clock())
    twin = _copy(tmp_path, j, "twin.db")
    parts = [rb.record_run(j, r, now_ms=9) for r in _run_ids(j)]
    merged = {k: [x for p in parts for x in p[k]] for k in EMPTY}
    assert merged == rb.record_from_journal(twin, now_ms=9)
    assert j.research_bank_objects() == twin.research_bank_objects()


def test_duplicate_is_reported_and_restart_idempotent(tmp_path):
    j = _ran(tmp_path)
    [rid] = _run_ids(j)
    first = rb.record_run(j, rid, now_ms=2)
    before = j.research_bank_objects()
    for journal in (j, Journal(j.db_path)):
        again = rb.record_run(journal, rid, now_ms=50)
        assert again == dict(EMPTY, duplicate=first["inserted"])
        assert journal.research_bank_objects() == before


def test_stored_conflict_is_reported_not_overwritten(tmp_path):
    j = _stored_conflict(tmp_path)
    tampered = j.research_bank_objects()
    [rid] = _run_ids(j)
    res = rb.record_run(j, rid, now_ms=3)
    assert len(res["conflict"]) == 1 and res["inserted"] == []
    assert j.research_bank_objects() == tampered


@pytest.mark.parametrize("rid", ["no-such-run", "", None, 7])
def test_missing_run_fails_closed(tmp_path, rid):
    j = _ran(tmp_path)
    assert rb.record_run(j, rid, now_ms=2) == dict(
        EMPTY, refusals=[(rid, None, "source_missing:run")])
    assert j.research_bank_objects() == []


def test_malformed_run_fails_closed(tmp_path):
    j = _malformed(tmp_path)
    [rid] = _run_ids(j)
    [(got, xid, why)] = rb.record_run(j, rid, now_ms=2)["refusals"]
    assert (got, xid) == (rid, None)
    assert why.startswith("source_invalid:run:")
    assert j.research_bank_objects() == []


# ── 6 record_from_journal behaviour unchanged ───────────────────────────
def test_record_from_journal_still_files_every_stored_run(tmp_path):
    j, rids = _two_runs(tmp_path)
    res = rb.record_from_journal(j, now_ms=6)
    assert len(res["inserted"]) == 2 and res["refusals"] == []
    assert [r["run_id"] for r in j.research_bank_objects()] == rids


# ── 7 no live caller; no retired invocation module ──────────────────────
def test_no_live_caller_and_no_invocation_module():
    root = Path(rb.__file__).resolve().parents[1]
    repo = root.parent
    pat = re.compile(r"\brecord_run\b")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_bank.py"
                     and pat.search(p.read_text(errors="ignore")))
    assert callers == []
    assert not (root / "cognition/research_invocation.py").exists()
    hits = [str(p.relative_to(repo)) for d in ("trader", "tests")
            for p in (repo / d).rglob("*.py")
            if p.name != Path(__file__).name
            and "research_invocation" in p.read_text(errors="ignore")]
    assert hits == []
