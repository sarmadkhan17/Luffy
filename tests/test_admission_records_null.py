"""Admission must record whether a candidate beats its own signals.

`Analyst.admit` gates on a 90-day pooled profit factor >= 1.15 and signal
overlap. That is the same bare arithmetic that scores an ALWAYS-LONG rule at
PF 1.28 on market drift, so it cannot tell a mechanism from a market
direction — and it admitted spec_momentum_divergence_trail, which then
measured at worst-symbol OOS PF 0.67, robust on 0 of 5 symbols, and beating a
rotation of its own entries on only 1 of 5.

The null is recorded rather than gated on. With 60 draws a single symbol's
percentile is noisy, and the admitted strategy's own per-symbol figures
(0.70-0.88) do not separate cleanly from the rejected one's (0.60-1.00); a
threshold picked to split those two would be fitted to them, not measured.
Recording it puts the number in front of the 6h review and the operator,
which is what it can honestly support today.
"""
from trader.brain.analyst import Analyst


def test_admission_evidence_carries_a_null_percentile_field():
    ev = {"chosen_timeframe": "4h", "pooled_pf": 1.4, "per_symbol": {}}
    out = Analyst._with_null_evidence(ev, {"BTC/USDT": 0.7, "ETH/USDT": 0.95})
    assert out["null_median_percentile"] == 0.825
    assert out["null_beats_90pct_on"] == "1/2"


def test_no_null_measurements_records_none_rather_than_a_guess():
    out = Analyst._with_null_evidence({"pooled_pf": 1.2}, {})
    assert out["null_median_percentile"] is None
    assert out["null_beats_90pct_on"] == "0/0"


def test_the_original_evidence_is_preserved():
    ev = {"chosen_timeframe": "1h", "pooled_pf": 1.3}
    out = Analyst._with_null_evidence(ev, {"BTC/USDT": 0.5})
    assert out["chosen_timeframe"] == "1h" and out["pooled_pf"] == 1.3


def test_it_does_not_mutate_the_evidence_it_was_given():
    ev = {"pooled_pf": 1.3}
    Analyst._with_null_evidence(ev, {"BTC/USDT": 0.5})
    assert "null_median_percentile" not in ev
