"""Offline persisted refusal evidence must recognize the producer vocabulary."""
import pytest

from trader.core import reason_codes as rc
from trader.core.journal import Journal
from trader.core.types import Action, Decision, Snapshot
from trader.strategy.signal_occurrence_observation import observe_journal


@pytest.mark.parametrize("code, expected_invalid", [
    (rc.VERSIONED_AUTHORITY_REQUIRED, False),
    ("unknown_future_code", True),
])
def test_authority_refusal_roundtrip(tmp_path, code, expected_invalid):
    journal = Journal(tmp_path / "journal.db")
    journal.log_cycle(Snapshot("BTC/USDT", "2026-10-11T00:00:00+00:00", 100, {}),
                      "cycle", "paper")
    signal = dict(strategy_id="spec:example", symbol="BTC/USDT", action="BUY",
                  params=dict(spec_id="example", spec_fingerprint="fp",
                              signal_timeframe="4h", signal_bar_close_ms=1791676800000))
    decision = Decision("decision", "cycle", "BTC/USDT", Action.BUY,
                        .8, .5, .8, [], [signal])
    decision.reason_codes = [code]
    decision.skip_reason = "version fence: " + code
    journal.log_decision(decision)
    result = observe_journal(journal)
    occurrence = result["occurrences"][0]
    assert occurrence["n_invalid_outcome_evidence"] == int(expected_invalid)
    assert occurrence["n_coded_refusal"] == int(not expected_invalid)
    assert bool(result["invalid_source_evidence"]) == expected_invalid
    assert journal.decision_observation_rows()[0]["executed"] == 0
