"""STR-03: first real-money approval binds the exact version and evidence.

Offline, temporary journals only. Nothing here activates trading, calls a
venue/provider, or starts the Kernel/Dashboard. The normal path under test:

    research candidate -> validated StrategyVersion (STR-01 lineage)
    -> exact paper install -> probation receipt -> approval request
    -> owner decision -> eligibility / Strategy Governor
"""
import copy
import json
import sqlite3

import pytest

from trader.core.journal import Journal
from trader.owner import approvals as A
from trader.strategy import factory_handoff as F
from trader.strategy.spec import StrategySpec
from tests.authority_factory_fixtures import (  # noqa: F401
    DAY, INPUTS, PASSING, T0, _approved, _install, _journal, _to_approval,
    _trades, cfg)
from tests.authority_legacy_fixtures import grant
from tests.test_str01_bank_lineage import AT, CFG, lineage  # noqa: F401
from tests.test_predictive_research_bridge import population  # noqa: F401
from tests.test_investigation_state_feedback import paths  # noqa: F401

NOCAP = "capacity_receipt_not_asserted"
APPROVAL_TABLES = ("strategy_approval_requests", "strategy_approval_decisions",
                   "strategy_version_events", "strategy_probation_receipts",
                   "strategy_validation_receipts", "strategy_versions")


def _val(j, sql, *a):
    return list(j.query(sql, a)[0].values())[0]


def _elig(j, cfg, vid, **kw):
    kw.setdefault("available_inputs", INPUTS)
    return F.eligible_for_first_live(j, vid, cfg=cfg, **kw)


def _ok(e):
    return not e.eligible and e.reasons == (NOCAP,)


def _snapshot(j):
    return {t: [tuple(r) for r in j.query(f"SELECT * FROM {t} ORDER BY 1")]
            for t in APPROVAL_TABLES}


def _world(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v, p, d = _approved(j, cfg)
    return j, h, v, p, d


# ── 1/2. the normal path and the exact bindings ──────────────────────────
def test_request_binds_the_exact_chain_and_the_decision_binds_the_request(
        tmp_path, cfg):
    j, h, v, p, d = _world(tmp_path, cfg)
    rec = F.load_version(j, v["version_id"])
    req = F.approval_request(j, v["version_id"])
    install = F._install_record(j, v["version_id"])
    assert req["request_id"] == p["request_id"]
    assert (req["strategy_id"], req["version_id"], req["spec_hash"]) == (
        rec["strategy_id"], v["version_id"], rec["spec_hash"])
    assert req["lineage_sha256"] == rec["lineage_sha256"]
    assert req["research_sha256"] == F._jsha(rec["lineage"]["research"])
    assert req["quantitative_sha256"] == F._jsha(rec["lineage"]["quantitative"])
    assert req["install_id"] == install["install_id"]
    assert req["validation_receipt_id"] == v["validation_receipt_id"]
    assert req["probation_receipt_id"] == p["probation_receipt_id"]
    assert req["request_id"] == F._jsha({k: req[k] for k in F._REQUEST_KEYS})
    dec = json.loads(_val(j, "SELECT canonical_json FROM strategy_approval_decisions"))
    assert dec["request_id"] == req["request_id"]
    assert dec["decision_id"] == d["decision_id"]
    assert dec["config_sha256"] == F._jsha(cfg)
    ev = [e for e in F.events(j, v["version_id"])
          if e["to_state"] == F.APPROVED_FIRST_LIVE]
    assert len(ev) == 1 and ev[0]["ref_id"] == d["decision_id"]
    assert _ok(_elig(j, cfg, v["version_id"]))


def test_scope_is_explicit_and_exact_version_only(tmp_path, cfg):
    j, h, v, p, d = _world(tmp_path, cfg)
    scope = F.approval_request(j, v["version_id"])["scope"]
    assert scope == {"kind": "FIRST_LIVE_EXACT_VERSION",
                     "version_id": v["version_id"], "spec_hash": v["spec_hash"],
                     "applies_to_descendants": False,
                     "applies_to_latest": False, "applies_by_name": False,
                     "activates": False}
    # the owner's view shows the same inspectable binding
    item = A.items(j, cfg, now_ms=T0 + 32 * DAY)["items"][0]
    assert item["bindings"]["scope"] == scope
    assert item["bindings"]["lineage_sha256"] == F.load_version(
        j, v["version_id"])["lineage_sha256"]
    assert d["decision_id"]


@pytest.mark.parametrize("field,value,code", [
    ("lineage_sha256", "1" * 64, "approval_request_stale_lineage"),
    ("research_sha256", "2" * 64, "approval_request_binding_mismatch"),
    ("quantitative_sha256", "3" * 64, "approval_request_binding_mismatch"),
    ("install_id", "4" * 64, "approval_request_binding_mismatch"),
    ("probation_receipt_id", "5" * 64, "approval_request_identity_mismatch"),
    ("validation_receipt_id", "6" * 64, "approval_request_identity_mismatch"),
    ("strategy_id", "other", "approval_request_wrong_version"),
    ("spec_hash", "7" * 64, "approval_request_wrong_version"),
    ("version_id", "8" * 64, "approval_request_wrong_version"),
    ("request_id", "9" * 64, "approval_request_identity_mismatch"),
    ("scope", {"kind": "FIRST_LIVE_EXACT_VERSION", "applies_to_latest": True},
     "approval_request_binding_mismatch"),
])
def test_any_changed_binding_in_a_request_is_refused(tmp_path, cfg, field,
                                                     value, code):
    j, h, v, p, d = _world(tmp_path, cfg)
    rec = F.load_version(j, v["version_id"])
    req = copy.deepcopy(F.approval_request(j, v["version_id"]))
    req[field] = value
    with pytest.raises(F.HandoffRefused) as e:
        F.verify_request(j, rec, req)
    assert e.value.code == code


def test_request_without_lineage_binding_is_unusable(tmp_path, cfg):
    j, h, v, p, d = _world(tmp_path, cfg)
    rec = F.load_version(j, v["version_id"])
    for key in ("lineage_sha256", "install_id", "scope", "research_sha256"):
        req = {k: x for k, x in F.approval_request(j, v["version_id"]).items()
               if k != key}
        with pytest.raises(F.HandoffRefused) as e:
            F.verify_request(j, rec, req)
        assert e.value.code == "approval_request_unbound_lineage"


# ── 3. staleness: any material change makes the approval unusable ───────
def _extra_trade(j, cfg, v):
    _trades(j, v["strategy_id"], [10.0], T0 + 40 * 3_600_000, prefix="late",
            spec_hash=v["spec_hash"])


def _research_withdrawn(j, h):
    from trader.research.ledger import Ledger
    Ledger(j).set_candidate(h, "4h", "fixed", "refused", reason="later")


def _quant_changed(j, h):
    from trader.research.ledger import Ledger
    row = j.query("SELECT * FROM research_candidates WHERE hash=?", (h,))[0]
    gate1 = json.loads(row["gate1"]) if isinstance(row["gate1"], str) \
        else row["gate1"]
    gate1 = {**gate1, "p": 0.2}
    Ledger(j).set_candidate(h, "4h", "fixed", "referee_passed", rank=40.0,
                            gate1=gate1,
                            gate3={"passed": True, "reason": "fixture book"})


def _installed_logic_changed(j, v):
    spec = StrategySpec.from_dict(v["spec"])
    spec.entry_long = "close > 1e9"
    j.upsert_spec(spec, state="paper", origin="research")


def _case_research(j, h, v, cfg):
    _research_withdrawn(j, h)
    return cfg, INPUTS, "candidate_not_referee_passed:refused"


def _case_quant(j, h, v, cfg):
    _quant_changed(j, h)
    return cfg, INPUTS, "candidate_gate1_does_not_match_look"


def _case_probation(j, h, v, cfg):
    _extra_trade(j, cfg, v)
    return cfg, INPUTS, "probation_evidence_changed"


def _case_exit_entry_logic(j, h, v, cfg):
    _installed_logic_changed(j, F.load_version(j, v["version_id"]))
    return cfg, INPUTS, "installed_version_differs"


def _case_risk(j, h, v, cfg):
    changed = {**cfg, "risk": {**cfg["risk"], "max_open_positions": 99}}
    return changed, INPUTS, "approved_configuration_changed"


def _case_limits(j, h, v, cfg):
    changed = {**cfg, "strategies": {**cfg["strategies"],
                                     "paper_probation_trades": 3}}
    return changed, INPUTS, "approved_configuration_changed"


def _case_data(j, h, v, cfg):
    return cfg, {"ohlcv"} - {"ohlcv"}, "inputs_unavailable:"


@pytest.mark.parametrize("case", [
    _case_research, _case_quant, _case_probation, _case_exit_entry_logic,
    _case_risk, _case_limits, _case_data])
def test_each_material_change_after_approval_makes_it_unusable(
        tmp_path, cfg, case):
    j, h, v, p, d = _world(tmp_path, cfg)
    assert _ok(_elig(j, cfg, v["version_id"]))
    before = _snapshot(j)
    use_cfg, inputs, reason = case(j, h, v, cfg)
    e = _elig(j, use_cfg, v["version_id"], available_inputs=inputs)
    assert not e.eligible
    assert any(r.startswith(reason) for r in e.reasons), e.reasons
    # the old approval is not mutated: stale, inspectable, unusable
    assert _snapshot(j) == before


def test_stale_approval_stays_inspectable_in_the_owner_view(tmp_path, cfg):
    j, h, v, p, d = _world(tmp_path, cfg)
    _research_withdrawn(j, h)
    item = A.items(j, cfg, now_ms=T0 + 32 * DAY)["items"][0]
    assert item["item_id"] == p["request_id"]
    assert item["validity"] == "STALE" and item["invalid_reasons"]
    assert item["receipt"]["decision_id"] == d["decision_id"]
    assert item["status"] == "STALE"


def test_changed_version_is_a_new_identity_without_the_old_approval(
        tmp_path, cfg):
    j, h, v, p, d = _world(tmp_path, cfg)
    before = _snapshot(j)
    spec = StrategySpec.from_dict(F.load_version(j, v["version_id"])["spec"])
    spec.exit.stop = {"kind": "atr", "mult": 4.0}
    b = F.derive_version(j, v["version_id"], spec, at_ms=T0 + 32 * DAY)
    assert b["version_id"] != v["version_id"]
    assert F.approval_request(j, b["version_id"]) is None
    after = _snapshot(j)
    for t in ("strategy_approval_requests", "strategy_approval_decisions",
              "strategy_probation_receipts", "strategy_validation_receipts"):
        assert after[t] == before[t]
    assert _ok(_elig(j, cfg, v["version_id"]))
    assert not _elig(j, cfg, b["version_id"]).eligible


# ── 4. owner approval is narrow ──────────────────────────────────────────
def test_approval_is_not_by_name_latest_descendant_or_label(tmp_path, cfg):
    j, h, v, p, d = _world(tmp_path, cfg)
    a = F.load_version(j, v["version_id"])
    spec = StrategySpec.from_dict(a["spec"])
    spec.exit.stop = {"kind": "atr", "mult": 4.0}
    b = F.derive_version(j, v["version_id"], spec, at_ms=T0 + 32 * DAY)
    assert F.load_version(j, b["version_id"])["strategy_id"] == a["strategy_id"]
    eb = _elig(j, cfg, b["version_id"])
    assert not eb.eligible and "approval_request_missing" in eb.reasons
    # the same strategy NAME, now installed at B, is not authorised by A
    j.upsert_spec(StrategySpec.from_dict(F.load_version(
        j, b["version_id"])["spec"]), state="paper", origin="research")
    assert F.live_entry_block(j, a["strategy_id"]) not in (None,)
    assert F.live_entry_block(j, a["strategy_id"]).startswith(
        "version_not_live_authorized")
    # A's own approval does not cover B, and A's is no longer installed
    assert "installed_version_differs" in _elig(j, cfg, v["version_id"]).reasons


def test_active_label_without_exact_approval_has_no_first_live_authority(
        tmp_path, cfg):
    j, h = _journal(tmp_path)
    vid = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                           at_ms=T0 - DAY)["version_id"]
    rec = _install(j, vid)
    j._local.governor_write = True      # privileged corruption fixture
    try:
        with j._tx() as c:
            c.execute("UPDATE strategies SET state='active' WHERE id=?",
                      (rec["strategy_id"],))
    finally:
        j._local.governor_write = False
    assert _val(j, "SELECT state FROM strategies") == "active"
    e = _elig(j, cfg, vid)
    assert not e.eligible and "approval_request_missing" in e.reasons
    assert F.live_entry_block(j, rec["strategy_id"]) == \
        "version_not_live_authorized:SHADOW"


# ── 5. legacy / grandfather / copied / fabricated ───────────────────────
def test_grandfather_receipt_cannot_replace_missing_version_approval(
        tmp_path, cfg):
    j, h = _journal(tmp_path)
    vid = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                           at_ms=T0 - DAY)["version_id"]
    rec = _install(j, vid)
    grant(j, rec["strategy_id"])
    assert F.live_entry_block(j, rec["strategy_id"]) == \
        "version_not_live_authorized:SHADOW"
    e = _elig(j, cfg, vid)
    assert not e.eligible and "approval_request_missing" in e.reasons


def test_legacy_version_without_lineage_has_no_first_live_authority(
        tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                         at_ms=T0 - DAY)
    rec = F.load_version(j, v["version_id"])
    legacy = F._version_record(rec["strategy_id"], rec["spec"],
                               rec["spec_hash"], None, rec["source"],
                               rec["evidence_ids"])       # no lineage
    assert "lineage" not in legacy
    with j._tx() as c:
        F._begin(c)
        assert F._insert_version(c, legacy, T0) == "inserted"
    e = _elig(j, cfg, legacy["version_id"])
    assert not e.eligible and "version_lineage_absent" in e.reasons
    with pytest.raises(F.HandoffRefused) as r:
        F._request_ident(legacy, "a" * 64, "b" * 64, "c" * 64)
    assert r.value.code == "legacy_version_lacks_lineage_contract"
    with pytest.raises(F.HandoffRefused) as r:
        F.record_exact_install(j, legacy["version_id"], at_ms=T0)
    assert r.value.code == "legacy_version_lacks_lineage_contract"


def test_unversioned_legacy_strategy_is_refused_first_live(tmp_path, cfg):
    j = Journal(tmp_path / "j.db")
    F.ensure(j)
    assert not _elig(j, cfg, "a" * 64).eligible
    assert F.live_entry_block(j, "never-versioned") == \
        "VERSIONED_AUTHORITY_REQUIRED"


def test_raw_sql_cannot_fabricate_a_request_or_decision(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v, p = _to_approval(j, cfg)
    req = F.approval_request(j, v["version_id"])
    with pytest.raises(sqlite3.IntegrityError,
                       match="STRATEGY_APPROVAL_AUTHORITY_REQUIRED"):
        with j._tx() as c:
            c.execute("INSERT INTO strategy_approval_decisions VALUES "
                      "(?,?,?,?,?,?,?)", ("d" * 64, req["request_id"],
                                          "APPROVED", "operator", "x", "{}", 0))
    with pytest.raises(sqlite3.IntegrityError,
                       match="STRATEGY_APPROVAL_AUTHORITY_REQUIRED"):
        with j._tx() as c:
            c.execute("INSERT INTO strategy_approval_requests VALUES "
                      "(?,?,?,?,?)", ("e" * 64, v["version_id"], "x", "{}", 0))
    assert not j.query("SELECT * FROM strategy_approval_decisions")
    assert not getattr(j._local, "approval_write", False)
    assert not _elig(j, cfg, v["version_id"]).eligible


def test_forged_event_or_decision_without_the_real_decision_id_is_refused(
        tmp_path, cfg):
    j, h = _journal(tmp_path)
    v, p = _to_approval(j, cfg)
    # privileged forged APPROVED_FIRST_LIVE event naming no real decision
    with j._tx() as c:
        c.execute("INSERT INTO strategy_version_events(version_id,from_state,"
                  "to_state,reason_code,ref_id,actor,at_ms) VALUES "
                  "(?,?,?,?,?,?,?)", (v["version_id"], F.APPROVAL_REQUIRED,
                                      F.APPROVED_FIRST_LIVE, "forged",
                                      "f" * 64, "operator", T0))
    e = _elig(j, cfg, v["version_id"])
    assert not e.eligible and "owner_approval_missing" in e.reasons


def test_copied_decision_does_not_approve_a_different_probation_run(
        tmp_path, cfg):
    a_dir, b_dir = tmp_path / "a", tmp_path / "b"
    a_dir.mkdir(), b_dir.mkdir()
    ja, ha, va, pa, da = _world(a_dir, cfg)
    jb, hb = _journal(b_dir)
    vb = F.create_version(jb, cfg, {"kind": "research_candidate", "hash": hb},
                          at_ms=T0 - DAY)
    rec = _install(jb, vb["version_id"])
    _trades(jb, rec["strategy_id"], [10.0] * 10 + [-5.0] * 5, T0 + 3_600_000,
            spec_hash=vb["spec_hash"])                    # another run
    pb = F.evaluate_probation(jb, cfg, vb["version_id"], at_ms=T0 + 30 * DAY)
    assert pb["request_id"] != pa["request_id"]
    copied = ja.query("SELECT * FROM strategy_approval_decisions")[0]
    forged = json.loads(copied["canonical_json"])
    forged["request_id"] = pb["request_id"]          # keep A's id, other run
    text = F.canonical(forged)
    jb._local.approval_write = True
    with jb._tx() as c:
        c.execute("INSERT INTO strategy_approval_decisions VALUES "
                  "(?,?,?,?,?,?,?)", (forged["decision_id"], pb["request_id"],
                                      "APPROVED", "operator",
                                      F._sha(text), text, T0))
    jb._local.approval_write = False
    e = _elig(jb, cfg, vb["version_id"])
    assert not e.eligible
    assert "owner_approval_wrong_version" in e.reasons


def test_stale_owner_request_cannot_be_decided(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v, p = _to_approval(j, cfg)
    _research_withdrawn(j, h)
    with pytest.raises(F.HandoffRefused):
        F.record_owner_decision(j, cfg, p["request_id"], "APPROVED",
                                actor="operator", decided_at_ms=T0 + 31 * DAY)
    assert not j.query("SELECT * FROM strategy_approval_decisions")
    assert F.state_of(j, v["version_id"]) == F.APPROVAL_REQUIRED


def test_probation_change_before_decision_blocks_the_decision(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v, p = _to_approval(j, cfg)
    _extra_trade(j, cfg, v)
    with pytest.raises(F.HandoffRefused) as e:
        F.record_owner_decision(j, cfg, p["request_id"], "APPROVED",
                                actor="operator", decided_at_ms=T0 + 31 * DAY)
    assert e.value.code == "probation_evidence_changed"


# ── 6. Governor handoff ──────────────────────────────────────────────────
def _gov_world(tmp_path, cfg, monkeypatch):
    from tests.test_universal_strategy_authority import approved_world
    return approved_world(tmp_path, cfg, monkeypatch)


def test_governor_loads_the_exact_approved_artifact(tmp_path, cfg,
                                                    monkeypatch):
    from tests.test_universal_strategy_authority import (
        activate, established_capacity)
    j, v, risk, proof = _gov_world(tmp_path, cfg, monkeypatch)
    rid = established_capacity(j, cfg, v, monkeypatch)
    proof = risk.release_check(10000)
    event = activate(j, cfg, v["version_id"], available_inputs=INPUTS,
                     capacity_receipt_id=rid, risk_manager=risk,
                     risk_release=proof)["event"]
    req = F.approval_request(j, v["version_id"])
    dec = json.loads(_val(j, "SELECT canonical_json FROM strategy_approval_decisions"))
    assert (event["version_id"], event["spec_hash"]) == (v["version_id"],
                                                         v["spec_hash"])
    assert event["owner_decision_id"] == dec["decision_id"]
    assert dec["request_id"] == req["request_id"]
    assert req["install_id"] == F.verify_install(j, v)["install_id"]
    # nothing was ordered or started by activation
    assert F.live_entry_block(j, v["strategy_id"]) == \
        "version_first_live_execution_not_enabled"


@pytest.mark.parametrize("gap", ["unapproved_sibling", "probation_changed",
                                 "installed_differs", "research_withdrawn",
                                 "config_changed"])
def test_governor_refuses_any_mismatch_with_the_approved_artifact(
        tmp_path, cfg, monkeypatch, gap):
    from tests.test_universal_strategy_authority import (
        activate, established_capacity)
    j, v, risk, proof = _gov_world(tmp_path, cfg, monkeypatch)
    rid = established_capacity(j, cfg, v, monkeypatch)
    proof = risk.release_check(10000)
    target, use_cfg = v["version_id"], cfg
    if gap == "unapproved_sibling":
        spec = StrategySpec.from_dict(v["spec"])
        spec.exit.stop = {"kind": "atr", "mult": 4.0}
        target = F.derive_version(j, v["version_id"], spec,
                                  at_ms=T0 + 32 * DAY)["version_id"]
    if gap == "probation_changed":      # a counted trade is rewritten
        with j._tx() as c:
            c.execute("UPDATE trades SET realized_pnl=-50 WHERE id='t0'")
    if gap == "installed_differs":
        _installed_logic_changed(j, v)
    if gap == "research_withdrawn":
        _research_withdrawn(j, j.query(
            "SELECT hash FROM research_candidates")[0]["hash"])
    if gap == "config_changed":
        use_cfg = {**cfg, "strategies": {**cfg["strategies"],
                                         "paper_probation_trades": 3}}
    with pytest.raises(F.HandoffRefused):
        activate(j, use_cfg, target, available_inputs=INPUTS,
                 capacity_receipt_id=rid, risk_manager=risk,
                 risk_release=proof)
    assert F.state_of(j, v["version_id"]) == F.APPROVED_FIRST_LIVE
    assert not j.query("SELECT * FROM strategy_governor_events")


# ── 7. approval alone is not first-live execution ───────────────────────
def test_approval_alone_starts_nothing_and_allocates_nothing(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v, p = _to_approval(j, cfg)
    n_trades = _val(j, "SELECT COUNT(*) FROM trades")
    control = j.kv_get("control_state")
    before_states = [tuple(r) for r in j.query(
        "SELECT id,state FROM strategies")]
    d = F.record_owner_decision(j, cfg, p["request_id"], "APPROVED",
                                actor="operator", decided_at_ms=T0 + 31 * DAY)
    assert d["status"] == "inserted"
    assert F.state_of(j, v["version_id"]) == F.APPROVED_FIRST_LIVE
    assert j.kv_get("control_state") == control
    assert _val(j, "SELECT COUNT(*) FROM trades") == n_trades
    assert [tuple(r) for r in j.query(
        "SELECT id,state FROM strategies")] == before_states
    assert not j.query("SELECT * FROM strategy_governor_events")
    # eligibility needs a capacity receipt; the live fence still refuses
    assert _ok(_elig(j, cfg, v["version_id"]))
    assert F.live_entry_block(j, v["strategy_id"]) == \
        "version_capacity_not_established"


# ── research bank revision (predictive-experiment origin) ───────────────
def test_superseded_research_result_stales_a_predictive_version(
        lineage, monkeypatch):
    import copy as _copy
    from trader.cognition import predictive_bank
    j, bank, final, src = lineage
    v = F.create_version(j, CFG, src, at_ms=AT)
    vid = v["version_id"]
    none = _elig(j, CFG, vid)
    assert "research_bank_not_asserted" in none.reasons
    fresh = _elig(j, CFG, vid, research_bank_path=str(bank))
    assert not any(r.startswith("research_") for r in fresh.reasons), \
        fresh.reasons
    real = predictive_bank.predictive_extensions

    def newer(path, bank_id):
        att = _copy.deepcopy(real(path, bank_id))
        ex = _copy.deepcopy(next(e for e in att["experiments"]
                                 if e["experiment_id"] == final["experiment_id"]))
        ex["experiment_id"] = "newer-revision"
        att["experiments"].append(ex)
        return att
    monkeypatch.setattr(predictive_bank, "predictive_extensions", newer)
    stale = _elig(j, CFG, vid, research_bank_path=str(bank))
    assert "research_revised:research_result_stale" in stale.reasons
    assert not stale.eligible


# ── 8. replay / immutability ─────────────────────────────────────────────
def test_identical_inputs_reproduce_identical_approval_identity(tmp_path,
                                                                cfg):
    import shutil
    j, h, v, p, d = _world(tmp_path, cfg)
    req = F.approval_request(j, v["version_id"])
    # identity is a pure function of the stored chain
    rec = F.load_version(j, v["version_id"])
    again = F._request_ident(rec, req["validation_receipt_id"],
                             req["install_id"], req["probation_receipt_id"])
    assert F._jsha(again) == req["request_id"] == p["request_id"]
    # re-running each step with identical inputs is a duplicate, same ids
    v2 = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                          at_ms=T0 - DAY)
    assert (v2["status"], v2["version_id"]) == ("duplicate", v["version_id"])
    with pytest.raises(F.HandoffRefused) as e:       # no second request
        F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    assert e.value.code == "not_in_probation:APPROVED_FIRST_LIVE"
    # a byte copy of the journal (another host/restore) replays identically
    j._conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
    other = tmp_path / "copy"
    other.mkdir()
    shutil.copy(tmp_path / "j.db", other / "j.db")
    jc = Journal(other / "j.db")
    assert _snapshot(jc) == _snapshot(j)
    assert F.approval_request(jc, v["version_id"]) == req
    assert _ok(_elig(jc, cfg, v["version_id"]))


def test_restart_and_replay_preserve_the_approval(tmp_path, cfg):
    j, h, v, p, d = _world(tmp_path, cfg)
    before = _snapshot(j)
    again = Journal(tmp_path / "j.db")          # a restart
    assert _snapshot(again) == before
    assert _ok(_elig(again, cfg, v["version_id"]))
    # an identical retry is a duplicate, a different decision is refused
    r = F.record_owner_decision(again, cfg, p["request_id"], "APPROVED",
                                actor="operator", decided_at_ms=T0 + 31 * DAY)
    assert r["status"] == "duplicate" and r["decision_id"] == d["decision_id"]
    with pytest.raises(F.HandoffRefused) as e:
        F.record_owner_decision(again, cfg, p["request_id"], "REJECTED",
                                actor="operator", decided_at_ms=T0 + 31 * DAY)
    assert e.value.code == "decision_already_recorded"
    assert _snapshot(again) == before
