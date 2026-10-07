"""STR-01: RES-08 lineage is authenticated against the Research Bank.

One real SUPPORTED cycle (synthetic numerical child receipts, the real planner,
referee writer and Bank) is run once; every attack then resolves through the
canonical `predictive_extensions` replay, never through caller-supplied ids."""
import copy
import json
import shutil
import sqlite3
from contextlib import closing

import pytest

from trader.cognition import predictive_bank
from trader.core.journal import Journal
from trader.research import predictive_bridge as B
from trader.strategy import factory_handoff as F
from tests.test_investigation_state_feedback import paths  # noqa: F401
from tests.test_predictive_research_bridge import (  # noqa: F401
    CFG, NumericalFixture, consume, population, saved)

AT = 1_790_000_000_000


@pytest.fixture
def lineage(population):
    source, bank, _c, created, later, _s = population
    fake = NumericalFixture("SUPPORTED")
    consume(population, fake, created)
    for _ in range(6):
        consume(population, fake, later)
    final = next(r for r in saved(bank, "bridge_bank_results")
                 if r["classification"] == "SUPPORTED" and r["experiment_id"])
    cand = final["referee_disposition"]["factory_candidate"]
    j = Journal(cand["quantitative_ledger"])
    return j, bank, final, B.factory_source(final, bank)


def _refused(j, src, code):
    with pytest.raises(F.HandoffRefused) as e:
        F.create_version(j, CFG, src, at_ms=AT)
    assert e.value.code == code
    assert j.query("SELECT name FROM sqlite_master WHERE name="
                   "'strategy_versions'") == [] or \
        j.query("SELECT * FROM strategy_versions") == []


def _with(src, **research):
    return {**src, "research": {**src["research"], **research}}


def test_genuine_exact_bank_lineage_creates_one_validated_version(lineage):
    j, bank, final, src = lineage
    v = F.create_version(j, CFG, src, at_ms=AT)
    assert F.state_of(j, v["version_id"]) == F.VALIDATED
    rec = F.load_version(j, v["version_id"])
    r = rec["lineage"]["research"]
    assert {k: r[k] for k in src["research"]} == \
        src["research"]
    assert r["bank_authentication"] == "REPLAYED_AGAINST_REGISTERED_LEDGER"
    assert r["bank_id"] == final["bank_id"] and r["experiment_binding_sha256"]
    F.verify_validation(j, rec)
    # restart / replay: same authenticated lineage, same identity, one row
    j2 = Journal(j.db_path)
    again = F.create_version(j2, CFG, B.factory_source(final, bank),
                             at_ms=AT + 1)
    assert again["version_id"] == v["version_id"] and again["status"] != "inserted"
    assert F.load_version(j2, v["version_id"]) == rec
    assert len(j2.query("SELECT * FROM strategy_versions")) == 1


def test_forged_well_formed_ids_are_refused(lineage):
    j, _b, _f, src = lineage
    fake = "f" * 64
    _refused(j, _with(src, result_id=fake), "research_bank_result_not_found")
    _refused(j, _with(src, measurement_id=fake),
             "research_bank_lineage_mismatch")
    _refused(j, _with(src, quantitative_evidence_id=fake),
             "research_bank_lineage_mismatch")


def test_ids_crossed_from_another_valid_result_are_refused(lineage):
    j, bank, final, src = lineage
    others = [r for r in saved(bank, "bridge_bank_results")
              if r["result_id"] != final["result_id"]]
    assert others
    for o in others:
        _refused(j, _with(src, result_id=o["result_id"]),
                 "research_bank_lineage_mismatch")
        if o["measurement_id"] not in (None, final["measurement_id"]):
            _refused(j, _with(src, measurement_id=o["measurement_id"]),
                     "research_bank_lineage_mismatch")
        if o["quantitative_evidence_id"] not in (None, final["quantitative_evidence_id"]):
            _refused(j, _with(src, quantitative_evidence_id=o[
                "quantitative_evidence_id"]), "research_bank_lineage_mismatch")
        if o["hypothesis_id"] != final["hypothesis_id"]:
            _refused(j, _with(src, hypothesis_id=o["hypothesis_id"]),
                     "research_lineage_mismatch")


def test_measurement_result_mismatch_and_wrong_candidate_are_refused(
        lineage, monkeypatch):
    j, _b, final, src = lineage
    real = predictive_bank.predictive_extensions

    def tampered(mut):
        def f(path, bank_id):
            att = copy.deepcopy(real(path, bank_id))
            mut(next(r for r in att["results"]
                     if r["result_id"] == final["result_id"]), att)
            return att
        return f

    def swap(name, fn, code):
        monkeypatch.setattr(predictive_bank, "predictive_extensions",
                            tampered(fn))
        _refused(j, src, code)
    # the retained result no longer names the registered look/gates
    swap("look", lambda r, a: r["referee_disposition"]["evidence"][
        "gate1_look"].update(p=0.5), "research_bank_evidence_mismatch")
    swap("cand", lambda r, a: r["referee_disposition"]["factory_candidate"]
         .update(hash="other"), "research_bank_candidate_mismatch")
    # an unsupported/inconclusive/UNTESTED result is no validated support
    for cls, q in (("INCONCLUSIVE", "UNTESTED"), ("UNSUPPORTED", "FAIL"),
                   ("SUPPORTED", "UNTESTED")):
        swap(cls, lambda r, a, c=cls, q=q: r.update(classification=c,
             quantitative_outcome=q), "research_result_not_supported")


def test_stale_result_of_an_older_experiment_revision_is_refused(
        lineage, monkeypatch):
    j, _b, final, src = lineage
    real = predictive_bank.predictive_extensions

    def newer(path, bank_id):
        att = copy.deepcopy(real(path, bank_id))
        ex = copy.deepcopy(next(e for e in att["experiments"]
                                if e["experiment_id"] == final["experiment_id"]))
        ex["experiment_id"] = "newer-revision"       # a later experiment
        att["experiments"].append(ex)
        return att
    monkeypatch.setattr(predictive_bank, "predictive_extensions", newer)
    _refused(j, src, "research_result_stale")


def test_tampered_or_unresolvable_bank_is_refused(lineage, tmp_path):
    j, bank, _f, src = lineage
    copy_dir = tmp_path / "copy"
    copy_dir.mkdir()
    shutil.copy(bank, copy_dir / bank.name)
    shutil.copytree(bank.parent / (bank.stem + "-experiments"),
                    copy_dir / (bank.stem + "-experiments"))
    with closing(sqlite3.connect(copy_dir / bank.name)) as db:
        db.execute("DROP TRIGGER bridge_bank_results_no_update")
        db.execute("UPDATE bridge_bank_results SET payload=replace(payload,"
                   "'SUPPORTED','UNSUPPORTED') WHERE payload LIKE "
                   "'%\"SUPPORTED\"%'")
        db.commit()
    _refused(j, {**src, "bank_path": str(copy_dir / bank.name)},
             "research_bank_unauthenticated")
    _refused(j, {**src, "bank_path": str(tmp_path / "missing.db")},
             "research_bank_unauthenticated")
    _refused(j, {k: v for k, v in src.items() if k != "bank_path"},
             "research_bank_required")


def test_changed_genuine_bank_revision_is_a_new_version_old_unchanged(lineage):
    j, _b, _f, src = lineage
    v = F.create_version(j, CFG, src, at_ms=AT)
    rec = F.load_version(j, v["version_id"])
    lin = copy.deepcopy(rec["lineage"])
    lin["research"]["experiment_binding_sha256"] = "0" * 64
    new = F._version_record(rec["strategy_id"], rec["spec"], rec["spec_hash"],
                            None, rec["source"], rec["evidence_ids"], lin)
    assert new["version_id"] != rec["version_id"]
    assert F.load_version(j, v["version_id"]) == rec
