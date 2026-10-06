"""QNT-06: insufficient statistical power is UNTESTED, distinct from FAIL/PASS.

The sufficiency requirement is declared by the experiment (`min_trades`, from
config), never invented here. Outcomes are recomputed from inputs on every
call, so a retry or restart cannot carry or lose sufficiency state.
"""
import copy

import pandas as pd

from tests.test_rolling import RISK, _frame, _spec
from trader.research import control
from trader.strategy import rolling
from trader.strategy.compile import compile_spec


def _rv(frames, **kw):
    return rolling.recent_verdict(compile_spec(_spec()), frames, RISK, "1h",
                                  recent_days=60, **kw)


def test_insufficient_samples_are_untested_with_declared_requirement():
    ok, ev = _rv({"A": _frame(3000)}, min_trades=100000)
    assert not ok
    assert ev["outcome"] == "UNTESTED"
    assert ev["untested_code"] == "insufficient_trades"
    assert ev["required_trades"] == 100000
    assert ev["observed_trades"] == ev["trades"] < 100000
    assert "only" in ev["reason"]


def test_empty_sample_is_untested_not_fail_or_pass():
    empty = _frame(3000).iloc[0:0]
    ok, ev = _rv({"A": empty}, min_trades=1)
    assert not ok and ev["outcome"] == "UNTESTED"
    assert ev["observed_trades"] == 0


def test_adequate_sample_is_judged_pass_or_fail_never_untested():
    frames = {"A": _frame(3000)}
    ok, ev = _rv(frames, min_trades=1, min_pf=0.0)
    assert ok and ev["outcome"] == "PASS" and ev["outcome"] != "UNTESTED"
    ok, ev = _rv(frames, min_trades=1, min_pf=1e9)      # no-edge control
    assert not ok and ev["outcome"] == "FAIL" and ev["outcome"] != "UNTESTED"


def test_sufficiency_state_is_recomputed_identically_on_retry():
    frames = {"A": _frame(3000)}
    a = _rv(frames, min_trades=100000)
    b = _rv(copy.deepcopy(frames), min_trades=100000)
    assert a[1] == b[1]


def test_idle_decay_is_not_decay_and_not_fail():
    dead, ev = rolling.has_decayed(compile_spec(_spec()), {"A": _frame(3000)},
                                   RISK, "1h", recent_days=60,
                                   min_trades=100000)
    assert not dead and "idle" in ev["verdict"]


def test_unpowered_window_never_reads_no_edge_and_unscored_stays_unlabelled():
    res = {"verdict": "scored", "consistency_p": 0.4}
    assert control.label(res, is_powered=False) == "underpowered"
    for v in ("empty", "untested", "untestable", "error"):
        assert control.label({"verdict": v, "consistency_p": None},
                             is_powered=True) == v
    assert control.label(None, is_powered=True) == "error"
    assert not control.powered(None) and not control.powered({})

