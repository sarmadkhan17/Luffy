"""Shared fixtures for the closure tests. Everything here is SYNTHETIC: data are
generated, receipts are first-close test receipts, and anything labelled
FIXTURE (a hand-made development result, a scripted referee reading) exists
only to drive the verifiers and is never evidence of an edge."""
import copy
from types import SimpleNamespace

from trader.core.config import load_config
from trader.research import referee
from trader.research.closure import exposure, families, manifest, qualification, synthetic as S
from trader.research.closure.store import Store

RISK = dict(risk_per_trade_pct=.5, taker_fee_pct=.04, slippage_atr_frac=.015, funding_rate_8h=.0001,
            real_funding=False, max_open_trades=8)
N_BARS, DEV_BARS = 3300, 1000


def lowered_cfg():
    """The real configuration with ONLY the strictness knobs a 10-market
    synthetic snapshot can carry lowered. Used to prove binding to whatever
    configuration is in force; production config is untouched."""
    cfg = copy.deepcopy(load_config())
    cfg['risk'] = dict(cfg.get('risk') or {}, **RISK)
    cfg['research'] = dict(cfg.get('research') or {}, min_discovery_symbols=6, min_threshold_samples=1000,
                           fdr_target=.5, lord_w0=.4)
    return cfg


def build(tmp, *, name='snap', edge=0.0, common=0.0, seed=3, seal='SYNTHETIC', klass='TEST_ONLY', n_sym=10,
          n_bars=N_BARS, mode='REPLAY', policy=None):
    snap = tmp / f'{name}.db'
    names = S.write_snapshot(snap, n_symbols=n_sym, n_bars=n_bars, seed=seed, edge=edge, common=common)
    store = Store(tmp / f'{name}.closure')
    st = S.EPOCH_MS
    if seal == 'SYNTHETIC':
        exposure.seal(store, 'SYNTHETIC_DECLARED', st, st + n_bars * S.TF_MS, at_ms=1)
    elif seal == 'FORWARD':
        old = exposure._now
        exposure._now = lambda: 1                  # trusted clock set BEFORE the (2020-era) synthetic availability
        try:
            exposure.seal(store, 'FORWARD_SEAL', st, st + n_bars * S.TF_MS)
        finally:
            exposure._now = old
    if policy is None:
        policy = manifest.configured_policy(lowered_cfg()) if klass == 'CONFIGURED' else manifest.test_policy(min_threshold_samples=1000)
    m = manifest.build_manifest(snap, '4h', names, exposure.events(store))
    env = manifest.make_envelope(
        m, snap, policy=policy, discovery_symbols=names[:6], heldout_symbols=names[6:], fit_start_ms=st,
        dev_end_ms=st + DEV_BARS * S.TF_MS, embargo_bars=10, protected_start_ms=st + (DEV_BARS + 100) * S.TF_MS,
        protected_end_ms=st + (n_bars - 1) * S.TF_MS, risk=RISK, development_mode=mode)
    return SimpleNamespace(store=store, snap=snap, names=names, manifest=m, env=env)


def fixture_freeze(b, family='trend_breakout.v1', params=None):
    """FIXTURE: a hand-made DEVELOPMENT_POSITIVE result (no state-label path to
    QUALIFIED exists; this only supplies a freeze for the real qualifier)."""
    from trader.research.closure import digest
    cand = families.make_candidate(family, params)
    b.store.put_once('candidates', cand['candidate_id'], cand)
    c = families.realize(cand, {}, b.env['envelope_id'])
    res = dict(schema='closure-development.v1', stage='DEVELOPMENT', candidate_id=cand['candidate_id'],
               envelope_id=b.env['envelope_id'], outcome='DEVELOPMENT_POSITIVE', combo=c.as_dict(), combo_hash=c.hash,
               threshold_fit={}, metrics=dict(total_pct=12.0, max_dd_pct=6.0, consistency_p=0.01, median_pf=1.4,
                                              trades=120, scored_symbols=6, testable=True))
    res['result_id'] = digest(res)
    return qualification.freeze_finalist(b.store, b.env, [res])


def scripted_referee(passing=True, calls=None):
    """FIXTURE: scripted referee reading fed to the real qualify() bookkeeping."""
    def run(fn, payload, **_):
        if calls is not None:
            calls.append(payload['combo']['parts'] if 'parts' in payload['combo'] else 1)
        tiny = 1e-5 if passing else 0.6
        leg = dict(trades=120, consistency_p=tiny, consistency_p_dep=tiny, scored_symbols=8, rho_bar=.1, n_eff=6)
        rot = dict(p=tiny, draws=payload['draws'], actual_total_pct=20.0, null_median=2.0, trades=100)
        g1 = referee.gate1(leg, dict(leg), rot)
        g3 = dict(passed=passing, reason='fixture book', without={}, **{'with': {}})
        from trader.research.combo import Combination
        c = Combination.from_dict(payload['combo'])
        return SimpleNamespace(ok=True, error=None, value=dict(hash=c.hash, looked=True, a=leg, b=dict(leg),
                               rotation=rot, gate1=g1, gate3=g3, cut_ms=payload['cut_ms']))
    return run


def approval(tmp, env, **over):
    """FIXTURE: a retained 'CALIBRATED' report. No harness run produced it; it only
    exercises the gate that CONSULTS an approval (negative variants cover refusal)."""
    from trader.research.closure import digest
    rep = dict(schema='closure-calibration-report.v2', calibration_status='CALIBRATED', kind='FINAL',
               numerics='EXACT_CONFIGURED', authority_granted=False, numeric_id=env['numeric_id'],
               evaluator_id=env['evaluator_id'])
    rep.update(over)
    rep['report_id'] = digest(rep)
    path = tmp / f'calibration-{rep["report_id"][:10]}.json'
    import json
    path.write_text(json.dumps(rep))
    return dict(report_path=str(path), report_id=rep['report_id'])


def gated_cfg(approval_record=None, referee=True, handoff=True):
    cfg = lowered_cfg()
    cfg['research'].update(referee=referee, handoff=handoff, closure=dict(approved_calibration=approval_record))
    return cfg


def source_journal(tmp, name='src.db', prior=None):
    """A source Journal carrying the registered budget (and optional prior spent looks)."""
    from trader.core.journal import Journal
    from trader.research.ledger import Ledger
    j = Journal(tmp / name)
    led = Ledger(j)
    led.ensure()
    led.register_budget(.5, .4)
    for i, (p, alpha, cut) in enumerate(prior or ()):
        led.record_test(f'prior{i}', '4h', 'fixed', 'gate1', p, alpha, False, dict(look_state='complete', cut_ms=cut))
    return tmp / name


def incumbent_book():
    """FIXTURE: a declared incumbent book (one comparable spec)."""
    from trader.research.combo import Combination
    cand = families.make_candidate('trend_breakout.v1', dict(channel=20, trend='st:above_ema50', geo='trail'))
    return [families.realize(cand, {}, 'incumbent').to_spec().to_dict()]


def slow_job(payload):
    import time
    time.sleep(120)
    return {}
