"""RP-03: Research -> Referee -> Factory handoff, two complete ledgers.

All evidence is the synthetic protocol fixture of test_predictive_research_bridge
(deterministic child receipts through the real planner, controls, LORD++ and
referee disposition writer). Nothing here is claimed to be a real edge, no
protected held-out is read and no alpha is spent: the looks that are copied were
"spent" inside the synthetic experiment.
"""
import json
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from tests import test_predictive_research_bridge as BT
from tests.test_predictive_research_bridge import CFG, NumericalFixture, saved
from trader.core.journal import Journal
from trader.research import factory_transfer as T
from trader.research import predictive_bridge as B
from trader.research.ledger import Ledger
from trader.strategy import factory_handoff as F


def _build(root: Path, disposition: str) -> dict:
    """The population fixture of the bridge tests, built once per module: a
    real Bank plus the experiment's own quantitative ledger."""
    import socket
    import time
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(socket.socket, "connect",
                   lambda *a: pytest.fail("network forbidden"))
        now = int(time.time() * 1000) // BT.I.TF * BT.I.TF + 60_000
        paths = (root / "attention.db", root / "investigation.db", now)
        Ledger(Journal(root / "luffy.db")).ensure()
        _, source, _ = paths
        iid, end = BT.measured(paths, "same_direction")
        result = BT.R.run(source, iid, recorded_at_ms=end + 2)
        assert result["status"] == "OK"
        chain = BT.R.chain(source, iid)
        s = BT.P.source_from_chain(chain, chain["runs"][-1], end + 2)
        candles = root / "candles.db"
        start = (end // BT.I.TF + 1) * BT.I.TF
        with sqlite3.connect(candles) as db:
            db.execute("CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,"
                       "open REAL,high REAL,low REAL,close REAL,volume REAL,"
                       "taker_buy REAL,PRIMARY KEY(symbol,tf,ts))")
            for sym in BT.DISCOVERY + BT.HELDOUT:
                db.executemany(
                    "INSERT INTO candles VALUES (?,?,?,?,?,?,?,?,?)",
                    [(sym, "4h", start + i * BT.I.TF, 100 + i * .01,
                      101 + i * .01, 99 + i * .01, 100.1 + i * .01,
                      100 + (i % 37) ** 2, 50) for i in range(1800)])
        from tests.retained_candle_fixtures import qualify
        qualify(candles)
        mp.setattr("trader.data.feed.time.time",
                   lambda: (start + 1800 * BT.I.TF) / 1000)
        pop = (source, root / "bridge.db", candles, end + 3,
               start + 1800 * BT.I.TF, s)
        fake = NumericalFixture(disposition)
        BT.consume(pop, fake, pop[3])
        for _ in range(6):
            BT.consume(pop, fake, pop[4])
        return {"root": root, "later": pop[4]}
    finally:
        mp.undo()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return {d: _build(tmp_path_factory.mktemp("rp03-" + d.lower()), d)
            for d in ("SUPPORTED", "UNSUPPORTED", "DATA_INSUFFICIENT")}


def _copy(built, disposition, dst: Path) -> Path:
    shutil.copytree(built[disposition]["root"], dst, ignore=shutil.ignore_patterns(
        "candles.db*", "attention*", "investigation*"))
    return dst / "bridge.db"


@pytest.fixture
def population(built, tmp_path):
    bank = _copy(built, "SUPPORTED", tmp_path / "w")
    return bank, built["SUPPORTED"]["later"]


def _supported(population):
    bank, later = population
    final = next(r for r in saved(bank, "bridge_bank_results")
                 if r["classification"] == "SUPPORTED" and r["experiment_id"])
    return bank, bank.parent / "luffy.db", final, later


def _other(built, tmp_path, disposition):
    bank = _copy(built, disposition, tmp_path / "o")
    return bank, [r for r in saved(bank, "bridge_bank_results")
                  if r["experiment_id"]]


def _dump(path):
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        return {t: db.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
                for (t,) in db.execute("SELECT name FROM sqlite_master "
                                       "WHERE type='table' AND name NOT LIKE "
                                       "'sqlite_%'").fetchall()}


@pytest.fixture
def target(tmp_path):
    j = Journal(tmp_path / "factory-journal.db")
    Ledger(j).ensure()
    return j


# ── the baseline defect, proven ─────────────────────────────────────────
def test_baseline_factory_cannot_see_the_experiment_evidence(population,
                                                             target):
    bank, prod, final, later = _supported(population)
    cand = final["referee_disposition"]["factory_candidate"]
    # the spent look lives only in the experiment ledger and the Bank ...
    with closing(sqlite3.connect(f"file:{cand['quantitative_ledger']}?mode=ro",
                                 uri=True)) as exp_j:
        assert len(exp_j.execute("SELECT * FROM research_tests").fetchall()) == 1
    assert len(saved(bank, "bridge_tests")) == 1
    # ... not in the Journal a production consumer prices alpha against
    assert target.query("SELECT * FROM research_tests") == []
    assert Journal(prod).query("SELECT * FROM research_tests") == []
    with pytest.raises(F.HandoffRefused, match="candidate_not_found"):
        F.create_version(target, CFG, B.factory_source(final, bank),
                         at_ms=later)
    # and the Factory refuses a bare candidate without its lineage
    with pytest.raises(F.HandoffRefused, match="candidate_not_found"):
        F.create_version(target, CFG, {"kind": "research_candidate",
                                       "hash": cand["hash"]}, at_ms=later)


# ── positive: authenticated transfer, then the unchanged Factory contract ─
def test_supported_result_transfers_and_factory_validates(population, target):
    bank, _prod, final, later = _supported(population)
    packet = T.authenticate(bank, final["result_id"])
    assert packet["hash"] == final["referee_disposition"][
        "factory_candidate"]["hash"]
    bank_before = _dump(bank)
    out = T.transfer(target, packet)
    assert out["status"] == "inserted" and out["rows_written"] == 4
    assert _dump(bank) == bank_before            # bank untouched
    led = Ledger(target)
    assert led.candidate(packet["hash"])["state"] == "referee_passed"
    assert len(led.tests()) == 1
    assert led.spent(packet["hash"])["seq"] == packet["look_seq"]
    assert led.spent_cut("4h") == json.loads(packet["tests"][0]["detail"])["cut_ms"]
    ver = F.create_version(target, CFG, B.factory_source(final, bank),
                           at_ms=later)
    assert ver["status"] == "inserted"
    rec = F.load_version(target, ver["version_id"])
    assert rec["lineage"]["research"]["result_id"] == final["result_id"]
    assert rec["lineage"]["research"]["bank_authentication"] \
        == "REPLAYED_AGAINST_REGISTERED_LEDGER"
    # nothing past VALIDATED: no install, probation, approval or activation
    assert F.state_of(target, ver["version_id"]) == F.VALIDATED
    for t in ("strategy_version_installs", "strategy_probation_receipts",
              "strategy_approval_requests", "strategy_approval_decisions"):
        assert target.query(f"SELECT * FROM {t}") == []
    # a later discovery look sees the spent look and its error budget
    assert led.next_alpha(*led._registered_or_default())[0] == 2
    with pytest.raises(ValueError, match="holdout_already_spent"):
        led.record_test(packet["hash"], "4h", "fixed", "gate1", .5, .0001,
                        False, {})


def test_identity_is_deterministic_and_replay_is_a_duplicate(population,
                                                             target,
                                                             tmp_path):
    bank, _p, final, later = _supported(population)
    a = T.authenticate(bank, final["result_id"])
    b = T.authenticate(bank, final["result_id"])
    assert a == b and a["transfer_id"] == b["transfer_id"]
    assert T.transfer(target, a)["status"] == "inserted"
    before = _dump(tmp_path / "factory-journal.db")
    again = T.transfer(target, b)
    assert again["status"] == "duplicate" and again["rows_written"] == 0
    assert _dump(tmp_path / "factory-journal.db") == before
    v1 = F.create_version(target, CFG, B.factory_source(final, bank),
                          at_ms=later)
    v2 = F.create_version(target, CFG, B.factory_source(final, bank),
                          at_ms=later + 99)
    assert (v1["status"], v2["status"]) == ("inserted", "duplicate")
    assert v1["version_id"] == v2["version_id"]


def test_restart_reopens_and_replays_identically(population, tmp_path):
    bank, _p, final, later = _supported(population)
    path = tmp_path / "restart.db"
    j = Journal(path)
    packet = T.authenticate(bank, final["result_id"])
    T.transfer(j, packet)
    v1 = F.create_version(j, CFG, B.factory_source(final, bank), at_ms=later)
    del j
    j2 = Journal(path)                              # restart
    again = T.authenticate(bank, final["result_id"])
    assert again == packet
    assert T.transfer(j2, again)["status"] == "duplicate"
    v2 = F.create_version(j2, CFG, B.factory_source(final, bank), at_ms=later)
    assert v2["status"] == "duplicate" and v2["version_id"] == v1["version_id"]
    assert F.verify_validation(j2, F.load_version(j2, v1["version_id"]))


# ── negative: research-only / unsupported results are explicit refusals ──
@pytest.mark.parametrize("disposition", ["UNSUPPORTED", "DATA_INSUFFICIENT"])
def test_unsupported_results_are_refused_and_write_nothing(built, target,
                                                           tmp_path,
                                                           disposition):
    bank, results = _other(built, tmp_path, disposition)
    assert results
    before = _dump(tmp_path / "factory-journal.db")
    for r in results:
        with pytest.raises(T.TransferRefused) as e:
            T.authenticate(bank, r["result_id"])
        assert e.value.code == "research_only_not_supported:" \
            + r["classification"]
        with pytest.raises(ValueError):
            B.factory_source(r, bank)
    assert _dump(tmp_path / "factory-journal.db") == before


def test_unknown_result_and_missing_bank_are_refused(population):
    bank, _p, final, _l = _supported(population)
    with pytest.raises(T.TransferRefused, match="research_result_not_found"):
        T.authenticate(bank, "0" * 64)
    with pytest.raises(T.TransferRefused, match="research_result_required"):
        T.authenticate(bank, "")
    with pytest.raises(T.TransferRefused, match="research_bank_required"):
        T.authenticate(bank.parent / "nope.db", final["result_id"])


def test_descriptive_source_results_are_never_transferable(population):
    bank, _p, final, _l = _supported(population)
    rows = [r for r in saved(bank, "bridge_bank_results")
            if not r["experiment_id"]]
    assert rows and all(r["classification"] != "SUPPORTED" for r in rows)
    for r in rows:
        with pytest.raises(T.TransferRefused, match="research_only_not_supp"):
            T.authenticate(bank, r["result_id"])


# ── negative: forged / drifted evidence ──────────────────────────────────
def _ledger(bank, final):
    return T.experiment_ledger_path(bank, final["experiment_id"])


def test_forged_experiment_combination_row_is_refused(population, target):
    bank, _p, final, _l = _supported(population)
    with closing(sqlite3.connect(_ledger(bank, final))) as db:
        db.execute("UPDATE research_combos SET result=result||' '")
        db.commit()
    with pytest.raises(T.TransferRefused,
                       match="experiment_combo_differs_from_bank"):
        T.authenticate(bank, final["result_id"])
    assert target.query("SELECT * FROM research_tests") == []


def test_forged_bank_evidence_is_refused(population):
    """The retained look is the authority: editing it breaks the result's own
    identity, which the predictive authority replays."""
    bank, _p, final, _l = _supported(population)
    with closing(sqlite3.connect(bank)) as db:
        (payload,) = db.execute("SELECT payload FROM bridge_bank_results "
                                "WHERE id=?", (final["result_id"],)).fetchone()
        for (name,) in db.execute("SELECT name FROM sqlite_master WHERE "
                                  "type='trigger'").fetchall():
            db.execute(f"DROP TRIGGER {name}")     # a forger with file access
        body = json.loads(payload)
        body["quantitative_evidence"]["spent_looks"][0]["alpha_t"] = 0.9
        db.execute("UPDATE bridge_bank_results SET payload=? WHERE id=?",
                   (json.dumps(body), final["result_id"]))
        db.commit()
    with pytest.raises(T.TransferRefused, match="research_bank_unauth"):
        T.authenticate(bank, final["result_id"])


def test_missing_or_foreign_experiment_ledger_is_refused(population, tmp_path):
    bank, _p, final, _l = _supported(population)
    led = _ledger(bank, final)
    keep = tmp_path / "kept.db"
    shutil.copy(led, keep)
    with closing(sqlite3.connect(led)) as db:
        db.execute("UPDATE predictive_experiment_binding SET id='other'")
        db.commit()
    with pytest.raises(T.TransferRefused, match="experiment_ledger_binding"):
        T.authenticate(bank, final["result_id"])
    led.unlink()
    with pytest.raises(T.TransferRefused, match="experiment_ledger_missing"):
        T.authenticate(bank, final["result_id"])


def test_tampered_packet_is_refused(population, target):
    bank, _p, final, _l = _supported(population)
    packet = T.authenticate(bank, final["result_id"])
    forged = json.loads(json.dumps(packet))
    forged["tests"][0]["alpha_t"] = 0.5
    with pytest.raises(T.TransferRefused, match="transfer_packet_identity"):
        T.transfer(target, forged)
    assert target.query("SELECT * FROM research_tests") == []


# ── statistical budget integrity ─────────────────────────────────────────
def test_diverged_target_sequence_is_refused_atomically(population, target,
                                                        tmp_path):
    bank, _p, final, _l = _supported(population)
    packet = T.authenticate(bank, final["result_id"])
    with target._tx() as c:       # the target spent a DIFFERENT look at seq 1
        c.execute("INSERT INTO research_budget VALUES (1,?,?,?)",
                  (packet["budget"]["alpha"], packet["budget"]["w0"],
                   packet["budget"]["registered_at"]))
        c.execute("INSERT INTO research_tests(seq,hash,tf,geo,gate,p,alpha_t,"
                  "rejected,braked,detail,at) VALUES (1,'other','4h','fixed',"
                  "'gate1',.5,.0025,0,0,?, 'x')",
                  (packet["tests"][0]["detail"],))
    before = _dump(tmp_path / "factory-journal.db")
    with pytest.raises(T.TransferRefused, match="budget_sequence_diverged"):
        T.transfer(target, packet)
    assert _dump(tmp_path / "factory-journal.db") == before
    with pytest.raises(F.HandoffRefused):
        F.create_version(target, CFG, B.factory_source(final, bank), at_ms=1)


def test_different_registered_budget_is_refused(population, target, tmp_path):
    bank, _p, final, _l = _supported(population)
    packet = T.authenticate(bank, final["result_id"])
    with target._tx() as c:
        c.execute("INSERT INTO research_budget VALUES (1,0.5,0.25,'x')")
    before = _dump(tmp_path / "factory-journal.db")
    with pytest.raises(T.TransferRefused, match="error_budget_differs"):
        T.transfer(target, packet)
    assert _dump(tmp_path / "factory-journal.db") == before


def test_frozen_cut_cannot_move(population, target, tmp_path):
    bank, _p, final, _l = _supported(population)
    packet = T.authenticate(bank, final["result_id"])
    other = dict(json.loads(packet["tests"][0]["detail"]), cut_ms=1)
    with target._tx() as c:      # an earlier look froze another cut; seq is
        c.execute("INSERT INTO research_tests(seq,hash,tf,geo,gate,p,alpha_t,"
                  "rejected,braked,detail,at) VALUES (9,'o','4h','fixed',"
                  "'gate1',.9,.0025,0,0,?, 'x')", (json.dumps(other),))
    before = _dump(tmp_path / "factory-journal.db")
    with pytest.raises(T.TransferRefused, match="frozen_cut_differs"):
        T.transfer(target, packet)
    assert _dump(tmp_path / "factory-journal.db") == before


def test_rule_already_spent_under_other_evidence_is_refused(population,
                                                            target, tmp_path):
    bank, _p, final, _l = _supported(population)
    packet = T.authenticate(bank, final["result_id"])
    with target._tx() as c:
        c.execute("INSERT INTO research_tests(seq,hash,tf,geo,gate,p,alpha_t,"
                  "rejected,braked,detail,at) VALUES (7,?,?,?,'gate1',.9,"
                  ".0025,0,0,?, 'x')", (packet["hash"], "4h", "fixed",
                                        packet["tests"][0]["detail"]))
    before = _dump(tmp_path / "factory-journal.db")
    with pytest.raises(T.TransferRefused, match="holdout_already_spent"):
        T.transfer(target, packet)
    assert _dump(tmp_path / "factory-journal.db") == before


def test_transfer_spends_nothing_and_adds_no_capacity(population, target):
    bank, _p, final, _l = _supported(population)
    packet = T.authenticate(bank, final["result_id"])
    led = Ledger(target)
    T.transfer(target, packet)
    rows = target.query("SELECT * FROM research_tests")
    assert [dict(r) for r in rows] == packet["tests"]
    # the next look is priced by the same sequence: alpha_2, never alpha_1
    t, alpha = led.next_alpha(*led._registered_or_default())
    assert t == 2
    assert alpha < packet["tests"][0]["alpha_t"] * 5
    # a strictly larger alpha than the registered ceiling is still refused
    with pytest.raises(ValueError, match="unregistered_alpha"):
        led.record_test("x" * 16, "4h", "fixed", "gate1", 1e-9, .9, False, {})


def test_prefix_target_accepts_later_look_in_order(population, tmp_path):
    """A target already holding an earlier prefix of the sequence is extended,
    never forked: transfer is order-preserving and idempotent."""
    bank, _p, final, later = _supported(population)
    packet = T.authenticate(bank, final["result_id"])
    j = Journal(tmp_path / "prefix.db")
    Ledger(j).ensure()
    with j._tx() as c:
        c.execute("INSERT INTO research_budget VALUES (1,?,?,?)",
                  tuple(packet["budget"][k] for k in
                        ("alpha", "w0", "registered_at")))
    out = T.transfer(j, packet)
    assert out["status"] == "inserted"
    assert T.transfer(j, packet)["status"] == "duplicate"


def test_factory_receipt_unchanged_after_transfer_for_other_target(population,
                                                                   tmp_path):
    """Two independent targets produce byte-identical Factory identities."""
    bank, _p, final, later = _supported(population)
    ids = []
    for name in ("a.db", "b.db"):
        j = Journal(tmp_path / name)
        T.transfer(j, T.authenticate(bank, final["result_id"]))
        v = F.create_version(j, CFG, B.factory_source(final, bank),
                             at_ms=later + len(name))
        ids.append((v["version_id"], v["validation_receipt_id"],
                    v["spec_hash"]))
    assert ids[0] == ids[1]
