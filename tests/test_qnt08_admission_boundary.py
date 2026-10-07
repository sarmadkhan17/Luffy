"""QNT-08: quantitative Referee admission is distinct from every downstream
permission. Admission = registered gate evidence re-read from the ledger;
it grants a validated immutable version and nothing else."""
import json

import pytest

from trader.core.journal import Journal
from trader.strategy import factory_handoff as F
from tests.test_strategy_factory_handoff import (  # noqa: F401
    INPUTS, T0, _candidate, _journal, cfg)

SRC = lambda h: {"kind": "research_candidate", "hash": h}  # noqa: E731


def _refused(j, cfg, h, code):
    with pytest.raises(F.HandoffRefused) as e:
        F.create_version(j, cfg, SRC(h), at_ms=T0)
    assert e.value.code == code
    assert j.query("SELECT * FROM strategy_versions") == []


def _earlier_look(j, h, cut=1_770_000_000_000):
    """A look on the same tf that fixed an earlier cut (seq before ours)."""
    from trader.research.ledger import Ledger
    led = Ledger(j)
    with j._tx() as c:
        c.execute("UPDATE research_tests SET seq=seq+100 WHERE hash=?", (h,))
    _t, a = led.next_alpha(0.10, 0.05)
    with j._tx() as c:
        c.execute("INSERT INTO research_tests (seq, hash, tf, geo, gate, p, "
                  "alpha_t, rejected, braked, detail, at) VALUES "
                  "(1,'other','4h','fixed','gate1',0.0001,?,1,0,?, 'x')",
                  (a, json.dumps({"cut_ms": cut})))


def _patch_look(j, h, **detail):
    row = j.query("SELECT detail FROM research_tests WHERE hash=?", (h,))[0]
    d = json.loads(row["detail"])
    d.update(detail)
    with j._tx() as c:
        c.execute("UPDATE research_tests SET detail=? WHERE hash=?",
                  (json.dumps(d), h))


# ── negatives: evidence that is not registered quantitative evidence ─────
def test_fake_candidate_without_any_look_is_refused(tmp_path, cfg):
    j, h = _journal(tmp_path, look=False)
    _refused(j, cfg, h, "gate1_look_missing")


def test_label_alone_never_admits(tmp_path, cfg):
    j, h = _journal(tmp_path, look=False, state="queued")
    for state in ("reason_passed", "admitted"):
        from trader.research.ledger import Ledger
        Ledger(j).set_candidate(h, "4h", "fixed", state)
        _refused(j, cfg, h, "gate1_look_missing")


def test_forged_loose_alpha_row_is_refused(tmp_path, cfg):
    j, h = _journal(tmp_path)
    with j._tx() as c:      # bypasses Ledger.record_test's registered ceiling
        c.execute("UPDATE research_tests SET alpha_t=0.9, p=0.5, rejected=1")
    j.query("SELECT 1")
    _refused(j, cfg, h, "candidate_gate1_does_not_match_look")
    # candidate copy forged to match: the ceiling itself still refuses
    with j._tx() as c:
        c.execute("UPDATE research_candidates SET gate1=?",
                  (json.dumps({"p": 0.5, "alpha": 0.9}),))
    _refused(j, cfg, h, "gate1_alpha_not_registered")


def test_unregistered_budget_is_refused(tmp_path, cfg):
    j, h = _journal(tmp_path)
    with j._tx() as c:
        c.execute("DELETE FROM research_budget")
    _refused(j, cfg, h, "error_budget_unregistered")


@pytest.mark.parametrize("patch", [{"cut_ms": None}, {"cut_ms": 1}])
def test_stale_or_wrong_cut_is_refused(tmp_path, cfg, patch):
    j, h = _journal(tmp_path)
    _earlier_look(j, h)       # the first look fixed an earlier cut
    _refused(j, cfg, h, "gate1_look_wrong_cut")
    _patch_look(j, h, **patch)
    _refused(j, cfg, h, "gate1_look_wrong_cut")


def test_underresolved_look_is_refused(tmp_path, cfg):
    j, h = _journal(tmp_path)
    _patch_look(j, h, draws=99)         # cannot resolve alpha 0.0025
    _refused(j, cfg, h, "gate1_look_underresolved")


def test_untested_look_cannot_admit(tmp_path, cfg):
    # an untestable referee result is p=1.0 and charged, never a pass
    j, h = _journal(tmp_path, p=1.0)
    _refused(j, cfg, h, "gate1_not_rejected")


def test_raw_or_missing_dependence_p_is_refused(tmp_path, cfg):
    j, h = _journal(tmp_path)
    _patch_look(j, h, a=None)           # QNT-03 corrected reading absent
    _refused(j, cfg, h, "gate1_dependence_evidence_missing")
    _patch_look(j, h, a={"consistency_p_dep": 1e-9},
                b={"consistency_p_dep": 1e-9}, rotation={"p": 1e-9})
    _refused(j, cfg, h, "gate1_p_not_dependence_corrected")


@pytest.mark.parametrize("src", [
    {"kind": "llm_proposal", "hash": "x"}, {"kind": "spec_writer", "hash": "x"},
    {"kind": "research_candidate"}, {"kind": "scheme_d", "hash": "x"}])
def test_llm_research_descriptive_or_parked_sources_cannot_admit(
        tmp_path, cfg, src):
    j, _h = _journal(tmp_path)
    with pytest.raises(F.HandoffRefused):
        F.create_version(j, cfg, src, at_ms=T0)
    assert j.query("SELECT * FROM strategy_versions") == []


# ── positive: admission is exactly a validated version, nothing more ─────
def test_admission_grants_no_order_capital_or_owner_authority(tmp_path, cfg):
    j, h = _journal(tmp_path)
    v = F.create_version(j, cfg, SRC(h), at_ms=T0)
    vid = v["version_id"]
    assert F.state_of(j, vid) == F.VALIDATED
    assert j.query("SELECT * FROM strategies") == []
    assert j.query("SELECT * FROM strategy_version_installs") == []
    assert j.query("SELECT * FROM strategy_approval_decisions") == []
    e = F.eligible_for_first_live(j, vid, cfg=cfg, available_inputs=INPUTS)
    assert not e.eligible
    # direct bypass: skipping shadow/approval states is not a legal transition
    with pytest.raises(Exception):
        F.govern_version(j, cfg, vid, F.APPROVED_FIRST_LIVE, actor="operator",
                         reason_code="bypass", at_ms=T0)
    assert F.state_of(j, vid) == F.VALIDATED
    assert F.live_entry_block(j, vid) is not None or not F.versioned(j, vid)


def test_replay_and_restart_reproduce_admission_and_refusal(tmp_path, cfg):
    j, h = _journal(tmp_path)
    ev1 = F.gate_evidence(j, h)
    v1 = F.create_version(j, cfg, SRC(h), at_ms=T0)
    j2 = Journal(tmp_path / "j.db")                      # restart
    assert F.gate_evidence(j2, h) == ev1
    v2 = F.create_version(j2, cfg, SRC(h), at_ms=T0 + 1)
    assert v2["version_id"] == v1["version_id"] and v2["status"] != "inserted"
    _earlier_look(j2, h)
    for jj in (j2, Journal(tmp_path / "j.db")):
        with pytest.raises(F.HandoffRefused) as e:
            F.gate_evidence(jj, h)
        assert e.value.code == "gate1_look_wrong_cut"
