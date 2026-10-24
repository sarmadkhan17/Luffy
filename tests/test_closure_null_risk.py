"""Frozen research risk is normalised BEFORE hashing: absent/null values from the real auto
config->envelope route never reach development or qualification as None."""
import pytest

from tests.closure_fixtures import RISK, build
from trader.core.config import load_config
from trader.research.closure import ClosureRefused, development as D, manifest


@pytest.fixture(scope='module')
def b(tmp_path_factory):
    return build(tmp_path_factory.mktemp('nrisk'), name='nrisk', n_bars=2300)


def envelope(b, risk):
    e = b.env
    return manifest.make_envelope(
        b.manifest, b.snap, policy=e['policy'], discovery_symbols=e['discovery_symbols'], heldout_symbols=e['heldout_symbols'],
        fit_start_ms=e['fit_window'][0], dev_end_ms=e['dev_end_ms'], embargo_bars=e['embargo_bars'],
        protected_start_ms=e['protected_window'][0], protected_end_ms=e['protected_window'][1], risk=risk,
        development_mode=e['development_mode'])


def test_absent_and_null_values_become_explicit_numbers(b):
    cfg_risk = {k: v for k, v in load_config()['risk'].items() if k in manifest.RESEARCH_COST_KEYS + ('risk_per_trade_pct',)}
    for risk in (cfg_risk, dict(cfg_risk, max_open_positions=8)):
        r = envelope(b, risk)['risk']
        assert r['max_open_trades'] == 8 and r['real_funding'] is False and isinstance(r['max_open_trades'], int)
        assert None not in r.values()


def test_real_auto_config_route_envelope_bundle_and_cost_identity(b, tmp_path):
    cfg = load_config()
    from trader.research.closure import auto
    spec_risk = {k: v for k, v in cfg['risk'].items() if k in auto.RISK_KEYS}
    assert 'max_open_positions' in spec_risk and 'max_open_trades' not in spec_risk and 'real_funding' not in spec_risk
    env = envelope(b, spec_risk)
    assert env['risk']['max_open_trades'] == cfg['risk']['max_open_positions']
    assert env['risk']['taker_fee_pct'] == cfg['risk']['taker_fee_pct']
    assert env['risk']['funding_rate_8h'] == cfg['risk']['funding_rate_8h']
    frames, _ = manifest.development_frames(b.snap, env)
    bundle = D.build_bundle(frames, env)
    assert bundle.max_open == 8
    assert env['cost_id'] == manifest.digest(env['risk'])
    assert D.research_cfg(env)['risk'] == env['risk']          # development and qualification both read this block
    assert slim_matches(cfg, env)
    manifest.verify_envelope(env)


def slim_matches(cfg, env):
    from trader.research.closure import auto
    return env['risk'] == manifest.research_risk(auto._slim(cfg)['risk'])


def test_explicit_nondefault_values_preserved(b):
    risk = dict(RISK, max_open_trades=3, real_funding=True, risk_per_trade_pct=.25, taker_fee_pct=.07)
    env = envelope(b, dict(risk, max_open_positions=8))
    assert env['risk'] == dict(risk) and env['risk']['max_open_trades'] == 3
    assert D.build_bundle(manifest.development_frames(b.snap, env)[0], env).max_open == 3
    assert env['cost_id'] != b.env['cost_id']


@pytest.mark.parametrize('bad', [dict(max_open_trades=None), dict(max_open_trades=0), dict(max_open_trades=2.5),
                                 dict(max_open_trades='8'), dict(real_funding=None), dict(real_funding='yes'),
                                 dict(taker_fee_pct=None), dict(taker_fee_pct=-1), dict(risk_per_trade_pct=float('nan')),
                                 dict(max_open_trades=True)])
def test_invalid_values_refused_typed(b, bad):
    with pytest.raises(ClosureRefused) as e:
        envelope(b, dict(RISK, **bad))
    assert e.value.code == 'risk_value_invalid'


def test_handoff_comparison_uses_same_normalisation(b):
    risk = load_config()['risk']
    assert b.env['risk'] == manifest.research_risk(dict(risk, **RISK))
