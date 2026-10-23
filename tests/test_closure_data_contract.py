"""P1: snapshot-only manifest, controlled exposure, configured strictness."""
import os
import shutil
import sqlite3

import pytest

from tests.closure_fixtures import DEV_BARS, N_BARS, build, lowered_cfg
from trader.core.config import load_config
from trader.research.closure import ClosureRefused, exposure, manifest, synthetic as S


@pytest.fixture(scope='module')
def good(tmp_path_factory):
    return build(tmp_path_factory.mktemp('data'), name='good')


def test_live_named_hardlinked_symlinked_and_unquiescent_snapshots_refused(tmp_path, good):
    (tmp_path / 'a').mkdir()
    (tmp_path / 'b').mkdir()
    live = tmp_path / 'a' / 'candles.db'
    shutil.copy(good.snap, live)
    with pytest.raises(ClosureRefused, match='live_database_refused'):
        manifest.refuse_live(live)
    link = tmp_path / 'b' / 'link.db'
    os.link(good.snap, link)
    with pytest.raises(ClosureRefused, match='snapshot_hardlinked'):
        manifest.refuse_live(link)
    sym = tmp_path / 'b' / 'sym.db'
    sym.symlink_to(good.snap)
    with pytest.raises(ClosureRefused, match='snapshot_missing_or_symlink'):
        manifest.refuse_live(sym)
    copy = tmp_path / 'b' / 'copy.db'
    shutil.copy(good.snap, copy)
    (tmp_path / 'b' / 'copy.db-wal').write_bytes(b'x')
    with pytest.raises(ClosureRefused, match='snapshot_not_quiescent'):
        manifest.refuse_live(copy)
    with pytest.raises(ClosureRefused):
        manifest.refuse_live(tmp_path)


def test_manifest_separates_three_clocks_and_counts_eligibility(good):
    inv = good.manifest['symbols'][good.names[0]]
    assert inv['stored_bars'] == inv['eligible_bars'] == N_BARS and inv['gap_count'] == 0
    assert inv['availability_lag_ms']['median'] == 0 and inv['observed_range_ms'][0] and inv['first_available_ms']
    assert manifest.verify_manifest(good.manifest)


def test_unknown_exposure_is_never_protected_evidence(tmp_path):
    b = build(tmp_path, seal=None)
    assert b.env['protected_status'] == 'UNAVAILABLE'
    assert all('exposure_unknown' in r for r in b.env['protected_reasons'] if 'exposure' in r)
    assert b.env['development_status'] == 'ELIGIBLE'            # development needs no seal


def test_free_text_cannot_assert_unexposed(tmp_path):
    with pytest.raises(TypeError):
        exposure.seal(None, 'FORWARD_SEAL', 0, 1, evidence='pristine, trust me')
    with pytest.raises(ClosureRefused):
        exposure.seal(__import__('trader.research.closure.store', fromlist=['x']).Store(tmp_path / 's'),
                      'ASSERTED_BY_CALLER', 0, 1, 1)


def test_forward_seal_must_precede_availability(tmp_path, monkeypatch):
    late = build(tmp_path, name='late', seal=None)
    st = S.EPOCH_MS
    with pytest.raises(ClosureRefused, match='seal_clock_is_trusted_only'):
        exposure.seal(late.store, 'FORWARD_SEAL', st, st + N_BARS * S.TF_MS, at_ms=1)        # no backdating argument
    monkeypatch.setattr(exposure, '_now', lambda: 10 ** 15)
    exposure.seal(late.store, 'FORWARD_SEAL', st, st + N_BARS * S.TF_MS)                   # sealed AFTER the data existed
    m = manifest.build_manifest(late.snap, '4h', late.names, exposure.events(late.store))
    env = manifest.make_envelope(m, late.snap, policy=manifest.test_policy(min_threshold_samples=1000),
                                 discovery_symbols=late.names[:6], heldout_symbols=late.names[6:], fit_start_ms=st,
                                 dev_end_ms=st + DEV_BARS * S.TF_MS, embargo_bars=10,
                                 protected_start_ms=st + 1100 * S.TF_MS, protected_end_ms=st + (N_BARS - 1) * S.TF_MS,
                                 risk=late.env['risk'])
    assert env['protected_status'] == 'UNAVAILABLE' and set(env['exposure_proofs'].values()) == {None}
    early = build(tmp_path, name='early', seal='FORWARD')
    assert early.env['protected_status'] == 'ELIGIBLE'
    assert set(early.env['exposure_proofs'].values()) == {'FORWARD_SEAL'}


def test_synthetic_declaration_has_no_force_under_configured_class(tmp_path):
    b = build(tmp_path, klass='CONFIGURED', seal='SYNTHETIC')
    assert b.env['policy_class'] == 'CONFIGURED' and b.env['protected_status'] == 'UNAVAILABLE'
    assert any('exposure_unknown' in r for r in b.env['protected_reasons'])


def test_production_strictness_is_bound_not_weakened(tmp_path):
    cur = manifest.configured_policy(load_config())
    assert cur['min_discovery_symbols'] == 16 and cur['min_threshold_samples'] == 5000 and cur['min_protected_bars'] >= 2070
    b = build(tmp_path, klass='CONFIGURED', seal='FORWARD', policy=cur)
    assert b.env['development_status'] == 'BLOCKED' and b.env['protected_status'] == 'UNAVAILABLE'
    assert any('below_16' in r for r in b.env['development_reasons'])
    weaker = dict(cur, min_discovery_symbols=6)
    assert 'min_discovery_symbols_weaker' in manifest.not_weaker(weaker, cur)
    assert manifest.not_weaker(dict(cur, fdr_target=.5), cur) == ['fdr_target_weaker']
    assert manifest.not_weaker(manifest.test_policy(), cur) == ['policy_not_configured_class']
    assert manifest.not_weaker(manifest.configured_policy(lowered_cfg()), cur)       # lowered cfg is weaker


def test_projection_counts_are_measured_not_arithmetic(tmp_path):
    b = build(tmp_path, name='full')
    full = b.env['projection_counts']
    holes = tmp_path / 'holes.db'
    shutil.copy(b.snap, holes)
    st = S.EPOCH_MS
    lo = st + 2000 * S.TF_MS
    with sqlite3.connect(holes) as db:                               # remove 40 protected bars of one held-out market
        victim = b.names[7]
        db.execute('DELETE FROM market_revisions WHERE series_key=? AND event_ms>=? AND event_ms<?',
                   (f'candle:{victim}:4h', lo, lo + 40 * S.TF_MS))
        db.execute('DELETE FROM candles WHERE symbol=? AND ts>=? AND ts<?', (victim, lo, lo + 40 * S.TF_MS))
    m = manifest.build_manifest(holes, '4h', b.names, exposure_events=b.manifest['exposure_events'])
    env = manifest.make_envelope(m, holes, policy=b.env['policy'], discovery_symbols=b.names[:6],
                                 heldout_symbols=b.names[6:], fit_start_ms=st, dev_end_ms=st + DEV_BARS * S.TF_MS,
                                 embargo_bars=10, protected_start_ms=st + 1100 * S.TF_MS,
                                 protected_end_ms=st + (N_BARS - 1) * S.TF_MS, risk=b.env['risk'])
    assert env['projection_counts']['a'][b.names[7]] == full['a'][b.names[7]] - 40
    assert env['projection_counts']['a'][b.names[6]] == full['a'][b.names[6]]
    assert any('gaps' in r or 'coverage' in r or 'unqualified' in r for r in env['protected_reasons']) is False \
        or env['protected_status'] == 'UNAVAILABLE'


def test_protected_and_future_mutation_cannot_reach_development(tmp_path):
    b = build(tmp_path, name='orig')
    frames, data_id = manifest.development_frames(b.snap, b.env)
    mutated = tmp_path / 'mut.db'
    shutil.copy(b.snap, mutated)
    cut = S.EPOCH_MS + (DEV_BARS + 50) * S.TF_MS
    with sqlite3.connect(mutated) as db:
        db.execute('UPDATE candles SET close=close*3, high=high*3 WHERE ts>=?', (cut,))
        db.execute('DELETE FROM market_revisions WHERE event_ms>=?', (cut,))
    f2, id2 = manifest.development_frames(mutated, b.env)
    assert id2 == data_id and all(frames[s].equals(f2[s]) for s in frames)
    step = S.TF_MS
    assert all(int(manifest._ms(df['ts']).max()) + step <= b.env['dev_end_ms'] for df in f2.values())
    with pytest.raises(ClosureRefused, match='snapshot_changed_since_manifest'):    # sealing re-verifies identity
        manifest.freeze_window(mutated, tmp_path / 'p.db', b.env)


def test_changed_development_rows_are_refused_and_identities_invalidate(tmp_path, monkeypatch):
    b = build(tmp_path, name='dev')
    edited = tmp_path / 'edited.db'
    shutil.copy(b.snap, edited)
    with sqlite3.connect(edited) as db:
        db.execute('UPDATE candles SET close=close*1.01 WHERE symbol=? AND ts=?', (b.names[0], S.EPOCH_MS + 500 * S.TF_MS))
        rid, rec = db.execute('SELECT revision_id,record_json FROM market_revisions WHERE series_key=? AND event_ms=?',
                              (f'candle:{b.names[0]}:4h', S.EPOCH_MS + 500 * S.TF_MS)).fetchone()
        import json
        r = json.loads(rec)
        r['close'] *= 1.01
        db.execute('UPDATE market_revisions SET record_json=? WHERE revision_id=?', (json.dumps(r), rid))
    with pytest.raises(ClosureRefused, match='development_data_changed'):
        manifest.development_frames(edited, b.env)
    assert manifest.evaluator_id() == b.env['evaluator_id']
    monkeypatch.setattr(manifest, 'evaluator_id', lambda: 'other')
    from trader.research.closure import development
    with pytest.raises(ClosureRefused, match='evaluator_changed_since_envelope'):
        development.require_current(b.env)


def test_evaluator_identity_covers_closure_and_fill_layers():
    import hashlib
    from pathlib import Path
    base = manifest.evaluator_id()
    here = Path(manifest.__file__)
    assert base != hashlib.sha256(b'').hexdigest()
    src = here.read_text()
    assert "'strategy/fill_timing.py'" in src and "'strategy/factory_handoff.py'" in src and "here.glob('*.py')" in src


def test_retrospective_development_without_receipts_but_never_protected(tmp_path):
    b = build(tmp_path, name='retro', mode='RETROSPECTIVE')
    stripped = tmp_path / 'stripped.db'
    shutil.copy(b.snap, stripped)
    with sqlite3.connect(stripped) as db:
        db.execute('DELETE FROM market_revisions')
        db.execute('DELETE FROM market_raw_sources')
    m = manifest.build_manifest(stripped, '4h', b.names, b.manifest['exposure_events'])
    st = S.EPOCH_MS
    kw = dict(policy=b.env['policy'], discovery_symbols=b.names[:6], heldout_symbols=b.names[6:], fit_start_ms=st,
              dev_end_ms=st + DEV_BARS * S.TF_MS, embargo_bars=10, protected_start_ms=st + 1100 * S.TF_MS,
              protected_end_ms=st + (N_BARS - 1) * S.TF_MS, risk=b.env['risk'])
    retro = manifest.make_envelope(m, stripped, development_mode='RETROSPECTIVE', **kw)
    assert retro['development_status'] == 'ELIGIBLE'
    frames, _ = manifest.development_frames(stripped, retro)
    assert len(frames) == 6 and all(df.attrs['read_mode'] == 'retrospective' for df in frames.values())
    assert retro['protected_status'] == 'UNAVAILABLE'                    # provenance is never downgraded
    replay = manifest.make_envelope(m, stripped, development_mode='REPLAY', **kw)
    assert replay['development_status'] == 'BLOCKED'


def test_exposure_history_is_chained_and_rewrites_are_detected(tmp_path):
    b = build(tmp_path, name='chain', seal='FORWARD')
    ev = exposure.events(b.store)
    assert ev[0]['prev'] is None and exposure.verify_chain(ev)
    exposure.mark(b.store, 'DEVELOPMENT_EXPOSED', 0, 1, 5, 'x')
    ev = exposure.events(b.store)
    assert ev[1]['prev'] == ev[0]['event_id']
    bad = [dict(ev[0], at_ms=10 ** 15)] + ev[1:]                         # backdating/rewrite
    with pytest.raises(ClosureRefused, match='exposure_chain_broken'):
        exposure.verify_chain(bad)


def test_rehashing_policy_class_cannot_promote_synthetic_exposure(tmp_path):
    b = build(tmp_path, name='promote', seal='SYNTHETIC')
    assert set(b.env['exposure_proofs'].values()) == {'SYNTHETIC_DECLARED'}
    assert manifest.rederive_proofs(b.env) == b.env['exposure_proofs']
    promoted = dict(b.env, policy=dict(b.env['policy'], klass='CONFIGURED'), policy_class='CONFIGURED')
    assert set(manifest.rederive_proofs(promoted).values()) == {None}      # events cannot prove a synthetic seal as real


def test_configured_live_directory_signature_refused_without_hardcoded_mount(tmp_path):
    d = tmp_path / 'datadir'
    d.mkdir()
    (d / 'luffy.db').write_bytes(b'')
    snap = d / 'something.db'
    shutil.copy(build(tmp_path, name='tmpl').snap, snap)
    with pytest.raises(ClosureRefused, match='live_data_directory_refused'):
        manifest.refuse_live(snap)
