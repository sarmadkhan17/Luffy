"""RP-01: eligible new investigation cases complete the research pipeline.

Synthetic fixtures only (no network, LLM, provider, production store). Legacy
cases are modelled by removing the registration context a case never had; no
context or evidence is ever fabricated for them.
"""
import json
import sqlite3

from trader.cognition import investigation as I
from trader.observability import investigation as C
from trader.observability import investigation_research as R
from trader.research import predictive_bridge as B
from trader.research.universe import DISCOVERY, HELDOUT
from trader.core.journal import Journal
from tests.test_attention_telemetry import frames
from tests.test_intelligence_spine import publish
from tests.test_investigation_state_feedback import paths  # noqa: F401  (fixture)
from tests.test_investigation_research_family import registered, measured, last_update, table, records
from tests.test_predictive_research_bridge import NumericalFixture, CFG, saved


def _advance(src, dest, case, steps):
    """Observe `steps` further 4h scans without completing the target window."""
    start = (case.registered_ms // I.TF + 1) * I.TF
    for k in range(1, steps + 1):
        t = start + k * I.TF + 1
        publish(src, t, sid=f"s{k+1}", spikes=(), data=frames(6, t))
        C.step(src, dest, t)
        yield t


def _legacy(dest, iid):
    """The state of a case registered before contexts were persisted."""
    with sqlite3.connect(dest) as db:
        db.execute("DELETE FROM opportunity_contexts WHERE investigation_id=?", (iid,))
        db.execute("DELETE FROM intelligence_traces WHERE investigation_id=?", (iid,))


def _candles(tmp_path, start):
    path = tmp_path / "candles.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,"
                   "close REAL,volume REAL,taker_buy REAL,PRIMARY KEY(symbol,tf,ts))")
        for sym in DISCOVERY + HELDOUT:
            db.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                           [(sym, "4h", start + i * I.TF, 100 + i * .01, 101 + i * .01, 99 + i * .01,
                             100.1 + i * .01, 100 + (i % 37) ** 2, 50) for i in range(2100)])
    from tests.retained_candle_fixtures import qualify
    qualify(path)
    return path


def test_legacy_case_without_context_never_starves_a_new_eligible_case(paths):  # noqa: F811
    src, dest, now = paths
    iid, case = registered(paths)
    _legacy(dest, iid)                      # oldest case: no registration context
    # A newer eligible case is registered after the legacy one terminates.
    end = case.measurement.deadline_ms
    data = frames(6, end + 1)
    publish(src, end + 1, sid="s2", spikes=(), data=data)
    C.step(src, dest, end + 1)
    publish(src, end + 2 * I.TF, sid="s3", spikes=(0,), data=frames(6, end + 2 * I.TF))
    C.step(src, dest, end + 2 * I.TF)
    ids = [r[0] for r in table(dest, "SELECT id FROM cases ORDER BY created_ms,id")]
    assert len(ids) >= 2 and ids[0] == iid
    before = table(dest, "SELECT COUNT(*) FROM opportunity_contexts")
    out = R.research_pass(dest, recorded_at_ms=end + 3 * I.TF, max_cases=1)
    assert out == {"attempted": 1, "ok": 1, "refused": {}}, out
    assert R.chain(dest, iid) is None                     # never researched, never invented
    assert R.run(dest, iid, recorded_at_ms=end)["reason"] == (
        "registration_context_unverified:replay_context_missing")
    spare = R.research_pass(dest, recorded_at_ms=end + 4 * I.TF, max_cases=8)
    assert spare["refused"] == {"registration_context_unverified:replay_context_missing": 1}
    assert table(dest, "SELECT COUNT(*) FROM opportunity_contexts") == before   # not backfilled
    assert all(r[0] != iid for r in table(dest, "SELECT investigation_id FROM investigation_research_records"))


def test_unresolved_updates_are_never_experiment_sources_or_spend_looks(paths, tmp_path, monkeypatch):  # noqa: F811
    src, dest, now = paths
    from trader.research.ledger import Ledger
    Ledger(Journal(tmp_path / "luffy.db")).ensure()
    iid, case = registered(paths)
    R.research_pass(dest, recorded_at_ms=now + 1, max_cases=8)
    for t in _advance(src, dest, case, 3):
        R.research_pass(dest, recorded_at_ms=t + 1, max_cases=8)
    assert len([r for r in records(dest) if r[0] == R.BANK_SCHEMA]) == 4   # interim evidence kept
    start = (case.registered_ms // I.TF + 1) * I.TF
    candles = _candles(tmp_path, start)
    later = start + 2100 * I.TF
    monkeypatch.setattr("trader.data.feed.time.time", lambda: later / 1000)
    fake, bank = NumericalFixture("SUPPORTED"), tmp_path / "bridge.db"
    for at in (now + 2, later, later + 1):
        out = B.cycle(dest, bank, candles, CFG, now_ms=at, run=fake, max_sources=16, max_experiments=4)
        assert out["counts"]["eligible_research_results"] == 0 and out["refusals"] == []
    assert fake.calls == []
    assert saved(bank, "bridge_sources") == saved(bank, "bridge_hypotheses") == []
    assert saved(bank, "bridge_experiments") == [] and saved(bank, "bridge_tests") == []


def test_terminal_result_is_one_source_with_exact_identity_and_replays(paths, tmp_path, monkeypatch):  # noqa: F811
    src, dest, now = paths
    from trader.research.ledger import Ledger
    Ledger(Journal(tmp_path / "luffy.db")).ensure()
    iid, case = registered(paths)
    R.research_pass(dest, recorded_at_ms=now + 1, max_cases=8)
    for t in _advance(src, dest, case, 2):
        R.research_pass(dest, recorded_at_ms=t + 1, max_cases=8)
    end = case.measurement.deadline_ms
    data = frames(6, end + 1)
    data["S0/USDT"]["4h"].loc[25:29, "volume"] = 10_000
    publish(src, end + 1, sid="s9", spikes=(), data=data)
    C.step(src, dest, end + 1)
    assert last_update(dest, iid).evidence.status == "measured"
    passed = R.research_pass(dest, recorded_at_ms=end + 2, max_cases=8)
    assert passed == {"attempted": 1, "ok": 1, "refused": {}}
    chain = R.chain(dest, iid)
    assert [r["result"]["status"] for r in chain["runs"]] == ["INCONCLUSIVE"] * 3 + ["SUPPORTED"]
    first = records(dest)
    assert R.chain(dest, iid) == chain                       # deterministic replay
    assert R.research_pass(dest, recorded_at_ms=end + 9, max_cases=8)["attempted"] == 0
    assert records(dest) == first                            # restart: nothing re-registered
    candles = _candles(tmp_path, (end // I.TF + 1) * I.TF)
    later = (end // I.TF + 1) * I.TF + 2100 * I.TF
    monkeypatch.setattr("trader.data.feed.time.time", lambda: later / 1000)
    fake, bank = NumericalFixture("SUPPORTED"), tmp_path / "bridge.db"
    original = dest.read_bytes()
    B.cycle(dest, bank, candles, CFG, now_ms=end + 3, run=fake, max_sources=16, max_experiments=4)
    for j in range(7):
        B.cycle(dest, bank, candles, CFG, now_ms=later + j, run=fake, max_sources=16, max_experiments=4)
    (source,) = saved(bank, "bridge_sources")
    terminal = chain["runs"][-1]
    assert source["bank_id"] == terminal["bank"]["bank_object_id"]
    assert source["investigation_id"] == iid and source["result_id"] == terminal["result"]["result_id"]
    ctx = json.loads(source["context_json"])
    assert ctx["attention"]["scan_id"] == chain["question"]["context"]["attention"]["scan_id"]
    assert len(saved(bank, "bridge_experiments")) == 1
    assert dest.read_bytes() == original                    # source ledger never mutated by the bridge
    calls = len(fake.calls)
    B.cycle(dest, bank, candles, CFG, now_ms=later + 50, run=fake, max_sources=16, max_experiments=4)
    assert len(fake.calls) == calls                         # no second look after restart
    assert len(saved(bank, "bridge_tests")) <= 1
