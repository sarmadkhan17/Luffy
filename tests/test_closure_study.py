"""P5: bounded resumable orchestration. Unit EXECUTION is real
(`qualify`, store, planner); only the per-candidate numerical development is
swapped for a deterministic stand-in in the orchestration cases, because those
cases test scheduling/resume/no-double-look, not statistics. The real-flow
end-to-end case runs the actual evaluator."""
import json

import pytest

from tests.closure_fixtures import DEV_BARS, N_BARS, RISK, build, scripted_referee
from trader.core.config import load_config
from trader.research.closure import ClosureRefused, __main__ as cli, digest, families, manifest, study as S
from trader.research.closure import synthetic as SYN


def spec_for(b, **over):
    st = SYN.EPOCH_MS
    spec = dict(snapshot_path=str(b.snap), tf='4h', discovery_symbols=b.names[:6], heldout_symbols=b.names[6:],
                fit_start_ms=st, dev_end_ms=st + DEV_BARS * SYN.TF_MS, embargo_bars=10,
                protected_start_ms=st + 1100 * SYN.TF_MS, protected_end_ms=st + (N_BARS - 1) * SYN.TF_MS,
                policy=b.env['policy'], risk=RISK, seed_families=['trend_breakout.v1', 'volatility_expansion_breakout.v1',
                                                                 'oversold_in_uptrend.v1'], workdir=str(b.snap.parent / 'wk'))
    spec.update(over)
    return spec


def positive(cand, snap, env, store=None, draws=None, **_context_paths):          # production passes derivs/world/references paths
    c = families.realize(cand, {}, env['envelope_id']) if not families.gauge_expressions(cand) else None
    if c is None:                                             # gauge families: not needed by these cases
        r = dict(outcome='NEGATIVE_ECONOMICS')
    else:
        r = dict(outcome='DEVELOPMENT_POSITIVE')
    res = dict(schema='closure-development.v1', stage='DEVELOPMENT', candidate_id=cand['candidate_id'],
               envelope_id=env['envelope_id'], reason='FIXTURE stand-in', cached=False,
               combo=c.as_dict() if c else None, combo_hash=c.hash if c else None, threshold_fit={},
               metrics=dict(total_pct=9.0 if cand['family_id'] == 'trend_breakout.v1' else 3.0, max_dd_pct=4.0,
                            consistency_p=.02, median_pf=1.3, trades=100, scored_symbols=6, testable=True), **r)
    res['result_id'] = digest(res)
    res['cache_key'] = digest(['k', cand['candidate_id']])
    return res


@pytest.fixture
def study(tmp_path, monkeypatch):
    b = build(tmp_path, name='st', seal='SYNTHETIC')
    monkeypatch.setattr(S, 'develop', positive)
    sid = S.create_study(b.store, spec_for(b), now_ms=1)
    return b, sid


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_multi_unit_study_progresses_across_resumes_within_a_deadline(study, monkeypatch):
    b, sid = study
    clock = Clock()
    real = S._run_unit

    def timed(*a, **k):
        clock.t += 10.0                                          # each unit costs 10 simulated seconds
        return real(*a, **k)
    monkeypatch.setattr(S, '_run_unit', timed)
    calls = []
    steps = []
    for i in range(10):
        out = S.step(b.store, sid, budget_s=25, clock=clock, now_ms=10 + i, run=scripted_referee(True, calls), child=False)
        steps.append(out)
        clock.t = 0.0
        if out['step'] == 'COMPLETE':
            break
    assert [s['step'] for s in steps[:-1]] and all(s['step'] == 'DEADLINE' for s in steps[:-1])
    assert steps[-1]['step'] == 'COMPLETE' and len(steps) >= 3          # needed several resumes
    assert all(0 < s.get('units_run', 0) <= 3 for s in steps)
    final = steps[-1]
    assert final['process_status'] == 'COMPLETED' and final['research_outcome'] == 'QUALIFIED'
    assert len(calls) == 1                                          # exactly one protected look
    done = {u['unit_id'] for u in b.store.units(sid) if u['status'] == 'DONE'}
    assert {'freeze', 'qualify'} <= done and sum(1 for d in done if d.startswith('develop:')) >= 3     # seeds + any bounded revisions
    assert steps[0]['last_progress_ms'] and steps[0]['next_work']


def test_crash_between_look_and_result_never_double_spends(study, monkeypatch):
    b, sid = study
    calls = []
    def dies(fn, payload, **_):
        calls.append(1)
        raise KeyboardInterrupt('machine lost power inside the look')
    with pytest.raises(KeyboardInterrupt):
        S.step(b.store, sid, budget_s=10 ** 6, run=dies, now_ms=5, child=False)
    mid = next(u for u in b.store.units(sid) if u['unit_id'] == 'qualify')
    assert mid['status'] == 'RUNNING' and mid['attempts'] == 1          # durable checkpoint
    out = S.step(b.store, sid, budget_s=10 ** 6, run=scripted_referee(True, calls), now_ms=6, child=False)
    assert len(calls) == 1                                          # the second run never re-looked
    assert out['process_status'] == 'COMPLETED' and out['research_outcome'] == 'INCONCLUSIVE'
    assert 'interrupted' in out['research_reason']


def test_busy_market_cycle_defers_visibly_without_work(study):
    b, sid = study
    out = S.step(b.store, sid, budget_s=100, cycle_seconds=120.0, now_ms=7, child=False)
    assert out['step'] == 'DEFERRED' and out['units'] == {} and out['stop_reason'] == 'busy_market_cycle'
    assert b.store.units(sid) == []


def test_completion_is_not_a_pass_when_nothing_is_positive(tmp_path, monkeypatch):
    b = build(tmp_path, name='neg', seal='SYNTHETIC')
    monkeypatch.setattr(S, 'develop', lambda cand, *a, **k: dict(
        schema='closure-development.v1', stage='DEVELOPMENT', candidate_id=cand['candidate_id'], outcome='NEGATIVE_ECONOMICS',
        envelope_id=b.env['envelope_id'], reason='FIXTURE', result_id=digest(['neg', cand['candidate_id']]), cache_key=digest(['neg-k', cand['candidate_id']]), cached=False, metrics=None))
    sid = S.create_study(b.store, spec_for(b, max_revisions=1), now_ms=1)
    out = S.step(b.store, sid, budget_s=10 ** 6, now_ms=2, child=False)
    assert out['process_status'] == 'COMPLETED' and out['research_outcome'] == 'NO_FINALIST'
    assert 'qualify' not in {u['unit_id'] for u in b.store.units(sid)}


def test_study_spec_and_create_are_idempotent(study):
    b, sid = study
    assert S.create_study(b.store, spec_for(b), now_ms=99) == sid and len(b.store.studies()) == 1
    with pytest.raises(ClosureRefused, match='study_spec_unknown_keys'):
        S.create_study(b.store, dict(spec_for(b), mystery=1), now_ms=1)


def test_scheduler_hook_is_opt_in_and_gates_stay_closed():
    cfg = load_config()
    assert (cfg['research'].get('closure') or {}).get('enabled', False) is False
    assert cfg['research']['referee'] is False and cfg['research']['handoff'] is False
    assert S.scheduled(cfg) == {'status': 'NOT_ENABLED'}


def test_real_entrypoint_runs_actual_evaluation_and_reports_blocked_real_policy(tmp_path, capsys):
    """Normal CLI -> real evaluator -> honest block under the CONFIGURED policy
    (16 development markets are not present in a 10-market snapshot)."""
    b = build(tmp_path, name='cli', seal='FORWARD')
    spec = spec_for(b, policy='CONFIGURED', seed_families=['trend_breakout.v1'])
    spec['workdir'] = str(tmp_path / 'wk')
    (tmp_path / 'spec.json').write_text(json.dumps(spec))
    store = str(tmp_path / 'cli.store')
    assert cli.main(['create', '--store', store, '--spec', str(tmp_path / 'spec.json')]) == 0
    sid = json.loads(capsys.readouterr().out)['study_id']
    assert cli.main(['step', '--store', store, '--study', sid, '--budget-s', '600']) == 0
    out = json.loads(capsys.readouterr().out)
    assert out['process_status'] == 'COMPLETED' and out['research_outcome'] == 'NO_FINALIST'
    assert cli.main(['status', '--store', store]) == 0
    assert 'production_gates' in json.loads(capsys.readouterr().out)
    from trader.research.closure.store import Store
    runs = Store(store).all_once('runs')
    assert runs[0]['outcome'] == 'BLOCKED_DATA' and 'development_blocked' in runs[0]['reason']


def test_slow_worker_is_actually_killed_at_the_unit_cap_and_attempts_are_bounded(tmp_path, monkeypatch):
    """Real spawned child that sleeps 120 s: the step returns near the cap, the
    unit keeps an explicit partial status, and repeated timeouts end in FAILED."""
    import time
    from tests import closure_fixtures as fx
    b = build(tmp_path, name='slow', seal='SYNTHETIC')
    monkeypatch.setattr(S, 'develop_job', fx.slow_job)
    sid = S.create_study(b.store, spec_for(b, unit_timeout_s=2, seed_families=['trend_breakout.v1']), now_ms=1)
    seen = []
    for i in range(4):
        t0 = time.monotonic()
        out = S.step(b.store, sid, budget_s=600, now_ms=10 + i)
        assert time.monotonic() - t0 < 40, 'the 120 s worker was not terminated'
        seen.append(out)
    assert seen[0]['step'] == 'UNIT_TIMEOUT'
    dev = next(u for u in b.store.units(sid) if u['kind'] == 'develop')
    assert dev['status'] == 'FAILED' and dev['attempts'] == S.MAX_UNIT_ATTEMPTS
    assert json.loads(dev['result'])['timed_out'] is True
    assert seen[-1]['research_outcome'] == 'NO_FINALIST' and seen[-1]['process_status'] == 'COMPLETED'


def test_step_budget_bounds_the_first_unit_and_retries_stay_bounded(tmp_path, monkeypatch):
    import time
    from tests import closure_fixtures as fx
    b = build(tmp_path, name='first', seal='SYNTHETIC')
    monkeypatch.setattr(S, 'develop_job', fx.slow_job)
    sid = S.create_study(b.store, spec_for(b, unit_timeout_s=900, seed_families=['trend_breakout.v1']), now_ms=1)
    t0 = time.monotonic()
    out = S.step(b.store, sid, budget_s=4, now_ms=2)           # no prior duration estimate exists
    assert time.monotonic() - t0 < 30 and out['step'] == 'DEADLINE'
    dev = next(u for u in b.store.units(sid) if u['kind'] == 'develop')
    assert dev['status'] == 'PENDING' and dev['attempts'] == 1 and json.loads(dev['result'])['hit_unit_cap'] is False
    t1 = time.monotonic()
    again = S.step(b.store, sid, budget_s=4, now_ms=3)         # known to need more than this budget: NOT relaunched
    assert time.monotonic() - t1 < 5 and again['step'] == 'BUDGET_INSUFFICIENT'
    assert next(u for u in b.store.units(sid) if u['kind'] == 'develop')['attempts'] == 1


def test_stop_request_single_flight_and_aggregate_slot_budget(study):
    b, sid = study
    out = S.step(b.store, sid, budget_s=100, should_stop=lambda: True, child=False)
    assert out['step'] == 'STOP_REQUESTED' and b.store.units(sid) == []
    with S.single_flight(b.store) as first:
        assert first is True
        again = S.step(b.store, sid, budget_s=100, child=False)               # a second concurrent study slice
        assert again['step'] == 'ALREADY_RUNNING'
    cfg = {'research': {'closure': {'enabled': True, 'store_path': str(b.store.path), 'study_id': sid, 'budget_s': 600}}}
    assert S.scheduled(cfg, slot_remaining_s=0.5)['reason'] == 'aggregate_slot_budget_exhausted'
    assert S.scheduled(cfg, should_stop=lambda: True)['status'] == 'STOP_REQUESTED'      # before any setup
    missing = {'research': {'closure': dict(cfg['research']['closure'], study_id='nope')}}
    assert S.scheduled(missing)['status'] == 'NOT_INITIALISED'                       # never builds a study in the kernel


def test_kernel_slice_runs_only_bounded_units_and_leaves_heavy_ones_to_the_operator(study, monkeypatch):
    b, sid = study
    cfg = {'research': {'closure': {'enabled': True, 'store_path': str(b.store.path), 'study_id': sid, 'budget_s': 600}}}
    kinds = []
    real = S._run_unit
    monkeypatch.setattr(S, '_run_unit', lambda store, study_id, st, env, unit, *a, **k: kinds.append(unit['kind']) or real(
        store, study_id, st, env, unit, *a, **k))
    monkeypatch.setattr(S, 'develop_job', lambda payload: positive(payload['candidate'], None, payload['env']))
    out = S.step(b.store, sid, budget_s=600, child=False, allow_heavy=False)
    assert out['step'] == 'REQUIRES_OPERATOR_STEP' and set(kinds) <= S.KERNEL_SAFE and 'qualify' not in kinds
    assert out['next_work'] == 'qualify'
    assert 'qualify' not in {u['unit_id'] for u in b.store.units(sid) if u['status'] == 'DONE'}
