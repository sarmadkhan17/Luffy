"""The real mechanism review persists health without granting retirement authority."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from trader.brain import analyst as analyst_module, ideas
from trader.core.journal import Journal
from trader.kernel import Kernel
from trader.strategy import health_observation as ho, rolling
from trader.strategy.spec_evidence import risk_for
from tests.test_strategy_health_observation import CFG, DOWN, RARE, UP, _spec


@pytest.fixture
def review(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "health.db")
    cfg = deepcopy(CFG)
    instances, flushes = [], []
    context = {"frames": {"BTC/USDT": UP}}
    analyst_class = analyst_module.Analyst
    original_flush = analyst_class.flush_health_observations

    def flush(analyst):
        pending = len(analyst._health_sweeps)
        result = original_flush(analyst)
        flushes.append((pending, result))
        return result

    def make_analyst(*args):
        analyst = analyst_class(*args)
        analyst._ctx = lambda tf, spec: (
            context["frames"], None, None, risk_for(cfg["risk"], tf))
        instances.append(analyst)
        return analyst

    monkeypatch.setattr(analyst_class, "flush_health_observations", flush)
    monkeypatch.setattr(analyst_module, "Analyst", make_analyst)
    monkeypatch.setattr(ideas, "pending", lambda *args: [])
    kernel = Kernel.__new__(Kernel)
    kernel.journal, kernel.cfg = journal, cfg
    kernel.feed, kernel.notifier = None, None

    def run(specs):
        for spec in specs:
            journal.upsert_spec(spec)
        # A full book (including an empty zero-cap book) excludes replacement
        # generation. No Kernel constructor, feed/provider or LLM is used.
        return kernel._mechanism_once(max_book=len(specs))

    yield SimpleNamespace(journal=journal, kernel=kernel, run=run,
                          instances=instances, flushes=flushes, context=context)
    journal._conn().rollback()
    journal._conn().close()


def records(review):
    rows = review.journal.strategy_health_rows()
    return ([json.loads(r["detail"]) for r in rows if r["kind"] == ho.KIND_SPEC],
            [json.loads(r["detail"]) for r in rows if r["kind"] == ho.KIND_SWEEP])


def reject_observation_writes(journal):
    with journal._tx() as conn:
        conn.execute("""CREATE TRIGGER reject_health_test BEFORE INSERT ON brain_events
            WHEN NEW.kind='strategy_health_observed' BEGIN
            SELECT RAISE(ABORT, 'health test write failure'); END""")


@pytest.mark.parametrize("entry,verdict", [
    ("close > ema(5)", ho.STILL_WORKING), (RARE, ho.IDLE),
    ("close >>> x(", ho.COMPILE_FAILED),
])
def test_normal_kernel_review_persists_each_verdict(review, entry, verdict):
    spec = _spec(entry=entry)
    result = review.run([spec])
    observations, sweeps = records(review)
    assert result["retired"] == []
    assert len(observations) == len(sweeps) == 1
    assert observations[0]["verdict"] == verdict
    assert observations[0]["schema"] == sweeps[0]["schema"] == ho.SCHEMA
    assert observations[0]["sweep_id"] == sweeps[0]["sweep_id"]
    assert sweeps[0]["status"] == ho.SWEEP_COMPLETED
    assert sweeps[0]["observation_recorded_spec_ids"] == [spec.id]
    assert review.flushes == [(1, True)]


def test_empty_kernel_review_persists_completed_empty_sweep(review):
    assert review.run([])["book"] == 0
    observations, sweeps = records(review)
    assert observations == [] and len(sweeps) == 1
    assert sweeps[0]["status"] == ho.SWEEP_COMPLETED
    assert sweeps[0]["intended_spec_ids"] == []
    assert sweeps[0]["observation_recorded_spec_ids"] == []
    assert review.flushes == [(1, True)]


@pytest.mark.parametrize("stage", ["context", "evaluate"])
@pytest.mark.parametrize("write_failure", [False, True])
def test_review_exception_is_preserved_and_exceptional_flush_is_not_duplicated(
        review, monkeypatch, stage, write_failure):
    error = RuntimeError("original review failure")

    def fail(*args, **kwargs):
        raise error

    if stage == "context":
        factory = analyst_module.Analyst

        def make_analyst(*args):
            analyst = factory(*args)
            analyst._ctx = fail
            return analyst

        monkeypatch.setattr(analyst_module, "Analyst", make_analyst)
    else:
        monkeypatch.setattr(rolling, "has_decayed", fail)
    if write_failure:
        reject_observation_writes(review.journal)
    with pytest.raises(RuntimeError) as caught:
        review.run([_spec()])
    assert caught.value is error
    observations, sweeps = records(review)
    assert len(sweeps) == 1 and sweeps[0]["status"] == ho.SWEEP_ABORTED
    assert len(observations) == (0 if write_failure else 1)
    if observations:
        assert observations[0]["verdict"] == ho.EVALUATION_FAILED
        assert observations[0]["error"]["stage"] == stage
    # Analyst's existing exceptional flush writes once; Kernel's finally is
    # still called, but sees the drained buffer rather than filing another sweep.
    assert review.flushes == [(1, True), (0, True)]


def test_legacy_decay_observation_does_not_apply_retirement(review):
    review.context["frames"] = {"BTC/USDT": DOWN}
    spec = _spec()
    review.journal.upsert_spec(spec)
    before = review.journal.query("SELECT * FROM strategies")
    result = review.kernel._mechanism_once(max_book=1)
    observations, sweeps = records(review)
    assert result["retired"] == [] and result["book"] == 1
    assert observations[0]["verdict"] == ho.DECAYED
    assert observations[0]["retirement_action_selected"] is True
    assert sweeps[0]["n_retirement_actions_selected"] == 1
    assert review.journal.query("SELECT * FROM strategies") == before
    assert len(review.journal.query(
        "SELECT * FROM brain_events WHERE kind='spec_decayed'")) == 1


@pytest.mark.parametrize("failure_site", ["notification", "legacy_refusal"])
def test_completed_review_flushes_before_propagating_later_failure(
        review, monkeypatch, failure_site):
    from trader.learning import foundation

    error = RuntimeError("original post-review failure")

    def fail(*args, **kwargs):
        raise error

    if failure_site == "notification":
        review.kernel.notifier = SimpleNamespace(send=fail)
    else:
        monkeypatch.setattr(foundation, "refuse_legacy_learning", fail)
    review.context["frames"] = {"BTC/USDT": DOWN}
    with pytest.raises(RuntimeError) as caught:
        review.run([_spec()])
    assert caught.value is error
    observations, sweeps = records(review)
    assert len(observations) == len(sweeps) == 1
    assert sweeps[0]["status"] == ho.SWEEP_COMPLETED
    assert review.flushes == [(1, True)]
    assert review.journal.query("SELECT state FROM strategies") == [{"state": "paper"}]


def test_database_write_failure_is_best_effort_without_retry(review):
    reject_observation_writes(review.journal)
    assert review.run([_spec()])["retired"] == []
    observations, sweeps = records(review)
    assert observations == [] and len(sweeps) == 1
    assert sweeps[0]["observation_failed_spec_ids"] == ["h1"]
    assert sweeps[0]["observation_recorded_spec_ids"] == []
    before = review.journal.strategy_health_rows()
    with review.journal._tx() as conn:
        conn.execute("DROP TRIGGER reject_health_test")
    assert review.instances[0].flush_health_observations() is True
    assert review.journal.strategy_health_rows() == before


def test_kernel_flush_preserves_transaction_guard(review):
    spec = _spec()
    review.journal.upsert_spec(spec)
    conn = review.journal._conn()
    conn.execute("INSERT INTO brain_events(ts,kind,subject,detail) "
                 "VALUES ('t','uncommitted_test','h1','{}')")
    assert conn.in_transaction
    assert review.kernel._mechanism_once(max_book=1)["retired"] == []
    assert conn.in_transaction
    assert review.journal.strategy_health_rows() == []
    assert review.flushes == [(1, False)]
    conn.commit()
    assert review.instances[0].flush_health_observations() is True
    observations, sweeps = records(review)
    assert len(observations) == len(sweeps) == 1


def test_history_and_research_readers_keep_existing_events_unchanged(review):
    from trader.cognition import research_question

    spec = _spec()
    review.run([spec])
    historical = review.journal.strategy_health_rows()
    review.context["frames"] = {"BTC/USDT": DOWN}
    review.kernel._mechanism_once(max_book=1)
    rows = review.journal.strategy_health_rows()
    assert rows[:len(historical)] == historical
    history = ho.history(rows)
    assert history["invalid_records"] == []
    assert history["sweeps_without_record"] == []
    assert history["specs"][0]["n_sweep_observations"] == 2
    questions = research_question.derive(rows).questions
    assert len(questions) == 1
    assert questions[0]["trigger"] == research_question.TRANSITION_TO_DECAYED
    before = review.journal.strategy_health_rows()
    for analyst in review.instances:
        assert analyst.flush_health_observations() is True
    assert review.journal.strategy_health_rows() == before
