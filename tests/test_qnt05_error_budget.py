"""QNT-05: the error budget is registered, retained, and cannot be reset.

Negative controls: retry, restart, rename/duplicate, exhausted budget,
reset attempt, unregistered threshold."""
from types import SimpleNamespace

import pytest

from trader.core.journal import Journal
from trader.research import fdr, portfolio_null as pn
from trader.research.ledger import Ledger
from trader.research.runner import ResearchRunner


def _led(path):
    lg = Ledger(Journal(path))
    lg.ensure()
    return lg


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "j.db")


def _look(led, h, p=0.9):
    t, a = led.next_alpha(0.10, 0.05)
    led.record_test(h, "4h", "fixed", "gate1", p, a, False, {})
    return t, a


def test_each_look_consumes_the_next_step_of_one_sequence(db):
    led = _led(db)
    steps = [_look(led, f"h{i}")[0] for i in range(4)]
    assert steps == [1, 2, 3, 4]
    assert led.next_alpha(0.10, 0.05)[0] == 5


def test_alpha_for_a_given_step_never_rises_without_a_rejection(db):
    led = _led(db)
    levels = [_look(led, f"h{i}")[1] for i in range(8)]
    assert levels == sorted(levels, reverse=True)       # junk tightens


def test_restart_does_not_restore_spent_budget(db):
    led = _led(db)
    _look(led, "h1")
    _look(led, "h2")
    again = _led(db)
    assert again.next_alpha(0.10, 0.05)[0] == 3
    assert again.next_alpha(0.10, 0.05)[1] == pytest.approx(
        fdr.alpha_at(3, []))


def test_retry_of_the_same_candidate_spends_no_second_test(db):
    led = _led(db)
    _look(led, "h1")
    with pytest.raises(ValueError, match="holdout_already_spent"):
        _look(led, "h1")
    assert len(led.tests()) == 1


def test_unregistered_threshold_cannot_reject(db):
    led = _led(db)
    _t, a = led.next_alpha(0.10, 0.05)
    with pytest.raises(ValueError, match="unregistered_alpha"):
        led.record_test("h1", "4h", "fixed", "gate1", 0.04, 0.05, False, {})
    assert led.tests() == []
    # the registered level, and a braked (lower) one, are accepted
    assert led.record_test("h1", "4h", "fixed", "gate1", a / 2, a / 2, True,
                           {})


def test_config_change_cannot_reset_registered_parameters(db):
    led = _led(db)
    _t, a = led.next_alpha(0.10, 0.05)
    _look(led, "h1")
    # someone raises the target / initial wealth in config after spending
    t2, a2 = led.next_alpha(0.50, 0.50)
    assert (t2, a2) == (2, pytest.approx(fdr.alpha_at(2, [], 0.10, 0.05)))
    again = _led(db)                                    # and across restart
    assert again.next_alpha(0.50, 0.50)[1] == pytest.approx(a2)
    with pytest.raises(ValueError, match="unregistered_alpha"):
        again.record_test("h2", "4h", "fixed", "gate1", 0.01,
                          fdr.alpha_at(2, [], 0.50, 0.50), False, {})


def test_failed_look_still_charges_and_does_not_reset(db):
    led = _led(db)
    t, a = led.next_alpha(0.10, 0.05)
    led.record_test("h1", "4h", "fixed", "gate1", 1.0, a, False,
                    {"error": "boom"})                   # charged p=1
    assert led.next_alpha(0.10, 0.05)[0] == t + 1
    assert not led.tests()[0]["rejected"]


def test_exhausted_budget_defers_not_tests(db):
    led = _led(db)
    for i in range(40):
        _look(led, f"h{i}")
    t, a = led.next_alpha(0.10, 0.05)
    assert pn.draws_for(a, 19999) is None               # cannot resolve
    led.set_candidate("late", "4h", "fixed", "queued")
    fake = SimpleNamespace(
        ledger=led, journal=led.journal, cfg={},
        _r=lambda k: {"fdr_target": 0.10, "lord_w0": 0.05,
                      "referee_max_draws": 19999}[k],
        _brake=lambda: 1.0,
        _combo=lambda row, tf, geo: object())
    led.journal.query  # noqa: B018
    with led.journal._tx() as c:
        c.execute("INSERT INTO research_combos (hash, tf, geo, k) "
                  "VALUES ('late','4h','fixed',1)")
    out = ResearchRunner._examine(fake, "4h", "fixed", {"hash": "late"}, 1)
    assert out["state"] == "deferred"
    assert led.candidate("late")["state"] == "deferred"
    assert len(led.tests()) == 40                       # nothing spent


def test_deferred_candidate_cannot_be_reset_to_a_free_look_by_rename(db):
    # a renamed duplicate is the same canonical hash: its spend is on file
    led = _led(db)
    _look(led, "same")
    led.set_candidate("same", "4h", "fixed", "queued")  # reset attempt
    assert led.candidate("same") is None or \
        led.candidate("same")["state"] != "queued"
