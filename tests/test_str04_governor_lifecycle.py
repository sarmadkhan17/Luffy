"""STR-04: the Strategy Governor is the sole lifecycle authority.

Offline, temporary journals; no venue/provider, Kernel or Dashboard.
"""
import json
import re
import sqlite3
import pathlib

import pytest

from trader.core.journal import Journal
from trader.strategy import factory_handoff as F
from trader.strategy.spec import StrategySpec
from tests.authority_factory_fixtures import (  # noqa: F401
    INPUTS, T0, DAY, _journal, _install, cfg)
from tests.authority_legacy_fixtures import grant
from tests.test_universal_strategy_authority import (
    NOW, activate, approved_world, established_capacity)

VERSION_TABLES = ("strategy_versions", "strategy_validation_receipts",
                  "strategy_version_installs", "strategy_probation_receipts",
                  "strategy_approval_requests", "strategy_approval_decisions")


def _snap(j, tables=VERSION_TABLES):
    return {t: [tuple(r) for r in j.query(f"SELECT * FROM {t} ORDER BY 1")]
            for t in tables}


def _row_state(j, sid):
    return j.query("SELECT state FROM strategies WHERE id=?", (sid,))[0]["state"]


@pytest.fixture
def world(tmp_path, cfg, monkeypatch):
    j, v, risk, _ = approved_world(tmp_path, cfg, monkeypatch)
    rid = established_capacity(j, cfg, v, monkeypatch)

    class W:
        pass
    w = W()
    w.j, w.v, w.risk, w.rid, w.cfg = j, v, risk, rid, cfg
    w.vid, w.sid = v["version_id"], v["strategy_id"]

    def act(**kw):
        return activate(j, cfg, w.vid, available_inputs=INPUTS,
                        capacity_receipt_id=rid, risk_manager=risk,
                        risk_release=risk.release_check(10000), **kw)

    def gov(state, reason="TEST-ONLY lifecycle", at=NOW, **kw):
        if state in ("REACTIVATED", "ACTIVE"):
            kw = {"allocation": .1, "available_inputs": INPUTS,
                  "capacity_receipt_id": rid, "risk_manager": risk,
                  "risk_release": risk.release_check(10000), **kw}
        return F.govern_version(j, cfg, w.vid, state,
                                actor="strategy_governor", reason_code=reason,
                                at_ms=at, **kw)
    w.act, w.gov = act, gov
    return w


# ── 1. one canonical lifecycle authority ─────────────────────────────────
def test_every_nonfactory_state_writer_is_legacy_only_and_guarded():
    """Static map of writers of strategies.state outside the Governor. Each
    is a legacy-row path; the database refuses them for a versioned id."""
    root = pathlib.Path(__file__).resolve().parents[1] / "trader"
    pat = re.compile(r"UPDATE strategies SET state")
    found = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                   if pat.search(p.read_text()))
    assert found == ["brain/strategist.py", "brain/tv.py", "core/journal.py",
                     "kernel.py", "strategy/factory_handoff.py",
                     "strategy/promotion.py"]


def test_database_refuses_every_ungoverned_write_to_a_versioned_strategy(world):
    j, sid, vid = world.j, world.sid, world.vid
    world.act()
    before = (_row_state(j, sid), len(F.governor_events(j, vid)))
    for sql, args in [
        ("UPDATE strategies SET state='retired' WHERE id=?", (sid,)),
        ("UPDATE strategies SET state='paper' WHERE id=?", (sid,)),
        ("DELETE FROM strategies WHERE id=?", (sid,)),
        ("INSERT INTO strategy_governor_events(version_id,to_state,"
         "canonical_json,canonical_sha256) VALUES(?,'RETIRED','{}','x')", (vid,)),
        ("INSERT INTO strategy_version_events(version_id,to_state,reason_code,"
         "actor,at_ms) VALUES(?,'RETIRED','bypass','kernel',1)", (vid,)),
    ]:
        with pytest.raises(sqlite3.IntegrityError,
                           match="STRATEGY_GOVERNOR_REQUIRED"):
            with j._tx() as c:
                c.execute(sql, args)
    with pytest.raises(sqlite3.IntegrityError, match="STRATEGY_GOVERNOR_REQUIRED"):
        j.upsert_spec(StrategySpec.from_dict(world.v["spec"]), state="retired")
    assert (_row_state(j, sid), len(F.governor_events(j, vid))) == before


# ── 2. exact version + approval binding ──────────────────────────────────
def test_each_event_binds_the_exact_artifact(world):
    j, v = world.j, world.v
    ev = world.act()["event"]
    req = F.approval_request(j, world.vid)
    rec = F.load_version(j, world.vid)
    assert ev["binding"] == {
        "strategy_id": v["strategy_id"], "lineage_sha256": rec["lineage_sha256"],
        "install_id": F._install_record(j, world.vid)["install_id"],
        "approval_request_id": req["request_id"]}
    assert ev["spec_hash"] == v["spec_hash"] and ev["from_state"] == \
        F.APPROVED_FIRST_LIVE
    dec = json.loads(j.query("SELECT canonical_json FROM "
                             "strategy_approval_decisions")[0]["canonical_json"])
    assert ev["owner_decision_id"] == dec["decision_id"]
    paused = world.gov("PAUSED", at=NOW + 1)["event"]
    assert paused["binding"] == ev["binding"]
    assert paused["previous_sha256"] == F._sha(F.canonical(ev))


def test_replay_refuses_a_rebound_or_rewritten_event(world):
    j = world.j
    world.act()
    row = j.query("SELECT * FROM strategy_governor_events")[0]
    ev = json.loads(row["canonical_json"])
    ev["binding"]["lineage_sha256"] = "0" * 64
    text = F.canonical(ev)
    with j._tx() as c:      # privileged corruption fixture
        c.execute("DROP TRIGGER strategy_governor_events_no_update")
        c.execute("UPDATE strategy_governor_events SET canonical_json=?, "
                  "canonical_sha256=?", (text, F._sha(text)))
    with pytest.raises(F.HandoffRefused) as e:
        F.governor_events(j, world.vid)
    assert e.value.code == "governor_binding_differs"
    assert F.live_entry_block(j, world.sid).startswith(
        "version_authority_unreadable")


def test_sibling_by_name_stale_and_copied_approval_cannot_activate(world):
    j = world.j
    spec = StrategySpec.from_dict(world.v["spec"])
    spec.exit.stop = {"kind": "atr", "mult": 4.0}
    sib = F.derive_version(j, world.vid, spec, at_ms=T0 + 32 * DAY)["version_id"]
    with pytest.raises(F.HandoffRefused):
        activate(j, world.cfg, sib, available_inputs=INPUTS,
                 capacity_receipt_id=world.rid, risk_manager=world.risk,
                 risk_release=world.risk.release_check(10000))
    assert F.state_of(j, sib) == F.PROPOSED
    # stale approval: research withdrawn after the owner's decision
    from trader.research.ledger import Ledger
    h = j.query("SELECT hash FROM research_candidates")[0]["hash"]
    Ledger(j).set_candidate(h, "4h", "fixed", "refused", reason="later")
    with pytest.raises(F.HandoffRefused, match="governor_evidence_unavailable"):
        world.act()
    assert not j.query("SELECT * FROM strategy_governor_events")
    assert _row_state(j, world.sid) == "paper"


def test_grandfather_and_label_cannot_activate_or_authorise(tmp_path, cfg):
    j, h = _journal(tmp_path)
    vid = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                           at_ms=T0 - DAY)["version_id"]
    rec = _install(j, vid)
    grant(j, rec["strategy_id"])
    j._local.governor_write = True       # privileged label fixture
    with j._tx() as c:
        c.execute("UPDATE strategies SET state='active' WHERE id=?",
                  (rec["strategy_id"],))
    j._local.governor_write = False
    assert not F.governor_events(j, vid)
    assert F.live_entry_block(j, rec["strategy_id"]).startswith(
        "version_not_live_authorized")
    with pytest.raises(F.HandoffRefused):
        F.govern_version(j, cfg, vid, "ACTIVE", actor="operator",
                         reason_code="x", at_ms=NOW, allocation=.1)


# ── 3. bounded state machine ─────────────────────────────────────────────
def test_registered_state_machine_bounds():
    A = F.GOVERNOR_ALLOWED
    assert A[F.RETIRED] == set()
    into_active = {s for s, to in A.items() if "ACTIVE" in to}
    assert into_active == {F.APPROVED_FIRST_LIVE, "ACTIVE", "REACTIVATED"}
    into_react = {s for s, to in A.items() if "REACTIVATED" in to}
    assert into_react == {"PAUSED", F.DEGRADED}
    for s in (F.PROPOSED, F.VALIDATED, F.SHADOW, F.APPROVAL_REQUIRED):
        assert A[s] == {F.DEGRADED, F.RETIRED}
    assert F.REJECTED not in A


@pytest.mark.parametrize("target", ["ACTIVE", "REACTIVATED", "PAUSED"])
def test_retired_is_terminal(world, target):
    world.act()
    world.gov(F.RETIRED, at=NOW + 1)
    with pytest.raises(F.HandoffRefused) as e:
        world.gov(target, at=NOW + 2)
    assert e.value.code == "governor_transition_not_allowed"
    assert F.state_of(world.j, world.vid) == F.RETIRED
    assert _row_state(world.j, world.sid) == "retired"


def test_unapproved_states_cannot_become_active(tmp_path, cfg):
    j, h = _journal(tmp_path)
    vid = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                           at_ms=T0 - DAY)["version_id"]
    import time
    for to in ("ACTIVE", "REACTIVATED", "PAUSED"):
        with pytest.raises(F.HandoffRefused) as e:
            F.govern_version(j, cfg, vid, to, actor="operator",
                             reason_code="x", at_ms=int(time.time() * 1000),
                             allocation=.1)
        if to == "PAUSED":
            assert e.value.code == "governor_transition_not_allowed"
    assert F.state_of(j, vid) == F.VALIDATED


def test_reactivation_needs_fresh_exact_evidence(world):
    world.act()
    world.gov("PAUSED", at=NOW + 1)
    world.gov(F.DEGRADED, at=NOW + 2)
    with world.j._tx() as c:       # a counted probation trade is rewritten
        c.execute("UPDATE trades SET realized_pnl=-50 WHERE id='t0'")
    with pytest.raises(F.HandoffRefused, match="governor_evidence_unavailable"):
        world.gov("REACTIVATED", at=NOW + 3)
    assert F.state_of(world.j, world.vid) == F.DEGRADED


def test_pause_degrade_retire_need_reason_clock_and_valid_actor(world):
    world.act()
    for kw, code in [
        (dict(reason="", at=NOW + 1), "governor_reason_or_clock_missing"),
        (dict(reason="x", at="late"), "governor_reason_or_clock_missing")]:
        with pytest.raises(F.HandoffRefused) as e:
            world.gov("PAUSED", **kw)
        assert e.value.code == code
    with pytest.raises(F.HandoffRefused) as e:
        F.govern_version(world.j, world.cfg, world.vid, "PAUSED",
                         actor="analyst", reason_code="x", at_ms=NOW + 1)
    assert e.value.code == "governor_actor_invalid"
    with pytest.raises(F.HandoffRefused) as e:        # clock may not regress
        world.gov("PAUSED", at=NOW - 5)
    assert e.value.code == "governor_clock_regressed"
    assert F.state_of(world.j, world.vid) == "ACTIVE"


# ── 4. atomicity ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("trap", ["strategies_update", "governor_insert"])
def test_failure_at_a_transition_boundary_leaves_nothing_partial(world, trap):
    j = world.j
    world.act()
    snap = (len(F.governor_events(j, world.vid)), _row_state(j, world.sid),
            F.state_of(j, world.vid))
    with j._tx() as c:
        if trap == "strategies_update":
            c.execute("CREATE TRIGGER boom BEFORE UPDATE ON strategies "
                      "BEGIN SELECT RAISE(ABORT,'injected'); END")
        else:
            c.execute("CREATE TRIGGER boom AFTER INSERT ON "
                      "strategy_governor_events BEGIN SELECT "
                      "RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        world.gov("PAUSED", at=NOW + 1)
    assert (len(F.governor_events(j, world.vid)), _row_state(j, world.sid),
            F.state_of(j, world.vid)) == snap
    with j._tx() as c:
        c.execute("DROP TRIGGER boom")
    assert F.projection_gap(j, world.vid) is None
    world.gov("PAUSED", at=NOW + 1)              # and it still works after
    assert F.state_of(j, world.vid) == "PAUSED"
    assert _row_state(j, world.sid) == "paper"


def test_row_always_projects_the_governor_state(world):
    j = world.j
    assert F.projection_gap(j, world.vid) is None        # no event yet
    for state, row in [("ACTIVE", "active"), ("PAUSED", "paper"),
                       ("REACTIVATED", "active"), (F.DEGRADED, "paper"),
                       (F.RETIRED, "retired")]:
        if state == "ACTIVE":
            world.act()
        else:
            world.gov(state, at=NOW + len(F.governor_events(j, world.vid)))
        assert _row_state(j, world.sid) == row == F.PROJECTION[state]
        assert F.projection_gap(j, world.vid) is None


def test_divergent_row_is_not_authority(world):
    j = world.j
    world.act()
    j._local.governor_write = True       # privileged corruption fixture
    with j._tx() as c:
        c.execute("UPDATE strategies SET state='retired' WHERE id=?", (world.sid,))
    j._local.governor_write = False
    assert F.projection_gap(j, world.vid) == "version_projection_diverged"
    assert F.live_entry_block(j, world.sid) is not None


# ── 5. idempotency ───────────────────────────────────────────────────────
def test_retry_of_the_same_transition_is_a_duplicate(world):
    j = world.j
    first = world.act()
    again = world.act()
    assert again["status"] == "duplicate"
    assert again["event"] == first["event"]
    p1 = world.gov("PAUSED", at=NOW + 1)
    p2 = world.gov("PAUSED", at=NOW + 1)
    assert (p1["status"], p2["status"]) == ("inserted", "duplicate")
    assert p2["event"]["transition_id"] == p1["event"]["transition_id"]
    assert len(F.governor_events(j, world.vid)) == 2


def test_same_key_different_transition_is_refused_and_same_key_replays(world):
    j = world.j
    world.act()
    a = world.gov("PAUSED", at=NOW + 1, transition_key="op-1")
    with pytest.raises(F.HandoffRefused) as e:
        world.gov(F.DEGRADED, at=NOW + 2, transition_key="op-1")
    assert e.value.code == "governor_replay_key_conflict"
    world.gov(F.DEGRADED, at=NOW + 3)            # history moves on
    b = world.gov("PAUSED", at=NOW + 1, transition_key="op-1")
    assert b["status"] == "duplicate" and b["event"] == a["event"]
    assert [e["to_state"] for e in F.governor_events(j, world.vid)] == [
        "ACTIVE", "PAUSED", F.DEGRADED]


def test_restart_replays_the_same_history_and_outcome(world, tmp_path):
    j = world.j
    world.act()
    world.gov("PAUSED", at=NOW + 1)
    events = F.governor_events(j, world.vid)
    again = Journal(j.db_path)
    assert F.governor_events(again, world.vid) == events
    assert F.state_of(again, world.vid) == "PAUSED"
    assert F.lifecycle_target(again, world.vid) == F.lifecycle_target(
        j, world.vid)
    r = F.retire_version(again, world.vid, F.RETIRED, reason_code="r",
                         actor="strategy_governor", at_ms=NOW + 2)
    assert F.retire_version(again, world.vid, F.RETIRED, reason_code="r",
                           actor="strategy_governor",
                           at_ms=NOW + 2)["status"] == "duplicate"
    assert r["status"] == "inserted"


# ── 6/7. owner limits and immutability of the strategy itself ───────────
def test_activation_stays_inside_the_owner_ceiling_and_risk(world):
    world.act()                                   # ceiling = .1
    with pytest.raises(F.HandoffRefused) as e:
        world.gov("REACTIVATED", at=NOW + 5)      # ACTIVE -> REACTIVATED
    assert e.value.code == "governor_transition_not_allowed"
    world.gov("PAUSED", at=NOW + 1)
    with pytest.raises(F.HandoffRefused) as e:
        world.gov("REACTIVATED", at=NOW + 2, allocation=.5)
    assert e.value.code == "governor_owner_boundaries_changed"
    changed = {**world.cfg, "risk": {**world.cfg["risk"],
                                     "max_open_positions": 99}}
    with pytest.raises(F.HandoffRefused):
        F.govern_version(world.j, changed, world.vid, "REACTIVATED",
                         actor="strategy_governor", reason_code="r",
                         at_ms=NOW + 3, allocation=.1,
                         available_inputs=INPUTS,
                         capacity_receipt_id=world.rid,
                         risk_manager=world.risk,
                         risk_release=world.risk.release_check(10000))
    assert F.state_of(world.j, world.vid) == "PAUSED"


def test_governor_never_rewrites_the_version_or_its_evidence(world):
    j = world.j
    before = _snap(j)
    world.act()
    world.gov("PAUSED", at=NOW + 1)
    world.gov("REACTIVATED", at=NOW + 2)
    world.gov(F.DEGRADED, at=NOW + 3)
    world.gov(F.RETIRED, at=NOW + 4)
    assert _snap(j) == before        # spec, lineage, evidence, approval, install
    for t in ("strategy_versions", "strategy_approval_decisions"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            with j._tx() as c:
                c.execute(f"UPDATE {t} SET canonical_json='{{}}'")
    import inspect
    params = inspect.signature(F.govern_version).parameters
    assert not {"spec", "lineage", "evidence"} & set(params)


def test_changed_semantics_need_a_new_version_not_a_state_edit(world):
    j = world.j
    world.act()
    spec = StrategySpec.from_dict(world.v["spec"])
    spec.entry_long = "close > 1e9"
    j._local.governor_write = True       # same-id row rewrite (privileged)
    j.upsert_spec(spec, state="active", origin="research")
    j._local.governor_write = False
    assert F.live_entry_block(j, world.sid) == "version_install_unbound"
    with pytest.raises(F.HandoffRefused):
        F.verify_install(j, F.load_version(j, world.vid))
    new = F.derive_version(j, world.vid, spec, at_ms=T0 + 33 * DAY)
    assert new["version_id"] != world.vid and new["spec_hash"] != \
        world.v["spec_hash"]


# ── 9. decay / recovery boundary ─────────────────────────────────────────
def test_loss_streaks_no_trades_and_noise_do_not_move_lifecycle(world):
    from trader.strategy import promotion
    j = world.j
    world.act()
    with j._tx() as c:
        for i in range(20):                  # a long loss streak
            c.execute("INSERT INTO trades(id,symbol,side,amount,entry_price,"
                      "strategy_id,status,realized_pnl,opened_at,closed_at) "
                      "VALUES(?,'BTC/USDT','long',1,100,?,'closed',-10,"
                      "'2026-10-01','2026-10-02')", (f"loss{i}", world.sid))
    n = len(F.governor_events(j, world.vid))
    assert promotion.evaluate_population(j) == []
    assert F.state_of(j, world.vid) == "ACTIVE"
    assert _row_state(j, world.sid) == "active"
    assert len(F.governor_events(j, world.vid)) == n
    # analyst/decision noise and idle time have no writer into lifecycle
    from trader.core.types import Action, Decision
    with j._tx() as c:
        c.execute("INSERT INTO cycles(id,ts,symbol) VALUES('c','2026-10-06','BTC/USDT')")
    j.log_decision(Decision("d1", "c", "BTC/USDT", Action.HOLD, .1, .2, .1, [], []))
    assert F.state_of(j, world.vid) == "ACTIVE"


# ── 10. ACTIVE is necessary, not sufficient ──────────────────────────────
def test_active_state_grants_no_order_permission(world):
    world.act()
    assert F.live_entry_block(world.j, world.sid) == \
        "version_first_live_execution_not_enabled"
    world.gov("PAUSED", at=NOW + 1)
    assert F.live_entry_block(world.j, world.sid).startswith("version_not_live")
    world.gov(F.RETIRED, at=NOW + 2)
    assert F.live_entry_block(world.j, world.sid) is not None


# ── 11. history ──────────────────────────────────────────────────────────
def test_history_is_append_only_and_reconstructable(world):
    j = world.j
    states = ["ACTIVE", "PAUSED", "REACTIVATED", F.DEGRADED, F.RETIRED]
    rows_seen = []
    for i, s in enumerate(states):
        world.act() if s == "ACTIVE" else world.gov(s, at=NOW + i)
        rows_seen.append([tuple(r) for r in j.query(
            "SELECT * FROM strategy_governor_events ORDER BY seq")])
    for earlier, later in zip(rows_seen, rows_seen[1:]):
        assert later[:len(earlier)] == earlier       # nothing rewritten
    ev = F.governor_events(j, world.vid)
    assert [e["to_state"] for e in ev] == states
    assert [e["from_state"] for e in ev] == [F.APPROVED_FIRST_LIVE] + states[:-1]
    for t in ("UPDATE strategy_governor_events SET to_state='ACTIVE'",
              "DELETE FROM strategy_governor_events"):
        with pytest.raises(sqlite3.IntegrityError):
            with j._tx() as c:
                c.execute(t)
    # the owner's approval stays inspectable after retirement
    assert F.approval_request(j, world.vid)
    assert j.query("SELECT * FROM strategy_approval_decisions")
