"""CoinGecko published TOTAL CSV -> detached reference store -> retrospective development.

Synthetic CSV fixtures only (planted random walk proves mechanism, not edge).
The real downloaded file is exercised for parsing/inventory in a temp store."""
import hashlib
import os
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tests import compose_fixtures as C
from trader.data import chart_exports, published_csv as P
from trader.data.references import DAY, REFS, RefStore
from trader.research.closure import ClosureRefused, development as D, manifest, synthetic as S
from trader.research.closure.store import Store
from trader.research.compose import catalog, data as cdata, graph
from trader.strategy.compile import compile_spec

KEY = 'cg_total_daily'
TF = S.TF_MS
CAPTURED = 1_790_000_000_000          # 2026-09 receipt for a 2020-era fixture: acquisition known "now", never backdated
REAL = Path(__file__).resolve().parents[3] / 'outputs' / 'published-market-context' / 'CoinGecko-GlobalCryptoMktCap-2026-10-23.csv'


def write_csv(path, caps, first_day_ms, vols=None, header='snapped_at,market_cap,total_volume', mtime=1_000_000_000):
    lines = [header]
    for i, c in enumerate(caps):
        lines.append(f'{first_day_ms + i * DAY},{float(c)!r},{0.0 if vols is None else float(vols[i])!r}')
    Path(path).write_text('\n'.join(lines) + '\n')
    os.utime(path, (mtime, mtime))
    return Path(path)


def walk(n, seed=11):
    return 1e12 * np.exp(np.cumsum(np.random.default_rng(seed).normal(0, 0.02, n)))


def csv_for(b, tmp, name='cg.csv', seed=11, mutate=None):
    first = S.EPOCH_MS // DAY * DAY - 45 * DAY
    n = (b.env['protected_window'][1] - first) // DAY + 3
    caps = walk(n, seed)
    if mutate:
        caps = mutate(caps, first)
    return write_csv(Path(tmp) / name, caps, first, vols=np.r_[0.0, np.full(n - 1, 5e10)]), caps, first


@pytest.fixture(scope='module')
def ctx(tmp_path_factory):
    b = C.build_ctx(tmp_path_factory.mktemp('cgctx'), mode='null', n_bars=1500)
    st = S.EPOCH_MS
    retro = manifest.make_envelope(
        b.manifest, b.snap, policy=manifest.test_policy(min_threshold_samples=1000), discovery_symbols=b.names[:6],
        heldout_symbols=b.names[6:], fit_start_ms=st, dev_end_ms=st + C.DEV_BARS * TF, embargo_bars=10,
        protected_start_ms=st + (C.DEV_BARS + 100) * TF, protected_end_ms=st + (1500 - 1) * TF, risk=C.RISK,
        development_mode='RETROSPECTIVE')
    assert retro['development_status'] == 'ELIGIBLE', retro['development_reasons']
    b.retro = retro
    return b


def imp(ctx, tmp, name='refs.db', captured=CAPTURED, **kw):
    src, caps, first = csv_for(ctx, tmp, **kw)
    db = Path(tmp) / name
    res = P.import_csv(src, db, captured_ms=captured)
    return db, src, caps, first, res


def rule(factor='ctx_cg_total_ret30'):
    f = {'f': factor}
    return graph.make_candidate(dict(question='q', rationale='r', expected='e', failure_conditions='f', structure='long_short',
                                     tf='4h', geo='fixed', long={'cmp': 'gt', 'a': f, 'b': {'lit': 0}},
                                     short={'cmp': 'lt', 'a': f, 'b': {'lit': 0}}))


def entries(b, cand, ext):
    frames, _ = manifest.development_frames(b.snap, b.retro)
    bundle = D.build_bundle(frames, b.retro, ext)
    comp = compile_spec(graph.realize(cand, 'x').to_spec())
    sym = b.names[0]
    lo, sh = comp.entries(bundle.sym_frames[sym], btc=bundle.btc, derivs=bundle.derivs.get(sym), universe=bundle.universe,
                          market=bundle.market, symbol=sym, world=None)
    return np.asarray(lo, bool), np.asarray(sh, bool), bundle.frames[sym]


def retro_contract(env):
    return dict(dataset_id='closure:' + env['manifest_id'][:16], cut_ms=int(env['dev_end_ms']), start_ms=int(env['fit_window'][0]))


# ── import contract ───────────────────────────────────────────────────────
def test_import_preserves_bytes_values_and_capture_identity(ctx, tmp_path):
    db, src, caps, first, res = imp(ctx, tmp_path)
    raw = src.read_bytes()
    assert res['csv_sha256'] == hashlib.sha256(raw).hexdigest() and res['captured_ms'] == CAPTURED
    assert res['vintage'] == 'AS_RETURNED_AT_CAPTURE' and res['rows_stored'] == res['rows_in_file']
    con = sqlite3.connect(db)
    texts = [r[0] for r in con.execute('SELECT raw_json FROM market_raw_sources')]
    assert any(hashlib.sha256(__import__('json').loads(t)['raw_csv'].encode()).hexdigest() == res['csv_sha256']
               for t in texts if t.startswith('{') and 'raw_csv' in t)                 # exact original text retained
    stored = dict(con.execute('SELECT ts, close FROM refs WHERE key=?', (KEY,)).fetchall())
    assert stored[first + 7 * DAY] == caps[7]                                           # published value, bit-exact
    ob = {r[0] for r in con.execute("SELECT DISTINCT observed_ms FROM market_revisions")}
    assert ob == {CAPTURED}                                                             # receipt = capture; no backdating
    assert not any(r[0] for r in con.execute('SELECT open FROM refs WHERE open IS NOT NULL'))   # no fabricated OHLC
    con.close()


def test_snapshot_definition_is_distinct_from_other_providers():
    ref = REFS[KEY]
    assert ref.source == 'coingecko-website-csv' and ref.close_only and ref.close_after_ms == DAY
    assert KEY not in ('cg_total', 'tv_total') and REFS['cg_total'].tf == 'snap' and REFS['tv_total'].source == 'tradingview-export'


@pytest.mark.parametrize('body,why', [
    ('snapped_at,market_cap,total_volume\n1600041600000,0,1\n', 'positive'),
    ('snapped_at,market_cap,total_volume\n1600041600000,nan,1\n', 'positive'),
    ('snapped_at,market_cap,total_volume\n1600041600000,5,-1\n', 'non-negative'),
    ('snapped_at,market_cap,total_volume\n1600041600000,5,inf\n', 'non-negative'),
    ('snapped_at,market_cap,total_volume\n1600041600000,5,1\n1600041600000,6,1\n', 'duplicates'),
    ('snapped_at,market_cap,total_volume\n1600128000000,5,1\n1600041600000,6,1\n', 'unordered'),
    ('snapped_at,market_cap,total_volume\n1600041600001,5,1\n', '00:00 UTC'),
    ('snapped_at,market_cap,total_volume\n1600041600,5,1\n', 'milliseconds'),
    ('snapped_at,cap,total_volume\n1600041600000,5,1\n', 'header'),
    ('snapped_at,market_cap,total_volume\n1600041600000,abc,1\n', 'non-numeric'),
    ('snapped_at,market_cap,total_volume\n', 'no rows'),
    ('snapped_at,market_cap,total_volume\n1900022400000,5,1\n', 'after the capture'),
])
def test_invalid_files_are_refused_not_repaired(tmp_path, body, why):
    f = tmp_path / 'x.csv'
    f.write_text(body)
    os.utime(f, (1_000_000_000, 1_000_000_000))
    with pytest.raises(ValueError, match=why):
        P.load_csv(f, captured_ms=CAPTURED)


def test_zero_historical_volume_allowed_and_capture_cannot_be_future_or_backdated(tmp_path):
    f = write_csv(tmp_path / 'ok.csv', [5.0, 6.0], 1_600_041_600_000, vols=[0.0, 3.0])
    key, df = P.load_csv(f, captured_ms=CAPTURED)
    assert key == KEY and df['volume'].tolist() == [0.0, 3.0]
    with pytest.raises(ValueError, match='future'):
        P.load_csv(f, captured_ms=CAPTURED, now_ms=CAPTURED - 10 * 60_000)
    new = write_csv(tmp_path / 'new.csv', [5.0], 1_600_041_600_000, mtime=CAPTURED // 1000 + 3600)
    with pytest.raises(ValueError, match='backdated'):
        P.load_csv(new, captured_ms=CAPTURED)


def test_import_refuses_live_or_production_targets(ctx, tmp_path):
    src, *_ = csv_for(ctx, tmp_path)
    for bad in (tmp_path / 'candles.db', Path('/mnt/luffy-data/LUFFY/data/research-refs.db')):
        with pytest.raises(ValueError, match='live or production'):
            P.import_csv(src, bad, captured_ms=CAPTURED)
    (tmp_path / 'live').mkdir()
    (tmp_path / 'live' / 'luffy.db').write_bytes(b'')
    with pytest.raises(ValueError, match='live or production'):
        P.import_csv(src, tmp_path / 'live' / 'refs.db', captured_ms=CAPTURED)


def test_unclosed_capture_day_is_not_stored_but_hash_covers_whole_file(tmp_path):
    f = write_csv(tmp_path / 'u.csv', [5.0, 6.0, 7.0], 1_600_041_600_000)
    captured = 1_600_041_600_000 + 2 * DAY + 3_600_000       # last row's day not closed
    res = P.import_csv(f, tmp_path / 'r.db', captured_ms=captured, now_ms=captured)
    assert (res['rows_in_file'], res['rows_stored'], res['rows_not_closed_at_capture']) == (3, 2, 1)
    assert res['csv_sha256'] == hashlib.sha256(f.read_bytes()).hexdigest()


@pytest.mark.skipif(not REAL.exists(), reason='real downloaded evidence not present')
def test_real_downloaded_export_parses_and_inventories(tmp_path):
    res = P.import_csv(REAL, tmp_path / 'real.db', captured_ms=int(REAL.stat().st_mtime * 1000) + 1000)
    assert res['csv_sha256'] == 'c3a57c76dbdf71f37e27f965aca5b4e09051afcb15823e3efd50c0da750b60fe'
    assert res['rows_in_file'] == 4927 and res['calendar_gap_count'] == 0 and res['first_ms'] == 1_367_107_200_000


# ── retrospective development ─────────────────────────────────────────────
def test_published_values_reach_actual_dsl_entries_in_retrospective_development(ctx, tmp_path):
    db, _, caps, first, _ = imp(ctx, tmp_path)
    cand = rule()
    ext = cdata.load_external(graph.requires_of(cand), ctx.snap, None, ctx.names[:6], int(ctx.retro['dev_end_ms']),
                              references_path=db, retrospective=retro_contract(ctx.retro))
    assert not ext['gaps'] and ext['market'][KEY].attrs['read_mode'] == 'retrospective'
    lo, sh, frame = entries(ctx, cand, ext)
    close = mp_ms(frame) + TF
    known = first + np.arange(len(caps)) * DAY + DAY                  # published day D is known one full day later
    j = np.searchsorted(known, close, side='right') - 1
    ret30 = np.full(len(close), np.nan)
    ok = j >= 30
    assert ok.all()                                                    # warmup history before the window reached the first bar
    ret30[ok] = caps[j[ok]] / caps[j[ok] - 30] - 1.0                  # independent recomputation from the raw file values
    sl = slice(300, None)
    assert lo.sum() > 100 and sh.sum() > 100
    assert (lo[sl] == (ret30[sl] > 0)).all() and (sh[sl] == (ret30[sl] < 0)).all()


def test_future_days_and_unclosed_day_never_reach_development(ctx, tmp_path):
    db, _, caps, first, _ = imp(ctx, tmp_path)
    cut = int(ctx.retro['dev_end_ms'])
    ext = cdata.load_external(['ref:' + KEY], ctx.snap, None, ctx.names[:6], cut, references_path=db,
                              retrospective=retro_contract(ctx.retro))
    mk = ext['market'][KEY]
    assert int(mp_ms(mk).max()) + DAY <= cut and len(mk) < len(caps) and int(mp_ms(mk).min()) < S.EPOCH_MS   # warmup before the window is served, never after the cut
    # a different FUTURE (after the cut) changes no development identity
    db2, *_ = imp(ctx, tmp_path, name='refs2.db', mutate=lambda c, f0: np.where(f0 + np.arange(len(c)) * DAY > cut, c * 3, c))
    ext2 = cdata.load_external(['ref:' + KEY], ctx.snap, None, ctx.names[:6], cut, references_path=db2,
                               retrospective=retro_contract(ctx.retro))
    assert ext2['provenance'][f'ref:{KEY}']['sources'][0]['csv_sha256'] != ext['provenance'][f'ref:{KEY}']['sources'][0]['csv_sha256']
    assert ext2['market'][KEY]['close'].tolist() == mk['close'].tolist()
    # store-level: an explicit start bound excludes early rows
    store = RefStore(db_path=str(db), retrospective=dict(retro_contract(ctx.retro), start_ms=first + 20 * DAY))
    early = store.load(KEY)
    store.db.close()
    assert mp_ms(early).min() >= first + 20 * DAY


def mp_ms(df):
    from trader.data import market_provenance as mp
    return mp.ms(df['ts'])


def test_new_capture_cannot_serve_historical_replay(ctx, tmp_path):
    db, *_ = imp(ctx, tmp_path)
    cut = int(ctx.retro['dev_end_ms'])
    plain = RefStore(db_path=str(db), read_only=True)
    served = plain.load(KEY, as_of_ms=cut)
    plain.db.close()
    assert served is None or not len(served)                          # received in 2026: nothing was available in 2020
    retro = RefStore(db_path=str(db), retrospective=retro_contract(ctx.retro))
    with pytest.raises(ValueError, match='cannot serve historical as-of'):
        retro.load(KEY, as_of_ms=cut)
    retro.db.close()
    with pytest.raises(ClosureRefused, match='references_path_requires_retrospective'):
        D.develop(rule(), ctx.snap, ctx.env, references_path=db)       # REPLAY envelope refuses a detached store
    with pytest.raises(ClosureRefused):
        cdata.load_external(['ref:' + KEY], ctx.snap, None, ctx.names[:6], cut, references_path=db)
    res = D.develop(rule(), ctx.snap, ctx.env, draws=50)               # REPLAY, snapshot has no such series
    assert res['outcome'] == 'BLOCKED_DATA' and 'ref:' + KEY in res['reason']


def test_changed_provenance_or_values_change_identity_and_cache_key(ctx, tmp_path):
    cand = rule()
    ids = {}
    for name, kw in (('a', {}), ('later', dict(captured=CAPTURED + 3_600_000)), ('edited', dict(seed=12))):
        db, *_ = imp(ctx, tmp_path, name=f'{name}.db', **kw)
        ext = cdata.load_external(graph.requires_of(cand), ctx.snap, None, ctx.names[:6], int(ctx.retro['dev_end_ms']),
                                  references_path=db, retrospective=retro_contract(ctx.retro))
        ids[name] = (ext['data_id'], D.cache_key(cand, ctx.retro, 'd', ext['data_id']))
    assert len({v[0] for v in ids.values()}) == 3 and len({v[1] for v in ids.values()}) == 3


def test_development_result_carries_capture_provenance_and_blocks_only_dependents(ctx, tmp_path):
    db, src, *_ = imp(ctx, tmp_path)
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    ok = D.develop(rule(), ctx.snap, ctx.retro, draws=50, references_path=db)
    assert ok['outcome'] not in ('BLOCKED_DATA', 'CODE_FAILURE') and ok['evidence_scope'] == 'retrospective_observation'
    entry = ok['context_provenance']['ref:' + KEY]                      # present whatever the trade count
    assert entry['sources'][0]['csv_sha256'] == sha and entry['sources'][0]['captured_ms'] == CAPTURED
    assert entry['sources'][0]['vintage'] == 'AS_RETURNED_AT_CAPTURE' and entry['read_mode'] == 'retrospective'
    tv = graph.make_candidate(dict(question='q', rationale='r', expected='e', failure_conditions='f', structure='long_short',
                                   tf='4h', geo='fixed', long={'cmp': 'gt', 'a': {'f': 'ctx_total'}, 'b': {'lit': 0}},
                                   short={'cmp': 'lt', 'a': {'f': 'ctx_total'}, 'b': {'lit': 0}}))
    blocked = D.develop(tv, ctx.snap, ctx.retro, draws=50, references_path=db)
    assert blocked['outcome'] == 'BLOCKED_DATA' and any(g['requirement'] == 'ref:tv_total' for g in blocked['context_gaps'])
    plain = D.develop(graph.make_candidate(dict(question='q', rationale='r', expected='e', failure_conditions='f',
                      structure='long_short', tf='4h', geo='fixed', long={'cmp': 'gt', 'a': {'f': 'rsi14'}, 'b': {'lit': 70}},
                      short={'cmp': 'lt', 'a': {'f': 'rsi14'}, 'b': {'lit': 30}})), ctx.snap, ctx.retro, draws=50)
    assert plain['outcome'] not in ('BLOCKED_DATA', 'CODE_FAILURE')


def test_no_local_ex_btc_calculation_and_tradingview_binding_untouched():
    cg = [f for f in catalog.FACTORS if 'ref:cg_total_daily' in f.requires]
    assert {f.key for f in cg} == {'ctx_cg_total', 'ctx_cg_total_ret30'} and all('cg_total_daily' in f.formula for f in cg)
    assert catalog.BY_KEY['ctx_total'].requires == ('ref:tv_total',) and catalog.BY_KEY['ctx_total'].provider == 'TradingView-CRYPTOCAP'
    for k in ('ctx_total1', 'ctx_total2', 'ctx_btc_dominance'):
        assert 'cg_' not in catalog.BY_KEY[k].formula
    assert not [k for k in REFS if k.startswith('cg_') and ('ex' in k or 'total1' in k or 'total3' in k)]
    src = Path(P.__file__).read_text()
    assert 'btc' not in src.lower().replace('ex-btc', '').replace('ex-btc-eth', '').replace('ex-btc /', '')


def test_replay_qualification_context_never_reads_detached_store(ctx, tmp_path):
    db, *_ = imp(ctx, tmp_path)
    env = dict(split=ctx.env['split'], tf='4h', discovery_symbols=ctx.names[:6], heldout_symbols=ctx.names[6:])
    res = manifest.freeze_context(ctx.snap, None, tmp_path / 'c.db', tmp_path / 'd.db', env, ['ref:' + KEY])
    assert any(g['requirement'] == 'ref:' + KEY for g in res['gaps'])    # the snapshot has no such series: blocked, not served


def test_study_refuses_detached_store_outside_retrospective(ctx, tmp_path):
    from trader.research.closure import study
    st = Store(tmp_path / 's.closure')
    with pytest.raises(ClosureRefused, match='references_path_requires_retrospective'):
        study.create_study(st, dict(snapshot_path=str(ctx.snap), tf='4h', references_path=str(tmp_path / 'r.db'),
                                    development_mode='REPLAY'), now_ms=1)


# ── existing chart_exports loader ─────────────────────────────────────────
def chart_csv(tmp_path, rows, header='time,open,high,low,close,volume', name=None):
    f = tmp_path / (name or f'chart-{abs(hash(tuple(rows)))}.csv')   # one file per fixture: never overwrite another case
    f.write_text(header + '\n' + '\n'.join(rows) + '\n')
    return f


def test_chart_export_numeric_and_iso_timestamps_convert_to_same_utc_instants(tmp_path):
    t0 = 1_600_041_600
    num = chart_csv(tmp_path, [f'{t0},1,2,0.5,1.5,10', f'{t0 + 86400},1.5,2,1,1.2,11'], name='numeric.csv')
    iso = chart_csv(tmp_path, ['2020-09-14T00:00:00Z,1,2,0.5,1.5,10', '2020-09-15T00:00:00+00:00,1.5,2,1,1.2,11'], name='iso.csv')
    cap = (t0 + 3 * 86400) * 1000
    _, a = chart_exports.load_export(num, symbol='CRYPTOCAP:TOTAL', timeframe='1d', captured_ms=cap)
    _, b = chart_exports.load_export(iso, symbol='CRYPTOCAP:TOTAL', timeframe='1d', captured_ms=cap)
    assert mp_ms(a).tolist() == mp_ms(b).tolist() == [t0 * 1000, (t0 + 86400) * 1000]


def test_chart_export_drops_unclosed_bar_and_refuses_ambiguous_time(tmp_path):
    t0 = 1_600_041_600
    f = chart_csv(tmp_path, [f'{t0},1,2,0.5,1.5,10', f'{t0 + 86400},1.5,2,1,1.2,11'])
    _, df = chart_exports.load_export(f, symbol='CRYPTOCAP:TOTAL', timeframe='1d', captured_ms=(t0 + 86400) * 1000 + 3600_000)
    assert len(df) == 1                                              # second day not closed at capture
    with pytest.raises(ValueError, match='timezone'):
        chart_exports.load_export(chart_csv(tmp_path, ['2020-09-14 00:00:00,1,2,0.5,1.5,10']), symbol='CRYPTOCAP:TOTAL',
                                  timeframe='1d', captured_ms=CAPTURED)
    with pytest.raises(ValueError, match='seconds'):
        chart_exports.load_export(chart_csv(tmp_path, [f'{t0 * 1000},1,2,0.5,1.5,10']), symbol='CRYPTOCAP:TOTAL',
                                  timeframe='1d', captured_ms=CAPTURED)
