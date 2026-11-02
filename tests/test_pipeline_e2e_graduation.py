"""End-to-end canary: Stage 5 candidate -> referee look -> VALIDATED version -> paper probation -> receipt.

The stages are the real ledger / factory_handoff code on temporary stores. Candidates, held-out numbers and
trades are SYNTHETIC FIXTURES reused from tests/test_strategy_factory_handoff.py: this proves that every
transition happens, in order, with the evidence each one must leave behind, and that a candidate which should
stop does stop at the right stage with nothing written downstream. It is not evidence of any edge.

Nothing here touches a production store or the network, and no gate or threshold is modified.
"""
import pytest

from tests import test_strategy_factory_handoff as H
from tests.test_strategy_factory_handoff import cfg  # noqa: F401  (fixture)
from trader.research import vocab
from trader.research.combo import Combination, evaluated_record
from trader.research.ledger import Ledger
from trader.strategy import factory_handoff as F

DOWNSTREAM = ("strategy_versions", "strategy_version_installs", "strategy_probation_receipts",
              "strategy_approval_requests", "strategy_approval_decisions")
TEN = [10.0] * 5 + [-5.0] * 5            # exactly 10 closed trades: win rate 0.50, profit factor 2.0
LOW_WIN_RATE = [10.0] * 2 + [-5.0] * 8   # 10 trades, win rate 0.20, profit factor 0.5
TOO_FEW = [10.0] * 3 + [-5.0] * 2        # 5 trades: below the probation count


def _n(j, table):
    if not j.query("SELECT 1 FROM sqlite_master WHERE name=?", (table,)):
        return 0
    return len(j.query(f"SELECT * FROM {table}"))


def _downstream(j):
    return {t: _n(j, t) for t in DOWNSTREAM}


def _source(h):
    return {"kind": "research_candidate", "hash": h}


def _referee_look(j, h, *, p, alpha=0.0025, passed=True):
    """Record the held-out look exactly as the referee's bookkeeping does, then the candidate's disposition."""
    led = Ledger(j)
    part = vocab.parts_for("4h", led.gauges("4h"))[0]
    combo = Combination((part,), "4h", "fixed", trigger="", evaluation_scope="")
    assert combo.hash == h
    led.record_test(h, "4h", "fixed", "gate1", p, alpha, False,
                    {"t": 1, "evaluated": evaluated_record(combo), "cut_ms": 1_780_000_000_000, "draws": 1999,
                     "a": {"consistency_p_dep": p / 2}, "b": {"consistency_p_dep": p / 2}, "rotation": {"p": p}})
    state = "referee_passed" if passed else "gate1_fail"
    led.set_candidate(h, "4h", "fixed", state, rank=40.0,
                      gate1={"p": p, "alpha": alpha, "t": 1, "reason": "fixture"},
                      gate3={"passed": True, "reason": "fixture book"})
    return led


def _validated(tmp_path, cfg, *, p=0.001):
    j, h = H._journal(tmp_path, state="queued", look=False)
    _referee_look(j, h, p=p)
    v = F.create_version(j, cfg, _source(h), at_ms=H.T0 - H.DAY)
    return j, h, v


def test_a_passing_candidate_graduates_through_every_stage_in_order(tmp_path, cfg):
    report = {}
    # Stage 5: a surviving candidate exists, no held-out look has been spent, the factory refuses it.
    j, h = H._journal(tmp_path, state="queued", look=False)
    led = Ledger(j)
    assert led.candidate(h)["state"] == "queued" and led.spent(h) is None and led.tests() == []
    with pytest.raises(F.HandoffRefused) as e:
        F.create_version(j, cfg, _source(h), at_ms=H.T0 - H.DAY)
    assert e.value.code == "candidate_not_referee_passed:queued"
    assert _downstream(j) == dict.fromkeys(DOWNSTREAM, 0)

    # Stage 6: the referee look is recorded and spends error budget; a second look is permanently refused.
    first_index = led.next_alpha(*led._registered_or_default())[0]
    _referee_look(j, h, p=0.001)
    assert led.candidate(h)["state"] == "referee_passed"
    assert len(led.tests()) == 1 and led.spent(h)["seq"] == 1
    report["lord_look_seq"], report["alpha_t"] = led.spent(h)["seq"], 0.0025
    report["next_look_index_before"], report["next_look_index_after"] = first_index, led.next_alpha(*led._registered_or_default())[0]
    assert report["next_look_index_after"] == first_index + 1                     # one look spent
    with pytest.raises(ValueError, match="holdout_already_spent"):
        led.record_test(h, "4h", "fixed", "gate1", .5, .0001, False, {})
    assert _downstream(j) == dict.fromkeys(DOWNSTREAM, 0)                           # nothing downstream yet

    # Stage 7: a VALIDATED version is created from that exact look; nothing past VALIDATED exists.
    v = F.create_version(j, cfg, _source(h), at_ms=H.T0 - H.DAY)
    assert v["status"] == "inserted" and F.state_of(j, v["version_id"]) == F.VALIDATED
    assert _downstream(j)["strategy_versions"] == 1
    assert all(_downstream(j)[t] == 0 for t in DOWNSTREAM[1:])

    # Stage 8: exact install starts probation (SHADOW); 10 paper trades meeting WR and PF satisfy it.
    rec = H._install(j, v["version_id"])
    assert F.state_of(j, v["version_id"]) == F.SHADOW and _downstream(j)["strategy_version_installs"] == 1
    H._trades(j, rec["strategy_id"], TEN, H.T0, spec_hash=v["spec_hash"])
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=H.T0 + 30 * H.DAY)
    assert p["status"] == F.P_SATISFIED
    assert F.state_of(j, v["version_id"]) == F.APPROVAL_REQUIRED
    assert _downstream(j)["strategy_probation_receipts"] == 1                       # receipt emitted
    assert _downstream(j)["strategy_approval_requests"] == 1                        # ready for the Governor / owner
    assert _downstream(j)["strategy_approval_decisions"] == 0                       # and no approval has been invented
    assert p["request_id"] and p["probation_receipt_id"]


@pytest.mark.parametrize("kw,code,spent_look", [
    ({"state": "gate1_fail"}, "candidate_not_referee_passed:gate1_fail", True),     # referee rejected it
    ({"state": "gate3_fail"}, "candidate_not_referee_passed:gate3_fail", True),     # incumbent-book gate rejected it
    ({"p": 0.01}, "gate1_not_rejected", True),                                       # p above alpha_t
    ({"look": False}, "gate1_look_missing", False),                                  # claims a pass, never looked
])
def test_a_failing_candidate_is_stopped_at_the_referee_stage(tmp_path, cfg, kw, code, spent_look):
    j, h = H._journal(tmp_path, **kw)
    with pytest.raises(F.HandoffRefused) as e:
        F.create_version(j, cfg, _source(h), at_ms=H.T0 - H.DAY)
    assert e.value.code == code
    assert _downstream(j) == dict.fromkeys(DOWNSTREAM, 0)                           # nothing reached the factory
    assert (Ledger(j).spent(h) is not None) is spent_look                           # a failed look still spends budget


@pytest.mark.parametrize("pnls,status", [(LOW_WIN_RATE, F.P_NOT_SATISFIED), (TOO_FEW, F.P_INSUFFICIENT)])
def test_a_candidate_that_passes_the_referee_but_fails_probation_is_stopped_there(tmp_path, cfg, pnls, status):
    j, _h, v = _validated(tmp_path, cfg)
    rec = H._install(j, v["version_id"])
    H._trades(j, rec["strategy_id"], pnls, H.T0, spec_hash=v["spec_hash"])
    p = F.evaluate_probation(j, cfg, v["version_id"], at_ms=H.T0 + 30 * H.DAY)
    assert p["status"] == status
    assert F.state_of(j, v["version_id"]) == F.SHADOW                               # not promoted
    counts = _downstream(j)
    assert counts["strategy_versions"] == 1 and counts["strategy_version_installs"] == 1
    assert counts["strategy_probation_receipts"] == 1                               # the failure is recorded
    assert counts["strategy_approval_requests"] == 0 and counts["strategy_approval_decisions"] == 0
    assert p["request_id"] is None
