"""STR-01: one immutable typed StrategySpec/StrategyVersion lineage.

`factory_handoff.create_version` is the only writer of `strategy_versions`.
A version binds the exact validated research and quantitative identities and
the spec's own requirements; any material change is a new version; creating
one grants no install, activation, capital, owner approval or order authority.
"""
import copy
import json
import pathlib
import re

import pytest

from trader.core.journal import Journal
from trader.strategy import factory_handoff as F
from trader.strategy.spec import StrategySpec
from tests.test_strategy_factory_handoff import (  # noqa: F401
    INPUTS, T0, _candidate, _journal, cfg)

ROOT = pathlib.Path(__file__).resolve().parents[1]
RES = {"hypothesis_id": "hyp1", "experiment_id": "exp1",
       "measurement_id": "m1", "quantitative_evidence_id": "q1",
       "result_id": "r1"}


def _src(h, research=None):
    s = {"kind": "research_candidate", "hash": h}
    if research is not None:
        s["research"] = research
    return s


def _refused(j, cfg, source, code):
    with pytest.raises(F.HandoffRefused) as e:
        F.create_version(j, cfg, source, at_ms=T0)
    assert e.value.code == code
    assert j.query("SELECT * FROM strategy_versions") == []


def _new(tmp_path, cfg, **kw):
    j, h = _journal(tmp_path, **kw)
    v = F.create_version(j, cfg, _src(h), at_ms=T0)
    return j, h, v, F.load_version(j, v["version_id"])


# ── 1. single creation authority ─────────────────────────────────────────
def test_only_factory_handoff_writes_strategy_versions():
    pat = re.compile(r"(INSERT\s+(OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM|"
                     r"REPLACE\s+INTO)\s+strategy_versions", re.I)
    ins = re.compile(r"_insert\(\s*c\s*,\s*[\"']strategy_versions", re.I)
    bad = []
    for p in (ROOT / "trader").rglob("*.py"):
        if p.name == "factory_handoff.py":
            continue
        t = p.read_text(errors="ignore")
        if pat.search(t) or ins.search(t):
            bad.append(str(p.relative_to(ROOT)))
    assert bad == []


def test_all_version_writers_inside_factory_go_through_insert_version():
    src = (ROOT / "trader/strategy/factory_handoff.py").read_text()
    assert len(re.findall(r"_insert_version\(c, rec, at_ms\)", src)) == 2
    assert len(re.findall(r'_insert\(c, "strategy_versions"', src)) == 1


@pytest.mark.parametrize("source", [
    {"kind": "llm_proposal", "hash": "x"}, {"kind": "ui", "hash": "x"},
    {"kind": "scraper", "hash": "x"}, {"kind": "legacy_strategy", "id": "x"},
    {"kind": "spec_writer", "hash": "x"}, {"kind": "derived", "id": "x"}])
def test_llm_ui_scraper_legacy_sources_cannot_create(tmp_path, cfg, source):
    j, _h = _journal(tmp_path)
    _refused(j, cfg, source, "not_a_referee_candidate")


def test_unvalidated_candidate_cannot_create(tmp_path, cfg):
    j, h = _journal(tmp_path, state="queued")
    _refused(j, cfg, _src(h), "candidate_not_referee_passed:queued")


# ── 2/3. exact upstream lineage, typed requirements ──────────────────────
def test_version_binds_research_quant_and_requirements(tmp_path, cfg):
    j, h, v, rec = _new(tmp_path, cfg)
    lin = rec["lineage"]
    assert lin["schema"] == F.LINEAGE_SCHEMA
    assert lin["research"]["origin"] == "discovery_search"
    assert lin["research"]["candidate_hash"] == h
    q = lin["quantitative"]
    assert q["gate1_test_seq"] == 1 and q["cut_ms"] == 1_780_000_000_000
    assert {k: q[k] for k in rec["evidence_ids"]} == rec["evidence_ids"]
    req = lin["requirements"]
    spec = rec["spec"]
    for k in ("universe", "timeframe", "entry_long", "entry_short", "exit",
              "data_requires", "regime_filter", "markets"):
        assert req["rules"][k] == spec[k]
    assert req["risk"]["config_sha256"] == F._jsha(cfg["risk"])
    assert req["risk"]["exit_semantics_id"] and req["compiled_spec_sha256"]
    assert req["spec_hash"] == rec["spec_hash"]
    assert rec["parent_version_id"] is None and rec["strategy_id"] == spec["id"]
    assert rec["lineage_sha256"] == F._jsha(lin)


def test_predictive_candidate_requires_exact_res_ids(tmp_path, cfg):
    j, h = _journal(tmp_path, trigger="hyp1", scope="exp1")
    _refused(j, cfg, _src(h), "research_lineage_missing")
    _refused(j, cfg, _src(h, {**RES, "hypothesis_id": "other"}),
             "research_lineage_mismatch")
    _refused(j, cfg, _src(h, {**RES, "experiment_id": "stale"}),
             "research_lineage_mismatch")
    bad = dict(RES)
    bad.pop("result_id")
    _refused(j, cfg, _src(h, bad), "research_lineage_malformed")
    _refused(j, cfg, _src(h, {**RES, "measurement_id": ""}),
             "research_lineage_malformed")
    # well-formed and consistent with the rule, but no Research Bank to
    # authenticate it against: nothing is created (see test_str01_bank_lineage)
    _refused(j, cfg, _src(h, RES), "research_bank_required")
    _refused(j, cfg, {**_src(h, RES), "bank_path": "/nonexistent/bank.db"},
             "research_bank_unauthenticated")


def test_discovery_candidate_may_not_claim_research_ids(tmp_path, cfg):
    j, h = _journal(tmp_path)
    _refused(j, cfg, _src(h, RES), "research_lineage_not_applicable")


def test_wrong_or_missing_quantitative_evidence_refuses(tmp_path, cfg):
    j, h = _journal(tmp_path, look=False)
    _refused(j, cfg, _src(h), "gate1_look_missing")
    (tmp_path / "p").mkdir()
    j, h = _journal(tmp_path / "p")
    with j._tx() as c:
        c.execute("UPDATE research_candidates SET gate1=?",
                  (json.dumps({"p": 0.0005, "alpha": 0.0025}),))
    _refused(j, cfg, _src(h), "candidate_gate1_does_not_match_look")


def test_stale_evidence_after_creation_blocks_install(tmp_path, cfg):
    j, h, v, rec = _new(tmp_path, cfg)
    with j._tx() as c:      # the look's retained detail changes after the fact
        d = json.loads(c.execute(
            "SELECT detail FROM research_tests").fetchone()[0])
        d["draws"] = 4999
        c.execute("UPDATE research_tests SET detail=?", (json.dumps(d),))
    with pytest.raises(F.HandoffRefused) as e:
        F.verify_validation(j, rec)
    assert e.value.code == "validation_evidence_changed"


# ── 4. material-change semantics ─────────────────────────────────────────
def _derive(j, cfg, rec, mutate):
    spec = StrategySpec.from_dict(copy.deepcopy(rec["spec"]))
    mutate(spec)
    return F.derive_version(j, rec["version_id"], spec, at_ms=T0 + 1, cfg=cfg)


def _set(attr, value):
    return lambda s: setattr(s, attr, value)


MATERIAL = {
    "entry_logic": _set("entry_long", "ret(6) < -2"),
    "exit_logic": lambda s: s.exit.target.update(v=4.0),
    "universe": _set("universe", {"include": ["BTC/USDT"], "exclude": []}),
    "horizon": _set("timeframe", "1h"),
    "world_requirement": _set("regime_filter", ["RANGING"]),
    "data_requirement": _set("filters", ["funding > 0"]),
    "metadata": _set("thesis", "a different but fully stated inefficiency: " + "x" * 60),
}


@pytest.mark.parametrize("name", sorted(MATERIAL))
def test_any_change_is_a_new_immutable_version(tmp_path, cfg, name):
    j, _h, v, rec = _new(tmp_path, cfg)
    before = j.query("SELECT * FROM strategy_versions")
    d = _derive(j, cfg, rec, MATERIAL[name])
    assert d["version_id"] != v["version_id"]
    assert d["parent_version_id"] == v["version_id"]
    new = F.load_version(j, d["version_id"])
    assert new["parent_version_id"] == v["version_id"]
    assert new["lineage"]["requirements"]["spec_hash"] == new["spec_hash"] \
        != rec["spec_hash"]
    assert new["lineage"]["research"] is None             # inherits nothing
    assert new["lineage"]["quantitative"] is None
    assert F.state_of(j, d["version_id"]) == F.PROPOSED
    assert F.load_version(j, v["version_id"]) == rec       # old unchanged
    assert [dict(r) for r in j.query("SELECT * FROM strategy_versions "
            "WHERE version_id=?", (v["version_id"],))] == \
        [dict(r) for r in before]
    # a derived version is not installable: it has no validation receipt
    with pytest.raises(F.HandoffRefused):
        F.record_exact_install(j, d["version_id"], at_ms=T0 + 2)


def test_identical_spec_is_not_a_new_version_and_id_is_fixed(tmp_path, cfg):
    j, _h, v, rec = _new(tmp_path, cfg)
    with pytest.raises(F.HandoffRefused) as e:
        _derive(j, cfg, rec, lambda s: None)
    assert e.value.code == "spec_unchanged"
    with pytest.raises(F.HandoffRefused) as e:
        _derive(j, cfg, rec, lambda s: (setattr(s, "id", "other"),
                                        setattr(s, "entry_long", "ret(6)<-3")))
    assert e.value.code == "strategy_id_changed"


def test_risk_requirement_change_is_a_new_version(tmp_path, cfg):
    j, h, v, rec = _new(tmp_path, cfg)
    cfg2 = copy.deepcopy(cfg)
    cfg2["risk"] = {**cfg["risk"], "max_open": cfg["risk"].get("max_open", 5) + 1}
    v2 = F.create_version(j, cfg2, _src(h), at_ms=T0 + 5)
    assert v2["version_id"] != v["version_id"]
    assert v2["spec_hash"] == v["spec_hash"]                # same strategy
    assert F.load_version(j, v["version_id"]) == rec
    again = F.create_version(j, cfg, _src(h), at_ms=T0 + 9)
    assert again["version_id"] == v["version_id"] and \
        again["status"] != "inserted"


def test_quant_or_research_source_revision_changes_identity(tmp_path, cfg):
    j, _h, v, rec = _new(tmp_path, cfg)
    base = dict(rec["lineage"])
    for mut in (lambda l: l["quantitative"].update(gate1_look_sha256="x" * 64),
                lambda l: l["research"].update(candidate_hash="other")):
        lin = copy.deepcopy(base)
        mut(lin)
        r = F._version_record(rec["strategy_id"], rec["spec"], rec["spec_hash"],
                              None, rec["source"], rec["evidence_ids"], lin)
        assert r["version_id"] != rec["version_id"]


def test_direct_duplicate_and_restart_replay(tmp_path, cfg):
    j, h, v, rec = _new(tmp_path, cfg)
    dup = F.create_version(j, cfg, _src(h), at_ms=T0 + 99)
    assert dup["version_id"] == v["version_id"] and dup["status"] != "inserted"
    j2 = Journal(tmp_path / "j.db")
    assert F.load_version(j2, v["version_id"]) == rec
    again = F.create_version(j2, cfg, _src(h), at_ms=T0 + 100)
    assert again["version_id"] == v["version_id"]
    assert len(j2.query("SELECT * FROM strategy_versions")) == 1


def test_stored_versions_cannot_be_rewritten_or_deleted(tmp_path, cfg):
    j, _h, v, _rec = _new(tmp_path, cfg)
    for sql in ("UPDATE strategy_versions SET spec_hash='x'",
                "DELETE FROM strategy_versions"):
        with pytest.raises(Exception, match="immutable"):
            with j._tx() as c:
                c.execute(sql)


# ── 5. authority boundary ────────────────────────────────────────────────
def test_creation_grants_no_install_activation_capital_or_orders(tmp_path, cfg):
    j, _h, v, _rec = _new(tmp_path, cfg)
    vid = v["version_id"]
    assert F.state_of(j, vid) == F.VALIDATED
    for t in ("strategies", "strategy_version_installs",
              "strategy_approval_requests", "strategy_approval_decisions",
              "strategy_governor_events", "orders"):
        try:
            assert j.query(f"SELECT * FROM {t}") == []
        except Exception as e:      # noqa: BLE001 - table may not exist
            assert "no such table" in str(e)
    assert not F.eligible_for_first_live(j, vid, cfg=cfg,
                                         available_inputs=INPUTS).eligible
    assert F.live_entry_block(j, vid) is not None or not F.versioned(j, vid)


def _plant(j, rec):
    with j._tx() as c:
        F._insert(c, "strategy_versions", "version_id",
                  F._version_row(rec, T0))


def test_fabricated_version_without_receipt_cannot_install(tmp_path, cfg):
    j, _h, v, rec = _new(tmp_path, cfg)
    spec = copy.deepcopy(rec["spec"])
    spec["entry_long"] = "ret(6) < -5"
    spec_d, sh = F._frozen_spec(StrategySpec.from_dict(spec))
    fake = F._version_record(spec_d["id"], spec_d, sh, None,
                             {"kind": "research_candidate", "id": _h},
                             rec["evidence_ids"],
                             F._lineage(spec_d, F._jsha(cfg["risk"]),
                                        rec["lineage"]["research"],
                                        rec["lineage"]["quantitative"], None))
    _plant(j, fake)
    assert F.load_version(j, fake["version_id"])["spec_hash"] == sh
    with pytest.raises(F.HandoffRefused) as e:
        F.record_exact_install(j, fake["version_id"], at_ms=T0 + 3)
    assert e.value.code == "validation_receipt_missing"


def test_tampered_lineage_is_refused_on_load(tmp_path, cfg):
    j, _h, v, rec = _new(tmp_path, cfg)
    # (a) lineage edited, hashes left alone
    bad = copy.deepcopy(rec)
    bad["lineage"]["research"]["candidate_hash"] = "other"
    _plant_raw(j, bad, "a")
    # (b) lineage claims evidence the version does not carry (rehashed)
    lin = copy.deepcopy(rec["lineage"])
    lin["quantitative"]["gate1_look_sha256"] = "0" * 64
    forged = F._version_record(rec["strategy_id"], rec["spec"], rec["spec_hash"],
                               None, {"kind": "research_candidate", "id": "z"},
                               rec["evidence_ids"], lin)
    _plant(j, forged)
    # (c) requirements no longer describe the spec (rehashed)
    lin = copy.deepcopy(rec["lineage"])
    lin["requirements"]["rules"]["entry_long"] = "ret(6) < -9"
    forged2 = F._version_record(rec["strategy_id"], rec["spec"],
                                rec["spec_hash"], None,
                                {"kind": "research_candidate", "id": "y"},
                                rec["evidence_ids"], lin)
    _plant(j, forged2)
    # (d) lineage stripped to pose as a legacy record
    stripped = {k: v_ for k, v_ in rec.items()
                if k not in ("lineage", "lineage_sha256")}
    _plant_raw(j, stripped, "d")
    for vid, code in ((bad["version_id"] + "a", "version_lineage_mismatch"),
                      (forged["version_id"],
                       "version_lineage_evidence_mismatch"),
                      (forged2["version_id"],
                       "version_lineage_requirements_mismatch"),
                      (stripped["version_id"] + "d", "version_id_mismatch")):
        with pytest.raises(F.HandoffRefused) as e:
            F.load_version(j, vid)
        assert e.value.code == code


def _plant_raw(j, rec, tag):
    rec = dict(rec, version_id=rec["version_id"] + tag)
    with j._tx() as c:
        F._insert(c, "strategy_versions", "version_id", F._version_row(rec, T0))


# ── 6. historical compatibility ──────────────────────────────────────────
def test_legacy_version_replays_unchanged_but_cannot_be_promoted(tmp_path, cfg):
    j, h, v, rec = _new(tmp_path, cfg)
    legacy = F._version_record(rec["strategy_id"], rec["spec"],
                               rec["spec_hash"], None,
                               {"kind": "research_candidate", "id": "old"},
                               rec["evidence_ids"])
    assert "lineage" not in legacy and "lineage_sha256" not in legacy
    assert legacy["version_id"] == F._jsha(
        {k: legacy[k] for k in ("schema", "strategy_id", "spec_hash",
                                "parent_version_id", "source")})
    _plant(j, legacy)
    got = F.load_version(j, legacy["version_id"])
    assert got == legacy and "lineage" not in got
    with pytest.raises(F.HandoffRefused) as e:
        F.record_exact_install(j, legacy["version_id"], at_ms=T0 + 3)
    assert e.value.code == "legacy_version_lacks_lineage_contract"
    assert F.state_of(j, legacy["version_id"]) is None
