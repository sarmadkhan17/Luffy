"""Offline DATA-03 consumer boundaries; no runtime/store acquisition."""
from types import SimpleNamespace

import pytest

from trader.data import market_provenance as P
from trader.data.feed import Universe
from trader.learning import capture_runtime as R
from tests.test_stage1_data_provenance_pit import frame, receipt, T, Q


@pytest.mark.parametrize('group', ['dfs', 'derivs', 'market', 'universe', 'anchors'])
@pytest.mark.parametrize('clock', ['available_at_ms', 'observed_at_ms'])
def test_capture_cannot_restamp_future_frame(tmp_path, group, clock):
    from trader.core.journal import Journal
    from trader.core.types import Snapshot
    import pandas as pd
    cut = T + Q
    data = receipt(frame(), cut)
    data[clock] = cut + 1
    snap = Snapshot('BTC/USDT', pd.Timestamp(cut, unit='ms', tz='UTC').isoformat(), 10, {})
    key = 'anchor_frames' if group == 'anchors' else group
    setattr(snap, key, {'BTC/USDT': {'15m': data}} if group in ('universe', 'anchors') else {'15m': data})
    j = Journal(tmp_path / 'capture.db')
    with j._tx() as db:
        R.C.ensure(db)
        with pytest.raises(ValueError, match='frame_receipt_not_available_at_cut'):
            R.frame_chunks(db, snap, cut)


def test_current_config_membership_not_restamped_before_observation(monkeypatch):
    import trader.data.feed as module
    monkeypatch.setattr(module.time, 'time', lambda: (T + Q) / 1000)
    monkeypatch.setattr(module, 'make_exchange', lambda *a, **k: SimpleNamespace())
    u = Universe({'universe': {'majors': ['BTC/USDT'], 'auto_scan': {'enabled': False}}})
    assert u.symbols(as_of_ms=T) == []
    assert u.membership_receipts(as_of_ms=T) == {}
    r = u.membership_receipts(as_of_ms=T + Q)['BTC/USDT']
    assert r['observed_at_ms'] == r['available_at_ms'] == T + Q
    assert u.membership_receipts(as_of_ms=T + Q + 10)['BTC/USDT'] == r


def test_historical_scan_cannot_restamp_live_strategy_include(monkeypatch):
    from trader.kernel import Kernel
    from tests.test_rolling import _spec
    k = Kernel.__new__(Kernel)
    k.universe = SimpleNamespace(symbols=lambda **kw: [], membership_receipts=lambda **kw: {},
                                 volumes=lambda **kw: {}, majors=[])
    k._spec_rows = [(None, _spec())]
    with pytest.raises(ValueError, match='historical_strategy_universe_unavailable'):
        k._scan_symbols(as_of_ms=T)


@pytest.mark.parametrize('missing', ['available_at_ms', 'observed_at_ms', 'revision_id'])
def test_capture_partial_receipt_cannot_become_unqualified(missing):
    data = receipt(frame(), T + Q).drop(columns=[missing])
    with pytest.raises(ValueError, match='frame_receipt_not_available_at_cut'):
        R.validate_frame_cut(data, T + Q)


def test_retained_future_chunk_refused_during_replay():
    from trader.learning import capture as C
    cut = T + Q
    raw = R.exact_frame(receipt(frame(), cut + 1))
    sha = R.L.digest(raw)
    sources = {'data': dict(format='snapshot-frame-chunks.v1', cutoff_ms=cut,
                           chunks={'dfs': {'15m': sha}}), 'outcome': {'observation': {}}}
    with pytest.raises(ValueError, match='frame_receipt_not_available_at_cut'):
        C.validate_chain({'observation': {}}, sources, {sha: raw})


def test_eligible_chunk_exact_roundtrip_and_restart(tmp_path):
    import sqlite3
    import json
    from trader.learning import capture as C
    from trader.core.types import Snapshot
    from trader.core.journal import Journal
    import pandas as pd
    cut = T + Q
    data = receipt(frame(), cut)
    snap = Snapshot('BTC/USDT', pd.Timestamp(cut, unit='ms', tz='UTC').isoformat(), 10, {'15m': data})
    path = tmp_path / 'capture.db'
    j = Journal(path)
    with j._tx() as db:
        C.ensure(db)
        captured = R.frame_chunks(db, snap, cut)
    with sqlite3.connect(path) as db:
        sha = captured['chunks']['dfs']['15m']
        raw = json.loads(db.execute('SELECT payload FROM learning_source_blobs WHERE sha256=?', (sha,)).fetchone()[0])
    restored = R.validate_frame_cut(R.restore_frame(raw), cut)
    pd.testing.assert_frame_equal(restored, data, check_exact=True, check_flags=False)
    sources = {'data': captured, 'outcome': {'observation': {}}}
    C.validate_chain({'observation': {}, 'kind': 'CASH', 'registration': {'profile': 'TRADING'}}, sources, {sha: raw})


def test_bulk_admission_clock_reversal_cannot_backdate_tickers(tmp_path, monkeypatch):
    import yaml
    from pathlib import Path
    from tests.test_hierarchical_admission import broad_kernel, CUT, ticker
    from trader.data.broad_crypto import observe
    cfg = yaml.safe_load(Path('config.yaml').read_text())
    clock = SimpleNamespace(at=CUT)
    monkeypatch.setattr('trader.data.broad_crypto.time.time', lambda: clock.at / 1000)
    k = broad_kernel(tmp_path, cfg, n=1)
    # Keep metadata eligible even after rollback: this control must exercise
    # the bulk request fence independently of the metadata availability fence.
    import pandas as pd
    known = CUT - 2000
    metadata = P.annotate(pd.DataFrame(dict(ts=pd.to_datetime([known],unit='ms',utc=True),close=[1.])),
        instrument_id='ref:venue_metadata',source=P.venue_source(k.exchange),kind='reference',
        received_ms=known,raw=[{'metadata':k.exchange.markets}],transform_version='venue_metadata.v1')
    with k.journal._tx() as conn:
        P.append(conn,k._venue_metadata_key,metadata)
    k._venue_metadata_revision=metadata['revision_id'].iloc[0]
    def acquire():
        clock.at -= 1000
        return {'S00/USDT': dict(ticker(), timestamp=clock.at)}
    k.universe.ex.fetch_tickers.side_effect = acquire
    observed, frames, members = observe(k, [])
    assert observed['rows'] == {} and frames == {} and members == {}
    assert observed['status'].startswith('UNAVAILABLE')


def test_capture_source_cut_cannot_precede_snapshot(tmp_path):
    from trader.core.types import Snapshot
    from trader.core.journal import Journal
    import pandas as pd
    snap = Snapshot('BTC/USDT', pd.Timestamp(T + Q, unit='ms', tz='UTC').isoformat(), 10, {})
    j = Journal(tmp_path / 'capture.db')
    with j._tx() as db, pytest.raises(ValueError, match='snapshot_after_source_cut'):
        R.frame_chunks(db, snap, T + Q - 1)


def test_frozen_parent_future_receipt_refused(tmp_path):
    from trader.core.journal import Journal
    from trader.learning import capture as C, decision_sources as D
    cut = T + Q
    j = Journal(tmp_path / 'parents.db')
    with j._tx() as db:
        C.ensure(db)
        raw = R.exact_frame(receipt(frame(), cut + 1))
        chunk = R.freeze(db, 'data', raw, cut, 'TEST_ONLY old capture bypass')
        data = dict(format='snapshot-frame-chunks.v1', cutoff_ms=cut,
                    chunks={'dfs': {'15m': chunk['sha256']}})
        dep = R.freeze(db, 'data', data, cut, 'TEST_ONLY old capture bypass')
        pid = C.register(db, 'root', R.L.Kind.CASH.value, {'decision_id': 'root', 'cycle_id': 'cycle'},
                         cut, [dep], decision_stage='SCAN')
        parent = C.registration(db, 'root')[1]
        child = dict(lineage=parent['lineage'], decision_ms=cut + 1,
                     chain=dict(parent_event_key='root', parent_registration_id=pid))
        ancestor = dict(registration_id=pid, registration=parent,
                        decision_source_manifest=D.load(db, parent['decision_source_manifest_id']),
                        sources={'data': data}, data_blobs={chunk['sha256']: raw})
        assert 'parent:data_chunk_invalid' in C.verify_ancestors(child, [ancestor])


def test_analyst_replay_leader_requires_local_observation_at_each_cut():
    from trader.agents.validate import validate_symbol
    leader = receipt(frame(at=T - 3_600_000), T + Q, tf='1h')
    leader['observed_at_ms'] = T + Q + 1
    seen = []
    agent = SimpleNamespace(evaluate=lambda snap: seen.append(snap) or None)
    validate_symbol({'probe': agent}, 'BTC/USDT', frame(n=24), leader, warmup=0)
    assert seen
    assert seen[0].dfs['BTC_1h'].empty, 'locally unobserved leader influenced historical replay'


def test_forward_outcome_read_enforces_requested_cut(tmp_path):
    import pandas as pd
    from trader.core.journal import Journal
    from trader.core.types import Snapshot, Decision, Action
    from trader.engine.outcomes import resolve_pending
    cut = T + 14_400_000 + 300_000
    j = Journal(tmp_path / 'outcomes.db')
    ts = pd.Timestamp(T, unit='ms', tz='UTC').isoformat()
    snap = Snapshot('BTC/USDT', ts, 10, {})
    j.log_cycle(snap, 'c', 'TEST_ONLY')
    j.log_decision(Decision('d', 'c', 'BTC/USDT', Action.BUY, 1, .2, .5, [], [], ts=ts))
    j.schedule_outcome('d', 'c', 'BTC/USDT', ts, 'BUY', 10)
    data = receipt(frame(at=T + 14_400_000), cut + 1, tf='5m')
    calls = []
    def read(*args, **kwargs):
        calls.append(kwargs)
        return data
    assert resolve_pending(j, SimpleNamespace(fetch_ohlcv=read), now_ms=cut) == 0
    assert calls[0]['as_of_ms'] == cut
    assert j.query('SELECT correct_4h FROM outcomes')[0]['correct_4h'] is None


@pytest.mark.parametrize('quality', ['VALID', 'STALE', 'UNKNOWN'])
def test_backfill_grade_uses_receipt_cut_and_quality(quality):
    import pandas as pd
    from trader.engine.outcome_backfill import grade
    cut = T + 14_400_000 + 300_000
    data = receipt(frame(at=T + 14_400_000, value=20), cut + (quality == 'VALID'), tf='5m')
    data['quality'] = quality
    result = grade(data, pd.Timestamp(T, unit='ms', tz='UTC'), 10, 'BUY', as_of_ms=cut)
    assert result['fwd_ret_4h'] is None and result['correct_4h'] is None


@pytest.mark.parametrize('case', ['missing', 'future', 'instrument', 'transform', 'schema'])
def test_broad_metadata_requires_receipt_at_source_cut(tmp_path, monkeypatch, case):
    import yaml
    from pathlib import Path
    from tests.test_hierarchical_admission import broad_kernel, CUT
    from trader.data.broad_crypto import observe
    cfg = yaml.safe_load(Path('config.yaml').read_text())
    monkeypatch.setattr('trader.data.broad_crypto.time.time', lambda: CUT / 1000)
    k = broad_kernel(tmp_path, cfg, n=1)
    if case == 'missing':
        k.__dict__.pop('_venue_metadata_key', None)
    else:
        import pandas as pd
        metadata = P.annotate(
            pd.DataFrame({'ts': pd.to_datetime([CUT], unit='ms', utc=True), 'close': [1]}),
            instrument_id='ref:foreign' if case == 'instrument' else 'ref:venue_metadata',
            source=P.venue_source(k.exchange), kind='reference',
            received_ms=CUT + (case == 'future'), raw=[{'metadata':k.exchange.markets}],
            transform_version='foreign.v1' if case == 'transform' else 'venue_metadata.v1')
        k._venue_metadata_key='context:venue_metadata:future'
        k._venue_metadata_revision=metadata['revision_id'].iloc[0]
        with k.journal._tx() as conn:
            P.init(conn);P.append(conn,k._venue_metadata_key,metadata)
            if case == 'schema':
                conn.execute('UPDATE market_revisions SET record_json=? WHERE series_key=?',
                             (P.encode({'schema_version':'foreign.v1'}),k._venue_metadata_key))
    observed, frames, members = observe(k, [])
    assert observed['rows'] == {} and frames == {} and members == {}
    assert observed['status'].startswith('UNAVAILABLE')


def test_boot_metadata_shared_reader_preserves_cut_revision_and_restart(tmp_path, monkeypatch):
    import json
    from trader.kernel import Kernel
    from trader.core.journal import Journal
    from tests.test_hierarchical_admission import CUT, markets
    clock = SimpleNamespace(at=CUT)
    monkeypatch.setattr('trader.kernel.time.time', lambda: clock.at / 1000)
    k = Kernel.__new__(Kernel)
    k.journal = Journal(tmp_path / 'metadata.db')
    def acquire():
        clock.at += 100
    k.exchange = SimpleNamespace(id='binanceusdm', urls={'api': 'https://offline.example/fapi'},
                                 markets=markets(['BTC/USDT']), load_markets=acquire)
    k.exchange.markets['BTC/USDT'].update(base='BTC')
    k.universe = SimpleNamespace(symbols=lambda: ['BTC/USDT'], majors=['BTC/USDT'], _alts=[])
    k._filter_universe_to_venue()
    first_cut = clock.at
    key = k._venue_metadata_key
    assert P.load(k.journal._conn(), key, as_of_ms=first_cut - 1) is None
    first = P.load(k.journal._conn(), key, as_of_ms=first_cut).iloc[0]
    assert first.revision_id == k._venue_metadata_revision and first.close == 1
    assert json.loads(first.raw_json)['metadata']['BTC/USDT']['active'] is True
    clock.at += 100
    k.exchange.markets['BTC/USDT']['active'] = False
    k._filter_universe_to_venue()
    reopened = Journal(tmp_path / 'metadata.db')
    earlier = P.load(reopened._conn(), key, as_of_ms=first_cut).iloc[0]
    assert earlier.revision_id == first.revision_id and earlier.raw_json == first.raw_json
    latest = P.load(reopened._conn(), key, as_of_ms=clock.at).iloc[-1]
    assert latest.revision_id != first.revision_id
    assert json.loads(latest.raw_json)['metadata']['BTC/USDT']['active'] is False


def test_broad_uses_retained_metadata_not_mutable_later_markets(tmp_path, monkeypatch):
    import yaml
    from pathlib import Path
    from tests.test_hierarchical_admission import broad_kernel, CUT
    from trader.data.broad_crypto import observe
    k = broad_kernel(tmp_path, yaml.safe_load(Path('config.yaml').read_text()), n=1)
    monkeypatch.setattr('trader.data.broad_crypto.time.time', lambda: CUT / 1000)
    original = observe(k, [])[0]
    k.exchange.markets['S00/USDT']['active'] = False
    replayed = observe(k, [])[0]
    assert replayed == original


def test_outcome_late_correction_and_restart_use_original_source_cut(tmp_path):
    import pandas as pd
    from trader.data.feed import DataFeed
    from trader.core.journal import Journal
    from trader.core.types import Snapshot, Decision, Action
    from trader.engine.outcomes import resolve_pending
    from tests.test_stage1_data_provenance_pit import Clock, Venue
    cut = T + 14_400_000 + 300_000
    path = tmp_path / 'candles.db'
    feed = DataFeed(exchange=Venue([]), db_path=path, clock_ms=Clock(cut + 100))
    iid, source = feed._identity('BTC/USDT')
    for available, value in ((cut, 20), (cut + 100, 30)):
        data = P.annotate(frame(at=T+14_400_000, value=value), instrument_id=iid, source=source,
                          kind='candle', received_ms=available, timeframe='5m')
        feed._store_save('BTC/USDT', '5m', data, now_ms=available)
    reopened = DataFeed(exchange=Venue([]), db_path=path, clock_ms=Clock(cut + 100))
    for index, reader in enumerate((feed, reopened)):
        j = Journal(tmp_path / ('outcome-%d.db' % index))
        ts = pd.Timestamp(T, unit='ms', tz='UTC').isoformat()
        j.log_cycle(Snapshot('BTC/USDT', ts, 10, {}), 'c', 'TEST_ONLY')
        j.log_decision(Decision('d', 'c', 'BTC/USDT', Action.BUY, 1, .2, .5, [], [], ts=ts))
        j.schedule_outcome('d', 'c', 'BTC/USDT', ts, 'BUY', 10)
        assert resolve_pending(j, reader, now_ms=cut) == 1
        assert j.query('SELECT fwd_ret_4h FROM outcomes')[0]['fwd_ret_4h'] == 1.0


def test_universe_rollback_cannot_publish_volume_before_request(monkeypatch):
    import trader.data.feed as module
    from tests.test_stage1_pit_blocker_fix import ScanVenue, U2
    clock = SimpleNamespace(at=U2)
    ex = ScanVenue()
    monkeypatch.setattr(module, 'make_exchange', lambda *a, **kw: ex)
    monkeypatch.setattr(module.time, 'time', lambda: clock.at / 1000)
    def acquire():
        clock.at -= 1000
        return {'ALT/USDT': dict(timestamp=clock.at, quoteVolume=1000, last=10)}
    ex.fetch_tickers = acquire
    u = Universe({'universe': {'majors': ['BTC/USDT'], 'auto_scan': {
        'min_volume_usdt':1, 'min_price':1, 'min_age_days':0}}}, ex)
    u._old_enough = lambda _: True
    u._rescan()
    assert u._volume_receipts == {} and u._volumes == {}, 'rollback published future volume'


@pytest.mark.parametrize('case', ['future', 'missing'])
def test_admission_replay_cannot_trust_outer_clock_over_metadata(tmp_path, monkeypatch, case):
    import yaml
    import pandas as pd
    from pathlib import Path
    from tests.test_hierarchical_admission import broad_kernel, CUT
    from trader.data.broad_crypto import observe
    from trader import attention_admission as A
    cfg = yaml.safe_load(Path('config.yaml').read_text())
    monkeypatch.setattr('trader.data.broad_crypto.time.time', lambda: CUT / 1000)
    k = broad_kernel(tmp_path, cfg, n=1)
    observed = observe(k, [])[0]
    if case == 'missing':
        observed['source_receipts']['venue_metadata_receipt'] = None
    else:
        markets = observed['source_receipts']['venue_metadata']
        future = P.annotate(pd.DataFrame(dict(ts=pd.to_datetime([CUT],unit='ms',utc=True),close=[float(len(markets))])),
            instrument_id='ref:venue_metadata',source=P.venue_source(k.exchange),kind='reference',received_ms=CUT+1,
            raw=[{'metadata':markets,'event_time_basis':'local_snapshot'}],transform_version='venue_metadata.v1')
        observed['source_receipts']['venue_metadata_receipt'] = P.receipt_metadata(future.iloc[0])
    with pytest.raises(ValueError, match='admission_metadata'):
        A.admit(observed, A.AdmissionPolicy.from_config(cfg), A.initial_state())
