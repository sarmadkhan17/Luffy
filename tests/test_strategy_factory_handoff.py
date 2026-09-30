"""Stage-5 handoff: referee-passed candidate -> first-live eligibility.

Every test runs on a temporary journal. The candidate is built exactly as
research phase 3 stores one (ledger combo row, one registered gate1 look in
research_tests, candidate gate JSON); nothing reads the production database.
"""
import hashlib
import json
import pathlib
import re
import sqlite3

import pytest

from trader.core.config import load_config
from trader.core.journal import Journal
from trader.strategy import factory_handoff as F
from trader.strategy.spec import StrategySpec
from tests.test_investigation_state_feedback import paths  # noqa: F401

T0 = 1_790_000_000_000             # probation start (ms)
DAY = 86_400_000
INPUTS = {"ohlcv", "funding", "oi", "ls_ratio", "ls_account_ratio", "taker",
          "basis", "xs", "btc", "ref"}


def _iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


@pytest.fixture
def cfg():
    return load_config()


def _candidate(j, state="referee_passed", p=0.001, alpha=0.0025,
               look=True, gate3=True, g1_p=None):
    from trader.research import vocab
    from trader.research.combo import Combination
    from trader.research.ledger import Ledger
    led = Ledger(j)
    led.ensure()
    led.record_gauges("4h", {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5,
                                 "p90": 1.0, "n": 9000, "finite_frac": 0.99,
                                 "usable": True}
                             for e in vocab.expressions("4h")})
    part = vocab.parts_for("4h", led.gauges("4h"))[0]
    c = Combination((part,), "4h", "trail")
    led.record_result({"hash": c.hash, "tf": "4h", "geo": "trail", "k": 1,
                       "parts": list(c.keys), "trades": 300,
                       "portfolio": {"total_pct": 40.0, "max_dd_pct": 20.0},
                       "testable": True, "verdict": "scored"},
                      "survivor", "r", {})
    if look:
        led.record_test(c.hash, "4h", "trail", "gate1", p, alpha, False,
                        {"t": 1})
    led.set_candidate(
        c.hash, "4h", "trail", state, rank=40.0,
        gate1={"p": p if g1_p is None else g1_p, "alpha": alpha, "t": 1,
               "reason": "fixture"},
        gate3={"passed": gate3, "reason": "fixture book"})
    return c.hash


def _journal(tmp_path, **kw):
    j = Journal(tmp_path / "j.db")
    return j, _candidate(j, **kw)


def _install(j, version_id):
    """What kernel._install_spec does on the existing paper path."""
    rec = F.load_version(j, version_id)
    j.upsert_spec(StrategySpec.from_dict(rec["spec"]), state="paper",
                  origin="research")
    return rec


def _trades(j, strategy_id, pnls, start_ms, prefix="t"):
    with j._tx() as c:
        for i, pnl in enumerate(pnls):
            o = start_ms + (i + 1) * 3_600_000
            c.execute(
                "INSERT INTO trades (id, symbol, side, amount, entry_price, "
                "strategy_id, exec_mode, opened_at, closed_at, realized_pnl, "
                "status) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"{prefix}{i}", "BTC/USDT", "buy", 1.0, 100.0, strategy_id,
                 "paper", _iso(o), _iso(o + 1_800_000), pnl, "closed"))


PASSING = [10.0] * 9 + [-5.0] * 6          # 15 trades, WR 0.6, PF 3.0


def _to_approval(j, cfg, h=None):
    h = h or j.query("SELECT hash FROM research_candidates")[0]["hash"]
    v = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                         at_ms=T0 - DAY)
    F.start_probation(j, v["version_id"], at_ms=T0)
    rec = _install(j, v["version_id"])
    _trades(j, rec["strategy_id"], PASSING, T0)
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    assert p["status"] == F.P_SATISFIED
    return v, p


def _approved(j, cfg):
    v, p = _to_approval(j, cfg)
    d = F.record_owner_decision(j, cfg, p["request_id"], "APPROVED",
                                actor="operator", decided_at_ms=T0 + 31 * DAY)
    return v, p, d


def _row(j, sql, *a):
    rows = j.query(sql, a)
    assert len(rows) == 1
    return rows[0]


# ── 7. the end-to-end trace ──────────────────────────────────────────────
def test_end_to_end_trace_uses_exact_stored_ids(tmp_path, cfg):
    j, h = _journal(tmp_path)
    # research candidate -> referee pass (the stored gate evidence)
    cand = _row(j, "SELECT * FROM research_candidates WHERE hash=?", h)
    look = _row(j, "SELECT * FROM research_tests WHERE hash=?", h)
    assert cand["state"] == "referee_passed" and look["rejected"] == 1

    # -> immutable version
    v = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                         at_ms=T0 - DAY)
    assert v["status"] == "inserted"
    vrow = _row(j, "SELECT * FROM strategy_versions WHERE version_id=?",
                v["version_id"])
    vrec = json.loads(vrow["canonical_json"])
    assert vrow["source_id"] == h and vrec["source"] == {
        "kind": "research_candidate", "id": h}
    assert vrec["spec_hash"] == v["spec_hash"] == hashlib.sha256(
        F.canonical(vrec["spec"]).encode()).hexdigest()
    assert vrec["spec"]["provenance"]["research_hash"] == h
    assert vrec["evidence_ids"]["gate1_test_seq"] == look["seq"]
    assert vrec["capacity"] == {"status": "UNAVAILABLE",
                                "reason": "no_truthful_capacity_estimator"}

    # -> validation receipt
    rrow = _row(j, "SELECT * FROM strategy_validation_receipts "
                   "WHERE version_id=?", v["version_id"])
    rrec = json.loads(rrow["canonical_json"])
    assert rrow["receipt_id"] == v["validation_receipt_id"]
    assert (rrec["version_id"], rrec["spec_hash"]) == (v["version_id"],
                                                       v["spec_hash"])
    assert rrec["gate1_look"]["seq"] == look["seq"]
    assert rrec["gate1_look"]["p"] == look["p"] <= look["alpha_t"]
    assert rrec["gate3"]["passed"] is True
    assert F.state_of(j, v["version_id"]) == F.VALIDATED

    # -> probation
    F.start_probation(j, v["version_id"], at_ms=T0)
    assert F.state_of(j, v["version_id"]) == F.SHADOW
    _install(j, v["version_id"])
    _trades(j, vrec["strategy_id"], PASSING, T0)
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    prow = _row(j, "SELECT * FROM strategy_probation_receipts "
                   "WHERE receipt_id=?", p["probation_receipt_id"])
    prec = json.loads(prow["canonical_json"])
    assert prec["status"] == F.P_SATISFIED
    assert prec["validation_receipt_id"] == rrow["receipt_id"]
    assert prec["installed_spec_hash"] == v["spec_hash"]
    assert [t["id"] for t in prec["trades"]] == [f"t{i}" for i in range(15)]
    assert prec["policy"] == {"source": "config.yaml strategies.*",
                              "min_trades": 15, "min_winrate": 0.4,
                              "min_profit_factor": 1.15}

    # -> approval request
    qrow = _row(j, "SELECT * FROM strategy_approval_requests "
                   "WHERE version_id=?", v["version_id"])
    qrec = json.loads(qrow["canonical_json"])
    assert qrow["request_id"] == p["request_id"]
    assert {k: qrec[k] for k in ("version_id", "spec_hash",
                                 "validation_receipt_id",
                                 "probation_receipt_id")} == {
        "version_id": v["version_id"], "spec_hash": v["spec_hash"],
        "validation_receipt_id": rrow["receipt_id"],
        "probation_receipt_id": prow["receipt_id"]}
    assert F.state_of(j, v["version_id"]) == F.APPROVAL_REQUIRED
    assert not F.eligible_for_first_live(
        j, v["version_id"], cfg=cfg, available_inputs=INPUTS).eligible

    # -> owner approval
    d = F.record_owner_decision(j, cfg, qrow["request_id"], "APPROVED",
                                actor="operator", decided_at_ms=T0 + 31 * DAY)
    drow = _row(j, "SELECT * FROM strategy_approval_decisions "
                   "WHERE request_id=?", qrow["request_id"])
    drec = json.loads(drow["canonical_json"])
    assert drow["decision_id"] == d["decision_id"]
    assert (drec["version_id"], drec["spec_hash"], drec["actor"],
            drec["decision"]) == (v["version_id"], v["spec_hash"],
                                  "operator", "APPROVED")

    # -> first-live eligibility
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                  available_inputs=INPUTS)
    assert e.eligible and e.reasons == () and e.decision_id == d[
        "decision_id"]
    assert e.capacity["status"] == "UNAVAILABLE"

    # the lifecycle log links each edge by its stored id
    ev = F.events(j, v["version_id"])
    assert [(x["from_state"], x["to_state"], x["ref_id"]) for x in ev] == [
        (None, F.VALIDATED, rrow["receipt_id"]),
        (F.VALIDATED, F.SHADOW, rrow["receipt_id"]),
        (F.SHADOW, F.APPROVAL_REQUIRED, qrow["request_id"]),
        (F.APPROVAL_REQUIRED, F.APPROVED_FIRST_LIVE, drow["decision_id"])]


def test_spec_is_the_one_the_kernel_handoff_builds(tmp_path, cfg):
    """No parallel spec construction: the frozen spec is the one
    kernel._research_handoff passes to analyst.admit, compiled."""
    from trader.kernel import Kernel
    j, h = _journal(tmp_path, state="reason_passed")
    seen = []

    class _Analyst:
        def admit(self, spec, book):
            seen.append(spec.to_dict())
            return False, {"reason": "capture only"}

    cand = _row(j, "SELECT * FROM research_candidates WHERE hash=?", h)
    built = F.candidate_spec(j, cfg, cand).to_dict()
    k = Kernel.__new__(Kernel)
    k.journal, k.cfg, k.notifier = j, {"research": {"handoff": True}}, None
    assert k._research_handoff(_Analyst(), []) is None
    assert built == seen[0]
    (tmp_path / "b").mkdir()
    j2 = Journal(tmp_path / "b" / "j.db")
    assert _candidate(j2, state="reason_passed") == h
    v = F.create_version(j2, cfg, {"kind": "research_candidate", "hash": h},
                         at_ms=T0)
    frozen = F.load_version(j2, v["version_id"])["spec"]
    assert {k: v2 for k, v2 in frozen.items() if k != "data_requires"} == {
        k: v2 for k, v2 in built.items() if k != "data_requires"}


# ── refusals ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("state", ["queued", "twin", "deferred", "gate1_fail",
                                   "gate3_fail", "refused"])
def test_candidate_not_referee_passed_is_refused(tmp_path, cfg, state):
    j, h = _journal(tmp_path, state=state)
    with pytest.raises(F.HandoffRefused) as e:
        F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                         at_ms=T0)
    assert e.value.code == f"candidate_not_referee_passed:{state}"
    assert j.query("SELECT * FROM strategy_versions") == []


@pytest.mark.parametrize("kw,code", [
    ({"look": False}, "gate1_look_missing"),
    ({"p": 0.01}, "gate1_not_rejected"),
    ({"gate3": False}, "gate3_not_passed"),
    ({"g1_p": 0.0001}, "candidate_gate1_does_not_match_look"),
])
def test_missing_or_inconsistent_evidence_is_refused(tmp_path, cfg, kw, code):
    j, h = _journal(tmp_path, **kw)
    with pytest.raises(F.HandoffRefused) as e:
        F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                         at_ms=T0)
    assert e.value.code == code


def test_unknown_candidate_and_other_sources_are_refused(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    for src, code in [({"kind": "research_candidate", "hash": "nope"},
                       "candidate_not_found"),
                      ({"kind": "spec_writer", "hash": "x"},
                       "not_a_referee_candidate"),
                      ({}, "not_a_referee_candidate")]:
        with pytest.raises(F.HandoffRefused) as e:
            F.create_version(j, cfg, src, at_ms=T0)
        assert e.value.code == code


def test_investigation_research_result_never_qualifies(tmp_path, cfg, paths):
    """A real SUPPORTED investigation_volume_anomaly result."""
    from tests.test_investigation_research_family import measured
    from trader.observability import investigation_research as R
    _src, dest, _ = paths
    iid, end = measured(paths, "same_direction")
    assert R.run(dest, iid, recorded_at_ms=end + 2)["result_status"] == \
        "SUPPORTED"
    result = R.chain(dest, iid)["runs"][-1]["result"]
    assert result["predictive_edge_established"] is False
    j, _h = _journal(tmp_path)
    for src in ({"kind": "investigation_research", "record": result},
                {"kind": "research_candidate", "record": result,
                 "hash": result["result_id"]}):
        with pytest.raises(F.HandoffRefused) as e:
            F.create_version(j, cfg, src, at_ms=T0)
        assert e.value.code == "predictive_edge_not_established"
    assert j.query("SELECT * FROM strategy_versions") == []


# ── immutability and idempotency ─────────────────────────────────────────
def test_version_is_immutable_and_retry_is_idempotent(tmp_path, cfg):
    j, h = _journal(tmp_path)
    src = {"kind": "research_candidate", "hash": h}
    a = F.create_version(j, cfg, src, at_ms=T0)
    b = F.create_version(j, cfg, src, at_ms=T0 + 5 * DAY)
    assert b["status"] == "duplicate" and b["version_id"] == a["version_id"]
    assert b["validation_receipt_id"] == a["validation_receipt_id"]
    assert len(j.query("SELECT * FROM strategy_versions")) == 1
    assert len(j.query("SELECT * FROM strategy_validation_receipts")) == 1
    assert len(F.events(j, a["version_id"])) == 1
    for t in ("strategy_versions", "strategy_validation_receipts",
              "strategy_version_events"):
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            with j._tx() as c:
                c.execute(f"DELETE FROM {t}")
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        with j._tx() as c:
            c.execute("UPDATE strategy_versions SET spec_hash='x'")


def test_probation_and_decision_retries_are_idempotent(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    v, p = _to_approval(j, cfg)
    again = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 40 * DAY)
    assert again["request_id"] == p["request_id"]
    assert len(j.query("SELECT * FROM strategy_approval_requests")) == 1
    kw = dict(actor="operator", decided_at_ms=T0 + 31 * DAY)
    d1 = F.record_owner_decision(j, cfg, p["request_id"], "APPROVED", **kw)
    d2 = F.record_owner_decision(j, cfg, p["request_id"], "APPROVED", **kw)
    assert d2["status"] == "duplicate" and d2["decision_id"] == \
        d1["decision_id"]
    with pytest.raises(F.HandoffRefused) as e:
        F.record_owner_decision(j, cfg, p["request_id"], "REJECTED", **kw)
    assert e.value.code == "decision_already_recorded"
    assert len(j.query("SELECT * FROM strategy_approval_decisions")) == 1


def _tamper(j, sql, *a):
    """Bypass the immutability triggers, as a corrupting writer would."""
    with j._tx() as c:
        for t in F._TABLES:
            c.execute(f"DROP TRIGGER IF EXISTS {t}_no_update")
        c.execute(sql, a)


def test_changed_spec_hash_breaks_eligibility(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    v, _p, _d = _approved(j, cfg)
    row = _row(j, "SELECT * FROM strategy_versions WHERE version_id=?",
               v["version_id"])
    rec = json.loads(row["canonical_json"])
    rec["spec"]["exit"]["stop"]["mult"] = 3.0      # an edit in place
    text = F.canonical(rec)
    _tamper(j, "UPDATE strategy_versions SET canonical_json=?, "
               "canonical_sha256=? WHERE version_id=?",
            text, hashlib.sha256(text.encode()).hexdigest(), v["version_id"])
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                  available_inputs=INPUTS)
    assert not e.eligible and e.reasons == ("spec_hash_mismatch",)


def test_edit_creates_new_version_that_needs_its_own_approval(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    a, _p, _d = _approved(j, cfg)
    spec = StrategySpec.from_dict(F.load_version(j, a["version_id"])["spec"])
    spec.exit.stop = {"kind": "atr", "mult": 3.0}
    b = F.derive_version(j, a["version_id"], spec, at_ms=T0 + 32 * DAY)
    assert b["version_id"] != a["version_id"]
    assert b["spec_hash"] != a["spec_hash"]
    brec = F.load_version(j, b["version_id"])
    assert brec["parent_version_id"] == a["version_id"]
    assert F.state_of(j, b["version_id"]) == F.PROPOSED
    eb = F.eligible_for_first_live(j, b["version_id"], cfg=cfg,
                                   available_inputs=INPUTS)
    assert not eb.eligible
    assert {"validation_receipt_missing", "approval_request_missing",
            "state_not_approved:PROPOSED"} <= set(eb.reasons)
    # A's approval is unchanged and still covers A only
    assert F.eligible_for_first_live(j, a["version_id"], cfg=cfg,
                                     available_inputs=INPUTS).eligible
    with pytest.raises(F.HandoffRefused):
        F.start_probation(j, b["version_id"], at_ms=T0 + 33 * DAY)
    unchanged = StrategySpec.from_dict(brec["spec"])
    with pytest.raises(F.HandoffRefused) as e:
        F.derive_version(j, b["version_id"], unchanged, at_ms=T0)
    assert e.value.code == "spec_unchanged"


def test_approval_bound_to_another_version_is_refused(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    a, p = _to_approval(j, cfg)
    req = F.approval_request(j, a["version_id"])
    ident = {"schema": F.DECISION_SCHEMA, "request_id": p["request_id"],
             "version_id": "0" * 64, "spec_hash": "1" * 64,
             "validation_receipt_id": req["validation_receipt_id"],
             "probation_receipt_id": req["probation_receipt_id"],
             "requested_at_ms": req["requested_at_ms"],
             "decision": "APPROVED", "actor": "operator",
             "decided_at_ms": T0 + 31 * DAY}
    rec = {**ident, "decision_id": F._jsha(ident)}
    text = F.canonical(rec)
    with j._tx() as c:          # a forged row: inserts are not blocked
        c.execute("INSERT INTO strategy_approval_decisions VALUES "
                  "(?,?,?,?,?,?,?)",
                  (rec["decision_id"], p["request_id"], "APPROVED",
                   "operator", hashlib.sha256(text.encode()).hexdigest(),
                   text, T0))
        c.execute("INSERT INTO strategy_version_events(version_id, "
                  "from_state, to_state, reason_code, ref_id, actor, at_ms) "
                  "VALUES (?,?,?,?,?,?,?)",
                  (a["version_id"], F.APPROVAL_REQUIRED,
                   F.APPROVED_FIRST_LIVE, "forged", rec["decision_id"],
                   "operator", T0))
    e = F.eligible_for_first_live(j, a["version_id"], cfg=cfg,
                                  available_inputs=INPUTS)
    assert not e.eligible and e.reasons == ("owner_approval_wrong_version",)


def test_rejected_approval_is_never_eligible(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    v, p = _to_approval(j, cfg)
    F.record_owner_decision(j, cfg, p["request_id"], "REJECTED",
                            actor="dashboard", decided_at_ms=T0 + 31 * DAY)
    assert F.state_of(j, v["version_id"]) == F.REJECTED
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                  available_inputs=INPUTS)
    assert not e.eligible
    assert {"version_rejected", "state_not_approved:REJECTED",
            "owner_approval_rejected"} <= set(e.reasons)
    with pytest.raises(F.HandoffRefused):
        F.record_owner_decision(j, cfg, p["request_id"], "APPROVED",
                                actor="operator",
                                decided_at_ms=T0 + 32 * DAY)


def test_decision_requires_owner_actor_and_order(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    _v, p = _to_approval(j, cfg)
    for actor in ("luffy", "factory", "risk_engine", "watchdog", ""):
        with pytest.raises(F.HandoffRefused) as e:
            F.record_owner_decision(j, cfg, p["request_id"], "APPROVED",
                                    actor=actor, decided_at_ms=T0 + 31 * DAY)
        assert e.value.code == "actor_not_owner"
    with pytest.raises(F.HandoffRefused) as e:
        F.record_owner_decision(j, cfg, p["request_id"], "APPROVED",
                                actor="operator", decided_at_ms=T0)
    assert e.value.code == "decision_before_request"
    with pytest.raises(F.HandoffRefused) as e:
        F.record_owner_decision(j, cfg, "f" * 64, "APPROVED",
                                actor="operator", decided_at_ms=T0)
    assert e.value.code == "approval_request_missing"


@pytest.mark.parametrize("to_state", [F.DEGRADED, F.RETIRED])
def test_retired_or_degraded_version_is_not_eligible(tmp_path, cfg,
                                                     to_state):
    j, _h = _journal(tmp_path)
    v, _p, _d = _approved(j, cfg)
    F.retire_version(j, v["version_id"], to_state, reason_code="test",
                     actor="factory", at_ms=T0 + 40 * DAY)
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                  available_inputs=INPUTS)
    assert not e.eligible and f"version_{to_state.lower()}" in e.reasons


def test_candidate_evidence_withdrawn_after_approval(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v, _p, _d = _approved(j, cfg)
    from trader.research.ledger import Ledger
    Ledger(j).set_candidate(h, "4h", "trail", "refused", reason="later")
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                  available_inputs=INPUTS)
    assert not e.eligible
    assert "candidate_not_referee_passed:refused" in e.reasons


# ── probation ────────────────────────────────────────────────────────────
def test_probation_without_registered_policy_stays_shadow(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                         at_ms=T0)
    F.start_probation(j, v["version_id"], at_ms=T0)
    no_policy = {**cfg, "strategies": {k: x for k, x in
                                       cfg["strategies"].items()
                                       if k != "paper_probation_trades"}}
    p = F.evaluate_probation(j, no_policy, v["version_id"], at_ms=T0 + DAY)
    assert p["status"] == F.P_NO_POLICY and p["request_id"] is None
    assert F.state_of(j, v["version_id"]) == F.SHADOW


def test_probation_needs_the_exact_version_installed(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                         at_ms=T0)
    F.start_probation(j, v["version_id"], at_ms=T0)
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + DAY)
    assert p["status"] == F.P_NOT_INSTALLED
    rec = F.load_version(j, v["version_id"])
    spec = StrategySpec.from_dict(rec["spec"])
    spec.regime_filter = ["TRENDING_UP"]      # what admission may measure
    j.upsert_spec(spec, state="paper")
    _trades(j, rec["strategy_id"], PASSING, T0)
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 2 * DAY)
    assert p["status"] == F.P_INSTALLED_DIFFERS
    assert F.state_of(j, v["version_id"]) == F.SHADOW


def test_probation_counts_only_trades_since_it_started(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                         at_ms=T0)
    F.start_probation(j, v["version_id"], at_ms=T0)
    rec = _install(j, v["version_id"])
    _trades(j, rec["strategy_id"], PASSING, T0 - 30 * DAY, prefix="old")
    _trades(j, rec["strategy_id"], PASSING[:14], T0)
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    assert p["status"] == F.P_INSUFFICIENT
    _trades(j, rec["strategy_id"], [-5.0], T0 + 20 * DAY, prefix="n")
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 31 * DAY)
    assert p["status"] == F.P_SATISFIED        # 9 W / 6 L, PF 90/30


def test_probation_below_policy_is_not_satisfied(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                         at_ms=T0)
    F.start_probation(j, v["version_id"], at_ms=T0)
    rec = _install(j, v["version_id"])
    _trades(j, rec["strategy_id"], [10.0] * 5 + [-5.0] * 10, T0)
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    assert p["status"] == F.P_NOT_SATISFIED
    assert F.state_of(j, v["version_id"]) == F.SHADOW


def test_changed_probation_evidence_or_policy_revokes(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    v, _p, _d = _approved(j, cfg)
    stricter = {**cfg, "strategies": {**cfg["strategies"],
                                      "paper_min_profit_factor": 1.5}}
    e = F.eligible_for_first_live(j, v["version_id"], cfg=stricter,
                                  available_inputs=INPUTS)
    assert not e.eligible and "probation_policy_changed" in e.reasons
    with j._tx() as c:
        c.execute("UPDATE trades SET realized_pnl=-50 WHERE id='t0'")
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                  available_inputs=INPUTS)
    assert not e.eligible and "probation_evidence_changed" in e.reasons


def test_inputs_must_be_asserted_available(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    v, _p, _d = _approved(j, cfg)
    need = F.load_version(j, v["version_id"])["spec"]["data_requires"]
    assert need
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg)
    assert not e.eligible and e.reasons == ("inputs_not_asserted",)
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                  available_inputs=set())
    assert e.reasons == ("inputs_unavailable:" + ",".join(sorted(need)),)
    assert F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                     available_inputs=set(need)).eligible


def test_eligibility_fails_closed_on_empty_or_unknown(tmp_path, cfg):
    j = Journal(tmp_path / "j.db")
    e = F.eligible_for_first_live(j, "a" * 64, cfg=cfg,
                                  available_inputs=INPUTS)
    assert not e.eligible and e.reasons == ("version_missing",)
    F.ensure(j)
    e = F.eligible_for_first_live(j, "a" * 64, cfg=cfg,
                                  available_inputs=INPUTS)
    assert not e.eligible and e.reasons == ("version_missing",)


# ── no trading behavior, no production state ─────────────────────────────
def _dump(path):
    with sqlite3.connect(path) as db:
        return list(db.iterdump())


def test_eligibility_is_read_only(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    v, _p, _d = _approved(j, cfg)
    before = _dump(tmp_path / "j.db")
    for _ in range(3):
        assert F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                         available_inputs=INPUTS).eligible
    assert _dump(tmp_path / "j.db") == before


def test_factory_writes_no_trading_state_and_has_no_trading_caller():
    src = pathlib.Path("trader/strategy/factory_handoff.py").read_text()
    code = src.split('"""', 2)[2]
    writes = set(re.findall(r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|"
                            r"DELETE\s+FROM)\s+(\w+)", code, re.I))
    assert writes - {"ON"} <= set(F._TABLES), writes
    assert set(re.findall(r'_insert\(c, "(\w+)"', code)) <= set(F._TABLES)
    for bad in ("upsert_spec", "state_kv", "control_events", "set_control",
                "create_order", "executor", "orchestrator", "notifier"):
        assert bad not in code, bad
    for f in pathlib.Path("trader").rglob("*.py"):
        if f.name != "factory_handoff.py":
            assert "factory_handoff" not in f.read_text(), f
    cfg = load_config()
    assert cfg["research"]["referee"] is False
    assert cfg["research"]["handoff"] is False
