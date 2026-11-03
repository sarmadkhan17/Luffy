"""Backfill metadata must not become an impossible replay-power requirement."""
import shutil
import sqlite3

from tests.closure_fixtures import build, DEV_BARS, N_BARS
from trader.research.closure import manifest, development, synthetic as S


def test_backfill_power_inventory_does_not_grant_protected_eligibility(tmp_path):
    b = build(tmp_path, name='before')
    snap = tmp_path / 'backfilled.db'
    shutil.copyfile(b.snap, snap)
    with sqlite3.connect(snap) as db:
        db.execute('DELETE FROM market_revisions')
        db.execute('DELETE FROM market_raw_sources')
        # A real inventory hole must change the count, not be filled by span.
        db.execute('DELETE FROM candles WHERE symbol=? AND ts=?',
                   (b.names[6], S.EPOCH_MS + 1500*S.TF_MS))
    m = manifest.build_manifest(snap, '4h', b.names, b.manifest['exposure_events'])
    st = S.EPOCH_MS
    kw = dict(policy=b.env['policy'], discovery_symbols=b.names[:6], heldout_symbols=b.names[6:],
        fit_start_ms=st, dev_end_ms=st+DEV_BARS*S.TF_MS, embargo_bars=10,
        protected_start_ms=st+1100*S.TF_MS, protected_end_ms=st+(N_BARS-1)*S.TF_MS,
        risk=b.env['risk'])
    retro = manifest.make_envelope(m, snap, development_mode='RETROSPECTIVE', **kw)
    assert all(n == 0 for part in retro['projection_counts'].values() for n in part.values())
    counts = development.heldout_counts(retro)
    assert counts['a'][b.names[6]] == counts['a'][b.names[7]] - 1
    assert counts['a'][b.names[7]] > 1000
    assert retro['development_projection_basis'] == 'retrospective_coverage_metadata'
    assert retro['protected_status'] == 'UNAVAILABLE'
    assert retro['protected_fingerprint'] is None
    assert retro['development_status'] == 'ELIGIBLE'
    assert manifest.verify_envelope(retro)
    # Replay research cannot consume this inventory as replay evidence.
    replay = dict(retro, development_mode='REPLAY')
    assert development.heldout_counts(replay) == retro['projection_counts']
    # An existing frozen envelope is never silently reinterpreted.
    old = {k:v for k,v in retro.items() if k not in ('development_projection_counts', 'development_projection_basis')}
    assert development.heldout_counts(old) == retro['projection_counts']


def test_replay_counts_and_basis_unchanged(tmp_path):
    b = build(tmp_path)
    assert b.env['development_projection_counts'] == b.env['projection_counts']
    assert b.env['development_projection_basis'] == 'retained_point_in_time'
    assert development.heldout_counts(b.env) == b.env['projection_counts']


def test_power_diagnostics_explains_short_window_without_waiving_floor():
    env = dict(development_mode='RETROSPECTIVE',
        development_projection_counts={'a': {'X': 1448}, 'b': {'X': 622}},
        projection_counts={'a': {'X': 0}, 'b': {'X': 19}},
        development_projection_basis='retrospective_coverage_metadata', protected_status='UNAVAILABLE')
    res = dict(median_rate=17/1410, projection={'markets_a': 1, 'markets_b': 0})
    report = development.power_diagnostics(res, env)
    assert report['minimum_bars_per_market'] == 874
    assert report['min_projected_trades'] == 8 and report['min_markets_per_part'] == 4
    assert report['replay_eligible_bars']['a']['X'] == 0
    assert report['protected_status'] == 'UNAVAILABLE'
    assert development.power_diagnostics(dict(median_rate=0), env)['minimum_bars_per_market'] is None
