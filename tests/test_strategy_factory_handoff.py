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
from trader.strategy.spec import ExitSpec, StrategySpec
from tests.test_investigation_state_feedback import paths  # noqa: F401

T0 = 1_790_000_000_000             # probation start (ms)
DAY = 86_400_000
INPUTS = {"ohlcv", "funding", "oi", "ls_ratio", "ls_account_ratio", "taker",
          "basis", "xs", "btc", "ref"}


def _iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


@pytest.fixture
def cfg(monkeypatch):
    # Lifecycle fixtures explicitly register synthetic cost observations.
    # Production/unknown-cost acceptance uses load_config directly.
    from tests.paper_cost_evidence_fixture import register_test_cost_evidence
    register_test_cost_evidence(monkeypatch)
    return load_config()


def _candidate(j, state="referee_passed", p=0.001, alpha=0.0025,
               look=True, gate3=True, g1_p=None, trigger="", scope=""):
    from trader.research import vocab
    from trader.research.combo import Combination, evaluated_record
    from trader.research.ledger import Ledger
    led = Ledger(j)
    led.ensure()
    led.record_gauges("4h", {e: {"p10": -1.0, "p25": -0.5, "p75": 0.5,
                                 "p90": 1.0, "n": 9000, "finite_frac": 0.99,
                                 "usable": True}
                             for e in vocab.expressions("4h")})
    part = vocab.parts_for("4h", led.gauges("4h"))[0]
    c = Combination((part,), "4h", "fixed", trigger=trigger,
                    evaluation_scope=scope)
    led.record_result({"hash": c.hash, "tf": "4h", "geo": "fixed", "k": 1,
                       "parts": list(c.keys), "trades": 300,
                       "portfolio": {"total_pct": 40.0, "max_dd_pct": 20.0},
                       "testable": True, "verdict": "scored",
                       **({"predictive_combo": c.as_dict()} if scope else {})},
                      "survivor", "r", {})
    # a queued/deferred candidate has not been looked at: a spent held-out
    # can never be in those states (ledger refuses to restore it)
    if look and state not in ("queued", "deferred"):
        led.record_test(c.hash, "4h", "fixed", "gate1", p, alpha, False,
                        {"t": 1, "evaluated": evaluated_record(c),
                         "cut_ms": 1_780_000_000_000, "draws": 1999,
                         "a": {"consistency_p_dep": p / 2},
                         "b": {"consistency_p_dep": p / 2},
                         "rotation": {"p": p}})
    led.set_candidate(
        c.hash, "4h", "fixed", state, rank=40.0,
        gate1={"p": p if g1_p is None else g1_p, "alpha": alpha, "t": 1,
               "reason": "fixture"},
        gate3={"passed": gate3, "reason": "fixture book"})
    return c.hash


def _journal(tmp_path, **kw):
    j = Journal(tmp_path / "j.db")     # carries trades.entry_identity_json
    return j, _candidate(j, **kw)


def _upsert(j, spec_d):
    """The existing paper install primitive (Journal.upsert_spec)."""
    j.upsert_spec(StrategySpec.from_dict(spec_d), state="paper",
                  origin="research")


def _install(j, version_id, at_ms=T0):
    """What the Kernel's exact-version handoff does: install the frozen
    spec unchanged, then bind the install (starts probation)."""
    rec = F.load_version(j, version_id)
    _upsert(j, rec["spec"])
    F.record_exact_install(j, version_id, at_ms=at_ms)
    return rec


def _identity(strategy_id, spec_hash, status="VERIFIED"):
    """A trade-entry-identity.v1 body as trade_provenance.entry_identity
    writes it for a spec strategy (fields probation reads)."""
    return json.dumps({"schema_version": "trade-entry-identity.v1",
                       "status": status, "strategy_id": strategy_id,
                       "kind": "spec", "spec_sha256": spec_hash,
                       "reason": "signal_spec_hash_matches_loaded_spec"},
                      sort_keys=True)


_EXACT = object()


def _trades(j, strategy_id, pnls, start_ms, prefix="t", spec_hash=None,
            identity=_EXACT):
    """Closed trades; with the provenance column, each carries the entry
    identity of `spec_hash` (or `identity` verbatim, None = NULL)."""
    has = F.trade_identity_available(j)
    installs = j.query("SELECT * FROM strategy_version_installs WHERE strategy_id=?", (strategy_id,))
    install = dict(installs[0]) if len(installs) == 1 else None
    with j._tx() as c:
        # Explicit TEST-ONLY reference-price simulation provenance for fixtures.
        columns = {r[1] for r in c.execute('PRAGMA table_info(trades)')}
        for name, typ in (('reference_price','REAL'),('exit_reference_price','REAL'),
                          ('fill_basis','TEXT'),('exit_fill_basis','TEXT')):
            if name not in columns:
                c.execute(f'ALTER TABLE trades ADD COLUMN {name} {typ}')
        for i, pnl in enumerate(pnls):
            evidence = None
            geometry = None
            o = start_ms + (i + 1) * 3_600_000
            idn = _identity(strategy_id, spec_hash) if identity is _EXACT \
                else identity
            if identity is _EXACT and install and spec_hash:
                body = json.loads(idn)
                body.update(version_id=install['version_id'], install_id=install['install_id'], exec_mode='paper')
                version = F.load_version(j, install['version_id'])
                if spec_hash == version['spec_hash']:
                    from trader.strategy import exit_policy as E
                    from trader.engine import paper_exit_evidence as X
                    from trader.core.types import TF_MS
                    spec = StrategySpec.from_dict(version['spec'])
                    step = TF_MS[spec.timeframe]
                    policy, initial = E.initialize(spec.exit, 100, 1, 'long', o-step, step)
                    body.update(exit_semantics_id=policy.semantics_id, exit_state=E.encode(policy, initial))
                    # Explicit TEST-ONLY observations replay the real contract.
                    px = policy.target+1 if pnl > 0 else policy.stop-1
                    amount = pnl/(px-100)
                    observation = E.Observation(o, px, px, px, px)
                    result = E.advance(policy, initial, observation, amount)
                    evidence = X.start(f'{prefix}{i}', body, policy, initial, amount)
                    X.observe(evidence, policy, result, observation)
                    geometry = (amount, policy.stop, policy.target, px, policy.initial_r, result.reason, o+step)
                idn = json.dumps(body, sort_keys=True)
            cols = ("id, symbol, side, amount, entry_price, strategy_id, "
                    "exec_mode, opened_at, closed_at, realized_pnl, status")
            vals = [f"{prefix}{i}", "BTC/USDT", "long", 1.0, 100.0,
                    strategy_id, "paper", _iso(o), _iso(o + 1_800_000), pnl,
                    "closed"]
            if has:
                cols += ", entry_identity_json"
                vals.append(idn)
            if geometry:
                amount, sl, tp, px, risk, reason, cl = geometry
                vals[3] = amount
                vals[8] = _iso(cl)
                cols += ',stop_loss,take_profit,exit_price,initial_risk,close_reason'
                vals += [sl,tp,px,risk,reason]
                cols += ',market_type,reference_price,exit_reference_price,fill_basis,exit_fill_basis'
                vals += ['futures',100,px,'TEST-ONLY reference simulation','TEST-ONLY reference simulation']
            c.execute(f"INSERT INTO trades ({cols}) VALUES "
                      f"({','.join('?' * len(vals))})", vals)
            if evidence:
                X.ensure(c)
                X.record(c, evidence)


PASSING = [10.0] * 9 + [-5.0] * 6          # 15 trades, WR 0.6, PF 3.0


def _to_approval(j, cfg, h=None):
    h = h or j.query("SELECT hash FROM research_candidates")[0]["hash"]
    v = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                         at_ms=T0 - DAY)
    rec = _install(j, v["version_id"])
    _trades(j, rec["strategy_id"], PASSING, T0, spec_hash=v["spec_hash"])
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    assert p["status"] == F.P_SATISFIED
    return v, p


def _approved(j, cfg):
    v, p = _to_approval(j, cfg)
    d = F.record_owner_decision(j, cfg, p["request_id"], "APPROVED",
                                actor="operator", decided_at_ms=T0 + 31 * DAY)
    return v, p, d


NOCAP = "capacity_receipt_not_asserted"


def _r(e):
    """Reasons other than the unasserted capacity receipt."""
    return tuple(r for r in e.reasons if r != NOCAP)


def _ok(e):
    """Everything but capacity holds: the only refusal is that no current
    capacity receipt was asserted (capacity is required for first live)."""
    return not e.eligible and e.reasons == (NOCAP,)


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
    # the immutable version is the strategy alone: capacity is evaluated at
    # use against its own receipts, never stored in (or frozen into) it
    assert "capacity" not in vrec
    assert vrec["evidence_ids"]["evaluated_sha256"] == F._jsha(
        json.loads(look["detail"])["evaluated"])

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

    # -> exact paper install -> probation
    _install(j, v["version_id"])
    assert F.state_of(j, v["version_id"]) == F.SHADOW
    irow = _row(j, "SELECT * FROM strategy_version_installs "
                   "WHERE version_id=?", v["version_id"])
    irec = json.loads(irow["canonical_json"])
    assert (irec["strategy_id"], irec["spec_hash"], irec["installed_spec_hash"],
            irec["mode"], irec["installed_at_ms"]) == (
        vrec["strategy_id"], v["spec_hash"], v["spec_hash"], "paper", T0)
    assert irec["installed_spec"] == vrec["spec"]
    _trades(j, vrec["strategy_id"], PASSING, T0, spec_hash=v["spec_hash"])
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    prow = _row(j, "SELECT * FROM strategy_probation_receipts "
                   "WHERE receipt_id=?", p["probation_receipt_id"])
    prec = json.loads(prow["canonical_json"])
    assert prec["status"] == F.P_SATISFIED
    assert prec["validation_receipt_id"] == rrow["receipt_id"]
    assert (prec["install_id"], prec["since_ms"]) == (irow["install_id"], T0)
    assert prec["excluded_trades"] == []
    assert [t["id"] for t in prec["trades"]] == [f"t{i}" for i in range(15)]
    assert prec["policy"] == {"source": "config.yaml strategies.*",
                              "min_trades": 15, "min_winrate": 0.4,
                              "min_profit_factor": 1.15,
                              "winrate_policy": __import__("trader.engine.paper_cost_evidence", fromlist=["WIN_RATE_POLICY"]).WIN_RATE_POLICY}

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
    assert _ok(e) and e.decision_id == d[
        "decision_id"]
    assert e.capacity["receipt_id"] is None and not e.capacity["current"]

    # the lifecycle log links each edge by its stored id
    ev = F.events(j, v["version_id"])
    assert [(x["from_state"], x["to_state"], x["ref_id"]) for x in ev] == [
        (None, F.VALIDATED, rrow["receipt_id"]),
        (F.VALIDATED, F.SHADOW, irow["install_id"]),
        (F.SHADOW, F.APPROVAL_REQUIRED, qrow["request_id"]),
        (F.APPROVAL_REQUIRED, F.APPROVED_FIRST_LIVE, drow["decision_id"])]


def test_kernel_handoff_admits_exactly_the_frozen_version(tmp_path, cfg):
    """One spec construction (factory.candidate_spec); the Kernel freezes
    the version first and hands analyst.admit exactly that spec."""
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
    vrow = _row(j, "SELECT * FROM strategy_versions")
    frozen = F.load_version(j, vrow["version_id"])["spec"]
    assert seen == [frozen]
    assert {k: v2 for k, v2 in frozen.items() if k != "data_requires"} == {
        k: v2 for k, v2 in built.items() if k != "data_requires"}
    assert frozen["universe"]["include"] and \
        frozen["provenance"]["research_hash"] == h


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
    rec["spec"]["exit"]["stop"]["mult"] = 4.0      # an edit in place
    text = F.canonical(rec)
    _tamper(j, "UPDATE strategy_versions SET canonical_json=?, "
               "canonical_sha256=? WHERE version_id=?",
            text, hashlib.sha256(text.encode()).hexdigest(), v["version_id"])
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                  available_inputs=INPUTS)
    assert not e.eligible and _r(e) == ("spec_hash_mismatch",)


def test_edit_creates_new_version_that_needs_its_own_approval(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    a, _p, _d = _approved(j, cfg)
    spec = StrategySpec.from_dict(F.load_version(j, a["version_id"])["spec"])
    spec.exit.stop = {"kind": "atr", "mult": 4.0}
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
    assert _ok(F.eligible_for_first_live(j, a["version_id"], cfg=cfg,
                                         available_inputs=INPUTS))
    with pytest.raises(F.HandoffRefused) as e:
        F.record_exact_install(j, b["version_id"], at_ms=T0 + 33 * DAY)
    assert e.value.code == "validation_receipt_missing"
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
    assert not e.eligible and set(_r(e)) == {"owner_approval_wrong_version", "owner_approval_configuration_not_recorded"}


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
    Ledger(j).set_candidate(h, "4h", "fixed", "refused", reason="later")
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                  available_inputs=INPUTS)
    assert not e.eligible
    assert "candidate_not_referee_passed:refused" in e.reasons


# ── exact paper install ──────────────────────────────────────────────────
def _validated(j, cfg, h):
    return F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                            at_ms=T0 - DAY)


def test_exact_spec_installs_byte_and_hash_identically(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = _validated(j, cfg, h)
    rec = _install(j, v["version_id"])
    row = _row(j, "SELECT * FROM strategies WHERE id=?", rec["strategy_id"])
    assert (row["kind"], row["state"]) == ("spec", "paper")
    installed = StrategySpec.from_json(row["spec_json"]).to_dict()
    assert F.canonical(installed) == F.canonical(rec["spec"])
    assert hashlib.sha256(F.canonical(installed).encode()).hexdigest() == \
        v["spec_hash"]
    irec = F.verify_install(j, rec)
    assert irec["installed_spec_hash"] == v["spec_hash"]
    again = F.record_exact_install(j, v["version_id"], at_ms=T0 + DAY)
    assert again["status"] == "duplicate"           # probation start is fixed
    assert F.verify_install(j, rec)["installed_at_ms"] == T0


@pytest.mark.parametrize("field,value", [
    ("timeframe", "1h"), ("regime_filter", ["TRENDING_UP"]),
    ("entry_long", "close > 0"),
    ("provenance", {"source_kind": "research", "regime_evidence": {}})])
def test_modified_install_is_refused(tmp_path, cfg, field, value):
    j, h = _journal(tmp_path)
    v = _validated(j, cfg, h)
    spec = dict(F.load_version(j, v["version_id"])["spec"])
    spec[field] = value
    _upsert(j, spec)
    with pytest.raises(F.HandoffRefused) as e:
        F.record_exact_install(j, v["version_id"], at_ms=T0)
    assert e.value.code == F.P_INSTALLED_DIFFERS
    assert F.state_of(j, v["version_id"]) == F.VALIDATED   # no probation
    assert j.query("SELECT * FROM strategy_version_installs") == []


def test_nothing_installed_is_refused(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = _validated(j, cfg, h)
    with pytest.raises(F.HandoffRefused) as e:
        F.record_exact_install(j, v["version_id"], at_ms=T0)
    assert e.value.code == F.P_NOT_INSTALLED
    assert F.state_of(j, v["version_id"]) == F.VALIDATED


class _Admitting:
    """analyst.admit + set_measured_regimes as the Kernel calls them. The
    regime measurement mutates what it is given, as the real one does."""

    def __init__(self, tf="4h"):
        self.tf, self.admitted, self.measured = tf, [], []

    def admit(self, spec, book):
        self.admitted.append(spec.to_dict())
        return True, {"reason": "test", "chosen_timeframe": self.tf,
                      "recent": {"pooled_pf": 1.3, "trades": 30}}

    def set_measured_regimes(self, spec):
        spec.regime_filter = ["TRENDING_UP"]
        spec.provenance = {**spec.provenance, "regime_evidence": {"x": 1}}
        self.measured.append(spec)
        return {"fit": ["TRENDING_UP"], "changed": True}


def _kernel(j):
    from trader.kernel import Kernel
    k = Kernel.__new__(Kernel)
    k.journal, k.cfg, k.notifier = j, {"research": {"handoff": True}}, None
    return k


def test_kernel_installs_the_exact_version_and_starts_probation(tmp_path):
    j, h = _journal(tmp_path, state="reason_passed")
    an = _Admitting()
    spec = _kernel(j)._research_handoff(an, [])
    assert spec is not None and an.measured    # regimes measured on a copy
    vrow = _row(j, "SELECT * FROM strategy_versions")
    v = F.load_version(j, vrow["version_id"])
    row = _row(j, "SELECT * FROM strategies WHERE id=?", v["strategy_id"])
    installed = StrategySpec.from_json(row["spec_json"]).to_dict()
    assert installed == v["spec"]              # regime_filter NOT replaced
    assert installed["regime_filter"] == [] and \
        "regime_evidence" not in installed["provenance"]
    assert F.state_of(j, v["version_id"]) == F.SHADOW
    F.verify_install(j, v)
    ev = [json.loads(r["detail"]) for r in j.query(
        "SELECT detail FROM brain_events WHERE kind='spec_admitted'")]
    assert ev[0]["version_id"] == v["version_id"]
    assert ev[0]["regimes_advisory_not_applied"]["fit"] == ["TRENDING_UP"]
    assert _row(j, "SELECT state FROM research_candidates")["state"] == \
        "admitted"


def test_kernel_refuses_a_timeframe_other_than_the_version(tmp_path):
    j, h = _journal(tmp_path, state="reason_passed")
    assert _kernel(j)._research_handoff(_Admitting(tf="1h"), []) is None
    vrow = _row(j, "SELECT * FROM strategy_versions")
    assert F.state_of(j, vrow["version_id"]) == F.VALIDATED
    assert j.query("SELECT * FROM strategies") == []      # nothing installed
    assert F.load_version(j, vrow["version_id"])["spec"]["timeframe"] == "4h"
    ev = json.loads(_row(j, "SELECT detail FROM brain_events WHERE "
                            "kind='spec_install_refused'")["detail"])
    assert ev["reason_code"] == "timeframe_differs_from_version"
    cand = _row(j, "SELECT * FROM research_candidates")
    assert cand["state"] == "refused" and "not installed" in cand["reason"]


def test_kernel_refuses_a_spec_that_is_not_the_frozen_one(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = _validated(j, cfg, h)
    spec = StrategySpec.from_dict(F.load_version(j, v["version_id"])["spec"])
    spec.exit.stop = {"kind": "atr", "mult": 4.0}
    ok = _kernel(j)._install_spec(
        spec, {"chosen_timeframe": spec.timeframe}, _Admitting(), {},
        version_id=v["version_id"])
    assert ok is False and j.query("SELECT * FROM strategies") == []
    assert F.state_of(j, v["version_id"]) == F.VALIDATED


def test_unversioned_install_keeps_its_existing_behavior(tmp_path):
    """Strategist-admitted specs have no frozen version; their admission
    path is unchanged by this package."""
    j = Journal(tmp_path / "j.db")
    spec = StrategySpec(
        id="s1", name="Plain spec", thesis="t" * 90, invalidation="i" * 40,
        provenance={}, universe={}, timeframe="4h", direction="long",
        entry_long="close > 0", entry_short="", filters=[], exit=ExitSpec(),
        regime_filter=[], markets=["futures"])
    ok = _kernel(j)._install_spec(
        spec, {"chosen_timeframe": "1h", "recent": {"pooled_pf": 1.2,
                                                    "trades": 20}},
        _Admitting(), {})
    assert ok is True
    stored = StrategySpec.from_json(_row(j, "SELECT spec_json FROM "
                                            "strategies")["spec_json"])
    assert stored.timeframe == "1h" and stored.regime_filter == ["TRENDING_UP"]


# ── probation bound to the exact version ─────────────────────────────────
def test_probation_without_registered_policy_stays_shadow(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = _validated(j, cfg, h)
    _install(j, v["version_id"])
    no_policy = {**cfg, "strategies": {k: x for k, x in
                                       cfg["strategies"].items()
                                       if k != "paper_probation_trades"}}
    p = F.evaluate_probation(j, no_policy, v["version_id"], at_ms=T0 + DAY)
    assert p["status"] == F.P_NO_POLICY and p["request_id"] is None
    assert F.state_of(j, v["version_id"]) == F.SHADOW


def test_probation_cannot_start_without_exact_install(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = _validated(j, cfg, h)
    with pytest.raises(F.HandoffRefused) as e:
        F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + DAY)
    assert e.value.code == "not_in_probation:VALIDATED"


def test_probation_stops_advancing_if_install_is_replaced(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = _validated(j, cfg, h)
    rec = _install(j, v["version_id"])
    _trades(j, rec["strategy_id"], PASSING, T0, spec_hash=v["spec_hash"])
    spec = dict(rec["spec"])
    spec["timeframe"] = "1h"
    _upsert(j, spec)
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    assert p["status"] == F.P_INSTALLED_DIFFERS and p["request_id"] is None
    assert F.state_of(j, v["version_id"]) == F.SHADOW


def test_exact_version_trades_count_and_others_do_not(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = _validated(j, cfg, h)
    rec = _install(j, v["version_id"])
    sid = rec["strategy_id"]
    _trades(j, sid, PASSING, T0 - 30 * DAY, prefix="old",
            spec_hash=v["spec_hash"])                  # before install
    _trades(j, sid, [10.0] * 14, T0, spec_hash=v["spec_hash"])
    _trades(j, sid, [-50.0] * 20, T0, prefix="other",
            spec_hash="b" * 64)                        # same id, other hash
    _trades(j, "someone_else", [-50.0] * 5, T0, prefix="x",
            spec_hash=v["spec_hash"])                  # other strategy id
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    prec = F._load(_row(j, "SELECT * FROM strategy_probation_receipts "
                           "WHERE receipt_id=?", p["probation_receipt_id"]))
    assert p["status"] == F.P_INCOMPLETE and p["request_id"] is None  # mixed version evidence
    assert [t["id"] for t in prec["trades"]] == [f"t{i}" for i in range(14)]
    assert {e["id"]: e["reason"] for e in prec["excluded_trades"]} == {
        f"other{i}": "other_version" for i in range(20)}
    _trades(j, sid, [-5.0], T0 + 20 * DAY, prefix="n",
            spec_hash=v["spec_hash"])
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 31 * DAY)
    assert p["status"] == F.P_INCOMPLETE and p["request_id"] is None


@pytest.mark.parametrize("identity", [
    None, "not json", _identity("x", "a" * 64, status="AMBIGUOUS"),
    json.dumps({"schema_version": "trade-entry-identity.v1",
                "status": "UNKNOWN", "reason": "signal_carries_no_spec_hash"}),
])
def test_unattributed_trade_makes_probation_incomplete(tmp_path, cfg,
                                                       identity):
    j, h = _journal(tmp_path)
    v = _validated(j, cfg, h)
    rec = _install(j, v["version_id"])
    _trades(j, rec["strategy_id"], PASSING, T0, spec_hash=v["spec_hash"])
    _trades(j, rec["strategy_id"], [-5.0], T0, prefix="u", identity=identity)
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    assert p["status"] == F.P_INCOMPLETE and p["request_id"] is None
    assert F.state_of(j, v["version_id"]) == F.SHADOW


def test_missing_trade_version_identity_fails_closed(tmp_path, cfg,
                                                     monkeypatch):
    """A journal without Trade Provenance's column: nothing attributable."""
    j, h = _journal(tmp_path)
    assert F.trade_identity_available(j)
    monkeypatch.setattr(F, "trade_identity_available", lambda _j: False)
    v = _validated(j, cfg, h)
    rec = _install(j, v["version_id"])
    _trades(j, rec["strategy_id"], PASSING, T0)
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    assert p["status"] == F.P_IDENTITY_UNAVAILABLE
    assert F.state_of(j, v["version_id"]) == F.SHADOW


def test_probation_below_policy_is_not_satisfied(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = _validated(j, cfg, h)
    rec = _install(j, v["version_id"])
    _trades(j, rec["strategy_id"], [10.0] * 5 + [-5.0] * 10, T0,
            spec_hash=v["spec_hash"])
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=T0 + 30 * DAY)
    assert p["status"] == F.P_NOT_SATISFIED
    assert F.state_of(j, v["version_id"]) == F.SHADOW


def test_derived_version_inherits_no_probation(tmp_path, cfg):
    j, h = _journal(tmp_path)
    a = _validated(j, cfg, h)
    rec = _install(j, a["version_id"])
    _trades(j, rec["strategy_id"], PASSING, T0, spec_hash=a["spec_hash"])
    spec = StrategySpec.from_dict(rec["spec"])
    spec.exit.stop = {"kind": "atr", "mult": 4.0}
    b = F.derive_version(j, a["version_id"], spec, at_ms=T0 + DAY)
    bv = F.load_version(j, b["version_id"])
    # B has no install, no probation, no receipts
    for t in ("strategy_version_installs", "strategy_probation_receipts",
              "strategy_approval_requests"):
        assert j.query(f"SELECT * FROM {t} WHERE version_id=?",
                       (b["version_id"],)) == []
    with pytest.raises(F.HandoffRefused):
        F.evaluate_probation(j, cfg, b["version_id"], at_ms=T0 + 30 * DAY)
    # installing B's spec in paper does not give B probation either
    _upsert(j, bv["spec"])
    with pytest.raises(F.HandoffRefused):
        F.record_exact_install(j, b["version_id"], at_ms=T0 + 2 * DAY)
    # and A's probation stays A's: B's trades never count for A
    _trades(j, rec["strategy_id"], [-50.0] * 5, T0 + 2 * DAY, prefix="b",
            spec_hash=b["spec_hash"])
    _upsert(j, rec["spec"])                        # A back in paper
    p = F.evaluate_probation(j, cfg, a["version_id"], at_ms=T0 + 30 * DAY)
    prec = F._load(_row(j, "SELECT * FROM strategy_probation_receipts "
                           "WHERE receipt_id=?", p["probation_receipt_id"]))
    assert p["status"] == F.P_INCOMPLETE and p["request_id"] is None
    assert all(t["id"].startswith("t") for t in prec["trades"])
    assert {e["reason"] for e in prec["excluded_trades"]} == {"other_version"}


def test_eligibility_requires_the_version_still_installed(tmp_path, cfg):
    j, _h = _journal(tmp_path)
    a, p, _d = _approved(j, cfg)
    spec = StrategySpec.from_dict(F.load_version(j, a["version_id"])["spec"])
    spec.exit.stop = {"kind": "atr", "mult": 4.0}
    b = F.derive_version(j, a["version_id"], spec, at_ms=T0 + 32 * DAY)
    _upsert(j, F.load_version(j, b["version_id"])["spec"])   # B now in paper
    e = F.eligible_for_first_live(j, a["version_id"], cfg=cfg,
                                  available_inputs=INPUTS)
    assert not e.eligible and _r(e) == ("installed_version_differs",)
    # A's probation receipt itself is still A's and still verifies
    F._verify_probation(j, cfg, F.load_version(j, a["version_id"]),
                        p["probation_receipt_id"])
    assert not F.eligible_for_first_live(j, b["version_id"], cfg=cfg,
                                         available_inputs=INPUTS).eligible
    with pytest.raises(__import__('sqlite3').IntegrityError,match='STRATEGY_GOVERNOR_REQUIRED'):
        with j._tx() as c:
            c.execute("DELETE FROM strategies")
    # Isolated privileged corruption fixture, to verify missing-install refusal.
    j._local.governor_write=True
    try:
        with j._tx() as c:
            c.execute("DELETE FROM strategies")
    finally:
        j._local.governor_write=False
    e = F.eligible_for_first_live(j, a["version_id"], cfg=cfg,
                                  available_inputs=INPUTS)
    assert _r(e) == ("installed_version_missing",)


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
    assert not e.eligible and _r(e) == ("inputs_not_asserted",)
    e = F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                  available_inputs=set())
    assert _r(e) == ("inputs_unavailable:" + ",".join(sorted(need)),)
    assert _ok(F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                         available_inputs=set(need)))


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
        assert _ok(F.eligible_for_first_live(j, v["version_id"], cfg=cfg,
                                             available_inputs=INPUTS))
    assert _dump(tmp_path / "j.db") == before


def test_factory_writes_no_trading_state_and_has_no_trading_caller():
    src = pathlib.Path("trader/strategy/factory_handoff.py").read_text()
    code = src.split('"""', 2)[2]
    writes = set(re.findall(r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|"
                            r"DELETE\s+FROM)\s+(\w+)", code, re.I))
    assert writes - {"ON","OF","strategies"} <= set(F._TABLES), writes
    assert set(re.findall(r'_insert\(c, "(\w+)"', code)) <= set(F._TABLES)
    for bad in ("upsert_spec", "state_kv", "control_events", "set_control",
                "create_order", "executor", "orchestrator", "notifier"):
        assert bad not in code, bad
    # the executor reads only the paper/live fence predicate
    for f in pathlib.Path("trader").rglob("*.py"):
        if f not in (pathlib.Path('trader/engine/entry_authority.py'), pathlib.Path('trader/owner/queries.py'), pathlib.Path('trader/owner/approvals.py'), pathlib.Path('trader/learning/foundation.py'), pathlib.Path('trader/learning/capture.py'), pathlib.Path('trader/learning/application.py'), pathlib.Path('trader/learning/runtime.py'), pathlib.Path('trader/brain/analyst.py'), pathlib.Path('trader/portfolio/current.py'), pathlib.Path('trader/research/predictive_bridge.py'), pathlib.Path('trader/strategy/compile.py')) and f.name not in ("factory_handoff.py", "kernel.py", "executor.py", "paper.py", "versioned_exits.py", "exits.py", "journal.py", "opportunity_live.py", "candidate_bridge.py", "legacy_authority.py", "stage5_activation.py"):
            assert "factory_handoff" not in f.read_text(), f
    # STR-02: the compiler may only READ/verify an exact version; it can never
    # create, install, govern or retire one (compiling grants no authority).
    compile_src = pathlib.Path('trader/strategy/compile.py').read_text()
    assert set(re.findall(r"\bfh\.(\w+)", compile_src)) <= {
        'load_version', 'verify_validation', 'verify_install', 'state_of',
        'VERSION_SCHEMA', 'RETIRED', 'REJECTED', '_refuse', 'HandoffRefused'}
    # Final admission reads only the same immutable live-entry fence as Executor.
    admission = pathlib.Path('trader/engine/entry_authority.py').read_text()
    assert set(re.findall(r"\bfactory_handoff\.(\w+)", admission)) == {"live_entry_block"}
    # Stage8 may read exact versions and record an authenticated exact owner
    # decision. It cannot create/install/activate/govern a strategy version.
    for path in ('trader/owner/queries.py', 'trader/owner/approvals.py'):
        text = pathlib.Path(path).read_text()
        for forbidden in ('create_version(', 'derive_version(', 'record_exact_install(',
                          'govern_version(', 'evaluate_probation(', 'create_order('):
            assert forbidden not in text, (path, forbidden)
    # Corrected Stage6 uses only the exact immutable version reader for capacity.
    current_src = pathlib.Path("trader/portfolio/current.py").read_text()
    assert set(re.findall(r"\bfactory_handoff\.(\w+)", current_src)) == {"load_version"}
    # Predictive research may READ registered gates, and cannot call a Factory writer.
    import ast
    bridge = ast.parse(pathlib.Path('trader/research/predictive_bridge.py').read_text())
    imports = [node for node in ast.walk(bridge) if isinstance(node, ast.ImportFrom)
               and node.module == 'trader.strategy.factory_handoff']
    assert len(imports) == 1 and [n.name for n in imports[0].names] == ['gate_evidence']
    ex_src = pathlib.Path("trader/engine/executor.py").read_text()
    assert set(re.findall(r"\bfh\.(\w+)", ex_src)) == {"live_entry_block"}
    # the Kernel reaches the factory from the research handoff, which
    # research.handoff=false keeps closed, its exact-version install, and
    # two read-only predicates: the paper/live fence and the versioned flag
    import ast
    k_src = pathlib.Path("trader/kernel.py").read_text()
    tree = ast.parse(k_src)
    users = {fn.name for fn in ast.walk(tree)
             if isinstance(fn, ast.FunctionDef)
             and "factory_handoff" in ast.unparse(fn)
             and not any("factory_handoff" in ast.unparse(g) for g in
                         ast.walk(fn) if g is not fn
                         and isinstance(g, ast.FunctionDef))}
    assert users == {"_research_handoff", "_install_version",
                     "_load_spec_population", "_try_enter", "_manage_one", "_mechanism_once",
                     "_install_spec", "_manage_paper", "_compile_population_spec"}, users
    for name in ("_load_spec_population", "_try_enter"):
        fn = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef)
                  and f.name == name)
        assert set(re.findall(r"\bfh\.(\w+)", ast.unparse(fn))) <= {
            "versioned", "live_entry_block", "load_version", "verify_validation", "verify_install"}, name
    for name, allowed in (("_install_spec", {"versioned"}),
                          ("_compile_population_spec", {"versioned"}),
                          ("_manage_paper", {"state_of", "evaluate_probation", "SHADOW"})):
        fn = next(f for f in ast.walk(tree) if isinstance(f, ast.FunctionDef) and f.name == name)
        assert set(re.findall(r"\bfh\.(\w+)", ast.unparse(fn))) <= allowed
    cfg = load_config()
    assert cfg["research"]["referee"] is False
    assert cfg["research"]["handoff"] is False


# ── the real identity bridge (Trade Provenance x exact version) ──────────
def test_legacy_compiler_provenance_without_factory_identity_is_incomplete(tmp_path, cfg):
    """Nothing hand-built: the Kernel installs the exact version, loads its
    population, stamps the entry identity the way it does before an order,
    the journal books the trade; missing factory version/install binding
    now makes probation incomplete despite verified compiler provenance."""
    from types import SimpleNamespace
    import pandas as pd
    from tests.test_trade_provenance import decision
    from trader.core.types import Position, Side
    j, h = _journal(tmp_path, state="reason_passed")
    k = _kernel(j)
    assert k._research_handoff(_Admitting(), []) is not None
    v = F.load_version(j, _row(j, "SELECT * FROM strategy_versions")
                       ["version_id"])
    sid = v["strategy_id"]
    install = F.verify_install(j, v)
    k._load_population()
    loaded = k._entry_identities[sid]
    assert loaded["spec_sha256"] == v["spec_hash"]      # population = version
    snap = SimpleNamespace(price=100.0, ts=_iso(T0),
                           df=lambda tf: pd.DataFrame({"ts": [_iso(T0)],
                                                       "close": [100.0]}))

    def book(i, stamped, pnl):
        d = decision(j, did=f"d{i}")
        d.strategy_signals = [{"strategy_id": sid, "action": "BUY",
                               "params": {"spec_id": sid,
                                          "spec_sha256": stamped}}]
        identity, _ref = k._entry_provenance(d, snap, sid, "15m")
        o = install["installed_at_ms"] + (i + 1) * 3_600_000
        j.add_trade(Position(id=f"p{i}", symbol="BTC/USDT", side=Side.LONG,
                             amount=1.0, entry_price=100.0,
                             notional_usdt=100.0, strategy_id=sid,
                             decision_id=d.id, exec_mode="paper",
                             opened_at=_iso(o), entry_identity=identity))
        j.close_trade(f"p{i}", 100.0 + pnl, pnl, "test",
                      closed_at=_iso(o + 1_800_000))
        return identity

    first = book(0, v["spec_hash"], 10.0)
    assert (first["status"], first["spec_sha256"]) == ("VERIFIED",
                                                        v["spec_hash"])
    stored = json.loads(_row(j, "SELECT entry_identity_json FROM trades "
                                "WHERE id='p0'")["entry_identity_json"])
    assert stored["spec_sha256"] == v["spec_hash"]
    for i, pnl in enumerate(PASSING[1:], start=1):
        book(i, v["spec_hash"], pnl)
    at = install["installed_at_ms"] + 30 * DAY
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=at)
    assert p["status"] == F.P_INCOMPLETE and p["request_id"] is None
    prec = F._load(_row(j, "SELECT * FROM strategy_probation_receipts "
                           "WHERE receipt_id=?", p["probation_receipt_id"]))
    assert len(prec["trades"]) == 0 and prec["install_id"] == \
        install["install_id"]


def test_real_identity_other_version_and_missing_identity(tmp_path, cfg):
    from types import SimpleNamespace
    import pandas as pd
    from tests.test_trade_provenance import decision
    from trader.core.types import Position, Side
    j, h = _journal(tmp_path, state="reason_passed")
    k = _kernel(j)
    k._research_handoff(_Admitting(), [])
    v = F.load_version(j, _row(j, "SELECT * FROM strategy_versions")
                       ["version_id"])
    sid, t0 = v["strategy_id"], F.verify_install(j, v)["installed_at_ms"]
    k._load_population()
    snap = SimpleNamespace(price=100.0, ts=_iso(T0), df=lambda tf: None)

    def book(i, stamped, pnl, identity=True):
        d = decision(j, did=f"d{i}")
        d.strategy_signals = [{"strategy_id": sid, "action": "BUY",
                               "params": {"spec_id": sid,
                                          "spec_sha256": stamped}}]
        idn = k._entry_provenance(d, snap, sid, "15m")[0] if identity \
            else None
        o = t0 + (i + 1) * 3_600_000
        j.add_trade(Position(id=f"p{i}", symbol="BTC/USDT", side=Side.LONG,
                             amount=1.0, entry_price=100.0,
                             notional_usdt=100.0, strategy_id=sid,
                             decision_id=d.id, exec_mode="paper",
                             opened_at=_iso(o), entry_identity=idn))
        j.close_trade(f"p{i}", 100.0 + pnl, pnl, "test",
                      closed_at=_iso(o + 1_800_000))

    for i, pnl in enumerate(PASSING):
        book(i, v["spec_hash"], pnl)
    # a signal from another evaluator version: provenance marks it AMBIGUOUS
    # against the loaded population, so it is unattributable, never counted
    book(100, "c" * 64, -50.0)
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=t0 + 30 * DAY)
    assert p["status"] == F.P_INCOMPLETE
    # adopted/legacy entries carry no identity at all: also fail closed
    (tmp_path / "x").mkdir()
    j2, _ = _journal(tmp_path / "x", state="reason_passed")
    k2 = _kernel(j2)
    k2._research_handoff(_Admitting(), [])
    v2 = F.load_version(j2, _row(j2, "SELECT * FROM strategy_versions")
                        ["version_id"])
    # the kernel's own risk configuration is part of the version identity
    # (STR-01): the same spec under it is the same strategy, not the same version
    assert v2["spec_hash"] == v["spec_hash"]
    v = v2
    j, k = j2, k2
    k._load_population()
    t0 = F.verify_install(j, v2)["installed_at_ms"]
    for i, pnl in enumerate(PASSING):
        book(i, v["spec_hash"], pnl, identity=(i != 3))
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=t0 + 30 * DAY)
    assert p["status"] == F.P_INCOMPLETE
