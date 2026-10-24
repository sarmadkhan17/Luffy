"""Frozen protective hedge -> independent protected assessment -> pure verification -> typed Factory handling.

NOT YET RUN (worker-tests-permitted.json absent when written). Every FIXTURE is labelled: a hand-made HEDGE_SUPPORTED
development label only supplies a freeze for the REAL protected evaluator; no state label can reach QUALIFIED because no
statistical admission method is registered, and a TEST-ONLY packet can never become Factory authority."""
import copy
import json

import pytest

from tests.closure_fixtures import build
from trader.research.closure import ClosureRefused, development as D, digest, handoff, hedge_qualification as HQ, manifest
from trader.research.closure import synthetic as S
from trader.research.compose import graph, hedge as H
from trader.strategy import factory_handoff as fh


def result(metric='max_drawdown_pct', *, hedged=5.0, unhedged=9.0, reduced=7.0, net=1.0, entries=10, folds=(True, True, True, False),
           complete=True, liquidated=False):
    def m(v, ret=net):
        return dict(max_drawdown_pct=v, worst_bar_pct=v, cvar5_pct=v, stress_loss_pct=v, net_return_pct=ret, costs=0.0, mean_gross=1.0,
                    mean_abs_net=0.2, unknown_mark_bars=0 if complete else 3, risk_complete=complete, liquidated=liquidated,
                    entries=entries, rebalances=1)
    fr = [dict(bars=[i, i + 1], hedged=1.0 if w else 2.0, unhedged=2.0 if w else 1.0, better=w) for i, w in enumerate(folds)]
    res = dict(spec_id='s', objective=metric, comparators=dict(hedged=m(hedged), unhedged=m(unhedged, 2.0), reduced=m(reduced),
                                                               cash=dict(net_return_pct=0.0, max_drawdown_pct=0.0)),
               folds=fr, stress={}, return_sacrificed_pct=1.0)
    res['verdict'] = HQ.rederive_verdict(res, {})
    res['reason'] = 'fixture'
    return res


SPEC = dict(purpose='protective_beta', objective=dict(metric='max_drawdown_pct'))


def test_rule_is_deterministic_and_reports_cash_beside_the_others():
    st, why = HQ.assess(result(), SPEC)
    assert st == 'ASSESSED_SUPPORTED'
    assert HQ.assess(result(hedged=9.5), SPEC)[0] == 'ASSESSED_NOT_SUPPORTED'
    assert HQ.assess(result(entries=1), SPEC)[0] == 'INCONCLUSIVE'
    assert HQ.assess(result(complete=False), SPEC)[0] == 'INCONCLUSIVE'
    assert HQ.assess(result(liquidated=True), SPEC)[0] == 'ASSESSED_NOT_SUPPORTED'


def test_cash_dominance_needs_an_independently_predeclared_exposure_constraint():
    losing = result(net=-2.0)                                     # hedge reduces risk but loses money: cash has higher return, no greater risk
    assert HQ.assess(losing, SPEC)[0] == 'DOMINATED_BY_CASH'
    assert HQ.assess(losing, dict(SPEC, primary_exposure_constraint=dict(min_net_exposure=0.5)))[0] == 'ASSESSED_SUPPORTED'


def test_relative_value_is_not_judged_as_a_protective_hedge():
    assert HQ.assess(result(), dict(SPEC, purpose='relative_value'))[0] == 'BLOCKED'


def test_a_verdict_its_own_numbers_do_not_support_is_refused():
    bad = result()
    bad['verdict'] = 'HEDGE_SUPPORTED'
    bad['comparators']['hedged']['max_drawdown_pct'] = 99.0
    with pytest.raises(ClosureRefused, match='verdict_not_rederivable'):
        HQ.assess(bad, SPEC)


def test_no_admission_method_is_registered_so_nothing_can_be_qualified():
    assert HQ.ADMISSION_METHODS == {} and HQ.registered_method({'research': {'hedge_qualification': dict(
        approved=True, method_id='x', version='1', calibration_id='c')}}) is None


# ── REAL frozen -> protected path on causal synthetic envelopes (TEST_ONLY; plumbing proof, never a real qualification) ───────
import sqlite3
from types import SimpleNamespace

import numpy as np
import pandas as pd

from tests.closure_fixtures import RISK, DEV_BARS, N_BARS
from trader.core.child import ChildResult
from trader.research.closure import exposure
from trader.research.closure.store import Store

TF = S.TF_MS


def fresh(tmp):
    """A new closure store with the same declared seal: an independent look ledger over the SAME frozen envelope."""
    st = Store(tmp / 'fresh.closure')
    exposure.seal(st, 'SYNTHETIC_DECLARED', S.EPOCH_MS, S.EPOCH_MS + N_BARS * TF, at_ms=1)
    return st


def run_inline(fn, payload):
    """An in-process stand-in for the bounded child: failures come back as a failed ChildResult (as run_child reports them)."""
    try:
        return ChildResult(ok=True, value=fn(payload))
    except Exception as exc:                                                       # noqa: BLE001
        return ChildResult(ok=False, error=f'{type(exc).__name__}: {exc}')


def build_world(tmp, *, common, rule_rsi=50, risk=None, drop_hedge_bars=(), lever=None, protected_bars=None):
    """A synthetic study envelope whose two legs share a planted common factor (`common`); optionally a few hedge-leg bars are
    deleted INSIDE the protected window (candle row + receipts) so the leg clocks differ."""
    snap = tmp / 'snap.db'
    names = S.write_snapshot(snap, n_symbols=10, n_bars=N_BARS, seed=3, common=common, lever=lever)
    if drop_hedge_bars:
        st = S.EPOCH_MS
        with sqlite3.connect(snap) as db:
            for k in drop_hedge_bars:
                ts = st + (DEV_BARS + 100 + k) * TF
                db.execute("DELETE FROM candles WHERE symbol=? AND tf='4h' AND ts=?", (names[1], ts))
                db.execute("DELETE FROM market_revisions WHERE series_key=? AND event_ms=?", (f'candle:{names[1]}:4h', ts))
    store = Store(tmp / 'snap.closure')
    exposure.seal(store, 'SYNTHETIC_DECLARED', S.EPOCH_MS, S.EPOCH_MS + N_BARS * TF, at_ms=1)
    policy = manifest.test_policy(min_threshold_samples=1000, **({'min_protected_bars': 100} if protected_bars else {}))
    m = manifest.build_manifest(snap, '4h', names, exposure.events(store))
    st = S.EPOCH_MS
    p_end = st + ((DEV_BARS + 100 + protected_bars - 1) if protected_bars else (N_BARS - 1)) * TF
    env = manifest.make_envelope(
        m, snap, policy=policy, discovery_symbols=names[:6], heldout_symbols=names[6:], fit_start_ms=st,
        dev_end_ms=st + DEV_BARS * TF, embargo_bars=10, protected_start_ms=st + (DEV_BARS + 100) * TF,
        protected_end_ms=p_end, risk=risk or RISK, development_mode='REPLAY')
    return SimpleNamespace(store=store, snap=snap, names=names, env=env, manifest=m)


def hedge_candidate(names, rsi=50, method='ols_beta', **extra):
    ratio = dict(method=method, value=1.0, lookback=96, max_ratio=2.0)
    spec = H.make_spec(primary=dict(symbol=names[0]), hedge=dict(symbol=names[1]), purpose='protective_beta', account_mode='netting',
                       ratio=ratio, **extra)
    return graph.make_candidate(dict(question='does a hedge cut drawdown', rationale='r', expected='e', failure_conditions='f',
                                     structure='hedged', tf='4h', geo='fixed',
                                     long={'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'lit': rsi}}, short=None, hedge=spec))


def freeze_for(b, cand, derivs_path=None):
    b.store.put_once('candidates', cand['candidate_id'], cand)
    dev = D.develop(cand, b.snap, b.env, draws=30, derivs_path=derivs_path)
    assert dev['outcome'] != 'CODE_FAILURE', dev.get('reason')
    assert (dev.get('hedge') or {}).get('per_symbol'), f"development reached no hedge evidence: {dev['outcome']} {dev.get('reason')}"
    res = copy.deepcopy(dev)                      # FIXTURE: relabel supported; ratio/spec/combo are the REAL development ones
    res['outcome'] = 'HEDGE_SUPPORTED'
    res['result_id'] = digest({k: v for k, v in res.items() if k != 'result_id'})
    return HQ.freeze_hedge_finalist(b.store, b.env, [res])


LEVERED = {1: (0, 2.0)}          # the hedge leg is a 2x leveraged twin of the primary: a ratio of 0.5 cancels ALL of its risk


@pytest.fixture(scope='module')
def planted(tmp_path_factory):
    """Planted causal structure a hedge can use and size reduction cannot: with beta 0.5 on a 2x twin the book carries (nearly) no
    price risk while the exposure-matched reduced alternative still holds half the primary's. The primary-exposure constraint is
    declared in the frozen spec BEFORE any protected value exists."""
    b = build_world(tmp_path_factory.mktemp('hq-pos'), common=0.5, lever=LEVERED)
    return b, freeze_for(b, hedge_candidate(b.names, primary_exposure_constraint=dict(min_net_exposure=0.4)))


@pytest.fixture(scope='module')
def dominated(tmp_path_factory):
    b = build_world(tmp_path_factory.mktemp('hq-dom'), common=0.5, lever=LEVERED)
    return b, freeze_for(b, hedge_candidate(b.names))              # same hedge, NO declared exposure constraint


@pytest.fixture(scope='module')
def independent(tmp_path_factory):
    b = build_world(tmp_path_factory.mktemp('hq-neg'), common=0.0)
    return b, freeze_for(b, hedge_candidate(b.names))


def test_planted_common_factor_hedge_is_assessed_supported_through_the_real_protected_path(planted, tmp_path):
    b, fz = planted
    q = HQ.qualify_hedge(b.store, fz, b.env, b.snap, tmp_path / 'wk', now_ms=5, cfg={}, run=run_inline)
    assert q['status'] == 'ASSESSED_SUPPORTED', (q['status'], q['reason'])         # real evaluator, planted causal structure
    r = q['protected_result']
    assert r['verdict'] == 'HEDGE_SUPPORTED' and r['comparators']['hedged']['max_drawdown_pct'] < r['comparators']['unhedged']['max_drawdown_pct']
    assert 'cash' in r['comparators'] and r['funding_basis'] == 'CONSERVATIVE_FLAT_EIGHT_HOUR' and r['alignment']['bars'] > 1000
    assert r['comparators']['hedged']['net_return_pct'] < 0                          # a risk-free hedged book of a no-edge asset pays its costs
    assert q['statistical_admission']['status'] == 'NONE_REGISTERED' and q['status'] != 'QUALIFIED'
    HQ.verify_hedge_qualification(q)
    assert b.store.get_once('looks', b.env['envelope_id'])['kind'] == 'hedge_protective'


def test_the_same_hedge_without_a_predeclared_exposure_constraint_is_dominated_by_cash(dominated, tmp_path):
    b, fz = dominated
    q = HQ.qualify_hedge(b.store, fz, b.env, b.snap, tmp_path / 'wk', now_ms=5, cfg={}, run=run_inline)
    assert q['status'] == 'DOMINATED_BY_CASH', (q['status'], q['reason'])            # no constraint invented after the result
    assert q['protected_result']['comparators']['cash']['net_return_pct'] == 0.0
    HQ.verify_hedge_qualification(q)


def test_independent_legs_give_no_risk_benefit_and_are_not_supported(independent, tmp_path):
    b, fz = independent
    q = HQ.qualify_hedge(b.store, fz, b.env, b.snap, tmp_path / 'wk', now_ms=5, cfg={}, run=run_inline)
    assert q['status'] in ('ASSESSED_NOT_SUPPORTED', 'DOMINATED_BY_CASH'), (q['status'], q['reason'])
    assert q['status'] != 'ASSESSED_SUPPORTED'
    HQ.verify_hedge_qualification(q)


def test_look_is_charged_before_dispatch_and_timeout_or_crash_is_spent_inconclusive_with_no_retry(planted, tmp_path):
    b, fz = planted
    seen = {}
    st = fresh(tmp_path)

    def crashing(fn, payload):
        seen['look_before_dispatch'] = st.get_once('looks', b.env['envelope_id']) is not None
        return ChildResult(ok=False, error='boom', timed_out=True)
    q = HQ.qualify_hedge(st, fz, b.env, b.snap, tmp_path / 'wk-t', now_ms=6, cfg={}, run=crashing)
    assert seen['look_before_dispatch'] is True                                    # charged BEFORE the child ran
    assert q['status'] == 'INCONCLUSIVE' and 'timed_out' in q['reason'] and 'no retry' in q['reason']
    again = HQ.qualify_hedge(st, fz, b.env, b.snap, tmp_path / 'wk-t2', now_ms=7, cfg={}, run=run_inline)
    assert again == q                                                              # never retried, never re-evaluated


def test_the_protected_evaluator_never_runs_in_the_parent(planted, tmp_path):
    b, fz = planted
    st = fresh(tmp_path)
    calls = []
    orig = HQ.protected_evaluation
    HQ.protected_evaluation = lambda *a, **k: calls.append('parent') or orig(*a, **k)
    try:
        q = HQ.qualify_hedge(st, dict(fz), b.env, b.snap, tmp_path / 'wk-p', now_ms=8, cfg={},
                             run=lambda fn, payload: ChildResult(ok=False, error='not run'))
    finally:
        HQ.protected_evaluation = orig
    assert calls == [] and q['status'] in ('INCONCLUSIVE',)                         # recalled or child-refused; no parent callback


def write_funding_early(path, names, n_bars, pre=90):
    """8h signed funding with receipts (observed at settlement) that STARTS BEFORE the first scored bar, so every scored bar has a
    prior settlement (the study demands measured funding for every scored bar)."""
    from trader.data import market_provenance as mp
    from trader.data.derivatives import DerivFeed
    feed = DerivFeed(db_path=str(path))
    rng = np.random.default_rng(7)
    step = 8 * 3_600_000
    n = n_bars * TF // step + pre
    for sym in names:
        frames = []
        for k in range(n):
            ts = S.EPOCH_MS - pre * step + k * step
            row = pd.DataFrame(dict(ts=[pd.Timestamp(ts, unit='ms')], value=[float(rng.normal(0.0001, 0.0002))]))
            frames.append(mp.annotate(row, instrument_id=f'offline:deriv:{sym}', source='offline:synthetic-context', kind='derivative',
                                      received_ms=ts + step))
        df = pd.concat(frames, ignore_index=True)
        df.attrs.update(frames[0].attrs)
        feed.save(sym, 'funding', df)
    feed.db.close()


def test_real_signed_funding_is_bound_from_the_sealed_file_and_missing_funding_blocks_before_any_look(tmp_path):
    import shutil
    from tests import compose_fixtures as C
    risk = dict(RISK, real_funding=True)
    b = build_world(tmp_path, common=0.95, risk=risk)
    write_funding_early(tmp_path / 'f.db', b.names, N_BARS)
    shutil.copy(tmp_path / 'f.db', tmp_path / 'f-dev.db')            # each reader gets a quiescent copy (a read leaves a -wal)
    shutil.copy(tmp_path / 'f.db', tmp_path / 'f-prot.db')
    fz = freeze_for(b, hedge_candidate(b.names, primary_exposure_constraint=dict(min_net_exposure=0.4)), derivs_path=tmp_path / 'f-dev.db')
    st = fresh(tmp_path)
    blocked = HQ.qualify_hedge(st, fz, b.env, b.snap, tmp_path / 'wk0', now_ms=5, cfg={}, run=run_inline, derivs_path=None)
    assert blocked['status'] == 'BLOCKED' and 'no look spent' in blocked['reason']
    assert st.get_once('looks', b.env['envelope_id']) is None                          # no global fallback, nothing charged
    ok = HQ.qualify_hedge(st, fz, b.env, b.snap, tmp_path / 'wk1', now_ms=6, cfg={}, run=run_inline, derivs_path=tmp_path / 'f-prot.db')
    assert ok['status'] in ('ASSESSED_SUPPORTED', 'ASSESSED_NOT_SUPPORTED', 'DOMINATED_BY_CASH'), (ok['status'], ok['reason'])
    r = ok['protected_result']
    assert r is not None and r['funding_basis'] == 'RETAINED_SIGNED_HISTORY'               # both legs bound from the sealed file
    assert r['comparators']['hedged']['costs']['funding'] != 0                              # signed funding was actually charged


def test_missing_hedge_leg_bars_in_the_protected_window_stay_unknown_not_dropped(tmp_path):
    b = build_world(tmp_path, common=0.5, lever=LEVERED, drop_hedge_bars=(300, 301, 302))
    fz = freeze_for(b, hedge_candidate(b.names, primary_exposure_constraint=dict(min_net_exposure=0.4)))
    q = HQ.qualify_hedge(b.store, fz, b.env, b.snap, tmp_path / 'wk', now_ms=5, cfg={}, run=run_inline)
    assert q['status'] != 'BLOCKED' and q['protected_result'] is not None, (q['status'], q['reason'])   # the hedge WAS evaluated
    al = q['protected_result']['alignment']
    assert al['missing_hedge'] >= 3 and al['bars'] > al['missing_hedge']                # the hole is on the clock, not compacted away
    r = q['protected_result']
    held_through_hole = r['comparators']['hedged']['unknown_mark_bars'] > 0
    if held_through_hole:                                                                # a held leg's missing price is UNKNOWN equity
        assert q['status'] == 'INCONCLUSIVE' and 'held-leg marks are unknown' in q['reason']
    else:                                                                                # otherwise the book was flat across it
        assert r['comparators']['hedged']['risk_complete'] is True


def test_a_long_dependency_window_longer_than_the_protected_window_is_refused_before_any_look(tmp_path):
    b = build_world(tmp_path, common=0.5, lever=LEVERED, protected_bars=300)
    spec = H.make_spec(primary=dict(symbol=b.names[0]), hedge=dict(symbol=b.names[1]), purpose='protective_beta', account_mode='netting')
    cand = graph.make_candidate(dict(question='long dependency', rationale='r', expected='e', failure_conditions='f', structure='hedged',
                                     tf='4h', geo='fixed', long={'cmp': 'gt', 'a': {'f': 'emadist200'}, 'b': {'lit': 0}}, short=None, hedge=spec))
    fz = freeze_for(b, cand)
    q = HQ.qualify_hedge(b.store, fz, b.env, b.snap, tmp_path / 'wk', now_ms=5, cfg={}, run=run_inline)
    assert q['status'] == 'BLOCKED' and 'insufficient_protected_warmup' in q['reason'] and 'no look spent' in q['reason']
    assert b.store.get_once('looks', b.env['envelope_id']) is None                      # nothing spent on an empty-signal run


def test_rolling_beta_freezes_a_procedure_and_refuses_unfrozen_estimators(tmp_path):
    b = build_world(tmp_path, common=0.9)
    cand = hedge_candidate(b.names, method='rolling_beta')
    fz = freeze_for(b, cand)
    assert fz['ratio_frozen'] is None and fz['ratio_procedure']['method'] == 'rolling_beta'
    HQ.verify_hedge_freeze(fz, b.env)
    with pytest.raises(ClosureRefused):
        HQ.verify_hedge_freeze(dict(fz, ratio_procedure=dict(fz['ratio_procedure'], lookback=24)), b.env)
    spec = dict(fz['hedge_spec'], ratio=dict(fz['hedge_spec']['ratio'], method='ols_beta', fit_end=10))
    bad = dict(fz, hedge_spec=spec)
    bad['freeze_id'] = digest({k: v for k, v in bad.items() if k != 'freeze_id'})
    with pytest.raises(ClosureRefused):
        HQ.verify_hedge_freeze(bad, b.env)


def test_retrospective_world_is_never_a_protected_input(tmp_path):
    from trader.research.compose.world_snapshot import seal_world
    from trader.research.compose import world_producer as WPR
    rows = [(1, json.dumps(dict(as_of_ms=1, world_model={}, evidence_scope='retrospective_observation')))]
    src = tmp_path / 'w.db'
    WPR.write_store(src, rows)
    env = dict(split=dict(start_ms=0, end_ms=10), tf='4h')
    with pytest.raises(ClosureRefused, match='world_retrospective_not_protected_authority'):
        seal_world(src, tmp_path / 'sealed.db', env)


def test_aligned_clock_keeps_holes_and_refuses_entries_that_could_only_fill_after_a_gap():
    step = TF
    t0 = pd.Timestamp('2024-01-01', tz='UTC')
    def frame(slots):
        ts = [t0 + pd.Timedelta(milliseconds=step * k) for k in slots]
        px = 100 + np.arange(len(slots), dtype=float)
        return pd.DataFrame(dict(ts=ts, open=px, high=px + 1, low=px - 1, close=px, volume=1.0))
    df = frame(range(10))                                   # primary: every slot
    hdf = frame([0, 1, 2, 3, 5, 6, 7, 8, 9])                # hedge: slot 4 missing
    lo = np.zeros(10, bool)
    lo[3] = True                                            # signal on slot 3 would fill at slot 4's open: hedge has none
    lo[8] = True                                            # a clean entry later
    al = H.align_pair(df, hdf, lo, np.zeros(10, bool), step)
    assert al['bars'] == 10 and al['missing_hedge'] == 1 and np.isnan(al['h'][4]) and np.isnan(al['ho'][4])
    assert al['refused']['leg_open_missing_at_fill'] == 1 and al['sig'][3] == 0 and al['sig'][8] == 1
    lo2 = np.zeros(10, bool)
    lo2[4] = True                                           # signal slot itself has no hedge close
    al2 = H.align_pair(df, hdf, lo2, np.zeros(10, bool), step)
    assert al2['refused']['leg_close_missing_at_signal'] == 1 and not al2['sig'].any()
    shifted = frame(range(2, 12))                           # mismatched clocks: primary starts two slots later
    al3 = H.align_pair(shifted, df, np.zeros(10, bool), np.zeros(10, bool), step)
    assert al3['bars'] == 12 and al3['missing_primary'] == 2 and al3['missing_hedge'] == 2


def test_held_leg_gap_leaves_an_unknown_mark_so_the_book_cannot_claim_a_risk_benefit():
    step = TF
    n = 60
    p = 100 * np.cumprod(1 + np.random.default_rng(2).normal(0, .01, n))
    h = p.copy()
    h[30] = np.nan                                          # the hedge leg has no price while the book is held
    sig = np.zeros(n, int)
    sig[10] = 1
    spec = H.make_spec(primary=dict(symbol='A'), hedge=dict(symbol='B'), purpose='protective_beta', account_mode='netting',
                       ratio=dict(method='fixed', value=1.0), unwind=dict(max_hold_bars=40, min_corr=None, leg_timeout_bars=3,
                                                                          missing_data_bars=60))
    res = H.evaluate(spec, p, h, sig, np.zeros(n, bool), opens=(p, h))
    assert res['comparators']['hedged']['unknown_mark_bars'] >= 1 and res['verdict'] == 'INSUFFICIENT_EVIDENCE'


def test_a_registered_test_method_can_qualify_only_a_test_only_packet_which_the_factory_refuses(planted, monkeypatch):
    b, fz = planted
    monkeypatch.setitem(HQ.ADMISSION_METHODS, 'test-method', lambda core: dict(admitted=True, evidence=dict(note='TEST ONLY')))
    cfg = {'research': {'hedge_qualification': dict(approved=True, method_id='test-method', version='1', calibration_id='c1')}}
    q = HQ._packet('QUALIFIED', 'fixture admitted', b.env, fz, result=result(), method=HQ.registered_method(cfg),
                   admission=dict(note='TEST ONLY'))
    HQ.verify_hedge_qualification(q)
    out = handoff.hand_off_hedge(_Journal(), cfg, q)
    assert out['status'] == 'REFUSED' and out['reason_code'] == 'hq_test_only_policy_has_no_factory_authority'
    assert out['execution_authority'] == 'NONE'


class _Journal:
    """A hand-off must refuse before touching the journal; any use is a failure."""
    def __getattr__(self, name):
        raise AssertionError('journal touched by a refused hedge hand-off: ' + name)


def test_assessed_packets_and_uncalibrated_configs_are_refused_with_stable_codes(planted):
    b, fz = planted
    q = HQ._packet('ASSESSED_SUPPORTED', 'fixture', b.env, fz, result=result())
    out = handoff.hand_off_hedge(_Journal(), {}, q)
    assert out['status'] == 'REFUSED' and out['reason_code'] == 'hq_hedge_status_not_qualified:ASSESSED_SUPPORTED'
    forged = dict(q, status='QUALIFIED')
    out = handoff.hand_off_hedge(_Journal(), {}, forged)
    assert out['status'] == 'REFUSED' and out['reason_code'].startswith('hq_hedge_')           # integrity: evidence_id no longer matches


# ── the single-leg Factory path can never run one leg of a two-leg strategy ────────────────────────────────────
def test_multi_leg_versions_are_blocked_everywhere_a_single_leg_spec_could_run():
    plain = dict(lineage=dict(research=dict(origin='historical_qualification')))
    hedged = dict(lineage=dict(research=dict(origin='historical_hedge_qualification', multi_leg=dict(hedge_spec={}))))
    assert fh.multi_leg_block(plain) is None and fh.multi_leg_block({}) is None
    assert fh.multi_leg_block(hedged) == 'multi_leg_requires_coordinated_execution'
