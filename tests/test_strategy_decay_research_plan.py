"""research-plan.v1: evidence-routing plans for strategy-decay questions.

One verified strategy_decay research-question.v1 yields one deterministic
plan naming which existing internal records bear on each SDD 14.5 decay
hypothesis. The plan concludes nothing, the regime section is explicitly
UNAVAILABLE, and nothing live, Attention, Risk or Execution calls it.
"""
import ast
import json
import re
import sqlite3
from pathlib import Path

import pytest

from tests.test_strategy_decay_research_question import (_FORBIDDEN_KEYS,
                                                         _Hist, _keys)
from trader.cognition import research_plan as rp
from trader.cognition import research_question as rq
from trader.core.journal import Journal
from trader.strategy import health_observation as ho

D, W, I, EF = ho.DECAYED, ho.STILL_WORKING, ho.IDLE, ho.EVALUATION_FAILED
CF = ho.COMPILE_FAILED


class _FullHist(_Hist):
    """_Hist records completed with every field the health writer emits for
    their variant (health_observation._build / _flush)."""

    def spec_rec(self, sweep, spec, verdict, fp="fp1"):
        rec = super().spec_rec(sweep, spec, verdict, fp)
        if verdict == ho.COMPILE_FAILED:
            for k in ("coverage", "metrics", "window", "simulation_settings"):
                rec.pop(k)
            rec["error"] = {"stage": "compile", "error_class": "SyntaxError",
                            "message": "bad"}
            return rec
        rec.update({"error": None, "lifecycle_verdict_text": "t",
                    "recent_days": 60.0, "window_days": 60.0,
                    "window_widened": False,
                    "simulation_settings": {"sha256": "sim1"}})
        rec["metrics"] = {"trades": 5, "wins": 1, "gross_win": 1.0,
                          "gross_loss": 2.0, "pooled_pf": 0.5,
                          "pooled_pf_kind": ho.PF_RATIO, "winrate": 0.2,
                          "pnl": -1.0, "per_symbol": {}}
        rec["window"] = {"window_bars": 10, "window_start_bar_open_ts": None,
                         "window_end_bar_open_ts": None,
                         "last_bar_open_ts": None, "last_bar_close_ms": None,
                         "last_bar_closed_at_sweep": None, "per_symbol": {}}
        return rec

    def sweep(self, verdicts, minute, **kw):
        over = {"observation_failures": {}, "abort_error": None,
                **(kw.pop("sweep_over", None) or {})}
        return super().sweep(verdicts, minute, sweep_over=over, **kw)


def _journal(tmp_path, verdicts, spec="s1", name="j.db"):
    j = Journal(tmp_path / name)
    for r in _FullHist().seq(spec, verdicts).rows:
        j.log_brain_event(r["kind"], r["subject"], r["detail"])
    rq.record_from_journal(j, now_ms=1)
    return j


def _qtext(j):
    [row] = j.research_questions()
    return row["canonical_json"]


def _plan(j):
    return rp.build(_qtext(j), j.strategy_health_rows())


def _section(plan, h):
    [s] = [s for s in plan["hypotheses"] if s["hypothesis"] == h]
    return s


# ── 1 one question -> one deterministic plan ────────────────────────────
def test_one_question_yields_one_deterministic_plan(tmp_path):
    j = _journal(tmp_path, [W, EF, D])
    res = rp.record_from_journal(j, now_ms=10)
    assert len(res["inserted"]) == 1 and not res["refusals"]
    [plan] = rp.load(j)
    [q] = rq.load(j)
    assert plan["plan_id"] == res["inserted"][0]
    assert plan["source_question"] == {
        "schema": rq.SCHEMA, "question_id": q["question_id"],
        "question_kind": rq.QUESTION_KIND, "trigger": rq.TRANSITION_TO_DECAYED,
        "canonical_sha256": rp._sha(_qtext(j))}
    assert plan["scope"] == {"kind": "strategy", "spec_id": "s1"}
    # deterministic and byte-stable
    assert _plan(j) == plan
    assert rp.canonical(_plan(j)) == j.research_plans()[0]["canonical_json"]


def test_same_question_in_another_journal_gives_identical_plan(tmp_path):
    a = _journal(tmp_path, [W, D], name="a.db")
    src, dst = sqlite3.connect(tmp_path / "a.db"), sqlite3.connect(tmp_path / "b.db")
    src.backup(dst)
    src.close(), dst.close()
    b = Journal(tmp_path / "b.db")
    rp.record_from_journal(b, 99)      # b plans first, at another time
    rp.record_from_journal(a, 1)
    assert (a.research_plans()[0]["canonical_json"]
            == b.research_plans()[0]["canonical_json"])


# ── 2 retry / restart idempotence ───────────────────────────────────────
def test_retry_and_restart_are_idempotent(tmp_path):
    j = _journal(tmp_path, [W, D, D, W, D])
    first = rp.record_from_journal(j, now_ms=1)
    assert len(first["inserted"]) == 2
    again = rp.record_from_journal(j, now_ms=2)
    assert again["inserted"] == [] and again["duplicate"] == first["inserted"]
    j2 = Journal(tmp_path / "j.db")
    third = rp.record_from_journal(j2, now_ms=3)
    assert third["duplicate"] == first["inserted"]
    assert [p["plan_id"] for p in rp.load(j2)] == first["inserted"]
    assert {r["recorded_at_ms"] for r in j2.research_plans()} == {1}


# ── 3 source question tampering / missing ───────────────────────────────
def _set_question_json(j, text):
    with j._tx() as c:
        c.execute("UPDATE research_questions SET canonical_json=?", (text,))


def test_tampered_source_question_is_rejected(tmp_path):
    j = _journal(tmp_path, [W, D])
    rp.record_from_journal(j, 1)
    q = json.loads(_qtext(j))
    q["source"]["thresholds"] = dict(q["source"]["thresholds"], decay_min_trades=1)
    _set_question_json(j, rq.canonical(q))
    with pytest.raises(rp.ResearchPlanError, match="question"):
        rp.load(j)
    assert rp.record_from_journal(j, 2)["refusals"]


def test_missing_source_question_is_rejected(tmp_path):
    j = _journal(tmp_path, [D])
    rp.record_from_journal(j, 1)
    with j._tx() as c:
        c.execute("DELETE FROM research_questions")
    with pytest.raises(rp.ResearchPlanError, match="source_question_missing"):
        rp.load(j)


def test_source_question_evidence_is_reverified(tmp_path):
    j = _journal(tmp_path, [W, D])
    rp.record_from_journal(j, 1)
    [q] = rq.load(j)
    with j._tx() as c:
        c.execute("DELETE FROM brain_events WHERE id=?", (q["source"]["event_id"],))
    with pytest.raises(rp.ResearchPlanError, match="question_evidence"):
        rp.load(j)
    res = rp.record_from_journal(j, 2)
    assert res["inserted"] == [] and len(res["refusals"]) == 1


def test_plan_bound_to_other_question_text_is_rejected(tmp_path):
    j = _journal(tmp_path, [W, D])
    plan = _plan(j)
    o = _journal(tmp_path, [I, D], name="o.db")
    with pytest.raises(rp.ResearchPlanError):
        rp.from_json(rp.canonical(plan), _qtext(o), o.strategy_health_rows())


# ── 4 conflicting duplicate ─────────────────────────────────────────────
def test_conflicting_duplicate_is_refused_not_overwritten(tmp_path):
    j = _journal(tmp_path, [D])
    row = rp.row_for(rp.build(_qtext(j), j.strategy_health_rows()))
    assert j.record_research_plan(row, recorded_at_ms=1) == "inserted"
    assert j.record_research_plan(row, recorded_at_ms=2) == "duplicate"
    same_question = dict(row, plan_id="0" * 64)
    assert j.record_research_plan(same_question, recorded_at_ms=2) == "conflict"
    tampered = dict(row, canonical_json=row["canonical_json"] + " ")
    assert j.record_research_plan(tampered, recorded_at_ms=2) == "conflict"
    [stored] = j.research_plans()
    assert stored["canonical_json"] == row["canonical_json"]
    assert stored["recorded_at_ms"] == 1


def _replace_plan(j, plan):
    with j._tx() as c:
        c.execute("UPDATE research_plans SET canonical_json=?",
                  (rp.canonical(plan),))


@pytest.mark.parametrize("mutate", [
    lambda p: p["hypotheses"].pop(),
    lambda p: p["hypotheses"][0]["evidence"].pop(),
    lambda p: p["hypotheses"][0].update(conclusion="genuine_deterioration"),
    lambda p: p["hypotheses"][3].update(status=rp.ROUTED),
    lambda p: p["hypotheses"][1]["evidence"][0]["fields"].append("x"),
    lambda p: p.update(salience=1.0),
    lambda p: p["scope"].update(symbol="BTC/USDT"),
    lambda p: p["source_question"].update(trigger=rq.FIRST_TRUTHFUL_DECAYED),
    lambda p: p.update(semantics="x"),
])
def test_load_rejects_altered_plan_content(tmp_path, mutate):
    j = _journal(tmp_path, [W, EF, D])
    rp.record_from_journal(j, 1)
    [plan] = rp.load(j)
    mutate(plan)
    _replace_plan(j, plan)
    with pytest.raises(rp.ResearchPlanError):
        rp.load(j)


def test_load_rejects_non_canonical_and_projection_drift(tmp_path):
    j = _journal(tmp_path, [D])
    rp.record_from_journal(j, 1)
    with j._tx() as c:
        c.execute("UPDATE research_plans SET canonical_json=canonical_json||' '")
    with pytest.raises(rp.ResearchPlanError, match="not_canonical"):
        rp.load(j)
    j = _journal(tmp_path, [D], name="k.db")
    rp.record_from_journal(j, 1)
    with j._tx() as c:
        c.execute("UPDATE research_plans SET scope_id='s9'")
    with pytest.raises(rp.ResearchPlanError, match="row_projection"):
        rp.load(j)


# ── 5/6 hypothesis sections and exact routing ───────────────────────────
def test_all_four_hypotheses_in_fixed_order(tmp_path):
    plan = _plan(_journal(tmp_path, [D]))
    assert [s["hypothesis"] for s in plan["hypotheses"]] == [
        "genuine_deterioration", "insufficient_recent_opportunities",
        "data_failure", "temporary_regime_absence"]
    for s in plan["hypotheses"][:3]:
        assert s["status"] == rp.ROUTED and s["evidence"]
        assert s["unavailable_reason"] is None


def test_regime_section_is_explicitly_unavailable(tmp_path):
    plan = _plan(_journal(tmp_path, [W, D]))
    s = _section(plan, rp.TEMPORARY_REGIME_ABSENCE)
    assert s == {"hypothesis": rp.TEMPORARY_REGIME_ABSENCE,
                 "status": rp.UNAVAILABLE,
                 "unavailable_reason": "no_truthful_worldmodel_source",
                 "unavailable_detail": rp.REGIME_UNAVAILABLE_DETAIL,
                 "evidence": []}


def test_routes_bind_the_questions_exact_evidence(tmp_path):
    j = _journal(tmp_path, [W, EF, D])
    [q] = rq.load(j)
    plan = rp.build(_qtext(j), j.strategy_health_rows())

    def locs(h, role):
        return [e["locator"] for e in _section(plan, h)["evidence"]
                if e["role"] == role]

    src, prior = q["source"]["event_id"], q["prior"]["event_id"]
    for h in (rp.GENUINE_DETERIORATION, rp.INSUFFICIENT_RECENT_OPPORTUNITIES,
              rp.DATA_FAILURE):
        assert locs(h, "source_observation") == [{"event_id": src}]
        assert locs(h, "prior_observation") == [{"event_id": prior}]
    assert locs(rp.GENUINE_DETERIORATION, "question_change_flags") == [
        {"question_id": q["question_id"]}]
    at = q["source"]["sweep_started_at"]
    assert locs(rp.INSUFFICIENT_RECENT_OPPORTUNITIES,
                "strategy_signal_decisions") == [
        {"selection": rp.DECISION_SELECTION,
         "rule": rp.DECISION_SELECTION_RULE, "spec_id": "s1",
         "decision_ts_at_or_before": at,
         "signal_bar_close_ms_at_or_before": rq._ms(at)}]
    assert locs(rp.DATA_FAILURE, "source_sweep") == [
        {"sweep_id": q["source"]["sweep_id"]}]
    assert locs(rp.DATA_FAILURE, "unreadable_observation") == [
        {"event_id": e} for e in q["unreadable_since_prior_event_ids"]] == [
        {"event_id": 3}]
    # every routed health event exists in the journal under that kind
    ids = {r["id"]: r["kind"] for r in j.strategy_health_rows()}
    for s in plan["hypotheses"]:
        for e in s["evidence"]:
            if e["store"] == "journal.brain_events" and "event_id" in e["locator"]:
                assert ids[e["locator"]["event_id"]] == e["record_kind"]
    # every routed health record is bound to its exact stored content
    raw = {r["id"]: r for r in j.strategy_health_rows()}
    for s in plan["hypotheses"]:
        for e in s["evidence"]:
            if e["store"] == "journal.brain_events":
                b = e["binding"]
                assert b["record_sha256"] == rp._sha(raw[b["event_id"]]["detail"])
            else:
                assert e["binding"] is None


def test_first_decayed_plan_has_no_prior_routes(tmp_path):
    plan = _plan(_journal(tmp_path, [D]))
    roles = {e["role"] for s in plan["hypotheses"] for e in s["evidence"]}
    assert "prior_observation" not in roles
    assert "question_change_flags" not in roles
    assert "unreadable_observation" not in roles


# ── 7 no conclusion / score / rank / salience / asset ───────────────────
_NO_CONCLUSION = _FORBIDDEN_KEYS | {"conclusion", "verdict", "likelihood",
                                    "confidence", "weight", "supported",
                                    "selected_hypothesis", "ranking"}


def test_plan_carries_no_conclusion_score_rank_or_asset_fields(tmp_path):
    for verdicts in ([D], [W, EF, D], [I, D]):
        plan = _plan(_journal(tmp_path, verdicts, name=f"{len(verdicts)}.db"))
        assert not (set(_keys(plan)) & _NO_CONCLUSION)
        assert set(plan) == set(rp._RECORD_KEYS)
        for s in plan["hypotheses"]:
            assert set(s) == {"hypothesis", "status", "unavailable_reason",
                              "unavailable_detail", "evidence"}


# ── 8 malformed / unsupported questions fail closed ─────────────────────
@pytest.mark.parametrize("text", [None, "", "not json", "[]", "{}",
                                  json.dumps({"schema": rq.SCHEMA})])
def test_malformed_question_fails_closed(text):
    with pytest.raises(rp.ResearchPlanError):
        rp.build(text, [])


def test_unsupported_or_non_canonical_question_fails_closed(tmp_path):
    text = _qtext(_journal(tmp_path, [D]))
    q = json.loads(text)
    for bad in (dict(q, question_kind="other"), dict(q, schema="research-question.v2")):
        with pytest.raises(rp.ResearchPlanError):
            rp.build(rq.canonical(bad), [])
    with pytest.raises(rp.ResearchPlanError):
        rp.build(text + " ", [])


def test_unsupported_stored_question_is_refused_not_planned(tmp_path):
    j = _journal(tmp_path, [D])
    with j._tx() as c:
        c.execute("UPDATE research_questions SET question_kind='other'")
    res = rp.record_from_journal(j, 1)
    assert res["inserted"] == [] and len(res["refusals"]) == 1
    assert j.research_plans() == []


# ── legacy journals ─────────────────────────────────────────────────────
def test_legacy_journal_without_plan_table_remains_readable(tmp_path):
    db = tmp_path / "legacy.db"
    j = _journal(tmp_path, [W, D], name="legacy.db")
    with j._tx() as c:
        c.execute("DROP TABLE research_plans")
    con = sqlite3.connect(db)
    assert "research_plans" not in {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    j2 = Journal(db)
    assert j2.research_plans() == [] and rp.load(j2) == []
    assert len(rq.load(j2)) == 1
    assert len(rp.record_from_journal(j2, 1)["inserted"]) == 1


def test_journal_without_questions_plans_nothing(tmp_path):
    j = Journal(tmp_path / "e.db")
    assert rp.record_from_journal(j, 1) == {"inserted": [], "duplicate": [],
                                            "conflict": [], "refusals": []}


# ── 9 no live-loop / Risk / Execution / Attention wiring ────────────────
def test_module_imports_only_contract_modules():
    src = Path(rp.__file__).read_text()
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add(node.module or "")
    assert mods <= {"__future__", "hashlib", "json",
                    "trader.cognition", "trader.strategy"}


def test_nothing_in_the_live_path_calls_it():
    root = Path(rp.__file__).resolve().parents[1]
    pat = re.compile(r"import\s+research_plan\b|research_plan\s+import"
                     r"|cognition\.research_plan\b|record_research_plan")
    callers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if p.name != "research_plan.py"
                     and pat.search(p.read_text(errors="ignore")))
    # the journal owns the storage primitive; research_evidence.py is the
    # offline evidence-collection consumer, guarded by its own test
    assert callers == ["cognition/research_evidence.py", "core/journal.py"]
    journal_src = (root / "core/journal.py").read_text()
    assert "research_plan import" not in journal_src


def test_planning_is_read_only_on_evidence(tmp_path):
    j = _journal(tmp_path, [W, D])
    tables = ("brain_events", "research_questions", "decisions", "trades")
    before = {t: j.query(f"SELECT * FROM {t}") for t in tables}
    rp.build(_qtext(j), j.strategy_health_rows())
    rp.record_from_journal(j, 1)
    rp.load(j)
    assert {t: j.query(f"SELECT * FROM {t}") for t in tables} == before


# ── review regressions ──────────────────────────────────────────────────
def _edit_detail(j, event_id, edit):
    [row] = j.query("SELECT detail FROM brain_events WHERE id=?", (event_id,))
    rec = json.loads(row["detail"])
    edit(rec)
    with j._tx() as c:
        c.execute("UPDATE brain_events SET detail=? WHERE id=?",
                  (json.dumps(rec), event_id))


def _evidence(plan, role):
    return [e for s in plan["hypotheses"] for e in s["evidence"]
            if e["role"] == role]


# blocker 1: nested types are checked on the serialized form
@pytest.mark.parametrize("value", [1.0, True])
def test_locator_type_substitution_is_rejected(tmp_path, value):
    j = _journal(tmp_path, [W, EF, D])
    rp.record_from_journal(j, 1)
    [plan] = rp.load(j)
    [item] = [e for e in _evidence(plan, "prior_observation")
              if e["locator"]["event_id"] == 1][:1]
    item["locator"]["event_id"] = value
    assert item["locator"]["event_id"] == 1          # equal in Python
    _replace_plan(j, plan)
    with pytest.raises(rp.ResearchPlanError, match="plan_content"):
        rp.load(j)


@pytest.mark.parametrize("value", [1.0, True])
def test_binding_type_substitution_is_rejected(tmp_path, value):
    j = _journal(tmp_path, [W, D])
    rp.record_from_journal(j, 1)
    [plan] = rp.load(j)
    _evidence(plan, "prior_observation")[0]["binding"]["event_id"] = value
    _replace_plan(j, plan)
    with pytest.raises(rp.ResearchPlanError, match="plan_content"):
        rp.load(j)


def test_question_threshold_type_substitution_is_rejected(tmp_path):
    j = _journal(tmp_path, [W, D])
    rp.record_from_journal(j, 1)
    q = json.loads(_qtext(j))
    q["source"]["thresholds"]["decay_min_trades"] = 3.0      # 3 == 3.0
    _set_question_json(j, rq.canonical(q))
    with pytest.raises(rq.ResearchQuestionError, match="source_evidence"):
        rq.load(j)
    with pytest.raises(rp.ResearchPlanError):
        rp.load(j)


# blocker 2: supporting records are contract-checked and bound
def _sweep_event(j, sweep_id):
    [row] = j.query("SELECT id FROM brain_events WHERE kind=? AND subject=?",
                    (ho.KIND_SWEEP, sweep_id))
    return row["id"]


def test_invented_sweep_status_before_registration_is_refused(tmp_path):
    j = _journal(tmp_path, [W, D])
    [q] = rq.load(j)
    _edit_detail(j, _sweep_event(j, q["source"]["sweep_id"]),
                 lambda r: r.update(status="invented_status"))
    res = rp.record_from_journal(j, 1)
    assert res["inserted"] == []
    assert res["refusals"] == [(q["question_id"],
                                "supporting_record_contract:source_sweep")]


@pytest.mark.parametrize("edit", [
    lambda r: r.update(status="invented_status"),
    lambda r: r.update(status=ho.SWEEP_ABORTED),
    lambda r: r.update(observation_failures={"x": {}}),
    lambda r: r.update(abort_error={"stage": "x", "error_class": "E",
                                    "message": "m"}),
])
def test_sweep_changed_after_registration_fails_load_and_rewrite(tmp_path, edit):
    j = _journal(tmp_path, [W, D])
    [pid] = rp.record_from_journal(j, 1)["inserted"]
    [q] = rq.load(j)
    _edit_detail(j, _sweep_event(j, q["source"]["sweep_id"]), edit)
    with pytest.raises(rp.ResearchPlanError):
        rp.load(j)
    res = rp.record_from_journal(j, 2)
    assert res["duplicate"] == [] and res["inserted"] == []
    assert res["conflict"] or res["refusals"]
    assert [r["plan_id"] for r in j.research_plans()] == [pid]


def test_unreadable_observation_change_fails_load_and_is_a_conflict(tmp_path):
    j = _journal(tmp_path, [W, CF, D])
    rp.record_from_journal(j, 1)
    [q] = rq.load(j)
    [e] = q["unreadable_since_prior_event_ids"]
    _edit_detail(j, e, lambda r: r["error"].update(message="edited"))
    rq.load(j)                         # the question does not bind it ...
    with pytest.raises(rp.ResearchPlanError, match="plan_content"):
        rp.load(j)                     # ... the plan does
    res = rp.record_from_journal(j, 2)
    assert len(res["conflict"]) == 1 and res["duplicate"] == []


@pytest.mark.parametrize("edit", [
    lambda r: r.update(error="boom"),
    lambda r: r.update(coverage={"coverage_complete": False}),
    lambda r: r.update(lifecycle_branch=ho.DECAYED),
])
def test_unreadable_observation_breaking_contract_is_refused(tmp_path, edit):
    j = _journal(tmp_path, [W, CF, D])
    [q] = rq.load(j)
    [e] = q["unreadable_since_prior_event_ids"]
    _edit_detail(j, e, edit)
    res = rp.record_from_journal(j, 1)
    assert res["inserted"] == [] and len(res["refusals"]) == 1


def test_routed_field_missing_from_a_record_is_refused(tmp_path):
    j = _journal(tmp_path, [W, D])
    [q] = rq.load(j)
    # the question binds source/prior content, so use the sweep record:
    # abort_error is routed but absent is not a contract violation by itself
    _edit_detail(j, _sweep_event(j, q["source"]["sweep_id"]),
                 lambda r: r.pop("abort_error"))
    [(qid, reason)] = rp.record_from_journal(j, 1)["refusals"]
    assert reason == "routed_field_missing:source_sweep:abort_error"


# blocker 3: the decision route declares raw rows and exact bounds
def test_decision_route_declares_raw_rows_and_exact_selection(tmp_path):
    j = _journal(tmp_path, [W, D])
    [q] = rq.load(j)
    [e] = _evidence(_plan(j), "strategy_signal_decisions")
    assert e["store"] == "journal.decisions"
    assert e["reader"] == "Journal.decision_observation_rows"
    assert e["record_schema"] is None and e["record_kind"] == "decision_row"
    from trader.strategy import signal_occurrence_observation as soo
    assert set(e["fields"]) <= set(soo.COLUMNS)     # the reader's columns
    [row] = j.query("SELECT sql FROM sqlite_master WHERE name='decisions'")
    assert all(f in row["sql"] for f in e["fields"])
    loc = e["locator"]
    assert loc["decision_ts_at_or_before"] == q["source"]["sweep_started_at"]
    assert type(loc["signal_bar_close_ms_at_or_before"]) is int
    assert "signal_occurrence.signal_occurrence" in loc["rule"]
    assert "strategy-signal-occurrence-observation" not in json.dumps(e)


# blocker 4: routes follow the real health writer's record variants
def _real_journal(tmp_path):
    """W, compile_failed, raised evaluation, D for spec s1 — written by the
    real Analyst health writer into a real Journal."""
    import time
    from trader.brain.analyst import Analyst
    from tests.test_strategy_health_observation import (DOWN, UP, _analyst,
                                                        _review, _spec)
    j = Journal(tmp_path / "real.db")
    a = _analyst({"BTC/USDT": UP, "_btc_1h": UP}, journal=j)
    _review(a, [_spec("s1")])
    time.sleep(0.01)
    _review(a, [_spec("s1", entry="close >>> nope(")])
    time.sleep(0.01)

    def boom(tf, spec):
        raise RuntimeError("feed down")
    a._ctx = boom
    with pytest.raises(RuntimeError):
        _review(a, [_spec("s1")])
    time.sleep(0.01)
    a._ctx = lambda tf, spec: Analyst._ctx(a, tf, spec)
    a.frames = lambda tf, extra=(): {"BTC/USDT": DOWN, "_btc_1h": DOWN}
    _review(a, [_spec("s1")])
    return j


def test_real_writer_records_route_only_existing_fields(tmp_path):
    j = _real_journal(tmp_path)
    assert rq.record_from_journal(j, 1)["refusals"] == []
    [q] = rq.load(j)
    assert len(q["unreadable_since_prior_event_ids"]) == 2
    res = rp.record_from_journal(j, 1)
    assert len(res["inserted"]) == 1 and res["refusals"] == []
    [plan] = rp.load(j)
    unread = _evidence(plan, "unreadable_observation")
    assert [e["record_variant"] for e in unread] == [
        rp.COMPILE_FAILED_RECORD, rp.EVALUATION_EXCEPTION_RECORD]
    assert all("coverage" not in e["fields"] for e in unread)
    raw = {r["id"]: json.loads(r["detail"]) for r in j.strategy_health_rows()}
    for s in plan["hypotheses"]:
        for e in s["evidence"]:
            if e["store"] != "journal.brain_events":
                continue
            rec = raw[e["binding"]["event_id"]]
            for f in e["fields"]:
                assert rp._has(rec, f), (e["role"], f)


def test_real_raised_record_error_change_fails_plan(tmp_path):
    j = _real_journal(tmp_path)
    rq.record_from_journal(j, 1)
    rp.record_from_journal(j, 1)
    [plan] = rp.load(j)
    [raised] = [e for e in _evidence(plan, "unreadable_observation")
                if e["record_variant"] == rp.EVALUATION_EXCEPTION_RECORD]
    _edit_detail(j, raised["binding"]["event_id"],
                 lambda r: r["error"].update(message="edited"))
    with pytest.raises(rp.ResearchPlanError, match="plan_content"):
        rp.load(j)
    rq.load(j)                          # the question itself still verifies


# ── review round 2 ──────────────────────────────────────────────────────
_WRITE_FAIL = {"step": "write", "stage": "write", "error_class": None,
               "message": None}


def _sweep_events(j):
    """{sweep_id: event_id} for every sweep record."""
    return {r["subject"]: r["id"] for r in j.strategy_health_rows()
            if r["kind"] == ho.KIND_SWEEP}


# finding 1: sweep failure entries and not_evaluated follow the writer
@pytest.mark.parametrize("edit", [
    lambda r: r.update(observation_failures={"s1": 7}),
    lambda r: r.update(observation_failures={"s9": _WRITE_FAIL}),
    lambda r: r.update(observation_failures={"s9": dict(_WRITE_FAIL,
                                                        step="build")},
                       observation_failed_spec_ids=["s9"]),
    lambda r: r.update(observation_failures={"s9": dict(_WRITE_FAIL,
                                                        message="x")},
                       observation_failed_spec_ids=["s9"]),
    lambda r: r.update(not_evaluated_spec_ids=["s1"]),
    lambda r: r.update(intended_spec_ids=["s1", "s9"]),   # s9 never listed
])
def test_sweep_contract_violation_is_refused_before_registration(tmp_path, edit):
    j = _journal(tmp_path, [W, D])
    [q] = rq.load(j)
    _edit_detail(j, _sweep_events(j)[q["source"]["sweep_id"]], edit)
    res = rp.record_from_journal(j, 1)
    assert res["inserted"] == [] and len(res["refusals"]) == 1
    assert j.research_plans() == []


def test_writer_shaped_failure_of_another_spec_is_accepted(tmp_path):
    j = _journal(tmp_path, [W, D])
    [q] = rq.load(j)
    both = ["s1", "s9"]
    _edit_detail(j, _sweep_events(j)[q["source"]["sweep_id"]], lambda r: r.update(
        intended_spec_ids=both, evaluation_attempted_spec_ids=both,
        evaluation_completed_spec_ids=both,
        observation_failed_spec_ids=["s9"],
        observation_failures={"s9": _WRITE_FAIL}))
    res = rp.record_from_journal(j, 1)
    assert len(res["inserted"]) == 1 and res["refusals"] == []


# finding 2: every sweep the derivation depends on is validated and bound
def test_history_sweeps_prior_through_source_are_bound(tmp_path):
    j = _journal(tmp_path, [W, W, CF, D])
    [q] = rq.load(j)
    plan = _plan(j)
    ev = _sweep_events(j)
    hist = _evidence(plan, "history_sweep")
    assert [e["locator"]["sweep_id"] for e in hist] == ["sw020", "sw030"]
    assert q["prior"]["sweep_id"] == "sw020"
    raw = {r["id"]: r for r in j.strategy_health_rows()}
    for e in hist:
        assert e["binding"] == {"event_id": ev[e["locator"]["sweep_id"]],
                                "record_sha256": rp._sha(
                                    raw[ev[e["locator"]["sweep_id"]]]["detail"])}


def test_first_decayed_binds_every_earlier_sweep(tmp_path):
    j = _journal(tmp_path, [EF, CF, D])
    [q] = rq.load(j)
    assert q["prior"] is None
    hist = _evidence(_plan(j), "history_sweep")
    assert [e["locator"]["sweep_id"] for e in hist] == ["sw010", "sw020"]


@pytest.mark.parametrize("which", ["sw010", "sw020"])     # prior, intervening
@pytest.mark.parametrize("edit", [
    lambda r: r.update(status="invented_status"),          # contract break
    lambda r: r.update(intended_spec_ids=r["intended_spec_ids"] + ["s9"],
                       not_evaluated_spec_ids=["s9"]),     # still valid
])
def test_history_sweep_edit_after_registration_fails_load_and_retry(
        tmp_path, which, edit):
    j = _journal(tmp_path, [W, CF, D])
    [pid] = rp.record_from_journal(j, 1)["inserted"]
    [q] = rq.load(j)
    assert q["prior"]["sweep_id"] == "sw010"
    _edit_detail(j, _sweep_events(j)[which], edit)
    with pytest.raises(rp.ResearchPlanError):
        rp.load(j)
    res = rp.record_from_journal(j, 2)
    assert res["duplicate"] == [] and res["inserted"] == []
    assert res["conflict"] or res["refusals"]
    assert [r["plan_id"] for r in j.research_plans()] == [pid]


def test_sweep_before_the_baseline_is_not_bound(tmp_path):
    j = _journal(tmp_path, [W, W, D])
    rp.record_from_journal(j, 1)
    [q] = rq.load(j)
    assert q["prior"]["sweep_id"] == "sw020"
    _edit_detail(j, _sweep_events(j)["sw010"], lambda r: r.update(
        intended_spec_ids=r["intended_spec_ids"] + ["s9"],
        not_evaluated_spec_ids=["s9"]))
    assert len(rp.load(j)) == 1
    assert rp.record_from_journal(j, 2)["duplicate"]
