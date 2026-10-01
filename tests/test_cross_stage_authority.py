"""LUFFY-CROSS-STAGE-AUTHORITY-FIX-R1: the four authority defects.

F1  a versioned strategy in paper/shadow probation (or approved without
    established capacity) never reaches the real order path;
F2  paper probation counts explicit paper trades only;
F3  a validation receipt binds the rule the referee actually evaluated,
    rendered thresholds included, never a re-rendering from current gauges;
F4  a versioned strategy's entry stop/target come from its frozen spec,
    never from mutable global target configuration;
P2-A the immutable version record carries no capacity.

Every test runs on a temporary journal through the production functions
(Kernel._research_handoff / _load_population / _try_enter, the real
Executor, factory_handoff); only the venue is a fake.
"""
import json

import numpy as np
import pandas as pd
import pytest

from trader.core import reason_codes as rc
from trader.core.config import load_config
from trader.core.types import Action, MarketType
from trader.engine.executor import Executor
from trader.engine.exits import SpecExit
from trader.strategy import factory_handoff as F
from trader.strategy.spec import StrategySpec
from tests.test_strategy_factory_handoff import (  # noqa: F401
    DAY, PASSING, T0, _Admitting, _approved, _install, _iso, _journal,
    _kernel, _row, _trades, cfg)
from tests.test_trade_provenance import Venue


# ── fixtures ─────────────────────────────────────────────────────────────
def _candidate(j, geo, state="reason_passed", p10=-1.0):
    """A referee-passed candidate stored exactly as the runner stores one,
    its gate1 look recording the evaluated rule."""
    from trader.research import vocab
    from trader.research.combo import Combination, evaluated_record
    from trader.research.ledger import Ledger
    led = Ledger(j)
    led.ensure()
    _gauges(led, p10=p10)
    part = vocab.parts_for("4h", led.gauges("4h"))[0]
    c = Combination((part,), "4h", geo)
    led.record_result({"hash": c.hash, "tf": "4h", "geo": geo, "k": 1,
                       "parts": list(c.keys), "trades": 300,
                       "portfolio": {"total_pct": 40.0, "max_dd_pct": 20.0},
                       "testable": True, "verdict": "scored"},
                      "survivor", "r", {})
    led.record_test(c.hash, "4h", geo, "gate1", 0.001, 0.0025, False,
                    {"t": 1, "evaluated": evaluated_record(c)})
    led.set_candidate(c.hash, "4h", geo, state, rank=40.0,
                      gate1={"p": 0.001, "alpha": 0.0025, "t": 1},
                      gate3={"passed": True, "reason": "fixture"})
    return c


def _gauges(led, p10):
    from trader.research import vocab
    led.record_gauges("4h", {e: {"p10": p10, "p25": -0.5, "p75": 0.5,
                                 "p90": 1.0, "n": 9000, "finite_frac": 0.99,
                                 "usable": True}
                             for e in vocab.expressions("4h")})


def _bars(n, atr_pts, price=100.0):
    """Flat closes with a fixed true range: ATR is exactly atr_pts."""
    return pd.DataFrame({"timestamp": np.arange(n) * 60000, "open": price,
                         "close": price, "high": price + atr_pts / 2,
                         "low": price - atr_pts / 2, "volume": 1.0})


class _Snap:
    price, symbol, ts = 100.0, "BTC/USDT", "2026-10-01T00:00:00+00:00"

    def df(self, tf):
        return _bars(60, 1.0)                  # ATR 1 on every frame


class _Sizing:
    ok, reason, code = True, "", ""
    amount, size_usdt, risk_usdt = 1.0, 100.0, 1.0


class _Risk:
    def protection_levels(self, price, atr, side, tp):
        return price - 2.5 * atr, price + tp * atr

    def check_entry(self, *a, **kw):
        return _Sizing()


class _SM:
    state = "ACTIVE"                           # global control permits entries


class _D:
    id, symbol, skip_reason, meta_size = "d1", "BTC/USDT", "", 1.0
    reason_codes = None
    confidence = 0.8

    def __init__(self, sid):
        self.action = Action.BUY
        self.strategy_signals = [{"strategy_id": sid, "action": "BUY",
                                  "confidence": 0.6}]


def _live_kernel(j, monkeypatch):
    """The Kernel's entry path with the REAL Executor over a fake venue."""
    monkeypatch.setattr("trader.engine.executor.time.sleep", lambda _: None)
    k = _kernel(j)
    k.cfg = {**k.cfg, "timeframes": {"execution": "15m"},
             "risk": {"min_notional_usdt": 10}}
    k.market_type = MarketType.FUTURES
    k.state_machine, k.risk = _SM(), _Risk()
    venue = Venue()
    k.executor = Executor(venue, j, load_config(), MarketType.FUTURES)
    k.executor.fill_retry_s = 0
    k._load_population()
    return k, venue


def _shadow(tmp_path, geo="fixed"):
    from trader.core.journal import Journal
    j = Journal(tmp_path / "j.db")
    _candidate(j, geo)
    assert _kernel(j)._research_handoff(_Admitting(), []) is not None
    v = F.load_version(j, _row(j, "SELECT * FROM strategy_versions")
                       ["version_id"])
    assert F.state_of(j, v["version_id"]) == F.SHADOW
    return j, v


# ── F1: paper/live authority fence ───────────────────────────────────────
def test_f1_shadow_version_signal_never_reaches_the_real_executor(
        tmp_path, monkeypatch):
    j, v = _shadow(tmp_path)
    k, venue = _live_kernel(j, monkeypatch)
    assert v["strategy_id"] in k._spec_exits      # loaded, trade-eligible row
    d = _D(v["strategy_id"])
    assert k._try_enter(d, _Snap(), 5000.0, 0) is False
    assert venue.sent == []                       # no order of any kind
    assert d.reason_codes == [rc.VERSION_NOT_LIVE_AUTHORIZED]
    assert "version_not_live_authorized:SHADOW" in d.skip_reason
    assert j.query("SELECT * FROM trades") == []


def test_f1_the_executor_boundary_itself_fails_closed(tmp_path, monkeypatch):
    """Even a caller that skips the Kernel check cannot place the order."""
    j, v = _shadow(tmp_path)
    k, venue = _live_kernel(j, monkeypatch)
    d = _D(v["strategy_id"])
    pos = k.executor.open(d, 1.0, 1.0, 97.0, 109.0,
                          strategy_id=v["strategy_id"], strategy_name="x",
                          exec_mode="live")
    assert pos is None and venue.sent == []
    assert d.reason_codes == [rc.VERSION_NOT_LIVE_AUTHORIZED]
    # a paper label cannot smuggle a real order either
    d2 = _D("legacy_genome")
    assert k.executor.open(d2, 1.0, 1.0, 97.0, 109.0,
                           strategy_id="legacy_genome", strategy_name="x",
                           exec_mode="paper") is None
    assert venue.sent == [] and d2.reason_codes == [rc.EXEC_MODE_NOT_REAL]


@pytest.fixture
def approved_unavailable(tmp_path, cfg):
    """An owner-APPROVED exact version whose capacity receipt is recorded
    and UNAVAILABLE (the capacity contract's real result today)."""
    from tests.test_strategy_capacity import _receipt
    j, _h = _journal(tmp_path)
    v, _p, _d = _approved(j, cfg)
    v = F.load_version(j, v["version_id"])
    out = _receipt(j, cfg, v)
    assert out["status_capacity"] != "ESTABLISHED"
    assert F.state_of(j, v["version_id"]) == F.APPROVED_FIRST_LIVE
    return j, v


def test_f1_approved_but_capacity_unavailable_never_reaches_the_executor(
        approved_unavailable, monkeypatch):
    j, v = approved_unavailable
    k, venue = _live_kernel(j, monkeypatch)
    d = _D(v["strategy_id"])
    assert k._try_enter(d, _Snap(), 5000.0, 0) is False
    assert venue.sent == []
    assert "version_capacity_not_established" in d.skip_reason
    assert F.live_entry_block(j, v["strategy_id"]) == \
        "version_capacity_not_established"


def test_f1_every_versioned_state_is_fenced_and_legacy_is_not(tmp_path, cfg):
    j, v = _shadow(tmp_path)
    assert F.live_entry_block(j, v["strategy_id"]).startswith(
        "version_not_live_authorized:")
    # legacy / non-versioned strategies keep their existing authority
    assert F.live_entry_block(j, "legacy_genome") is None
    assert F.live_entry_block(j, "") is None
    # a journal without the factory tables is a legacy journal
    from trader.core.journal import Journal
    (tmp_path / "legacy").mkdir()
    assert F.live_entry_block(Journal(tmp_path / "legacy" / "j.db"),
                              "anything") is None

    class _Broken:
        def query(self, *a):
            raise RuntimeError("db gone")
    assert F.live_entry_block(_Broken(), "x").startswith(
        "version_authority_unreadable")


def test_f1_a_legacy_spec_still_reaches_the_executor(tmp_path, monkeypatch):
    """The fence blocks versions only: a non-versioned spec's entry is sent."""
    from trader.core.journal import Journal
    from tests.test_trade_provenance import _valid
    j = Journal(tmp_path / "j.db")
    spec = _valid()
    j.upsert_spec(spec, state="paper")
    k, venue = _live_kernel(j, monkeypatch)
    assert k._spec_exits[spec.id].versioned is False
    k._entry_provenance = lambda *a: (None, None)
    d = _D(spec.id)
    from tests.test_trade_provenance import decision
    real = decision(j)
    d.id, d.cycle_id, d.ts = real.id, real.cycle_id, real.ts
    k._try_enter(d, _Snap(), 5000.0, 0)
    assert any(s[1] == "market" for s in venue.sent)


# ── F2: paper probation counts paper trades only ─────────────────────────
def _probation(j, cfg, v, at=T0 + 30 * DAY):
    return F.evaluate_probation(j, cfg, v["version_id"], at_ms=at)


def _version(tmp_path, cfg):
    j, h = _journal(tmp_path)
    ver = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                           at_ms=T0 - DAY)
    return j, _install(j, ver["version_id"])


def _set_mode(j, prefix, mode):
    with j._tx() as c:
        c.execute("UPDATE trades SET exec_mode=? WHERE id LIKE ?",
                  (mode, prefix + "%"))


def test_f2_fifteen_exact_paper_trades_may_satisfy(tmp_path, cfg):
    j, v = _version(tmp_path, cfg)
    _trades(j, v["strategy_id"], PASSING, T0, spec_hash=v["spec_hash"])
    assert _probation(j, cfg, v)["status"] == F.P_SATISFIED


def test_f2_fifteen_exact_live_trades_count_zero(tmp_path, cfg):
    j, v = _version(tmp_path, cfg)
    _trades(j, v["strategy_id"], PASSING, T0, spec_hash=v["spec_hash"])
    _set_mode(j, "t", "live")
    p = _probation(j, cfg, v)
    assert p["status"] == F.P_INSUFFICIENT
    rec = F._load(_row(j, "SELECT * FROM strategy_probation_receipts"))
    assert rec["trades"] == [] and rec["stats"]["trades"] == 0
    assert {e["reason"] for e in rec["excluded_trades"]} == {
        "not_paper_execution"}


def test_f2_mixed_counts_only_explicit_paper(tmp_path, cfg):
    j, v = _version(tmp_path, cfg)
    _trades(j, v["strategy_id"], PASSING, T0, spec_hash=v["spec_hash"])
    _trades(j, v["strategy_id"], [50.0] * 15, T0 + DAY, prefix="L",
            spec_hash=v["spec_hash"])
    _set_mode(j, "L", "live")
    p = _probation(j, cfg, v)
    rec = F._load(_row(j, "SELECT * FROM strategy_probation_receipts"))
    assert p["status"] == F.P_SATISFIED
    assert {t["id"] for t in rec["trades"]} == {f"t{i}" for i in range(15)}
    assert all(t["exec_mode"] == "paper" for t in rec["trades"])
    assert rec["stats"]["pnl"] == pytest.approx(sum(PASSING) - len(PASSING) * 0.06)


def test_f2_open_pre_probation_and_wrong_version_do_not_count(tmp_path, cfg):
    j, v = _version(tmp_path, cfg)
    _trades(j, v["strategy_id"], PASSING[:14], T0, spec_hash=v["spec_hash"])
    _trades(j, v["strategy_id"], [10.0], T0 + DAY, prefix="open",
            spec_hash=v["spec_hash"])
    with j._tx() as c:
        c.execute("UPDATE trades SET status='open', closed_at=NULL "
                  "WHERE id LIKE 'open%'")
    _trades(j, v["strategy_id"], [10.0], T0 - 2 * DAY, prefix="pre",
            spec_hash=v["spec_hash"])
    _trades(j, v["strategy_id"], [10.0], T0 + DAY, prefix="wrong",
            spec_hash="f" * 64)
    p = _probation(j, cfg, v)
    rec = F._load(_row(j, "SELECT * FROM strategy_probation_receipts"))
    assert p["status"] == F.P_INCOMPLETE and p["request_id"] is None
    assert len(rec["trades"]) == 14
    ids = {t["id"] for t in rec["trades"]}
    assert not ids & {"open0", "pre0", "wrong0"}


@pytest.mark.parametrize("mode", [None, "", "shadow?", "LIVE"])
def test_f2_missing_or_unknown_execution_mode_fails_closed(tmp_path, cfg,
                                                           mode):
    j, v = _version(tmp_path, cfg)
    _trades(j, v["strategy_id"], PASSING, T0, spec_hash=v["spec_hash"])
    _trades(j, v["strategy_id"], [10.0], T0 + DAY, prefix="u",
            spec_hash=v["spec_hash"])
    _set_mode(j, "u", mode)
    assert _probation(j, cfg, v)["status"] == F.P_INCOMPLETE


def test_f2_a_live_satisfied_receipt_cannot_back_an_approval(tmp_path, cfg):
    """A receipt satisfied on paper stops re-verifying if its trades are
    later shown to be live: the approval edge refuses."""
    j, v = _version(tmp_path, cfg)
    _trades(j, v["strategy_id"], PASSING, T0, spec_hash=v["spec_hash"])
    p = _probation(j, cfg, v)
    assert p["status"] == F.P_SATISFIED
    _set_mode(j, "t", "live")
    with pytest.raises(F.HandoffRefused) as e:
        F.record_owner_decision(j, cfg, p["request_id"], "APPROVED",
                                actor="operator",
                                decided_at_ms=T0 + 31 * DAY)
    assert e.value.code == "probation_evidence_changed"


# ── F3: validation binds the evaluated parameters ────────────────────────
def test_f3_gauge_mutation_after_referee_refuses_validation(tmp_path, cfg):
    from trader.core.journal import Journal
    from trader.research import vocab
    from trader.research.ledger import Ledger
    j = Journal(tmp_path / "j.db")
    c = _candidate(j, "fixed", state="referee_passed")
    evaluated = c.long
    assert "-1" in evaluated and "-10" not in evaluated
    led = Ledger(j)
    _gauges(led, p10=-10.0)                     # re-measured after the look
    now = vocab.parts_for("4h", led.gauges("4h"))[0]
    assert now.key == c.parts[0].key            # same symbolic identity...
    assert now.long != evaluated and "-10" in now.long   # ...other numbers
    with pytest.raises(F.HandoffRefused) as e:
        F.create_version(j, cfg, {"kind": "research_candidate",
                                  "hash": c.hash}, at_ms=T0)
    assert e.value.code == "validation_reconstruction_drift"
    assert j.query("SELECT * FROM strategy_versions") == []
    assert j.query("SELECT * FROM strategy_validation_receipts") == []


def test_f3_unchanged_frozen_reconstruction_succeeds(tmp_path, cfg):
    from trader.core.journal import Journal
    j = Journal(tmp_path / "j.db")
    c = _candidate(j, "fixed", state="referee_passed")
    out = F.create_version(j, cfg, {"kind": "research_candidate",
                                    "hash": c.hash}, at_ms=T0)
    v = F.load_version(j, out["version_id"])
    assert (v["spec"]["entry_long"], v["spec"]["entry_short"]) == (c.long,
                                                                   c.short)
    rec = F.verify_validation(j, v)
    assert rec["evaluated"]["entry_long"] == c.long
    look = _row(j, "SELECT * FROM research_tests WHERE hash=?", c.hash)
    assert rec["evidence"]["evaluated_sha256"] == F._jsha(
        json.loads(look["detail"])["evaluated"])


def test_f3_a_look_that_did_not_record_the_evaluated_rule_is_refused(
        tmp_path, cfg):
    """Legacy evidence names parts by rank only: it cannot bind numbers."""
    from trader.core.journal import Journal
    j = Journal(tmp_path / "j.db")
    c = _candidate(j, "fixed", state="referee_passed")
    with j._tx() as db:
        db.execute("UPDATE research_tests SET detail=? WHERE hash=?",
                   (json.dumps({"t": 1}), c.hash))
    with pytest.raises(F.HandoffRefused) as e:
        F.create_version(j, cfg, {"kind": "research_candidate",
                                  "hash": c.hash}, at_ms=T0)
    assert e.value.code == "referee_evaluated_candidate_unbound"


def test_f3_the_referee_records_what_it_evaluated(tmp_path):
    """The runner's registered look carries the evaluated rule."""
    import inspect
    from trader.research import runner
    src = inspect.getsource(runner.ResearchRunner._examine)
    assert "evaluated_record(c)" in src
    assert src.count('"evaluated": evaluated') == 2   # pass and charged failure


# ── F4: frozen exit geometry at runtime ──────────────────────────────────
def test_f4_versioned_runtime_target_equals_frozen_geometry(
        tmp_path, monkeypatch):
    j, v = _shadow(tmp_path, geo="fixed")        # 3 ATR stop, 3R target
    assert v["spec"]["exit"]["stop"] == {"kind": "atr", "mult": 3.0}
    assert v["spec"]["exit"]["target"] == {"kind": "rr", "v": 3.0}
    k, _venue = _live_kernel(j, monkeypatch)
    se = k._spec_exits[v["strategy_id"]]
    assert se.versioned is True
    assert k._protection_for(se, 100.0, 1.0, "long") == (97.0, 109.0)
    assert k._protection_for(se, 100.0, 1.0, "short") == (103.0, 91.0)
    # the mutable global target never reaches the version
    assert k.executor.tp_atr_mult == 4.5          # config take_profit_atr_mult
    k.executor.tp_atr_mult = 99.0
    assert k._protection_for(se, 100.0, 1.0, "long") == (97.0, 109.0)


def test_f4_frozen_levels_are_the_backtests_own_geometry(tmp_path, cfg):
    from trader.strategy.geometries import GEOS
    from trader.strategy.vector_backtest import (_stop_distance,
                                                 _target_distance)
    j, h = _journal(tmp_path)
    ver = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                           at_ms=T0 - DAY)
    spec = StrategySpec.from_dict(F.load_version(j, ver["version_id"])["spec"])
    spec.exit = GEOS["fixed"]
    se = SpecExit.from_spec(spec, versioned=True)
    for px, atr in ((100.0, 1.0), (100.0, 0.01), (2.5, 0.2)):
        for side, sign in (("long", 1.0), ("short", -1.0)):
            sl_d = _stop_distance(spec.exit, px, atr, None, 0, side)
            tp_d = _target_distance(spec.exit, px, atr, sl_d)
            assert se.frozen_levels(px, atr, side) == (px - sign * sl_d,
                                                       px + sign * tp_d)
    # a legacy (non-versioned) spec keeps the configured target
    assert SpecExit.from_spec(spec).versioned is False


def test_f4_unsupported_geometry_refuses_the_install_before_probation(
        tmp_path, cfg, monkeypatch):
    j, h = _journal(tmp_path)
    ver = F.create_version(j, cfg, {"kind": "research_candidate", "hash": h},
                           at_ms=T0 - DAY)
    v = F.load_version(j, ver["version_id"])
    swing = StrategySpec.from_dict(v["spec"])
    swing.exit.stop = {"kind": "swing", "lookback": 20}
    assert SpecExit.frozen_unsupported(swing) == "stop=swing"
    with pytest.raises(ValueError):
        SpecExit.from_spec(swing, versioned=True).frozen_levels(
            100.0, 1.0, "long")
    # through the real exact install: a geometry the runtime cannot run
    # exactly (here: pretend ATR stops were unsupported) never starts
    # probation
    monkeypatch.setattr(SpecExit, "FROZEN_STOPS", ("pct",))
    j.upsert_spec(StrategySpec.from_dict(v["spec"]), state="paper",
                  origin="research")
    with pytest.raises(F.HandoffRefused) as e:
        F.record_exact_install(j, v["version_id"], at_ms=T0)
    assert e.value.code == "unsupported_exit_geometry:stop=atr"
    assert F.state_of(j, v["version_id"]) == F.VALIDATED
    assert j.query("SELECT * FROM strategy_version_installs") == []


# ── P2-A: the version record is the strategy alone ───────────────────────
def test_p2a_version_retry_is_idempotent_over_a_legacy_capacity_field(
        tmp_path, cfg):
    j, h = _journal(tmp_path)
    src = {"kind": "research_candidate", "hash": h}
    first = F.create_version(j, cfg, src, at_ms=T0)
    row = _row(j, "SELECT * FROM strategy_versions")
    assert "capacity" not in json.loads(row["canonical_json"])
    # a record written before this fix carried a capacity reference
    for legacy in ({"status": "UNAVAILABLE",
                    "reason": "no_truthful_capacity_estimator"}, F.CAPACITY):
        rec = json.loads(row["canonical_json"])
        rec["capacity"] = legacy
        text = F.canonical(rec)
        with j._tx() as c:
            c.execute("DROP TRIGGER strategy_versions_no_update")
            c.execute("UPDATE strategy_versions SET canonical_json=?, "
                      "canonical_sha256=?", (text, F._sha(text)))
        F.ensure(j)                                # restores the trigger
        assert F.load_version(j, first["version_id"])["capacity"] == legacy
        again = F.create_version(j, cfg, src, at_ms=T0 + 1)
        assert (again["status"], again["version_id"]) == (
            "duplicate", first["version_id"])
