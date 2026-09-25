"""SDD-STAGE-3-REPEATED-REJECTION-SIGNAL-OCCURRENCE-OBSERVATION-V1.

Read-only, deterministic observation of persisted decisions per exact
strategy-signal-occurrence.v1 key. Counts are scan observations of
decision-level outcomes — never signal-caused refusals, opportunities,
episodes or salience.
"""
import ast
import hashlib
import json
import random
import sqlite3
from pathlib import Path

import pytest

from trader.core import reason_codes as rc
from trader.core.journal import Journal
from trader.core.types import Action, Decision
from trader.strategy import signal_occurrence as so
from trader.strategy import signal_occurrence_observation as obs

ROOT = Path(__file__).resolve().parents[1]
H4 = 4 * 3600 * 1000
BAR = 1_767_225_600_000          # 2026-01-01T00:00Z


def sig(bar=BAR, spec="s1", fp="f" * 64, symbol="BTC/USDT", action="BUY",
        tf="4h"):
    return {"strategy_id": f"spec:{spec}", "symbol": symbol, "action": action,
            "params": {"spec_id": spec, "spec_fingerprint": fp,
                       "signal_timeframe": tf, "signal_bar_close_ms": bar}}


def row(did, ts, sigs, executed=0, codes=None, version="auto",
        action="BUY", symbol="BTC/USDT", scan="scan"):
    if version == "auto":
        version = rc.VERSION if codes is not None else None
    return {"id": did, "ts": ts, "scan_id": f"{scan}-{did}", "symbol": symbol,
            "action": action, "executed": executed,
            "reason_codes": None if codes is None else
            (codes if isinstance(codes, str) else json.dumps(codes)),
            "reason_codes_version": version,
            "signals_json": sigs if isinstance(sigs, str) or sigs is None
            else json.dumps(sigs)}


REFUSED = [rc.STRATEGY_NO_SIGNAL]
T = "2026-01-01T04:0{}:00+00:00"


def only(out):
    assert len(out["occurrences"]) == 1
    return out["occurrences"][0]


# 1
def test_repeated_scans_of_one_occurrence_aggregate_to_one_key():
    out = obs.observe([row(f"d{i}", T.format(i), [sig()], codes=REFUSED)
                       for i in range(5)])
    o = only(out)
    assert o["n_observing_decisions"] == 5 == o["n_coded_refusal"]
    assert (o["spec_id"], o["symbol"], o["action"], o["signal_timeframe"],
            o["signal_bar_close_ms"]) == ("s1", "BTCUSDT", "BUY", "4h", BAR)
    assert o["first_observed_ts"] == T.format(0)
    assert o["last_observed_ts"] == T.format(4)
    assert out["counts_are"] == "scan_observations"


# 2
def test_consecutive_signal_bars_remain_different_keys():
    out = obs.observe([row("d1", T.format(1), [sig(BAR)], codes=REFUSED),
                       row("d2", T.format(2), [sig(BAR + H4)], codes=REFUSED)])
    assert [o["signal_bar_close_ms"] for o in out["occurrences"]] == \
        [BAR, BAR + H4]
    assert all(o["n_observing_decisions"] == 1 for o in out["occurrences"])


# 3
@pytest.mark.parametrize("other", [
    dict(fp="e" * 64), dict(symbol="ETH/USDT"), dict(action="SELL"),
    dict(tf="1h"), dict(spec="s2")])
def test_identity_fields_separate_keys(other):
    out = obs.observe([row("d1", T.format(1), [sig()], codes=[]),
                       row("d2", T.format(2), [sig(**other)], codes=[])])
    assert len(out["occurrences"]) == 2


# 4
def test_executed_sets_executed_ever_and_is_not_refusal():
    out = obs.observe([
        row("d1", T.format(1), [sig()], codes=REFUSED),
        row("d2", T.format(2), [sig()], executed=1, codes=REFUSED)])
    o = only(out)
    assert o["executed_ever"] is True
    assert o["n_executed"] == 1 and o["n_coded_refusal"] == 1
    assert o["history"][1]["decision_outcome"] == obs.EXECUTED
    # codes stay visible as persisted, but do not reclassify the row
    assert o["history"][1]["reason_codes"] == REFUSED


# 5, 6, 7
def test_classification_by_executed_and_codes():
    out = obs.observe([
        row("d1", T.format(1), [sig()], codes=REFUSED),
        row("d2", T.format(2), [sig()], codes=[]),
        row("d3", T.format(3), [sig()], codes=None)])
    o = only(out)
    assert (o["n_coded_refusal"], o["n_uncoded_non_execution"],
            o["n_codes_not_recorded"], o["n_executed"]) == (1, 1, 1, 0)
    assert o["executed_ever"] is False
    states = [(h["reason_codes_state"], h["reason_codes"])
              for h in o["history"]]
    assert states == [(obs.CODES_RECORDED, REFUSED), (obs.CODES_RECORDED, []),
                      (obs.CODES_NULL, None)]


# 8
@pytest.mark.parametrize("codes,version,why", [
    ("not json", rc.VERSION, obs.REASON_CODES_UNDECODABLE),
    ('{"a": 1}', rc.VERSION, obs.REASON_CODES_NOT_LIST),
    ("[1]", rc.VERSION, obs.REASON_CODES_NON_STRING),
    ('["made_up_code"]', rc.VERSION, obs.REASON_CODES_UNKNOWN_CODE),
    ("[]", "decision-rejection-reason.v0", obs.REASON_CODES_VERSION_UNSUPPORTED),
    ("[]", None, obs.REASON_CODES_VERSION_UNSUPPORTED),
    (None, rc.VERSION, obs.REASON_CODES_VERSION_WITHOUT_CODES)])
def test_malformed_reason_codes_are_not_null(codes, version, why):
    r = row("d1", T.format(1), [sig()])
    r["reason_codes"], r["reason_codes_version"] = codes, version
    out = obs.observe([r])
    o = only(out)
    assert o["n_codes_not_recorded"] == 0 == o["n_uncoded_non_execution"]
    assert o["n_invalid_outcome_evidence"] == 1
    h = o["history"][0]
    assert h["reason_codes_state"] == obs.CODES_INVALID
    assert h["reason_codes_invalid"] == why
    assert out["invalid_source_evidence"] == [
        {"decision_id": "d1", "ts": T.format(1), "field": "reason_codes",
         "reason": why}]
    # the tolerant journal decoder would have called these "not recorded"
    if codes is not None and version == rc.VERSION and why in (
            obs.REASON_CODES_UNDECODABLE, obs.REASON_CODES_NOT_LIST):
        assert rc.decode(codes) is None


@pytest.mark.parametrize("codes,version", [
    ("not json", rc.VERSION), ('["made_up_code"]', rc.VERSION),
    ("[]", "decision-rejection-reason.v0"), (None, rc.VERSION)])
def test_executed_row_with_malformed_codes_stays_executed(codes, version):
    r = row("d1", T.format(1), [sig()], executed=1)
    r["reason_codes"], r["reason_codes_version"] = codes, version
    out = obs.observe([r])
    o = only(out)
    assert o["n_executed"] == 1 and o["executed_ever"] is True
    assert (o["n_coded_refusal"], o["n_uncoded_non_execution"],
            o["n_codes_not_recorded"], o["n_invalid_outcome_evidence"]) == \
        (0, 0, 0, 0)
    h = o["history"][0]
    assert h["decision_outcome"] == obs.EXECUTED and h["executed"] is True
    assert h["reason_codes_state"] == obs.CODES_INVALID
    assert h["reason_codes"] is None            # malformed codes not trusted
    assert h["reason_codes_invalid"]
    assert out["invalid_source_evidence"] == [
        {"decision_id": "d1", "ts": T.format(1), "field": "reason_codes",
         "reason": h["reason_codes_invalid"]}]


@pytest.mark.parametrize("codes", [REFUSED, [], None])
def test_executed_never_enters_non_execution_buckets(codes):
    o = only(obs.observe([row("d1", T.format(1), [sig()], executed=1,
                              codes=codes)]))
    assert o["n_executed"] == 1 == o["n_observing_decisions"]
    assert (o["n_coded_refusal"], o["n_uncoded_non_execution"],
            o["n_codes_not_recorded"], o["n_invalid_outcome_evidence"]) == \
        (0, 0, 0, 0)


@pytest.mark.parametrize("executed", [None, 2, -1, "1", 1.0, True])
def test_invalid_executed_value_is_invalid_and_never_executed(executed):
    out = obs.observe([row("d1", T.format(1), [sig()], executed=executed,
                           codes=[])])
    o = only(out)
    assert o["n_invalid_outcome_evidence"] == 1
    assert o["n_executed"] == 0 and o["executed_ever"] is False
    assert out["invalid_source_evidence"][0]["reason"] == \
        obs.EXECUTED_UNREADABLE


def test_unreadable_executed_is_invalid_not_refusal():
    out = obs.observe([row("d1", T.format(1), [sig()], executed=None,
                           codes=REFUSED)])
    o = only(out)
    assert o["n_invalid_outcome_evidence"] == 1 and o["n_coded_refusal"] == 0
    assert out["invalid_source_evidence"][0]["reason"] == obs.EXECUTED_UNREADABLE


@pytest.mark.parametrize("raw,why", [
    ("[{", obs.SIGNALS_JSON_UNDECODABLE),
    ('{"symbol": "BTC/USDT"}', obs.SIGNALS_JSON_NOT_LIST),
    (None, obs.SIGNALS_JSON_NOT_RECORDED)])
def test_malformed_signals_json_creates_no_occurrence(raw, why):
    out = obs.observe([row("d1", T.format(1), raw, codes=REFUSED)])
    assert out["occurrences"] == [] and out["keyless_signals"] == {}
    assert out["invalid_source_evidence"][0]["reason"] == why


# 9, 14
def test_history_order_and_output_are_row_order_independent():
    rows = [row(f"d{i:02d}", T.format(i % 3), [sig(), sig(action="SELL")],
                codes=[REFUSED, [], None][i % 3]) for i in range(12)]
    rows.append(row("zbad", T.format(0), "[{", codes=[]))
    rows.append(row("zbad2", T.format(0), [sig()], codes="oops"))
    ref = obs.observe(rows)
    for seed in range(10):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert obs.observe(shuffled) == ref
    for o in ref["occurrences"]:
        keys = [(h["ts"], h["decision_id"]) for h in o["history"]]
        assert keys == sorted(keys)
    assert json.dumps(ref, sort_keys=True)          # plain, serialisable


# 10
def test_multi_signal_decision_contributes_unsplit_to_each_occurrence():
    codes = [rc.STRATEGY_NO_SIGNAL, rc.FENCE_STATE_HALTED]
    out = obs.observe([row("d1", T.format(1),
                           [sig(), sig(action="SELL"), sig(spec="s2")],
                           action="HOLD", codes=codes)])
    assert len(out["occurrences"]) == 3
    for o in out["occurrences"]:
        assert o["n_coded_refusal"] == 1 and o["n_observing_decisions"] == 1
        h = o["history"][0]
        assert h["reason_codes"] == codes              # unsplit, unallocated
        assert h["decision_action"] == "HOLD"          # disagreement visible
        assert h["n_occurrences_in_decision"] == 3
    # no field assigns a code or a cause to a signal
    blob = json.dumps(out)
    for word in ("caused", "cause", "attributed", "blame"):
        assert word not in blob


# 11
def test_duplicate_occurrence_in_one_row_counts_one_decision():
    out = obs.observe([row("d1", T.format(1), [sig(), sig(), sig(
        symbol="BTC/USDT:USDT")], codes=REFUSED)])
    o = only(out)
    assert o["n_observing_decisions"] == 1 == o["n_coded_refusal"]
    assert out["n_duplicate_occurrence_entries"] == 2


# 12
def test_keyless_signals_are_counted_by_reason_and_never_grouped():
    legacy = {"strategy_id": "ema", "symbol": "BTC/USDT", "action": "BUY",
              "params": {}, "signal_bar_age_min": 3}
    no_close = sig() | {"params": {"signal_bar_close_ms": None,
                                   "signal_occurrence_unavailable":
                                   so.ENTRY_SERIES_MISALIGNED}}
    bad = sig(bar="123")
    out = obs.observe([
        row("d1", T.format(1), [legacy, no_close, bad, "junk",
                                sig() | {"action": {}}], codes=REFUSED),
        row("d2", T.format(2), [legacy], codes=REFUSED)])
    assert out["occurrences"] == []
    assert out["keyless_signals"] == {
        so.ENTRY_SERIES_MISALIGNED: {"n_signal_entries": 1, "n_decisions": 1},
        so.INVALID_OCCURRENCE_FIELDS: {"n_signal_entries": 1, "n_decisions": 1},
        so.NO_CLOSED_BAR_IDENTITY: {"n_signal_entries": 2, "n_decisions": 2},
        obs.SIGNAL_ENTRY_NOT_OBJECT: {"n_signal_entries": 1, "n_decisions": 1},
        obs.SIGNAL_ENTRY_UNREADABLE: {"n_signal_entries": 1, "n_decisions": 1}}


# 13
def test_symbol_aliases_collapse_through_occurrence_identity():
    out = obs.observe([
        row("d1", T.format(1), [sig(symbol="BTC/USDT")], codes=[]),
        row("d2", T.format(2), [sig(symbol="BTC/USDT:USDT")], codes=[]),
        row("d3", T.format(3), [sig(symbol="BTCUSDT")], codes=[])])
    o = only(out)
    assert o["symbol"] == "BTCUSDT" and o["n_observing_decisions"] == 3


# 15
def _digest(path):
    con = sqlite3.connect(path)
    dump = "\n".join(con.iterdump())
    con.close()
    return hashlib.sha256(Path(path).read_bytes()).hexdigest(), dump


def test_journal_accessor_is_read_only(tmp_path):
    db = tmp_path / "j.db"
    j = Journal(db)
    with j._tx() as c:
        c.execute("INSERT INTO cycles(id,ts,symbol) VALUES ('c','t','BTC/USDT')")
    for i, codes in enumerate([REFUSED, [], None]):
        d = Decision(f"d{i}", "c", "BTC/USDT", Action.BUY, .5, .2, .6, [],
                     [sig()], ts=T.format(i))
        d.reason_codes = codes
        j.log_decision(d)
    with j._tx() as c:        # a legacy-malformed row, as found on disk
        c.execute("UPDATE decisions SET reason_codes='garbage' WHERE id='d0'")
    before = _digest(db)
    out = obs.observe_journal(j)
    out2 = obs.observe_journal(j)
    assert _digest(db) == before and out == out2
    o = only(out)
    assert (o["n_invalid_outcome_evidence"], o["n_uncoded_non_execution"],
            o["n_codes_not_recorded"]) == (1, 1, 1)
    assert set(j.decision_observation_rows()[0]) == set(obs.COLUMNS)


# 16
def test_no_trigger_salience_threshold_or_writes():
    src = (ROOT / "trader/strategy/signal_occurrence_observation.py").read_text()
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    code = src.split('"""', 2)[2].lower()       # past the docstring
    for word in ("salience", "threshold", "trigger", "opportunit", "episode",
                 "rank", "insert", "update ", "delete", "_tx", "execute("):
        assert word not in code, word
    assert not {"attention", "risk", "executor"} & {n.lower() for n in names}
    imports = {n.module for n in ast.walk(tree)
               if isinstance(n, ast.ImportFrom)}
    assert imports <= {"__future__", "core.reason_codes", None}
