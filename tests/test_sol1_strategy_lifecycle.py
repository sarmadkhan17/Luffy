"""One strategy lifecycle, proven offline and TEST-ONLY:

    CREATE -> BACKTEST PASS -> 10 PAPER TRADES -> OWNER APPROVAL + LIVE SAFETY
    -> BOUNDED LIVE ELIGIBILITY

No venue, provider, Referee run, protected held-out read, error-budget spend or
production database. Cost sources are the TEST-ONLY authorities the existing
Factory fixtures register; production has no commission/slippage source adapter
(see the lifecycle report), which these tests do not pretend otherwise.
"""
import copy
import time

import pytest

from trader.core.journal import Journal
from trader.strategy import factory_handoff as F
from trader.strategy import qualification as Q
from trader.strategy.spec import ExitSpec, StrategySpec
from tests.authority_factory_fixtures import (  # noqa: F401  (cfg is a fixture)
    DAY, INPUTS, T0, _identity, _install, _trades, cfg)

TF = "4h"
SCORED = ("BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "SUI/USDT")
RUN = {"contract": "quant-contract.v1", "spec_digest": "a" * 64,
       "data_digest": "b" * 64, "config_digest": "c" * 64,
       "engine_digest": "d" * 64, "identity": "e" * 64}


def _spec(sid="spec_probe_trend", include=("BTC/USDT", "ETH/USDT"), **kw):
    base = dict(
        id=sid, name="Probe Trend",
        thesis="TEST-ONLY mechanism: persistent trends in liquid perpetuals "
               "continue because slow participants reprice late, and this "
               "sentence is long enough for the validator.",
        invalidation="TEST-ONLY: retire when the recent pooled profit factor "
                     "falls below the floor.",
        provenance={"source_kind": "test"},
        universe={"include": list(include), "exclude": [],
                  "min_volume_usdt": 100_000_000},
        timeframe=TF, direction="long", entry_long="close > ema(20)",
        entry_short="", filters=[],
        exit=ExitSpec(stop={"kind": "atr", "mult": 2.0},
                      target={"kind": "rr", "v": 2.0},
                      trail={"kind": "none"}, time={"max_bars": 24}),
        regime_filter=["RANGING"], markets=["futures"])
    base.update(kw)
    return StrategySpec(**base)


def _ev(trades=40, pf=1.6, p=0.002, overlap=0.1, symbols=SCORED):
    each = trades // len(symbols)
    per = {s: {"trades": each, "pf": pf, "pnl": 10.0, "wr": 0.5}
           for s in symbols}
    return {"recent": {"recent_days": 90, "window_days": 90,
                       "trades": each * len(symbols), "pooled_pf": pf,
                       "winrate": 0.5, "pnl": 10.0 * len(symbols),
                       "per_symbol": per},
            "null_consistency_p": p, "null_symbols": len(symbols),
            "null_dependence": {"common_rotation": {"p": p}},
            "redundancy": {"max_overlap": overlap, "twin": ""}}


def _qualification(spec, cfg, **kw):
    """What `Analyst.qualify` builds for `spec`, from stated TEST-ONLY numbers."""
    probe = StrategySpec.from_dict({**spec.to_dict(), "timeframe": TF})
    return Q.build(
        spec_id=spec.id, timeframe=TF,
        evaluated_spec_sha256=F._frozen_spec(probe)[1],
        declared_universe=probe.universe,
        declared_regime_filter=list(probe.regime_filter),
        exit_semantics_id=_exit_id(), ev=_ev(**kw), th=Q.thresholds(cfg),
        run=RUN)


def _exit_id():
    from trader.strategy.exit_policy import EXIT_SEMANTICS_ID
    return EXIT_SEMANTICS_ID


def _frozen(spec):
    """What the Kernel freezes: the evaluated spec, regime guess dropped."""
    out = copy.deepcopy(spec)
    out.regime_filter = []
    return out


def _source(spec, q):
    return {"kind": Q.SOURCE_KIND, "spec": _frozen(spec).to_dict(),
            "qualification": q}


def _journal(tmp_path):
    j = Journal(tmp_path / "j.db")
    F.ensure(j)
    return j


def _validated(j, cfg, spec=None, **kw):
    spec = spec or _spec()
    q = _qualification(spec, cfg, **kw)
    return F.create_version(j, cfg, _source(spec, q), at_ms=T0 - DAY), q


def _reseal(rec):
    rec = copy.deepcopy(rec)
    rec.pop("qualification_id")
    rec["qualification_id"] = Q.digest(rec)
    return rec


# ── 1. the Backtest PASS is re-derived, never flagged ───────────────────
def _mutate(path, value):
    def apply(rec):
        node = rec
        for k in path[:-1]:
            node = node[k]
        node[path[-1]] = value
        return rec
    return apply


FORGERIES = [
    ("insufficient_trades", _mutate(("recent", "trades"), 4)),
    ("below_floor", _mutate(("recent", "pooled_pf"), 1.0)),
    ("rotation_not_rejected", _mutate(("robustness", "consistency_p_dep"), .5)),
    ("rotation_unavailable", _mutate(("robustness", "common_rotation_p"), None)),
    ("overlap", _mutate(("redundancy", "max_overlap"), .9)),
    ("not_pass", _mutate(("verdict",), "FAIL")),
    ("wrong_scope", _mutate(("scope",), "LIVE")),
    ("run_identity_missing", _mutate(("run", "identity"), None)),
    ("no_symbols", _mutate(("recent", "per_symbol"), {})),
]


@pytest.mark.parametrize("name,forge", FORGERIES, ids=[f[0] for f in FORGERIES])
def test_a_resealed_forgery_is_rederived_and_refused(cfg, name, forge):
    good = _qualification(_spec(), cfg)
    assert Q.verify(good) is good
    with pytest.raises(Q.QualificationRefused):
        Q.verify(_reseal(forge(copy.deepcopy(good))))


def test_a_tampered_record_without_reseal_is_refused(cfg):
    q = _qualification(_spec(), cfg)
    q["recent"]["pooled_pf"] = 9.0
    with pytest.raises(Q.QualificationRefused, match="id_mismatch"):
        Q.verify(q)


def test_pass_numbers_must_reconcile(cfg):
    q = copy.deepcopy(_qualification(_spec(), cfg))
    q["recent"]["trades"] += 1
    with pytest.raises(Q.QualificationRefused, match="reconcile"):
        Q.verify(_reseal(q))


def test_a_lenient_recorded_threshold_is_not_a_pass(tmp_path, cfg):
    spec = _spec()
    q = _reseal({**_qualification(spec, cfg),
                 "thresholds": {**Q.thresholds(cfg), "min_pf": 0.5}})
    q = _reseal({**q, "recent": {**q["recent"], "pooled_pf": 0.9}})
    assert Q.verify(q)                      # internally consistent ...
    with pytest.raises(F.HandoffRefused,
                       match="thresholds_weaker_than_configured"):
        F.create_version(_journal(tmp_path), cfg, _source(spec, q), at_ms=T0)


def test_the_record_is_deterministic(cfg):
    spec = _spec()
    assert _qualification(spec, cfg) == _qualification(spec, cfg)


# ── 2. Backtest PASS -> exact immutable version -> exact paper install ──
def test_valid_strategy_backtest_pass_to_exact_paper_install(tmp_path, cfg):
    j = _journal(tmp_path)
    spec = _spec()
    out, q = _validated(j, cfg, spec)
    vid = out["version_id"]
    assert F.state_of(j, vid) == F.VALIDATED
    v = F.load_version(j, vid)
    assert v["source"] == {"kind": Q.SOURCE_KIND, "id": q["qualification_id"]}
    assert v["spec"]["regime_filter"] == []        # the guess was never tested
    assert v["spec"]["universe"]["include"] == ["BTC/USDT", "ETH/USDT"]
    assert F.verify_validation(j, v)["qualification"] == q
    # idempotent
    again = F.create_version(j, cfg, _source(spec, q), at_ms=T0 + 1)
    assert again["status"] == "duplicate" and again["version_id"] == vid
    # exact paper install starts probation
    _install(j, vid)
    assert F.state_of(j, vid) == F.SHADOW
    inst = F.verify_install(j, v)
    assert inst["spec_hash"] == v["spec_hash"] and inst["mode"] == "paper"
    # no live authority from a Backtest PASS
    assert F.live_entry_block(j, v["strategy_id"], cfg=cfg) \
        == f"version_not_live_authorized:{F.SHADOW}"


def test_declared_universe_must_have_been_scored(tmp_path, cfg):
    spec = _spec(include=("BTC/USDT", "DOGE/USDT"))
    with pytest.raises(F.HandoffRefused, match="universe_not_evidenced"):
        _validated(_journal(tmp_path), cfg, spec)


def test_an_undeclared_universe_freezes_to_the_scored_markets(tmp_path, cfg):
    spec = _spec(include=())
    j = _journal(tmp_path)
    frozen = _frozen(spec)
    frozen.universe = {**frozen.universe, "include": sorted(SCORED)}
    out = F.create_version(j, cfg, {"kind": Q.SOURCE_KIND,
                                    "spec": frozen.to_dict(),
                                    "qualification": _qualification(spec, cfg)},
                           at_ms=T0)
    assert F.load_version(j, out["version_id"])["spec"]["universe"][
        "include"] == sorted(SCORED)
    frozen.universe = {**frozen.universe, "include": ["BTC/USDT"]}
    with pytest.raises(F.HandoffRefused, match="universe_not_evidenced"):
        F.create_version(j, cfg, {"kind": Q.SOURCE_KIND,
                                  "spec": frozen.to_dict(),
                                  "qualification": _qualification(spec, cfg)},
                         at_ms=T0)


def _spec_with(spec, **changes):
    d = _frozen(spec).to_dict()
    for k, v in changes.items():
        d[k] = v
    return d


REFUSED_SPECS = [
    ("different_rule", dict(entry_long="close > ema(50)"),
     "qualified_spec_not_evaluated_spec"),
    ("regime_guess_kept", dict(regime_filter=["RANGING"]),
     "regime_filter_unmeasured"),
    ("other_timeframe", dict(timeframe="1h"), "qualified_timeframe_differs"),
    ("trailing_exit", dict(exit={**ExitSpec(
        trail={"kind": "atr", "mult": 3.0, "arm_at_r": 1.0}).__dict__}),
     "unsupported_versioned_exit"),
    ("signal_exit", dict(exit={**ExitSpec(signal_exit="close < ema(5)"
                                          ).__dict__}),
     "unsupported_versioned_exit"),
]


@pytest.mark.parametrize("name,changes,code", REFUSED_SPECS,
                         ids=[r[0] for r in REFUSED_SPECS])
def test_a_pass_cannot_be_attached_to_another_strategy(tmp_path, cfg, name,
                                                       changes, code):
    spec = _spec()
    q = _qualification(spec, cfg)
    with pytest.raises(F.HandoffRefused, match=code):
        F.create_version(_journal(tmp_path), cfg,
                         {"kind": Q.SOURCE_KIND,
                          "spec": _spec_with(spec, **changes),
                          "qualification": q}, at_ms=T0)


def test_stored_validation_cannot_be_swapped_after_the_fact(tmp_path, cfg):
    j = _journal(tmp_path)
    out, _q = _validated(j, cfg)
    v = F.load_version(j, out["version_id"])
    with j._tx() as db:
        db.execute("DROP TRIGGER strategy_validation_receipts_no_update")
        db.execute("UPDATE strategy_validation_receipts SET "
                   "canonical_sha256='corrupt'")
    with pytest.raises(F.HandoffRefused):
        F.verify_validation(j, v)


# ── 3. edits and unqualified strategies ─────────────────────────────────
def test_an_edited_strategy_inherits_nothing(tmp_path, cfg):
    j = _journal(tmp_path)
    spec = _spec()
    out, _q = _validated(j, cfg, spec)
    vid = out["version_id"]
    rec = _install(j, vid)
    _trades(j, rec["strategy_id"], [10.0] * 10, T0, spec_hash=rec["spec_hash"])
    edited = StrategySpec.from_dict(rec["spec"])
    edited.entry_long = "close > ema(30)"
    child = F.derive_version(j, vid, edited, at_ms=T0 + DAY, cfg=cfg)
    cid = child["version_id"]
    assert F.state_of(j, cid) == F.PROPOSED
    with pytest.raises(F.HandoffRefused):
        F.verify_validation(j, F.load_version(j, cid))
    with pytest.raises(F.HandoffRefused):
        F.record_exact_install(j, cid, at_ms=T0 + DAY)
    with pytest.raises(F.HandoffRefused):
        F.evaluate_probation(j, cfg, cid, at_ms=T0 + 30 * DAY)
    assert F.approval_request(j, cid) is None
    assert F.live_entry_block(j, rec["strategy_id"], cfg=cfg) \
        == f"version_not_live_authorized:{F.SHADOW}"


def test_no_unqualified_strategy_reaches_paper_or_live(tmp_path, cfg):
    """A spec nobody qualified has no version: the install, probation and
    live fences all refuse it, whatever its row says."""
    j = _journal(tmp_path)
    spec = _frozen(_spec())
    j.upsert_spec(spec, state="paper")
    assert not F.versioned(j, spec.id)
    assert F.live_entry_block(j, spec.id, cfg=cfg) \
        == "VERSIONED_AUTHORITY_REQUIRED"
    with pytest.raises(F.HandoffRefused):
        F.create_version(j, cfg, {"kind": Q.SOURCE_KIND, "spec": spec.to_dict(),
                                  "qualification": {"verdict": "PASS"}},
                         at_ms=T0)
    with pytest.raises(F.HandoffRefused):
        F.record_exact_install(j, "0" * 64, at_ms=T0)
    assert not F.versioned(j, spec.id)


# ── 4. paper probation: ten attributable trades AND the configured criteria
def _probation(tmp_path, cfg, pnls, **trade_kw):
    j = _journal(tmp_path)
    out, _q = _validated(j, cfg)
    rec = _install(j, out["version_id"])
    _trades(j, rec["strategy_id"], pnls, T0, spec_hash=rec["spec_hash"],
            **trade_kw)
    return j, out["version_id"], rec


def _evaluate(j, cfg, vid):
    return F.evaluate_probation(j, cfg, vid, at_ms=T0 + 30 * DAY)


def test_the_policy_is_ten_trades(cfg):
    assert F._policy(cfg)["min_trades"] == 10


def test_nine_qualifying_trades_are_insufficient(tmp_path, cfg):
    j, vid, _ = _probation(tmp_path, cfg, [10.0] * 6 + [-5.0] * 3)
    p = _evaluate(j, cfg, vid)
    assert p["status"] == F.P_INSUFFICIENT and p["request_id"] is None
    assert F.state_of(j, vid) == F.SHADOW
    assert F.approval_request(j, vid) is None


def test_ten_trades_meeting_every_criterion_pass(tmp_path, cfg):
    j, vid, rec = _probation(tmp_path, cfg, [10.0] * 7 + [-5.0] * 3)
    p = _evaluate(j, cfg, vid)
    assert p["status"] == F.P_SATISFIED and p["request_id"]
    assert F.state_of(j, vid) == F.APPROVAL_REQUIRED
    req = F.approval_request(j, vid)
    assert req["request_id"] == p["request_id"]
    # the request is exact: this version, its install, both receipts
    assert req["version_id"] == vid and req["spec_hash"] == rec["spec_hash"]
    assert req["scope"]["applies_to_descendants"] is False
    assert req["scope"]["applies_by_name"] is False
    assert req["validation_receipt_id"] and req["probation_receipt_id"]
    F.verify_request(j, F.load_version(j, vid), req)
    # a retry is the same request
    assert _evaluate(j, cfg, vid)["result"] == "duplicate"


FAILING = [
    # win rate 0.3 < 0.40 although net profit factor is high
    ("win_rate", [30.0] * 3 + [-5.0] * 7),
    # win rate fine, net profit factor 0.75 < 1.15
    ("net_profitability", [5.0] * 6 + [-10.0] * 4),
]


@pytest.mark.parametrize("name,pnls", FAILING, ids=[f[0] for f in FAILING])
def test_ten_trades_alone_never_pass(tmp_path, cfg, name, pnls):
    j, vid, _ = _probation(tmp_path, cfg, pnls)
    p = _evaluate(j, cfg, vid)
    assert p["status"] == F.P_NOT_SATISFIED and p["request_id"] is None
    assert F.state_of(j, vid) == F.SHADOW
    assert F.approval_request(j, vid) is None


def test_ten_trades_without_cost_evidence_are_refused(tmp_path, cfg,
                                                      monkeypatch):
    j, vid, _ = _probation(tmp_path, cfg, [10.0] * 7 + [-5.0] * 3)
    monkeypatch.setattr(F, "_paper_cost_evidence", lambda *a: {
        "net_pnl_status": "UNAVAILABLE",
        "reason": "COST_RECEIPT_MISSING_OR_INVALID"})
    p = _evaluate(j, cfg, vid)
    assert p["status"] == F.P_COST_INCOMPLETE and p["request_id"] is None
    assert F.state_of(j, vid) == F.SHADOW


def test_a_trade_of_another_version_makes_probation_incomplete(tmp_path, cfg):
    j, vid, rec = _probation(tmp_path, cfg, [10.0] * 7 + [-5.0] * 3)
    _trades(j, rec["strategy_id"], [10.0], T0, prefix="other",
            spec_hash="f" * 64)
    p = _evaluate(j, cfg, vid)
    assert p["status"] == F.P_INCOMPLETE and p["request_id"] is None


@pytest.mark.parametrize("identity", [
    None, "not json", _identity("x", "a" * 64, status="AMBIGUOUS")])
def test_ten_trades_with_unproven_identity_are_refused(tmp_path, cfg, identity):
    j, vid, rec = _probation(tmp_path, cfg, [10.0] * 6 + [-5.0] * 3)
    _trades(j, rec["strategy_id"], [10.0], T0, prefix="u", identity=identity)
    p = _evaluate(j, cfg, vid)
    assert p["status"] == F.P_INCOMPLETE and p["request_id"] is None
    assert F.state_of(j, vid) == F.SHADOW


def test_trades_before_the_exact_install_do_not_count(tmp_path, cfg):
    j = _journal(tmp_path)
    out, _q = _validated(j, cfg)
    rec = _install(j, out["version_id"])
    _trades(j, rec["strategy_id"], [10.0] * 10, T0 - 40 * DAY, prefix="old",
            spec_hash=rec["spec_hash"])
    p = _evaluate(j, cfg, out["version_id"])
    assert p["status"] == F.P_INSUFFICIENT


def test_no_elapsed_time_wait_blocks_a_qualified_strategy(tmp_path, cfg):
    """Probation is trades, not days: ten qualifying trades booked on the
    install's first day already pass; the evaluation clock is only the
    evidence cut, not a waiting period."""
    j = _journal(tmp_path)
    out, _q = _validated(j, cfg)
    vid = out["version_id"]
    rec = _install(j, vid)
    _trades(j, rec["strategy_id"], [10.0] * 7 + [-5.0] * 3, T0,
            spec_hash=rec["spec_hash"])
    p = F.evaluate_probation(j, cfg, vid, at_ms=T0 + 15 * 3_600_000)
    assert p["status"] == F.P_SATISFIED
    assert F.state_of(j, vid) == F.APPROVAL_REQUIRED


# ── 5. owner approval, then bounded live eligibility (isolated simulation) ─
from trader.core.types import Action, Decision, MarketType  # noqa: E402
from trader.engine.executor import Executor  # noqa: E402
from trader.engine.risk import RiskManager  # noqa: E402
from tests.authority_capacity_fixtures import NOW  # noqa: E402
from tests.test_entry_recovery import Venue  # noqa: E402
from tests.test_universal_strategy_authority import (  # noqa: E402
    activate, established_capacity)

OWNER = "operator"


def _approved(j, cfg, spec=None):
    out, _q = _validated(j, cfg, spec)
    vid = out["version_id"]
    rec = _install(j, vid)
    _trades(j, rec["strategy_id"], [10.0] * 7 + [-5.0] * 3, T0,
            spec_hash=rec["spec_hash"])
    p = F.evaluate_probation(j, cfg, vid, at_ms=T0 + 30 * DAY)
    assert p["status"] == F.P_SATISFIED
    d = F.record_owner_decision(j, cfg, p["request_id"], "APPROVED",
                                actor=OWNER, decided_at_ms=T0 + 31 * DAY)
    return F.load_version(j, vid), p, d


def test_successful_probation_asks_the_owner_for_exactly_this_version(
        tmp_path, cfg):
    j = _journal(tmp_path)
    v, p, d = _approved(j, cfg)
    assert F.state_of(j, v["version_id"]) == F.APPROVED_FIRST_LIVE
    req = F.approval_request(j, v["version_id"])
    assert (req["version_id"], req["install_id"], req["scope"]["kind"]) == (
        v["version_id"], F.verify_install(j, v)["install_id"],
        "FIRST_LIVE_EXACT_VERSION")
    # the owner decision is bound to the request; a stranger cannot decide
    j2 = _journal(tmp_path / "x") if (tmp_path / "x").mkdir() is None else None
    out, _q = _validated(j2, cfg)
    rec = _install(j2, out["version_id"])
    _trades(j2, rec["strategy_id"], [10.0] * 7 + [-5.0] * 3, T0,
            spec_hash=rec["spec_hash"])
    q = F.evaluate_probation(j2, cfg, out["version_id"], at_ms=T0 + DAY)
    with pytest.raises(F.HandoffRefused, match="actor_not_owner"):
        F.record_owner_decision(j2, cfg, q["request_id"], "APPROVED",
                                actor="strategy_governor", decided_at_ms=T0 + 2 * DAY)
    assert F.state_of(j2, out["version_id"]) == F.APPROVAL_REQUIRED


def _world(tmp_path, cfg, monkeypatch):
    j = _journal(tmp_path)
    v, p, d = _approved(j, cfg)
    monkeypatch.setattr(time, "time", lambda: NOW / 1000)
    j.kv_set("control_state", "ACTIVE")
    risk = RiskManager(cfg, j)
    risk.update_equity(10000)
    risk.release_check(10000)
    return j, v, risk


def _activated(tmp_path, cfg, monkeypatch):
    j, v, risk = _world(tmp_path, cfg, monkeypatch)
    rid = established_capacity(j, cfg, v, monkeypatch)
    activate(j, cfg, v["version_id"], available_inputs=INPUTS,
             capacity_receipt_id=rid, risk_manager=risk,
             risk_release=risk.release_check(10000))
    return j, v, risk, rid


def test_activation_alone_is_not_live_and_approval_is_not_deployment(
        tmp_path, cfg, monkeypatch):
    j, v, _risk, _rid = _activated(tmp_path, cfg, monkeypatch)
    sid = v["strategy_id"]
    assert F.state_of(j, v["version_id"]) == "ACTIVE"
    # no owner deployment authorization has been recorded: real execution off
    assert F.live_entry_block(j, sid, cfg=cfg) \
        == "version_first_live_execution_not_enabled"
    assert F.live_entry_block(j, sid) == "version_first_live_execution_not_enabled"
    venue = Venue()
    ex = Executor(venue, j, cfg, MarketType.FUTURES)
    d = Decision("d", "c", "BTC/USDT", Action.BUY, 1, .5, .8, [], [])
    assert ex.open(d, 2, 2, 95, 110, sid, "version") is None
    assert "version fence" in d.skip_reason and venue.sent == []


def test_only_the_owner_can_authorize_deployment(tmp_path, cfg, monkeypatch):
    j, v, _risk, _rid = _activated(tmp_path, cfg, monkeypatch)
    vid = v["version_id"]
    for who in ("strategy_governor", "factory", "kernel"):
        with pytest.raises(F.HandoffRefused, match="actor_not_owner"):
            F.record_first_live_deployment(j, cfg, vid, actor=who, at_ms=NOW)
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match="AUTHORITY_REQUIRED"):
        with j._tx() as db:                     # raw SQL cannot mint it
            db.execute("INSERT INTO strategy_first_live_deployments VALUES"
                       "(?, '{}', 'x', 1)", (vid,))
    assert F.live_entry_block(j, v["strategy_id"], cfg=cfg) \
        == "version_first_live_execution_not_enabled"


def test_an_unapproved_version_cannot_be_deployed(tmp_path, cfg):
    j = _journal(tmp_path)
    out, _q = _validated(j, cfg)
    with pytest.raises(F.HandoffRefused, match="approval_request_missing"):
        F.record_first_live_deployment(j, cfg, out["version_id"],
                                       actor=OWNER, at_ms=T0)


def _deploy(j, cfg, v):
    return F.record_first_live_deployment(j, cfg, v["version_id"], actor=OWNER,
                                          at_ms=NOW)


def test_approved_version_with_complete_evidence_is_live_eligible(
        tmp_path, cfg, monkeypatch):
    j, v, _risk, _rid = _activated(tmp_path, cfg, monkeypatch)
    assert _deploy(j, cfg, v)["status"] == "inserted"
    assert _deploy(j, cfg, v)["status"] == "duplicate"
    # the fence opens; Risk, EntryAuthority and Execution still decide
    assert F.live_entry_block(j, v["strategy_id"], cfg=cfg) is None
    venue = Venue()
    ex = Executor(venue, j, cfg, MarketType.FUTURES)
    d = Decision("d", "c", "BTC/USDT", Action.BUY, 1, .5, .8, [], [])
    assert ex.open(d, 2, 2, 95, 110, v["strategy_id"], "version") is None
    assert "version fence" not in (d.skip_reason or "")     # held by Risk, not the fence
    assert venue.sent == []                                  # no order without Risk permission


HOLDS = ["frozen", "halted", "stale_capacity", "capacity_missing_receipt",
         "risk_config_changed", "install_changed", "validation_corrupt",
         "probation_corrupt", "owner_approval_corrupt", "paused", "retired",
         "deployment_for_other_decision"]


@pytest.mark.parametrize("hold", HOLDS)
def test_each_missing_or_stale_prerequisite_is_a_hold(tmp_path, cfg,
                                                      monkeypatch, hold):
    j, v, risk, rid = _activated(tmp_path, cfg, monkeypatch)
    _deploy(j, cfg, v)
    sid, vid = v["strategy_id"], v["version_id"]
    assert F.live_entry_block(j, sid, cfg=cfg) is None
    if hold == "frozen":
        j.kv_set("control_state", "FROZEN")
    if hold == "halted":
        j.kv_set("control_state", "HALTED")
    if hold == "stale_capacity":
        monkeypatch.setattr(time, "time", lambda: NOW / 1000 + 3 * 86400)
    if hold == "capacity_missing_receipt":
        with j._tx() as db:
            db.execute("DROP TRIGGER IF EXISTS strategy_governor_events_no_update")
            db.execute("UPDATE strategy_governor_events SET canonical_json="
                       "replace(canonical_json, ?, 'missing') ", (rid,))
    if hold == "risk_config_changed":
        changed = copy.deepcopy(cfg)
        changed["risk"]["leverage"] = int(changed["risk"]["leverage"]) + 1
        assert F.live_entry_block(j, sid, cfg=changed) is not None
        return
    if hold == "install_changed":
        changed = StrategySpec.from_dict(v["spec"])
        changed.entry_long = "close > 1"
        with j._tx() as db:     # content changes, state does not: the row is
            db.execute("UPDATE strategies SET spec_json=? WHERE id=?",
                       (changed.to_json(), sid))    # no longer the approved version
    if hold in ("validation_corrupt", "probation_corrupt",
                "owner_approval_corrupt"):
        table = {"validation_corrupt": "strategy_validation_receipts",
                 "probation_corrupt": "strategy_probation_receipts",
                 "owner_approval_corrupt": "strategy_approval_decisions"}[hold]
        with j._tx() as db:
            db.execute(f"DROP TRIGGER {table}_no_update")
            db.execute(f"UPDATE {table} SET canonical_sha256='corrupt'")
    if hold == "paused":
        F.govern_version(j, cfg, vid, "PAUSED", actor="strategy_governor",
                         reason_code="TEST-ONLY", at_ms=NOW)
    if hold == "retired":
        F.retire_version(j, vid, F.RETIRED, reason_code="TEST-ONLY",
                         actor="strategy_governor", at_ms=NOW)
    if hold == "deployment_for_other_decision":
        with j._tx() as db:
            db.execute("DROP TRIGGER strategy_first_live_deployments_no_update")
            db.execute("UPDATE strategy_first_live_deployments SET "
                       "canonical_sha256='corrupt'")
    blocked = F.live_entry_block(j, sid, cfg=cfg)
    assert blocked, hold
    venue = Venue()
    ex = Executor(venue, j, cfg, MarketType.FUTURES)
    d = Decision("d", "c", "BTC/USDT", Action.BUY, 1, .5, .8, [], [])
    assert ex.open(d, 2, 2, 95, 110, sid, "version") is None
    assert venue.sent == []


def test_unsafe_size_is_refused_by_the_existing_risk_contract(
        tmp_path, cfg, monkeypatch):
    """No new sizing model: Risk alone sizes, and refuses a stop-less or
    oversized entry whatever the strategy's evidence says."""
    j, v, risk, _rid = _activated(tmp_path, cfg, monkeypatch)
    _deploy(j, cfg, v)
    assert F.live_entry_block(j, v["strategy_id"], cfg=cfg) is None
    from trader.core.types import ControlState
    ok = risk.check_entry(ControlState.ACTIVE, "BTC/USDT", 100.0, 2.0, 0.02,
                          [], 10000, 0, "futures")
    no_stop = risk.check_entry(ControlState.ACTIVE, "BTC/USDT", 100.0, 2.0,
                               0.0, [], 10000, 0, "futures")
    frozen = risk.check_entry(ControlState.FROZEN, "BTC/USDT", 100.0, 2.0,
                              0.02, [], 10000, 0, "futures")
    assert ok.ok is True and ok.amount > 0
    assert no_stop.ok is False and frozen.ok is False


# ── 6. the Kernel admits through the one path ───────────────────────────
class _AnalystStub:
    def __init__(self, *a, **kw):
        pass

    def review_deployed(self, book):
        return []

    def flush_health_observations(self):
        return True

    def set_measured_regimes(self, spec):
        return {"fit": ["TRENDING_UP"], "declared": []}


def _kernel(j, cfg):
    from trader.kernel import Kernel
    k = Kernel.__new__(Kernel)
    k.journal, k.cfg, k.feed, k.notifier = j, cfg, None, None
    k._load_population = lambda: []
    k._strategist_knowledge = lambda: {}
    return k


def test_kernel_installs_a_qualified_spec_as_one_exact_paper_version(
        tmp_path, cfg):
    j = Journal(tmp_path / "k.db")
    spec = _spec(include=())                      # the writer named no markets
    q = _qualification(spec, cfg)
    ev = {"chosen_timeframe": TF, "recent": {"pooled_pf": 1.6, "trades": 40}}
    assert _kernel(j, cfg)._qualified_install(spec, ev, q, _AnalystStub(), {}) \
        is True
    row, = [r for r, _s in j.list_specs()]
    vid = F.resolve_current_install(j, spec.id)["version_id"]
    v = F.load_version(j, vid)
    assert row["state"] == "paper" and F.state_of(j, vid) == F.SHADOW
    assert v["spec"]["universe"]["include"] == sorted(SCORED)
    assert v["spec"]["regime_filter"] == []        # measured regimes advisory only
    assert v["source"]["kind"] == Q.SOURCE_KIND
    assert j.query("SELECT COUNT(*) n FROM strategy_probation_receipts")[0]["n"] == 0
    assert F.live_entry_block(j, spec.id, cfg=cfg).startswith(
        "version_not_live_authorized")


def test_kernel_refuses_an_edit_of_an_existing_version(tmp_path, cfg):
    j = Journal(tmp_path / "k.db")
    spec = _spec(include=())
    q = _qualification(spec, cfg)
    ev = {"chosen_timeframe": TF}
    k = _kernel(j, cfg)
    assert k._qualified_install(spec, ev, q, _AnalystStub(), {})
    again = _spec(include=(), entry_long="close > ema(30)")
    assert k._qualified_install(again, ev, _qualification(again, cfg),
                                _AnalystStub(), {}) is False
    assert j.query("SELECT COUNT(*) n FROM strategy_versions")[0]["n"] == 1


def _mechanism(tmp_path, cfg, monkeypatch, qualify):
    j = Journal(tmp_path / "m.db")
    spec = _spec(include=())

    class Analyst(_AnalystStub):
        pass
    Analyst.qualify = lambda self, s, book: qualify(s)

    class Writer:
        def __init__(self, *a):
            pass

        def write(self, **kw):
            return spec, {}
    monkeypatch.setattr("trader.brain.analyst.Analyst", Analyst)
    monkeypatch.setattr("trader.brain.spec_writer.SpecWriter", Writer)
    monkeypatch.setattr("trader.brain.llm.BrainLLM", lambda *a: None)
    return j, spec, _kernel(j, cfg)._mechanism_once()


def test_a_failed_backtest_installs_nothing(tmp_path, cfg, monkeypatch):
    j, spec, rep = _mechanism(tmp_path, cfg, monkeypatch, lambda s: (
        False, {"spec": s.id, "reason": "pooled PF 0.90 < 1.15"}, None))
    assert rep["added"] == [] and j.list_specs() == []
    assert not F.versioned(j, spec.id)
    assert j.query("SELECT COUNT(*) n FROM brain_events WHERE kind="
                   "'spec_rejected'")[0]["n"] == 1


def test_a_passed_backtest_installs_a_version_never_a_bare_row(
        tmp_path, cfg, monkeypatch):
    def qualify(s):
        return True, {"chosen_timeframe": TF, "recent": {"pooled_pf": 1.6}}, \
            _qualification(s, cfg)
    j, spec, rep = _mechanism(tmp_path, cfg, monkeypatch, qualify)
    assert rep["added"] == [spec.name]
    assert F.versioned(j, spec.id)
    assert F.state_of(j, F.resolve_current_install(j, spec.id)["version_id"]) \
        == F.SHADOW


def test_a_qualified_spec_that_cannot_be_versioned_is_not_installed(
        tmp_path, cfg, monkeypatch):
    def qualify(s):                     # evidence of a different strategy
        other = _spec(entry_long="close > ema(99)")
        return True, {"chosen_timeframe": TF}, _qualification(other, cfg)
    j, spec, rep = _mechanism(tmp_path, cfg, monkeypatch, qualify)
    assert rep["added"] == [] and j.list_specs() == []
    assert not F.versioned(j, spec.id)


def test_unmapped_legacy_install_path_is_gone():
    from trader.kernel import Kernel
    assert not hasattr(Kernel, "_install_spec")
    assert hasattr(Kernel, "_qualified_install")
    import trader.strategy.promotion as P
    assert not any(hasattr(P, n) for n in (
        "PROMOTE_MIN_TRADES", "PROMOTE_MIN_WINRATE", "PROMOTE_MIN_PF",
        "DEMOTE_CONSEC_LOSSES", "RETIRE_DEMOTED_DAYS"))


# ── 7. the real Analyst engine produces the Backtest PASS ───────────────
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def _series(n, seed, persistent=True):
    rng = np.random.default_rng(seed)
    drift, d = np.zeros(n), 0.0
    for i in range(n):
        d = 0.97 * d + rng.normal(0, 0.0012)
        drift[i] = d if persistent else 0.0
    close = 100 * np.cumprod(1 + drift + rng.normal(0, 0.006, n))
    df = pd.DataFrame({"ts": pd.date_range("2024-01-01", periods=n, freq="4h",
                                           tz="UTC"),
                       "open": np.r_[close[0], close[:-1]], "close": close,
                       "volume": rng.uniform(50, 500, n)})
    df["high"] = df[["open", "close"]].max(axis=1) * 1.002
    df["low"] = df[["open", "close"]].min(axis=1) * 0.998
    return df[["ts", "open", "high", "low", "close", "volume"]]


ENGINE_CFG = {
    "risk": {"stop_loss_atr_mult": 2.5, "take_profit_atr_mult": 4.5,
             "taker_fee_pct": 0.05, "slippage_atr_frac": 0.06,
             "risk_per_trade_pct": 1.5, "funding_rate_8h": 0.0001,
             "bar_minutes": 240, "real_funding": False, "max_open_trades": 8},
    "strategies": {"select_recent_days": 90, "select_min_trades": 20,
                   "select_min_pf": 1.15, "select_null_max_p": 0.01}}


class _Events:
    def log_brain_event(self, *a):
        pass


def _analyst(monkeypatch, persistent, symbols=8, bars=3000):
    from trader.brain.analyst import Analyst
    frames = {f"S{i}/USDT": _series(bars, 100 + i, persistent)
              for i in range(symbols)}
    a = Analyst(_Events(), copy.deepcopy(ENGINE_CFG))
    monkeypatch.setattr(a, "frames", lambda tf, extra=(): dict(frames))
    return a, frames


def _trend_spec():
    return _spec(entry_long="close > ema(20) and close > ema(50)", include=(),
                 exit=ExitSpec(stop={"kind": "atr", "mult": 2.0},
                               target={"kind": "none"},
                               trail={"kind": "none"}, time={"max_bars": 24}),
                 regime_filter=[])


@pytest.fixture(scope="module")
def engine_pass():
    """One real Analyst.qualify run on synthetic persistent-trend candles."""
    mp = pytest.MonkeyPatch()
    try:
        a, frames = _analyst(mp, persistent=True)
        spec = _trend_spec()
        ok, ev, q = a.qualify(spec, [])
        yield spec, ok, ev, q, frames, a
    finally:
        mp.undo()


def test_real_engine_issues_a_reproducible_backtest_pass(engine_pass, tmp_path):
    spec, ok, ev, q, frames, a = engine_pass
    assert ok is True and q is not None, ev.get("reason")
    assert Q.verify(q) is q and q["verdict"] == "PASS"
    assert q["exit_semantics_id"] == _exit_id()          # measured as it will run
    assert q["timeframe"] == ev["chosen_timeframe"] == TF
    assert q["recent"]["trades"] >= q["thresholds"]["min_trades"]
    assert q["recent"]["pooled_pf"] >= q["thresholds"]["min_pf"]
    assert q["robustness"]["consistency_p_dep"] <= q["thresholds"]["null_max_p"]
    # reproducible: the run identity is a pure function of spec, data, config
    from trader.strategy import quant_contract
    probe = StrategySpec.from_dict({**spec.to_dict(), "timeframe": TF})
    again = quant_contract.run_identity(
        probe, a._ctx(TF, probe)[0], a._ctx(TF, probe)[3])
    assert again["identity"] == q["run"]["identity"]
    # and it becomes a version through the Factory with the engine's own cfg
    j = _journal(tmp_path)
    frozen = copy.deepcopy(spec)
    frozen.universe = {**frozen.universe,
                       "include": sorted(q["recent"]["per_symbol"])}
    frozen.regime_filter = []
    out = F.create_version(j, ENGINE_CFG, {"kind": Q.SOURCE_KIND,
                                           "spec": frozen.to_dict(),
                                           "qualification": q}, at_ms=T0)
    assert F.state_of(j, out["version_id"]) == F.VALIDATED


def test_real_engine_refuses_a_strategy_with_no_edge(monkeypatch):
    a, _frames = _analyst(monkeypatch, persistent=False)
    ok, ev, q = a.qualify(_trend_spec(), [])
    assert ok is False and q is None and ev.get("reason")


def test_real_engine_refuses_an_unsupported_exit_before_evaluating(monkeypatch):
    a, _frames = _analyst(monkeypatch, persistent=True)
    monkeypatch.setattr(a, "evaluate", lambda *x, **k: pytest.fail("evaluated"))
    spec = _trend_spec()
    spec.exit = ExitSpec(trail={"kind": "atr", "mult": 3.0, "arm_at_r": 1.0})
    ok, ev, q = a.qualify(spec, [])
    assert ok is False and q is None
    assert ev["reason"].startswith("unsupported_versioned_exit")


def test_qualification_does_not_leak_into_other_admissions(engine_pass):
    *_, a = engine_pass
    assert getattr(a, "_exit_semantics_id", None) is None
