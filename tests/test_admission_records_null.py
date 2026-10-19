"""Percentile summaries are diagnostic; current admission separately requires
dependence-corrected and common-rotation gates. Missing summaries remain null.
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
