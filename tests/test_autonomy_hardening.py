"""Hardening probes for the autonomous scheduler: stable snapshot proof, one deadline, resume, derivative copy, backoff,
allowlisted child config, cumulative resources / actual fit counts, study-owned lineage. Cheap; no heavy study."""
import json
import sqlite3
from pathlib import Path

import pytest

from tests import compose_fixtures as C
from trader.research.closure import auto, manifest, study as S
from trader.research.closure.store import Store
from trader.research.compose import search as csearch

TINY = 0.0005     # GiB reserve for tests


def wal_db(path, rows=40):
    con = sqlite3.connect(path)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA wal_autocheckpoint=0')
    con.execute('CREATE TABLE t(x INTEGER)')
    con.executemany('INSERT INTO t VALUES (?)', [(i,) for i in range(rows)])
    con.commit()
    return con


def count(path):
    with sqlite3.connect(path) as db:
        return db.execute('SELECT COUNT(*) FROM t').fetchone()[0]


def test_changes_during_the_copy_refuse_the_snapshot_instead_of_mixing_it(tmp_path):
    con = wal_db(tmp_path / 'src.db')
    # (a) WAL generation restart (a checkpoint truncating the WAL) while the copy runs
    with pytest.raises(Exception, match='auto_source_unstable'):
        auto.copy_database(tmp_path / 'src.db', tmp_path / 's' / 'a.db', min_free_bytes=1,
                           _between=lambda: con.execute('PRAGMA wal_checkpoint(TRUNCATE)'))
    # (b) a rollback-mode source rewritten mid-copy
    plain = tmp_path / 'plain.db'
    c2 = sqlite3.connect(plain)
    c2.execute('CREATE TABLE t(x INTEGER)')
    c2.executemany('INSERT INTO t VALUES (?)', [(i,) for i in range(40)])
    c2.commit()

    def rewrite():
        c2.execute('INSERT INTO t VALUES (999)')
        c2.commit()
    with pytest.raises(Exception, match='auto_source_unstable'):
        auto.copy_database(plain, tmp_path / 's' / 'b.db', min_free_bytes=1, _between=rewrite)
    assert not [p for p in (tmp_path / 's').glob('*')]                       # nothing partial or mixed is left behind
    c2.close()
    con.close()


def test_wal_growth_within_one_generation_copies_a_bounded_prefix_consistently(tmp_path):
    con = wal_db(tmp_path / 'src.db', rows=40)
    out = auto.copy_database(tmp_path / 'src.db', tmp_path / 's' / 'ok.db', min_free_bytes=1,
                             _between=lambda: (con.execute('INSERT INTO t VALUES (1000)'), con.commit()))
    assert count(out['path']) == 40                                          # rows committed AFTER the prefix are not half-copied
    assert out['consistency'] == 'STABLE_SOURCE_VERIFIED_BEFORE_AND_AFTER'
    con.close()


def test_copy_stops_at_the_callers_deadline_and_cleans_up(tmp_path):
    con = wal_db(tmp_path / 'src.db', rows=500)
    import time
    with pytest.raises(Exception, match='auto_prep_deadline'):
        auto.copy_database(tmp_path / 'src.db', tmp_path / 's' / 'd.db', min_free_bytes=1, deadline=time.monotonic() - 1)
    assert not list((tmp_path / 's').glob('*'))
    with pytest.raises(Exception, match='auto_disk_space_insufficient'):
        auto.copy_database(tmp_path / 'src.db', tmp_path / 's' / 'e.db', min_free_bytes=10 ** 15)
    con.close()


def auto_cfg(tmp, **over):
    a = dict(enabled=True, snapshot_dir=str(tmp / 'snaps'), market_db=str(tmp / 'missing.db'), min_free_gb=TINY)
    a.update(over)
    return dict(research=dict(closure=dict(enabled=True, store_path=str(tmp / 'a.closure'), auto=a)), risk={})


def test_one_deadline_and_resume_from_a_ready_snapshot_without_recopying(tmp_path, monkeypatch):
    cfg = auto_cfg(tmp_path)
    st = Store(tmp_path / 'a.closure')
    a = auto.settings(cfg)
    out = auto.tick(cfg, st, a, now_ms=1, budget_s=5, child=False)                 # too little slot left: no copy is started
    assert out['status'] == 'DEFERRED_BUDGET' and not (tmp_path / 'snaps').exists()
    # a copy exists but creation could not run in the previous slot: the next slot RESUMES from it
    con = wal_db(tmp_path / 'src.db')
    snap = auto.copy_database(tmp_path / 'src.db', tmp_path / 'snaps' / 'candles-1.db', min_free_bytes=1)
    st.log_schedule(2, 'SNAPSHOT_READY', detail=dict(candles=snap['path'], derivs=None))
    monkeypatch.setattr(auto, 'snapshot_job', lambda p: pytest.fail('copied the database again'))
    monkeypatch.setattr(auto, 'publish_job', lambda p: pytest.fail('published again'))
    monkeypatch.setattr(auto, 'create_job', lambda p: dict(blocked='insufficient_covered_symbols: test'))
    out = auto.tick(cfg, st, a, now_ms=3, budget_s=600, child=False)
    assert out['status'] == 'DATA_BLOCKED' and out['detail']['snapshot'] == snap['path']
    assert not Path(snap['path']).exists()                                        # a blocked copy is freed immediately
    con.close()


def test_failed_and_blocked_preparations_back_off_instead_of_recopying_every_slot(tmp_path, monkeypatch):
    cfg = auto_cfg(tmp_path, interval_hours=12, retry_hours=1)
    st = Store(tmp_path / 'a.closure')
    a = auto.settings(cfg)
    hour = 3_600_000
    st.log_schedule(0, 'PREP_FAILED', detail=dict(stage='snapshot'))
    assert auto.due(st, a, 30 * 60_000)[0] is False and auto.due(st, a, hour)[0] is True
    st.log_schedule(hour, 'DATA_BLOCKED', detail=dict(reason='x', snapshot='s'))
    assert auto.due(st, a, hour + hour)[0] is False and auto.due(st, a, 3 * hour)[0] is True       # 2h after the 2nd failure
    st.log_schedule(3 * hour, 'PREP_FAILED', detail={})
    assert auto.due(st, a, 3 * hour + 3 * hour)[0] is False and auto.due(st, a, 7 * hour)[0] is True  # 4h after the 3rd
    st.log_schedule(7 * hour, 'STUDY_CREATED', detail=dict(snapshot='z'))
    assert auto.due(st, a, 7 * hour + 11 * hour)[0] is False and auto.due(st, a, 19 * hour)[0] is True  # success waits the full interval
    monkeypatch.setattr(auto, 'snapshot_job', lambda p: pytest.fail('copied while backing off'))
    monkeypatch.setattr(auto, 'publish_job', lambda p: pytest.fail('published while backing off'))
    assert auto.tick(cfg, st, a, now_ms=7 * hour + hour, budget_s=600, child=False)['status'] == 'IDLE'


def test_derivative_copy_taken_with_the_candles_is_what_the_study_reads(tmp_path, monkeypatch):
    snap = tmp_path / 'live' / 'm.db'
    snap.parent.mkdir()
    C.write_context_snapshot(snap, n_symbols=10)
    monkeypatch.setattr(manifest, 'configured_policy', lambda cfg: manifest.test_policy())
    a = dict(auto.DEFAULTS, snapshot_dir=str(tmp_path / 'snaps'), market_db=str(snap), derivs_path='/live/derivs.db', fit_bars=900)
    out = auto.derive_spec(a, {'risk': {}}, snap, [], derivs_snapshot='/snaps/derivs-1.db')
    assert out['spec']['derivs_path'] == '/snaps/derivs-1.db'                      # never the live path when a copy exists
    assert auto.derive_spec(a, {'risk': {}}, snap, [])['spec']['derivs_path'] == '/live/derivs.db'


def test_child_configuration_is_an_allowlist_never_the_whole_config():
    cfg = dict(research=dict(min_discovery_symbols=16, seed=3, api_token='SECRET', closure=dict(auto=dict(x=1)), notes='n'),
               risk=dict(taker_fee_pct=0.05, exchange_secret='SECRET2'), exchange=dict(api_key='K'))
    slim = auto._slim(cfg)
    assert slim == dict(research=dict(min_discovery_symbols=16, seed=3), risk=dict(taker_fee_pct=0.05))
    assert 'SECRET' not in json.dumps(slim) and 'K' not in json.dumps(slim).replace('taker', '')


def test_resources_accumulate_across_timeouts_and_interrupted_attempts(tmp_path):
    st = Store(tmp_path / 'r.closure')
    a = st.start_resource('s', 'develop:x', 'develop', 1_000)
    st.finish_resource(a, 2_000, 40.0, 'TIMEOUT')                                 # a timed-out attempt keeps what it spent
    st.start_resource('s', 'develop:x', 'develop', 10_000)                        # process died: still RUNNING
    st.close_interrupted('s', 'develop:x', 10_000 + 500_000, cap_s=300)           # charged up to the unit cap, not free
    b = st.start_resource('s', 'develop:x', 'develop', 600_000)
    st.finish_resource(b, 610_000, 10.0, 'DONE')
    assert st.resource_seconds('s') == pytest.approx(40 + 300 + 10)
    assert csearch.spent(st, 's', units=[dict(result=json.dumps(dict(elapsed_s=10.0)))])['cpu_seconds'] == pytest.approx(350.0)
    budget = dict(csearch.DEFAULT_BUDGET, cpu_seconds=345)
    assert csearch.exhausted(st, 's', budget) == 'BUDGET_EXHAUSTED:cpu_seconds'    # restart cannot reset the spent compute


def test_fit_budget_counts_actual_walk_forward_fits_and_reads_follow_the_loaders():
    ridge = dict(model='ridge', folds=4, inputs=[], side='long', horizon=6)
    grid = dict(ridge, grid={'alpha': [0.1, 1.0, 10.0]})
    assert csearch._fits(dict(long=dict(fit_spec=ridge), short=None)) == 5                # 5 segments x 1 final fit
    assert csearch._fits(dict(long=dict(fit_spec=grid), short=dict(fit_spec=ridge))) == 5 * 4 + 5    # grid: 3 inner + final
    assert csearch._fits(dict(long={'cmp': 'gt'}, short=None)) == 0
    from trader.research.compose import graph
    c = graph.make_candidate(dict(question='q', rationale='r', expected='e', failure_conditions='f', structure='long_only', tf='4h',
                                  geo='fixed', long={'cmp': 'gt', 'a': {'f': 'ctx_cg_total_ret30'}, 'b': {'lit': 0}}, short=None))
    assert csearch.reads_of(c, symbols=6) == 6 + 1                                        # candles per symbol + one reference store key


def test_a_compiled_item_belongs_to_the_study_that_consumed_it(tmp_path):
    st = Store(tmp_path / 'o.closure')
    for sid in ('A', 'B'):
        with st._tx() as c:
            c.execute("INSERT INTO studies(study_id,payload,status) VALUES (?,?,?)", (sid, '{}', 'CREATED'))
    own = dict(payload=json.dumps(dict(study_id='A')))
    legacy = dict(payload=json.dumps({}))
    assert S._owned(st, 'A', own) and not S._owned(st, 'B', own)                          # no cross-study inheritance
    assert not S._owned(st, 'B', legacy) and not S._owned(st, 'A', legacy)                # legacy attributed only if one study exists
    with st._tx() as c:
        c.execute("DELETE FROM studies WHERE study_id='B'")
    assert S._owned(st, 'A', legacy)


def test_owner_evidence_projects_graph_models_alternatives_regimes_lineage_and_resources(tmp_path):
    from trader.research.closure import digest
    st = Store(tmp_path / 'e.closure')
    cand = dict(candidate_id='c1', question='does context lead', structure='long_short', long={'cmp': 'gt'}, short=None, roles={'x': 'condition'})
    st.put_once('candidates', 'c1', cand)
    run = dict(stage='DEVELOPMENT', candidate_id='c1', envelope_id='env1', outcome='INCONCLUSIVE_POWER', reason='too few trades',
               requires=['ohlcv', 'ref:cg_total_daily'], metrics=dict(trades=3), baselines={'x': dict(verdict='scored')},
               graph_fit=dict(models=[dict(artifact_id='m1')], walk_forward=[dict(folds=[])]),
               diagnostics=dict(regime=dict(states={}), mtm=dict(mtm_max_dd_pct=1.2)), result_id='r1', data_id='d1',
               context_provenance={'ref:cg_total_daily': dict(sources=[dict(csv_sha256='h')])})
    st.put_once('runs', 'k1', run)
    with st._tx() as c:
        c.execute('INSERT INTO studies(study_id,payload,status) VALUES (?,?,?)', ('S', json.dumps(dict(envelope_id='env1', origin='auto')), 'IN_PROGRESS'))
    a = st.start_resource('S', 'develop:c1', 'develop', 1)
    st.finish_resource(a, 2, 5.0, 'TIMEOUT')
    st.close()
    ev = auto.evidence(tmp_path / 'e.closure')
    [s] = ev['studies']
    [c] = s['candidates']
    assert c['question'] == 'does context lead' and c['outcome'] == 'INCONCLUSIVE_POWER' and c['reason'] == 'too few trades'
    assert c['graph']['long'] == {'cmp': 'gt'} and c['models'][0]['artifact_id'] == 'm1' and c['alternatives']['x']['verdict'] == 'scored'
    assert c['mtm']['mtm_max_dd_pct'] == 1.2 and 'ref:cg_total_daily' in c['input_lineage'] and 'not qualification' in c['authority']
    assert s['resources'] == dict(attempts=1, seconds=5.0, interrupted_or_failed_attempts=1, note=s['resources']['note'])
    # runs of OTHER envelopes never appear under this study
    st2 = Store(tmp_path / 'e.closure')
    st2.put_once('runs', 'k2', dict(run, envelope_id='other', candidate_id='c2'))
    st2.close()
    assert [x['candidate_id'] for x in auto.evidence(tmp_path / 'e.closure')['studies'][0]['candidates']] == ['c1']


# ── bounded exact-cut publication through the reader contract (no physical copy of the cache) ───────────────
def wal_context(tmp, n_bars=400):
    src = tmp / 'live' / 'candles-src.db'
    src.parent.mkdir()
    C.write_context_snapshot(src, n_symbols=4, n_bars=n_bars)
    con = sqlite3.connect(src)
    con.execute('PRAGMA journal_mode=WAL')
    con.commit()
    return src, con


def test_published_snapshot_is_one_consistent_exact_cut_bounded_to_the_declared_rows(tmp_path):
    from trader.research.closure import publish
    src, _ = wal_context(tmp_path)
    live = sqlite3.connect(src)
    before = live.execute("SELECT COUNT(*) FROM candles WHERE tf='4h'").fetchone()[0]

    def writer_commits_during_publish():                       # a collector commits NEW bars after the snapshot instant
        w = sqlite3.connect(src)
        w.execute("INSERT INTO candles(symbol,tf,ts,open,high,low,close,volume,taker_buy) "
                  "VALUES ('S0/USDT','4h',9999999999999,1,1,1,1,1,1)")
        w.commit()
        w.close()
    out = publish.publish_exact_cut(src, tmp_path / 'snaps' / 'candles-1.db', tf='4h', window_bars=120,
                                    _between=writer_commits_during_publish)
    with sqlite3.connect(out['path']) as db:
        n = db.execute('SELECT COUNT(*) FROM candles').fetchone()[0]
        assert n == 4 * 121 or n <= 4 * 121                                  # bounded: symbols x window, not the whole cache
        assert db.execute('SELECT COUNT(*) FROM candles WHERE ts>=9999999999999').fetchone()[0] == 0   # the later commit is NOT in this cut
        revs = db.execute("SELECT COUNT(*) FROM market_revisions WHERE series_key LIKE 'candle:%'").fetchone()[0]
        assert revs == n                                                       # every published bar keeps its retained receipt
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='candles' AND sql IS NULL").fetchone()
    assert n < before and out['consistency'] == 'SINGLE_READ_TRANSACTION_EXACT_CUT'
    manifest.refuse_live(out['path'])                                          # a quiescent single file the study accepts
    assert live.execute("SELECT COUNT(*) FROM candles WHERE ts>=9999999999999").fetchone()[0] == 1   # source received the write, untouched by us
    live.close()


def test_publication_refuses_an_unbounded_request_and_stops_at_its_deadline(tmp_path):
    import time
    from trader.research.closure import publish
    src, _ = wal_context(tmp_path)
    with pytest.raises(Exception, match='publish_rows_exceed_bound'):
        publish.publish_exact_cut(src, tmp_path / 's' / 'a.db', tf='4h', window_bars=300, max_rows=50)
    with pytest.raises(Exception, match='publish_deadline'):
        publish.publish_exact_cut(src, tmp_path / 's' / 'b.db', tf='4h', window_bars=300, deadline=time.monotonic() - 1)
    assert not list((tmp_path / 's').glob('*'))                                # nothing partial left


def test_published_snapshot_supports_a_study_manifest_and_replay_receipts(tmp_path, monkeypatch):
    from trader.research.closure import publish
    src, _ = wal_context(tmp_path, n_bars=1800)
    out = publish.publish_exact_cut(src, tmp_path / 's' / 'candles-2.db', tf='4h', window_bars=1700)
    m = manifest.build_manifest(out['path'], '4h', out['symbols'], [])
    inv = m['symbols'][out['symbols'][0]]
    assert inv['stored_bars'] == inv['eligible_bars'] > 1600 and inv['gap_count'] == 0     # receipts survived: replay-eligible


def test_publish_bounds_include_derivatives_without_receipts_and_payload_bytes(tmp_path):
    from trader.research.closure import publish
    from trader.core.types import TF_MS
    tf = TF_MS['4h']
    ts = 1_700_000_000_000 // tf * tf
    candles, derivs = tmp_path / 'source-c.db', tmp_path / 'source-d.db'
    with sqlite3.connect(candles) as db:
        db.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL)')
        db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)', ('A', '4h', ts, 1, 1, 1, 1, 1))
    with sqlite3.connect(derivs) as db:
        db.execute('CREATE TABLE derivs(symbol TEXT,series TEXT,ts INTEGER,value REAL)')
        db.executemany('INSERT INTO derivs VALUES (?,?,?,?)', [('A', 'funding', ts - j * tf, .001) for j in range(10)])
    dst, ddst = tmp_path / 'out' / 'c.db', tmp_path / 'out' / 'd.db'
    with pytest.raises(Exception, match='publish_rows_exceed_bound'):
        publish.publish_exact_cut(candles, dst, tf='4h', window_bars=20, symbols=['A'],
                                  deriv_src=derivs, dst_derivs=ddst, max_rows=5)
    assert not dst.exists() and not ddst.exists()
    with pytest.raises(Exception, match='publish_bytes_exceed_bound'):
        publish.publish_exact_cut(candles, dst, tf='4h', window_bars=20, max_bytes=1)
    assert not dst.exists()


def test_publication_cannot_move_its_cut_to_an_incomplete_or_future_candle(tmp_path):
    from trader.research.closure import publish
    from trader.core.types import TF_MS
    tf = TF_MS['4h']
    clock = 1_700_000_000_000 // tf * tf + tf // 2
    closed = clock // tf * tf - tf
    source = tmp_path / 'c.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE candles(symbol TEXT,tf TEXT,ts INTEGER,open REAL,high REAL,low REAL,close REAL,volume REAL)')
        db.executemany('INSERT INTO candles VALUES (?,?,?,?,?,?,?,?)',
                       [('A', '4h', t, 1, 1, 1, 1, 1) for t in (closed, closed + tf, closed + 10 * tf)])
    out = publish.publish_exact_cut(source, tmp_path / 'out' / 'c.db', tf='4h', window_bars=5, now_ms=clock)
    assert out['as_of_ms'] == closed + tf <= clock
    with sqlite3.connect(out['path']) as db:
        assert db.execute('SELECT ts FROM candles').fetchall() == [(closed,)]


def test_publication_never_replaces_existing_outputs_and_cleans_both_on_late_failure(tmp_path, monkeypatch):
    from trader.research.closure import publish
    src, _ = wal_context(tmp_path, n_bars=300)
    dst = tmp_path / 'out' / 'c.db'
    dst.parent.mkdir()
    dst.write_bytes(b'previous study')
    with pytest.raises(Exception, match='publish_destination_exists'):
        publish.publish_exact_cut(src, dst, tf='4h', window_bars=100)
    assert dst.read_bytes() == b'previous study'
    dst.unlink()
    # A successfully staged derivative output must be removed if the total physical bound fails.
    derivs = tmp_path / 'd.db'
    with sqlite3.connect(derivs) as db:
        db.execute('CREATE TABLE derivs(symbol TEXT,series TEXT,ts INTEGER,value REAL)')
    ddst = dst.with_name('d.db')
    original = publish._publish_derivs
    def fail_after_derivatives(*args, **kwargs):
        value = original(*args, **kwargs)
        raise RuntimeError('late failure')
    monkeypatch.setattr(publish, '_publish_derivs', fail_after_derivatives)
    with pytest.raises(RuntimeError, match='late failure'):
        publish.publish_exact_cut(src, dst, tf='4h', window_bars=100, deriv_src=derivs, dst_derivs=ddst)
    assert not list(dst.parent.iterdir())


def test_world_preparation_is_not_run_for_an_explicit_study_that_does_not_need_it(tmp_path, monkeypatch):
    monkeypatch.setattr(auto, 'world_frames', lambda *a: pytest.fail('unrequired historical World construction'))
    spec = dict(search=dict(factors=['rsi14', 'relvol50']), seed_graphs=[])
    assert auto.produce_world(dict(snapshot_dir=str(tmp_path)), spec, tmp_path/'not-opened.db', 1) is None
    # A declared World dependency must still reach its producer, even when no historical data is present.
    called = []
    monkeypatch.setattr(auto, 'world_frames', lambda *a: called.append(True) or {})
    assert auto.produce_world(dict(snapshot_dir=str(tmp_path)), dict(spec, search=dict(factors=['world_volume_anomaly'])),
                              tmp_path/'not-opened.db', 1) is None
    assert called == [True]


def test_study_evidence_lists_alpha_and_hedge_qualification_packets_separately(tmp_path):
    st = Store(tmp_path / 'q.closure')
    with st._tx() as c:
        c.execute('INSERT INTO studies(study_id,payload,status) VALUES (?,?,?)', ('S', json.dumps(dict(envelope_id='env1', origin='auto')), 'IN_PROGRESS'))
    st.put_once('qualifications', 'a1', dict(schema='closure-qualification.v1', status='QUALIFIED', reason='referee passed', qualification_id='a1',
                                              envelope=dict(envelope_id='env1'), authority_class='CONFIGURED'))
    st.put_once('qualifications', 'h1', dict(schema='closure-hedge-qualification.v1', status='ASSESSED_SUPPORTED', reason='risk cut', qualification_id='h1',
                                              envelope=dict(envelope_id='env1'), authority_class='TEST_ONLY', rule_id='r',
                                              statistical_admission=dict(status='NONE_REGISTERED'), gaps=[]))
    st.put_once('qualifications', 'x', dict(schema='closure-qualification.v1', status='QUALIFIED', qualification_id='x', envelope=dict(envelope_id='other')))
    st.close()
    kinds = {q['qualification_id']: (q['kind'], q['status'], q['statistical_admission']) for q in auto.evidence(tmp_path / 'q.closure')['studies'][0]['qualifications']}
    assert kinds == {'h1': ('hedge_protective', 'ASSESSED_SUPPORTED', 'NONE_REGISTERED'), 'a1': ('alpha', 'QUALIFIED', None)}
