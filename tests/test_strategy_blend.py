"""Weighted combination of the active strategy set.

The orchestrator already COMBINES — it sums every eligible strategy's signal
alongside the analyst votes. But it summed them at a flat
`STRATEGY_VOTE_WEIGHT`, so a strategy printing PF 1.8 in the live regime
counted exactly as much as one limping at 0.9. Analysts were weighted by
measured accuracy and regime fit from the start; strategies were not.

That is what these cover: the set stays a set, and the members are weighed by
what they are actually doing NOW.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from trader.strategy.blend import (F_MAX, F_MIN, MAX_W, MIN_W, PRIOR_TRADES,
                                   _shrink, performance_multiplier,
                                   regime_multiplier, strategy_weights)


class _J:
    """Journal stub: closed trades per strategy."""

    def __init__(self, trades=None):
        self._t = trades or {}

    def trades_for_strategy(self, sid):
        return self._t.get(sid, [])


def _trades(wins, losses, win=100.0, loss=-100.0):
    return ([{"realized_pnl": win, "status": "closed"}] * wins +
            [{"realized_pnl": loss, "status": "closed"}] * losses)


# ── performance ──────────────────────────────────────────────────────────
def test_no_history_is_neutral():
    """An untraded strategy is not punished — it is simply unproven."""
    assert performance_multiplier([]) == 1.0


def test_a_winning_record_lifts_the_weight():
    assert performance_multiplier(_trades(30, 10)) > 1.0


def test_a_losing_record_cuts_the_weight():
    assert performance_multiplier(_trades(10, 30)) < 1.0


def test_a_tiny_sample_barely_moves_the_weight():
    """Two lucky trades are not evidence. Shrinkage toward neutral is what
    stops the book chasing noise."""
    small = performance_multiplier(_trades(2, 0))
    big = performance_multiplier(_trades(60, 0))
    assert 1.0 < small < big


def test_shrinkage_is_worth_half_at_the_prior():
    """The documented property: at n == prior, evidence counts half."""
    assert _shrink(2.0, PRIOR_TRADES, PRIOR_TRADES) == pytest.approx(1.5)
    assert _shrink(2.0, 0.0, PRIOR_TRADES) == pytest.approx(1.0)


def test_a_single_factor_cannot_pin_the_weight_at_the_ceiling():
    """If one factor alone saturates, every strong strategy collapses to the
    same weight and the ordering inside the set is lost."""
    assert performance_multiplier(_trades(500, 1)) <= F_MAX < MAX_W


def test_an_all_losing_record_is_floored_not_negative():
    assert performance_multiplier(_trades(0, 40)) >= F_MIN


def test_open_trades_are_ignored():
    """Unrealized P&L is not a result."""
    assert performance_multiplier(
        [{"realized_pnl": None, "status": "open"}] * 5) == 1.0


# ── regime fit ───────────────────────────────────────────────────────────
def test_no_measured_evidence_is_neutral():
    assert regime_multiplier({}, "RANGING") == 1.0


def test_a_regime_the_strategy_pays_in_lifts_it():
    ev = {"RANGING": {"hit_rate": 0.8, "median_pf": 1.6, "windows": 8}}
    assert regime_multiplier(ev, "RANGING") > 1.0


def test_a_regime_it_loses_in_cuts_it():
    ev = {"VOLATILE": {"hit_rate": 0.2, "median_pf": 0.6, "windows": 8}}
    assert regime_multiplier(ev, "VOLATILE") < 1.0


def test_evidence_for_another_regime_does_not_transfer():
    ev = {"RANGING": {"hit_rate": 0.9, "median_pf": 2.0, "windows": 9}}
    assert regime_multiplier(ev, "TRENDING_UP") == 1.0


def test_too_few_windows_is_not_evidence():
    ev = {"RANGING": {"hit_rate": 1.0, "median_pf": 3.0, "windows": 1}}
    assert regime_multiplier(ev, "RANGING") == 1.0


# ── combined weights ─────────────────────────────────────────────────────
class _S:
    def __init__(self, sid, evidence=None):
        self.id = sid
        self.provenance = {"regime_evidence": evidence} if evidence else {}


def test_weights_are_returned_per_strategy_id():
    j = _J({"a": _trades(30, 10), "b": _trades(10, 30)})
    w = strategy_weights(j, [_S("a"), _S("b")], "RANGING")
    assert set(w) == {"a", "b"}
    assert w["a"] > w["b"]


def test_every_weight_stays_inside_the_bounds():
    j = _J({"a": _trades(200, 0), "b": _trades(0, 200)})
    w = strategy_weights(j, [_S("a"), _S("b")], "RANGING")
    assert all(MIN_W <= v <= MAX_W for v in w.values())


def test_the_set_is_never_reduced_to_one_member():
    """The whole point: combine the set, do not pick a winner. Even a poor
    performer keeps a positive weight and still speaks."""
    j = _J({"good": _trades(80, 5), "bad": _trades(2, 40)})
    w = strategy_weights(j, [_S("good"), _S("bad")], "RANGING")
    assert len(w) == 2
    assert w["bad"] > 0


def test_performance_and_regime_fit_compound():
    ev = {"RANGING": {"hit_rate": 0.85, "median_pf": 1.8, "windows": 9}}
    j = _J({"a": _trades(12, 8), "b": _trades(12, 8)})
    w = strategy_weights(j, [_S("a", ev), _S("b")], "RANGING")
    assert w["a"] > w["b"]


def test_a_broken_journal_degrades_to_neutral_weights():
    class _Broken:
        def trades_for_strategy(self, sid):
            raise RuntimeError("db gone")

    w = strategy_weights(_Broken(), [_S("a")], "RANGING")
    assert w == {"a": 1.0}
